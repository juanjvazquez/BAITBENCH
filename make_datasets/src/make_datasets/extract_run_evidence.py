"""Extract mechanical evidence packets from remote run directories.

Examples:
    uv run src/make_datasets/extract_run_evidence.py
    uv run src/make_datasets/extract_run_evidence.py --agent codex
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from make_datasets.synthetic_suite import (  # noqa: E402
    DEFAULT_MANIFEST_PATH,
    REMOTE_BASE,
    VPS_HOST,
    VPS_KEY,
)

app = typer.Typer(help="Extract read/write evidence packets from remote run directories.")

REMOTE_SCRIPT = r"""
import json
import re
import shlex
import sys
from pathlib import Path

run_id = sys.argv[1]
remote_base = Path(sys.argv[2])
run_dir = remote_base / run_id
workspace = run_dir / "workspace"
transcript_path = run_dir / "transcript.json"
workspace_names = {path.name for path in workspace.iterdir()} if workspace.exists() else set()

FILE_REF_RE = re.compile(r'([A-Za-z0-9_./-]+\.(?:py|csv|json|md|tsv|toml|log))')
WRITE_HINT_RE = re.compile(
    r'(^|[;&| ])(?:touch|mkdir|rm|mv|cp|tee|install|git add|git commit|git rm)\b'
)
REDIRECT_RE = re.compile(r'(?:^|[ ;|&])(?:[^<>]*?)(?:>>?|1>>?|1>|2>>?|2>)\s*([A-Za-z0-9_./-]+)')


def read_text(path: Path) -> str | None:
    if not path.exists():
        return None
    return path.read_text()


def parse_trajectory(results_tsv: str | None) -> list[dict[str, object]]:
    if results_tsv is None:
        return []

    rows = []
    for raw_line in results_tsv.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split("\t", 5)
        if len(parts) not in {4, 6}:
            continue
        commit_hash, score_text, status, description = parts[:4]
        if commit_hash.lower() == "commit":
            continue
        try:
            score = float(score_text)
        except ValueError:
            continue
        row: dict[str, object] = {
            "commit_hash": commit_hash,
            "score": score,
            "status": status,
            "description": description,
        }
        if len(parts) == 6:
            row["validity"] = parts[4]
            row["reflection"] = parts[5]
        rows.append(row)
    return rows


def unwrap_command(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    if len(parts) >= 3 and parts[1] == "-lc":
        return parts[2]
    return command


def normalize_file_ref(ref: str) -> str:
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


def is_plausible_path(ref: str) -> bool:
    suffix = Path(ref).suffix
    return "/" in ref or suffix in {".py", ".csv", ".json", ".md", ".tsv", ".toml", ".log", ".txt"} or ref in workspace_names


def classify_write(command: str) -> bool:
    return (
        WRITE_HINT_RE.search(command) is not None
        or ">" in command
        or ">>" in command
    )


def extract_file_refs(command: str) -> list[str]:
    refs = []
    for match in FILE_REF_RE.finditer(command):
        normalized = normalize_file_ref(match.group(1))
        if normalized not in refs:
            refs.append(normalized)
    return refs


def extract_write_targets(command: str) -> list[str]:
    targets = []
    for match in REDIRECT_RE.finditer(command):
        normalized = normalize_file_ref(match.group(1))
        if is_plausible_path(normalized) and normalized not in targets:
            targets.append(normalized)
    touch_like = re.findall(
        r'(?:^|[ ;|&])(?:touch|rm|mkdir -p|mkdir|install -m \d+|install|cp|mv)\s+([A-Za-z0-9_./-]+)',
        command,
    )
    for target in touch_like:
        normalized = normalize_file_ref(target)
        if is_plausible_path(normalized) and normalized not in targets:
            targets.append(normalized)
    return targets


python_files = {}
for path in sorted(workspace.glob("*.py")):
    python_files[path.name] = path.read_text()

written_files = {}
for name in ("results.tsv", "results.md", "solution.py"):
    content = read_text(workspace / name)
    if content is not None:
        written_files[name] = content

record: dict[str, object] = {
    "run_id": run_id,
    "workspace_files": sorted(path.name for path in workspace.iterdir()) if workspace.exists() else [],
    "agent_exit_code": read_text(run_dir / "agent_exit_code.txt"),
    "python_files": python_files,
    "written_files": written_files,
    "reads": [],
    "writes": [],
    "trajectory": parse_trajectory(written_files.get("results.tsv")),
    "evaluation_events": [],
}

if transcript_path.exists():
    step = 0
    with transcript_path.open() as handle:
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
                file_refs = extract_file_refs(command)
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
                    write_targets = extract_write_targets(command)
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
                                "path": normalize_file_ref(change.get("path", "")),
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

print(json.dumps(record))
"""


def load_manifest(manifest_path: Path) -> list[dict[str, Any]]:
    return json.loads(manifest_path.read_text())


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record))
        handle.write("\n")


def select_entries(
    entries: list[dict[str, Any]],
    *,
    agent: str,
    run_id: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    filtered = [entry for entry in entries if entry["agent"] == agent]
    if run_id is not None:
        filtered = [entry for entry in filtered if entry["run_id"] == run_id]
    if limit is not None:
        filtered = filtered[:limit]
    return filtered


def remote_extract(run_id: str, *, host: str, key_path: str, remote_base: str) -> dict[str, Any]:
    proc = subprocess.run(
        ["ssh", "-i", key_path, host, "python3", "-", run_id, remote_base],
        input=REMOTE_SCRIPT,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{run_id}: remote extract failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout)


@app.command()
def main(
    manifest: Annotated[Path, typer.Option("--manifest")] = DEFAULT_MANIFEST_PATH,
    output: Annotated[Path, typer.Option("--output")] = (
        Path("runs/batches/synthetic_leakage_batch_codex_evidence.jsonl")
    ),
    agent: Annotated[str, typer.Option("--agent")] = "codex",
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    host: Annotated[str, typer.Option("--host")] = VPS_HOST,
    key_path: Annotated[str, typer.Option("--key-path")] = VPS_KEY,
    remote_base: Annotated[str, typer.Option("--remote-base")] = REMOTE_BASE,
) -> None:
    entries = select_entries(
        load_manifest(manifest),
        agent=agent,
        run_id=run_id,
        limit=limit,
    )
    output.unlink(missing_ok=True)
    append_jsonl(
        output,
        {
            "type": "header",
            "manifest": str(manifest),
            "agent": agent,
            "expected_record_count": len(entries),
        },
    )
    for index, entry in enumerate(entries, start=1):
        typer.echo(f"[{index}/{len(entries)}] extracting {entry['run_id']}")
        record = remote_extract(
            entry["run_id"],
            host=host,
            key_path=key_path,
            remote_base=remote_base,
        )
        record["manifest_entry"] = entry
        append_jsonl(output, {"type": "record", **record})

    append_jsonl(
        output,
        {
            "type": "footer",
            "manifest": str(manifest),
            "agent": agent,
            "record_count": len(entries),
        },
    )
    typer.echo(f"Wrote {len(entries)} records to {output}")


if __name__ == "__main__":
    app()
