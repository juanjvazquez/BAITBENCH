#!/usr/bin/env python3
"""Run the complete offline BAITBENCH reproduction from the repository root."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
VERIFY = ROOT / "verify_repository.py"
ANALYSIS = ROOT / "analyze_transcripts/run_camera_ready_analyses.py"
EVAL_PROJECT = ROOT / "reward-hacking-evals"
DEFAULT_OUTPUT = ROOT / "analyze_transcripts/generated/reproduction"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use reduced Monte Carlo counts while checking all data paths and invariants.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Directory for regenerated tables, reports, logs, and the run manifest.",
    )
    return parser.parse_args()


def run(command: list[str]) -> None:
    subprocess.run(command, cwd=ROOT, check=True)


def main() -> None:
    args = parse_args()
    uv = shutil.which("uv")
    if uv is None:
        raise SystemExit("uv is required; install it from https://docs.astral.sh/uv/")

    run([sys.executable, str(VERIFY)])
    command = [
        uv,
        "run",
        "--project",
        str(EVAL_PROJECT),
        "python",
        str(ANALYSIS),
        "--output-dir",
        str(args.output_dir.resolve()),
    ]
    if args.quick:
        command.append("--quick")
    run(command)


if __name__ == "__main__":
    main()

