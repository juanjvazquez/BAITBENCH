#!/usr/bin/env python3
"""Run the shared judging pipeline (binary -> family -> classification) on Modal.

Reads cases from /results/cases/<batch>/cases.jsonl on the rh-evals-results
volume, writes judging outputs to /results/judging/<label>/<batch>/<backend>__<model>/.

Per-batch + per-backend fan-out: one container per (batch, backend) combination,
because the existing judging code already does internal concurrency=8 against
its API. Three backends -> three parallel containers per batch.

Cases.jsonl is treated as the canonical input. The judging code already
supports JSONL input via run_shared_judging_pipeline.py.

Usage:

    modal run scripts/judge_on_modal.py --batch vps2 --label vps2_20260506 \
        --backends "anthropic,openai,openrouter"

    # Single backend smoke test:
    modal run scripts/judge_on_modal.py --batch vps2 --label vps2_smoke \
        --backends anthropic --limit 5

    # Resume / append mode:
    modal run scripts/judge_on_modal.py --batch vps2 --label vps2_20260506 \
        --backends anthropic --append
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import modal


app = modal.App("rh-evals-judge")

results_vol = modal.Volume.from_name("rh-evals-results", create_if_missing=False)


REPO_ROOT = Path(__file__).resolve().parent.parent

judge_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ca-certificates")
    .pip_install("pandas>=2.3", "numpy>=2.0")
    .add_local_dir(str(REPO_ROOT / "src"), "/work/src")
)


@app.function(
    image=judge_image,
    cpu=2,
    memory=4096,
    timeout=7200,  # 2h for full 443-case judge across one backend
    secrets=[
        modal.Secret.from_name("judge-anthropic"),
        modal.Secret.from_name("judge-openai"),
        modal.Secret.from_name("judge-openrouter"),
    ],
    volumes={"/results": results_vol},
)
def judge_one_backend(
    batch: str,
    backend: str,
    label: str,
    model: str = "auto",
    concurrency: int = 8,
    limit: int = 0,
    run_id: str = "",
    append: bool = False,
    include_h_classification: bool = False,
) -> dict:
    """Run binary + family judging (and optionally H-code) for one (batch, backend).

    By default the fine-grained H-code stage is skipped. Pass
    `include_h_classification=True` to opt in (the orchestrator's
    --include-h-classification flag).
    """
    cases_jsonl = f"/results/cases/{batch}/cases.jsonl"
    if not Path(cases_jsonl).exists():
        return {
            "batch": batch,
            "backend": backend,
            "ok": False,
            "error": f"cases.jsonl missing at {cases_jsonl}",
        }
    # Resolve "auto" to the actual default model so the output dirname encodes
    # the real model used. Mirrors src/judges/backends.py:DEFAULT_MODELS.
    BACKEND_DEFAULTS = {
        "openai": "gpt-5.4",
        "anthropic": "claude-opus-4-6",
        "openrouter": "z-ai/glm-5.1",
    }
    resolved_model = BACKEND_DEFAULTS[backend] if model == "auto" else model
    out_dir = Path(f"/results/judging/{label}/{batch}/{backend}__{resolved_model.replace('/', '__')}")
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "judging.log"

    cmd = [
        "python3", "/work/src/judges/run_shared_judging_pipeline.py",
        cases_jsonl,
        "--output-dir", str(out_dir),
        "--source-project", "make_datasets",
        "--backend", backend,
        "--model", model,
        "--concurrency", str(concurrency),
    ]
    if limit > 0:
        cmd += ["--limit", str(limit)]
    if run_id:
        cmd += ["--run-id", run_id]
    if append:
        cmd.append("--append")
    if include_h_classification:
        cmd.append("--include-h-classification")

    print(f"[judge] batch={batch} backend={backend} -> {out_dir}", flush=True)
    print(f"[judge] cmd: {' '.join(cmd)}", flush=True)
    with log_path.open("w") as logf:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        logf.write(proc.stdout or "")
    print((proc.stdout or "")[-3000:], flush=True)
    results_vol.commit()
    return {
        "batch": batch,
        "backend": backend,
        "model": model,
        "label": label,
        "exit_code": proc.returncode,
        "out_dir": str(out_dir),
        "log_path": str(log_path),
    }


@app.local_entrypoint()
def main(
    batch: str,
    label: str,
    backends: str = "anthropic,openai,openrouter",
    model: str = "auto",
    concurrency: int = 8,
    limit: int = 0,
    run_id: str = "",
    append: bool = False,
    include_h_classification: bool = False,
) -> None:
    """Top-level CLI.

    --batch                       vps1 | vps2 | all
    --label                       output dir leaf, e.g. "vps2_20260506_174030"
    --backends                    comma-separated subset of {anthropic, openai, openrouter}
    --model                       "auto" picks the backend default; or pass an explicit name
    --concurrency                 per-backend judge concurrency (default 8)
    --limit N                     cap to first N cases per backend (smoke test)
    --append                      append/resume — skip already-judged case ids
    --include-h-classification    opt in to the soft-deprecated H-code stage (default off).
    """
    backend_list = [b.strip() for b in backends.split(",") if b.strip()]
    print(f"[entrypoint] batch={batch} label={label} backends={backend_list} "
          f"model={model} limit={limit} append={append} "
          f"include_h_classification={include_h_classification}", flush=True)
    results = list(judge_one_backend.starmap(
        [(batch, backend, label, model, concurrency, limit, run_id, append, include_h_classification)
         for backend in backend_list],
        return_exceptions=True,
    ))
    print()
    failures = []
    for r in results:
        print(f"  -> {r}", flush=True)
        if isinstance(r, BaseException):
            failures.append(r)
        elif isinstance(r, dict) and r.get("exit_code") not in (0, None):
            failures.append(r)
    if failures:
        # Make the failure visible to operators / GitHub Actions / wrappers.
        print(
            f"\n[entrypoint] FAILURE: {len(failures)}/{len(results)} backend(s) "
            f"did not complete cleanly.",
            flush=True,
        )
        for f in failures:
            print(f"  - {f}", flush=True)
        raise SystemExit(1)
