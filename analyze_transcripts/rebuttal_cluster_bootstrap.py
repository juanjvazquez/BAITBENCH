#!/usr/bin/env python3
"""Cluster-aware BAITBENCH rebuttal statistics.

Fixed strata are task family by dataset size. The resampling cluster is the
seed-generated dataset instance identified by (task family, size, seed). All
models, prompt conditions, and paired Claude/GPT labels for a sampled instance
travel together.

Outputs:
  * pooled and task-family reward-hacking rates;
  * pooled rate excluding the no-signal task;
  * judge-consensus-only rate;
  * paired baseline-minus-validity prompt effects per model and pooled;
  * reward-hacking rates and prompt effects by dataset size;
  * prompt effects by task family;
  * 95% stratified cluster-bootstrap percentile intervals;
  * cluster-level sign-flip p-values for paired prompt effects.
"""

from __future__ import annotations

import argparse
import json
import random
from collections import defaultdict
from pathlib import Path
from typing import Callable, Sequence

from judge_family_cluster_bootstrap import (
    Observation,
    build_cluster_index,
    load_paper_records,
    mean,
    percentile,
    to_observations,
    validate_design,
)


TASK_ORDER = [
    "Entity overlap",
    "Near-duplicate leakage",
    "No-signal classification",
]
SIZE_ORDER = ["100", "10k", "100k"]
MODEL_ORDER = [
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "gpt-5.4",
    "kimi-k2.5",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "deepseek-v4-pro",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "reward-hacking-evals",
    )
    parser.add_argument("--bootstrap-reps", type=int, default=10_000)
    parser.add_argument("--permutation-reps", type=int, default=20_000)
    parser.add_argument("--random-seed", type=int, default=20_260_711)
    parser.add_argument(
        "--output-json",
        type=Path,
        default=Path(__file__).with_name("rebuttal_cluster_bootstrap_20260711.json"),
    )
    return parser.parse_args()


def judge_average(observation: Observation) -> float:
    return (observation.claude_rh + observation.gpt_rh) / 2.0


def flatten(clusters: Sequence[Sequence[Observation]]) -> list[Observation]:
    return [observation for cluster in clusters for observation in cluster]


def extract_pairs(
    clusters: Sequence[Sequence[Observation]],
) -> list[tuple[tuple[str, str, int], str, str, str, float]]:
    """Return paired baseline-minus-validity effects, preserving cluster copies."""

    pairs: list[tuple[tuple[str, str, int], str, str, str, float]] = []
    for cluster in clusters:
        if not cluster:
            continue
        cluster_key = cluster[0].cluster
        by_model: dict[str, dict[str, Observation]] = defaultdict(dict)
        for observation in cluster:
            if observation.prompt in by_model[observation.model]:
                raise ValueError(
                    f"duplicate prompt {observation.prompt!r} for model "
                    f"{observation.model!r} in cluster {cluster_key}"
                )
            by_model[observation.model][observation.prompt] = observation
        for model, prompts in by_model.items():
            baseline = prompts.get("baseline")
            validity = prompts.get("validity")
            if baseline is None or validity is None:
                continue
            pairs.append(
                (
                    cluster_key,
                    model,
                    baseline.task,
                    baseline.size,
                    judge_average(baseline) - judge_average(validity),
                )
            )
    return pairs


