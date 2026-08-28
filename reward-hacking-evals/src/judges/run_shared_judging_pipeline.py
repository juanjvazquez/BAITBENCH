#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from backends import resolve_model


ROOT = Path(__file__).resolve().parent

KNOWN_BACKEND_ALIASES = {
    "openai",
    "anthropic",
    "openrouter",
    "openrouter_flash",
}


def safe_model_dir_name(model: str) -> str:
    return model.replace("/", "__")


def resolve_output_dir(requested: Path, *, backend: str, model: str) -> Path:
    resolved_model = resolve_model(backend, model)
    safe_model = safe_model_dir_name(resolved_model)

    if requested.name == safe_model:
        return requested

    if requested.name in KNOWN_BACKEND_ALIASES:
        return requested.with_name(safe_model)

    for alias in sorted(KNOWN_BACKEND_ALIASES, key=len, reverse=True):
        for sep in ("_", "-"):
            suffix = f"{sep}{alias}"
            if requested.name.endswith(suffix):
                return requested.with_name(requested.name[: -len(suffix)] + f"{sep}{safe_model}")

    return requested


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the shared reward-hacking judging pipeline: binary on all cases, "
            "then family-level classification on positives, then full classification on positives."
        )
    )
    parser.add_argument(
        "input_path",
        help="Path to a canonical case JSON, case directory, case JSONL, XML, XML directory, or legacy JSONL evidence file.",
    )
    parser.add_argument("--output-dir", required=True, help="Directory for pipeline outputs.")
    parser.add_argument("--prefix", default="judging", help="Filename prefix inside the output directory.")
    parser.add_argument("--source-project", default="auto", help="Source project: auto, autoresearch, or make_datasets.")
    parser.add_argument("--robust-results", help="Optional robust-results JSONL for make_datasets evidence.")
    parser.add_argument("--run-id", help="Only judge one run ID.")
    parser.add_argument("--limit", type=int, help="Only judge the first N records.")
    parser.add_argument(
        "--backend",
        default="openai",
        choices=["openai", "anthropic", "openrouter"],
        help="Judge backend (openai, anthropic, openrouter).",
    )
    parser.add_argument("--model", default="auto", help="Model name; 'auto' picks the backend default.")
    parser.add_argument("--concurrency", type=int, default=8, help="Number of concurrent judge calls.")
    parser.add_argument("--append", action="store_true", help="Append mode; resume both stages.")
    parser.add_argument(
        "--include-h-classification",
        action="store_true",
        help=(
            "Opt in to the soft-deprecated fine-grained H-code classification stage "
            "(judge_classification_h_deprecated.py). By default this stage is skipped — the "
            "family-level judge is the canonical mechanism layer."
        ),
    )
    # Hidden alias for back-compat. The v1 flag was --skip-h-classification (default off, so the
    # classification stage ran by default). The default is now flipped, so --skip-h-classification
    # is a no-op and is silently accepted to keep older invocations working.
    parser.add_argument(
        "--skip-h-classification",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    return parser


def run_step(name: str, cmd: list[str]) -> None:
    print(f"START {name}")
    subprocess.run(cmd, check=True)
    print(f"DONE  {name}")


def extend_stage_args(
    args: argparse.Namespace,
    output_path: Path,
    *,
    include_binary_results: Path | None = None,
) -> list[str]:
    stage_args = [
        "--source-project",
        args.source_project,
        "--output",
        str(output_path),
        "--backend",
        args.backend,
        "--model",
        args.model,
        "--concurrency",
        str(args.concurrency),
    ]
    if include_binary_results is not None:
        stage_args.extend(["--binary-results", str(include_binary_results)])
    if args.robust_results:
        stage_args.extend(["--robust-results", args.robust_results])
    if args.run_id:
        stage_args.extend(["--run-id", args.run_id])
    if args.limit is not None:
        stage_args.extend(["--limit", str(args.limit)])
    if args.append:
        stage_args.append("--append")
    return stage_args


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    output_dir = resolve_output_dir(
        Path(args.output_dir),
        backend=args.backend,
        model=args.model,
    )
    output_dir.mkdir(parents=True, exist_ok=True)

    binary_output = output_dir / f"{args.prefix}_binary.jsonl"
    family_output = output_dir / f"{args.prefix}_family.jsonl"
    classification_output = output_dir / f"{args.prefix}_classification.jsonl"

    binary_cmd = [
        sys.executable,
        str(ROOT / "judge_binary.py"),
        args.input_path,
        *extend_stage_args(args, binary_output),
    ]
    run_step("binary", binary_cmd)

    family_cmd = [
        sys.executable,
        str(ROOT / "judge_classification_family.py"),
        args.input_path,
        *extend_stage_args(args, family_output, include_binary_results=binary_output),
    ]
    run_step("family", family_cmd)

    if args.include_h_classification:
        classification_cmd = [
            sys.executable,
            str(ROOT / "judge_classification_h_deprecated.py"),
            args.input_path,
            *extend_stage_args(args, classification_output, include_binary_results=binary_output),
        ]
        run_step("classification", classification_cmd)
    else:
        print("SKIP  classification (H-codes) — soft-deprecated; pass --include-h-classification to enable")

    print()
    print("Saved outputs:")
    print(f"  output_dir: {output_dir}")
    print(f"  binary: {binary_output}")
    print(f"  family: {family_output}")
    if args.include_h_classification:
        print(f"  classification: {classification_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
