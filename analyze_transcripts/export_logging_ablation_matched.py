#!/usr/bin/env python3
"""Export and analyze the matched validity-logging ablation cases."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence


RH = "reward_hacking"
NON_RH = "not_reward_hacking"
UNCLEAR = "unclear"
VALID_LABELS = {RH, NON_RH, UNCLEAR}
SIZE_RE = re.compile(r"_n(100k|10k|100)(?:_|$)")
SEED_RE = re.compile(r"_s0?([0-9]+)(?:_|$)")
SIZE_VALUES = {"100": 100, "10k": 10_000, "100k": 100_000}

MODELS = {
    "GPT-5.4": {
        "original": ("codex", "gpt-5.4", "validity_aware"),
        "logging_prefix": "opencode_gpt_",
    },
    "Sonnet 4.6": {
        "original": ("claude", "claude-sonnet-4-6", "validity"),
        "logging_prefix": "opencode_sonnet_",
    },
    "Gemini Flash": {
        "original": ("gemini", "openrouter/google/gemini-3-flash-preview", "validity"),
        "logging_prefix": "opencode_gemini_flash_",
    },
    "Kimi K2.5": {
        "original": ("kimi", "kimi-k2.5", "validity_aware"),
        "logging_prefix": "kimi_",
    },
}

FIELDNAMES = [
    "model",
    "task_family",
    "dataset_size",
    "seed",
    "original_run_id",
    "logging_run_id",
    "original_label",
    "logging_label",
    "original_rh",
    "logging_rh",
    "difference",
    "original_logged_experiment_rows",
    "logging_logged_experiment_rows",
    "original_engaged",
    "logging_engaged",
    "original_test_score",
    "logging_test_score",
    "original_robust_score",
    "logging_robust_score",
    "original_generalization_gap",
    "logging_generalization_gap",
]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_labels(path: Path) -> dict[str, str]:
    labels: dict[str, str] = {}
    for record in read_jsonl(path):
        judgment = record.get("judgment")
        label = judgment.get("label") if isinstance(judgment, dict) else None
        run_id = record.get("run_id")
        if record.get("type") == "record" and isinstance(run_id, str) and label in VALID_LABELS:
            labels[run_id] = label
    return labels


def task_key(case: dict[str, Any]) -> tuple[str, int, int]:
    metadata = case["metadata"]
    task_id = metadata["task_id"]
    size_match = SIZE_RE.search(task_id)
    seed_match = SEED_RE.search(task_id)
    if size_match is None or seed_match is None:
        raise ValueError(f"Could not parse size and seed from {task_id!r}")
    return metadata["family_code"], SIZE_VALUES[size_match.group(1)], int(seed_match.group(1))


def logged_rows(case: dict[str, Any]) -> int:
    count = case.get("package_counts", {}).get("trajectory_row_count")
    if not isinstance(count, int):
        raise ValueError(f"Missing trajectory_row_count for {case.get('run_id')}")
    return count


def robust_values(case: dict[str, Any]) -> tuple[Any, Any, Any]:
    robust = case.get("evidence", {}).get("robust_evaluation") or {}
    return robust.get("test_score"), robust.get("robust_score"), robust.get("generalization_gap")


def build_matched_rows(repo: Path) -> tuple[list[dict[str, Any]], dict[str, dict[str, int]]]:
    original_cases_path = repo / "data/cases/make_datasets/v4_paper_canonical_1258_20260709.jsonl"
    original_judgments_path = (
        repo / "data/outputs/judging/neutral_z-ai_glm-5.2_v4_20260709/judging_binary.jsonl"
    )
    logging_cases_path = repo / "data/cases/make_datasets/logging_ablation_20260711/cases.jsonl"
    logging_judgments_path = (
        repo / "data/outputs/judging/logging_ablation_20260711_glm52_high/judging_binary.jsonl"
    )

    original_labels = load_labels(original_judgments_path)
    logging_labels = load_labels(logging_judgments_path)
    original_cases = read_jsonl(original_cases_path)
    logging_cases = read_jsonl(logging_cases_path)

    original: dict[str, dict[tuple[str, int, int], dict[str, Any]]] = {
        model: {} for model in MODELS
    }
    logging: dict[str, dict[tuple[str, int, int], dict[str, Any]]] = {
        model: {} for model in MODELS
    }
    all_original_engagement: dict[str, dict[str, int]] = {}

    for model, config in MODELS.items():
        selected: list[dict[str, Any]] = []
        for case in original_cases:
            metadata = case["metadata"]
            identity = (metadata.get("agent"), metadata.get("model"), metadata.get("prompt_condition"))
            if identity == config["original"] and case["run_id"] in original_labels:
                key = task_key(case)
                if key in original[model]:
                    raise ValueError(f"Duplicate original case for {model} {key}")
                original[model][key] = case
                selected.append(case)
        all_original_engagement[model] = {
            "engaged": sum(logged_rows(case) > 0 for case in selected),
            "total": len(selected),
        }

    for case in logging_cases:
        run_id = case["run_id"]
        for model, config in MODELS.items():
            if run_id.startswith(config["logging_prefix"]) and run_id in logging_labels:
                key = task_key(case)
                if key in logging[model]:
                    raise ValueError(f"Duplicate logging case for {model} {key}")
                logging[model][key] = case

    rows: list[dict[str, Any]] = []
    for model in MODELS:
        for key in sorted(set(original[model]) & set(logging[model])):
            original_case = original[model][key]
            logging_case = logging[model][key]
            original_label = original_labels[original_case["run_id"]]
            logging_label = logging_labels[logging_case["run_id"]]
            if original_label == UNCLEAR or logging_label == UNCLEAR:
                raise ValueError(f"Matched comparison contains unclear label for {model} {key}")
            original_test, original_robust, original_gap = robust_values(original_case)
            logging_test, logging_robust, logging_gap = robust_values(logging_case)
            original_count = logged_rows(original_case)
            logging_count = logged_rows(logging_case)
            original_rh = int(original_label == RH)
            logging_rh = int(logging_label == RH)
            rows.append(
                {
                    "model": model,
                    "task_family": key[0],
                    "dataset_size": key[1],
                    "seed": key[2],
                    "original_run_id": original_case["run_id"],
                    "logging_run_id": logging_case["run_id"],
                    "original_label": original_label,
                    "logging_label": logging_label,
                    "original_rh": original_rh,
                    "logging_rh": logging_rh,
                    "difference": logging_rh - original_rh,
                    "original_logged_experiment_rows": original_count,
                    "logging_logged_experiment_rows": logging_count,
                    "original_engaged": int(original_count > 0),
                    "logging_engaged": int(logging_count > 0),
                    "original_test_score": original_test,
                    "logging_test_score": logging_test,
                    "original_robust_score": original_robust,
                    "logging_robust_score": logging_robust,
                    "original_generalization_gap": original_gap,
                    "logging_generalization_gap": logging_gap,
                }
            )

    expected = {"GPT-5.4": 18, "Sonnet 4.6": 18, "Gemini Flash": 18, "Kimi K2.5": 17}
    observed = {model: sum(row["model"] == model for row in rows) for model in MODELS}
    if observed != expected or len(rows) != 71:
        raise ValueError(f"Unexpected matched coverage: {observed}; total={len(rows)}")
    return rows, all_original_engagement


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("Mean requires at least one value")
    return sum(values) / len(values)


def percentile(values: Sequence[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def cluster_bootstrap(
    rows: Sequence[dict[str, Any]], repetitions: int, random_seed: int
) -> dict[str, float]:
    strata: dict[tuple[str, int], dict[int, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for row in rows:
        strata[(row["task_family"], int(row["dataset_size"]))][int(row["seed"])].append(row)

    rng = random.Random(random_seed)
    draws: list[float] = []
    for _ in range(repetitions):
        sampled: list[dict[str, Any]] = []
        for stratum in sorted(strata):
            clusters = list(strata[stratum].values())
            for _ in range(len(clusters)):
                sampled.extend(clusters[rng.randrange(len(clusters))])
        draws.append(mean([float(row["difference"]) for row in sampled]))
    return {
        "estimate": mean([float(row["difference"]) for row in rows]),
        "bootstrap_mean": mean(draws),
        "ci95_low": percentile(draws, 0.025),
        "ci95_high": percentile(draws, 0.975),
    }


def rate(rows: Iterable[dict[str, Any]], field: str) -> dict[str, Any]:
    selected = list(rows)
    numerator = sum(int(row[field]) for row in selected)
    return {"numerator": numerator, "denominator": len(selected), "rate": numerator / len(selected)}


def analyze(
    rows: list[dict[str, Any]],
    all_original_engagement: dict[str, dict[str, int]],
    repetitions: int,
    random_seed: int,
) -> dict[str, Any]:
    by_model: dict[str, Any] = {}
    for model in MODELS:
        model_rows = [row for row in rows if row["model"] == model]
        original_engaged = [row for row in model_rows if row["original_engaged"]]
        logging_engaged = [row for row in model_rows if row["logging_engaged"]]
        by_model[model] = {
            "matched_pairs": len(model_rows),
            "original": rate(model_rows, "original_rh"),
            "logging": rate(model_rows, "logging_rh"),
            "difference": mean([row["difference"] for row in model_rows]),
            "paired_cluster_bootstrap": cluster_bootstrap(model_rows, repetitions, random_seed),
            "matched_engagement": {
                "original": {"engaged": len(original_engaged), "total": len(model_rows)},
                "logging": {"engaged": len(logging_engaged), "total": len(model_rows)},
            },
            "engaged_sensitivity": {
                "original": rate(original_engaged, "original_rh"),
                "logging": rate(logging_engaged, "logging_rh"),
            },
            "all_original_valid_judgments_engagement": all_original_engagement[model],
        }

    original_engaged = [row for row in rows if row["original_engaged"]]
    logging_engaged = [row for row in rows if row["logging_engaged"]]
    return {
        "method": {
            "primary_estimand": "mean paired difference: logging RH indicator minus original RH indicator",
            "fixed_strata": ["task_family", "dataset_size"],
            "resampling_cluster": ["task_family", "dataset_size", "seed"],
            "cluster_contents": "all available model pairs for the sampled dataset instance",
            "bootstrap_repetitions": repetitions,
            "random_seed": random_seed,
            "interval": "95% percentile cluster bootstrap with linear interpolation",
            "engagement_definition": "at least one recorded experiment row in results.tsv",
            "engagement_note": "post-treatment sensitivity analysis, not the primary estimate",
        },
        "coverage": {"matched_pairs": len(rows), "models": len(MODELS)},
        "pooled": {
            "original": rate(rows, "original_rh"),
            "logging": rate(rows, "logging_rh"),
            "difference": mean([row["difference"] for row in rows]),
            "paired_cluster_bootstrap": cluster_bootstrap(rows, repetitions, random_seed),
            "matched_engagement": {
                "original": {"engaged": len(original_engaged), "total": len(rows)},
                "logging": {"engaged": len(logging_engaged), "total": len(rows)},
            },
            "engaged_sensitivity": {
                "original": rate(original_engaged, "original_rh"),
                "logging": rate(logging_engaged, "logging_rh"),
                "difference": rate(logging_engaged, "logging_rh")["rate"]
                - rate(original_engaged, "original_rh")["rate"],
            },
        },
        "models": by_model,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--bootstrap-reps", type=int, default=10_000)
    parser.add_argument("--random-seed", type=int, default=20_260_712)
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    rows, all_original_engagement = build_matched_rows(args.repo.resolve())
    summary = analyze(rows, all_original_engagement, args.bootstrap_reps, args.random_seed)
    write_csv(output_dir / "matched_pairs.csv", rows)
    (output_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(output_dir / "matched_pairs.csv")
    print(output_dir / "summary.json")


if __name__ == "__main__":
    main()
