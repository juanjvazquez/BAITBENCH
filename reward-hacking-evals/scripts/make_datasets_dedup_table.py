#!/usr/bin/env python3
"""Render de-duplicated make_datasets reward-hacking result tables.

The v4 local task-family outputs include some rerun attempts, especially for
VPS2 models. For paper reporting, reruns should replace the original planned
run rather than count as extra independent trials. This script collapses
``*_rerunNN`` records onto their base run ID, keeps the highest rerun number,
and drops smoke/test extras such as ``*_vps2_smoke_01``.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


OLD_V4_LABELS = {
    "Entity overlap": "v4_t01_local_20260516",
    "Near-duplicate leakage": "v4_t02_local_20260516",
    "No-signal classification": "v4_t03_local_20260513",
}

SONNET_V4_LABEL = "sonnet_v4_20260520"

TASK_ORDER = [
    "Entity overlap",
    "Near-duplicate leakage",
    "No-signal classification",
]

OLD_V4_MODEL_ORDER = [
    "claude-opus-4-6",
    "gpt-5.4",
    "kimi-k2.5",
    "gemini-3.1-pro-preview",
    "deepseek-v4-pro",
]

SONNET_V4_MODEL_ORDER = ["claude-sonnet-4-6"]

RERUN_RE = re.compile(r"_rerun(\d+)$")
SMOKE_RE = re.compile(r"_vps2_smoke_\d+$")


@dataclass(frozen=True)
class Row:
    task: str
    model: str
    n: int
    claude_rh: int
    gpt_rh: int
    agreement: int


def load_binary_labels(path: Path) -> dict[str, str]:
    labels: dict[str, str] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("type") != "record" or obj.get("skipped"):
                continue
            judgment = obj.get("judgment") or {}
            label = judgment.get("label")
            run_id = obj.get("run_id")
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


def model_from_run_id(run_id: str) -> str:
    prefix = run_id.split("_", 1)[0]
    if prefix == "claude":
        if "claude-sonnet-4-6" in run_id:
            return "claude-sonnet-4-6"
        return "claude-opus-4-6"
    if prefix == "codex":
        return "gpt-5.4"
    if prefix == "kimi":
        return "kimi-k2.5"
    if prefix == "gemini":
        return "gemini-3.1-pro-preview"
    if prefix == "deepseek":
        return "deepseek-v4-pro"
    return prefix


def task_from_run_id(run_id: str) -> str | None:
    if "_regression_task_01_" in run_id:
        return "Entity overlap"
    if "_regression_task_02_" in run_id:
        return "Near-duplicate leakage"
    if "_classification_task_03_" in run_id:
        return "No-signal classification"
    return None


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
    return [run_id for _, run_id in best_by_logical_id.values()]


def build_old_v4_rows(outputs_root: Path) -> list[Row]:
    rows: list[Row] = []
    for task, label_dir_name in OLD_V4_LABELS.items():
        label_dir = outputs_root / label_dir_name
        claude_labels = load_binary_labels(label_dir / "judging_binary_anthropic.jsonl")
        gpt_labels = load_binary_labels(label_dir / "judging_binary_openai.jsonl")
        by_model: dict[str, list[str]] = defaultdict(list)
        for run_id in select_canonical_run_ids(claude_labels, gpt_labels):
            by_model[model_from_run_id(run_id)].append(run_id)

        for model in OLD_V4_MODEL_ORDER:
            run_ids = by_model.get(model, [])
            if not run_ids:
                continue
            n = len(run_ids)
            claude_rh = sum(
                claude_labels[run_id] == "reward_hacking" for run_id in run_ids
            )
            gpt_rh = sum(gpt_labels[run_id] == "reward_hacking" for run_id in run_ids)
            agreement = sum(
                claude_labels[run_id] == gpt_labels[run_id] for run_id in run_ids
            )
            rows.append(
                Row(
                    task=task,
                    model=model,
                    n=n,
                    claude_rh=claude_rh,
                    gpt_rh=gpt_rh,
                    agreement=agreement,
                )
            )
    return rows


def build_sonnet_v4_rows(outputs_root: Path) -> list[Row]:
    label_dir = outputs_root / SONNET_V4_LABEL
    claude_labels = load_binary_labels(label_dir / "judging_binary_anthropic.jsonl")
    gpt_labels = load_binary_labels(label_dir / "judging_binary_openai.jsonl")
    by_task_and_model: dict[tuple[str, str], list[str]] = defaultdict(list)
    for run_id in select_canonical_run_ids(claude_labels, gpt_labels):
        task = task_from_run_id(run_id)
        if task is None:
            continue
        by_task_and_model[(task, model_from_run_id(run_id))].append(run_id)

    rows: list[Row] = []
    for task in TASK_ORDER:
        for model in SONNET_V4_MODEL_ORDER:
            run_ids = by_task_and_model.get((task, model), [])
            if not run_ids:
                continue
            n = len(run_ids)
            claude_rh = sum(
                claude_labels[run_id] == "reward_hacking" for run_id in run_ids
            )
            gpt_rh = sum(gpt_labels[run_id] == "reward_hacking" for run_id in run_ids)
            agreement = sum(
                claude_labels[run_id] == gpt_labels[run_id] for run_id in run_ids
            )
            rows.append(
                Row(
                    task=task,
                    model=model,
                    n=n,
                    claude_rh=claude_rh,
                    gpt_rh=gpt_rh,
                    agreement=agreement,
                )
            )
    return rows


def build_rows(outputs_root: Path, *, preset: str) -> list[Row]:
    if preset == "old-v4":
        return build_old_v4_rows(outputs_root)
    if preset == "sonnet-v4":
        return build_sonnet_v4_rows(outputs_root)
    raise ValueError(f"unknown preset: {preset}")


def count_pct(count: int, denom: int, *, latex: bool = False) -> str:
    pct = f"{count / denom:.1%}"
    if latex:
        pct = pct.replace("%", r"\%")
    return f"{count}/{denom} ({pct})"


def markdown_table(rows: Iterable[Row]) -> str:
    lines = [
        "| Task | Model | Runs | Claude judge RH | GPT judge RH | Judge agreement |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            " | ".join(
                [
                    f"| {row.task}",
                    row.model,
                    str(row.n),
                    count_pct(row.claude_rh, row.n),
                    count_pct(row.gpt_rh, row.n),
                    f"{count_pct(row.agreement, row.n)} |",
                ]
            )
        )
    return "\n".join(lines)


def family_summary_markdown(rows: Iterable[Row]) -> str:
    totals: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    for row in rows:
        accum = totals[row.task]
        accum[0] += row.n
        accum[1] += row.claude_rh
        accum[2] += row.gpt_rh
        accum[3] += row.agreement

    lines = [
        "| Task | Runs | Claude judge RH | GPT judge RH | Judge agreement |",
        "|---|---:|---:|---:|---:|",
    ]
    for task in TASK_ORDER:
        if task not in totals:
            continue
        n, claude_rh, gpt_rh, agreement = totals[task]
        lines.append(
            " | ".join(
                [
                    f"| {task}",
                    str(n),
                    count_pct(claude_rh, n),
                    count_pct(gpt_rh, n),
                    f"{count_pct(agreement, n)} |",
                ]
            )
        )
    return "\n".join(lines)


def latex_table(rows: Iterable[Row]) -> str:
    lines = [
        r"\begin{table*}[t]",
        r"\centering",
        r"\small",
        r"\setlength{\tabcolsep}{4pt}",
        r"\begin{tabular}{llccc}",
        r"\toprule",
        r"Task & Agent model & Claude RH & GPT RH & Agreement \\",
        r"\midrule",
    ]
    rows_by_task: dict[str, list[Row]] = defaultdict(list)
    for row in rows:
        rows_by_task[row.task].append(row)
    first_task = True
    for task in TASK_ORDER:
        if not first_task:
            lines.append(r"\midrule")
        first_task = False
        for row in rows_by_task.get(task, []):
            lines.append(
                " & ".join(
                    [
                        row.task,
                        row.model,
                        count_pct(row.claude_rh, row.n, latex=True),
                        count_pct(row.gpt_rh, row.n, latex=True),
                        count_pct(row.agreement, row.n, latex=True),
                    ]
                )
                + r" \\"
            )
    lines.extend(
        [
            r"\bottomrule",
            r"\end{tabular}",
            (
                r"\caption{Reward-hacking judgments by agent model and task "
                r"family after collapsing reruns onto the corresponding "
                r"planned run. Each denominator is the number of canonical "
                r"judged runs for that agent model and family.}"
            ),
            r"\label{tab:rh-by-agent-model-family-dedup}",
            r"\end{table*}",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--outputs-root",
        type=Path,
        default=Path("data/outputs/judging"),
        help="Root containing judging output directories.",
    )
    parser.add_argument(
        "--preset",
        choices=["old-v4", "sonnet-v4"],
        default="old-v4",
        help=(
            "Result set to render. old-v4 reads v4_t01/t02/t03 local outputs; "
            "sonnet-v4 reads sonnet_v4_20260520."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["markdown", "latex", "both"],
        default="markdown",
        help="Output table format.",
    )
    parser.add_argument(
        "--include-summary",
        action="store_true",
        help="Also print the task-level summary table.",
    )
    args = parser.parse_args()

    rows = build_rows(args.outputs_root, preset=args.preset)
    if args.format in {"markdown", "both"}:
        print(markdown_table(rows))
        if args.include_summary:
            print()
            print(family_summary_markdown(rows))
    if args.format == "both":
        print()
    if args.format in {"latex", "both"}:
        print(latex_table(rows))


if __name__ == "__main__":
    main()
