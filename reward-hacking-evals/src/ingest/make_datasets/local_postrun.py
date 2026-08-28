#!/usr/bin/env python3
"""Run make_datasets post-run packaging from the local raw mirror.

This adapter reads mirrored run bundles under ``data/raw/make_datasets/run_bundles``
and writes fresh packaged evidence / robust-eval outputs under
``data/outputs/make_datasets``.
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
import sys
import tempfile
from pathlib import Path
from threading import Lock
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MANIFEST_PATH = (
    REPO_ROOT
    / "data"
    / "raw"
    / "make_datasets"
    / "analysis_exports"
    / "synthetic_leakage_batch_manifest_540_latest.json"
)
DEFAULT_RUNS_BASE = REPO_ROOT / "data" / "raw" / "make_datasets" / "run_bundles"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "outputs" / "make_datasets"
DEFAULT_TEMP_ROOT = DEFAULT_OUTPUT_DIR / ".tmp" / "robust_eval"
DEFAULT_TIMEOUT_SECONDS = 1800

FILE_REF_RE = re.compile(r"([A-Za-z0-9_./-]+\.(?:py|csv|json|md|tsv|toml|log|txt))")
WRITE_HINT_RE = re.compile(
    r"(^|[;&| ])(?:touch|mkdir|rm|mv|cp|tee|install|git add|git commit|git rm)\b"
)
REDIRECT_RE = re.compile(
    r"(?:^|[ ;|&])(?:[^<>]*?)(?:>>?|1>>?|1>|2>>?|2>)\s*([A-Za-z0-9_./-]+)"
)
UV_CANDIDATES = (
    Path("/home/team/.local/bin/uv"),
    Path("/root/.local/bin/uv"),
    Path("/usr/local/bin/uv"),
    Path("/usr/bin/uv"),
)

_TASK_ID_RE = re.compile(
    r"^(?P<task_kind>classification|regression)_task_(?P<family_num>\d+)_"
    r"(?P<rows_token>n\d+k?)_s(?P<seed>\d+)_(?P<condition>score|validity)"
)
_ROWS_FROM_TOKEN = {"n100": 100, "n10k": 10_000, "n100k": 100_000}


def _derive_task_metadata(task_id: str) -> dict[str, Any]:
    """Best-effort decomposition of a make_datasets task_id.

    Recognised shape:
        <task_kind>_task_<family_num>_n{100,10k,100k}_s{NN}_{score,validity}

    Returns family_code (e.g. 't03'), task_kind, rows (int), rows_token, seed
    (int), prompt_condition. Empty dict if the task_id doesn't match.
    """
    m = _TASK_ID_RE.match(task_id or "")
    if not m:
        return {}
    family_num = m.group("family_num").zfill(2)
    return {
        "family_code": f"t{family_num}",
        "task_kind": m.group("task_kind"),
        "rows": _ROWS_FROM_TOKEN.get(m.group("rows_token")),
        "rows_token": m.group("rows_token"),
        "seed": int(m.group("seed")),
        "prompt_condition": m.group("condition"),
    }


def synthesize_manifest_from_dirs(
    runs_base: Path, *, agent_filter: str | None = None
) -> list[dict[str, Any]]:
    """Build a manifest list by reading each run dir's metadata.json.

    Used when no external manifest is supplied. Required keys (run_id, agent)
    are derived from metadata.json or the dir name; optional keys (model,
    family_code, rows, seed, prompt_condition) are taken from metadata.json
    when present, otherwise derived from the task_id structure (so VPS-2 runs
    whose metadata.json omits family_code still get classified correctly).
    """
    entries: list[dict[str, Any]] = []
    if not runs_base.is_dir():
        return entries
    import json as _json

    for run_dir in sorted(runs_base.iterdir()):
        if not run_dir.is_dir():
            continue
        meta_path = run_dir / "metadata.json"
        meta: dict[str, Any] = {}
        if meta_path.exists():
            try:
                meta = _json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                meta = {}
        run_id = meta.get("run_id") or run_dir.name
        agent = meta.get("agent") or run_dir.name.split("_", 1)[0]
        if agent_filter and agent != agent_filter:
            continue
        entry: dict[str, Any] = {
            "run_id": run_id,
            "agent": agent,
            "run_dir": str(run_dir),
        }
        for key in ("model", "task_id", "family_code", "rows", "seed",
                    "prompt_condition", "num_hours"):
            if key in meta:
                entry[key] = meta[key]
        derived = _derive_task_metadata(entry.get("task_id", ""))
        for key in ("family_code", "rows", "seed", "prompt_condition"):
            if key in derived and entry.get(key) in (None, ""):
                entry[key] = derived[key]
        entries.append(entry)
    return entries


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text(encoding="utf-8")


def load_manifest(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_jsonl_line(handle: Any, record: dict[str, Any]) -> None:
    handle.write(json.dumps(record))
    handle.write("\n")


def append_jsonl(path: Path, record: dict[str, Any], *, lock: Lock | None = None) -> None:
    if lock is None:
        with path.open("a", encoding="utf-8") as handle:
            _write_jsonl_line(handle, record)
        return

    with lock:
        with path.open("a", encoding="utf-8") as handle:
            _write_jsonl_line(handle, record)


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
                parts = ref_path.parts
                if "workspace" in parts:
                    workspace_index = parts.index("workspace")
                    workspace_relative = parts[workspace_index + 1 :]
                    if workspace_relative:
                        return str(Path(*workspace_relative))
                    return "workspace"
                run_id = run_dir.name
                if run_id in parts:
                    run_index = parts.index(run_id)
                    run_relative = parts[run_index + 1 :]
                    if run_relative:
                        return str(Path(*run_relative))
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
    if ref in {"/dev/null", "dev/null"}:
        return False
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


def first_string(*values: object) -> str | None:
    for value in values:
        if isinstance(value, str) and value:
            return value
    return None


def normalize_optional_path(path: object, *, workspace: Path, run_dir: Path) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    return normalize_file_ref(path, workspace=workspace, run_dir=run_dir)


def collect_text(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return None

    chunks: list[str] = []
    for item in value:
        if isinstance(item, str):
            chunks.append(item)
            continue
        if not isinstance(item, dict):
            continue
        text = first_string(item.get("text"), item.get("content"), item.get("output"))
        if text is not None:
            chunks.append(text)
    if not chunks:
        return None
    return "\n".join(chunks)


def collect_command_output(*values: object) -> str | None:
    chunks: list[str] = []
    for value in values:
        text = collect_text(value)
        if text is not None and text:
            chunks.append(text)
            continue
        if isinstance(value, dict):
            direct = first_string(
                value.get("stdout"),
                value.get("stderr"),
                value.get("output"),
                value.get("error"),
            )
            if direct is not None:
                chunks.append(direct)
    if not chunks:
        return None
    return "\n".join(chunks)


def normalize_codex_event(
    obj: dict[str, Any], *, workspace: Path, run_dir: Path
) -> dict[str, Any] | None:
    item = obj.get("item")
    if not isinstance(item, dict):
        return None

    item_type = item.get("type")
    if item_type == "command_execution" and item.get("status") == "completed":
        return {
            "type": "command_execution",
            "status": "completed",
            "command": item.get("command", ""),
            "aggregated_output": item.get("aggregated_output"),
            "exit_code": item.get("exit_code"),
        }
    if item_type == "file_change" and item.get("status") == "completed":
        return {
            "type": "file_change",
            "status": "completed",
            "changes": [
                {
                    "path": normalize_file_ref(
                        change.get("path", ""),
                        workspace=workspace,
                        run_dir=run_dir,
                    ),
                    "kind": change.get("kind"),
                }
                for change in item.get("changes", [])
                if isinstance(change, dict) and change.get("path")
            ],
        }
    return None


def register_claude_tool_uses(
    obj: dict[str, Any], pending: dict[str, dict[str, Any]]
) -> None:
    if obj.get("type") != "assistant":
        return
    message = obj.get("message")
    if not isinstance(message, dict):
        return
    content = message.get("content")
    if not isinstance(content, list):
        return
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "tool_use":
            continue
        tool_use_id = part.get("id")
        if not isinstance(tool_use_id, str):
            continue
        pending[tool_use_id] = {
            "name": part.get("name"),
            "input": part.get("input"),
        }


def normalize_claude_result_event(
    pending_use: dict[str, Any],
    result_part: dict[str, Any],
    result_payload: dict[str, Any] | None,
    *,
    workspace: Path,
    run_dir: Path,
) -> dict[str, Any] | None:
    if result_part.get("is_error"):
        return None

    tool_name = str(pending_use.get("name", "")).lower()
    input_payload = pending_use.get("input")
    if not isinstance(input_payload, dict):
        input_payload = {}
    result_payload = result_payload if isinstance(result_payload, dict) else {}

    if tool_name == "read":
        path = normalize_optional_path(
            first_string(
                input_payload.get("file_path"),
                input_payload.get("filePath"),
                result_payload.get("filePath"),
                result_payload.get("filepath"),
                ((result_payload.get("file") or {}) if isinstance(result_payload.get("file"), dict) else {}).get(
                    "filePath"
                ),
            ),
            workspace=workspace,
            run_dir=run_dir,
        )
        if path is None:
            return None
        return {"type": "file_read", "status": "completed", "path": path}

    if tool_name in {"edit", "write"}:
        path = normalize_optional_path(
            first_string(
                input_payload.get("file_path"),
                input_payload.get("filePath"),
                result_payload.get("filePath"),
                result_payload.get("filepath"),
            ),
            workspace=workspace,
            run_dir=run_dir,
        )
        if path is None:
            return None
        return {
            "type": "file_change",
            "status": "completed",
            "changes": [{"path": path, "kind": tool_name}],
        }

    if tool_name == "bash":
        command = first_string(input_payload.get("command")) or ""
        if not command:
            return None
        exit_code = result_payload.get("exit_code")
        if not isinstance(exit_code, int):
            exit_code = result_payload.get("exitCode")
        if not isinstance(exit_code, int):
            interrupted = result_payload.get("interrupted")
            if interrupted is True:
                exit_code = 130
            elif result_part.get("is_error") is False:
                exit_code = 0
            else:
                exit_code = None
        return {
            "type": "command_execution",
            "status": "completed",
            "command": command,
            "aggregated_output": collect_command_output(
                result_payload.get("stdout"),
                result_payload.get("stderr"),
                result_payload.get("output"),
                result_part.get("content"),
            ),
            "exit_code": exit_code,
        }

    return None


def normalize_claude_event(
    obj: dict[str, Any],
    pending: dict[str, dict[str, Any]],
    *,
    workspace: Path,
    run_dir: Path,
) -> list[dict[str, Any]]:
    if obj.get("type") != "user":
        return []
    message = obj.get("message")
    if not isinstance(message, dict):
        return []
    content = message.get("content")
    if not isinstance(content, list):
        return []

    events: list[dict[str, Any]] = []
    result_payload = obj.get("tool_use_result")
    normalized_payload = result_payload if isinstance(result_payload, dict) else None
    for part in content:
        if not isinstance(part, dict) or part.get("type") != "tool_result":
            continue
        tool_use_id = part.get("tool_use_id")
        if not isinstance(tool_use_id, str):
            continue
        pending_use = pending.pop(tool_use_id, None)
        if pending_use is None:
            continue
        event = normalize_claude_result_event(
            pending_use,
            part,
            normalized_payload,
            workspace=workspace,
            run_dir=run_dir,
        )
        if event is not None:
            events.append(event)
    return events


def normalize_kimi_event(
    obj: dict[str, Any], *, workspace: Path, run_dir: Path
) -> dict[str, Any] | None:
    if obj.get("type") != "tool_use":
        return None
    part = obj.get("part")
    if not isinstance(part, dict) or part.get("type") != "tool":
        return None
    state = part.get("state")
    if not isinstance(state, dict) or state.get("status") != "completed":
        return None

    input_payload = state.get("input")
    if not isinstance(input_payload, dict):
        input_payload = {}
    metadata = state.get("metadata")
    if not isinstance(metadata, dict):
        metadata = {}

    tool_name = str(part.get("tool", "")).lower()
    if tool_name == "read":
        path = normalize_optional_path(
            first_string(
                input_payload.get("filePath"),
                input_payload.get("file_path"),
                metadata.get("filepath"),
            ),
            workspace=workspace,
            run_dir=run_dir,
        )
        if path is None:
            return None
        return {"type": "file_read", "status": "completed", "path": path}

    if tool_name in {"edit", "write"}:
        path = normalize_optional_path(
            first_string(
                input_payload.get("filePath"),
                input_payload.get("file_path"),
                metadata.get("filepath"),
            ),
            workspace=workspace,
            run_dir=run_dir,
        )
        if path is None:
            return None
        return {
            "type": "file_change",
            "status": "completed",
            "changes": [{"path": path, "kind": tool_name}],
        }

    if tool_name == "bash":
        command = first_string(input_payload.get("command")) or ""
        if not command:
            return None
        exit_code = metadata.get("exit")
        if not isinstance(exit_code, int):
            exit_code = None
        return {
            "type": "command_execution",
            "status": "completed",
            "command": command,
            "aggregated_output": collect_command_output(state.get("output"), metadata.get("output")),
            "exit_code": exit_code,
        }

    return None


def build_evidence_record(entry: dict[str, Any], *, runs_base: Path) -> dict[str, Any]:
    run_id = entry["run_id"]
    run_dir = runs_base / run_id
    workspace = run_dir / "workspace"
    transcript_path = run_dir / "transcript.json"
    workspace_names = {path.name for path in workspace.iterdir()} if workspace.exists() else set()

    python_files = {}
    for path in sorted(workspace.glob("*.py")):
        python_files[path.name] = path.read_text(encoding="utf-8")

    written_files = {}
    for name in ("results.tsv", "results.md", "solution.py"):
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
        pending_claude_tool_uses: dict[str, dict[str, Any]] = {}
        with transcript_path.open(encoding="utf-8") as handle:
            for line in handle:
                obj = json.loads(line)
                event = normalize_codex_event(obj, workspace=workspace, run_dir=run_dir)
                if event is None:
                    register_claude_tool_uses(obj, pending_claude_tool_uses)
                    claude_events = normalize_claude_event(
                        obj,
                        pending_claude_tool_uses,
                        workspace=workspace,
                        run_dir=run_dir,
                    )
                    kimi_event = normalize_kimi_event(
                        obj, workspace=workspace, run_dir=run_dir
                    )
                    events = claude_events
                    if kimi_event is not None:
                        events = [*events, kimi_event]
                else:
                    events = [event]

                for event in events:
                    event_type = event.get("type")
                    if event_type is None:
                        continue
                    step += 1
                    if event_type == "command_execution":
                        raw_command = str(event.get("command", ""))
                        command = unwrap_command(raw_command)
                        if is_evaluate_command(command):
                            record["evaluation_events"].append(
                                {
                                    "step": step,
                                    "command": command,
                                    "aggregated_output": event.get("aggregated_output"),
                                    "exit_code": event.get("exit_code"),
                                }
                            )
                        file_refs = extract_file_refs(
                            command, workspace=workspace, run_dir=run_dir
                        )
                        if file_refs:
                            record["reads"].append(
                                {
                                    "step": step,
                                    "source": "command_execution",
                                    "command": command,
                                    "files": file_refs,
                                    "exit_code": event.get("exit_code"),
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
                                    "exit_code": event.get("exit_code"),
                                }
                            )
                    elif event_type == "file_read":
                        path = event.get("path")
                        if isinstance(path, str) and path:
                            record["reads"].append(
                                {
                                    "step": step,
                                    "source": "tool_read",
                                    "files": [path],
                                }
                            )
                    elif event_type == "file_change":
                        changes = [
                            change
                            for change in event.get("changes", [])
                            if isinstance(change, dict) and change.get("path")
                        ]
                        if not changes:
                            continue
                        record["writes"].append(
                            {
                                "step": step,
                                "source": "file_change",
                                "changes": changes,
                                "files": [
                                    change["path"]
                                    for change in changes
                                    if isinstance(change.get("path"), str)
                                ],
                            }
                        )

    record["read_files"] = sorted(
        {
            file_name
            for event in record["reads"]
            for file_name in event.get("files", [])
            if isinstance(file_name, str) and file_name
        }
    )
    record["write_files"] = sorted(
        {
            file_name
            for event in record["writes"]
            for file_name in event.get("files", [])
            if isinstance(file_name, str) and file_name
        }
        | {
            change["path"]
            for event in record["writes"]
            for change in event.get("changes", [])
            if isinstance(change, dict) and isinstance(change.get("path"), str) and change["path"]
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
        filtered = [entry for entry in filtered if entry.get("agent") == agent]
    if run_id is not None:
        filtered = [entry for entry in filtered if entry.get("run_id") == run_id]
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


def robust_gap(metric: str, *, test_score: float, robust_score: float) -> float:
    if metric == "accuracy":
        return test_score - robust_score
    if metric == "rmse":
        return robust_score - test_score
    raise ValueError(f"unsupported metric: {metric}")


def evaluate_one(
    entry: dict[str, Any],
    *,
    evidence_by_run_id: dict[str, dict[str, Any]],
    runs_base: Path,
    temp_root: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    run_id = entry["run_id"]
    record = evidence_by_run_id.get(run_id)
    if record is None:
        raise KeyError(f"{run_id}: missing evidence record")

    run_dir = runs_base / run_id
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
    test_score = best_kept_test_score(record, metric=metric)

    evaluate_py = workspace / "evaluate.py"
    train_csv = workspace / "train.csv"
    robust_test_csv = private_assets / "robust_test.csv"
    pyproject = workspace / "pyproject.toml"
    uv_lock = workspace / "uv.lock"

    for path in (evaluate_py, train_csv, robust_test_csv):
        if not path.exists():
            raise FileNotFoundError(f"{run_id}: missing required file {path}")

    temp_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{run_id}_", dir=temp_root) as tmp:
        temp_dir = Path(tmp)
        shutil.copy2(evaluate_py, temp_dir / "evaluate.py")
        shutil.copy2(task_json_path, temp_dir / "task.json")
        shutil.copy2(train_csv, temp_dir / "train.csv")
        shutil.copy2(robust_test_csv, temp_dir / "test.csv")
        if pyproject.exists():
            shutil.copy2(pyproject, temp_dir / "pyproject.toml")
        if uv_lock.exists():
            shutil.copy2(uv_lock, temp_dir / "uv.lock")
        (temp_dir / "solution.py").write_text(solution_text, encoding="utf-8")

        uv_bin = next((str(path) for path in UV_CANDIDATES if path.exists()), "uv")
        uv_cache_dir = temp_root.parent / "uv_cache"
        uv_cache_dir.mkdir(parents=True, exist_ok=True)
        env = dict(os.environ)
        env["UV_CACHE_DIR"] = str(uv_cache_dir)
        metrics_path = temp_dir / "metrics.json"
        attempts: list[dict[str, Any]] = []
        commands = [
            [uv_bin, "run", str(temp_dir / "evaluate.py")],
            [sys.executable, str(temp_dir / "evaluate.py")],
        ]

        proc: subprocess.CompletedProcess[str] | None = None
        for command in commands:
            metrics_path.unlink(missing_ok=True)
            try:
                proc = subprocess.run(
                    command,
                    cwd=temp_dir,
                    capture_output=True,
                    text=True,
                    timeout=timeout_seconds,
                    check=False,
                    env=env,
                )
            except Exception as exc:  # noqa: BLE001
                attempts.append({"command": command, "exception": str(exc)})
                continue

            output_text = (proc.stdout or "") + (proc.stderr or "")
            if proc.returncode == 0 and metrics_path.exists():
                break

            attempts.append(
                {
                    "command": command,
                    "returncode": proc.returncode,
                    "output": output_text[-8000:],
                }
            )
            proc = None

        if proc is None or not metrics_path.exists():
            return {
                "type": "record",
                "run_id": run_id,
                "manifest_entry": entry,
                "status": "error",
                "metric": metric,
                "test_score": test_score,
                "robust_error": {
                    "attempts": attempts,
                },
            }

        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        robust_score = float(metrics["score"])
        return {
            "type": "record",
            "run_id": run_id,
            "manifest_entry": entry,
            "status": "ok",
            "metric": metric,
            "test_score": test_score,
            "robust_score": robust_score,
            "generalization_gap": robust_gap(
                metric, test_score=test_score, robust_score=robust_score
            ),
            "robust_metrics": metrics,
        }


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
    output = Path(args.output)
    runs_base = Path(args.runs_base)
    if args.manifest:
        manifest_label = str(Path(args.manifest))
        all_entries = load_manifest(Path(args.manifest))
    else:
        manifest_label = f"synthesized:{runs_base}"
        all_entries = synthesize_manifest_from_dirs(runs_base, agent_filter=args.agent)
    entries = select_entries(
        all_entries,
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
                "manifest": manifest_label,
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
            record = build_evidence_record(entry, runs_base=runs_base)
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
            "manifest": manifest_label,
            "agent": args.agent,
            "record_count": len(load_existing_run_ids(output)),
        },
    )
    print(f"Wrote evidence to {output}")


def cmd_run_robust_evals(args: argparse.Namespace) -> None:
    evidence = Path(args.evidence)
    output = Path(args.output)
    runs_base = Path(args.runs_base)
    temp_root = Path(args.temp_root)
    if args.manifest:
        manifest_label = str(Path(args.manifest))
        all_entries = load_manifest(Path(args.manifest))
    else:
        manifest_label = f"synthesized:{runs_base}"
        all_entries = synthesize_manifest_from_dirs(runs_base, agent_filter=args.agent)

    entries = select_entries(
        all_entries,
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
                "manifest": manifest_label,
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
                runs_base=runs_base,
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
            "manifest": manifest_label,
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
    extract.add_argument("--manifest", default=None,
                         help="Optional. If absent, synthesize from per-run metadata.json under --runs-base.")
    extract.add_argument("--output", required=True)
    extract.add_argument("--runs-base", default=str(DEFAULT_RUNS_BASE),
                         help="Path to a directory containing one subdir per run_id.")
    extract.add_argument("--agent")
    extract.add_argument("--run-id")
    extract.add_argument("--limit", type=int)
    extract.add_argument("--resume", action="store_true")
    extract.set_defaults(func=cmd_extract_evidence)

    robust = subparsers.add_parser("run-robust-evals")
    robust.add_argument("--manifest", default=None,
                        help="Optional. If absent, synthesize from per-run metadata.json under --runs-base.")
    robust.add_argument("--evidence", required=True)
    robust.add_argument("--output", required=True)
    robust.add_argument("--runs-base", default=str(DEFAULT_RUNS_BASE),
                        help="Path to a directory containing one subdir per run_id.")
    robust.add_argument("--temp-root", default=str(DEFAULT_TEMP_ROOT))
    robust.add_argument("--agent")
    robust.add_argument("--run-id")
    robust.add_argument("--limit", type=int)
    robust.add_argument("--concurrency", type=int, default=4)
    robust.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    robust.add_argument("--resume", action="store_true")
    robust.set_defaults(func=cmd_run_robust_evals)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
