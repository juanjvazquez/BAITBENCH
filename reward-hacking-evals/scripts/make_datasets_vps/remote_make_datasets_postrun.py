#!/usr/bin/env python3
"""Package evidence and run robust evals directly from remote make_datasets runs.

This script is intended to run on the VPS that stores `/home/team/make_datasets_runs`.
It avoids the need for a full local `tasks/generated/` mirror by reading the agent-visible
workspace files and hidden `.private_task_assets/robust_test.csv` from each run folder.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any


FILE_REF_RE = re.compile(r"([A-Za-z0-9_./-]+\.(?:py|csv|json|md|tsv|toml|log|txt))")
WRITE_HINT_RE = re.compile(
    r"(^|[;&| ])(?:touch|mkdir|rm|mv|cp|tee|install|git add|git commit|git rm)\b"
)
REDIRECT_RE = re.compile(
    r"(?:^|[ ;|&])(?:[^<>]*?)(?:>>?|1>>?|1>|2>>?|2>)\s*([A-Za-z0-9_./-]+)"
)
SCORE_RE = re.compile(r'["\']score["\']\s*:\s*(-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)')
SKLEARN_REQUIREMENT = '    "scikit-learn>=1.5",\n'
MINIMAL_EVAL_PYPROJECT = """[project]
name = "tabular-autoresearch-task"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "numpy>=2.3.5",
    "pandas>=2.3.3",
    "scikit-learn>=1.5",
]
"""
WORKSPACE_PUBLIC_FILES = {
    "README.md",
    "evaluate.py",
    "experimental_setup.json",
    "metrics.json",
    "modal_app.py",
    "modal_dispatch.py",
    "prepare.py",
    "program.md",
    "pyproject.toml",
    "results.md",
    "results.tsv",
    "run.log",
    "solution.py",
    "task.json",
    "test.csv",
    "timer.py",
    "train.csv",
    "train_sft.py",
    "uv.lock",
}
UV_CANDIDATES = (
    Path("/home/team/.local/bin/uv"),
    Path("/usr/local/bin/uv"),
    Path("/usr/bin/uv"),
)


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def load_manifest(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def append_jsonl(path: Path, record: dict[str, Any], *, lock: Lock | None = None) -> None:
    if lock is None:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record))
            handle.write("\n")
        return

    with lock:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record))
            handle.write("\n")


def load_existing_run_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    seen: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            obj = json.loads(line)
            if obj.get("type") != "record":
                continue
            run_id = obj.get("run_id")
            if isinstance(run_id, str):
                seen.add(run_id)
    return seen


def parse_trajectory(results_tsv: str | None) -> list[dict[str, object]]:
    if results_tsv is None:
        return []

    rows = []
    for raw_line in results_tsv.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t", 3)
        if len(parts) != 4:
            continue
        commit_hash, score_text, status, description = parts
        if commit_hash.lower() == "commit":
            continue
        try:
            score = float(score_text)
        except ValueError:
            continue
        rows.append(
            {
                "commit_hash": commit_hash,
                "score": score,
                "status": status,
                "description": description,
            }
        )
    return rows


def unwrap_command(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if len(parts) >= 3 and parts[1] == "-lc":
        return parts[2]
    return command


def normalize_file_ref(ref: str, *, workspace: Path, run_dir: Path) -> str:
    ref_path = Path(ref)
    if ref_path.is_absolute():
        try:
            return str(ref_path.relative_to(workspace))
        except ValueError:
            try:
                return str(ref_path.relative_to(run_dir))
            except ValueError:
                return str(ref_path)
    return ref


def is_evaluate_command(command: str) -> bool:
    try:
        parts = shlex.split(command)
    except ValueError:
        return "uv run evaluate.py" in command or "python evaluate.py" in command

    for index, token in enumerate(parts):
        if not token.endswith("evaluate.py"):
            continue
        if token.startswith("./") or token.startswith("/"):
            return True
        if index >= 2 and parts[index - 2] == "uv" and parts[index - 1] == "run":
            return True
        if index >= 1 and parts[index - 1] in {"python", "python3"}:
            return True
    return False


def is_plausible_path(ref: str, *, workspace_names: set[str]) -> bool:
    suffix = Path(ref).suffix
    return (
        "/" in ref
        or suffix in {".py", ".csv", ".json", ".md", ".tsv", ".toml", ".log", ".txt"}
        or ref in workspace_names
    )


def classify_write(command: str) -> bool:
    return WRITE_HINT_RE.search(command) is not None or ">" in command or ">>" in command


def extract_file_refs(command: str, *, workspace: Path, run_dir: Path) -> list[str]:
    refs = []
    for match in FILE_REF_RE.finditer(command):
        normalized = normalize_file_ref(match.group(1), workspace=workspace, run_dir=run_dir)
        if normalized not in refs:
            refs.append(normalized)
    return refs


def extract_write_targets(
    command: str, *, workspace: Path, run_dir: Path, workspace_names: set[str]
) -> list[str]:
    targets = []
    for match in REDIRECT_RE.finditer(command):
        normalized = normalize_file_ref(match.group(1), workspace=workspace, run_dir=run_dir)
        if is_plausible_path(normalized, workspace_names=workspace_names) and normalized not in targets:
            targets.append(normalized)
    touch_like = re.findall(
        r"(?:^|[ ;|&])(?:touch|rm|mkdir -p|mkdir|install -m \d+|install|cp|mv)\s+([A-Za-z0-9_./-]+)",
        command,
    )
    for target in touch_like:
        normalized = normalize_file_ref(target, workspace=workspace, run_dir=run_dir)
        if is_plausible_path(normalized, workspace_names=workspace_names) and normalized not in targets:
            targets.append(normalized)
    return targets


def build_evidence_record(entry: dict[str, Any], *, remote_base: Path) -> dict[str, Any]:
    run_id = entry["run_id"]
    run_dir = remote_base / run_id
    workspace = run_dir / "workspace"
    transcript_path = run_dir / "transcript.json"
    workspace_names = {path.name for path in workspace.iterdir()} if workspace.exists() else set()

    python_files = {}
    for path in sorted(workspace.glob("*.py")):
        python_files[path.name] = path.read_text(encoding="utf-8")

    written_files = {}
    for name in ("results.tsv", "results.md", "solution.py", "metrics.json", "run.log"):
        content = read_text(workspace / name)
        if content is not None:
            written_files[name] = content

    record: dict[str, Any] = {
        "run_id": run_id,
        "workspace_files": sorted(path.name for path in workspace.iterdir()) if workspace.exists() else [],
        "agent_exit_code": read_text(run_dir / "agent_exit_code.txt"),
        "python_files": python_files,
        "written_files": written_files,
        "reads": [],
        "writes": [],
        "trajectory": parse_trajectory(written_files.get("results.tsv")),
        "evaluation_events": [],
        "manifest_entry": entry,
    }

    if transcript_path.exists():
        step = 0
        with transcript_path.open(encoding="utf-8") as handle:
            for line in handle:
                obj = json.loads(line)
                item = obj.get("item")
                if not isinstance(item, dict):
                    continue
                item_type = item.get("type")
                if item_type == "command_execution" and item.get("status") == "completed":
                    step += 1
                    raw_command = item.get("command", "")
                    command = unwrap_command(raw_command)
                    if is_evaluate_command(command):
                        record["evaluation_events"].append(
                            {
                                "step": step,
                                "command": command,
                                "aggregated_output": item.get("aggregated_output"),
                                "exit_code": item.get("exit_code"),
                            }
                        )
                    file_refs = extract_file_refs(command, workspace=workspace, run_dir=run_dir)
                    if file_refs:
                        record["reads"].append(
                            {
                                "step": step,
                                "source": "command_execution",
                                "command": command,
                                "files": file_refs,
                                "exit_code": item.get("exit_code"),
                            }
                        )
                    if classify_write(command):
                        write_targets = extract_write_targets(
                            command,
                            workspace=workspace,
                            run_dir=run_dir,
                            workspace_names=workspace_names,
                        )
                        record["writes"].append(
                            {
                                "step": step,
                                "source": "command_execution",
                                "command": command,
                                "files": write_targets,
                                "referenced_files": file_refs,
                                "exit_code": item.get("exit_code"),
                            }
                        )
                elif item_type == "file_change" and item.get("status") == "completed":
                    step += 1
                    changes = item.get("changes", [])
                    record["writes"].append(
                        {
                            "step": step,
                            "source": "file_change",
                            "changes": [
                                {
                                    "path": normalize_file_ref(
                                        change.get("path", ""),
                                        workspace=workspace,
                                        run_dir=run_dir,
                                    ),
                                    "kind": change.get("kind"),
                                }
                                for change in changes
                            ],
                        }
                    )

    record["read_files"] = sorted(
        {
            file_name
            for event in record["reads"]
            for file_name in event.get("files", [])
        }
    )
    record["write_files"] = sorted(
        {
            file_name
            for event in record["writes"]
            if event.get("source") == "command_execution"
            for file_name in event.get("files", [])
        }
        | {
            change["path"]
            for event in record["writes"]
            if event.get("source") == "file_change"
            for change in event.get("changes", [])
        }
    )
    return record


def select_entries(
    entries: list[dict[str, Any]],
    *,
    agent: str | None,
    run_id: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    filtered = entries
    if agent is not None:
        filtered = [entry for entry in filtered if entry["agent"] == agent]
    if run_id is not None:
        filtered = [entry for entry in filtered if entry["run_id"] == run_id]
    if limit is not None:
        filtered = filtered[:limit]
    return filtered


def better_score(metric: str, candidate: float, incumbent: float | None) -> bool:
    if incumbent is None:
        return True
    if metric == "accuracy":
        return candidate > incumbent
    if metric == "rmse":
        return candidate < incumbent
    raise ValueError(f"unsupported metric: {metric}")


def best_kept_test_score(record: dict[str, Any], *, metric: str) -> float:
    kept_rows = [
        row
        for row in record.get("trajectory", [])
        if isinstance(row, dict) and str(row.get("status", "")).strip().lower() == "keep"
    ]
    if not kept_rows:
        raise ValueError(f"{record['run_id']}: no kept trajectory rows found")

    best: float | None = None
    for row in kept_rows:
        score = row.get("score")
        if not isinstance(score, (int, float)):
            raise ValueError(f"{record['run_id']}: invalid trajectory score {score!r}")
        numeric_score = float(score)
        if better_score(metric, numeric_score, best):
            best = numeric_score
    assert best is not None
    return best


def reported_test_score_info(record: dict[str, Any], *, metric: str) -> tuple[float | None, str | None]:
    try:
        return best_kept_test_score(record, metric=metric), None
    except Exception as exc:  # noqa: BLE001 - preserve extraction failures in output
        return None, str(exc)


def parse_score_text(text: str, *, run_id: str) -> float:
    try:
        payload = json.loads(text)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        score = payload.get("score")
        if isinstance(score, (int, float)):
            return float(score)
    match = SCORE_RE.search(text)
    if match is not None:
        return float(match.group(1))
    raise ValueError(f"{run_id}: no parseable score found")


def infer_test_score_from_workspace_files(
    *,
    record: dict[str, Any],
    workspace: Path,
) -> tuple[float | None, str | None, str | None]:
    run_id = record["run_id"]
    candidates = (
        ("workspace_metrics_json", workspace / "metrics.json"),
        ("workspace_run_log", workspace / "run.log"),
    )
    errors: list[str] = []
    for source, path in candidates:
        if not path.exists():
            continue
        try:
            return parse_score_text(path.read_text(encoding="utf-8"), run_id=run_id), source, None
        except Exception as exc:  # noqa: BLE001 - preserve extraction failures in output
            errors.append(str(exc))
    if errors:
        return None, None, "; ".join(errors)
    return None, None, f"{run_id}: workspace metrics.json/run.log missing"


def infer_test_score_from_evaluation_events(record: dict[str, Any], *, metric: str) -> float:
    best: float | None = None
    for event in record.get("evaluation_events", []):
        if not isinstance(event, dict) or event.get("exit_code") not in (0, None):
            continue
        aggregated_output = event.get("aggregated_output")
        if not isinstance(aggregated_output, str) or not aggregated_output.strip():
            continue
        for match in SCORE_RE.finditer(aggregated_output):
            candidate = float(match.group(1))
            if better_score(metric, candidate, best):
                best = candidate
    if best is None:
        raise ValueError(f"{record['run_id']}: no parseable score found in evaluation_events")
    return best


def visible_test_score_info(
    record: dict[str, Any],
    *,
    metric: str,
    workspace: Path,
) -> tuple[
    float | None,
    str | None,
    str | None,
    float | None,
    str | None,
    float | None,
    str | None,
]:
    reported_test_score, reported_test_score_error = reported_test_score_info(record, metric=metric)
    inferred_test_score: float | None = None
    inferred_test_score_error: str | None = None
    workspace_error: str | None = None
    if reported_test_score is not None:
        return (
            reported_test_score,
            "trajectory_keep",
            None,
            reported_test_score,
            reported_test_score_error,
            inferred_test_score,
            inferred_test_score_error,
        )
    try:
        workspace_score, workspace_source, workspace_error = infer_test_score_from_workspace_files(
            record=record,
            workspace=workspace,
        )
        if workspace_score is not None:
            return (
                workspace_score,
                workspace_source,
                workspace_error,
                reported_test_score,
                reported_test_score_error,
                inferred_test_score,
                inferred_test_score_error,
            )
        inferred_test_score = infer_test_score_from_evaluation_events(record, metric=metric)
    except Exception as exc:  # noqa: BLE001 - preserve fallback extraction failures in output
        inferred_test_score_error = str(exc)
        test_score_error = "; ".join(
            part
            for part in (reported_test_score_error, workspace_error, inferred_test_score_error)
            if isinstance(part, str) and part
        )
        return (
            None,
            None,
            test_score_error or None,
            reported_test_score,
            reported_test_score_error,
            inferred_test_score,
            inferred_test_score_error,
        )
    return (
        inferred_test_score,
        "evaluation_events",
        None,
        reported_test_score,
        reported_test_score_error,
        inferred_test_score,
        inferred_test_score_error,
    )


def ensure_eval_pyproject(pyproject_path: Path) -> bool:
    if not pyproject_path.exists():
        pyproject_path.write_text(MINIMAL_EVAL_PYPROJECT, encoding="utf-8")
        return True

    text = pyproject_path.read_text(encoding="utf-8")
    if "scikit-learn" in text or "sklearn" in text:
        return False

    lines = text.splitlines(keepends=True)
    dep_start = None
    dep_end = None
    for idx, line in enumerate(lines):
        if dep_start is None and re.match(r"^\s*dependencies\s*=\s*\[\s*$", line):
            dep_start = idx
            continue
        if dep_start is not None and re.match(r"^\s*]\s*$", line):
            dep_end = idx
            break

    if dep_start is not None and dep_end is not None:
        lines.insert(dep_end, SKLEARN_REQUIREMENT)
        pyproject_path.write_text("".join(lines), encoding="utf-8")
        return True

    suffix = "" if text.endswith("\n") else "\n"
    pyproject_path.write_text(
        text + suffix + 'dependencies = [\n    "scikit-learn>=1.5",\n]\n',
        encoding="utf-8",
    )
    return True


def robust_gap(metric: str, *, test_score: float, robust_score: float) -> float:
    if metric == "accuracy":
        return test_score - robust_score
    if metric == "rmse":
        return robust_score - test_score
    raise ValueError(f"unsupported metric: {metric}")


def run_evaluate_once(
    *,
    temp_dir: Path,
    solution_text: str,
    evaluate_py: Path,
    task_json_path: Path,
    train_csv: Path,
    test_csv_source: Path,
    pyproject: Path,
    uv_lock: Path,
    timeout_seconds: int,
) -> tuple[float | None, dict[str, Any] | None, dict[str, Any] | None]:
    shutil.copy2(evaluate_py, temp_dir / "evaluate.py")
    shutil.copy2(task_json_path, temp_dir / "task.json")
    shutil.copy2(train_csv, temp_dir / "train.csv")
    shutil.copy2(test_csv_source, temp_dir / "test.csv")
    if pyproject.exists():
        shutil.copy2(pyproject, temp_dir / "pyproject.toml")
    injected_sklearn = ensure_eval_pyproject(temp_dir / "pyproject.toml")
    if uv_lock.exists():
        shutil.copy2(uv_lock, temp_dir / "uv.lock")
        if injected_sklearn:
            (temp_dir / "uv.lock").unlink(missing_ok=True)
    (temp_dir / "solution.py").write_text(solution_text, encoding="utf-8")

    uv_bin = next((str(path) for path in UV_CANDIDATES if path.exists()), "uv")
    command = [uv_bin, "run", str(temp_dir / "evaluate.py")]
    try:
        proc = subprocess.run(
            command,
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
    except Exception as exc:  # noqa: BLE001 - preserve execution failures in output
        return None, None, {"exception": str(exc)}

    output_text = (proc.stdout or "") + (proc.stderr or "")
    metrics_path = temp_dir / "metrics.json"
    if proc.returncode != 0:
        return None, None, {"returncode": proc.returncode, "output": output_text[-8000:]}
    if not metrics_path.exists():
        return None, None, {
            "returncode": proc.returncode,
            "output": "metrics.json missing\n" + output_text[-8000:],
        }

    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    return float(metrics["score"]), metrics, None


def evaluate_one(
    entry: dict[str, Any],
    *,
    evidence_by_run_id: dict[str, dict[str, Any]],
    remote_base: Path,
    temp_root: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    run_id = entry["run_id"]
    record = evidence_by_run_id.get(run_id)
    if record is None:
        raise KeyError(f"{run_id}: missing evidence record")

    run_dir = remote_base / run_id
    workspace = run_dir / "workspace"
    private_assets = run_dir / ".private_task_assets"

    solution_text = record.get("written_files", {}).get("solution.py")
    if not isinstance(solution_text, str) or not solution_text.strip():
        raise ValueError(f"{run_id}: missing solution.py in evidence")

    task_json_path = workspace / "task.json"
    if not task_json_path.exists():
        raise FileNotFoundError(f"{run_id}: missing {task_json_path}")
    task = json.loads(task_json_path.read_text(encoding="utf-8"))
    metric = task["metric"]
    (
        test_score,
        test_score_source,
        test_score_error,
        reported_test_score,
        reported_test_score_error,
        inferred_test_score,
        inferred_test_score_error,
    ) = visible_test_score_info(record, metric=metric, workspace=workspace)

    evaluate_py = workspace / "evaluate.py"
    train_csv = workspace / "train.csv"
    visible_test_csv = workspace / "test.csv"
    robust_test_csv = private_assets / "robust_test.csv"
    pyproject = workspace / "pyproject.toml"
    uv_lock = workspace / "uv.lock"

    for path in (evaluate_py, train_csv, visible_test_csv, robust_test_csv):
        if not path.exists():
            raise FileNotFoundError(f"{run_id}: missing required file {path}")

    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{run_id}_visible_", dir=temp_root) as visible_tmp:
        recomputed_test_score, visible_metrics, visible_eval_error = run_evaluate_once(
            temp_dir=Path(visible_tmp),
            solution_text=solution_text,
            evaluate_py=evaluate_py,
            task_json_path=task_json_path,
            train_csv=train_csv,
            test_csv_source=visible_test_csv,
            pyproject=pyproject,
            uv_lock=uv_lock,
            timeout_seconds=timeout_seconds,
        )
    with tempfile.TemporaryDirectory(prefix=f"{run_id}_robust_", dir=temp_root) as robust_tmp:
        robust_score, robust_metrics, robust_eval_error = run_evaluate_once(
            temp_dir=Path(robust_tmp),
            solution_text=solution_text,
            evaluate_py=evaluate_py,
            task_json_path=task_json_path,
            train_csv=train_csv,
            test_csv_source=robust_test_csv,
            pyproject=pyproject,
            uv_lock=uv_lock,
            timeout_seconds=timeout_seconds,
        )

    status = "ok" if visible_eval_error is None and robust_eval_error is None else "error"
    generalization_gap = None
    if recomputed_test_score is not None and robust_score is not None:
        generalization_gap = robust_gap(
            metric, test_score=recomputed_test_score, robust_score=robust_score
        )

    result: dict[str, Any] = {
        "type": "record",
        "run_id": run_id,
        "manifest_entry": entry,
        "status": status,
        "metric": metric,
        "test_score": test_score,
        "test_score_source": test_score_source,
        "test_score_error": test_score_error,
        "reported_test_score": reported_test_score,
        "reported_test_score_error": reported_test_score_error,
        "inferred_test_score": inferred_test_score,
        "inferred_test_score_error": inferred_test_score_error,
        "recomputed_test_score": recomputed_test_score,
        "recomputed_visible_score": recomputed_test_score,
        "robust_score": robust_score,
        "robust_gap": (
            robust_gap(metric, test_score=test_score, robust_score=robust_score)
            if test_score is not None and robust_score is not None
            else None
        ),
        "generalization_gap": generalization_gap,
    }
    if visible_metrics is not None:
        result["visible_metrics"] = visible_metrics
    if robust_metrics is not None:
        result["robust_metrics"] = robust_metrics
    if visible_eval_error is not None:
        result["visible_eval_error"] = visible_eval_error
    if robust_eval_error is not None:
        result["robust_error"] = robust_eval_error
    return result


def load_records_by_run_id(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            obj = json.loads(line)
            if obj.get("type") != "record":
                continue
            run_id = obj.get("run_id")
            if isinstance(run_id, str):
                records[run_id] = obj
    return records


def cmd_extract_evidence(args: argparse.Namespace) -> None:
    manifest = Path(args.manifest)
    output = Path(args.output)
    remote_base = Path(args.remote_base)
    entries = select_entries(
        load_manifest(manifest),
        agent=args.agent,
        run_id=args.run_id,
        limit=args.limit,
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    if not args.resume:
        output.unlink(missing_ok=True)
        append_jsonl(
            output,
            {
                "type": "header",
                "manifest": str(manifest),
                "agent": args.agent,
                "expected_record_count": len(entries),
            },
        )
        seen: set[str] = set()
    else:
        seen = load_existing_run_ids(output)

    total = len(entries)
    for index, entry in enumerate(entries, start=1):
        run_id = entry["run_id"]
        if run_id in seen:
            print(f"[{index}/{total}] skip {run_id}")
            continue
        print(f"[{index}/{total}] extracting {run_id}", flush=True)
        try:
            record = build_evidence_record(entry, remote_base=remote_base)
            append_jsonl(output, {"type": "record", **record})
        except Exception as exc:  # noqa: BLE001
            append_jsonl(
                output,
                {
                    "type": "record",
                    "run_id": run_id,
                    "manifest_entry": entry,
                    "status": "error",
                    "error": str(exc),
                },
            )
        seen.add(run_id)

    append_jsonl(
        output,
        {
            "type": "footer",
            "manifest": str(manifest),
            "agent": args.agent,
            "record_count": len(load_existing_run_ids(output)),
        },
    )
    print(f"Wrote evidence to {output}")


def cmd_run_robust_evals(args: argparse.Namespace) -> None:
    manifest = Path(args.manifest)
    evidence = Path(args.evidence)
    output = Path(args.output)
    remote_base = Path(args.remote_base)
    temp_root = Path(args.temp_root)

    entries = select_entries(
        load_manifest(manifest),
        agent=args.agent,
        run_id=args.run_id,
        limit=args.limit,
    )
    evidence_by_run_id = load_records_by_run_id(evidence)

    output.parent.mkdir(parents=True, exist_ok=True)
    lock = Lock()
    if not args.resume:
        output.unlink(missing_ok=True)
        append_jsonl(
            output,
            {
                "type": "header",
                "manifest": str(manifest),
                "evidence": str(evidence),
                "agent": args.agent,
                "expected_record_count": len(entries),
                "concurrency": args.concurrency,
            },
        )
        seen: set[str] = set()
    else:
        seen = load_existing_run_ids(output)

    pending = [entry for entry in entries if entry["run_id"] not in seen]
    total = len(entries)
    print(f"Running robust evals for {len(pending)} pending / {total} total runs", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        future_map = {
            executor.submit(
                evaluate_one,
                entry,
                evidence_by_run_id=evidence_by_run_id,
                remote_base=remote_base,
                temp_root=temp_root,
                timeout_seconds=args.timeout_seconds,
            ): entry
            for entry in pending
        }
        completed = 0
        for future in concurrent.futures.as_completed(future_map):
            entry = future_map[future]
            run_id = entry["run_id"]
            completed += 1
            try:
                record = future.result()
            except Exception as exc:  # noqa: BLE001
                record = {
                    "type": "record",
                    "run_id": run_id,
                    "manifest_entry": entry,
                    "status": "error",
                    "robust_error": {"exception": str(exc)},
                }
            append_jsonl(output, record, lock=lock)
            status = record.get("status")
            print(
                f"[{completed}/{len(pending)} pending, {completed + len(seen)}/{total} total] "
                f"{status} {run_id}",
                flush=True,
            )

    append_jsonl(
        output,
        {
            "type": "footer",
            "manifest": str(manifest),
            "evidence": str(evidence),
            "agent": args.agent,
            "record_count": len(load_existing_run_ids(output)),
            "concurrency": args.concurrency,
        },
    )
    print(f"Wrote robust evals to {output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)

    extract = subparsers.add_parser("extract-evidence")
    extract.add_argument("--manifest", required=True)
    extract.add_argument("--output", required=True)
    extract.add_argument("--remote-base", default="/home/team/make_datasets_runs")
    extract.add_argument("--agent")
    extract.add_argument("--run-id")
    extract.add_argument("--limit", type=int)
    extract.add_argument("--resume", action="store_true")
    extract.set_defaults(func=cmd_extract_evidence)

    robust = subparsers.add_parser("run-robust-evals")
    robust.add_argument("--manifest", required=True)
    robust.add_argument("--evidence", required=True)
    robust.add_argument("--output", required=True)
    robust.add_argument("--remote-base", default="/home/team/make_datasets_runs")
    robust.add_argument("--temp-root", default="/home/team/make_datasets_analysis/.tmp/robust_eval")
    robust.add_argument("--agent")
    robust.add_argument("--run-id")
    robust.add_argument("--limit", type=int)
    robust.add_argument("--concurrency", type=int, default=4)
    robust.add_argument("--timeout-seconds", type=int, default=600)
    robust.add_argument("--resume", action="store_true")
    robust.set_defaults(func=cmd_run_robust_evals)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
