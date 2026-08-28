#!/usr/bin/env python3
"""Verify the consolidated BAITBENCH repository without network access."""

from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
CANONICAL = (
    ROOT
    / "reward-hacking-evals/data/cases/make_datasets/"
    "v4_paper_canonical_1258_20260709.jsonl"
)
CANONICAL_SHA256 = "6e382e83faba05f9c31cf773f2a1e5f990cf0c99aec0d7bead3ececf796ca1a3"

REQUIRED = (
    ROOT / "make_datasets/pyproject.toml",
    ROOT / "make_datasets/src/make_datasets/synthetic_suite.py",
    ROOT / "reward-hacking-evals/pyproject.toml",
    ROOT / "reward-hacking-evals/scripts/make_datasets_paper_stats.py",
    ROOT / "analyze_transcripts/run_camera_ready_analyses.py",
    ROOT / "reproduce.py",
    ROOT / "REPRODUCIBILITY.md",
    ROOT / "CITATION.cff",
    ROOT / "LICENSE",
    CANONICAL,
)

FORBIDDEN_DIRECTORY_NAMES = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "runs",
    "vps_transcripts",
}

SECRET_PATTERNS = {
    "private key": re.compile(rb"BEGIN (?:OPENSSH|RSA|EC) PRIVATE KEY"),
    "OpenRouter API key": re.compile(
        rb"(?<![A-Za-z0-9])sk-or-v1-[A-Za-z0-9]{20,}"
    ),
    "Anthropic API key": re.compile(
        rb"(?<![A-Za-z0-9])sk-ant-[A-Za-z0-9_-]{20,}"
    ),
    "OpenAI API key": re.compile(
        rb"(?<![A-Za-z0-9])sk-(?:(?:proj|svcacct)-)?[A-Za-z0-9]{20,}"
    ),
    "Slack token": re.compile(rb"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{20,}"),
    "GitHub token": re.compile(rb"(?<![A-Za-z0-9])gh[opusr]_[A-Za-z0-9]{30,}"),
    "Modal token secret": re.compile(rb"--token-secret\s+[^<\s][^\s]+"),
}

TEXT_SUFFIXES = {
    ".cfg",
    ".csv",
    ".ini",
    ".json",
    ".jsonl",
    ".lock",
    ".md",
    ".py",
    ".sh",
    ".toml",
    ".tsv",
    ".txt",
    ".yaml",
    ".yml",
}


def fail(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise SystemExit(1)


def verify_structure() -> None:
    missing = [str(path.relative_to(ROOT)) for path in REQUIRED if not path.is_file()]
    if missing:
        fail(f"missing required files: {', '.join(missing)}")

    forbidden = [
        path.relative_to(ROOT)
        for path in ROOT.rglob("*")
        if path.is_dir()
        and path.name in FORBIDDEN_DIRECTORY_NAMES
        and path != ROOT / ".git"
    ]
    if forbidden:
        fail(f"excluded directories are present: {', '.join(map(str, forbidden))}")

    env_files = [
        path.relative_to(ROOT)
        for path in ROOT.rglob(".env*")
        if path.name != ".env.example"
    ]
    if env_files:
        fail(f"environment files are present: {', '.join(map(str, env_files))}")


def verify_canonical_cases() -> None:
    digest = hashlib.sha256(CANONICAL.read_bytes()).hexdigest()
    if digest != CANONICAL_SHA256:
        fail(f"canonical case checksum changed: {digest}")

    run_ids: set[str] = set()
    records = 0
    with CANONICAL.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            record = json.loads(line)
            run_id = record.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                fail(f"canonical line {line_number} has no run_id")
            records += 1
            run_ids.add(run_id)
    if records != 1_258 or len(run_ids) != 1_258:
        fail(f"expected 1,258 unique canonical cases, found {records}/{len(run_ids)}")


def verify_no_secret_patterns() -> None:
    for path in ROOT.rglob("*"):
        if (
            not path.is_file()
            or ".git" in path.parts
            or path.suffix.lower() not in TEXT_SUFFIXES
        ):
            continue
        data = path.read_bytes()
        for label, pattern in SECRET_PATTERNS.items():
            if pattern.search(data):
                fail(f"possible {label} in {path.relative_to(ROOT)}")


def main() -> None:
    verify_structure()
    verify_canonical_cases()
    verify_no_secret_patterns()
    print("BAITBENCH repository verification passed")
    print("canonical cases: 1,258 unique records")
    print(f"canonical SHA-256: {CANONICAL_SHA256}")


if __name__ == "__main__":
    main()
