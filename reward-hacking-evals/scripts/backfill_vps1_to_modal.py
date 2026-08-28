#!/usr/bin/env python3
"""Backfill VPS-1 evidence + robust scores onto rh-evals-results.

Reads /home/team/make_datasets_analysis/synthetic_540_<agent>_(evidence|robust_scores)*.jsonl
from VPS 1 over SSH, renames to the canonical layout that VPS-2 already uses:

    rh-evals-results/
    ├── evidence/vps1/<agent>_evidence.jsonl
    ├── robust_scores/vps1/<agent>_robust_scores.jsonl
    └── _provenance/vps1/<agent>_robust_inputs.json   (merge audit)

Robust score regen files (`*_no_keep_regen.jsonl`,
`*_timeout_1800_regen.jsonl`, `*_sklearn_regen.jsonl`) are merged into the
canonical robust file using **last-write-wins per run_id**: the canonical
score is overwritten by any regen that touches the same run_id, preserving
the historical "this regen patches the canonical" semantics. Provenance is
recorded so analysis can recover which source contributed each run_id.

After this runs, invoke `build_make_datasets_on_modal.py` with
--batch vps1 --skip-extract --skip-robust to produce cases/vps1/.

Usage:

    modal run scripts/backfill_vps1_to_modal.py
    modal run scripts/backfill_vps1_to_modal.py --agents claude
    modal run scripts/backfill_vps1_to_modal.py --dry-run
"""

from __future__ import annotations

import json
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import modal


app = modal.App("rh-evals-backfill-vps1")

results_vol = modal.Volume.from_name("rh-evals-results", create_if_missing=False)


image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("rsync", "openssh-client", "ca-certificates")
)

VPS1_ANALYSIS_DIR = "/home/team/make_datasets_analysis"
DEFAULT_AGENTS = ("claude", "codex", "kimi")


def _write_ssh_key_to_disk() -> Path:
    import os
    val = os.environ.get("SSH_PRIVATE_KEY", "")
    if not val:
        raise RuntimeError("SSH_PRIVATE_KEY env var not set")
    p = Path("/tmp/ssh_id")
    p.write_text(val if val.endswith("\n") else val + "\n")
    p.chmod(0o600)
    return p


def _rsync_pull(ssh_key: Path, ssh_host: str, file_list: list[str], dest: Path) -> int:
    files_from = Path("/tmp/backfill_files_from.txt")
    files_from.write_text("\n".join(file_list) + "\n")
    cmd = [
        "rsync", "-az", "--info=progress2",
        "--ignore-missing-args",
        "-e", f"ssh -i {ssh_key} -o BatchMode=yes -o StrictHostKeyChecking=accept-new",
        f"--files-from={files_from}",
        f"{ssh_host}:{VPS1_ANALYSIS_DIR}/",
        f"{dest}/",
    ]
    print(f"[rsync] pulling {len(file_list)} files -> {dest}", flush=True)
    proc = subprocess.run(cmd, check=False)
    return proc.returncode


def _load_records(path: Path) -> dict[str, dict]:
    """Load run_id -> record from a header-record-footer JSONL."""
    out: dict[str, dict] = {}
    if not path.exists():
        return out
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if obj.get("type") == "record" and "run_id" in obj:
                out[obj["run_id"]] = obj
    return out