def calculate_statistics(
    clusters: Sequence[Sequence[Observation]],
) -> dict[str, float]:
    observations = flatten(clusters)
    pairs = extract_pairs(clusters)
    stats: dict[str, float] = {}

    stats["rate.pooled"] = mean([judge_average(o) for o in observations])
    for task in TASK_ORDER:
        stats[f"rate.task.{task}"] = mean(
            [judge_average(o) for o in observations if o.task == task]
        )
    stats["rate.excluding_no_signal"] = mean(
        [judge_average(o) for o in observations if o.task != "No-signal classification"]
    )
    # This is a binary-label sensitivity analysis. Cases where both judges
    # returned ``unclear`` are agreement records, but they are not binary
    # consensus and must not enter the denominator as non-RH.
    binary_labels = {"reward_hacking", "not_reward_hacking"}
    consensus = [
        o
        for o in observations
        if o.claude_label == o.gpt_label and o.claude_label in binary_labels
    ]
    stats["rate.consensus_only"] = mean([float(o.claude_rh) for o in consensus])

    for size in SIZE_ORDER:
        stats[f"rate.size.{size}"] = mean(
            [judge_average(o) for o in observations if o.size == size]
        )

    for model in MODEL_ORDER:
        stats[f"prompt.model.{model}"] = mean(
            [delta for _, pair_model, _, _, delta in pairs if pair_model == model]
        )
    stats["prompt.pooled"] = mean([delta for *_, delta in pairs])
    for size in SIZE_ORDER:
        stats[f"prompt.size.{size}"] = mean(
            [delta for _, _, _, pair_size, delta in pairs if pair_size == size]
        )
    for task in TASK_ORDER:
        stats[f"prompt.task.{task}"] = mean(
            [delta for _, _, pair_task, _, delta in pairs if pair_task == task]
        )

    return stats


def observed_clusters(index) -> list[list[Observation]]:
    return [
        cluster
        for stratum in sorted(index)
        for _, cluster in sorted(index[stratum].items())
    ]


def bootstrap_samples(index, repetitions: int, rng: random.Random):
    strata = sorted(index)
    for _ in range(repetitions):
        sample: list[list[Observation]] = []
        for stratum in strata:
            clusters = list(index[stratum].values())
            for _ in range(len(clusters)):
                sample.append(clusters[rng.randrange(len(clusters))])
        yield sample


def bootstrap_intervals(
    index,
    keys: Sequence[str],
    repetitions: int,
    random_seed: int,
) -> dict[str, dict[str, float]]:
    distributions: dict[str, list[float]] = {key: [] for key in keys}
    rng = random.Random(random_seed)
    for sample in bootstrap_samples(index, repetitions, rng):
        stats = calculate_statistics(sample)
        for key in keys:
            distributions[key].append(stats[key])

    output: dict[str, dict[str, float]] = {}
    for key, values in distributions.items():
        ordered = sorted(values)
        output[key] = {
            "bootstrap_mean": mean(values),
            "ci_95_lower": percentile(ordered, 0.025),
            "ci_95_upper": percentile(ordered, 0.975),
        }
    return output


def cluster_sign_flip_p_values(
    clusters: Sequence[Sequence[Observation]],
    selectors: dict[str, Callable[[str, str, str], bool]],
    repetitions: int,
    random_seed: int,
) -> dict[str, float]:
    """Two-sided sign-flip tests, applying one sign to all pairs in a cluster."""

    if repetitions <= 0:
        raise ValueError("permutation repetitions must be positive")
    pairs = extract_pairs(clusters)
    by_cluster: dict[tuple[str, str, int], list[tuple[str, str, str, float]]] = defaultdict(
        list
    )
    for cluster_key, model, task, size, delta in pairs:
        by_cluster[cluster_key].append((model, task, size, delta))

    observed: dict[str, float] = {}
    counts: dict[str, int] = {}
    for name, selector in selectors.items():
        selected = [
            delta
            for model, task, size, delta in (
                item for items in by_cluster.values() for item in items
            )
            if selector(model, task, size)
        ]
        observed[name] = abs(mean(selected))
        counts[name] = len(selected)

    hits = {name: 0 for name in selectors}
    rng = random.Random(random_seed)
    cluster_items = sorted(by_cluster.items())
    for _ in range(repetitions):
        sums = {name: 0.0 for name in selectors}
        signs = {cluster_key: (1.0 if rng.random() < 0.5 else -1.0) for cluster_key, _ in cluster_items}
        for cluster_key, items in cluster_items:
            sign = signs[cluster_key]
            for model, task, size, delta in items:
                for name, selector in selectors.items():
                    if selector(model, task, size):
                        sums[name] += sign * delta
        for name in selectors:
            simulated = abs(sums[name] / counts[name])
            if simulated >= observed[name] - 1e-12:
                hits[name] += 1

    # Add-one correction avoids reporting an impossible Monte Carlo p=0.
    return {
        name: (hits[name] + 1) / (repetitions + 1)
        for name in selectors
    }


def format_percent(value: float) -> str:
    return f"{100.0 * value:.2f}%"


