#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

SRC_ROOT = Path(__file__).resolve().parents[1]
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from cases.builders import build_make_datasets_case
from cases.schema import write_case_json

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT_DIR = REPO_ROOT / "data" / "outputs" / "make_datasets"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "cases" / "make_datasets" / "records"
DEFAULT_OUTPUT_JSONL = REPO_ROOT / "data" / "cases" / "make_datasets" / "cases.jsonl"
DEFAULT_MANIFEST_PATH = REPO_ROOT / "data" / "cases" / "make_datasets" / "build_manifest.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build canonical reward-hacking case JSON files for make_datasets from evidence and robust JSONLs."
    )
    parser.add_argument(
        "--input-dir",
        default=str(DEFAULT_INPUT_DIR),
        help=f"Directory containing *_evidence.jsonl and *_robust_scores.jsonl files (default: {DEFAULT_INPUT_DIR})",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_OUTPUT_DIR),
        help=f"Directory for per-run case JSON files (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--output-jsonl",
        default=str(DEFAULT_OUTPUT_JSONL),
        help=f"Path for the aggregate canonical cases JSONL (default: {DEFAULT_OUTPUT_JSONL})",
    )
    parser.add_argument(
        "--manifest-path",
        default=str(DEFAULT_MANIFEST_PATH),
        help=f"Path for the build manifest (default: {DEFAULT_MANIFEST_PATH})",
    )
    parser.add_argument(
        "--agent",
        choices=["codex", "claude", "kimi", "gemini", "deepseek"],
        help="Only build cases for one agent.",
    )
    parser.add_argument("--run-id", help="Only rebuild a single run ID.")
    parser.add_argument("--limit", type=int, help="Only rebuild the first N matching runs.")
    return parser


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            obj = json.loads(line)
            if obj.get("type") == "record":
                records.append(obj)
    return records


def load_records_by_run_id(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    records: dict[str, dict[str, Any]] = {}
    for record in load_jsonl_records(path):
        run_id = record.get("run_id")
        if isinstance(run_id, str):
            records[run_id] = record
    return records


def discover_input_pairs(input_dir: Path, agent: str | None) -> list[tuple[str, Path, Path | None]]:
    pairs: list[tuple[str, Path, Path | None]] = []
    for evidence_path in sorted(input_dir.glob("*_evidence.jsonl")):
        name = evidence_path.name
        if "_evidence.jsonl" not in name:
            continue
        agent_name = name.split("_evidence.jsonl", 1)[0].rsplit("_", 1)[-1]
        if agent is not None and agent_name != agent:
            continue
        robust_name = name.replace("_evidence.jsonl", "_robust_scores.jsonl")
        robust_path = input_dir / robust_name
        pairs.append((agent_name, evidence_path, robust_path if robust_path.exists() else None))
    return pairs


def rewrite_cases_jsonl(cases_dir: Path, output_jsonl: Path) -> int:
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with output_jsonl.open("w", encoding="utf-8") as handle:
        for path in sorted(cases_dir.glob("*.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            handle.write(json.dumps(record, ensure_ascii=True))
            handle.write("\n")
            count += 1
    return count


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)
    output_jsonl = Path(args.output_jsonl)
    manifest_path = Path(args.manifest_path)

    if not input_dir.is_dir():
        print(f"Error: input directory not found: {input_dir}", file=sys.stderr)
        return 1

    pairs = discover_input_pairs(input_dir, args.agent)
    if not pairs:
        print(f"Error: no matching evidence files found in {input_dir}", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    built: list[dict[str, Any]] = []
    counts_by_agent: Counter[str] = Counter()
    remaining = args.limit

    for agent_name, evidence_path, robust_path in pairs:
        robust_records_by_run_id = load_records_by_run_id(robust_path)
        evidence_records = load_jsonl_records(evidence_path)

        for evidence_record in evidence_records:
            run_id = evidence_record.get("run_id")
            if not isinstance(run_id, str):
                continue
            if args.run_id is not None and run_id != args.run_id:
                continue
            if remaining is not None and remaining <= 0:
                break

            case_record = build_make_datasets_case(
                evidence_record,
                robust_records_by_run_id.get(run_id),
                evidence_ref=str(evidence_path),
                robust_ref=str(robust_path) if robust_path is not None else None,
            )
            case_path = output_dir / f"{run_id}.json"
            write_case_json(case_path, case_record)
            built.append(
                {
                    "run_id": run_id,
                    "agent": agent_name,
                    "case_path": str(case_path),
                    "evidence_ref": str(evidence_path),
                    "robust_ref": str(robust_path) if robust_path is not None else None,
                }
            )
            counts_by_agent[agent_name] += 1
            if remaining is not None:
                remaining -= 1

        if remaining is not None and remaining <= 0:
            break

    case_count = rewrite_cases_jsonl(output_dir, output_jsonl)
    manifest = {
        "input_dir": str(input_dir),
        "output_dir": str(output_dir),
        "output_jsonl": str(output_jsonl),
        "matched_file_count": len(pairs),
        "built_count": len(built),
        "case_count": case_count,
        "counts_by_agent": dict(counts_by_agent),
        "built": built,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    print(f"Matched evidence files: {len(pairs)}")
    print(f"Built cases: {len(built)}")
    print(f"Canonical cases: {case_count}")
    print(f"Manifest: {manifest_path}")
    if built:
        print(f"Example case: {built[0]['case_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