def _merge_robust_scores(
    *, canonical_path: Path, regen_paths: list[Path], output_path: Path
) -> dict:
    """Last-write-wins merge of robust score files. Records provenance.

    Canonical file goes first, then each regen file in order; later writes
    overwrite earlier ones for the same run_id.
    """
    sources = [(canonical_path, "canonical")] + [
        (p, p.name.replace(".jsonl", "")) for p in regen_paths
    ]
    merged: dict[str, dict] = {}
    contributed: dict[str, str] = {}

    for path, label in sources:
        records = _load_records(path)
        for run_id, rec in records.items():
            merged[run_id] = rec
            contributed[run_id] = label

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w") as f:
        f.write(json.dumps({
            "type": "header",
            "merged_from": [str(p) for p, _ in sources],
            "record_count": len(merged),
        }) + "\n")
        for run_id in sorted(merged):
            f.write(json.dumps(merged[run_id]) + "\n")
        f.write(json.dumps({"type": "footer", "record_count": len(merged)}) + "\n")

    return {
        "output": str(output_path),
        "record_count": len(merged),
        "sources": [
            {"path": str(p), "label": label, "records": len(_load_records(p))}
            for p, label in sources
        ],
        "contributors": contributed,
    }


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("ssh-vps1")],
    volumes={"/results": results_vol},
    cpu=2,
    memory=4096,
    timeout=1800,
)
def backfill(agents: list[str], dry_run: bool, overwrite_evidence: bool = False) -> dict:
    """Pull VPS-1 evidence + robust score files, normalise into canonical layout."""
    import os

    ssh_key = _write_ssh_key_to_disk()
    ssh_host = os.environ["SSH_HOST"]
    print(f"[backfill] host={ssh_host} agents={agents} dry_run={dry_run}", flush=True)

    # 1) Build the file list to pull. We pull every synthetic_540_<agent>_*.jsonl
    #    (covers canonical + every regen variant) plus the manifests.
    file_list: list[str] = []
    for agent in agents:
        # Canonical evidence + robust
        file_list.append(f"synthetic_540_{agent}_evidence.jsonl")
        file_list.append(f"synthetic_540_{agent}_robust_scores.jsonl")
        # Per-agent regen variants we know about. rsync's --ignore-missing-args
        # tolerates absent files.
        for variant in ("no_keep_regen", "timeout_1800_regen", "sklearn_regen"):
            file_list.append(f"synthetic_540_{agent}_robust_scores_{variant}.jsonl")
    # Useful side files (kept under analysis/)
    file_list += [
        "synthetic_leakage_batch_manifest_540_latest.json",
        "kimi_sklearn_subset_manifest.json",
        "no_keep_subset_manifest.json",
        "_debug_timeout_claude.jsonl",
    ]

    raw_dir = Path("/tmp/vps1_raw")
    if raw_dir.exists():
        shutil.rmtree(raw_dir)
    raw_dir.mkdir(parents=True)

    if dry_run:
        print(f"[backfill] dry-run: would pull {len(file_list)} candidate files", flush=True)
    else:
        rc = _rsync_pull(ssh_key, ssh_host, file_list, raw_dir)
        if rc != 0:
            return {"ok": False, "error": f"rsync exit {rc}"}

    # 2) Per-agent: copy evidence, merge robust, write provenance.
    summary: dict[str, dict] = {}
    for agent in agents:
        ev_src = raw_dir / f"synthetic_540_{agent}_evidence.jsonl"
        ev_dst = Path(f"/results/evidence/vps1/{agent}_evidence.jsonl")
        ev_dst.parent.mkdir(parents=True, exist_ok=True)
        if ev_src.exists():
            should_copy = True
            if ev_dst.exists() and not overwrite_evidence:
                # Don't clobber a richer evidence file (e.g. one produced by
                # local_postrun.py with multi-format transcript parsers).
                # Compare record counts as a quick check.
                existing_records = len(_load_records(ev_dst))
                src_records = len(_load_records(ev_src))
                if existing_records >= src_records:
                    should_copy = False
                    print(f"[backfill] skip evidence for {agent}: existing has "
                          f"{existing_records} records (vs source {src_records}); "
                          f"pass --overwrite-evidence to force.", flush=True)
            if should_copy and not dry_run:
                shutil.copy2(ev_src, ev_dst)
            ev_records = len(_load_records(ev_src)) if ev_src.exists() else 0
        else:
            ev_records = 0
            print(f"[backfill] WARNING: missing evidence for {agent}: {ev_src}", flush=True)

        canonical_robust = raw_dir / f"synthetic_540_{agent}_robust_scores.jsonl"
        regen_paths = sorted(
            p for p in raw_dir.glob(f"synthetic_540_{agent}_robust_scores_*_regen.jsonl")
        )
        rb_dst = Path(f"/results/robust_scores/vps1/{agent}_robust_scores.jsonl")
        prov_dst = Path(f"/results/_provenance/vps1/{agent}_robust_inputs.json")

        if not dry_run and canonical_robust.exists():
            merge_info = _merge_robust_scores(
                canonical_path=canonical_robust,
                regen_paths=regen_paths,
                output_path=rb_dst,
            )
            prov_dst.parent.mkdir(parents=True, exist_ok=True)
            prov_dst.write_text(json.dumps(merge_info, indent=2))
        elif canonical_robust.exists():
            print(f"[backfill] (dry-run) would merge canonical + {len(regen_paths)} regens "
                  f"-> {rb_dst}", flush=True)

        summary[agent] = {
            "evidence_records": ev_records,
            "evidence_dst": str(ev_dst),
            "robust_canonical": str(canonical_robust) if canonical_robust.exists() else None,
            "robust_regens": [str(p) for p in regen_paths],
            "robust_dst": str(rb_dst),
            "provenance_dst": str(prov_dst),
        }

    # 3) Stash the manifests for posterity.
    manifests_dst = Path("/results/manifests/vps1")
    manifests_dst.mkdir(parents=True, exist_ok=True)
    manifest_files: list[str] = []
    for name in (
        "synthetic_leakage_batch_manifest_540_latest.json",
        "kimi_sklearn_subset_manifest.json",
        "no_keep_subset_manifest.json",
    ):
        src = raw_dir / name
        if src.exists() and not dry_run:
            shutil.copy2(src, manifests_dst / name)
            manifest_files.append(name)

    if not dry_run:
        results_vol.commit()

    return {
        "ok": True,
        "agents": summary,
        "manifests": manifest_files,
        "dry_run": dry_run,
    }


@app.local_entrypoint()
def main(
    agents: str = "claude,codex,kimi",
    dry_run: bool = False,
    overwrite_evidence: bool = False,
) -> None:
    """Backfill VPS-1 evidence + robust scores into the rh-evals-results volume.

    --overwrite-evidence: by default this script preserves existing evidence on
    the volume (so a re-extract by local_postrun.py with multi-format transcript
    parsers isn't clobbered by the older codex-only evidence on VPS 1). Pass
    --overwrite-evidence to force-replace.
    """
    agent_list = [a.strip() for a in agents.split(",") if a.strip()]
    print(f"[entrypoint] agents={agent_list} dry_run={dry_run} "
          f"overwrite_evidence={overwrite_evidence}", flush=True)
    result = backfill.remote(agent_list, dry_run, overwrite_evidence)
    print(json.dumps(result, indent=2))
