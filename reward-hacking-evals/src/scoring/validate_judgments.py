#!/usr/bin/env python3
"""Validate classification-judge JSONL output against the canonical taxonomy.

Checks performed per record:

1. (parent_category, mechanism_family, h_label) triple matches H_LABEL_TAXONOMY
   for every entry in `mechanisms`.
2. `route` is consistent: must be 'a' or 'b' iff h_label in {H-inflate, H-whack};
   'none' otherwise.
3. Mutual exclusion: a single record's `mechanisms` list must not contain both
   H-api and H-gate.
4. `primary_mechanism_family` matches `mechanisms[0].mechanism_family`.

Outputs:
- A JSONL of issues (one record per problematic run).
- A short summary printed to stdout.

Usage
-----
    python validate_judgments.py judgments.jsonl --output issues.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Make the judges/ taxonomy importable.
HERE = Path(__file__).resolve().parent
JUDGES_DIR = HERE.parent / "judges"
if str(JUDGES_DIR) not in sys.path:
    sys.path.insert(0, str(JUDGES_DIR))
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from common import iter_jsonl_records  # type: ignore  # noqa: E402
from taxonomy import H_LABEL_TAXONOMY  # type: ignore  # noqa: E402


ROUTE_REQUIRED_LABELS = {"H-inflate", "H-whack"}


def validate_mechanism(item: dict[str, Any], idx: int) -> list[str]:
    issues: list[str] = []
    if not isinstance(item, dict):
        return [f"mechanisms[{idx}]: not an object"]
    pc = item.get("parent_category")
    mf = item.get("mechanism_family")
    hl = item.get("h_label")
    route = item.get("route")

    if hl not in H_LABEL_TAXONOMY:
        issues.append(f"mechanisms[{idx}]: unknown h_label={hl!r}")
        return issues

    canonical = H_LABEL_TAXONOMY[hl]
    if pc != canonical["parent_category"]:
        issues.append(
            f"mechanisms[{idx}]: h_label={hl} requires parent_category="
            f"{canonical['parent_category']!r} but got {pc!r}"
        )
    if mf != canonical["mechanism_family"]:
        issues.append(
            f"mechanisms[{idx}]: h_label={hl} requires mechanism_family="
            f"{canonical['mechanism_family']!r} but got {mf!r}"
        )

    if hl in ROUTE_REQUIRED_LABELS:
        if route not in ("a", "b"):
            issues.append(
                f"mechanisms[{idx}]: h_label={hl} requires route in {{'a','b'}} but got {route!r}"
            )
    else:
        if route != "none":
            issues.append(
                f"mechanisms[{idx}]: h_label={hl} requires route='none' but got {route!r}"
            )
    return issues


def validate_record(record: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    judgment = record.get("judgment")
    if not isinstance(judgment, dict):
        return ["judgment is missing or not an object"]
    mechanisms = judgment.get("mechanisms")
    if not isinstance(mechanisms, list) or not mechanisms:
        return ["mechanisms is missing or empty"]

    h_labels = []
    for i, item in enumerate(mechanisms):
        issues.extend(validate_mechanism(item, i))
        if isinstance(item, dict):
            hl = item.get("h_label")
            if isinstance(hl, str):
                h_labels.append(hl)

    # Mutual exclusion: H-api and H-gate
    if "H-api" in h_labels and "H-gate" in h_labels:
        issues.append("mutual_exclusion: mechanisms contain both H-api and H-gate; H-api is a sub-type of H-gate")

    # primary_mechanism_family vs mechanisms[0]
    primary = judgment.get("primary_mechanism_family")
    first = mechanisms[0]
    if isinstance(first, dict):
        first_mf = first.get("mechanism_family")
        if primary != first_mf:
            issues.append(
                f"primary_mechanism_family={primary!r} does not match mechanisms[0].mechanism_family={first_mf!r}"
            )
    return issues
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Validate classification judgments against the canonical taxonomy.")
    p.add_argument("input", help="Classification judge JSONL file.")
    p.add_argument("--output", help="Optional issues JSONL output (one record per problematic run).")
    p.add_argument("--strict", action="store_true", help="Exit non-zero if any issues are found.")
    return p


def main() -> int:
    args = build_parser().parse_args()
    in_path = Path(args.input)
    if not in_path.exists():
        print(f"Missing input: {in_path}", file=sys.stderr)
        return 2

    total = 0
    bad = 0
    issue_records: list[dict[str, Any]] = []
    issue_counts: dict[str, int] = {}

    for record in iter_jsonl_records(in_path):
        total += 1
        issues = validate_record(record)
        if not issues:
            continue
        bad += 1
        for issue in issues:
            key = issue.split(":", 1)[0]
            issue_counts[key] = issue_counts.get(key, 0) + 1
        issue_records.append({
            "run_id": record.get("run_id"),
            "source_project": record.get("source_project"),
            "backend": record.get("backend"),
            "model": record.get("model"),
            "issues": issues,
        })

    if args.output:
        with Path(args.output).open("w", encoding="utf-8") as handle:
            for rec in issue_records:
                handle.write(json.dumps(rec) + "\n")

    summary = {
        "input": str(in_path),
        "n_records": total,
        "n_with_issues": bad,
        "pct_with_issues": round(bad / total, 4) if total else 0.0,
        "issue_counts_by_type": issue_counts,
    }
    print(json.dumps(summary, indent=2, sort_keys=True))

    if args.strict and bad:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
