#!/usr/bin/env python3
"""Stratified dataset-instance cluster bootstrap for judge-family effects.

The estimand compares the Claude-minus-GPT binary reward-hacking judgment gap
on an own-family group with the same judge gap on agents from neither family.

Experimental structure
----------------------
Fixed strata:
    (task family, dataset size)

Resampling cluster:
    (task family, dataset size, dataset seed)

All model runs, prompt conditions, and paired judge labels belonging to a
sampled dataset instance travel together. Within each task-by-size stratum,
the observed seed clusters are sampled with replacement.

This analysis is conditional on the evaluated models and benchmark strata. It
does not estimate variation across a population of unseen GPT/Claude models.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence


RH_LABEL = "reward_hacking"
SIZE_SEED_RE = re.compile(r"_n(?P<size>100k|10k|100)_s(?P<seed>\d+)_")


@dataclass(frozen=True)
class Observation:
    """One canonical agent run with its paired judge labels."""

    run_id: str
    model: str
    task: str
    size: str
    seed: int
    prompt: str
    claude_label: str
    gpt_label: str
    claude_rh: int
    gpt_rh: int

    @property
    def judge_delta(self) -> int:
        """Claude RH indicator minus GPT RH indicator."""

        return self.claude_rh - self.gpt_rh

    @property
    def stratum(self) -> tuple[str, str]:
        return self.task, self.size

    @property
    def cluster(self) -> tuple[str, str, int]:
        return self.task, self.size, self.seed


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "reward-hacking-evals",
        help="Path to reward-hacking-evals (default: sibling workspace repo)",
    )
    parser.add_argument(
        "--bootstrap-reps",
        type=int,
        default=10_000,
        help="Number of bootstrap replications (default: 10000)",
    )
    parser.add_argument(
        "--random-seed",
        type=int,
        default=20_260_711,
        help="Deterministic bootstrap RNG seed (default: 20260711)",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=None,
        help="Optional path for a machine-readable result",
    )
    return parser.parse_args()


def load_paper_records(repo: Path):
    """Load the same canonical collapsed records used by the paper script."""

    module_path = repo / "scripts" / "make_datasets_paper_stats.py"
    if not module_path.is_file():
        raise FileNotFoundError(f"paper statistics module not found: {module_path}")

    spec = importlib.util.spec_from_file_location("make_datasets_paper_stats", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not import {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.load_records(repo / "data" / "outputs" / "judging")


def to_observations(records: Iterable[object]) -> list[Observation]:
    observations: list[Observation] = []
    unparseable: list[str] = []

    for record in records:
        match = SIZE_SEED_RE.search(record.run_id)
        if match is None:
            unparseable.append(record.run_id)
            continue
        observations.append(
            Observation(
                run_id=record.run_id,
                model=record.model,
                task=record.task,
                size=match.group("size"),
                seed=int(match.group("seed")),
                prompt=record.prompt,
                claude_label=record.claude_label,
                gpt_label=record.gpt_label,
                claude_rh=int(record.claude_label == RH_LABEL),
                gpt_rh=int(record.gpt_label == RH_LABEL),
            )
        )

    if unparseable:
        preview = ", ".join(unparseable[:5])
        raise ValueError(
            f"could not parse size/seed from {len(unparseable)} run IDs; first: {preview}"
        )
    if not observations:
        raise ValueError("no canonical observations loaded")
    return observations


def mean(values: Sequence[float]) -> float:
    if not values:
        raise ValueError("mean requires at least one value")
    return sum(values) / len(values)


def family_effects(observations: Sequence[Observation]) -> dict[str, float]:
    """Return own-family excess judge gaps relative to neutral-model runs."""

    claude_models = {"claude-opus-4-6", "claude-sonnet-4-6"}
    gpt_models = {"gpt-5.4"}
    family_models = claude_models | gpt_models

    neutral = [o.judge_delta for o in observations if o.model not in family_models]
    claude = [o.judge_delta for o in observations if o.model in claude_models]
    gpt = [o.judge_delta for o in observations if o.model in gpt_models]
    if not neutral or not claude or not gpt:
        raise ValueError(
            "analysis requires Claude-family, GPT-family, and neutral-model observations"
        )

    neutral_gap = mean(neutral)
    claude_gap = mean(claude)
    gpt_gap = mean(gpt)
    return {
        "neutral_gap": neutral_gap,
        "claude_family_gap": claude_gap,
        "gpt_family_gap": gpt_gap,
        "claude_family_effect": claude_gap - neutral_gap,
        "gpt_family_effect": gpt_gap - neutral_gap,
    }


def build_cluster_index(
    observations: Sequence[Observation],
) -> dict[tuple[str, str], dict[int, list[Observation]]]:
    """Index complete dataset-instance clusters within fixed strata."""

    index: dict[tuple[str, str], dict[int, list[Observation]]] = defaultdict(
        lambda: defaultdict(list)
    )
    seen_run_ids: set[str] = set()
    for observation in observations:
        if observation.run_id in seen_run_ids:
            raise ValueError(f"duplicate canonical run ID: {observation.run_id}")
        seen_run_ids.add(observation.run_id)
        index[observation.stratum][observation.seed].append(observation)
    return {stratum: dict(seed_map) for stratum, seed_map in index.items()}


def validate_design(
    observations: Sequence[Observation],
    index: dict[tuple[str, str], dict[int, list[Observation]]],
) -> None:
    """Fail on structural errors and print transparent coverage diagnostics."""

    print("== Design diagnostics ==")
    print(f"Canonical observations: {len(observations)}")
    print(f"Fixed task×size strata: {len(index)}")
    print(f"Dataset-instance clusters: {sum(len(x) for x in index.values())}")
    print(f"Models: {dict(sorted(Counter(o.model for o in observations).items()))}")
    print(f"Prompts: {dict(sorted(Counter(o.prompt for o in observations).items()))}")

    expected_strata = 9
    if len(index) != expected_strata:
        raise ValueError(f"expected {expected_strata} task×size strata, found {len(index)}")

    cluster_shapes: Counter[tuple[tuple[str, int], ...]] = Counter()
    missing_seed_strata: list[tuple[tuple[str, str], int]] = []
    for stratum, seed_map in sorted(index.items()):
        if len(seed_map) != 10:
            missing_seed_strata.append((stratum, len(seed_map)))
        for cluster in seed_map.values():
            model_counts = tuple(sorted(Counter(o.model for o in cluster).items()))
            cluster_shapes[model_counts] += 1

    print("Seeds per stratum:")
    for stratum, seed_map in sorted(index.items()):
        print(f"  {stratum}: {len(seed_map)} seeds; {sum(map(len, seed_map.values()))} runs")
    print(f"Distinct cluster membership shapes: {len(cluster_shapes)}")
    for shape, count in cluster_shapes.most_common():
        print(f"  {count} clusters: {dict(shape)}")

    if missing_seed_strata:
        raise ValueError(f"expected 10 seeds in every stratum; found {missing_seed_strata}")


def bootstrap_effects(
    index: dict[tuple[str, str], dict[int, list[Observation]]],
    repetitions: int,
    random_seed: int,
) -> tuple[list[float], list[float]]:
    """Resample seed clusters within task×size strata."""

    if repetitions <= 0:
        raise ValueError("bootstrap repetitions must be positive")

    rng = random.Random(random_seed)
    claude_effects: list[float] = []
    gpt_effects: list[float] = []
    strata = sorted(index)

    for _ in range(repetitions):
        sample: list[Observation] = []
        for stratum in strata:
            clusters = list(index[stratum].values())
            # Resample the observed number of seed-generated instances within
            # this fixed task×size stratum. A selected cluster brings every
            # model, prompt, and paired judge observation with it.
            for _ in range(len(clusters)):
                sample.extend(clusters[rng.randrange(len(clusters))])
        effects = family_effects(sample)
        claude_effects.append(effects["claude_family_effect"])
        gpt_effects.append(effects["gpt_family_effect"])

    return claude_effects, gpt_effects


def percentile(sorted_values: Sequence[float], probability: float) -> float:
    """Linearly interpolated empirical percentile (NumPy-compatible default)."""

    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be in [0, 1]")
    if not sorted_values:
        raise ValueError("percentile requires at least one value")
    position = (len(sorted_values) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def summarize_bootstrap(values: list[float]) -> dict[str, float]:
    ordered = sorted(values)
    return {
        "bootstrap_mean": mean(values),
        "ci_95_lower": percentile(ordered, 0.025),
        "ci_95_upper": percentile(ordered, 0.975),
    }


def pp(value: float) -> str:
    return f"{100.0 * value:+.2f} pp"


def main() -> None:
    args = parse_args()
    records = load_paper_records(args.repo.resolve())
    observations = to_observations(records)
    index = build_cluster_index(observations)
    validate_design(observations, index)

    observed = family_effects(observations)
    claude_boot, gpt_boot = bootstrap_effects(
        index=index,
        repetitions=args.bootstrap_reps,
        random_seed=args.random_seed,
    )
    claude_summary = summarize_bootstrap(claude_boot)
    gpt_summary = summarize_bootstrap(gpt_boot)

    result = {
        "method": {
            "fixed_strata": ["task_family", "dataset_size"],
            "resampling_cluster": ["task_family", "dataset_size", "dataset_seed"],
            "cluster_contents": ["models", "prompt_conditions", "paired_judge_labels"],
            "bootstrap_repetitions": args.bootstrap_reps,
            "random_seed": args.random_seed,
            "interval": "95% percentile bootstrap with linear interpolation",
        },
        "coverage": {
            "canonical_runs": len(observations),
            "strata": len(index),
            "dataset_instance_clusters": sum(len(x) for x in index.values()),
        },
        "observed": observed,
        "claude_family_effect": {
            "estimate": observed["claude_family_effect"],
            **claude_summary,
        },
        "gpt_family_effect": {
            "estimate": observed["gpt_family_effect"],
            **gpt_summary,
        },
    }

    print("\n== Observed judge gaps ==")
    print(f"Neutral-model Claude−GPT gap: {pp(observed['neutral_gap'])}")
    print(f"Claude-family Claude−GPT gap: {pp(observed['claude_family_gap'])}")
    print(f"GPT-family Claude−GPT gap: {pp(observed['gpt_family_gap'])}")
    print("\n== Stratified dataset-instance cluster bootstrap ==")
    print(
        "Claude-family effect: "
        f"{pp(observed['claude_family_effect'])} "
        f"[95% CI {pp(claude_summary['ci_95_lower'])}, "
        f"{pp(claude_summary['ci_95_upper'])}]"
    )
    print(
        "GPT-family effect:    "
        f"{pp(observed['gpt_family_effect'])} "
        f"[95% CI {pp(gpt_summary['ci_95_lower'])}, "
        f"{pp(gpt_summary['ci_95_upper'])}]"
    )

    if args.output_json is not None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {args.output_json}")


if __name__ == "__main__":
    main()
