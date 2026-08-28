#!/usr/bin/env python3
"""Aggregate judge JSONL outputs across multiple backends/models.

Given N JSONL files produced by ``judge_binary.py`` or ``judge_classification_h_deprecated.py``
(each from a different backend/model), join on ``run_id`` and compute:

- per-run majority label (binary) or majority primary_mechanism_family (classification)
- per-run agreement score (fraction of raters agreeing with majority)
- per-run probability mean / std / min / max
- per-run conflict flag (any disagreement OR any "unclear")
- for classification: Jaccard similarity over the union of listed mechanism_family values
- corpus-level percent agreement, Cohen's kappa (pairwise) and Fleiss' kappa (>=3 raters)

Usage
-----
    python aggregate_judgments.py \
        --task binary \
        --rater openai=out/binary_gpt.jsonl \
        --rater anthropic=out/binary_claude.jsonl \
        --rater openrouter=out/binary_kimi.jsonl \
        --output-tsv out/binary_aggregated.tsv \
        --summary-json out/binary_summary.json

For classification, replace ``--task binary`` with ``--task classification``.
The rater-name prefix is free-form; it is used as a column suffix in the TSV.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from common import load_judge_records  # type: ignore  # noqa: E402

# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def parse_rater_specs(specs: list[str]) -> list[tuple[str, Path]]:
    raters: list[tuple[str, Path]] = []
    for spec in specs:
        if "=" not in spec:
            raise ValueError(f"--rater spec must be NAME=PATH, got {spec!r}")
        name, path = spec.split("=", 1)
        name = name.strip()
        path = path.strip()
        if not name or not path:
            raise ValueError(f"Invalid --rater spec: {spec!r}")
        raters.append((name, Path(path)))
    return raters


# ---------------------------------------------------------------------------
# Extracting per-rater fields per task
# ---------------------------------------------------------------------------


def extract_binary(record: dict[str, Any]) -> dict[str, Any] | None:
    judgment = record.get("judgment")
    if not isinstance(judgment, dict):
        return None
    label = judgment.get("label")
    if not isinstance(label, str):
        return None
    prob = judgment.get("probability")
    return {
        "label": label,
        "probability": float(prob) if isinstance(prob, (int, float)) else None,
    }


def extract_classification(record: dict[str, Any]) -> dict[str, Any] | None:
    judgment = record.get("judgment")
    if not isinstance(judgment, dict):
        return None
    primary = judgment.get("primary_mechanism_family")
    mechanisms = judgment.get("mechanisms") or []
    if not isinstance(primary, str):
        # Fall back to mechanisms[0]
        if isinstance(mechanisms, list) and mechanisms:
            first = mechanisms[0]
            if isinstance(first, dict):
                primary = first.get("mechanism_family")
    if not isinstance(primary, str):
        return None

    families: list[str] = []
    probs: list[float] = []
    for item in mechanisms if isinstance(mechanisms, list) else []:
        if not isinstance(item, dict):
            continue
        fam = item.get("mechanism_family")
        if isinstance(fam, str):
            families.append(fam)
        p = item.get("probability")
        if isinstance(p, (int, float)):
            probs.append(float(p))

    primary_prob = probs[0] if probs else None
    return {
        "label": primary,            # primary mechanism_family used for kappa
        "probability": primary_prob, # probability of primary mechanism
        "mechanism_set": set(families),
    }


# ---------------------------------------------------------------------------
# Agreement metrics
# ---------------------------------------------------------------------------


def jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 1.0
    return len(a & b) / len(union)


def cohens_kappa(rater_a: list[str], rater_b: list[str]) -> float | None:
    """Cohen's kappa between two equal-length lists of categorical labels."""
    if len(rater_a) != len(rater_b) or not rater_a:
        return None
    n = len(rater_a)
    categories = sorted(set(rater_a) | set(rater_b))
    if len(categories) < 2:
        # All raters all-agree on the same single label across all items
        return 1.0
    agree = sum(1 for x, y in zip(rater_a, rater_b) if x == y)
    p_o = agree / n
    count_a = Counter(rater_a)
    count_b = Counter(rater_b)
    p_e = sum((count_a[c] / n) * (count_b[c] / n) for c in categories)
    if p_e >= 1.0:
        return 1.0 if p_o >= 1.0 else 0.0
    return (p_o - p_e) / (1.0 - p_e)


