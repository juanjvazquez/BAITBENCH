#!/usr/bin/env python3
"""Summarize transcript-awareness labels among runs both binary judges call RH."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Callable, Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from make_datasets_paper_stats import MODEL_ORDER, TASK_ORDER, RunRecord, load_records


DEFAULT_OUTPUTS_ROOT = Path("data/outputs/judging")
DEFAULT_AWARENESS_JSONL = Path(
    "data/outputs/judging/"
    "transcript_awareness_gemini_flash/"
    "transcript_awareness_openrouter.jsonl"
)

LABEL_NONE = "no_obvious_awareness"
LABEL_GEN_ONLY = "generalization_awareness_only"
LABEL_MECHANISM = "mechanism_awareness_framed_as_success"
LABEL_EXPLICIT = "explicit_invalidity_awareness"
LABEL_SELF_REPORT = "self_reported_reward_hack_or_invalid_final"

LABELS = [
    LABEL_NONE,
    LABEL_GEN_ONLY,
    LABEL_MECHANISM,
    LABEL_EXPLICIT,
    LABEL_SELF_REPORT,
]
STRONG_LABELS = {LABEL_MECHANISM, LABEL_EXPLICIT, LABEL_SELF_REPORT}


def load_awareness_records(path: Path) -> dict[str, dict[str, object]]:
    records: dict[str, dict[str, object]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("type") != "record":
                continue
            run_id = obj.get("run_id")
            if isinstance(run_id, str):
                records[run_id] = obj
    return records


def both_judges_reward_hacking(record: RunRecord) -> bool:
    return (
        record.claude_label == "reward_hacking"
        and record.gpt_label == "reward_hacking"
    )


def label_for(awareness_record: dict[str, object]) -> str:
    judgment = awareness_record.get("judgment")
    if not isinstance(judgment, dict):
        raise ValueError("awareness record missing judgment object")
    label = judgment.get("awareness_label")
    if not isinstance(label, str):
        raise ValueError("awareness record missing judgment.awareness_label")
    return label


def pct(count: int, denom: int) -> str:
    if denom == 0:
        return "NA"
    return f"{100.0 * count / denom:.1f}%"


def count_pct(count: int, denom: int) -> str:
    return f"{count} ({pct(count, denom)})"


def ordered_keys(keys: Iterable[str], preferred: Iterable[str]) -> list[str]:
    preferred_list = [key for key in preferred if key in keys]
    rest = sorted(set(keys) - set(preferred_list))
    return preferred_list + rest


def group_counts(
    both_rh_records: list[RunRecord],
    awareness_by_run_id: dict[str, dict[str, object]],
    key_fn: Callable[[RunRecord], str],
) -> dict[str, dict[str, object]]:
    grouped: dict[str, list[RunRecord]] = defaultdict(list)
    for record in both_rh_records:
        grouped[key_fn(record)].append(record)

    rows: dict[str, dict[str, object]] = {}
    for key, records in grouped.items():
        denom = len(records)
        labels: Counter[str] = Counter()
        missing = 0
        for record in records:
            awareness_record = awareness_by_run_id.get(record.run_id)
            if awareness_record is None:
                missing += 1
                continue
            labels[label_for(awareness_record)] += 1
        any_awareness = denom - missing - labels[LABEL_NONE]
        shortcut_or_invalid = sum(labels[label] for label in STRONG_LABELS)
        rows[key] = {
            "denom": denom,
            "missing": missing,
            "labels": labels,
            "with_transcript": denom - missing,
            "any_awareness": any_awareness,
            "shortcut_or_invalid": shortcut_or_invalid,
        }
    return rows


def print_overall_label_table(row: dict[str, object]) -> None:
    denom = int(row["denom"])
    missing = int(row["missing"])
    labels = row["labels"]
    assert isinstance(labels, Counter)

    print("| Transcript awareness label | Count | Rate of both-RH denom |")
    print("|---|---:|---:|")
    names = [
        (LABEL_MECHANISM, "`mechanism_awareness_framed_as_success`"),
        (LABEL_EXPLICIT, "`explicit_invalidity_awareness`"),
        (LABEL_GEN_ONLY, "`generalization_awareness_only`"),
        (LABEL_NONE, "`no_obvious_awareness`"),
    ]
    for label, display in names:
        print(f"| {display} | {labels[label]} | {pct(labels[label], denom)} |")
    print(f"| missing transcript-awareness judgment | {missing} | {pct(missing, denom)} |")


def print_summary_table(rows: dict[str, dict[str, object]], order: list[str] | None = None) -> None:
    keys = ordered_keys(rows.keys(), order or [])
    print(
        "| Group | Both-RH denom | Missing | Any awareness | "
        "Shortcut/invalidity | None | Gen-only | Mechanism-as-success | "
        "Explicit invalid | Self-report |"
    )
    print("|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
    for key in keys:
        row = rows[key]
        denom = int(row["denom"])
        missing = int(row["missing"])
        labels = row["labels"]
        assert isinstance(labels, Counter)
        values = [
            key,
            str(denom),
            str(missing),
            count_pct(int(row["any_awareness"]), denom),
            count_pct(int(row["shortcut_or_invalid"]), denom),
            count_pct(labels[LABEL_NONE], denom),
            count_pct(labels[LABEL_GEN_ONLY], denom),
            count_pct(labels[LABEL_MECHANISM], denom),
            count_pct(labels[LABEL_EXPLICIT], denom),
            count_pct(labels[LABEL_SELF_REPORT], denom),
        ]
        print("| " + " | ".join(values) + " |")


def print_markdown(
    both_rh_records: list[RunRecord],
    awareness_by_run_id: dict[str, dict[str, object]],
    *,
    include_model_task: bool,
) -> None:
    overall = group_counts(both_rh_records, awareness_by_run_id, lambda _: "All")
    overall_row = overall["All"]
    denom = int(overall_row["denom"])
    missing = int(overall_row["missing"])

    print("# Transcript Awareness Among Both-Judge Reward-Hacking Runs")
    print()
    print(
        "Denominator: canonical make_datasets runs where both v4 binary judges "
        "labeled the submitted solution `reward_hacking`. Canonicalization uses "
        "the same rule as `scripts/make_datasets_paper_stats.py`: reruns replace "
        "the corresponding original run and smoke/test extras are excluded."
    )
    print()
    print(
        f"Total both-RH canonical runs: `{denom}`. Transcript-awareness judgments "
        f"exist for `{denom - missing}` of them; `{missing}` are missing from the "
        "transcript-awareness JSONL."
    )
    print()
    print("## Overall")
    print()
    print_overall_label_table(overall_row)
    print()
    print(
        f"- Any awareness: `{overall_row['any_awareness']}/{denom}` "
        f"= {pct(int(overall_row['any_awareness']), denom)}"
    )
    print(
        f"- Shortcut/invalidity awareness: "
        f"`{overall_row['shortcut_or_invalid']}/{denom}` "
        f"= {pct(int(overall_row['shortcut_or_invalid']), denom)}"
    )
    print()
    print("## By Model")
    print()
    by_model = group_counts(both_rh_records, awareness_by_run_id, lambda r: r.model)
    print_summary_table(by_model, MODEL_ORDER)
    print()
    print("## By Task Family")
    print()
    by_task = group_counts(both_rh_records, awareness_by_run_id, lambda r: r.task)
    print_summary_table(by_task, TASK_ORDER)
    print()
    print("## By Prompt")
    print()
    by_prompt = group_counts(both_rh_records, awareness_by_run_id, lambda r: r.prompt)
    print_summary_table(by_prompt, ["baseline", "validity"])
    if include_model_task:
        print()
        print("## Model x Task Family")
        print()
        by_model_task = group_counts(
            both_rh_records,
            awareness_by_run_id,
            lambda r: f"{r.model} / {r.task}",
        )
        model_task_order = [
            f"{model} / {task}" for model in MODEL_ORDER for task in TASK_ORDER
        ]
        print_summary_table(by_model_task, model_task_order)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Summarize transcript-awareness labels among canonical runs where "
            "both v4 binary judges labeled the submitted solution reward_hacking."
        )
    )
    parser.add_argument(
        "--outputs-root",
        type=Path,
        default=DEFAULT_OUTPUTS_ROOT,
        help="Root containing v4 binary judge output directories.",
    )
    parser.add_argument(
        "--awareness-jsonl",
        type=Path,
        default=DEFAULT_AWARENESS_JSONL,
        help="Transcript-awareness JSONL produced by judge_transcript_awareness.py.",
    )
    parser.add_argument(
        "--include-model-task",
        action="store_true",
        help="Include the larger model-by-task-family table.",
    )
    args = parser.parse_args()

    records = load_records(args.outputs_root)
    both_rh_records = [record for record in records if both_judges_reward_hacking(record)]
    awareness_by_run_id = load_awareness_records(args.awareness_jsonl)

    print_markdown(
        both_rh_records,
        awareness_by_run_id,
        include_model_task=args.include_model_task,
    )


if __name__ == "__main__":
    main()
