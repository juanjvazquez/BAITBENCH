#!/usr/bin/env python3
"""Reproduce logging-ablation rates and paired cluster-bootstrap intervals."""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence


MODELS = ("GPT-5.4", "Sonnet 4.6", "Gemini Flash", "Kimi K2.5")
EXPECTED_COUNTS = {"GPT-5.4": 18, "Sonnet 4.6": 18, "Gemini Flash": 18, "Kimi K2.5": 17}
INTEGER_FIELDS = {
    "dataset_size",
    "seed",
    "original_rh",
    "logging_rh",
    "difference",
    "original_logged_experiment_rows",
    "logging_logged_experiment_rows",
    "original_engaged",
    "logging_engaged",
}


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open(newline="") as file:
        rows = list(csv.DictReader(file))
    for row in rows:
        for field in INTEGER_FIELDS:
            row[field] = int(row[field])
        if row["difference"] != row["logging_rh"] - row["original_rh"]:
            raise ValueError(f"Invalid paired difference for {row['logging_run_id']}")
        if row["original_engaged"] != int(row["original_logged_experiment_rows"] > 0):
            raise ValueError(f"Invalid original engagement for {row['original_run_id']}")
        if row["logging_engaged"] != int(row["logging_logged_experiment_rows"] > 0):
            raise ValueError(f"Invalid logging engagement for {row['logging_run_id']}")
    counts = {model: sum(row["model"] == model for row in rows) for model in MODELS}
    if len(rows) != 71 or counts != EXPECTED_COUNTS:
        raise ValueError(f"Unexpected matched coverage: total={len(rows)}, models={counts}")
    return rows


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
        strata[(row["task_family"], row["dataset_size"])][row["seed"]].append(row)

    rng = random.Random(random_seed)
    draws: list[float] = []
    for _ in range(repetitions):
        sampled: list[dict[str, Any]] = []
        for stratum in sorted(strata):
            clusters = list(strata[stratum].values())
            for _ in range(len(clusters)):
                sampled.extend(clusters[rng.randrange(len(clusters))])
        draws.append(mean([row["difference"] for row in sampled]))
    return {
        "estimate": mean([row["difference"] for row in rows]),
        "bootstrap_mean": mean(draws),
        "ci95_low": percentile(draws, 0.025),
        "ci95_high": percentile(draws, 0.975),
    }


def rate(rows: Sequence[dict[str, Any]], field: str) -> dict[str, float | int]:
    numerator = sum(row[field] for row in rows)
    return {"numerator": numerator, "denominator": len(rows), "rate": numerator / len(rows)}


def summarize_group(
    rows: list[dict[str, Any]], repetitions: int, random_seed: int
) -> dict[str, Any]:
    original_engaged = [row for row in rows if row["original_engaged"]]
    logging_engaged = [row for row in rows if row["logging_engaged"]]
    original_engaged_rate = rate(original_engaged, "original_rh")
    logging_engaged_rate = rate(logging_engaged, "logging_rh")
    return {
        "matched_pairs": len(rows),
        "original": rate(rows, "original_rh"),
        "logging": rate(rows, "logging_rh"),
        "difference": mean([row["difference"] for row in rows]),
        "paired_cluster_bootstrap": cluster_bootstrap(rows, repetitions, random_seed),
        "engagement": {
            "original": {"engaged": len(original_engaged), "total": len(rows)},
            "logging": {"engaged": len(logging_engaged), "total": len(rows)},
        },
        "engaged_sensitivity": {
            "original": original_engaged_rate,
            "logging": logging_engaged_rate,
            "difference": logging_engaged_rate["rate"] - original_engaged_rate["rate"],
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("matched_pairs.csv"))
    parser.add_argument("--output", type=Path, default=Path("summary.json"))
    parser.add_argument("--bootstrap-reps", type=int, default=10_000)
    parser.add_argument("--random-seed", type=int, default=20_260_712)
    args = parser.parse_args()

    rows = load_rows(args.input)
    result = {
        "method": {
            "primary_estimand": "mean paired difference: logging RH indicator minus original RH indicator",
            "fixed_strata": ["task_family", "dataset_size"],
            "resampling_cluster": ["task_family", "dataset_size", "seed"],
            "cluster_contents": "all available model pairs for the sampled dataset instance",
            "bootstrap_repetitions": args.bootstrap_reps,
            "random_seed": args.random_seed,
            "interval": "95% percentile cluster bootstrap with linear interpolation",
            "engagement_definition": "at least one recorded experiment row in results.tsv",
            "engagement_note": "post-treatment sensitivity analysis, not the primary estimate",
        },
        "pooled": summarize_group(rows, args.bootstrap_reps, args.random_seed),
        "pooled_excluding_kimi": summarize_group(
            [row for row in rows if row["model"] != "Kimi K2.5"],
            args.bootstrap_reps,
            args.random_seed,
        ),
        "models": {
            model: summarize_group(
                [row for row in rows if row["model"] == model],
                args.bootstrap_reps,
                args.random_seed,
            )
            for model in MODELS
        },
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output)


if __name__ == "__main__":
    main()