def fleiss_kappa(label_matrix: list[list[str]]) -> float | None:
    """Fleiss' kappa for k>=3 raters.

    label_matrix: list of N items, each a list of K labels (one per rater),
    same K for every item.
    """
    if not label_matrix:
        return None
    K = len(label_matrix[0])
    if K < 2 or any(len(row) != K for row in label_matrix):
        return None
    N = len(label_matrix)
    categories = sorted({label for row in label_matrix for label in row})
    if len(categories) < 2:
        return 1.0
    cat_index = {c: i for i, c in enumerate(categories)}
    # n[i][j] = number of raters that assigned item i to category j
    n = [[0] * len(categories) for _ in range(N)]
    for i, row in enumerate(label_matrix):
        for label in row:
            n[i][cat_index[label]] += 1
    # P_i: extent of agreement for item i
    if K < 2:
        return None
    P_items = [
        (sum(c * c for c in n[i]) - K) / (K * (K - 1))
        for i in range(N)
    ]
    P_bar = sum(P_items) / N
    # p_j: proportion of all assignments to category j
    total_assignments = N * K
    p_cat = [sum(n[i][j] for i in range(N)) / total_assignments for j in range(len(categories))]
    P_e = sum(p * p for p in p_cat)
    if P_e >= 1.0:
        return 1.0 if P_bar >= 1.0 else 0.0
    return (P_bar - P_e) / (1.0 - P_e)


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def aggregate(
    *,
    task: str,
    rater_records: dict[str, dict[str, dict[str, Any]]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Returns (per_run_rows, summary_stats)."""
    extractor = extract_binary if task == "binary" else extract_classification

    rater_names = list(rater_records.keys())
    # Intersect run_ids: only runs every rater scored
    common_ids: set[str] | None = None
    for name in rater_names:
        ids = set(rater_records[name].keys())
        common_ids = ids if common_ids is None else common_ids & ids
    common_ids = sorted(common_ids or set())

    per_run_rows: list[dict[str, Any]] = []
    label_columns: dict[str, list[str]] = defaultdict(list)  # rater -> labels in run-order

    for run_id in common_ids:
        per_rater_extracted: dict[str, dict[str, Any]] = {}
        skip = False
        for name in rater_names:
            ext = extractor(rater_records[name][run_id])
            if ext is None:
                skip = True
                break
            per_rater_extracted[name] = ext
        if skip:
            continue

        labels = [per_rater_extracted[name]["label"] for name in rater_names]
        for name, label in zip(rater_names, labels):
            label_columns[name].append(label)

        # Majority label
        counts = Counter(labels)
        top_count = max(counts.values())
        majority_candidates = sorted(c for c, cnt in counts.items() if cnt == top_count)
        majority = majority_candidates[0] if len(majority_candidates) == 1 else "TIE"
        agreement_score = top_count / len(labels)
        all_agree = top_count == len(labels)
        any_unclear = any(
            label.lower() == "unclear" or label.lower().startswith("h-unclear")
            for label in labels
        )
        conflict_flag = (not all_agree) or any_unclear

        # Probabilities
        probs = [
            per_rater_extracted[name]["probability"]
            for name in rater_names
            if per_rater_extracted[name]["probability"] is not None
        ]
        prob_mean = statistics.fmean(probs) if probs else None
        prob_std = statistics.pstdev(probs) if len(probs) >= 2 else (0.0 if probs else None)
        prob_min = min(probs) if probs else None
        prob_max = max(probs) if probs else None

        row: dict[str, Any] = {
            "run_id": run_id,
            "majority_label": majority,
            "agreement_score": round(agreement_score, 4),
            "all_agree": all_agree,
            "any_unclear": any_unclear,
            "conflict_flag": conflict_flag,
            "probability_mean": round(prob_mean, 4) if prob_mean is not None else None,
            "probability_std": round(prob_std, 4) if prob_std is not None else None,
            "probability_min": round(prob_min, 4) if prob_min is not None else None,
            "probability_max": round(prob_max, 4) if prob_max is not None else None,
        }
        for name in rater_names:
            row[f"label__{name}"] = per_rater_extracted[name]["label"]
            p = per_rater_extracted[name]["probability"]
            row[f"probability__{name}"] = round(p, 4) if p is not None else None

        # Classification: pairwise Jaccard over mechanism_set
        if task == "classification":
            sets = [per_rater_extracted[name]["mechanism_set"] for name in rater_names]
            jacs = [jaccard(a, b) for a, b in combinations(sets, 2)]
            row["mechanism_jaccard_mean"] = round(statistics.fmean(jacs), 4) if jacs else None
            row["mechanism_union"] = sorted({fam for s in sets for fam in s})

        per_run_rows.append(row)

    # Corpus-level summary
    summary: dict[str, Any] = {
        "task": task,
        "raters": rater_names,
        "n_runs_total_per_rater": {n: len(rater_records[n]) for n in rater_names},
        "n_runs_intersected": len(common_ids),
        "n_runs_aggregated": len(per_run_rows),
    }
    if per_run_rows:
        summary["pct_all_agree"] = round(
            sum(1 for r in per_run_rows if r["all_agree"]) / len(per_run_rows), 4
        )
        summary["pct_any_unclear"] = round(
            sum(1 for r in per_run_rows if r["any_unclear"]) / len(per_run_rows), 4
        )
        summary["pct_conflict"] = round(
            sum(1 for r in per_run_rows if r["conflict_flag"]) / len(per_run_rows), 4
        )
        # Cohen's kappa pairwise
        kappas: dict[str, float] = {}
        for a, b in combinations(rater_names, 2):
            kap = cohens_kappa(label_columns[a], label_columns[b])
            if kap is not None:
                kappas[f"{a}__vs__{b}"] = round(kap, 4)
        summary["cohens_kappa_pairwise"] = kappas
        if kappas:
            summary["cohens_kappa_mean"] = round(
                sum(kappas.values()) / len(kappas), 4
            )
        # Fleiss' kappa if >= 3 raters
        if len(rater_names) >= 3:
            matrix = [[label_columns[n][i] for n in rater_names] for i in range(len(per_run_rows))]
            fk = fleiss_kappa(matrix)
            if fk is not None:
                summary["fleiss_kappa"] = round(fk, 4)
        # Probability ensemble distribution
        means = [r["probability_mean"] for r in per_run_rows if r["probability_mean"] is not None]
        if means:
            summary["probability_mean_overall"] = round(statistics.fmean(means), 4)
            summary["probability_mean_std_across_runs"] = round(
                statistics.pstdev(means) if len(means) >= 2 else 0.0, 4
            )
        if task == "classification":
            jacs = [r["mechanism_jaccard_mean"] for r in per_run_rows if r["mechanism_jaccard_mean"] is not None]
            if jacs:
                summary["mechanism_jaccard_mean_overall"] = round(statistics.fmean(jacs), 4)

    return per_run_rows, summary


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_tsv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    # Stable column order: run_id first, then aggregates, then per-rater
    fixed = [
        "run_id",
        "majority_label",
        "agreement_score",
        "all_agree",
        "any_unclear",
        "conflict_flag",
        "probability_mean",
        "probability_std",
        "probability_min",
        "probability_max",
        "mechanism_jaccard_mean",
        "mechanism_union",
    ]
    seen = set(fixed)
    extra = [k for r in rows for k in r.keys() if k not in seen and not (seen.add(k))]
    headers = [h for h in fixed if any(h in r for r in rows)] + extra
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\t".join(headers) + "\n")
        for row in rows:
            handle.write("\t".join(_tsv_cell(row.get(h)) for h in headers) + "\n")


def _tsv_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    s = str(value)
    return s.replace("\t", " ").replace("\n", " ")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Aggregate judge JSONL outputs across multiple raters and compute inter-rater agreement.",
    )
    parser.add_argument(
        "--task",
        required=True,
        choices=["binary", "classification"],
        help="Which judge schema the inputs use.",
    )
    parser.add_argument(
        "--rater",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help="Repeatable. NAME is a free-form rater label (e.g. openai, claude, kimi); PATH is a judge JSONL file.",
    )
    parser.add_argument("--output-tsv", help="Optional per-run aggregated TSV output.")
    parser.add_argument("--output-jsonl", help="Optional per-run aggregated JSONL output.")
    parser.add_argument("--summary-json", help="Optional corpus summary JSON output.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    rater_specs = parse_rater_specs(args.rater)
    if len(rater_specs) < 2:
        print("Need at least 2 raters to compute agreement.", file=sys.stderr)
        return 2

    rater_records: dict[str, dict[str, dict[str, Any]]] = {}
    for name, path in rater_specs:
        if not path.exists():
            print(f"Missing rater file: {path}", file=sys.stderr)
            return 2
        rater_records[name] = load_judge_records(path)

    rows, summary = aggregate(task=args.task, rater_records=rater_records)

    if args.output_tsv:
        write_tsv(Path(args.output_tsv), rows)
    if args.output_jsonl:
        with Path(args.output_jsonl).open("w", encoding="utf-8") as handle:
            for row in rows:
                serializable = {
                    k: (sorted(v) if isinstance(v, set) else v) for k, v in row.items()
                }
                handle.write(json.dumps(serializable) + "\n")
    if args.summary_json:
        Path(args.summary_json).write_text(
            json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
        )

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
