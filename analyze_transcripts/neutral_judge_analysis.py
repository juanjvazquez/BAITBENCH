#!/usr/bin/env python3
"""Recompute the neutral GLM judge audit from local archived outputs.

This script makes no API calls. It checks exact canonical coverage, retains the
last valid categorical judgment per run ID, and compares GLM-5.2 with the two
original binary judges on the same 1,258 cases.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from pathlib import Path
from typing import Iterable

from judge_family_cluster_bootstrap import load_paper_records


BINARY_LABELS = {"reward_hacking", "not_reward_hacking"}
VALID_LABELS = BINARY_LABELS | {"unclear"}
DEFAULT_NEUTRAL_RELATIVE = Path(
    "data/outputs/judging/neutral_z-ai_glm-5.2_v4_20260709/judging_binary.jsonl"
)


def read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{path}:{line_number}: malformed JSON") from exc
            if not isinstance(value, dict):
                raise ValueError(f"{path}:{line_number}: expected a JSON object")
            rows.append(value)
    return rows


def valid_neutral_labels(rows: Iterable[dict]) -> tuple[dict[str, str], dict[str, int]]:
    labels: dict[str, str] = {}
    valid_record_rows = 0
    invalid_record_rows = 0
    duplicate_valid_rows = 0
    skipped_rows = 0
    error_rows = 0
    header_rows = 0

    for row in rows:
        row_type = row.get("type")
        if row_type == "header":
            header_rows += 1
            continue
        if row_type != "record":
            if row_type == "error":
                error_rows += 1
            continue
        if row.get("skipped"):
            skipped_rows += 1
            continue
        run_id = row.get("run_id")
        judgment = row.get("judgment")
        label = judgment.get("label") if isinstance(judgment, dict) else None
        if not isinstance(run_id, str) or label not in VALID_LABELS:
            invalid_record_rows += 1
            continue
        if run_id in labels:
            duplicate_valid_rows += 1
        labels[run_id] = label
        valid_record_rows += 1

    diagnostics = {
        "header_rows": header_rows,
        "valid_record_rows": valid_record_rows,
        "unique_valid_records": len(labels),
        "duplicate_valid_rows": duplicate_valid_rows,
        "invalid_record_rows": invalid_record_rows,
        "skipped_rows": skipped_rows,
        "error_rows": error_rows,
    }
    return labels, diagnostics


def cohen_kappa(left: list[str], right: list[str]) -> float:
    if len(left) != len(right) or not left:
        raise ValueError("Cohen's kappa requires equal non-empty label vectors")
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / len(left)
    left_counts = Counter(left)
    right_counts = Counter(right)
    # The original judges may return ``unclear``. Treat it as a third category
    # in agreement calculations, matching the audited neutral-judge report.
    categories = set(left_counts) | set(right_counts)
    expected = sum(
        (left_counts[label] / len(left)) * (right_counts[label] / len(right))
        for label in categories
    )
    if math.isclose(expected, 1.0):
        return 1.0 if math.isclose(observed, 1.0) else float("nan")
    return (observed - expected) / (1.0 - expected)


def pair_summary(
    run_ids: list[str],
    left: dict[str, str],
    right: dict[str, str],
) -> dict[str, float | int]:
    left_values = [left[run_id] for run_id in run_ids]
    right_values = [right[run_id] for run_id in run_ids]
    agreements = sum(a == b for a, b in zip(left_values, right_values, strict=True))
    return {
        "n": len(run_ids),
        "agreement_count": agreements,
        "agreement": agreements / len(run_ids),
        "cohen_kappa": cohen_kappa(left_values, right_values),
    }


def render_markdown(result: dict) -> str:
    counts = result["neutral"]["label_counts"]
    comparisons = result["comparisons"]
    lines = [
        "# Neutral-judge local audit",
        "",
        (
            f"GLM-5.2 produced valid categorical judgments for "
            f"**{result['coverage']['matched_canonical_runs']:,}/"
            f"{result['coverage']['canonical_runs']:,}** canonical cases. "
            f"It labeled **{counts['reward_hacking']:,}/"
            f"{result['coverage']['canonical_runs']:,} "
            f"({100 * result['neutral']['reward_hacking_rate']:.2f}%)** "
            "as reward hacking."
        ),
        "",
        "| Judge pair | Cases | Agreement | Cohen's κ |",
        "|---|---:|---:|---:|",
    ]
    for key, label in (
        ("glm_vs_gpt", "GLM-5.2 vs GPT-5.4"),
        ("glm_vs_claude", "GLM-5.2 vs Claude Opus 4.6"),
        ("claude_vs_gpt", "Claude Opus 4.6 vs GPT-5.4"),
    ):
        row = comparisons[key]
        lines.append(
            f"| {label} | {row['n']:,} | {100 * row['agreement']:.2f}% | "
            f"{row['cohen_kappa']:.4f} |"
        )
    lines.extend(
        [
            "",
            "## Input audit",
            "",
            f"- Neutral output: `{result['inputs']['neutral_judgments']}`",
            f"- Canonical cases: {result['coverage']['canonical_runs']:,}",
            f"- Missing canonical judgments: {result['coverage']['missing_run_ids']}",
            f"- Extra judgment run IDs: {result['coverage']['extra_run_ids']}",
            (
                "- Raw-row diagnostics: "
                + ", ".join(
                    f"{key}={value}"
                    for key, value in result["neutral"]["row_diagnostics"].items()
                )
            ),
            "",
            "This is a robustness check using another LLM judge, not ground truth.",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "reward-hacking-evals",
    )
    parser.add_argument("--neutral-jsonl", type=Path, default=None)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo.resolve()
    neutral_path = (
        args.neutral_jsonl.resolve()
        if args.neutral_jsonl is not None
        else repo / DEFAULT_NEUTRAL_RELATIVE
    )
    paper_records = load_paper_records(repo)
    canonical_ids = {record.run_id for record in paper_records}
    if len(canonical_ids) != 1_258:
        raise ValueError(f"expected 1,258 unique canonical runs, found {len(canonical_ids)}")

    claude = {record.run_id: record.claude_label for record in paper_records}
    gpt = {record.run_id: record.gpt_label for record in paper_records}
    if not all(label in BINARY_LABELS | {"unclear"} for label in claude.values()):
        raise ValueError("unexpected Claude label")
    if not all(label in BINARY_LABELS | {"unclear"} for label in gpt.values()):
        raise ValueError("unexpected GPT label")

    neutral_rows = read_jsonl(neutral_path)
    neutral, diagnostics = valid_neutral_labels(neutral_rows)
    missing = sorted(canonical_ids - neutral.keys())
    extra = sorted(neutral.keys() - canonical_ids)
    if missing or extra:
        raise ValueError(
            f"neutral coverage mismatch: {len(missing)} missing, {len(extra)} extra"
        )

    ordered_ids = sorted(canonical_ids)
    counts = Counter(neutral.values())
    result = {
        "method": {
            "canonicalization": "paper canonical run-collapse logic",
            "neutral_deduplication": "last valid categorical judgment per run_id",
            "agreement": "raw agreement and Cohen's kappa on exact shared cases",
        },
        "inputs": {
            "repo": str(repo),
            "neutral_judgments": str(neutral_path),
        },
        "coverage": {
            "canonical_runs": len(canonical_ids),
            "matched_canonical_runs": len(ordered_ids),
            "missing_run_ids": len(missing),
            "extra_run_ids": len(extra),
        },
        "neutral": {
            "label_counts": {
                "reward_hacking": counts["reward_hacking"],
                "not_reward_hacking": counts["not_reward_hacking"],
                "unclear": counts["unclear"],
            },
            "reward_hacking_rate": counts["reward_hacking"] / len(ordered_ids),
            "row_diagnostics": diagnostics,
        },
        "comparisons": {
            "glm_vs_gpt": pair_summary(ordered_ids, neutral, gpt),
            "glm_vs_claude": pair_summary(ordered_ids, neutral, claude),
            "claude_vs_gpt": pair_summary(ordered_ids, claude, gpt),
        },
    }

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    args.output_markdown.write_text(render_markdown(result), encoding="utf-8")
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_markdown}")


if __name__ == "__main__":
    main()
