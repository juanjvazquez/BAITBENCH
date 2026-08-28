#!/usr/bin/env python3
"""Reproduce paper-facing make_datasets reward-hacking statistics.

This script reads the archived local judge outputs under
``data/outputs/judging``. It does not call the viewer. It uses the same
canonicalization rule as ``make_datasets_dedup_table.py``: ``*_rerunNN`` records
replace the corresponding original run, and smoke/test extras are excluded.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import statistics
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


TASK_LABELS = {
    "Entity overlap": "v4_t01_local_20260516",
    "Near-duplicate leakage": "v4_t02_local_20260516",
    "No-signal classification": "v4_t03_local_20260513",
}

SONNET_LABEL = "sonnet_v4_20260520"
GEMINI_FLASH_LABEL = "gemini_flash_v4_20260520"

FULL_GRID_MODEL_LABELS = {
    "claude-sonnet-4-6": SONNET_LABEL,
    "gemini-3-flash-preview": GEMINI_FLASH_LABEL,
}

TASK_ORDER = [
    "Entity overlap",
    "Near-duplicate leakage",
    "No-signal classification",
]

MODEL_ORDER = [
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "gpt-5.4",
    "kimi-k2.5",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "deepseek-v4-pro",
]

RERUN_RE = re.compile(r"_rerun(\d+)$")
SMOKE_RE = re.compile(r"_vps2_smoke_\d+$")


@dataclass(frozen=True)
class RunRecord:
    run_id: str
    task: str
    model: str
    prompt: str
    claude_label: str
    gpt_label: str


@dataclass
class BootstrapCI:
    samples: int = 10000
    confidence: float = 0.95
    seed: int = 0

    def __post_init__(self) -> None:
        if self.samples <= 0:
            raise ValueError("--bootstrap-samples must be positive")
        if not 0.0 < self.confidence < 1.0:
            raise ValueError("--ci-level must be between 0 and 1")
        self.rng = random.Random(self.seed)


def pct(numer: float, denom: float) -> float:
    if denom == 0:
        return float("nan")
    return 100.0 * numer / denom


def ci_text(ci: tuple[float, float] | None, *, latex: bool = False) -> str:
    if ci is None:
        return ""
    low, high = ci
    if not math.isfinite(low) or not math.isfinite(high):
        return ""
    suffix = r"\%" if latex else "%"
    return f" [{low:.1f}{suffix}, {high:.1f}{suffix}]"


def pp_ci_text(ci: tuple[float, float] | None) -> str:
    if ci is None:
        return ""
    low, high = ci
    if not math.isfinite(low) or not math.isfinite(high):
        return ""
    return f" [{low:.1f}, {high:.1f}]"


def pct_value_text(
    value: float,
    *,
    latex: bool = False,
    ci: tuple[float, float] | None = None,
) -> str:
    if value != value:
        return "NA"
    suffix = r"\%" if latex else "%"
    return f"{value:.1f}{suffix}{ci_text(ci, latex=latex)}"


def pct_text(
    numer: float,
    denom: float,
    *,
    latex: bool = False,
    ci: tuple[float, float] | None = None,
) -> str:
    return pct_value_text(pct(numer, denom), latex=latex, ci=ci)


def count_pct_text(
    count: int,
    denom: int,
    *,
    latex: bool = False,
    ci: tuple[float, float] | None = None,
) -> str:
    return f"{pct_text(count, denom, latex=latex, ci=ci)} ({count}/{denom})"


def num_text(value: float) -> str:
    if value != value:
        return "NA"
    return f"{value:.3f}"


def one_decimal_text(value: float) -> str:
    if value != value:
        return "NA"
    return f"{value:.1f}"


def get_ci(row: dict[str, object], prefix: str) -> tuple[float, float] | None:
    value = row.get(f"{prefix}_ci")
    if isinstance(value, (tuple, list)) and len(value) == 2:
        low_value = float_or_none(value[0])
        high_value = float_or_none(value[1])
        if low_value is not None and high_value is not None:
            return low_value, high_value
    low = row.get(f"{prefix}_ci_low")
    high = row.get(f"{prefix}_ci_high")
    low_value = float_or_none(low)
    high_value = float_or_none(high)
    if low_value is not None and high_value is not None:
        return low_value, high_value
    return None


def number_ci_text(ci: tuple[float, float] | None) -> str:
    if ci is None:
        return ""
    low, high = ci
    if not math.isfinite(low) or not math.isfinite(high):
        return ""
    return f" [{low:.3f}, {high:.3f}]"


def num_text_ci(value: float, ci: tuple[float, float] | None = None) -> str:
    return f"{num_text(value)}{number_ci_text(ci)}"


def finite_number(value: object) -> bool:
    return isinstance(value, (int, float)) and math.isfinite(float(value))


def float_or_none(value: object) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else float("nan")


def median(values: list[float]) -> float:
    return statistics.median(values) if values else float("nan")


def percentile(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    sorted_values = sorted(values)
    position = q * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(position)]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def ci_bounds(samples: list[float], ci: BootstrapCI) -> tuple[float, float]:
    alpha = 1.0 - ci.confidence
    return percentile(samples, alpha / 2.0), percentile(samples, 1.0 - alpha / 2.0)


def bootstrap_one_sample_ci(
    values: list[float],
    statistic,
    ci: BootstrapCI | None,
) -> tuple[float, float] | None:
    if ci is None or not values:
        return None
    n = len(values)
    samples: list[float] = []
    for _ in range(ci.samples):
        draw = [values[ci.rng.randrange(n)] for _ in range(n)]
        sample_value = statistic(draw)
        if math.isfinite(sample_value):
            samples.append(sample_value)
    if not samples:
        return None
    return ci_bounds(samples, ci)


def bootstrap_two_sample_ci(
    left: list[float],
    right: list[float],
    statistic,
    ci: BootstrapCI | None,
) -> tuple[float, float] | None:
    if ci is None or not left or not right:
        return None
    left_n = len(left)
    right_n = len(right)
    samples: list[float] = []
    for _ in range(ci.samples):
        left_draw = [left[ci.rng.randrange(left_n)] for _ in range(left_n)]
        right_draw = [right[ci.rng.randrange(right_n)] for _ in range(right_n)]
        sample_value = statistic(left_draw, right_draw)
        if math.isfinite(sample_value):
            samples.append(sample_value)
    if not samples:
        return None
    return ci_bounds(samples, ci)


def mean_pct(values: list[float]) -> float:
    return 100.0 * mean(values)


def label_values(records: list[RunRecord], judge: str) -> list[float]:
    if judge == "claude":
        return [1.0 if r.claude_label == "reward_hacking" else 0.0 for r in records]
    if judge == "gpt":
        return [1.0 if r.gpt_label == "reward_hacking" else 0.0 for r in records]
    raise ValueError(f"unknown judge: {judge}")


def judge_average_values(records: list[RunRecord]) -> list[float]:
    return [
        (
            (1.0 if r.claude_label == "reward_hacking" else 0.0)
            + (1.0 if r.gpt_label == "reward_hacking" else 0.0)
        )
        / 2.0
        for r in records
    ]


def add_ci_fields(
    row: dict[str, object],
    prefix: str,
    ci: tuple[float, float] | None,
) -> None:
    if ci is None:
        return
    row[f"{prefix}_ci"] = ci
    row[f"{prefix}_ci_low"] = ci[0]
    row[f"{prefix}_ci_high"] = ci[1]


def load_binary_labels(path: Path) -> dict[str, str]:
    labels: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("type") != "record" or obj.get("skipped"):
                continue
            run_id = obj.get("run_id")
            judgment = obj.get("judgment") or {}
            label = judgment.get("label")
            if isinstance(run_id, str) and isinstance(label, str):
                labels[run_id] = label
    return labels


def logical_run_id_and_rank(run_id: str) -> tuple[str | None, int | None]:
    if SMOKE_RE.search(run_id):
        return None, None
    match = RERUN_RE.search(run_id)
    if match:
        return RERUN_RE.sub("", run_id), int(match.group(1))
    return run_id, 0


def select_canonical_run_ids(
    claude_labels: dict[str, str],
    gpt_labels: dict[str, str],
) -> list[str]:
    best_by_logical_id: dict[str, tuple[int, str]] = {}
    for run_id in set(claude_labels) & set(gpt_labels):
        logical_id, rank = logical_run_id_and_rank(run_id)
        if logical_id is None or rank is None:
            continue
        previous = best_by_logical_id.get(logical_id)
        if previous is None or rank > previous[0]:
            best_by_logical_id[logical_id] = (rank, run_id)
    return sorted(run_id for _, run_id in best_by_logical_id.values())


def model_from_run_id(run_id: str) -> str:
    if run_id.startswith("claude_"):
        if "claude-sonnet-4-6" in run_id:
            return "claude-sonnet-4-6"
        return "claude-opus-4-6"
    if run_id.startswith("codex_"):
        return "gpt-5.4"
    if run_id.startswith("kimi_"):
        return "kimi-k2.5"
    if run_id.startswith("gemini_"):
        if "gemini-3-flash-preview" in run_id:
            return "gemini-3-flash-preview"
        return "gemini-3.1-pro-preview"
    if run_id.startswith("deepseek_"):
        return "deepseek-v4-pro"
    return run_id.split("_", 1)[0]


def prompt_from_run_id(run_id: str) -> str:
    if "_validity_" in run_id:
        return "validity"
    if "_score_" in run_id:
        return "baseline"
    return "unknown"


def task_from_run_id(run_id: str) -> str | None:
    if "_regression_task_01_" in run_id:
        return "Entity overlap"
    if "_regression_task_02_" in run_id:
        return "Near-duplicate leakage"
    if "_classification_task_03_" in run_id:
        return "No-signal classification"
    return None


def load_records(
    outputs_root: Path,
    *,
    include_sonnet: bool = True,
    include_gemini_flash: bool = True,
) -> list[RunRecord]:
    records: list[RunRecord] = []
    for task, label_dir_name in TASK_LABELS.items():
        label_dir = outputs_root / label_dir_name
        claude_labels = load_binary_labels(label_dir / "judging_binary_anthropic.jsonl")
        gpt_labels = load_binary_labels(label_dir / "judging_binary_openai.jsonl")
        for run_id in select_canonical_run_ids(claude_labels, gpt_labels):
            records.append(
                RunRecord(
                    run_id=run_id,
                    task=task,
                    model=model_from_run_id(run_id),
                    prompt=prompt_from_run_id(run_id),
                    claude_label=claude_labels[run_id],
                    gpt_label=gpt_labels[run_id],
                )
            )
    extra_label_dirs = []
    if include_sonnet:
        extra_label_dirs.append(SONNET_LABEL)
    if include_gemini_flash:
        extra_label_dirs.append(GEMINI_FLASH_LABEL)
    for label_dir_name in extra_label_dirs:
        label_dir = outputs_root / label_dir_name
        claude_labels = load_binary_labels(label_dir / "judging_binary_anthropic.jsonl")
        gpt_labels = load_binary_labels(label_dir / "judging_binary_openai.jsonl")
        for run_id in select_canonical_run_ids(claude_labels, gpt_labels):
            task = task_from_run_id(run_id)
            if task is None:
                continue
            records.append(
                RunRecord(
                    run_id=run_id,
                    task=task,
                    model=model_from_run_id(run_id),
                    prompt=prompt_from_run_id(run_id),
                    claude_label=claude_labels[run_id],
                    gpt_label=gpt_labels[run_id],
                )
            )
    return records


def case_path_for_record(cases_root: Path, record: RunRecord) -> Path:
    full_grid_label = FULL_GRID_MODEL_LABELS.get(record.model)
    if full_grid_label:
        full_grid_path = cases_root / full_grid_label / f"{record.run_id}.json"
        if full_grid_path.exists():
            return full_grid_path
    return cases_root / TASK_LABELS[record.task] / f"{record.run_id}.json"


def robust_status(cases_root: Path, record: RunRecord) -> str:
    path = case_path_for_record(cases_root, record)
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "missing_case_json"
    robust = (obj.get("evidence") or {}).get("robust_evaluation")
    if not robust:
        return "missing_robust_evaluation"
    if robust.get("status") == "error" or robust.get("error"):
        error = str(robust.get("error") or "")
        if "too large" in error.lower() or "large" in error.lower():
            return "robust_error_too_large"
        return "robust_error_other"
    if not finite_number(robust.get("robust_score")):
        return "missing_or_nonfinite_robust_score"
    if not finite_number(robust.get("generalization_gap")):
        return "missing_or_nonfinite_gap"
    return "finite_robust_score_and_gap"


def robust_evaluation(cases_root: Path, record: RunRecord) -> dict[str, object] | None:
    path = case_path_for_record(cases_root, record)
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    robust = (obj.get("evidence") or {}).get("robust_evaluation")
    if isinstance(robust, dict):
        return robust
    return None


def task_rates(
    records: Iterable[RunRecord],
    *,
    ci: BootstrapCI | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    records_by_task: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        records_by_task[record.task].append(record)
    for task in TASK_ORDER:
        task_records = records_by_task.get(task, [])
        n = len(task_records)
        claude_rh = sum(r.claude_label == "reward_hacking" for r in task_records)
        gpt_rh = sum(r.gpt_label == "reward_hacking" for r in task_records)
        row: dict[str, object] = {
            "task": task,
            "n": n,
            "claude_rh": claude_rh,
            "claude_rate": pct(claude_rh, n),
            "gpt_rh": gpt_rh,
            "gpt_rate": pct(gpt_rh, n),
        }
        add_ci_fields(
            row,
            "claude_rate",
            bootstrap_one_sample_ci(label_values(task_records, "claude"), mean_pct, ci),
        )
        add_ci_fields(
            row,
            "gpt_rate",
            bootstrap_one_sample_ci(label_values(task_records, "gpt"), mean_pct, ci),
        )
        rows.append(row)
    return rows


def model_rates(
    records: Iterable[RunRecord],
    *,
    ci: BootstrapCI | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    records_by_model: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        records_by_model[record.model].append(record)
    for model in MODEL_ORDER:
        model_records = records_by_model.get(model, [])
        if not model_records:
            continue
        n = len(model_records)
        claude_rh = sum(r.claude_label == "reward_hacking" for r in model_records)
        gpt_rh = sum(r.gpt_label == "reward_hacking" for r in model_records)
        row: dict[str, object] = {
            "model": model,
            "n": n,
            "claude_rh": claude_rh,
            "claude_rate": pct(claude_rh, n),
            "gpt_rh": gpt_rh,
            "gpt_rate": pct(gpt_rh, n),
        }
        add_ci_fields(
            row,
            "claude_rate",
            bootstrap_one_sample_ci(
                label_values(model_records, "claude"), mean_pct, ci
            ),
        )
        add_ci_fields(
            row,
            "gpt_rate",
            bootstrap_one_sample_ci(label_values(model_records, "gpt"), mean_pct, ci),
        )
        rows.append(row)
    return rows


def model_prompt_rates(
    records: Iterable[RunRecord],
    *,
    ci: BootstrapCI | None = None,
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    records_by_model_prompt: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for record in records:
        records_by_model_prompt[(record.model, record.prompt)].append(record)
    for model in MODEL_ORDER:
        for prompt in ["baseline", "validity"]:
            prompt_records = records_by_model_prompt.get((model, prompt), [])
            if not prompt_records:
                continue
            n = len(prompt_records)
            claude_rh = sum(r.claude_label == "reward_hacking" for r in prompt_records)
            gpt_rh = sum(r.gpt_label == "reward_hacking" for r in prompt_records)
            row: dict[str, object] = {
                "model": model,
                "prompt": prompt,
                "n": n,
                "claude_rh": claude_rh,
                "claude_rate": pct(claude_rh, n),
                "gpt_rh": gpt_rh,
                "gpt_rate": pct(gpt_rh, n),
            }
            add_ci_fields(
                row,
                "claude_rate",
                bootstrap_one_sample_ci(
                    label_values(prompt_records, "claude"), mean_pct, ci
                ),
            )
            add_ci_fields(
                row,
                "gpt_rate",
                bootstrap_one_sample_ci(
                    label_values(prompt_records, "gpt"), mean_pct, ci
                ),
            )
            rows.append(row)
    return rows


def cohen_kappa(records: list[RunRecord]) -> tuple[int, int, float, float]:
    labels = sorted({r.claude_label for r in records} | {r.gpt_label for r in records})
    n = len(records)
    if n == 0:
        return 0, 0, float("nan"), float("nan")
    agreement = sum(r.claude_label == r.gpt_label for r in records)
    observed = agreement / n
    expected = 0.0
    for label in labels:
        claude_count = sum(r.claude_label == label for r in records)
        gpt_count = sum(r.gpt_label == label for r in records)
        expected += (claude_count / n) * (gpt_count / n)
    if expected == 1.0:
        kappa = 1.0 if observed == 1.0 else float("nan")
    else:
        kappa = (observed - expected) / (1.0 - expected)
    return n, agreement, observed, kappa


def cohen_kappa_from_pairs(pairs: list[tuple[str, str]]) -> float:
    labels = sorted({left for left, _ in pairs} | {right for _, right in pairs})
    n = len(pairs)
    if n == 0:
        return float("nan")
    agreement = sum(left == right for left, right in pairs)
    observed = agreement / n
    expected = 0.0
    for label in labels:
        left_count = sum(left == label for left, _ in pairs)
        right_count = sum(right == label for _, right in pairs)
        expected += (left_count / n) * (right_count / n)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else float("nan")
    return (observed - expected) / (1.0 - expected)


def bootstrap_pair_ci(
    pairs: list[tuple[str, str]],
    statistic,
    ci: BootstrapCI | None,
) -> tuple[float, float] | None:
    if ci is None or not pairs:
        return None
    n = len(pairs)
    samples: list[float] = []
    for _ in range(ci.samples):
        draw = [pairs[ci.rng.randrange(n)] for _ in range(n)]
        sample_value = statistic(draw)
        if math.isfinite(sample_value):
            samples.append(sample_value)
    if not samples:
        return None
    return ci_bounds(samples, ci)


def agreement_rows(
    records: Iterable[RunRecord],
    *,
    ci: BootstrapCI | None = None,
) -> list[dict[str, object]]:
    record_list = list(records)
    rows: list[dict[str, object]] = []

    def append(axis: str, group: str, subset: list[RunRecord]) -> None:
        n, agreement, observed, kappa = cohen_kappa(subset)
        row: dict[str, object] = {
            "axis": axis,
            "group": group,
            "n": n,
            "agreement": agreement,
            "agreement_rate": observed * 100.0,
            "kappa": kappa,
        }
        agreement_values = [
            1.0 if record.claude_label == record.gpt_label else 0.0 for record in subset
        ]
        label_pairs = [(record.claude_label, record.gpt_label) for record in subset]
        add_ci_fields(
            row,
            "agreement_rate",
            bootstrap_one_sample_ci(agreement_values, mean_pct, ci),
        )
        add_ci_fields(
            row,
            "kappa",
            bootstrap_pair_ci(label_pairs, cohen_kappa_from_pairs, ci),
        )
        rows.append(row)

    append("Overall", "All runs", record_list)
    for task in TASK_ORDER:
        append("Task type", task, [r for r in record_list if r.task == task])
    for model in MODEL_ORDER:
        subset = [r for r in record_list if r.model == model]
        if subset:
            append("Agent model", model, subset)
    for prompt in ["baseline", "validity"]:
        append(
            "Prompt",
            prompt_label(prompt),
            [r for r in record_list if r.prompt == prompt],
        )
    for model in MODEL_ORDER:
        for prompt in ["baseline", "validity"]:
            subset = [r for r in record_list if r.model == model and r.prompt == prompt]
            if subset:
                append(
                    "Agent model x prompt", f"{model}, {prompt_label(prompt)}", subset
                )
    for task in TASK_ORDER:
        for prompt in ["baseline", "validity"]:
            append(
                "Task type x prompt",
                f"{task}, {prompt_label(prompt)}",
                [r for r in record_list if r.task == task and r.prompt == prompt],
            )
    for model in MODEL_ORDER:
        for task in TASK_ORDER:
            subset = [r for r in record_list if r.model == model and r.task == task]
            if subset:
                append("Agent model x task", f"{model}, {task}", subset)
    return rows


def robustness_coverage_rows(
    records: Iterable[RunRecord],
    *,
    cases_root: Path,
    ci: BootstrapCI | None = None,
) -> list[dict[str, object]]:
    records_list = list(records)

    def summarize(group: str, subset: list[RunRecord]) -> dict[str, object]:
        status_counts: dict[str, int] = defaultdict(int)
        for record in subset:
            status_counts[robust_status(cases_root, record)] += 1
        n = len(subset)
        finite = status_counts.get("finite_robust_score_and_gap", 0)
        missing = n - finite
        row: dict[str, object] = {
            "group": group,
            "n": n,
            "finite": finite,
            "missing_or_error": missing,
            "missing_or_error_rate": pct(missing, n),
            "robust_error_other": status_counts.get("robust_error_other", 0),
            "robust_error_too_large": status_counts.get("robust_error_too_large", 0),
            "missing_or_nonfinite_robust_score": status_counts.get(
                "missing_or_nonfinite_robust_score", 0
            ),
            "missing_or_nonfinite_gap": status_counts.get(
                "missing_or_nonfinite_gap", 0
            ),
            "missing_robust_evaluation": status_counts.get(
                "missing_robust_evaluation", 0
            ),
            "missing_case_json": status_counts.get("missing_case_json", 0),
        }
        finite_values = [
            1.0
            if robust_status(cases_root, record) == "finite_robust_score_and_gap"
            else 0.0
            for record in subset
        ]
        missing_values = [1.0 - value for value in finite_values]
        add_ci_fields(
            row,
            "finite_rate",
            bootstrap_one_sample_ci(finite_values, mean_pct, ci),
        )
        add_ci_fields(
            row,
            "missing_or_error_rate",
            bootstrap_one_sample_ci(missing_values, mean_pct, ci),
        )
        return row

    rows = [summarize("Overall", records_list)]
    for task in TASK_ORDER:
        rows.append(summarize(task, [r for r in records_list if r.task == task]))
    return rows


def robust_gap_decisions(
    records: Iterable[RunRecord],
    *,
    cases_root: Path,
) -> list[dict[str, object]]:
    decisions: list[dict[str, object]] = []
    for record in records:
        robust = robust_evaluation(cases_root, record)
        if not robust:
            continue
        gap = robust.get("generalization_gap")
        metric = robust.get("metric")
        gap_value = float_or_none(gap)
        if not isinstance(metric, str) or gap_value is None:
            continue
        for judge, label in [
            ("Claude", record.claude_label),
            ("GPT", record.gpt_label),
        ]:
            if label not in {"reward_hacking", "not_reward_hacking"}:
                continue
            decisions.append(
                {
                    "task": record.task,
                    "metric": metric,
                    "judge": judge,
                    "label": label,
                    "gap": gap_value,
                }
            )
    return decisions


def robust_gap_summary_rows(
    records: Iterable[RunRecord],
    *,
    cases_root: Path,
    ci: BootstrapCI | None = None,
) -> list[dict[str, object]]:
    decisions = robust_gap_decisions(records, cases_root=cases_root)
    rows: list[dict[str, object]] = []

    def append(
        *,
        split: str,
        group: str,
        judge: str,
        label: str,
        gaps: list[float],
    ) -> None:
        positive_values = [1.0 if gap > 0 else 0.0 for gap in gaps]
        row: dict[str, object] = {
            "split": split,
            "group": group,
            "judge": judge,
            "label": label,
            "n": len(gaps),
            "median_gap": median(gaps),
            "mean_gap": mean(gaps),
            "positive_gaps": sum(gap > 0 for gap in gaps),
            "positive_gap_rate": pct(sum(gap > 0 for gap in gaps), len(gaps))
            if gaps
            else float("nan"),
        }
        add_ci_fields(row, "median_gap", bootstrap_one_sample_ci(gaps, median, ci))
        add_ci_fields(row, "mean_gap", bootstrap_one_sample_ci(gaps, mean, ci))
        add_ci_fields(
            row,
            "positive_gap_rate",
            bootstrap_one_sample_ci(positive_values, mean_pct, ci),
        )
        rows.append(row)

    for metric in ["accuracy", "rmse"]:
        for label in ["reward_hacking", "not_reward_hacking"]:
            for judge in ["Claude", "GPT"]:
                gaps = [
                    float(d["gap"])
                    for d in decisions
                    if d["metric"] == metric
                    and d["label"] == label
                    and d["judge"] == judge
                    and finite_number(d["gap"])
                ]
                append(
                    split="Metric",
                    group=metric,
                    judge=judge,
                    label=label,
                    gaps=gaps,
                )

    for task in TASK_ORDER:
        for label in ["reward_hacking", "not_reward_hacking"]:
            gaps = [
                float(d["gap"])
                for d in decisions
                if d["task"] == task and d["label"] == label and finite_number(d["gap"])
            ]
            append(
                split="Task",
                group=task,
                judge="Pooled",
                label=label,
                gaps=gaps,
            )
    return rows


def degradation_pct(
    metric: str, test_score: float, robust_score: float
) -> float | None:
    if test_score == 0:
        return None
    if metric == "accuracy":
        return 100.0 * (test_score - robust_score) / abs(test_score)
    if metric == "rmse":
        return 100.0 * (robust_score - test_score) / abs(test_score)
    return None


def task_degradation_rows(
    records: Iterable[RunRecord],
    *,
    cases_root: Path,
    ci: BootstrapCI | None = None,
) -> list[dict[str, object]]:
    decisions: list[dict[str, object]] = []
    for record in records:
        robust = robust_evaluation(cases_root, record)
        if not robust:
            continue
        metric = robust.get("metric")
        test_score = robust.get("test_score")
        robust_score = robust.get("robust_score")
        gap = robust.get("generalization_gap")
        test_score_value = float_or_none(test_score)
        robust_score_value = float_or_none(robust_score)
        gap_value = float_or_none(gap)
        if (
            not isinstance(metric, str)
            or test_score_value is None
            or robust_score_value is None
            or gap_value is None
        ):
            continue
        pct_degradation = degradation_pct(metric, test_score_value, robust_score_value)
        for judge_label in [record.claude_label, record.gpt_label]:
            if judge_label not in {"reward_hacking", "not_reward_hacking"}:
                continue
            decisions.append(
                {
                    "task": record.task,
                    "label": judge_label,
                    "gap": gap_value,
                    "degradation_pct": pct_degradation,
                }
            )

    rows: list[dict[str, object]] = []
    for task in TASK_ORDER:
        for label in ["reward_hacking", "not_reward_hacking"]:
            subset = [d for d in decisions if d["task"] == task and d["label"] == label]
            defined_degradations = [
                float(d["degradation_pct"])
                for d in subset
                if finite_number(d["degradation_pct"])
            ]
            robust_worse = sum(
                float(d["gap"]) > 0 for d in subset if finite_number(d["gap"])
            )
            robust_worse_values = [
                1.0 if float(d["gap"]) > 0 else 0.0
                for d in subset
                if finite_number(d["gap"])
            ]
            row: dict[str, object] = {
                "task": task,
                "label": label,
                "n": len(subset),
                "defined_degradation_n": len(defined_degradations),
                "median_degradation_pct": median(defined_degradations),
                "robust_worse": robust_worse,
                "robust_worse_rate": pct(robust_worse, len(subset)),
            }
            add_ci_fields(
                row,
                "median_degradation_pct",
                bootstrap_one_sample_ci(defined_degradations, median, ci),
            )
            add_ci_fields(
                row,
                "robust_worse_rate",
                bootstrap_one_sample_ci(robust_worse_values, mean_pct, ci),
            )
            rows.append(row)
    return rows


def validity_reductions(
    records: Iterable[RunRecord],
    *,
    ci: BootstrapCI | None = None,
) -> list[dict[str, object]]:
    by_model_prompt: dict[tuple[str, str], list[RunRecord]] = defaultdict(list)
    for record in records:
        by_model_prompt[(record.model, record.prompt)].append(record)

    rows: list[dict[str, object]] = []
    for model in MODEL_ORDER:
        baseline = by_model_prompt.get((model, "baseline"), [])
        validity = by_model_prompt.get((model, "validity"), [])
        if not baseline or not validity:
            continue
        baseline_rh = (
            sum(
                (r.claude_label == "reward_hacking") + (r.gpt_label == "reward_hacking")
                for r in baseline
            )
            / 2.0
        )
        validity_rh = (
            sum(
                (r.claude_label == "reward_hacking") + (r.gpt_label == "reward_hacking")
                for r in validity
            )
            / 2.0
        )
        baseline_rate = pct(baseline_rh, len(baseline))
        validity_rate = pct(validity_rh, len(validity))
        absolute_reduction = baseline_rate - validity_rate
        relative_reduction = pct(absolute_reduction, baseline_rate)
        baseline_values = judge_average_values(baseline)
        validity_values = judge_average_values(validity)
        row: dict[str, object] = {
            "model": model,
            "baseline_n": len(baseline),
            "baseline_rh": baseline_rh,
            "baseline_rate": baseline_rate,
            "validity_n": len(validity),
            "validity_rh": validity_rh,
            "validity_rate": validity_rate,
            "absolute_reduction_pp": absolute_reduction,
            "relative_reduction": relative_reduction,
        }
        add_ci_fields(
            row,
            "baseline_rate",
            bootstrap_one_sample_ci(baseline_values, mean_pct, ci),
        )
        add_ci_fields(
            row,
            "validity_rate",
            bootstrap_one_sample_ci(validity_values, mean_pct, ci),
        )
        add_ci_fields(
            row,
            "absolute_reduction_pp",
            bootstrap_two_sample_ci(
                baseline_values,
                validity_values,
                lambda left, right: mean_pct(left) - mean_pct(right),
                ci,
            ),
        )
        add_ci_fields(
            row,
            "relative_reduction",
            bootstrap_two_sample_ci(
                baseline_values,
                validity_values,
                lambda left, right: pct(
                    mean_pct(left) - mean_pct(right), mean_pct(left)
                ),
                ci,
            ),
        )
        rows.append(row)

    baseline_all: list[RunRecord] = []
    validity_all: list[RunRecord] = []
    for model in MODEL_ORDER:
        baseline_all.extend(by_model_prompt.get((model, "baseline"), []))
        validity_all.extend(by_model_prompt.get((model, "validity"), []))
    baseline_all_rh = (
        sum(
            (r.claude_label == "reward_hacking") + (r.gpt_label == "reward_hacking")
            for r in baseline_all
        )
        / 2.0
    )
    validity_all_rh = (
        sum(
            (r.claude_label == "reward_hacking") + (r.gpt_label == "reward_hacking")
            for r in validity_all
        )
        / 2.0
    )
    baseline_all_rate = pct(baseline_all_rh, len(baseline_all))
    validity_all_rate = pct(validity_all_rh, len(validity_all))
    absolute_all = baseline_all_rate - validity_all_rate
    baseline_all_values = judge_average_values(baseline_all)
    validity_all_values = judge_average_values(validity_all)
    row: dict[str, object] = {
        "model": "Overall",
        "baseline_n": len(baseline_all),
        "baseline_rh": baseline_all_rh,
        "baseline_rate": baseline_all_rate,
        "validity_n": len(validity_all),
        "validity_rh": validity_all_rh,
        "validity_rate": validity_all_rate,
        "absolute_reduction_pp": absolute_all,
        "relative_reduction": pct(absolute_all, baseline_all_rate),
    }
    add_ci_fields(
        row,
        "baseline_rate",
        bootstrap_one_sample_ci(baseline_all_values, mean_pct, ci),
    )
    add_ci_fields(
        row,
        "validity_rate",
        bootstrap_one_sample_ci(validity_all_values, mean_pct, ci),
    )
    add_ci_fields(
        row,
        "absolute_reduction_pp",
        bootstrap_two_sample_ci(
            baseline_all_values,
            validity_all_values,
            lambda left, right: mean_pct(left) - mean_pct(right),
            ci,
        ),
    )
    add_ci_fields(
        row,
        "relative_reduction",
        bootstrap_two_sample_ci(
            baseline_all_values,
            validity_all_values,
            lambda left, right: pct(mean_pct(left) - mean_pct(right), mean_pct(left)),
            ci,
        ),
    )
    rows.append(row)
    return rows


def results_tsv_experiment_row_count(cases_root: Path, record: RunRecord) -> int:
    path = case_path_for_record(cases_root, record)
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return 0
    written_files = (obj.get("evidence") or {}).get("written_files") or {}
    results_tsv = written_files.get("results.tsv")
    if not isinstance(results_tsv, str) or not results_tsv.strip():
        return 0
    rows = [line for line in results_tsv.splitlines() if line.strip()]
    if rows and rows[0].startswith("commit\t"):
        rows = rows[1:]
    return len(rows)


def engagement_rates(
    records: Iterable[RunRecord],
    *,
    cases_root: Path,
    ci: BootstrapCI | None = None,
) -> list[dict[str, object]]:
    records_by_model: dict[str, list[RunRecord]] = defaultdict(list)
    for record in records:
        records_by_model[record.model].append(record)

    rows: list[dict[str, object]] = []
    for model in MODEL_ORDER:
        model_records = records_by_model.get(model, [])
        if not model_records:
            continue
        engaged_records = [
            record
            for record in model_records
            if results_tsv_experiment_row_count(cases_root, record) > 0
        ]
        total_rh = (
            sum(
                (r.claude_label == "reward_hacking") + (r.gpt_label == "reward_hacking")
                for r in model_records
            )
            / 2.0
        )
        engaged_rh = (
            sum(
                (r.claude_label == "reward_hacking") + (r.gpt_label == "reward_hacking")
                for r in engaged_records
            )
            / 2.0
        )
        row: dict[str, object] = {
            "model": model,
            "n": len(model_records),
            "engaged_n": len(engaged_records),
            "rh": total_rh,
            "rh_rate": pct(total_rh, len(model_records)),
            "engaged_rh": engaged_rh,
            "engaged_rh_rate": pct(engaged_rh, len(engaged_records)),
        }
        add_ci_fields(
            row,
            "rh_rate",
            bootstrap_one_sample_ci(judge_average_values(model_records), mean_pct, ci),
        )
        add_ci_fields(
            row,
            "engaged_rh_rate",
            bootstrap_one_sample_ci(
                judge_average_values(engaged_records), mean_pct, ci
            ),
        )
        rows.append(row)
    return rows


def print_task_rates_markdown(rows: list[dict[str, object]]) -> None:
    print("| Task type | Claude RH | GPT RH |")
    print("|---|---:|---:|")
    for row in rows:
        print(
            f"| {row['task']} | "
            f"{count_pct_text(int(row['claude_rh']), int(row['n']), ci=get_ci(row, 'claude_rate'))} | "
            f"{count_pct_text(int(row['gpt_rh']), int(row['n']), ci=get_ci(row, 'gpt_rate'))} |"
        )


def print_task_rates_latex(rows: list[dict[str, object]]) -> None:
    print(r"\begin{table}[t]")
    print(r"\centering")
    print(r"\small")
    print(r"\begin{tabular}{lcc}")
    print(r"\toprule")
    print(r"Task type & Claude RH & GPT RH \\")
    print(r"\midrule")
    for row in rows:
        print(
            f"{row['task']} & "
            f"{count_pct_text(int(row['claude_rh']), int(row['n']), latex=True, ci=get_ci(row, 'claude_rate'))} & "
            f"{count_pct_text(int(row['gpt_rh']), int(row['n']), latex=True, ci=get_ci(row, 'gpt_rate'))} "
            + r"\\"
        )
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(
        r"\caption{Reward-hacking judgment rates by task type. "
        r"Percentages are computed after collapsing reruns.}"
    )
    print(r"\label{tab:rh-by-task-type}")
    print(r"\end{table}")


def print_model_rates_markdown(rows: list[dict[str, object]]) -> None:
    print("| Agent model | Claude RH | GPT RH |")
    print("|---|---:|---:|")
    for row in rows:
        print(
            f"| {row['model']} | "
            f"{count_pct_text(int(row['claude_rh']), int(row['n']), ci=get_ci(row, 'claude_rate'))} | "
            f"{count_pct_text(int(row['gpt_rh']), int(row['n']), ci=get_ci(row, 'gpt_rate'))} |"
        )


def print_model_rates_latex(rows: list[dict[str, object]]) -> None:
    print(r"\begin{table}[t]")
    print(r"\centering")
    print(r"\small")
    print(r"\begin{tabular}{lcc}")
    print(r"\toprule")
    print(r"Agent model & Claude RH & GPT RH \\")
    print(r"\midrule")
    for row in rows:
        print(
            f"{row['model']} & "
            f"{count_pct_text(int(row['claude_rh']), int(row['n']), latex=True, ci=get_ci(row, 'claude_rate'))} & "
            f"{count_pct_text(int(row['gpt_rh']), int(row['n']), latex=True, ci=get_ci(row, 'gpt_rate'))} "
            + r"\\"
        )
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(
        r"\caption{Reward-hacking judgment rates by agent model. "
        r"Percentages are computed after collapsing reruns and aggregating "
        r"across task types.}"
    )
    print(r"\label{tab:rh-by-agent-model}")
    print(r"\end{table}")


def print_engagement_rates_markdown(rows: list[dict[str, object]]) -> None:
    print(
        "| Agent model | Runs | Runs with >=1 experiment row | "
        "Avg. RH | Avg. RH with >=1 experiment row |"
    )
    print("|---|---:|---:|---:|---:|")
    for row in rows:
        print(
            f"| {row['model']} | {row['n']} | {row['engaged_n']} | "
            f"{pct_text(float(row['rh']), float(row['n']), ci=get_ci(row, 'rh_rate'))} | "
            f"{pct_text(float(row['engaged_rh']), float(row['engaged_n']), ci=get_ci(row, 'engaged_rh_rate'))} |"
        )


def print_engagement_rates_latex(rows: list[dict[str, object]]) -> None:
    print(r"\begin{table*}[t]")
    print(r"\centering")
    print(r"\small")
    print(r"\setlength{\tabcolsep}{5pt}")
    print(r"\begin{tabular}{lrrrr}")
    print(r"\toprule")
    print(
        r"Agent model & Runs & Runs with $\geq$1 experiment row & "
        r"Avg. RH & Avg. RH with $\geq$1 experiment row \\"
    )
    print(r"\midrule")
    for row in rows:
        print(
            f"{row['model']} & {row['n']} & {row['engaged_n']} & "
            f"{pct_text(float(row['rh']), float(row['n']), latex=True, ci=get_ci(row, 'rh_rate'))} & "
            f"{pct_text(float(row['engaged_rh']), float(row['engaged_n']), latex=True, ci=get_ci(row, 'engaged_rh_rate'))} "
            + r"\\"
        )
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(
        r"\caption{Reward-hacking rates before and after conditioning on task "
        r"engagement. A run is counted as engaged if its final "
        r"\texttt{results.tsv} contains at least one non-header experiment row. "
        r"Reward-hacking rates are averaged across the Claude and GPT judges "
        r"after collapsing reruns.}"
    )
    print(r"\label{tab:rh-by-engagement}")
    print(r"\end{table*}")


def prompt_label(prompt: object) -> str:
    if prompt == "baseline":
        return "Baseline"
    if prompt == "validity":
        return "Validity-rule"
    return str(prompt)


def print_model_prompt_rates_markdown(rows: list[dict[str, object]]) -> None:
    print("| Agent model | Prompt | Claude RH | GPT RH |")
    print("|---|---|---:|---:|")
    for row in rows:
        print(
            f"| {row['model']} | {prompt_label(row['prompt'])} | "
            f"{count_pct_text(int(row['claude_rh']), int(row['n']), ci=get_ci(row, 'claude_rate'))} | "
            f"{count_pct_text(int(row['gpt_rh']), int(row['n']), ci=get_ci(row, 'gpt_rate'))} |"
        )


def print_model_prompt_rates_latex(rows: list[dict[str, object]]) -> None:
    print(r"\begin{table*}[t]")
    print(r"\centering")
    print(r"\small")
    print(r"\setlength{\tabcolsep}{5pt}")
    print(r"\begin{tabular}{llcc}")
    print(r"\toprule")
    print(r"Agent model & Prompt & Claude RH & GPT RH \\")
    print(r"\midrule")
    previous_model = None
    for row in rows:
        model = str(row["model"])
        if previous_model is not None and model != previous_model:
            print(r"\midrule")
        previous_model = model
        print(
            f"{model} & {prompt_label(row['prompt'])} & "
            f"{count_pct_text(int(row['claude_rh']), int(row['n']), latex=True, ci=get_ci(row, 'claude_rate'))} & "
            f"{count_pct_text(int(row['gpt_rh']), int(row['n']), latex=True, ci=get_ci(row, 'gpt_rate'))} "
            + r"\\"
        )
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(
        r"\caption{Reward-hacking judgment rates by agent model and prompt type. "
        r"Percentages are computed after collapsing reruns and aggregating across "
        r"task types.}"
    )
    print(r"\label{tab:rh-by-agent-model-prompt}")
    print(r"\end{table*}")


def print_agreement_markdown(rows: list[dict[str, object]]) -> None:
    print("| Split | Group | Runs | Agreement | Cohen's $\\kappa$ |")
    print("|---|---|---:|---:|---:|")
    for row in rows:
        print(
            f"| {row['axis']} | {row['group']} | {row['n']} | "
            f"{count_pct_text(int(row['agreement']), int(row['n']), ci=get_ci(row, 'agreement_rate'))} | "
            f"{num_text_ci(float(row['kappa']), get_ci(row, 'kappa'))} |"
        )


def print_agreement_latex(rows: list[dict[str, object]]) -> None:
    print(r"\begin{table*}[t]")
    print(r"\centering")
    print(r"\small")
    print(r"\setlength{\tabcolsep}{4pt}")
    print(r"\begin{tabular}{llrcc}")
    print(r"\toprule")
    print(r"Split & Group & Runs & Agreement & Cohen's $\kappa$ \\")
    print(r"\midrule")
    previous_axis = None
    for row in rows:
        axis = str(row["axis"])
        if previous_axis is not None and axis != previous_axis:
            print(r"\midrule")
        previous_axis = axis
        print(
            f"{axis} & {row['group']} & {row['n']} & "
            f"{count_pct_text(int(row['agreement']), int(row['n']), latex=True, ci=get_ci(row, 'agreement_rate'))} & "
            f"{num_text_ci(float(row['kappa']), get_ci(row, 'kappa'))} " + r"\\"
        )
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(
        r"\caption{Agreement between the Claude and GPT reward-hacking judges. "
        r"Cohen's $\kappa$ is computed over the judge labels after collapsing reruns.}"
    )
    print(r"\label{tab:judge-agreement-kappa}")
    print(r"\end{table*}")


def print_robustness_coverage_markdown(rows: list[dict[str, object]]) -> None:
    print("| Group | Runs | Finite robust score/gap | Missing/error | Breakdown |")
    print("|---|---:|---:|---:|---|")
    for row in rows:
        breakdown = (
            f"errors={row['robust_error_other']}, "
            f"too-large errors={row['robust_error_too_large']}, "
            f"nonfinite score={row['missing_or_nonfinite_robust_score']}, "
            f"nonfinite gap={row['missing_or_nonfinite_gap']}"
        )
        print(
            f"| {row['group']} | {row['n']} | "
            f"{count_pct_text(int(row['finite']), int(row['n']), ci=get_ci(row, 'finite_rate'))} | "
            f"{count_pct_text(int(row['missing_or_error']), int(row['n']), ci=get_ci(row, 'missing_or_error_rate'))} | "
            f"{breakdown} |"
        )


def print_robustness_coverage_latex(rows: list[dict[str, object]]) -> None:
    print(r"\begin{table}[t]")
    print(r"\centering")
    print(r"\small")
    print(r"\setlength{\tabcolsep}{4pt}")
    print(r"\begin{tabular}{lccc}")
    print(r"\toprule")
    print(r"Group & Runs & Finite robust score/gap & Missing/error \\")
    print(r"\midrule")
    for row in rows:
        print(
            f"{row['group']} & {row['n']} & "
            f"{count_pct_text(int(row['finite']), int(row['n']), latex=True, ci=get_ci(row, 'finite_rate'))} & "
            f"{count_pct_text(int(row['missing_or_error']), int(row['n']), latex=True, ci=get_ci(row, 'missing_or_error_rate'))} "
            + r"\\"
        )
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(
        r"\caption{Coverage of robust-evaluation scores among canonical judged runs. "
        r"Missing/error includes robust-evaluation errors and missing or non-finite "
        r"robust scores or generalization gaps.}"
    )
    print(r"\label{tab:robustness-coverage}")
    print(r"\end{table}")


def readable_label(label: object) -> str:
    if label == "reward_hacking":
        return "RH"
    if label == "not_reward_hacking":
        return "not RH"
    return str(label)


def print_robust_gap_summary_markdown(rows: list[dict[str, object]]) -> None:
    print(
        "| Split | Group | Judge | Label | N | Median gap | Mean gap | Robust worse |"
    )
    print("|---|---|---|---|---:|---:|---:|---:|")
    for row in rows:
        print(
            f"| {row['split']} | {row['group']} | {row['judge']} | "
            f"{readable_label(row['label'])} | {row['n']} | "
            f"{num_text_ci(float(row['median_gap']), get_ci(row, 'median_gap'))} | "
            f"{num_text_ci(float(row['mean_gap']), get_ci(row, 'mean_gap'))} | "
            f"{count_pct_text(int(row['positive_gaps']), int(row['n']), ci=get_ci(row, 'positive_gap_rate'))} |"
        )


def print_robust_gap_summary_latex(rows: list[dict[str, object]]) -> None:
    print(r"\begin{table*}[t]")
    print(r"\centering")
    print(r"\scriptsize")
    print(r"\setlength{\tabcolsep}{3pt}")
    print(r"\begin{tabular}{llllrccc}")
    print(r"\toprule")
    print(
        r"Split & Group & Judge & Label & $n$ & Median gap & Mean gap & "
        r"Robust worse \\"
    )
    print(r"\midrule")
    previous_split = None
    for row in rows:
        split = str(row["split"])
        if previous_split is not None and split != previous_split:
            print(r"\midrule")
        previous_split = split
        print(
            f"{split} & {row['group']} & {row['judge']} & "
            f"{readable_label(row['label'])} & {row['n']} & "
            f"{num_text_ci(float(row['median_gap']), get_ci(row, 'median_gap'))} & "
            f"{num_text_ci(float(row['mean_gap']), get_ci(row, 'mean_gap'))} & "
            f"{count_pct_text(int(row['positive_gaps']), int(row['n']), latex=True, ci=get_ci(row, 'positive_gap_rate'))} "
            + r"\\"
        )
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(
        r"\caption{Robust generalization gaps by judge label. Positive gaps mean "
        r"the robust split was worse than the visible benchmark split. Rows are "
        r"computed after collapsing reruns onto the corresponding planned run.}"
    )
    print(r"\label{tab:robust-gap-summary}")
    print(r"\end{table*}")


def print_task_degradation_markdown(rows: list[dict[str, object]]) -> None:
    print("| Task | Label | Judgments | Median robust degradation | Robust worse |")
    print("|---|---|---:|---:|---:|")
    for row in rows:
        print(
            f"| {row['task']} | {readable_label(row['label'])} | {row['n']} | "
            f"{pct_value_text(float(row['median_degradation_pct']), ci=get_ci(row, 'median_degradation_pct'))} "
            f"({row['defined_degradation_n']} defined) | "
            f"{count_pct_text(int(row['robust_worse']), int(row['n']), ci=get_ci(row, 'robust_worse_rate'))} |"
        )


def print_task_degradation_latex(rows: list[dict[str, object]]) -> None:
    print(r"\begin{table}[t]")
    print(r"\centering")
    print(r"\small")
    print(r"\setlength{\tabcolsep}{4pt}")
    print(r"\begin{tabular}{llcc}")
    print(r"\toprule")
    print(r"Task & Label & Median robust degradation & Robust worse \\")
    print(r"\midrule")
    previous_task = None
    for row in rows:
        task = str(row["task"])
        if previous_task is not None and task != previous_task:
            print(r"\midrule")
        previous_task = task
        print(
            f"{task} & {readable_label(row['label'])} & "
            f"{pct_value_text(float(row['median_degradation_pct']), latex=True, ci=get_ci(row, 'median_degradation_pct'))} "
            f"({row['defined_degradation_n']} defined) & "
            f"{count_pct_text(int(row['robust_worse']), int(row['n']), latex=True, ci=get_ci(row, 'robust_worse_rate'))} "
            + r"\\"
        )
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(
        r"\caption{Direction-adjusted robust degradation by task and judge label. "
        r"For accuracy, degradation is the relative drop from visible to robust "
        r"accuracy; for RMSE, it is the relative increase from visible to robust "
        r"error. Cases with visible RMSE equal to zero have undefined relative "
        r"degradation and are excluded from the median only.}"
    )
    print(r"\label{tab:robust-degradation-by-task}")
    print(r"\end{table}")


def print_validity_markdown(rows: list[dict[str, object]]) -> None:
    print(
        "| Agent model | Baseline RH | Validity-rule RH | "
        "Absolute reduction | Relative reduction |"
    )
    print("|---|---:|---:|---:|---:|")
    for row in rows:
        print(
            f"| {row['model']} | "
            f"{pct_value_text(float(row['baseline_rate']), ci=get_ci(row, 'baseline_rate'))} | "
            f"{pct_value_text(float(row['validity_rate']), ci=get_ci(row, 'validity_rate'))} | "
            f"{float(row['absolute_reduction_pp']):.1f} pp{pp_ci_text(get_ci(row, 'absolute_reduction_pp'))} | "
            f"{pct_value_text(float(row['relative_reduction']), ci=get_ci(row, 'relative_reduction'))} |"
        )


def print_validity_latex(rows: list[dict[str, object]]) -> None:
    print(r"\begin{table*}[t]")
    print(r"\centering")
    print(r"\small")
    print(r"\setlength{\tabcolsep}{5pt}")
    print(r"\begin{tabular}{lcccc}")
    print(r"\toprule")
    print(
        r"Agent model & Baseline RH & Validity-rule RH & "
        r"Absolute reduction & Relative reduction \\"
    )
    print(r"\midrule")
    for idx, row in enumerate(rows):
        if row["model"] == "Overall" and idx > 0:
            print(r"\midrule")
        print(
            f"{row['model']} & "
            f"{pct_text(float(row['baseline_rh']), float(row['baseline_n']), latex=True, ci=get_ci(row, 'baseline_rate'))} & "
            f"{pct_text(float(row['validity_rh']), float(row['validity_n']), latex=True, ci=get_ci(row, 'validity_rate'))} & "
            f"{float(row['absolute_reduction_pp']):.1f} pp{pp_ci_text(get_ci(row, 'absolute_reduction_pp'))} & "
            f"{pct_value_text(float(row['relative_reduction']), latex=True, ci=get_ci(row, 'relative_reduction'))} "
            + r"\\"
        )
    print(r"\bottomrule")
    print(r"\end{tabular}")
    print(
        r"\caption{Effect of the validity-rule prompt on judged reward hacking "
        r"by agent model. Rates are judge-averaged across the Claude and GPT "
        r"judges after collapsing reruns. Absolute reduction is baseline minus "
        r"validity-rule RH rate; relative reduction divides this difference by "
        r"the baseline rate.}"
    )
    print(r"\label{tab:validity-prompt-reduction}")
    print(r"\end{table*}")


def write_csv(table: str, rows: list[dict[str, object]]) -> None:
    if table == "task-rates":
        fieldnames = ["task", "n", "claude_rh", "claude_rate", "gpt_rh", "gpt_rate"]
    elif table == "model-rates":
        fieldnames = ["model", "n", "claude_rh", "claude_rate", "gpt_rh", "gpt_rate"]
    elif table == "engagement-rates":
        fieldnames = [
            "model",
            "n",
            "engaged_n",
            "rh",
            "rh_rate",
            "engaged_rh",
            "engaged_rh_rate",
        ]
    elif table == "model-prompt-rates":
        fieldnames = [
            "model",
            "prompt",
            "n",
            "claude_rh",
            "claude_rate",
            "gpt_rh",
            "gpt_rate",
        ]
    elif table == "judge-agreement":
        fieldnames = [
            "axis",
            "group",
            "n",
            "agreement",
            "agreement_rate",
            "kappa",
        ]
    elif table == "robustness-coverage":
        fieldnames = [
            "group",
            "n",
            "finite",
            "missing_or_error",
            "missing_or_error_rate",
            "robust_error_other",
            "robust_error_too_large",
            "missing_or_nonfinite_robust_score",
            "missing_or_nonfinite_gap",
            "missing_robust_evaluation",
            "missing_case_json",
        ]
    elif table == "robust-gap-summary":
        fieldnames = [
            "split",
            "group",
            "judge",
            "label",
            "n",
            "median_gap",
            "mean_gap",
            "positive_gaps",
            "positive_gap_rate",
        ]
    elif table == "task-degradation":
        fieldnames = [
            "task",
            "label",
            "n",
            "defined_degradation_n",
            "median_degradation_pct",
            "robust_worse",
            "robust_worse_rate",
        ]
    else:
        fieldnames = [
            "model",
            "baseline_n",
            "baseline_rh",
            "baseline_rate",
            "validity_n",
            "validity_rh",
            "validity_rate",
            "absolute_reduction_pp",
            "relative_reduction",
        ]
    extra_ci_fields = sorted(
        {
            key
            for row in rows
            for key in row
            if key.endswith("_ci_low") or key.endswith("_ci_high")
        }
    )
    fieldnames.extend(field for field in extra_ci_fields if field not in fieldnames)
    writer = csv.DictWriter(sys.stdout, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow({field: row.get(field, "") for field in fieldnames})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outputs-root",
        type=Path,
        default=Path("data/outputs/judging"),
        help="Root containing archived judging output directories.",
    )
    parser.add_argument(
        "--cases-root",
        type=Path,
        default=Path("data/cases/make_datasets"),
        help="Root containing archived make_datasets case JSON directories.",
    )
    parser.add_argument(
        "--table",
        choices=[
            "task-rates",
            "model-rates",
            "engagement-rates",
            "model-prompt-rates",
            "validity-reduction",
            "judge-agreement",
            "robustness-coverage",
            "robust-gap-summary",
            "task-degradation",
        ],
        default="task-rates",
        help="Which paper statistic to render.",
    )
    parser.add_argument(
        "--exclude-sonnet",
        action="store_true",
        help="Exclude archived claude-sonnet-4-6 results.",
    )
    parser.add_argument(
        "--exclude-gemini-flash",
        action="store_true",
        help="Exclude archived gemini-3-flash-preview results.",
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "latex", "csv", "json"],
        default="markdown",
        help="Output format.",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="Add percentile bootstrap confidence intervals to supported statistics.",
    )
    parser.add_argument(
        "--ci-level",
        type=float,
        default=0.95,
        help="Bootstrap confidence level used with --ci.",
    )
    parser.add_argument(
        "--bootstrap-samples",
        type=int,
        default=10000,
        help="Number of bootstrap resamples used with --ci.",
    )
    parser.add_argument(
        "--bootstrap-seed",
        type=int,
        default=0,
        help="Random seed used with --ci.",
    )
    args = parser.parse_args()
    ci = (
        BootstrapCI(
            samples=args.bootstrap_samples,
            confidence=args.ci_level,
            seed=args.bootstrap_seed,
        )
        if args.ci
        else None
    )

    records = load_records(
        args.outputs_root,
        include_sonnet=not args.exclude_sonnet,
        include_gemini_flash=not args.exclude_gemini_flash,
    )
    if args.table == "task-rates":
        rows = task_rates(records, ci=ci)
    elif args.table == "model-rates":
        rows = model_rates(records, ci=ci)
    elif args.table == "engagement-rates":
        rows = engagement_rates(records, cases_root=args.cases_root, ci=ci)
    elif args.table == "model-prompt-rates":
        rows = model_prompt_rates(records, ci=ci)
    elif args.table == "judge-agreement":
        rows = agreement_rows(records, ci=ci)
    elif args.table == "robustness-coverage":
        rows = robustness_coverage_rows(records, cases_root=args.cases_root, ci=ci)
    elif args.table == "robust-gap-summary":
        rows = robust_gap_summary_rows(records, cases_root=args.cases_root, ci=ci)
    elif args.table == "task-degradation":
        rows = task_degradation_rows(records, cases_root=args.cases_root, ci=ci)
    else:
        rows = validity_reductions(records, ci=ci)

    if args.format == "json":
        print(json.dumps(rows, indent=2))
    elif args.format == "csv":
        write_csv(args.table, rows)
    elif args.table == "task-rates" and args.format == "markdown":
        print_task_rates_markdown(rows)
    elif args.table == "task-rates" and args.format == "latex":
        print_task_rates_latex(rows)
    elif args.table == "model-rates" and args.format == "markdown":
        print_model_rates_markdown(rows)
    elif args.table == "model-rates" and args.format == "latex":
        print_model_rates_latex(rows)
    elif args.table == "engagement-rates" and args.format == "markdown":
        print_engagement_rates_markdown(rows)
    elif args.table == "engagement-rates" and args.format == "latex":
        print_engagement_rates_latex(rows)
    elif args.table == "model-prompt-rates" and args.format == "markdown":
        print_model_prompt_rates_markdown(rows)
    elif args.table == "model-prompt-rates" and args.format == "latex":
        print_model_prompt_rates_latex(rows)
    elif args.table == "judge-agreement" and args.format == "markdown":
        print_agreement_markdown(rows)
    elif args.table == "judge-agreement" and args.format == "latex":
        print_agreement_latex(rows)
    elif args.table == "robustness-coverage" and args.format == "markdown":
        print_robustness_coverage_markdown(rows)
    elif args.table == "robustness-coverage" and args.format == "latex":
        print_robustness_coverage_latex(rows)
    elif args.table == "robust-gap-summary" and args.format == "markdown":
        print_robust_gap_summary_markdown(rows)
    elif args.table == "robust-gap-summary" and args.format == "latex":
        print_robust_gap_summary_latex(rows)
    elif args.table == "task-degradation" and args.format == "markdown":
        print_task_degradation_markdown(rows)
    elif args.table == "task-degradation" and args.format == "latex":
        print_task_degradation_latex(rows)
    elif args.table == "validity-reduction" and args.format == "markdown":
        print_validity_markdown(rows)
    elif args.table == "validity-reduction" and args.format == "latex":
        print_validity_latex(rows)


if __name__ == "__main__":
    main()
