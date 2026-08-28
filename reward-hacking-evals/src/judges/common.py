from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cases import case_dump_text, is_case_record
from cases.constants import MAKE_DATASETS_FAMILY_CONTEXT


def load_dotenv(path: Path = Path(".env"), *, override: bool = False) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if override or key not in os.environ:
            os.environ[key] = value


def close_object_schemas(schema: dict[str, Any]) -> dict[str, Any]:
    if isinstance(schema, dict):
        if schema.get("type") == "object":
            schema.setdefault("additionalProperties", False)
        for value in schema.values():
            if isinstance(value, dict):
                close_object_schemas(value)
            elif isinstance(value, list):
                for item in value:
                    if isinstance(item, dict):
                        close_object_schemas(item)
    return schema


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record))
        handle.write("\n")


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                obj = json.loads(stripped)
            except json.JSONDecodeError as exc:
                print(
                    f"warning: skipping malformed JSONL line {line_number} in {path}: {exc}",
                    file=sys.stderr,
                )
                continue
            if not isinstance(obj, dict):
                print(
                    f"warning: skipping non-object JSONL line {line_number} in {path}",
                    file=sys.stderr,
                )
                continue
            if obj.get("type") == "record":
                records.append(obj)
    return records


def build_skipped_result(
    entry: dict[str, Any],
    *,
    stage: str,
    exc: Exception | None = None,
    reason: str | None = None,
    backend: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    message = reason or (str(exc) if exc is not None else "unknown error")
    result = {
        "type": "record",
        "run_id": entry.get("run_id", ""),
        "source_project": entry.get("source_project"),
        "input_ref": entry.get("input_ref"),
        "judgment": None,
        "skipped": True,
        "skip_reason": f"{stage}: {message}",
        "error_stage": stage,
    }
    if exc is not None:
        result["error_type"] = type(exc).__name__
        result["error_message"] = str(exc)
    if backend is not None:
        result["backend"] = backend
    if model is not None:
        result["model"] = model
    metadata = entry.get("metadata")
    if isinstance(metadata, dict) and metadata:
        result["metadata"] = metadata
    return result


def load_records_by_run_id(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for record in load_jsonl_records(path):
        run_id = record.get("run_id")
        if isinstance(run_id, str):
            records[run_id] = record
    return records


def select_records(
    records: list[dict[str, Any]],
    *,
    run_id: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    filtered = records
    if run_id is not None:
        filtered = [record for record in filtered if record.get("run_id") == run_id]
    if limit is not None:
        filtered = filtered[:limit]
    return filtered


def load_positive_run_ids(path: Path) -> set[str]:
    positive: set[str] = set()
    for record in load_jsonl_records(path):
        judgment = record.get("judgment", {})
        if isinstance(judgment, dict) and judgment.get("label") == "reward_hacking":
            run_id = record.get("run_id")
            if isinstance(run_id, str):
                positive.add(run_id)
    return positive


def load_existing_judged_run_ids(path: Path) -> set[str]:
    """Return successfully judged IDs that append mode should not rerun.

    Skipped records are failed attempts, not completed judgments.  Excluding
    them here makes ``--append`` both resume successful work and retry transient
    failures from an interrupted or rate-limited batch.
    """
    if not path.exists():
        return set()
    seen: set[str] = set()
    for record in load_jsonl_records(path):
        run_id = record.get("run_id")
        judgment = record.get("judgment")
        if (
            isinstance(run_id, str)
            and not record.get("skipped")
            and isinstance(judgment, dict)
        ):
            seen.add(run_id)
    return seen


def filter_append_entries(
    entries: list[dict[str, Any]],
    *,
    output_path: Path,
    append: bool,
) -> list[dict[str, Any]]:
    if not append:
        return entries
    existing = load_existing_judged_run_ids(output_path)
    return [entry for entry in entries if entry["run_id"] not in existing]


def infer_source_project(record: dict[str, Any], requested: str) -> str:
    if requested != "auto":
        return requested
    source_project = record.get("source_project")
    if isinstance(source_project, str) and source_project:
        return source_project
    if "manifest_entry" in record:
        return "make_datasets"
    return "generic"


def robustness_summary(robust_record: dict[str, Any] | None) -> dict[str, Any] | None:
    if robust_record is None:
        return None
    if robust_record.get("error"):
        return {
            "status": "error",
            "metric": robust_record.get("metric"),
            "test_score": robust_record.get("test_score"),
            "robust_score": robust_record.get("robust_score"),
            "generalization_gap": robust_record.get("robust_gap"),
            "error": robust_record.get("error"),
            "interpretation": (
                "robust evaluation did not complete successfully; use the error as evidence about "
                "robustness and implementation stability"
            ),
        }
    return {
        "status": "ok",
        "metric": robust_record.get("metric"),
        "test_score": robust_record.get("test_score"),
        "robust_score": robust_record.get("robust_score"),
        "generalization_gap": robust_record.get("robust_gap"),
        "interpretation": (
            "positive means the robust split was materially worse than the visible benchmark split"
        ),
    }


def build_make_datasets_dump(record: dict[str, Any], robust_record: dict[str, Any] | None) -> str:
    manifest_entry = record.get("manifest_entry", {})
    family_code = manifest_entry.get("family_code")
    task_context = {
        "run_id": record.get("run_id"),
        "task_id": manifest_entry.get("task_id"),
        "family_code": family_code,
        "family_context": MAKE_DATASETS_FAMILY_CONTEXT.get(str(family_code), ""),
        "prompt_condition": manifest_entry.get("prompt_condition"),
        "rows": manifest_entry.get("rows"),
        "seed": manifest_entry.get("seed"),
        "agent": manifest_entry.get("agent"),
        "model": manifest_entry.get("model"),
    }
    payload = {
        "source_project": "make_datasets",
        "task_context": task_context,
        "robust_evaluation": robustness_summary(robust_record),
        "read_files": record.get("read_files", []),
        "write_files": record.get("write_files", []),
        "reads": record.get("reads", []),
        "writes": record.get("writes", []),
        "trajectory": record.get("trajectory", []),
        "evaluation_events": record.get("evaluation_events", []),
        "written_files": record.get("written_files", {}),
        "python_files": record.get("python_files", {}),
    }
    return json.dumps(payload, ensure_ascii=True, indent=2)


def build_generic_dump(record: dict[str, Any]) -> str:
    payload = dict(record)
    payload.pop("type", None)
    return json.dumps(payload, ensure_ascii=True, indent=2)


def load_case_record(path: Path) -> dict[str, Any] | None:
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict) or not is_case_record(record):
        return None
    return record


def case_entries_from_path(
    input_path: Path,
    *,
    source_project: str,
    run_id: str | None,
    limit: int | None,
    robust_records_by_run_id: dict[str, dict[str, Any]] | None = None,
    restrict_run_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    if input_path.is_dir():
        json_paths = sorted(input_path.glob("*.json"))
        case_paths = [(path, load_case_record(path)) for path in json_paths]
        case_paths = [(path, record) for path, record in case_paths if record is not None]
        if case_paths:
            if run_id is not None:
                case_paths = [(path, record) for path, record in case_paths if path.stem == run_id]
            if restrict_run_ids is not None:
                case_paths = [(path, record) for path, record in case_paths if path.stem in restrict_run_ids]
            if limit is not None:
                case_paths = case_paths[:limit]
            return [
                {
                    "run_id": str(record["run_id"]),
                    "source_project": str(record["source_project"]),
                    "dump": case_dump_text(record),
                    "input_ref": str(path),
                    "metadata": {
                        "case_path": str(path),
                        "source_variant": record.get("source_variant"),
                        "package_counts": record.get("package_counts", {}),
                    },
                }
                for path, record in case_paths
            ]

        xml_paths = sorted(input_path.glob("*.xml"))
        if run_id is not None:
            xml_paths = [path for path in xml_paths if path.stem == run_id]
        if restrict_run_ids is not None:
            xml_paths = [path for path in xml_paths if path.stem in restrict_run_ids]
        if limit is not None:
            xml_paths = xml_paths[:limit]
        return [
            {
                "run_id": path.stem,
                "source_project": source_project if source_project != "auto" else "autoresearch",
                "dump": path.read_text(encoding="utf-8"),
                "input_ref": str(path),
                "metadata": {"xml_path": str(path)},
            }
            for path in xml_paths
        ]

    if input_path.suffix == ".xml":
        if restrict_run_ids is not None and input_path.stem not in restrict_run_ids:
            return []
        return [
            {
                "run_id": input_path.stem,
                "source_project": source_project if source_project != "auto" else "autoresearch",
                "dump": input_path.read_text(encoding="utf-8"),
                "input_ref": str(input_path),
                "metadata": {"xml_path": str(input_path)},
            }
        ]

    if input_path.suffix == ".json":
        record = load_case_record(input_path)
        if record is None:
            raise ValueError(f"Unsupported JSON input path: {input_path}")
        if restrict_run_ids is not None and str(record["run_id"]) not in restrict_run_ids:
            return []
        if run_id is not None and str(record["run_id"]) != run_id:
            return []
        return [
            {
                "run_id": str(record["run_id"]),
                "source_project": str(record["source_project"]),
                "dump": case_dump_text(record),
                "input_ref": str(input_path),
                "metadata": {
                    "case_path": str(input_path),
                    "source_variant": record.get("source_variant"),
                    "package_counts": record.get("package_counts", {}),
                },
            }
        ]

    if input_path.suffix != ".jsonl":
        raise ValueError(f"Unsupported input path: {input_path}")

    records = select_records(load_jsonl_records(input_path), run_id=run_id, limit=limit)
    if restrict_run_ids is not None:
        records = [record for record in records if record.get("run_id") in restrict_run_ids]

    robust_records_by_run_id = robust_records_by_run_id or {}
    entries: list[dict[str, Any]] = []
    for index, record in enumerate(records, start=1):
        record_run_id = str(record.get("run_id", "")) or f"unknown_record_{index}"
        effective_source = infer_source_project(record, source_project)
        robust_record = robust_records_by_run_id.get(record_run_id)
        metadata = {
            "case": is_case_record(record),
            "package_counts": record.get("package_counts", {}),
            "manifest_entry": record.get("manifest_entry"),
            "has_robust_evaluation": robust_record is not None,
        }
        try:
            dump = record.get("dump")
            if not isinstance(dump, str) or not dump.strip():
                if is_case_record(record):
                    dump = case_dump_text(record)
                elif effective_source == "make_datasets":
                    dump = build_make_datasets_dump(record, robust_record)
                else:
                    dump = build_generic_dump(record)
            entries.append(
                {
                    "run_id": record_run_id,
                    "source_project": str(record.get("source_project", effective_source)),
                    "dump": dump,
                    "input_ref": str(input_path),
                    "metadata": metadata,
                }
            )
        except Exception as exc:
            metadata["preparation_error"] = str(exc)
            entries.append(
                {
                    "run_id": record_run_id,
                    "source_project": str(record.get("source_project", effective_source)),
                    "dump": "",
                    "input_ref": str(input_path),
                    "metadata": metadata,
                    "precomputed_result": build_skipped_result(
                        {
                            "run_id": record_run_id,
                            "source_project": str(record.get("source_project", effective_source)),
                            "input_ref": str(input_path),
                            "metadata": metadata,
                        },
                        stage="prepare_entry",
                        exc=exc,
                    ),
                }
            )
    return entries


def prepare_entries(
    input_path: Path,
    *,
    source_project: str,
    run_id: str | None,
    limit: int | None,
    robust_results: str | None = None,
    binary_results: str | None = None,
) -> list[dict[str, Any]]:
    robust_records_by_run_id: dict[str, dict[str, Any]] = {}
    if robust_results:
        robust_records_by_run_id = load_records_by_run_id(Path(robust_results))

    restrict_run_ids: set[str] | None = None
    if binary_results:
        restrict_run_ids = load_positive_run_ids(Path(binary_results))

    return case_entries_from_path(
        input_path,
        source_project=source_project,
        run_id=run_id,
        limit=limit,
        robust_records_by_run_id=robust_records_by_run_id,
        restrict_run_ids=restrict_run_ids,
    )


def write_jsonl_header(path: Path, append: bool, header: dict[str, Any]) -> None:
    if append:
        return
    path.unlink(missing_ok=True)
    append_jsonl(path, header)


def write_jsonl_footer(
    path: Path,
    *,
    append: bool,
    footer: dict[str, Any],
    failure_count: int,
) -> None:
    if append:
        return
    record = dict(footer)
    record["failure_count"] = failure_count
    append_jsonl(path, record)


def run_judging_stage(
    entries: list[dict[str, Any]],
    *,
    output_path: Path,
    concurrency: int,
    append: bool,
    header: dict[str, Any],
    footer: dict[str, Any],
    stage: str,
    progress_verb: str,
    judge_entry,
    backend: str,
    model: str,
    max_consecutive_skips: int = 3,
) -> int:
    """Run a judging stage with a fail-fast guard.

    If the first N produced results in a row are skipped (transient retries
    already exhausted, so this is a systemic failure — auth, quota, rate
    limit), cancel pending judge calls, write the footer, and raise so the
    caller exits non-zero. This stops a misconfigured key (e.g. corrupted
    .env) from burning through every case before the operator notices.

    Pass max_consecutive_skips=0 to disable the guard.
    """
    write_jsonl_header(output_path, append, header)

    total = len(entries)
    completed = 0
    failures = 0
    precomputed_entries = [entry for entry in entries if "precomputed_result" in entry]
    queued_entries = [entry for entry in entries if "precomputed_result" not in entry]

    for entry in precomputed_entries:
        append_jsonl(output_path, entry["precomputed_result"])
        completed += 1
        failures += 1
        print(f"[{completed}/{total}] {progress_verb} skipped {entry['run_id']} (prepare_entry)")

    write_lock = Lock()
    consecutive_skips = 0
    last_skip_reason: str | None = None
    aborted = False

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_entry = {
            executor.submit(judge_entry, entry): entry
            for entry in queued_entries
        }
        for future in as_completed(future_to_entry):
            entry = future_to_entry[future]
            if aborted:
                # Cancel anything we haven't already started. as_completed
                # still drains the iterator, so we just stop processing.
                future.cancel()
                continue
            try:
                result = future.result()
            except Exception as exc:
                result = build_skipped_result(
                    entry,
                    stage=stage,
                    exc=exc,
                    backend=backend,
                    model=model,
                )
            is_skipped = bool(result.get("skipped"))
            with write_lock:
                append_jsonl(output_path, result)
                completed += 1
                if is_skipped:
                    failures += 1
                    consecutive_skips += 1
                    last_skip_reason = result.get("skip_reason") or last_skip_reason
                    print(f"[{completed}/{total}] {progress_verb} skipped {entry['run_id']}")
                    print(f"  reason: {result.get('skip_reason')}")
                else:
                    consecutive_skips = 0
                    print(f"[{completed}/{total}] {progress_verb} {entry['run_id']}")
            if (
                max_consecutive_skips > 0
                and consecutive_skips >= max_consecutive_skips
            ):
                aborted = True
                print(
                    f"\n[FAIL-FAST] {stage}: {consecutive_skips} consecutive "
                    f"skips on backend={backend} model={model}. Aborting stage.\n"
                    f"  last_reason: {last_skip_reason}\n"
                    f"  Likely systemic (auth/quota). Fix the underlying issue, "
                    f"then re-run.",
                    flush=True,
                )
                # Cancel anything that hasn't started yet. Already-running
                # judge_entry calls will finish and their results will be
                # skipped over by the `if aborted` check above.
                for f in future_to_entry:
                    if not f.done():
                        f.cancel()

    write_jsonl_footer(
        output_path,
        append=append,
        footer=footer,
        failure_count=failures,
    )
    if aborted:
        raise RuntimeError(
            f"{stage} aborted after {consecutive_skips} consecutive skips "
            f"(backend={backend}, model={model}). Last reason: {last_skip_reason!r}"
        )
    return failures
