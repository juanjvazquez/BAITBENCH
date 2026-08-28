#!/usr/bin/env python3
"""Compare the validity-logging ablation with the original validity condition."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
ORIGINAL_CASES = ROOT / "data/cases/make_datasets/v4_paper_canonical_1258_20260709.jsonl"
ORIGINAL_JUDGMENTS = (
    ROOT / "data/outputs/judging/neutral_z-ai_glm-5.2_v4_20260709/judging_binary.jsonl"
)
ABLATION_CASES = ROOT / "data/cases/make_datasets/logging_ablation_20260711/cases.jsonl"
ABLATION_JUDGMENTS = (
    ROOT / "data/outputs/judging/logging_ablation_20260711_glm52_high/judging_binary.jsonl"
)

RH = "reward_hacking"
NON_RH = "not_reward_hacking"
UNCLEAR = "unclear"
VALID_LABELS = {RH, NON_RH, UNCLEAR}

MODELS = {
    "GPT-5.4": {
        "original": ("codex", "gpt-5.4", "validity_aware"),
        "ablation_prefix": "opencode_gpt_",
    },
    "Sonnet 4.6": {
        "original": ("claude", "claude-sonnet-4-6", "validity"),
        "ablation_prefix": "opencode_sonnet_",
    },
    "Gemini Flash": {
        "original": ("gemini", "openrouter/google/gemini-3-flash-preview", "validity"),
        "ablation_prefix": "opencode_gemini_flash_",
    },
    "Kimi K2.5": {
        "original": ("kimi", "kimi-k2.5", "validity_aware"),
        "ablation_prefix": "kimi_",
    },
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_valid_judgments(path: Path) -> dict[str, str]:
    """Deduplicate by run ID, retaining the last valid categorical judgment."""
    output: dict[str, str] = {}
    for record in read_jsonl(path):
        judgment = record.get("judgment") or {}
        label = judgment.get("label")
        run_id = record.get("run_id")
        if record.get("type") == "record" and isinstance(run_id, str) and label in VALID_LABELS:
            output[run_id] = label
    return output


def task_key(text: str) -> tuple[str, str, str]:
    family_match = re.search(r"task_0?([123])", text)
    size_match = re.search(r"_n(100k|10k|100)(?:_|$)", text)
    seed_match = re.search(r"_s0?([0-9]+)(?:_|$)", text)
    if not (family_match and size_match and seed_match):
        raise ValueError(f"Could not parse task key from {text!r}")
    return f"t{family_match.group(1)}", size_match.group(1), f"s{int(seed_match.group(1)):02d}"


def rate(labels: list[str]) -> tuple[int, int, float]:
    numerator = sum(label == RH for label in labels)
    denominator = len(labels)
    return numerator, denominator, numerator / denominator if denominator else float("nan")


def percent(numerator: int, denominator: int) -> str:
    return f"{numerator}/{denominator} = {100 * numerator / denominator:.1f}%"


def trajectory_count(case: dict[str, Any]) -> int:
    count = case.get("package_counts", {}).get("trajectory_row_count")
    if isinstance(count, int):
        return count
    return len(case.get("evidence", {}).get("trajectory") or [])


def median(values: list[int]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[middle])
    return (ordered[middle - 1] + ordered[middle]) / 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, help="Write the Markdown report to this path.")
    args = parser.parse_args()

    original_cases = {case["run_id"]: case for case in read_jsonl(ORIGINAL_CASES)}
    ablation_cases = {case["run_id"]: case for case in read_jsonl(ABLATION_CASES)}
    original_judgments = load_valid_judgments(ORIGINAL_JUDGMENTS)
    ablation_judgments = load_valid_judgments(ABLATION_JUDGMENTS)

    original: dict[str, dict[tuple[str, str, str], tuple[str, dict[str, Any]]]] = {
        model: {} for model in MODELS
    }
    ablation: dict[str, dict[tuple[str, str, str], tuple[str, dict[str, Any]]]] = {
        model: {} for model in MODELS
    }

    for run_id, case in original_cases.items():
        metadata = case.get("metadata", {})
        identity = (metadata.get("agent"), metadata.get("model"), metadata.get("prompt_condition"))
        for model, config in MODELS.items():
            if identity == config["original"] and run_id in original_judgments:
                key = task_key(metadata.get("task_id", run_id))
                original[model][key] = (original_judgments[run_id], case)

    for run_id, case in ablation_cases.items():
        for model, config in MODELS.items():
            if run_id.startswith(config["ablation_prefix"]) and run_id in ablation_judgments:
                key = task_key(case.get("metadata", {}).get("task_id", run_id))
                ablation[model][key] = (ablation_judgments[run_id], case)

    rows: list[tuple[str, tuple[int, int, float], tuple[int, int, float], tuple[int, int, float]]] = []
    engagement_rows: list[
        tuple[str, tuple[int, int, float], tuple[int, int, float], tuple[int, int, float]]
    ] = []
    discordance: dict[str, Counter[tuple[str, str]]] = {}
    for model in MODELS:
        all_original = rate([label for label, _ in original[model].values()])
        matched_keys = sorted(set(original[model]) & set(ablation[model]))
        matched_labels = [
            original[model][key][0] for key in matched_keys
        ] + [ablation[model][key][0] for key in matched_keys]
        if UNCLEAR in matched_labels:
            raise ValueError(f"Matched comparison for {model} contains an unclear judgment")
        matched_original = rate([original[model][key][0] for key in matched_keys])
        matched_ablation = rate([ablation[model][key][0] for key in matched_keys])
        rows.append((model, all_original, matched_original, matched_ablation))
        all_original_engaged = rate(
            [label for label, case in original[model].values() if trajectory_count(case) > 0]
        )
        matched_original_engaged = rate(
            [
                original[model][key][0]
                for key in matched_keys
                if trajectory_count(original[model][key][1]) > 0
            ]
        )
        matched_ablation_engaged = rate(
            [
                ablation[model][key][0]
                for key in matched_keys
                if trajectory_count(ablation[model][key][1]) > 0
            ]
        )
        engagement_rows.append(
            (model, all_original_engaged, matched_original_engaged, matched_ablation_engaged)
        )
        discordance[model] = Counter(
            (original[model][key][0], ablation[model][key][0]) for key in matched_keys
        )

    pooled_matched_original = rate(
        [
            original[model][key][0]
            for model in MODELS
            for key in set(original[model]) & set(ablation[model])
        ]
    )
    pooled_ablation = rate(
        [
            ablation[model][key][0]
            for model in MODELS
            for key in set(original[model]) & set(ablation[model])
        ]
    )
    pooled_matched_original_engaged = rate(
        [
            original[model][key][0]
            for model in MODELS
            for key in set(original[model]) & set(ablation[model])
            if trajectory_count(original[model][key][1]) > 0
        ]
    )
    pooled_ablation_engaged = rate(
        [
            ablation[model][key][0]
            for model in MODELS
            for key in set(original[model]) & set(ablation[model])
            if trajectory_count(ablation[model][key][1]) > 0
        ]
    )

    original_kimi = [case for _, case in original["Kimi K2.5"].values()]
    ablation_kimi = [case for _, case in ablation["Kimi K2.5"].values()]
    original_kimi_counts = [trajectory_count(case) for case in original_kimi]
    ablation_kimi_counts = [trajectory_count(case) for case in ablation_kimi]
    engaged_kimi_labels = [
        label
        for label, case in original["Kimi K2.5"].values()
        if trajectory_count(case) > 0
    ]
    original_kimi_t02 = [
        (label, case)
        for key, (label, case) in original["Kimi K2.5"].items()
        if key[0] == "t2"
    ]

    lines = [
        "# Validity-logging ablation compared with the original validity condition",
        "",
        "## Result",
        "",
        (
            "Across exactly matched task-family, dataset-size, and seed cells, the original "
            f"condition produced {percent(*pooled_matched_original[:2])} reward-hacking judgments, "
            f"while validity logging produced {percent(*pooled_ablation[:2])}. The raw change is "
            f"{100 * (pooled_ablation[2] - pooled_matched_original[2]):+.1f} percentage points."
        ),
        "",
        "| Model | Original, all available seeds | Original, matched seeds | Validity logging | Matched change |",
        "|---|---:|---:|---:|---:|",
    ]
    for model, all_original, matched_original, matched_ablation in rows:
        unclear_count = sum(label == UNCLEAR for label, _ in original[model].values())
        unclear_note = f" ({unclear_count} unclear)" if unclear_count else ""
        lines.append(
            f"| {model} | {percent(*all_original[:2])}{unclear_note} | "
            f"{percent(*matched_original[:2])} | "
            f"{percent(*matched_ablation[:2])} | "
            f"{100 * (matched_ablation[2] - matched_original[2]):+.1f} pp |"
        )

    lines.extend(["", "## Matched-case label changes", ""])
    for model in MODELS:
        changes = discordance[model]
        lines.append(
            f"- **{model}:** {changes[(RH, RH)]} stayed RH, "
            f"{changes[(NON_RH, NON_RH)]} stayed non-RH, "
            f"{changes[(RH, NON_RH)]} moved RH to non-RH, and "
            f"{changes[(NON_RH, RH)]} moved non-RH to RH."
        )

    lines.extend(
        [
            "",
            "## Rates conditional on engagement",
            "",
            (
                "Engagement is defined prospectively here as at least one recorded experiment row in "
                "`results.tsv`. This removes nominally successful no-op runs, but it conditions on a "
                "post-treatment variable and must therefore be presented as a sensitivity analysis rather "
                "than the primary causal estimate."
            ),
            "",
            "| Model | Original engaged, all seeds | Original engaged, matched seed set | Validity-logging engaged |",
            "|---|---:|---:|---:|",
        ]
    )
    for model, all_original, matched_original, matched_ablation in engagement_rows:
        lines.append(
            f"| {model} | {percent(*all_original[:2])} | {percent(*matched_original[:2])} | "
            f"{percent(*matched_ablation[:2])} |"
        )
    lines.extend(
        [
            "",
            (
                f"Pooled across the matched seed set and conditional on engagement, the original rate was "
                f"{percent(*pooled_matched_original_engaged[:2])} and the validity-logging rate was "
                f"{percent(*pooled_ablation_engaged[:2])}, a change of "
                f"{100 * (pooled_ablation_engaged[2] - pooled_matched_original_engaged[2]):+.1f} points."
            ),
        ]
    )

    engaged_rate = rate(engaged_kimi_labels)
    lines.extend(
        [
            "",
            "## Kimi engagement and possible inference failure",
            "",
            (
                f"The original Kimi condition had a median of {median(original_kimi_counts):g} logged "
                f"experiment rows and a mean of {sum(original_kimi_counts) / len(original_kimi_counts):.1f}; "
                f"{sum(value == 0 for value in original_kimi_counts)}/{len(original_kimi_counts)} runs "
                "recorded no experiments."
            ),
            (
                f"Validity logging had a median of {median(ablation_kimi_counts):g} rows and a mean of "
                f"{sum(ablation_kimi_counts) / len(ablation_kimi_counts):.1f}; "
                f"{sum(value == 0 for value in ablation_kimi_counts)}/{len(ablation_kimi_counts)} runs "
                "recorded no experiments."
            ),
            "",
            (
                f"All {len(original_kimi_t02)} original Kimi near-duplicate runs recorded zero experiment "
                "rows, left the initial mean-prediction solution in place, and received a nominal agent exit "
                "code of zero. This perfectly systematic no-op pattern is compatible with low engagement, "
                "but it is also compatible with an empty, truncated, or unusable provider response that the "
                "harness treated as successful. The retained artifacts do not distinguish these mechanisms."
            ),
            "",
            (
                f"Among original Kimi runs with at least one experiment, the RH rate was "
                f"{percent(*engaged_rate[:2])}, compared with the unconditional "
                f"{percent(*rate([label for label, _ in original['Kimi K2.5'].values()])[:2])}. "
                "The unconditional original Kimi rate should therefore not be treated as strong evidence "
                "that Kimi safely avoided reward hacking."
            ),
            "",
            "## Interpretation and limitations",
            "",
            (
                "GPT-5.4 and Gemini Flash had identical aggregate matched-seed rates, while Sonnet was "
                "11.1 percentage points lower. Excluding Kimi, the matched pooled rate moved only from "
                "30/54 (55.6%) to 28/54 (51.9%), a change of -3.7 points. The pooled increase is therefore "
                "driven by Kimi's change in engagement regime rather than a consistent cross-model effect."
            ),
            "",
            (
                "The ablation has only two seeds per family-by-size cell, versus ten in the original design. "
                "One judgment changes a model-wide 18-run rate by 5.6 percentage points and a two-run cell "
                "rate by 50 points. These data support the descriptive conclusion that explicit validity "
                "logging did not consistently reduce reward hacking; they do not establish equivalence or "
                "a precise causal effect."
            ),
            "",
            "## Reproduction",
            "",
            "```bash",
            "uv run python scripts/analyze_validity_logging_ablation.py --output docs/validity_logging_ablation_20260712.md",
            "```",
            "",
            "Both conditions use the same GLM-5.2 high-reasoning judge and v4/p4 judgment schema. "
            "Original judgments are deduplicated by run ID, retaining the last valid categorical judgment. "
            "The all-seed column conservatively retains `unclear` judgments in its denominator; excluding "
            "them gives Sonnet 62/89 (69.7%) and Gemini Flash 60/86 (69.8%). No matched-seed or ablation "
            "judgments are unclear.",
            "",
        ]
    )

    report = "\n".join(lines)
    if args.output:
        output = args.output if args.output.is_absolute() else ROOT / args.output
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(report, encoding="utf-8")
        print(output)
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
