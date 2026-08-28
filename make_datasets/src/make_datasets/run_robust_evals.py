"""Run local robust evaluations for synthetic-suite solutions.

Examples:
    uv run src/make_datasets/run_robust_evals.py
    uv run src/make_datasets/run_robust_evals.py --limit 1
    uv run src/make_datasets/run_robust_evals.py --run-id codex_gpt-5.4_regression_task_01_n100_s01_score_20260411_080203
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Annotated, Any

import typer

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

DEFAULT_MANIFEST_PATH = Path("runs/batches/synthetic_leakage_batch_manifest.json")
DEFAULT_EVIDENCE_PATH = Path("runs/batches/synthetic_leakage_batch_codex_evidence.jsonl")
DEFAULT_OUTPUT_PATH = Path("runs/batches/synthetic_leakage_batch_codex_robust_scores.jsonl")
GENERATED_TASKS_DIR = REPO_ROOT / "tasks" / "generated"
TEMP_ROOT = REPO_ROOT / ".tmp" / "robust_eval"
DEFAULT_TIMEOUT_SECONDS = 600

app = typer.Typer(help="Run local robust evaluations for extracted synthetic-suite solutions.")


def load_manifest(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_evidence(path: Path) -> dict[str, dict[str, Any]]:
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


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
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


def select_entries(
    entries: list[dict[str, Any]],
    *,
    run_id: str | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    filtered = entries
    if run_id is not None:
        filtered = [entry for entry in filtered if entry["run_id"] == run_id]
    if limit is not None:
        filtered = filtered[:limit]
    return filtered


def task_dir_for(entry: dict[str, Any]) -> Path:
    task_id = entry["task_id"]
    task_dir = GENERATED_TASKS_DIR / task_id
    if not task_dir.exists():
        raise FileNotFoundError(f"{entry['run_id']}: missing generated task directory {task_dir}")
    return task_dir


def better_score(metric: str, candidate: float, incumbent: float | None) -> bool:
    if incumbent is None:
        return True
    if metric == "accuracy":
        return candidate > incumbent
    if metric == "rmse":
        return candidate < incumbent
    raise ValueError(f"unsupported metric: {metric}")


def parse_trajectory(results_tsv: str | None) -> list[dict[str, Any]]:
    if results_tsv is None:
        return []

    rows: list[dict[str, Any]] = []
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
        row: dict[str, Any] = {
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


def trajectory_rows(record: dict[str, Any]) -> list[dict[str, Any]]:
    rows = record.get("trajectory")
    if isinstance(rows, list) and rows:
        return rows
    written_files = record.get("written_files", {})
    if isinstance(written_files, dict):
        results_tsv = written_files.get("results.tsv")
        if isinstance(results_tsv, str):
            return parse_trajectory(results_tsv)
    return []


def best_kept_test_score(record: dict[str, Any], *, metric: str) -> float:
    kept_rows = [
        row
        for row in trajectory_rows(record)
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
    if best is None:
        raise AssertionError("best score should have been set")
    return best


def write_temp_workspace(
    temp_dir: Path,
    *,
    solution_text: str,
    task_dir: Path,
) -> None:
    shutil.copy2(task_dir / "evaluate.py", temp_dir / "evaluate.py")
    shutil.copy2(task_dir / "task.json", temp_dir / "task.json")
    shutil.copy2(task_dir / "train.csv", temp_dir / "train.csv")
    shutil.copy2(task_dir / "robust_test.csv", temp_dir / "test.csv")
    (temp_dir / "solution.py").write_text(solution_text, encoding="utf-8")


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
    timeout_seconds: int,
) -> dict[str, Any]:
    run_id = entry["run_id"]
    record = evidence_by_run_id.get(run_id)
    if record is None:
        raise KeyError(f"{run_id}: missing evidence record")

    solution_text = record.get("written_files", {}).get("solution.py")
    if not isinstance(solution_text, str) or not solution_text.strip():
        raise ValueError(f"{run_id}: missing solution.py in evidence")

    task_dir = task_dir_for(entry)
    task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
    metric = task["metric"]
    test_score = best_kept_test_score(record, metric=metric)

    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"{run_id}_", dir=TEMP_ROOT) as tmp:
        temp_dir = Path(tmp)
        write_temp_workspace(temp_dir, solution_text=solution_text, task_dir=task_dir)
        proc = subprocess.run(
            ["uv", "run", str(temp_dir / "evaluate.py")],
            cwd=temp_dir,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            check=False,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"{run_id}: robust evaluation failed with exit code {proc.returncode}: "
                f"{proc.stderr.strip() or proc.stdout.strip()}"
            )
        metrics = json.loads((temp_dir / "metrics.json").read_text(encoding="utf-8"))
        robust_score = float(metrics["score"])

    return {
        "type": "record",
        "run_id": run_id,
        "manifest_entry": entry,
        "metric": metric,
        "test_score": test_score,
        "robust_score": robust_score,
        "robust_gap": robust_gap(metric, test_score=test_score, robust_score=robust_score),
    }


def evaluate_one_safe(
    entry: dict[str, Any],
    *,
    evidence_by_run_id: dict[str, dict[str, Any]],
    timeout_seconds: int,
) -> dict[str, Any]:
    try:
        return evaluate_one(
            entry,
            evidence_by_run_id=evidence_by_run_id,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        metric: str | None = None
        test_score: float | None = None
        record = evidence_by_run_id.get(entry["run_id"])
        if record is not None:
            try:
                task_dir = task_dir_for(entry)
                task = json.loads((task_dir / "task.json").read_text(encoding="utf-8"))
                metric = task["metric"]
                test_score = best_kept_test_score(record, metric=metric)
            except Exception:
                metric = None
                test_score = None
        return {
            "type": "record",
            "run_id": entry["run_id"],
            "manifest_entry": entry,
            "metric": metric,
            "test_score": test_score,
            "robust_score": None,
            "robust_gap": None,
            "error": str(exc),
        }


@app.command()
def main(
    manifest: Annotated[Path, typer.Option("--manifest")] = DEFAULT_MANIFEST_PATH,
    evidence: Annotated[Path, typer.Option("--evidence")] = DEFAULT_EVIDENCE_PATH,
    output: Annotated[Path, typer.Option("--output")] = DEFAULT_OUTPUT_PATH,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    run_id: Annotated[str | None, typer.Option("--run-id")] = None,
    concurrency: Annotated[int, typer.Option("--concurrency")] = 4,
    append: Annotated[bool, typer.Option("--append")] = False,
    timeout_seconds: Annotated[int, typer.Option("--timeout-seconds")] = DEFAULT_TIMEOUT_SECONDS,
) -> None:
    entries = select_entries(load_manifest(manifest), run_id=run_id, limit=limit)
    evidence_by_run_id = load_evidence(evidence)
    entries = [entry for entry in entries if entry["run_id"] in evidence_by_run_id]

    if append:
        existing = load_existing_run_ids(output)
        entries = [entry for entry in entries if entry["run_id"] not in existing]
    else:
        output.unlink(missing_ok=True)
        append_jsonl(
            output,
            {
                "type": "header",
                "manifest": str(manifest),
                "evidence": str(evidence),
                "expected_record_count": len(entries),
                "concurrency": concurrency,
                "timeout_seconds": timeout_seconds,
            },
        )

    write_lock = Lock()
    completed = 0
    failures = 0
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_entry = {
            executor.submit(
                evaluate_one_safe,
                entry,
                evidence_by_run_id=evidence_by_run_id,
                timeout_seconds=timeout_seconds,
            ): entry
            for entry in entries
        }
        for future in as_completed(future_to_entry):
            entry = future_to_entry[future]
            result = future.result()
            with write_lock:
                append_jsonl(output, result)
                completed += 1
                if result.get("error"):
                    failures += 1
                    typer.echo(f"[{completed}/{len(entries)}] robust-eval failed {entry['run_id']}")
                else:
                    typer.echo(f"[{completed}/{len(entries)}] robust-evaluated {entry['run_id']}")

    if not append:
        append_jsonl(
            output,
            {
                "type": "footer",
                "manifest": str(manifest),
                "evidence": str(evidence),
                "record_count": len(entries),
                "concurrency": concurrency,
                "timeout_seconds": timeout_seconds,
                "failure_count": failures,
            },
        )
    typer.echo(f"Wrote {len(entries)} robust evaluation records to {output}")


if __name__ == "__main__":
    app()
