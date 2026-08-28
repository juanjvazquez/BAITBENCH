#!/usr/bin/env python3
"""Run scripts/summarize_judging_run.sh on the rh-evals-results volume.

Walks /results/judging/<label>/ on the volume and produces
/results/judging/<label>/summary/{summary.json,summary.md} via the existing
src/scoring/summarize_judging_run.py.

Usage:

    modal run scripts/summarize_on_modal.py --label vps1_20260506_234816
    modal run scripts/summarize_on_modal.py --label vps2_20260506_234825
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import modal


app = modal.App("rh-evals-summarize")
results_vol = modal.Volume.from_name("rh-evals-results", create_if_missing=False)

REPO_ROOT = Path(__file__).resolve().parent.parent

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("pandas>=2.3", "numpy>=2.0")
    .add_local_dir(str(REPO_ROOT / "src"), "/work/src")
)


@app.function(
    image=image,
    cpu=2,
    memory=4096,
    timeout=600,
    volumes={"/results": results_vol},
)
def summarize(label: str) -> dict:
    run_root = Path(f"/results/judging/{label}")
    if not run_root.is_dir():
        return {"ok": False, "error": f"missing {run_root}"}
    cmd = [
        "python3", "/work/src/scoring/summarize_judging_run.py",
        str(run_root),
    ]
    print(f"[summarize] {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.stdout:
        print(proc.stdout[-3000:], flush=True)
    if proc.returncode != 0:
        print(proc.stderr[-3000:], file=sys.stderr, flush=True)
    results_vol.commit()
    summary_dir = run_root / "summary"
    return {
        "label": label,
        "exit_code": proc.returncode,
        "summary_dir": str(summary_dir),
        "summary_files": sorted(p.name for p in summary_dir.iterdir()) if summary_dir.is_dir() else [],
    }


@app.local_entrypoint()
def main(label: str) -> None:
    print(json.dumps(summarize.remote(label), indent=2))
