#!/usr/bin/env python3
"""Build the exact 1,258-case paper corpus for neutral judging.

The archived v4 cases live in five directories and include superseded reruns,
smoke cases, and other non-canonical records.  This script deliberately reuses
the paper statistics module's canonicalization instead of introducing a second
definition of the evaluation set.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from collections import Counter
from pathlib import Path
from types import ModuleType


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = (
    REPO_ROOT
    / "data/cases/make_datasets/v4_paper_canonical_1258_20260709.jsonl"
)
DEFAULT_MANIFEST = DEFAULT_OUTPUT.with_suffix(".manifest.json")


def load_paper_stats_module() -> ModuleType:
    path = REPO_ROOT / "scripts/make_datasets_paper_stats.py"
    spec = importlib.util.spec_from_file_location("make_datasets_paper_stats", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load paper statistics module: {path}")
    module = importlib.util.module_from_spec(spec)
    # Dataclasses resolve forward annotations through sys.modules while the
    # module body executes (required by Python 3.14's stricter implementation).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outputs-root",
        type=Path,
        default=REPO_ROOT / "data/outputs/judging",
        help="Root containing the archived Claude and GPT judge batches.",
    )
    parser.add_argument(
        "--cases-root",
        type=Path,
        default=REPO_ROOT / "data/cases/make_datasets",
        help="Root containing the five archived v4 case collections.",
    )
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--expected-count", type=int, default=1258)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paper = load_paper_stats_module()
    records = paper.load_records(args.outputs_root)

    run_ids = [record.run_id for record in records]
    if len(run_ids) != len(set(run_ids)):
        duplicates = sorted(
            run_id for run_id, count in Counter(run_ids).items() if count > 1
        )
        raise RuntimeError(f"canonical loader returned duplicate run IDs: {duplicates[:10]}")
    if len(records) != args.expected_count:
        raise RuntimeError(
            f"expected {args.expected_count} canonical records, found {len(records)}"
        )

    output_rows: list[str] = []
    source_counts: Counter[str] = Counter()
    task_counts: Counter[str] = Counter()
    model_counts: Counter[str] = Counter()
    prompt_counts: Counter[str] = Counter()

    for record in sorted(records, key=lambda item: item.run_id):
        case_path = paper.case_path_for_record(args.cases_root, record)
        if not case_path.exists():
            raise FileNotFoundError(f"missing canonical case for {record.run_id}: {case_path}")
        case = json.loads(case_path.read_text(encoding="utf-8"))
        if case.get("run_id") != record.run_id:
            raise RuntimeError(
                f"case/run mismatch: expected {record.run_id}, got {case.get('run_id')}"
            )
        output_rows.append(json.dumps(case, sort_keys=True, separators=(",", ":")))
        source_counts[case_path.parent.name] += 1
        task_counts[record.task] += 1
        model_counts[record.model] += 1
        prompt_counts[record.prompt] += 1

    output_text = "\n".join(output_rows) + "\n"
    output_sha256 = hashlib.sha256(output_text.encode("utf-8")).hexdigest()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp_output = args.output.with_suffix(args.output.suffix + ".tmp")
    temp_output.write_text(output_text, encoding="utf-8")
    temp_output.replace(args.output)

    manifest = {
        "schema_version": "neutral-judge-input-manifest-v1",
        "description": "Exact canonical BAITBENCH v4 paper corpus for neutral judging.",
        "canonicalization_source": "scripts/make_datasets_paper_stats.py:load_records",
        "record_count": len(records),
        "output": str(args.output.relative_to(REPO_ROOT)),
        "output_sha256": output_sha256,
        "counts_by_source_batch": dict(sorted(source_counts.items())),
        "counts_by_task": dict(sorted(task_counts.items())),
        "counts_by_model": dict(sorted(model_counts.items())),
        "counts_by_prompt": dict(sorted(prompt_counts.items())),
    }
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    print(f"wrote {len(records)} canonical cases to {args.output}")
    print(f"sha256 {output_sha256}")
    print(f"manifest {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
