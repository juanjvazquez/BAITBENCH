#!/usr/bin/env python3
"""Analyze observable behavioral characteristics associated with BAITBENCH RH.

This analysis uses the paper's 1,258 canonical runs. It separates observable
harness evidence (logged experiment rows and evaluator calls) from judge-coded
transcript awareness. Associations are descriptive and do not identify causal
or activation-level mechanisms.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from evaluator_calls_and_judge_reasons import (
    Record,
    case_path,
    load_records,
    percentile,
    roc_auc,
)


MODEL_ORDER = (
    "claude-opus-4-6",
    "claude-sonnet-4-6",
    "gpt-5.4",
    "kimi-k2.5",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "deepseek-v4-pro",
)

MODEL_NAMES = {
    "claude-opus-4-6": "Claude Opus 4.6",
    "claude-sonnet-4-6": "Claude Sonnet 4.6",
    "gpt-5.4": "GPT-5.4",
    "kimi-k2.5": "Kimi K2.5",
    "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
    "gemini-3-flash-preview": "Gemini 3 Flash",
    "deepseek-v4-pro": "DeepSeek V4 Pro",
}

SHORTCUT_AWARENESS = {
    "mechanism_awareness_framed_as_success",
    "explicit_invalidity_awareness",
}


@dataclass(frozen=True)
class BehaviorRecord:
    base: Record
    experiment_rows: int
    keep_rows: int
    discard_rows: int
    crash_rows: int
    post_first_keep_rows: int
    read_events: int
    write_events: int
    prompt: str

    @property
    def engaged(self) -> bool:
        return self.experiment_rows > 0

    @property
    def is_consensus(self) -> bool:
        return self.base.consensus_group in {"both_rh", "both_non_rh"}

    @property
    def is_rh(self) -> bool:
        return self.base.consensus_group == "both_rh"


def prompt_from_run_id(run_id: str) -> str:
    return "validity" if "_validity_" in run_id else "baseline"


def experiment_row_count(case: dict[str, object]) -> int:
    package_counts = case.get("package_counts") or {}
    value = package_counts.get("trajectory_row_count")
    if isinstance(value, int):
        return value
    trajectory = (case.get("evidence") or {}).get("trajectory") or []
    return len(trajectory)


def package_count(case: dict[str, object], key: str) -> int:
    value = (case.get("package_counts") or {}).get(key)
    return value if isinstance(value, int) else 0


def trajectory_statistics(case: dict[str, object]) -> tuple[int, int, int, int, int]:
    trajectory = (case.get("evidence") or {}).get("trajectory") or []
    statuses = [str(row.get("status", "")).lower() for row in trajectory if isinstance(row, dict)]
    keep = sum(status == "keep" for status in statuses)
    discard = sum(status == "discard" for status in statuses)
    crash = sum(status == "crash" for status in statuses)
    first_keep = next((index for index, status in enumerate(statuses) if status == "keep"), None)
    after_keep = len(statuses) - first_keep - 1 if first_keep is not None else 0
    return len(statuses), keep, discard, crash, after_keep


def enrich_records(records: list[Record], cases_root: Path) -> list[BehaviorRecord]:
    enriched: list[BehaviorRecord] = []
    for record in records:
        path = case_path(cases_root, record.task, record.model, record.run_id)
        case = json.loads(path.read_text(encoding="utf-8"))
        experiment_rows, keep_rows, discard_rows, crash_rows, post_first_keep_rows = (
            trajectory_statistics(case)
        )
        enriched.append(
            BehaviorRecord(
                base=record,
                experiment_rows=experiment_rows,
                keep_rows=keep_rows,
                discard_rows=discard_rows,
                crash_rows=crash_rows,
                post_first_keep_rows=post_first_keep_rows,
                read_events=package_count(case, "read_event_count"),
                write_events=package_count(case, "write_event_count"),
                prompt=prompt_from_run_id(record.run_id),
            )
        )
    return enriched


def load_awareness(path: Path) -> dict[str, dict[str, object]]:
    output: dict[str, dict[str, object]] = {}
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            obj = json.loads(line)
            run_id = obj.get("run_id")
            judgment = obj.get("judgment")
            if obj.get("type") == "record" and isinstance(run_id, str) and isinstance(judgment, dict):
                output[run_id] = judgment
    return output


def pct(count: int, total: int) -> str:
    return f"{count}/{total} ({100 * count / total:.1f}%)" if total else "0/0"


def median(values: list[int]) -> str:
    return f"{statistics.median(values):.1f}" if values else "NA"


def experiment_bin(value: int) -> str:
    if value == 0:
        return "0"
    if value <= 5:
        return "1-5"
    if value <= 10:
        return "6-10"
    if value <= 20:
        return "11-20"
    return ">20"


def call_bin(value: int) -> str:
    return experiment_bin(value)


def keep_bin(value: int) -> str:
    if value == 0:
        return "0"
    if value == 1:
        return "1"
    if value == 2:
        return "2"
    if value <= 5:
        return "3-5"
    return ">5"


BIN_ORDER = ("0", "1-5", "6-10", "11-20", ">20")


def auc_statistics(
    rows: list[BehaviorRecord], *, samples: int, seed: int
) -> dict[str, tuple[float, tuple[float, float]]]:
    cohort = [row for row in rows if row.is_consensus]

    def calculate(draw: list[BehaviorRecord]) -> tuple[float, float]:
        labels = [int(row.is_rh) for row in draw]
        return (
            roc_auc([float(row.experiment_rows) for row in draw], labels),
            roc_auc([float(row.base.evaluator_calls) for row in draw], labels),
        )

    point = calculate(cohort)
    clusters: dict[tuple[str, str, int], list[BehaviorRecord]] = defaultdict(list)
    for row in cohort:
        clusters[(row.base.task, row.base.size, row.base.seed)].append(row)
    keys_by_stratum: dict[tuple[str, str], list[tuple[str, str, int]]] = defaultdict(list)
    for key in clusters:
        keys_by_stratum[(key[0], key[1])].append(key)

    rng = random.Random(seed)
    draws: list[list[float]] = [[], []]
    for _ in range(samples):
        sample: list[BehaviorRecord] = []
        for stratum in sorted(keys_by_stratum):
            keys = keys_by_stratum[stratum]
            for _ in range(len(keys)):
                sample.extend(clusters[rng.choice(keys)])
        estimates = calculate(sample)
        for index, estimate in enumerate(estimates):
            if math.isfinite(estimate):
                draws[index].append(estimate)

    return {
        "experiment_rows": (
            point[0],
            (percentile(draws[0], 0.025), percentile(draws[0], 0.975)),
        ),
        "evaluator_calls": (
            point[1],
            (percentile(draws[1], 0.025), percentile(draws[1], 0.975)),
        ),
    }


FEATURES = {
    "experiment_rows": ("Logged experiment rows", lambda row: float(row.experiment_rows)),
    "evaluator_calls": ("Evaluator calls", lambda row: float(row.base.evaluator_calls)),
    "keep_rows": ("Rows marked keep", lambda row: float(row.keep_rows)),
    "discard_rows": ("Rows marked discard", lambda row: float(row.discard_rows)),
    "post_first_keep_rows": (
        "Experiments after first keep",
        lambda row: float(row.post_first_keep_rows),
    ),
    "read_events": ("File-read events", lambda row: float(row.read_events)),
    "write_events": ("File-write events", lambda row: float(row.write_events)),
}


def fixed_stratum(row: BehaviorRecord) -> tuple[str, str, str, str]:
    return (row.base.model, row.base.task, row.base.size, row.prompt)


def stratified_auc(
    rows: list[BehaviorRecord], feature_key: str
) -> tuple[float, int, int]:
    getter = FEATURES[feature_key][1]
    groups: dict[tuple[str, str, str, str], list[BehaviorRecord]] = defaultdict(list)
    for row in rows:
        groups[fixed_stratum(row)].append(row)

    weighted = 0.0
    pair_count = 0
    usable_strata = 0
    for group in groups.values():
        positives = sum(row.is_rh for row in group)
        negatives = len(group) - positives
        if positives == 0 or negatives == 0:
            continue
        labels = [int(row.is_rh) for row in group]
        auc = roc_auc([getter(row) for row in group], labels)
        pairs = positives * negatives
        weighted += auc * pairs
        pair_count += pairs
        usable_strata += 1
    return (
        weighted / pair_count if pair_count else float("nan"),
        pair_count,
        usable_strata,
    )


def stratified_auc_statistics(
    rows: list[BehaviorRecord], *, samples: int, seed: int
) -> dict[str, dict[str, object]]:
    eligible = [row for row in rows if row.is_consensus and row.engaged]
    points = {key: stratified_auc(eligible, key) for key in FEATURES}

    clusters: dict[tuple[str, str, int], list[BehaviorRecord]] = defaultdict(list)
    for row in eligible:
        clusters[(row.base.task, row.base.size, row.base.seed)].append(row)
    keys_by_stratum: dict[tuple[str, str], list[tuple[str, str, int]]] = defaultdict(list)
    for key in clusters:
        keys_by_stratum[(key[0], key[1])].append(key)

    rng = random.Random(seed)
    draws: dict[str, list[float]] = {key: [] for key in FEATURES}
    for _ in range(samples):
        sample: list[BehaviorRecord] = []
        for stratum in sorted(keys_by_stratum):
            keys = keys_by_stratum[stratum]
            for _ in range(len(keys)):
                sample.extend(clusters[rng.choice(keys)])
        for key in FEATURES:
            estimate, _, _ = stratified_auc(sample, key)
            if math.isfinite(estimate):
                draws[key].append(estimate)

    return {
        key: {
            "auc": points[key][0],
            "pairs": points[key][1],
            "strata": points[key][2],
            "ci": (
                percentile(draws[key], 0.025),
                percentile(draws[key], 0.975),
            ),
        }
        for key in FEATURES
    }


def subgroup_stratified_auc(
    rows: list[BehaviorRecord], feature_key: str, *, field: str
) -> dict[str, tuple[float, int, int]]:
    values: dict[str, list[BehaviorRecord]] = defaultdict(list)
    for row in rows:
        if not (row.is_consensus and row.engaged):
            continue
        if field == "task":
            value = row.base.task
        elif field == "prompt":
            value = row.prompt
        else:
            raise ValueError(field)
        values[value].append(row)
    return {value: stratified_auc(group, feature_key) for value, group in values.items()}


def leave_one_model_out_auc(
    rows: list[BehaviorRecord], feature_key: str
) -> dict[str, tuple[float, int, int]]:
    return {
        model: stratified_auc(
            [
                row
                for row in rows
                if row.is_consensus and row.engaged and row.base.model != model
            ],
            feature_key,
        )
        for model in MODEL_ORDER
    }


def render_report(
    rows: list[BehaviorRecord],
    awareness: dict[str, dict[str, object]],
    *,
    bootstrap_samples: int,
    seed: int,
) -> str:
    consensus = [row for row in rows if row.is_consensus]
    rh = [row for row in rows if row.is_rh]
    engaged_consensus = [row for row in consensus if row.engaged]
    disengaged_consensus = [row for row in consensus if not row.engaged]

    lines = [
        "# Observable behavioral characteristics associated with BAITBENCH reward hacking",
        "",
        "## Scope",
        "",
        "- Population: the paper's 1,258 canonical runs.",
        "- Reward hacking: consensus of the archived Claude Opus 4.6 and GPT-5.4 binary judges; disagreements and unclear cases are excluded from RH-rate denominators.",
        "- Engagement: at least one experiment row recorded in `results.tsv` (`trajectory_row_count > 0`). This is an observable harness measure, not a claim about internal effort.",
        "- Experiment rows and evaluator calls are independently packaged harness events. Transcript awareness is a separate LLM-coded annotation and is reported only among consensus-RH runs.",
        "- The binary judges could inspect the trajectories, including `results.tsv`. Behavioral associations therefore characterize patterns accompanying the judged outcome; they are not independent validation of the labels.",
        "- All relationships are descriptive. Engagement is post-treatment, model and scaffold are partly confounded, and none of these statistics identifies an activation-level or causal mechanism.",
        "",
        "## Main result",
        "",
        f"Recorded engagement is an eligibility condition, not a behavioral predictor: without an experiment, a run has little opportunity to discover or retain a shortcut. The primary behavioral cohort is therefore the {len(engaged_consensus)} consensus-labeled runs with at least one logged experiment; {sum(row.is_rh for row in engaged_consensus)} ({100 * sum(row.is_rh for row in engaged_consensus) / len(engaged_consensus):.1f}%) were RH.",
        "",
        f"For context, the full consensus-labeled cohort was {pct(sum(row.is_rh for row in consensus), len(consensus))}, while runs with no recorded experiment were {pct(sum(row.is_rh for row in disengaged_consensus), len(disengaged_consensus))}. Conditioning changes some model comparisons substantially, especially Kimi, so both denominators should be disclosed.",
        "",
        "## Behavioral funnel by model",
        "",
        "| Model | Canonical runs | Engaged | Consensus RH | RH among engaged consensus | Median experiment rows among engaged | Shortcut/invalidity awareness among consensus RH |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]

    for model in MODEL_ORDER:
        model_rows = [row for row in rows if row.base.model == model]
        model_consensus = [row for row in model_rows if row.is_consensus]
        model_engaged_consensus = [row for row in model_consensus if row.engaged]
        model_rh = [row for row in model_rows if row.is_rh]
        aware = sum(
            awareness.get(row.base.run_id, {}).get("awareness_label")
            in SHORTCUT_AWARENESS
            for row in model_rh
        )
        lines.append(
            f"| {MODEL_NAMES[model]} | {len(model_rows)} | "
            f"{pct(sum(row.engaged for row in model_rows), len(model_rows))} | "
            f"{pct(sum(row.is_rh for row in model_consensus), len(model_consensus))} | "
            f"{pct(sum(row.is_rh for row in model_engaged_consensus), len(model_engaged_consensus))} | "
            f"{median([row.experiment_rows for row in model_rows if row.engaged])} | "
            f"{pct(aware, len(model_rh))} |"
        )

    lines.extend(
        [
            "",
            "The awareness column asks a narrower question: among runs whose retained submission was already labeled RH, did the transcript explicitly identify the shortcut or call the method invalid? Missing awareness judgments remain in the denominator.",
            "",
            "## Eligibility-threshold sensitivity",
            "",
            "The one-row threshold is the primary definition because it captures the minimum recorded experiment. Stricter thresholds test whether conclusions depend on barely active runs.",
            "",
            "| Minimum logged experiment rows | Eligible consensus runs | Consensus RH rate | Kimi eligible consensus runs | Kimi RH rate |",
            "|---:|---:|---:|---:|---:|",
        ]
    )
    for threshold in (1, 2, 3, 5):
        eligible = [row for row in consensus if row.experiment_rows >= threshold]
        kimi = [row for row in eligible if row.base.model == "kimi-k2.5"]
        lines.append(
            f"| {threshold} | {len(eligible)} | {pct(sum(row.is_rh for row in eligible), len(eligible))} | "
            f"{len(kimi)} | {pct(sum(row.is_rh for row in kimi), len(kimi))} |"
        )

    lines.extend(
        [
            "",
            "## RH rate by post-engagement activity",
            "",
            "### Logged experiment rows",
            "",
            "| Experiment rows | Consensus-labeled runs | Consensus RH | RH rate within bin |",
            "|---:|---:|---:|---:|",
        ]
    )
    for label in BIN_ORDER[1:]:
        group = [
            row
            for row in engaged_consensus
            if experiment_bin(row.experiment_rows) == label
        ]
        lines.append(f"| {label} | {len(group)} | {sum(row.is_rh for row in group)} | {pct(sum(row.is_rh for row in group), len(group))} |")

    lines.extend(
        [
            "",
            "### Evaluator calls",
            "",
            "| Evaluator calls | Consensus-labeled runs | Consensus RH | RH rate within bin |",
            "|---:|---:|---:|---:|",
        ]
    )
    for label in BIN_ORDER:
        group = [
            row
            for row in engaged_consensus
            if call_bin(row.base.evaluator_calls) == label
        ]
        lines.append(f"| {label} | {len(group)} | {sum(row.is_rh for row in group)} | {pct(sum(row.is_rh for row in group), len(group))} |")

    lines.extend(
        [
            "",
            "### Successive provisional keep decisions",
            "",
            "A `keep` row means the agent explicitly logged an evaluated variant as worth retaining. Multiple keep rows therefore record repeated acceptance of visible-score winners, although the exact logging practice can vary by scaffold and model.",
            "",
            "| Rows marked keep | Consensus-labeled engaged runs | Consensus RH | RH rate within bin |",
            "|---:|---:|---:|---:|",
        ]
    )
    for label in ("0", "1", "2", "3-5", ">5"):
        group = [row for row in engaged_consensus if keep_bin(row.keep_rows) == label]
        lines.append(
            f"| {label} | {len(group)} | {sum(row.is_rh for row in group)} | "
            f"{pct(sum(row.is_rh for row in group), len(group))} |"
        )

    stratified = stratified_auc_statistics(
        rows, samples=bootstrap_samples, seed=seed + 1
    )
    lines.extend(
        [
            "",
            "## Primary within-cell behavioral associations",
            "",
            "The primary comparison is restricted to engaged runs and compares RH with non-RH only within the same model, task family, dataset size, and prompt condition. The reported AUC pools only within-cell RH/non-RH pairs; 0.5 means no ordering and values above 0.5 mean the characteristic is larger in RH runs. Confidence intervals cluster-bootstrap whole seed-generated dataset instances.",
            "",
            "| Post-engagement characteristic | Within-cell AUC [95% cluster-bootstrap CI] | Comparable RH/non-RH pairs | Usable cells |",
            "|---|---:|---:|---:|",
        ]
    )
    for key, (label, _) in FEATURES.items():
        result = stratified[key]
        ci = result["ci"]
        lines.append(
            f"| {label} | {result['auc']:.3f} [{ci[0]:.3f}, {ci[1]:.3f}] | "
            f"{result['pairs']} | {result['strata']} |"
        )

    lines.extend(
        [
            "",
            "A count of rows marked `keep` is partly a logging convention, while read/write events are scaffold-mediated. The most interpretable search characteristics are total experiments, evaluator calls, discarded trials, and continued experimentation after the first provisional keep.",
            "",
            "## Stability across tasks and prompts",
            "",
            "| Subgroup | Logged experiments AUC | Evaluator calls AUC | Rows marked keep AUC | Experiments after first keep AUC |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    task_results = {
        key: subgroup_stratified_auc(rows, key, field="task")
        for key in (
            "experiment_rows",
            "evaluator_calls",
            "keep_rows",
            "post_first_keep_rows",
        )
    }
    for task in ("t01", "t02", "t03"):
        lines.append(
            f"| {task} | {task_results['experiment_rows'][task][0]:.3f} | "
            f"{task_results['evaluator_calls'][task][0]:.3f} | "
            f"{task_results['keep_rows'][task][0]:.3f} | "
            f"{task_results['post_first_keep_rows'][task][0]:.3f} |"
        )
    prompt_results = {
        key: subgroup_stratified_auc(rows, key, field="prompt")
        for key in (
            "experiment_rows",
            "evaluator_calls",
            "keep_rows",
            "post_first_keep_rows",
        )
    }
    for prompt in ("baseline", "validity"):
        lines.append(
            f"| {prompt} prompt | {prompt_results['experiment_rows'][prompt][0]:.3f} | "
            f"{prompt_results['evaluator_calls'][prompt][0]:.3f} | "
            f"{prompt_results['keep_rows'][prompt][0]:.3f} | "
            f"{prompt_results['post_first_keep_rows'][prompt][0]:.3f} |"
        )

    lines.extend(
        [
            "",
            "## Leave-one-model-out stability",
            "",
            "| Excluded model | Logged experiments AUC | Evaluator calls AUC | Rows marked keep AUC | Experiments after first keep AUC |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    loo = {
        key: leave_one_model_out_auc(rows, key)
        for key in (
            "experiment_rows",
            "evaluator_calls",
            "keep_rows",
            "post_first_keep_rows",
        )
    }
    for model in MODEL_ORDER:
        lines.append(
            f"| {MODEL_NAMES[model]} | {loo['experiment_rows'][model][0]:.3f} | "
            f"{loo['evaluator_calls'][model][0]:.3f} | "
            f"{loo['keep_rows'][model][0]:.3f} | "
            f"{loo['post_first_keep_rows'][model][0]:.3f} |"
        )

    aware_any = sum(
        awareness.get(row.base.run_id, {}).get("awareness_label")
        not in {None, "no_obvious_awareness"}
        for row in rh
    )
    aware_shortcut = sum(
        awareness.get(row.base.run_id, {}).get("awareness_label")
        in SHORTCUT_AWARENESS
        for row in rh
    )
    explicit_invalid = sum(
        awareness.get(row.base.run_id, {}).get("awareness_label")
        == "explicit_invalidity_awareness"
        for row in rh
    )
    warns_or_rejects = sum(
        awareness.get(row.base.run_id, {}).get("rejects_or_warns_against_final_method")
        is True
        for row in rh
    )
    lines.extend(
        [
            "",
            "## Recognition among retained RH submissions",
            "",
            "These annotations cannot predict whether a run becomes RH because awareness was judged only for consensus-RH runs. They instead characterize what happens after a shortcut is found and retained.",
            "",
            "| Transcript characteristic among consensus-RH runs | Runs | Rate of consensus-RH denominator |",
            "|---|---:|---:|",
            f"| Any generalization or shortcut awareness | {aware_any} | {pct(aware_any, len(rh))} |",
            f"| Shortcut or invalidity awareness | {aware_shortcut} | {pct(aware_shortcut, len(rh))} |",
            f"| Explicitly calls the method invalid or leaky | {explicit_invalid} | {pct(explicit_invalid, len(rh))} |",
            f"| Rejects or warns against the final method | {warns_or_rejects} | {pct(warns_or_rejects, len(rh))} |",
            "",
            "## Interpretation for Reviewer 3 C3-C7",
            "",
            "1. Engagement is the eligibility boundary: no-op runs have little opportunity to discover or retain a shortcut, and mixing them into model comparisons materially changes Kimi's apparent rate.",
            "2. After engagement, the most stable observable characteristic is repeated provisional acceptance: RH runs contain more rows explicitly marked `keep` even within the same model, task, size, and prompt cell. This association survives every task and every leave-one-model-out check, but remains descriptive and partly dependent on logging conventions.",
            "3. Total experimentation and evaluator use are weaker and heterogeneous. Experiment count separates RH in t01/t02 but not t03, so it should not be presented as a universal mechanism.",
            "4. Recognition often fails to prevent retention. Many RH transcripts acknowledge generalization risk or the shortcut, so the behavioral failure can occur between recognition and the final keep decision.",
            "5. Model differences should be described as different observed funnels through engagement, search, recognition, and retention. The data do not establish why the underlying models differ internally, and scaffold/provider differences remain possible explanations.",
            "",
            "## Reproduction",
            "",
            "```bash",
            "cd /Users/pradyu/Projects/spar_project",
            "uv run python analyze_transcripts/observable_behavior_analysis.py",
            "```",
            "",
            f"Bootstrap replications: {bootstrap_samples:,}; random seed: {seed}.",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("--bootstrap-samples", type=int, default=10_000)
    parser.add_argument("--seed", type=int, default=20260712)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).with_name("observable_behavior_analysis_20260712.md"),
    )
    args = parser.parse_args()

    eval_root = args.repo_root / "reward-hacking-evals"
    judging_root = eval_root / "data" / "outputs" / "judging"
    cases_root = eval_root / "data" / "cases" / "make_datasets"
    awareness_path = (
        judging_root
        / "transcript_awareness_gemini_flash"
        / "transcript_awareness_openrouter.jsonl"
    )
    records = load_records(judging_root, cases_root)
    if len(records) != 1_258:
        raise RuntimeError(f"expected 1,258 canonical records, found {len(records)}")
    rows = enrich_records(records, cases_root)
    awareness = load_awareness(awareness_path)
    report = render_report(
        rows,
        awareness,
        bootstrap_samples=args.bootstrap_samples,
        seed=args.seed,
    )
    args.output.write_text(report, encoding="utf-8")
    print(f"wrote {args.output} ({len(rows)} canonical records)")


if __name__ == "__main__":
    main()
