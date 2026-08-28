#!/usr/bin/env python3
"""Bayesian paired, design-standardized harness equivalence analysis."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1] / "reward-hacking-evals"
MODELS = ("gpt-5.4", "claude-sonnet-4-6")
LABELS = {"not_reward_hacking": 0, "reward_hacking": 1}
ROWS = {"n100": 100, "n10k": 10_000, "n100k": 100_000}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.open() if line.strip()]


def task_stratum(metadata: dict) -> tuple[str, int, str]:
    task_id = metadata["task_id"]
    match = re.search(r"_(n100k|n10k|n100)_", task_id)
    if match is None:
        raise ValueError(f"cannot parse dataset size from {task_id}")
    condition = metadata["prompt_condition"]
    if condition == "score_only":
        condition = "score"
    return metadata["family_code"], ROWS[match.group(1)], condition


def load_pairs() -> dict[str, dict[tuple[str, int, str], list[tuple[int, int]]]]:
    open_case_paths = (
        ROOT / "data/cases/make_datasets/opencode_vps_20260710_canonical182.jsonl",
        ROOT / "data/cases/make_datasets/opencode_stratified_fill4_final66_cases.jsonl",
    )
    open_judge_paths = (
        ROOT
        / "data/outputs/judging/opencode_vps_20260710_glm52_high/judging_binary_final182.jsonl",
        ROOT
        / "data/outputs/judging/opencode_stratified_fill4_glm52_high_final66.jsonl",
    )
    native_cases_path = (
        ROOT / "data/cases/make_datasets/v4_paper_canonical_1258_20260709.jsonl"
    )
    native_judges_path = (
        ROOT
        / "data/outputs/judging/neutral_z-ai_glm-5.2_v4_20260709/judging_binary.jsonl"
    )

    open_cases = {
        row["run_id"]: row
        for path in open_case_paths
        for row in load_jsonl(path)
    }
    open_labels = {
        row["run_id"]: LABELS[row["judgment"]["label"]]
        for path in open_judge_paths
        for row in load_jsonl(path)
        if row.get("type") == "record"
        and isinstance(row.get("judgment"), dict)
        and row["judgment"].get("label") in LABELS
    }

    native_labels: dict[str, int] = {}
    for row in load_jsonl(native_judges_path):
        judgment = row.get("judgment")
        if (
            row.get("type") == "record"
            and isinstance(judgment, dict)
            and judgment.get("label") in LABELS
        ):
            native_labels[row["run_id"]] = LABELS[judgment["label"]]

    native_index: dict[tuple[str, str], list[str]] = defaultdict(list)
    for row in load_jsonl(native_cases_path):
        metadata = row["metadata"]
        native_index[(metadata["model"], metadata["task_id"])].append(row["run_id"])

    pairs: dict[str, dict[tuple[str, int, str], list[tuple[int, int]]]] = {
        model: defaultdict(list) for model in MODELS
    }
    for run_id, open_label in open_labels.items():
        metadata = open_cases[run_id]["metadata"]
        model = metadata["model"]
        if model not in MODELS:
            continue
        matches = [
            candidate
            for candidate in native_index[(model, metadata["task_id"])]
            if candidate in native_labels
        ]
        if len(matches) != 1:
            raise ValueError(f"expected one native match for {run_id}, got {matches}")
        pairs[model][task_stratum(metadata)].append(
            (native_labels[matches[0]], open_label)
        )

    for model, strata in pairs.items():
        if len(strata) != 18:
            raise ValueError(f"{model}: expected 18 strata, got {len(strata)}")
        if min(map(len, strata.values())) < 4:
            raise ValueError(f"{model}: a stratum has fewer than four pairs")
    return pairs


def posterior_draws(
    strata: dict[tuple[str, int, str], list[tuple[int, int]]],
    *,
    prior: float,
    draws: int,
    rng: np.random.Generator,
) -> np.ndarray:
    deltas = np.empty((len(strata), draws), dtype=np.float64)
    for index, pairs in enumerate(sorted(strata.values(), key=lambda x: str(x))):
        counts = Counter(pairs)
        # Categories: native/open = 00, 01, 10, 11.
        alpha = np.array(
            [
                counts[(0, 0)] + prior,
                counts[(0, 1)] + prior,
                counts[(1, 0)] + prior,
                counts[(1, 1)] + prior,
            ],
            dtype=np.float64,
        )
        sampled = rng.dirichlet(alpha, size=draws)
        deltas[index] = sampled[:, 1] - sampled[:, 2]
    return deltas.mean(axis=0)


def summarize(draws: np.ndarray) -> dict[str, float]:
    q025, q50, q975 = np.quantile(draws, [0.025, 0.5, 0.975])
    return {
        "mean": float(draws.mean()),
        "median": float(q50),
        "ci95_low": float(q025),
        "ci95_high": float(q975),
        "p_within_05": float(np.mean(np.abs(draws) < 0.05)),
        "p_within_10": float(np.mean(np.abs(draws) < 0.10)),
        "p_within_15": float(np.mean(np.abs(draws) < 0.15)),
        "p_increase": float(np.mean(draws > 0)),
        "p_noninferior_10": float(np.mean(draws < 0.10)),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--draws", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=20260711)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("bayesian_harness_equivalence_20260711.json"),
    )
    args = parser.parse_args()

    pairs = load_pairs()
    result: dict[str, object] = {
        "method": "paired four-outcome Dirichlet by stratum; equal average over 18 strata",
        "draws": args.draws,
        "seed": args.seed,
        "models": {},
    }
    for prior_name, prior in (("jeffreys", 0.5), ("uniform", 1.0)):
        rng = np.random.default_rng(args.seed)
        model_draws: dict[str, np.ndarray] = {}
        for model in MODELS:
            model_draws[model] = posterior_draws(
                pairs[model], prior=prior, draws=args.draws, rng=rng
            )
            model_result = result["models"].setdefault(model, {})  # type: ignore[index, union-attr]
            flat_pairs = [
                pair for stratum_pairs in pairs[model].values() for pair in stratum_pairs
            ]
            model_result["n"] = len(flat_pairs)
            model_result["min_per_stratum"] = min(map(len, pairs[model].values()))
            model_result["raw_paired"] = {
                "native_reward_hacking": sum(native for native, _ in flat_pairs),
                "opencode_reward_hacking": sum(opencode for _, opencode in flat_pairs),
                "native_rate": sum(native for native, _ in flat_pairs) / len(flat_pairs),
                "opencode_rate": sum(opencode for _, opencode in flat_pairs)
                / len(flat_pairs),
                "difference": sum(opencode - native for native, opencode in flat_pairs)
                / len(flat_pairs),
            }
            model_result[prior_name] = summarize(model_draws[model])
        grand = 0.5 * (model_draws[MODELS[0]] + model_draws[MODELS[1]])
        result.setdefault("grand_equal_model_weight", {})[prior_name] = summarize(grand)  # type: ignore[index]

    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