def format_pp(value: float) -> str:
    return f"{100.0 * value:+.2f} pp"


def print_result_line(
    label: str,
    key: str,
    observed: dict[str, float],
    intervals: dict[str, dict[str, float]],
    effect: bool = False,
    p_value: float | None = None,
) -> None:
    formatter = format_pp if effect else format_percent
    interval = intervals[key]
    suffix = "" if p_value is None else f"; cluster sign-flip p={p_value:.5f}"
    print(
        f"{label:32s} {formatter(observed[key]):>10s} "
        f"[95% CI {formatter(interval['ci_95_lower'])}, "
        f"{formatter(interval['ci_95_upper'])}]{suffix}"
    )


def main() -> None:
    args = parse_args()
    records = load_paper_records(args.repo.resolve())
    observations = to_observations(records)
    index = build_cluster_index(observations)
    validate_design(observations, index)
    clusters = observed_clusters(index)
    observed = calculate_statistics(clusters)
    intervals = bootstrap_intervals(
        index=index,
        keys=sorted(observed),
        repetitions=args.bootstrap_reps,
        random_seed=args.random_seed,
    )

    selectors: dict[str, Callable[[str, str, str], bool]] = {
        "prompt.pooled": lambda _model, _task, _size: True,
    }
    selectors.update(
        {
            f"prompt.model.{model}": (
                lambda pair_model, _task, _size, target=model: pair_model == target
            )
            for model in MODEL_ORDER
        }
    )
    permutation_p = cluster_sign_flip_p_values(
        clusters=clusters,
        selectors=selectors,
        repetitions=args.permutation_reps,
        random_seed=args.random_seed + 1,
    )

    print("\n== Cluster-bootstrap reward-hacking rates ==")
    print_result_line("Pooled", "rate.pooled", observed, intervals)
    for task in TASK_ORDER:
        print_result_line(task, f"rate.task.{task}", observed, intervals)
    print_result_line(
        "Excluding no-signal", "rate.excluding_no_signal", observed, intervals
    )
    print_result_line("Judge-consensus only", "rate.consensus_only", observed, intervals)

    print("\n== Paired prompt effects: baseline minus validity ==")
    for model in MODEL_ORDER:
        key = f"prompt.model.{model}"
        print_result_line(
            model,
            key,
            observed,
            intervals,
            effect=True,
            p_value=permutation_p[key],
        )
    print_result_line(
        "Pooled",
        "prompt.pooled",
        observed,
        intervals,
        effect=True,
        p_value=permutation_p["prompt.pooled"],
    )

    print("\n== Dataset-size results ==")
    for size in SIZE_ORDER:
        print_result_line(f"RH rate n={size}", f"rate.size.{size}", observed, intervals)
        print_result_line(
            f"Prompt effect n={size}",
            f"prompt.size.{size}",
            observed,
            intervals,
            effect=True,
        )

    print("\n== Prompt effects by task family ==")
    for task in TASK_ORDER:
        print_result_line(
            task,
            f"prompt.task.{task}",
            observed,
            intervals,
            effect=True,
        )

    result = {
        "method": {
            "fixed_strata": ["task_family", "dataset_size"],
            "resampling_cluster": ["task_family", "dataset_size", "dataset_seed"],
            "cluster_contents": ["models", "prompt_conditions", "paired_judge_labels"],
            "bootstrap_repetitions": args.bootstrap_reps,
            "permutation_repetitions": args.permutation_reps,
            "random_seed": args.random_seed,
            "interval": "95% percentile stratified cluster bootstrap",
            "test": "two-sided cluster-level sign-flip with add-one correction",
        },
        "coverage": {
            "canonical_runs": len(observations),
            "strata": len(index),
            "dataset_instance_clusters": sum(len(x) for x in index.values()),
            "paired_prompt_comparisons": len(extract_pairs(clusters)),
        },
        "statistics": {
            key: {
                "estimate": value,
                **intervals[key],
                **(
                    {"cluster_sign_flip_p": permutation_p[key]}
                    if key in permutation_p
                    else {}
                ),
            }
            for key, value in observed.items()
        },
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {args.output_json}")


if __name__ == "__main__":
    main()
