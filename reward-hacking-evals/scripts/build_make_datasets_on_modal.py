#!/usr/bin/env python3
"""Run the make_datasets ingest pipeline on Modal: extract → robust → cases.

Reads run dirs from the make-datasets-mirror Modal volume, writes evidence,
robust scores, and case JSON to the rh-evals-results Modal volume. One Modal
container per robust-eval (64 GiB / 4 CPU / 1800s timeout) so the n100k ELM
solutions that OOM'd VPS 2 (16 GB host) finish cleanly.

Layout written into rh-evals-results:

    /evidence/<batch>/<agent>_evidence.jsonl
    /robust_scores/<batch>/<agent>_robust_scores.jsonl
    /cases/<batch>/cases.jsonl
    /cases/<batch>/records/<run_id>.json

Idempotent: --resume on extract and robust skips already-completed run_ids.

Usage:

    modal run scripts/build_make_datasets_on_modal.py --batch vps2 --agents "gemini,deepseek"

    # Dry run (lists run counts, doesn't launch robust-eval containers):
    modal run scripts/build_make_datasets_on_modal.py --batch vps2 --agents gemini --dry-run

    # Resume after partial run:
    modal run scripts/build_make_datasets_on_modal.py --batch vps2 --agents gemini --resume

    # Limit for smoke test:
    modal run scripts/build_make_datasets_on_modal.py --batch vps2 --agents gemini --limit 3
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import modal


app = modal.App("rh-evals-build-make-datasets")

mirror_vol = modal.Volume.from_name("make-datasets-mirror", create_if_missing=False)
results_vol = modal.Volume.from_name("rh-evals-results", create_if_missing=True)


REPO_ROOT = Path(__file__).resolve().parent.parent

# Image with uv (drives evaluate.py inside per-run tempdirs) + the repo source.
build_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("git", "curl", "ca-certificates", "build-essential")
    .pip_install("pandas>=2.3", "numpy>=2.0")
    .run_commands(
        # Install uv as root so /root/.local/bin/uv resolves (matches
        # postrun.UV_CANDIDATES).
        "curl -LsSf https://astral.sh/uv/install.sh | sh",
        "ln -sf /root/.local/bin/uv /usr/local/bin/uv",
    )
    .add_local_dir(str(REPO_ROOT / "src"), "/work/src")
    .add_local_file(str(REPO_ROOT / "pyproject.toml"), "/work/pyproject.toml")
)


# Per-batch defaults (mirrors mirror_into_modal.py).
BATCH_DEFAULTS = {
    "vps1": {"runs_subdir": "run_bundles_vps1"},
    "vps2": {"runs_subdir": "run_bundles_vps2"},
}


# ---------------------------------------------------------------------------
# Cheap container for evidence + cases-build (no per-run sklearn execution).
# ---------------------------------------------------------------------------


@app.function(
    image=build_image,
    cpu=4,
    memory=8192,
    timeout=3600,
    volumes={"/runs": mirror_vol, "/results": results_vol},
)
def extract_evidence(batch: str, agent: str, resume: bool, manifest_path: str = "") -> dict:
    """Run postrun extract-evidence for one (batch, agent).

    If `manifest_path` is given (path to a JSON list of run entries), filter
    extraction to those run_ids — useful for vps1 to scope down to the 540
    synthetic-suite runs and exclude UCI legacy runs.
    """
    runs_subdir = BATCH_DEFAULTS[batch]["runs_subdir"]
    runs_base = f"/runs/{runs_subdir}"
    out_dir = Path(f"/results/evidence/{batch}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{agent}_evidence.jsonl"
    cmd = [
        "python3", "/work/src/ingest/make_datasets/local_postrun.py",
        "extract-evidence",
        "--runs-base", runs_base,
        "--agent", agent,
        "--output", str(out),
    ]
    if manifest_path:
        cmd += ["--manifest", manifest_path]
    if resume:
        cmd.append("--resume")
    print(f"[extract] {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    print(proc.stdout[-2000:] if proc.stdout else "", flush=True)
    if proc.returncode != 0:
        print(proc.stderr[-2000:], file=sys.stderr, flush=True)
    results_vol.commit()
    n_records = sum(1 for _ in out.open()) if out.exists() else 0
    return {
        "batch": batch,
        "agent": agent,
        "exit_code": proc.returncode,
        "evidence_path": str(out),
        "evidence_lines": n_records,
    }


# ---------------------------------------------------------------------------
# Heavy per-run robust-eval container: 64 GiB / 4 CPU / 1800s.
# ---------------------------------------------------------------------------


@app.function(
    image=build_image,
    cpu=4,
    memory=65536,
    timeout=2200,
    retries=modal.Retries(max_retries=1, initial_delay=5.0),
    volumes={"/runs": mirror_vol, "/results": results_vol},
)
def robust_eval_one(batch: str, agent: str, run_id: str) -> dict:
    """Run postrun run-robust-evals on a single run_id.

    Per-run output is stored at /results/robust_scores/<batch>/<run_id>.jsonl
    (single-line file). The aggregator merges these into <agent>_robust_scores.jsonl.
    """
    runs_subdir = BATCH_DEFAULTS[batch]["runs_subdir"]
    runs_base = f"/runs/{runs_subdir}"
    evidence_path = f"/results/evidence/{batch}/{agent}_evidence.jsonl"
    out_dir = Path(f"/results/robust_scores/{batch}/_per_run/{agent}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{run_id}.jsonl"
    if out.exists() and out.stat().st_size > 0:
        return {"run_id": run_id, "status": "skipped_already_done"}
    cmd = [
        "python3", "/work/src/ingest/make_datasets/local_postrun.py",
        "run-robust-evals",
        "--runs-base", runs_base,
        "--agent", agent,
        "--run-id", run_id,
        "--evidence", evidence_path,
        "--output", str(out),
        "--temp-root", "/tmp/robust_eval",
        "--concurrency", "1",
        "--timeout-seconds", "1800",
    ]
    print(f"[robust] {run_id}", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stderr[-3000:], file=sys.stderr, flush=True)
    results_vol.commit()
    return {
        "run_id": run_id,
        "exit_code": proc.returncode,
        "stdout_tail": (proc.stdout or "")[-1000:],
        "stderr_tail": (proc.stderr or "")[-1000:],
    }


# ---------------------------------------------------------------------------
# Cheap container: merge per-run robust outputs into one file per agent.
# ---------------------------------------------------------------------------


@app.function(
    image=build_image,
    cpu=2,
    memory=4096,
    timeout=600,
    volumes={"/results": results_vol},
)
def merge_robust_scores(batch: str, agent: str) -> dict:
    src_dir = Path(f"/results/robust_scores/{batch}/_per_run/{agent}")
    out_dir = Path(f"/results/robust_scores/{batch}")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{agent}_robust_scores.jsonl"

    n_records = 0
    with out.open("w") as outf:
        outf.write(json.dumps({
            "type": "header",
            "batch": batch,
            "agent": agent,
            "merged_from": str(src_dir),
        }) + "\n")
        if src_dir.is_dir():
            for f in sorted(src_dir.iterdir()):
                if f.suffix != ".jsonl":
                    continue
                for line in f.open():
                    line = line.strip()
                    if not line:
                        continue
                    outf.write(line + "\n")
                    obj = json.loads(line)
                    if obj.get("type") in (None, "record"):
                        n_records += 1
        outf.write(json.dumps({
            "type": "footer",
            "record_count": n_records,
        }) + "\n")
    results_vol.commit()
    return {"batch": batch, "agent": agent, "records": n_records, "path": str(out)}


# ---------------------------------------------------------------------------
# Cheap container: build canonical cases.jsonl from evidence + robust scores.
# ---------------------------------------------------------------------------


@app.function(
    image=build_image,
    cpu=2,
    memory=4096,
    timeout=900,
    volumes={"/results": results_vol},
)
def build_cases(batch: str, agents: list[str]) -> dict:
    """Build canonical cases.jsonl using src/cases/build_make_datasets_cases.py.

    The canonical builder expects a single directory containing both
    <agent>_evidence.jsonl and <agent>_robust_scores.jsonl side by side. We
    stage symlinks into /tmp/cases_input/<batch>/ pointing at the per-batch
    files on the volume, then invoke the canonical script unchanged. This
    guarantees both batches produce the same cases schema (the one the
    existing judges already know how to read).
    """
    import shutil

    stage_dir = Path(f"/tmp/cases_input/{batch}")
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)

    found_pairs: list[str] = []
    for agent in agents:
        ev = Path(f"/results/evidence/{batch}/{agent}_evidence.jsonl")
        rb = Path(f"/results/robust_scores/{batch}/{agent}_robust_scores.jsonl")
        if not ev.exists():
            print(f"[cases] WARNING: evidence missing for {agent}: {ev}", flush=True)
            continue
        # Symlink into stage dir using names the canonical discoverer expects.
        (stage_dir / ev.name).symlink_to(ev)
        if rb.exists():
            (stage_dir / rb.name).symlink_to(rb)
        else:
            print(f"[cases] WARNING: robust scores missing for {agent}: {rb}", flush=True)
        found_pairs.append(agent)

    if not found_pairs:
        return {"batch": batch, "case_count": 0, "error": "no evidence files found"}

    out_dir = Path(f"/results/cases/{batch}")
    records_dir = out_dir / "records"
    cases_jsonl = out_dir / "cases.jsonl"
    manifest_path = out_dir / "build_manifest.json"
    records_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python3", "/work/src/cases/build_make_datasets_cases.py",
        "--input-dir", str(stage_dir),
        "--output-dir", str(records_dir),
        "--output-jsonl", str(cases_jsonl),
        "--manifest-path", str(manifest_path),
    ]
    print(f"[cases] {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout[-3000:], flush=True)
    if proc.returncode != 0:
        print(proc.stderr[-3000:], file=sys.stderr, flush=True)
        return {
            "batch": batch,
            "agents": found_pairs,
            "exit_code": proc.returncode,
            "error": proc.stderr[-1000:],
        }

    n = sum(1 for _ in cases_jsonl.open()) if cases_jsonl.exists() else 0
    results_vol.commit()
    return {
        "batch": batch,
        "agents": found_pairs,
        "case_count": n,
        "cases_path": str(cases_jsonl),
        "records_dir": str(records_dir),
        "manifest_path": str(manifest_path),
    }


# ---------------------------------------------------------------------------
# Helpers used by the local entrypoint.
# ---------------------------------------------------------------------------


def _list_run_ids_from_evidence(evidence_path: Path) -> list[str]:
    """Read a header-record-footer evidence.jsonl and return run_ids."""
    out: list[str] = []
    with evidence_path.open() as f:
        for line in f:
            obj = json.loads(line)
            if obj.get("type") == "record":
                out.append(obj["run_id"])
    return out


@app.function(
    image=build_image,
    cpu=1,
    memory=512,
    volumes={"/results": results_vol},
    timeout=120,
)
def list_evidence_run_ids(batch: str, agent: str) -> list[str]:
    p = Path(f"/results/evidence/{batch}/{agent}_evidence.jsonl")
    if not p.exists():
        return []
    return _list_run_ids_from_evidence(p)


@app.function(
    image=build_image,
    cpu=1,
    memory=512,
    volumes={"/results": results_vol},
    timeout=120,
)
def list_done_robust_run_ids(batch: str, agent: str) -> list[str]:
    src_dir = Path(f"/results/robust_scores/{batch}/_per_run/{agent}")
    if not src_dir.is_dir():
        return []
    return [p.stem for p in src_dir.iterdir() if p.suffix == ".jsonl" and p.stat().st_size > 0]


# ---------------------------------------------------------------------------
# Local entrypoint: orchestrates extract → robust fan-out → merge → cases.
# ---------------------------------------------------------------------------


@app.local_entrypoint()
def main(
    batch: str,
    agents: str = "",
    resume: bool = True,
    dry_run: bool = False,
    limit: int = 0,
    skip_extract: bool = False,
    skip_robust: bool = False,
    skip_cases: bool = False,
    manifest_path: str = "",
) -> None:
    """Top-level orchestrator.

    --batch          vps1 | vps2
    --agents         comma-separated; defaults vps2 -> "gemini,deepseek",
                     vps1 -> "claude,codex,kimi"
    --resume         skip already-extracted / already-robust-eval'd runs (default: True)
    --dry-run        list counts and exit; no robust-eval containers launched
    --limit N        cap robust-eval fan-out at N runs per agent (smoke test)
    --skip-extract   skip extract-evidence step (useful when re-running cases only)
    --skip-robust    skip robust-eval fan-out
    --skip-cases     skip cases-build
    --manifest-path  path on the rh-evals-results volume to a manifest JSON to
                     filter runs (e.g. /results/manifests/vps1/...). When
                     unset, synthesize manifest from per-run metadata.json.
    """
    if batch not in BATCH_DEFAULTS:
        raise SystemExit(f"unknown --batch {batch!r}")
    agent_list = [a for a in agents.split(",") if a.strip()] or (
        ["claude", "codex", "kimi"] if batch == "vps1" else ["gemini", "deepseek"]
    )
    print(f"[entrypoint] batch={batch} agents={agent_list} resume={resume} "
          f"dry_run={dry_run} limit={limit} manifest_path={manifest_path or '(synthesize)'}",
          flush=True)

    if not skip_extract:
        for agent in agent_list:
            print(f"\n[entrypoint] extract-evidence({agent})", flush=True)
            r = extract_evidence.remote(batch, agent, resume, manifest_path)
            print(f"  -> {r}", flush=True)

    if not skip_robust:
        for agent in agent_list:
            print(f"\n[entrypoint] enumerating runs for robust-eval({agent})", flush=True)
            all_ids = list_evidence_run_ids.remote(batch, agent)
            done_ids = set(list_done_robust_run_ids.remote(batch, agent)) if resume else set()
            todo = [r for r in all_ids if r not in done_ids]
            if limit > 0:
                todo = todo[:limit]
            print(f"  total={len(all_ids)}  already_done={len(done_ids)}  todo={len(todo)}", flush=True)
            if dry_run:
                print(f"  [dry-run] would launch {len(todo)} robust-eval containers", flush=True)
                continue
            if todo:
                results = list(robust_eval_one.starmap(
                    [(batch, agent, run_id) for run_id in todo],
                    return_exceptions=True,
                ))
                ok = sum(1 for r in results if isinstance(r, dict) and r.get("exit_code") == 0)
                errs = sum(1 for r in results if not (isinstance(r, dict) and r.get("exit_code") == 0))
                print(f"  -> robust_eval ok={ok} err_or_skip={errs}", flush=True)
            else:
                print(f"  -> all {len(all_ids)} robust evals already done; skipping fan-out", flush=True)
            # Always merge so the canonical <agent>_robust_scores.jsonl exists.
            r = merge_robust_scores.remote(batch, agent)
            print(f"  -> merge: {r}", flush=True)

    if not skip_cases and not dry_run:
        print(f"\n[entrypoint] build_cases", flush=True)
        r = build_cases.remote(batch, agent_list)
        print(f"  -> {r}", flush=True)

    print(f"\n[entrypoint] done.", flush=True)
