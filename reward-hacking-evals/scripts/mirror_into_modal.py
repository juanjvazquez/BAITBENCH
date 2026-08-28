#!/usr/bin/env python3
"""Mirror a VPS's make_datasets_runs/ directory into a Modal volume.

Runs entirely inside a Modal container (no laptop-side rsync). Reads the SSH
private key + host from a Modal Secret. Excludes giant per-run CSVs that
robust-eval doesn't actually need re-mirrored every time (train.csv, test.csv
in the workspace; the held-out robust_test.csv stays).

Usage (from any machine with `modal` configured for the spar-maded profile):

    modal run scripts/mirror_into_modal.py --batch vps2

    # Re-mirror just one run for debugging:
    modal run scripts/mirror_into_modal.py --batch vps2 --include-runs gemini_..._s01_score_...

    # Mirror VPS-1 raw (rare; the existing analysis files are usually enough):
    modal run scripts/mirror_into_modal.py --batch vps1

After this, `build_make_datasets_on_modal.py` reads `/runs/run_bundles_<batch>/`
inside its containers and calls `postrun.py` against it.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import modal


app = modal.App("rh-evals-mirror")

mirror_vol = modal.Volume.from_name(
    "make-datasets-mirror", create_if_missing=True
)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("rsync", "openssh-client", "ca-certificates")
)


# Default per-batch settings.
BATCH_DEFAULTS = {
    "vps1": {
        "secret_name": "ssh-vps1",
        "remote_path": "/home/team/make_datasets_runs",
        "subdir": "run_bundles_vps1",
        # VPS-1 has 540 runs; the canonical mirror script (raw_540) only pulls
        # specific files. We do the same by default to keep size sane.
        "include_globs": [
            "transcript.json",
            "metadata.json",
            "agent_stderr.log",
            "agent_exit_code.txt",
            "run_agent.sh",
            ".private_task_assets/robust_test.csv",
            "workspace/evaluate.py",
            "workspace/experimental_setup.json",
            "workspace/metrics.json",
            "workspace/modal_app.py",
            "workspace/modal_dispatch.py",
            "workspace/prepare.py",
            "workspace/program.md",
            "workspace/pyproject.toml",
            "workspace/results.md",
            "workspace/results.tsv",
            "workspace/run.log",
            "workspace/solution.py",
            "workspace/task.json",
            "workspace/test.csv",
            "workspace/timer.py",
            "workspace/train.csv",
            "workspace/train_sft.py",
            "workspace/uv.lock",
        ],
    },
    "vps2": {
        "secret_name": "ssh-vps2",
        "remote_path": "/home/team/make_datasets_runs",
        "subdir": "run_bundles_vps2",
        # Includes train.csv (80 MB x ~443 = ~35 GB) — needed because robust
        # eval re-runs solution.py against (train, robust_test) for the
        # generalization gap. Storage is cheap; pull-on-demand is complex.
        "include_globs": [
            "transcript.json",
            "metadata.json",
            "agent_stderr.log",
            "agent_exit_code.txt",
            "run_agent.sh",
            ".private_task_assets/robust_test.csv",
            "workspace/evaluate.py",
            "workspace/metrics.json",
            "workspace/program.md",
            "workspace/pyproject.toml",
            "workspace/results.md",
            "workspace/results.tsv",
            "workspace/run.log",
            "workspace/solution.py",
            "workspace/task.json",
            "workspace/test.csv",
            "workspace/timer.py",
            "workspace/train.csv",
            "workspace/uv.lock",
        ],
    },
}


def _write_ssh_key_to_disk() -> Path:
    """Materialize SSH_PRIVATE_KEY env var (from Modal Secret) to a 0600 file."""
    import os

    key_value = os.environ.get("SSH_PRIVATE_KEY", "")
    if not key_value:
        raise RuntimeError("SSH_PRIVATE_KEY env var not set (Secret not mounted?)")
    p = Path("/tmp/ssh_id")
    p.write_text(key_value if key_value.endswith("\n") else key_value + "\n")
    p.chmod(0o600)
    return p


def _build_files_from_list(
    *,
    ssh_host: str,
    ssh_key: Path,
    remote_path: str,
    include_globs: list[str],
    include_prefixes: list[str] | None,
    include_runs: list[str] | None,
) -> list[str]:
    """Generate the rsync --files-from list by listing remote run dirs."""
    list_cmd = (
        f"ls -1 {shlex.quote(remote_path)}"
    )
    proc = subprocess.run(
        [
            "ssh",
            "-i",
            str(ssh_key),
            "-o",
            "BatchMode=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            ssh_host,
            list_cmd,
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=120,
    )
    run_ids = [line.strip() for line in proc.stdout.splitlines() if line.strip()]

    if include_runs:
        wanted = set(include_runs)
        run_ids = [r for r in run_ids if r in wanted]
    elif include_prefixes:
        run_ids = [
            r for r in run_ids
            if any(r.startswith(prefix) for prefix in include_prefixes)
        ]

    files = []
    for run_id in run_ids:
        for rel in include_globs:
            files.append(f"{run_id}/{rel}")
    return files


def _run_mirror(
    *,
    secret_name: str,
    remote_path: str,
    subdir: str,
    include_globs: list[str],
    include_prefixes: list[str] | None,
    include_runs: list[str] | None,
    dry_run: bool,
) -> dict:
    import os
    import time

    ssh_key_path = _write_ssh_key_to_disk()
    ssh_host = os.environ["SSH_HOST"]
    print(f"[mirror] secret={secret_name} host={ssh_host} remote={remote_path}")
    print(f"[mirror] dest=/runs/{subdir}")

    files_list = _build_files_from_list(
        ssh_host=ssh_host,
        ssh_key=ssh_key_path,
        remote_path=remote_path,
        include_globs=include_globs,
        include_prefixes=include_prefixes,
        include_runs=include_runs,
    )
    print(f"[mirror] {len(files_list)} files queued for rsync "
          f"(prefixes={include_prefixes}, runs={len(include_runs or [])})")

    files_list_path = Path("/tmp/files_from.txt")
    files_list_path.write_text("\n".join(files_list) + "\n")

    dest = Path("/runs") / subdir
    dest.mkdir(parents=True, exist_ok=True)

    rsync_cmd = [
        "rsync",
        "-az",
        "--info=progress2",
        "--no-inc-recursive",
        "--ignore-missing-args",
        "-e",
        f"ssh -i {ssh_key_path} -o BatchMode=yes -o StrictHostKeyChecking=accept-new",
        f"--files-from={files_list_path}",
    ]
    if dry_run:
        rsync_cmd.append("--dry-run")
    rsync_cmd += [
        f"{ssh_host}:{remote_path.rstrip('/')}/",
        f"{dest}/",
    ]
    print(f"[mirror] {' '.join(shlex.quote(s) for s in rsync_cmd[:8])} ...")
    started = time.time()
    proc = subprocess.run(rsync_cmd, check=False)
    elapsed = time.time() - started

    if proc.returncode != 0:
        return {
            "ok": False,
            "exit_code": proc.returncode,
            "elapsed_seconds": elapsed,
            "files_queued": len(files_list),
        }

    if not dry_run:
        mirror_vol.commit()
    return {
        "ok": True,
        "elapsed_seconds": elapsed,
        "files_queued": len(files_list),
        "dest": str(dest),
    }


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("ssh-vps1")],
    volumes={"/runs": mirror_vol},
    timeout=3600,
    cpu=2,
    memory=4096,
)
def mirror_vps1(
    include_prefixes: list[str] | None = None,
    include_runs: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    cfg = BATCH_DEFAULTS["vps1"]
    return _run_mirror(
        secret_name=cfg["secret_name"],
        remote_path=cfg["remote_path"],
        subdir=cfg["subdir"],
        include_globs=cfg["include_globs"],
        include_prefixes=include_prefixes,
        include_runs=include_runs,
        dry_run=dry_run,
    )


@app.function(
    image=image,
    secrets=[modal.Secret.from_name("ssh-vps2")],
    volumes={"/runs": mirror_vol},
    timeout=7200,
    cpu=2,
    memory=4096,
)
def mirror_vps2(
    include_prefixes: list[str] | None = None,
    include_runs: list[str] | None = None,
    dry_run: bool = False,
) -> dict:
    cfg = BATCH_DEFAULTS["vps2"]
    return _run_mirror(
        secret_name=cfg["secret_name"],
        remote_path=cfg["remote_path"],
        subdir=cfg["subdir"],
        include_globs=cfg["include_globs"],
        include_prefixes=include_prefixes,
        include_runs=include_runs,
        dry_run=dry_run,
    )


@app.local_entrypoint()
def main(
    batch: str,
    include_prefixes: str = "",
    include_runs: str = "",
    dry_run: bool = False,
) -> None:
    """Top-level CLI.

    --batch              vps1 | vps2
    --include-prefixes   comma-separated; defaults: vps2 -> "gemini_,deepseek_",
                         vps1 -> "" (all runs)
    --include-runs       comma-separated explicit run_ids (overrides prefixes)
    --dry-run            rsync --dry-run
    """
    prefixes = [p for p in include_prefixes.split(",") if p] or None
    runs = [r for r in include_runs.split(",") if r] or None
    if batch == "vps2" and prefixes is None and runs is None:
        prefixes = ["gemini_", "deepseek_"]

    if batch == "vps1":
        fn = mirror_vps1
    elif batch == "vps2":
        fn = mirror_vps2
    else:
        raise SystemExit(f"unknown --batch {batch!r}; expected vps1 or vps2")

    result = fn.remote(
        include_prefixes=prefixes,
        include_runs=runs,
        dry_run=dry_run,
    )
    print(f"\n[entrypoint] result: {result}")
