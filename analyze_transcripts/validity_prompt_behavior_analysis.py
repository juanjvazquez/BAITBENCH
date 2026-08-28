#!/usr/bin/env python3
"""Analyze how the validity-aware prompt succeeds and fails in paired runs."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from pathlib import Path

from evaluator_calls_and_judge_reasons import load_records
from observable_behavior_analysis import (
    MODEL_NAMES,
    MODEL_ORDER,
    BehaviorRecord,
    enrich_records,
    load_awareness,
    pct,
)


def pair_key(row: BehaviorRecord) -> tuple[str, str, str, int]:
    return (row.base.model, row.base.task, row.base.size, row.base.seed)


def transition(left: BehaviorRecord, right: BehaviorRecord) -> str:
    if left.is_rh and right.is_rh:
        return "both_rh"
    if not left.is_rh and not right.is_rh:
        return "both_non_rh"
    if left.is_rh and not right.is_rh:
        return "prevented"
    return "reverse"


def paired_rows(rows: list[BehaviorRecord]) -> list[tuple[BehaviorRecord, BehaviorRecord]]:
    grouped: dict[tuple[str, str, str, int], dict[str, BehaviorRecord]] = defaultdict(dict)
    for row in rows:
        if row.is_consensus:
            grouped[pair_key(row)][row.prompt] = row
    return [
        (conditions["baseline"], conditions["validity"])
        for conditions in grouped.values()
        if {"baseline", "validity"} <= conditions.keys()
    ]


def awareness_label(
    awareness: dict[str, dict[str, object]], row: BehaviorRecord
) -> str:
    return str(awareness.get(row.base.run_id, {}).get("awareness_label", "missing"))


def render(
    pairs: list[tuple[BehaviorRecord, BehaviorRecord]],
    awareness: dict[str, dict[str, object]],
) -> str:
    counts = Counter(transition(left, right) for left, right in pairs)
    lines = [
        "# Validity-aware prompt: paired behavioral success and failure analysis",
        "",
        "## Scope",
        "",
        "- Pairs match model, task family, dataset size, and seed; only pairs with consensus RH/non-RH labels in both prompt conditions are included.",
        "- `Prevented` means baseline RH and validity non-RH. `Reverse` means baseline non-RH and validity RH. These are paired descriptive transitions, not deterministic prompt effects for individual runs.",
        "- Awareness labels were generated only for consensus-RH runs and are used to characterize persistent failures, not to infer awareness among successful non-RH runs.",
        "",
        "## Paired outcomes",
        "",
        "| Outcome across prompt conditions | Pairs | Percentage of paired cohort |",
        "|---|---:|---:|",
        f"| RH under both prompts | {counts['both_rh']} | {pct(counts['both_rh'], len(pairs))} |",
        f"| Non-RH under both prompts | {counts['both_non_rh']} | {pct(counts['both_non_rh'], len(pairs))} |",
        f"| Baseline RH, validity non-RH | {counts['prevented']} | {pct(counts['prevented'], len(pairs))} |",
        f"| Baseline non-RH, validity RH | {counts['reverse']} | {pct(counts['reverse'], len(pairs))} |",
        "",
        "## By model",
        "",
        "| Model | Paired runs | Both RH | Both non-RH | Prevented | Reverse | Net prevented |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for model in MODEL_ORDER:
        model_pairs = [pair for pair in pairs if pair[0].base.model == model]
        model_counts = Counter(transition(left, right) for left, right in model_pairs)
        lines.append(
            f"| {MODEL_NAMES[model]} | {len(model_pairs)} | {model_counts['both_rh']} | "
            f"{model_counts['both_non_rh']} | {model_counts['prevented']} | "
            f"{model_counts['reverse']} | {model_counts['prevented'] - model_counts['reverse']:+d} |"
        )

    persistent = [pair for pair in pairs if transition(*pair) == "both_rh"]
    both_awareness = [
        pair
        for pair in persistent
        if awareness_label(awareness, pair[0]) != "missing"
        and awareness_label(awareness, pair[1]) != "missing"
    ]
    baseline_labels = Counter(awareness_label(awareness, left) for left, _ in both_awareness)
    validity_labels = Counter(awareness_label(awareness, right) for _, right in both_awareness)
    labels = (
        "mechanism_awareness_framed_as_success",
        "explicit_invalidity_awareness",
        "generalization_awareness_only",
        "no_obvious_awareness",
    )
    display = {
        "mechanism_awareness_framed_as_success": "Recognizes mechanism but frames it as success",
        "explicit_invalidity_awareness": "Explicitly calls the method invalid or leaky",
        "generalization_awareness_only": "Generalization concern only",
        "no_obvious_awareness": "No obvious awareness",
    }
    lines.extend(
        [
            "",
            "## Awareness among persistent RH pairs",
            "",
            f"Among the {len(persistent)} pairs labeled RH under both prompts, {len(both_awareness)} have awareness judgments in both conditions.",
            "",
            "| Transcript characterization | Baseline | Validity-aware | Change |",
            "|---|---:|---:|---:|",
        ]
    )
    for label in labels:
        left = baseline_labels[label]
        right = validity_labels[label]
        lines.append(
            f"| {display[label]} | {pct(left, len(both_awareness))} | "
            f"{pct(right, len(both_awareness))} | {100 * (right - left) / len(both_awareness):+.1f} pp |"
        )

    lines.extend(
        [
            "",
            "## Interpretation",
            "",
            "The prompt sometimes prevents RH, but a substantial persistent-RH group remains. Within persistent failures, the prompt shifts the agent's language away from treating the shortcut as an achievement and toward explicitly recognizing invalidity. The prompt can therefore change recognition without reliably changing the final retention decision.",
            "",
            "## Reproduction",
            "",
            "```bash",
            "cd /Users/pradyu/Projects/spar_project",
            "uv run python analyze_transcripts/validity_prompt_behavior_analysis.py",
            "```",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("validity_prompt_behavior_analysis_20260712.md"),
    )
    args = parser.parse_args()
    eval_root = args.repo_root / "reward-hacking-evals"
    judging_root = eval_root / "data" / "outputs" / "judging"
    cases_root = eval_root / "data" / "cases" / "make_datasets"
    awareness_path = (
        judging_root
        / "transcript_awareness_gemini_flash"
        / "transcript_awareness_openrouter.jsonl"
    )
    records = load_records(judging_root, cases_root)
    if len(records) != 1_258:
        raise RuntimeError(f"expected 1,258 canonical records, found {len(records)}")
    rows = enrich_records(records, cases_root)
    pairs = paired_rows(rows)
    report = render(pairs, load_awareness(awareness_path))
    args.output.write_text(report, encoding="utf-8")
    print(f"wrote {args.output} ({len(pairs)} paired comparisons)")


if __name__ == "__main__":
    main()
