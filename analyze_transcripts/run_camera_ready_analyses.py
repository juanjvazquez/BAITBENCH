#!/usr/bin/env python3
"""Run every local BAITBENCH camera-ready analysis and audit the results.

The bundle is deliberately offline: it reads archived cases, judgments, and
annotations and never calls a model provider or modifies an external service.
All generated files go into a separate output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANALYSIS_ROOT = PROJECT_ROOT / "analyze_transcripts"
EVAL_ROOT = PROJECT_ROOT / "reward-hacking-evals"
PAPER_TABLES = (
    "task-rates",
    "model-rates",
    "engagement-rates",
    "model-prompt-rates",
    "validity-reduction",
    "judge-agreement",
    "robustness-coverage",
    "robust-gap-summary",
    "task-degradation",
)
_INPUT_METADATA_CACHE: list[dict[str, Any]] | None = None


@dataclass
class Task:
    name: str
    command: list[str]
    cwd: Path
    stdout_artifact: Path | None = None


@dataclass
class TaskResult:
    name: str
    command: list[str]
    cwd: str
    return_code: int
    duration_seconds: float
    stdout_log: str
    stderr_log: str
    stdout_artifact: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ANALYSIS_ROOT / "generated/camera_ready",
    )
    parser.add_argument("--bootstrap-reps", type=int, default=10_000)
    parser.add_argument("--permutation-reps", type=int, default=20_000)
    parser.add_argument("--bayesian-draws", type=int, default=1_000_000)
    parser.add_argument("--seed", type=int, default=20_260_711)
    parser.add_argument(
        "--quick",
        action="store_true",
        help="Use smaller Monte Carlo counts for a fast structural smoke test.",
    )
    return parser.parse_args()


def task_list(
    output: Path,
    *,
    bootstrap_reps: int,
    permutation_reps: int,
    bayesian_draws: int,
    seed: int,
) -> list[Task]:
    py = sys.executable
    tasks: list[Task] = [
        Task(
            "neutral_judge",
            [
                py,
                str(ANALYSIS_ROOT / "neutral_judge_analysis.py"),
                "--repo",
                str(EVAL_ROOT),
                "--output-json",
                str(output / "neutral_judge.json"),
                "--output-markdown",
                str(output / "neutral_judge.md"),
            ],
            PROJECT_ROOT,
        ),
        Task(
            "cluster_bootstrap",
            [
                py,
                str(ANALYSIS_ROOT / "rebuttal_cluster_bootstrap.py"),
                "--repo",
                str(EVAL_ROOT),
                "--bootstrap-reps",
                str(bootstrap_reps),
                "--permutation-reps",
                str(permutation_reps),
                "--random-seed",
                str(seed),
                "--output-json",
                str(output / "cluster_bootstrap.json"),
            ],
            PROJECT_ROOT,
        ),
        Task(
            "judge_family_bias",
            [
                py,
                str(ANALYSIS_ROOT / "judge_family_cluster_bootstrap.py"),
                "--repo",
                str(EVAL_ROOT),
                "--bootstrap-reps",
                str(bootstrap_reps),
                "--random-seed",
                str(seed),
                "--output-json",
                str(output / "judge_family_bias.json"),
            ],
            PROJECT_ROOT,
        ),
        Task(
            "bayesian_harness",
            [
                py,
                str(ANALYSIS_ROOT / "bayesian_harness_equivalence.py"),
                "--draws",
                str(bayesian_draws),
                "--seed",
                str(seed),
                "--output",
                str(output / "bayesian_harness.json"),
            ],
            PROJECT_ROOT,
        ),
        Task(
            "validity_logging_ablation",
            [
                py,
                str(EVAL_ROOT / "scripts/analyze_validity_logging_ablation.py"),
                "--output",
                str(output / "validity_logging_ablation.md"),
            ],
            EVAL_ROOT,
        ),
        Task(
            "evaluator_calls_and_judge_reasons",
            [
                py,
                str(ANALYSIS_ROOT / "evaluator_calls_and_judge_reasons.py"),
                "--judging-root",
                str(EVAL_ROOT / "data/outputs/judging"),
                "--cases-root",
                str(EVAL_ROOT / "data/cases/make_datasets"),
                "--bootstrap-samples",
                str(bootstrap_reps),
                "--seed",
                str(seed + 1),
                "--output",
                str(output / "evaluator_calls_and_judge_reasons.md"),
            ],
            PROJECT_ROOT,
        ),
        Task(
            "observable_behavior",
            [
                py,
                str(ANALYSIS_ROOT / "observable_behavior_analysis.py"),
                "--repo-root",
                str(PROJECT_ROOT),
                "--bootstrap-samples",
                str(bootstrap_reps),
                "--seed",
                str(seed + 1),
                "--output",
                str(output / "observable_behavior.md"),
            ],
            PROJECT_ROOT,
        ),
        Task(
            "validity_prompt_behavior",
            [
                py,
                str(ANALYSIS_ROOT / "validity_prompt_behavior_analysis.py"),
                "--repo-root",
                str(PROJECT_ROOT),
                "--output",
                str(output / "validity_prompt_behavior.md"),
            ],
            PROJECT_ROOT,
        ),
        Task(
            "partial_oversight",
            [
                py,
                str(ANALYSIS_ROOT / "partial_oversight_evidence_concentration.py"),
                "--repo",
                str(EVAL_ROOT),
                "--output",
                str(output / "partial_oversight.md"),
            ],
            PROJECT_ROOT,
        ),
        Task(
            "qualitative_examples",
            [
                py,
                str(ANALYSIS_ROOT / "r3_c5_qualitative_examples.py"),
                "--output",
                str(output / "qualitative_examples.md"),
            ],
            PROJECT_ROOT,
        ),
        Task(
            "transcript_awareness",
            [
                py,
                str(EVAL_ROOT / "scripts/summarize_transcript_awareness_both_rh.py"),
                "--outputs-root",
                str(EVAL_ROOT / "data/outputs/judging"),
                "--awareness-jsonl",
                str(
                    EVAL_ROOT
                    / "data/outputs/judging/transcript_awareness_gemini_flash"
                    / "transcript_awareness_openrouter.jsonl"
                ),
                "--include-model-task",
            ],
            EVAL_ROOT,
            output / "transcript_awareness.md",
        ),
        Task(
            "appendix_e_human_validation",
            [
                py,
                str(ANALYSIS_ROOT / "verify_appendix_e.py"),
                "--input",
                str(EVAL_ROOT / "data/annotations_viewer_export_20260709.json"),
                "--output-json",
                str(output / "appendix_e_human_validation.json"),
                "--output-markdown",
                str(output / "appendix_e_human_validation.md"),
            ],
            PROJECT_ROOT,
        ),
    ]

    for table in PAPER_TABLES:
        tasks.append(
            Task(
                f"paper_table_{table}",
                [
                    py,
                    str(EVAL_ROOT / "scripts/make_datasets_paper_stats.py"),
                    "--outputs-root",
                    str(EVAL_ROOT / "data/outputs/judging"),
                    "--cases-root",
                    str(EVAL_ROOT / "data/cases/make_datasets"),
                    "--table",
                    table,
                    "--format",
                    "json",
                    "--ci",
                    "--ci-level",
                    "0.95",
                    "--bootstrap-samples",
                    str(bootstrap_reps),
                    "--bootstrap-seed",
                    str(seed + 2),
                ],
                EVAL_ROOT,
                output / "paper_tables" / f"{table}.json",
            )
        )
    return tasks


def run_task(task: Task, log_dir: Path) -> TaskResult:
    started = time.monotonic()
    process = subprocess.run(
        task.command,
        cwd=task.cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        env={**os.environ, "PYTHONHASHSEED": "0"},
    )
    duration = time.monotonic() - started
    stdout_log = log_dir / f"{task.name}.stdout.log"
    stderr_log = log_dir / f"{task.name}.stderr.log"
    stdout_log.write_text(process.stdout, encoding="utf-8")
    stderr_log.write_text(process.stderr, encoding="utf-8")
    if task.stdout_artifact is not None and process.returncode == 0:
        task.stdout_artifact.parent.mkdir(parents=True, exist_ok=True)
        task.stdout_artifact.write_text(process.stdout, encoding="utf-8")
    return TaskResult(
        name=task.name,
        command=task.command,
        cwd=str(task.cwd),
        return_code=process.returncode,
        duration_seconds=duration,
        stdout_log=str(stdout_log),
        stderr_log=str(stderr_log),
        stdout_artifact=str(task.stdout_artifact) if task.stdout_artifact else None,
    )


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def input_files() -> list[Path]:
    judging = EVAL_ROOT / "data/outputs/judging"
    files = [
        EVAL_ROOT / "data/cases/make_datasets/v4_paper_canonical_1258_20260709.jsonl",
        judging / "neutral_z-ai_glm-5.2_v4_20260709/judging_binary.jsonl",
        judging
        / "transcript_awareness_gemini_flash/transcript_awareness_openrouter.jsonl",
        EVAL_ROOT / "data/annotations_viewer_export_20260709.json",
        EVAL_ROOT / "data/cases/make_datasets/logging_ablation_20260711/cases.jsonl",
        judging / "logging_ablation_20260711_glm52_high/judging_binary.jsonl",
        EVAL_ROOT / "data/cases/make_datasets/opencode_vps_20260710_canonical182.jsonl",
        EVAL_ROOT
        / "data/cases/make_datasets/opencode_stratified_fill4_final66_cases.jsonl",
        judging
        / "opencode_vps_20260710_glm52_high/judging_binary_final182.jsonl",
        judging / "opencode_stratified_fill4_glm52_high_final66.jsonl",
    ]
    files.extend(sorted(judging.glob("v4_t*_local_*/judging_binary_*.jsonl")))
    files.extend(sorted(judging.glob("sonnet_v4_*/judging_binary_*.jsonl")))
    files.extend(sorted(judging.glob("gemini_flash_v4_*/judging_binary_*.jsonl")))
    return sorted(set(path.resolve() for path in files))


def git_state(path: Path) -> dict[str, Any]:
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    return {
        "path": str(path),
        "commit": commit.stdout.strip() if commit.returncode == 0 else None,
        "dirty": bool(status.stdout.strip()) if status.returncode == 0 else None,
        "status": status.stdout.splitlines() if status.returncode == 0 else [],
    }


def input_metadata() -> list[dict[str, Any]]:
    global _INPUT_METADATA_CACHE
    if _INPUT_METADATA_CACHE is None:
        _INPUT_METADATA_CACHE = []
        for file in input_files():
            _INPUT_METADATA_CACHE.append(
                {
                    "path": str(file),
                    "exists": file.is_file(),
                    "size_bytes": file.stat().st_size if file.is_file() else None,
                    "sha256": sha256(file) if file.is_file() else None,
                }
            )
    return _INPUT_METADATA_CACHE


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def require_text(path: Path, pattern: str, label: str) -> re.Match[str]:
    match = re.search(pattern, path.read_text(encoding="utf-8"), flags=re.MULTILINE)
    if match is None:
        raise ValueError(f"{label}: pattern not found in {path}: {pattern}")
    return match


def audit(output: Path, quick: bool) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []

    def exact(name: str, actual: Any, expected: Any) -> None:
        passed = actual == expected
        checks.append(
            {"name": name, "actual": actual, "expected": expected, "passed": passed}
        )

    def close(name: str, actual: float, expected: float, tolerance: float) -> None:
        passed = abs(actual - expected) <= tolerance
        checks.append(
            {
                "name": name,
                "actual": actual,
                "expected": expected,
                "tolerance": tolerance,
                "passed": passed,
            }
        )

    neutral = load_json(output / "neutral_judge.json")
    cluster = load_json(output / "cluster_bootstrap.json")
    family = load_json(output / "judge_family_bias.json")
    harness = load_json(output / "bayesian_harness.json")
    human = load_json(output / "appendix_e_human_validation.json")
    stats = cluster["statistics"]

    exact("canonical run count", cluster["coverage"]["canonical_runs"], 1_258)
    exact("task-size strata", cluster["coverage"]["strata"], 9)
    exact("dataset-instance clusters", cluster["coverage"]["dataset_instance_clusters"], 90)
    exact("paired prompt comparisons", cluster["coverage"]["paired_prompt_comparisons"], 628)
    close("pooled RH rate", stats["rate.pooled"]["estimate"], 0.5707472178060413, 1e-12)
    close(
        "exclude-no-signal RH rate",
        stats["rate.excluding_no_signal"]["estimate"],
        0.7750596658711217,
        1e-12,
    )
    close(
        "binary-consensus RH rate",
        stats["rate.consensus_only"]["estimate"],
        681 / 1_169,
        1e-12,
    )
    close(
        "pooled paired prompt effect",
        stats["prompt.pooled"]["estimate"],
        0.06210191082802548,
        1e-12,
    )

    exact("neutral canonical coverage", neutral["coverage"]["matched_canonical_runs"], 1_258)
    exact("neutral RH count", neutral["neutral"]["label_counts"]["reward_hacking"], 749)
    close(
        "neutral vs GPT agreement",
        neutral["comparisons"]["glm_vs_gpt"]["agreement"],
        0.9642,
        0.00006,
    )
    close(
        "neutral vs Claude agreement",
        neutral["comparisons"]["glm_vs_claude"]["agreement"],
        0.9348,
        0.00006,
    )

    close(
        "Claude-family excess judge gap",
        family["claude_family_effect"]["estimate"],
        -0.008201795109873102,
        1e-12,
    )
    close(
        "GPT-family excess judge gap",
        family["gpt_family_effect"]["estimate"],
        0.025131538223460228,
        1e-12,
    )

    gpt = harness["models"]["gpt-5.4"]
    sonnet = harness["models"]["claude-sonnet-4-6"]
    exact("GPT harness pairs", gpt["n"], 112)
    exact("Sonnet harness pairs", sonnet["n"], 97)
    exact("GPT minimum pairs per stratum", gpt["min_per_stratum"], 4)
    exact("Sonnet minimum pairs per stratum", sonnet["min_per_stratum"], 4)
    exact("GPT OpenCode RH count", gpt["raw_paired"]["opencode_reward_hacking"], 65)
    exact("GPT native RH count", gpt["raw_paired"]["native_reward_hacking"], 57)
    exact("Sonnet OpenCode RH count", sonnet["raw_paired"]["opencode_reward_hacking"], 65)
    exact("Sonnet native RH count", sonnet["raw_paired"]["native_reward_hacking"], 69)
    if not quick:
        close("GPT standardized harness effect", gpt["jeffreys"]["mean"], 0.0472, 0.0002)
        close(
            "Sonnet standardized harness effect",
            sonnet["jeffreys"]["mean"],
            -0.0416,
            0.0002,
        )

    exact("Appendix E V4 annotations", human["v4_annotations"], 31)
    exact("Appendix E usable annotations", human["usable_annotations"], 25)
    exact("Appendix E consensus matches", human["consensus_matches_human"], 18)

    logging = output / "validity_logging_ablation.md"
    require_text(logging, r"35/71 = 49\.3%", "logging original matched rate")
    require_text(logging, r"40/71 = 56\.3%", "logging ablation rate")
    require_text(logging, r"35/63 = 55\.6%", "logging engaged original rate")
    require_text(logging, r"40/71 = 56\.3%", "logging engaged ablation rate")

    behavior = output / "observable_behavior.md"
    require_text(
        behavior,
        r"Rows marked keep \| 0\.759 \[0\.",
        "observable keep-row AUC",
    )
    prompt = output / "validity_prompt_behavior.md"
    require_text(prompt, r"RH under both prompts \| 282 \|", "persistent RH pairs")
    require_text(prompt, r"Non-RH under both prompts \| 188 \|", "persistent non-RH pairs")
    require_text(prompt, r"Baseline RH, validity non-RH \| 55 \|", "prevented pairs")
    require_text(prompt, r"Baseline non-RH, validity RH \| 27 \|", "reverse pairs")

    oversight = output / "partial_oversight.md"
    require_text(oversight, r"681 canonical runs", "oversight cohort")
    require_text(oversight, r"409/409 \| 8\.7%", "direct code footprint")
    awareness = output / "transcript_awareness.md"
    require_text(awareness, r"Total both-RH canonical runs: `681`", "awareness cohort")
    require_text(awareness, r"`387/681` = 56\.8%", "shortcut awareness")

    paper_table_counts: dict[str, int] = {}
    for table in PAPER_TABLES:
        rows = load_json(output / "paper_tables" / f"{table}.json")
        if not isinstance(rows, list) or not rows:
            raise ValueError(f"paper table {table} is empty or not a list")
        paper_table_counts[table] = len(rows)

    result = {
        "neutral_judge": neutral,
        "cluster_bootstrap": cluster,
        "judge_family_bias": family,
        "bayesian_harness": harness,
        "appendix_e_human_validation": human,
        "paper_table_row_counts": paper_table_counts,
    }
    return result, checks


def pct(value: float, digits: int = 1) -> str:
    return f"{100 * value:.{digits}f}%"


def pp(value: float, digits: int = 1) -> str:
    return f"{100 * value:+.{digits}f} pp"


def interval(row: dict[str, float], low: str, high: str) -> str:
    return f"[{100 * row[low]:+.1f}, {100 * row[high]:+.1f}] pp"


def render_summary(
    results: dict[str, Any],
    checks: list[dict[str, Any]],
    *,
    quick: bool,
) -> str:
    cluster = results["cluster_bootstrap"]
    stats = cluster["statistics"]
    neutral = results["neutral_judge"]
    family = results["judge_family_bias"]
    harness = results["bayesian_harness"]
    human = results["appendix_e_human_validation"]
    failed = [check for check in checks if not check["passed"]]

    lines = [
        "# BAITBENCH camera-ready local analysis bundle",
        "",
        (
            f"Run mode: **{'quick structural smoke test' if quick else 'full'}**. "
            f"Audit checks: **{len(checks) - len(failed)}/{len(checks)} passed**."
        ),
        "",
        "## Headline and task-family sensitivity",
        "",
        "| Quantity | Estimate | 95% cluster-bootstrap interval |",
        "|---|---:|---:|",
    ]
    for label, key in (
        ("Pooled", "rate.pooled"),
        ("Entity overlap", "rate.task.Entity overlap"),
        ("Near-duplicate leakage", "rate.task.Near-duplicate leakage"),
        ("No-signal classification", "rate.task.No-signal classification"),
        ("Excluding no-signal", "rate.excluding_no_signal"),
        ("Binary judge consensus only", "rate.consensus_only"),
    ):
        row = stats[key]
        lines.append(
            f"| {label} | {pct(row['estimate'])} | "
            f"[{pct(row['ci_95_lower'])}, {pct(row['ci_95_upper'])}] |"
        )

    lines.extend(
        [
            "",
            (
                "The binary-consensus denominator excludes eight cases where both "
                "judges returned `unclear`; the correct sensitivity estimate is "
                f"681/1,169 = {pct(681 / 1_169)}."
            ),
            "",
            "## Paired validity-prompt effects",
            "",
            "| Model | Baseline minus validity | 95% cluster-bootstrap interval | Sign-flip p |",
            "|---|---:|---:|---:|",
        ]
    )
    model_names = {
        "claude-opus-4-6": "Claude Opus 4.6",
        "claude-sonnet-4-6": "Claude Sonnet 4.6",
        "gpt-5.4": "GPT-5.4",
        "kimi-k2.5": "Kimi K2.5",
        "gemini-3.1-pro-preview": "Gemini 3.1 Pro",
        "gemini-3-flash-preview": "Gemini 3 Flash",
        "deepseek-v4-pro": "DeepSeek V4 Pro",
    }
    for model, name in model_names.items():
        row = stats[f"prompt.model.{model}"]
        lines.append(
            f"| {name} | {pp(row['estimate'])} | "
            f"{interval(row, 'ci_95_lower', 'ci_95_upper')} | "
            f"{row['cluster_sign_flip_p']:.4f} |"
        )
    pooled = stats["prompt.pooled"]
    lines.append(
        f"| **Pooled** | **{pp(pooled['estimate'])}** | "
        f"**{interval(pooled, 'ci_95_lower', 'ci_95_upper')}** | "
        f"**{pooled['cluster_sign_flip_p']:.4f}** |"
    )

    lines.extend(
        [
            "",
            "## Neutral judging and same-family bias",
            "",
            (
                f"GLM-5.2 labeled **749/1,258 "
                f"({pct(neutral['neutral']['reward_hacking_rate'], 2)})** cases RH."
            ),
            "",
            "| Comparison | Estimate | 95% interval |",
            "|---|---:|---:|",
        ]
    )
    for label, key in (
        ("Claude-family excess judge gap", "claude_family_effect"),
        ("GPT-family excess judge gap", "gpt_family_effect"),
    ):
        row = family[key]
        lines.append(
            f"| {label} | {pp(row['estimate'], 2)} | "
            f"{interval(row, 'ci_95_lower', 'ci_95_upper')} |"
        )
    lines.extend(
        [
            "",
            "| Judge pair | Agreement | Cohen's κ |",
            "|---|---:|---:|",
        ]
    )
    for key, label in (
        ("glm_vs_gpt", "GLM-5.2 vs GPT-5.4"),
        ("glm_vs_claude", "GLM-5.2 vs Claude Opus 4.6"),
        ("claude_vs_gpt", "Claude Opus 4.6 vs GPT-5.4"),
    ):
        row = neutral["comparisons"][key]
        lines.append(
            f"| {label} | {pct(row['agreement'], 2)} | {row['cohen_kappa']:.4f} |"
        )

    lines.extend(
        [
            "",
            "## Execution-pathway control",
            "",
            "| Model | Pairs | Native RH | OpenCode RH | Equal-stratum posterior mean | 95% credible interval |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for model, label in (
        ("gpt-5.4", "GPT-5.4"),
        ("claude-sonnet-4-6", "Claude Sonnet 4.6"),
    ):
        row = harness["models"][model]
        raw = row["raw_paired"]
        posterior = row["jeffreys"]
        lines.append(
            f"| {label} | {row['n']} | "
            f"{raw['native_reward_hacking']}/{row['n']} ({pct(raw['native_rate'])}) | "
            f"{raw['opencode_reward_hacking']}/{row['n']} ({pct(raw['opencode_rate'])}) | "
            f"{pp(posterior['mean'])} | "
            f"[{100 * posterior['ci95_low']:+.1f}, "
            f"{100 * posterior['ci95_high']:+.1f}] pp |"
        )

    lines.extend(
        [
            "",
            "## Other reproduced analyses",
            "",
            "- Validity-logging matched rates: original 35/71 (49.3%), logging 40/71 (56.3%); conditional on recorded engagement, 35/63 (55.6%) versus 40/71 (56.3%).",
            "- Observable behavior: the strongest within-cell correlate is the count of experiment rows marked `keep`; see `observable_behavior.md` for the AUC, cluster-bootstrap interval, and stability checks.",
            "- Prompt behavior: 552 paired consensus comparisons comprise 282 RH→RH, 188 non-RH→non-RH, 55 RH→non-RH, and 27 non-RH→RH pairs.",
            "- Partial oversight: 681 consensus-RH cases were audited; direct-form evidence cited a median 8.7% of nonblank submitted-code lines across 409 measurable cases.",
            "- Transcript awareness: 387/681 (56.8%) consensus-RH runs explicitly recognized the shortcut or its invalidity.",
            (
                f"- Recovered human validation: {human['usable_annotations']} usable "
                f"binary cases, with {human['consensus_matches_human']}/"
                f"{human['consensus_cases']} consensus judgments matching the human label. "
                "This does not complete the later 100-case annotation commitment."
            ),
            "",
            "## Generated artifacts",
            "",
            "- Machine-readable consolidated results: `camera_ready_results.json`",
            "- Task execution and checksums: `run_manifest.json`",
            "- Per-analysis reports: the Markdown and JSON files beside this report",
            "- Paper-statistics tables: `paper_tables/*.json`",
            "- Captured standard output and errors: `logs/`",
            "",
        ]
    )
    if failed:
        lines.extend(
            [
                "## Failed audit checks",
                "",
                *[
                    f"- `{row['name']}`: actual `{row['actual']}`, expected `{row['expected']}`"
                    for row in failed
                ],
                "",
            ]
        )
    return "\n".join(lines)


def write_manifest(
    path: Path,
    *,
    args: argparse.Namespace,
    task_results: list[TaskResult],
    checks: list[dict[str, Any]] | None = None,
) -> None:
    manifest = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "quick" if args.quick else "full",
        "parameters": {
            "bootstrap_reps": args.bootstrap_reps,
            "permutation_reps": args.permutation_reps,
            "bayesian_draws": args.bayesian_draws,
            "seed": args.seed,
        },
        "python": sys.version,
        "git": [
            git_state(ANALYSIS_ROOT),
            git_state(EVAL_ROOT),
        ],
        "inputs": input_metadata(),
        "tasks": [asdict(result) for result in task_results],
        "checks": checks or [],
    }
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if args.quick:
        args.bootstrap_reps = min(args.bootstrap_reps, 500)
        args.permutation_reps = min(args.permutation_reps, 1_000)
        args.bayesian_draws = min(args.bayesian_draws, 100_000)

    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    log_dir = output / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    (output / "paper_tables").mkdir(parents=True, exist_ok=True)

    tasks = task_list(
        output,
        bootstrap_reps=args.bootstrap_reps,
        permutation_reps=args.permutation_reps,
        bayesian_draws=args.bayesian_draws,
        seed=args.seed,
    )
    task_results: list[TaskResult] = []
    print(f"Writing bundle to {output}")
    for index, task in enumerate(tasks, start=1):
        print(f"[{index:02d}/{len(tasks):02d}] {task.name}", flush=True)
        result = run_task(task, log_dir)
        task_results.append(result)
        state = "ok" if result.return_code == 0 else f"FAILED ({result.return_code})"
        print(f"           {state} in {result.duration_seconds:.1f}s", flush=True)
        write_manifest(
            output / "run_manifest.json",
            args=args,
            task_results=task_results,
        )

    failed_tasks = [result for result in task_results if result.return_code != 0]
    if failed_tasks:
        names = ", ".join(result.name for result in failed_tasks)
        print(f"Analysis tasks failed: {names}", file=sys.stderr)
        return 1

    results, checks = audit(output, args.quick)
    (output / "camera_ready_results.json").write_text(
        json.dumps(results, indent=2) + "\n", encoding="utf-8"
    )
    (output / "camera_ready_results.md").write_text(
        render_summary(results, checks, quick=args.quick), encoding="utf-8"
    )
    write_manifest(
        output / "run_manifest.json",
        args=args,
        task_results=task_results,
        checks=checks,
    )

    failed_checks = [check for check in checks if not check["passed"]]
    if failed_checks:
        print(
            f"{len(failed_checks)} audit checks failed; see camera_ready_results.md",
            file=sys.stderr,
        )
        return 2
    print(f"All {len(checks)} audit checks passed.")
    print(f"Summary: {output / 'camera_ready_results.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
