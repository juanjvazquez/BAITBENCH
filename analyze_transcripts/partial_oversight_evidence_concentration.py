#!/usr/bin/env python3
"""Quantify where archived BAITBENCH reward-hacking evidence is concentrated.

This is an evidence-location analysis, not a human detection experiment.  It
uses the paper's canonical runs, restricts to runs labeled reward hacking by
both binary judges, and follows the judges' archived evidence references back
to submitted ``solution.py`` files and trajectory step ranges.
"""

from __future__ import annotations

import argparse
import json
import statistics
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

from evaluator_calls_and_judge_reasons import Record, load_records


@dataclass(frozen=True)
class Concentration:
    base: Record
    exploit_group: str
    solution_lines: int
    direct_cited_lines: int
    max_step: int
    cited_steps: int
    has_robust_evidence: bool
    has_solution_ref: bool
    has_trajectory_ref: bool

    @property
    def code_pct(self) -> float | None:
        if not self.solution_lines:
            return None
        return 100 * self.direct_cited_lines / self.solution_lines

    @property
    def trajectory_pct(self) -> float | None:
        if not self.max_step:
            return None
        return 100 * self.cited_steps / self.max_step


def percentile(values: list[float], p: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty percentile input")
    index = (len(ordered) - 1) * p
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    weight = index - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def summary(values: list[float]) -> str:
    if not values:
        return "NA"
    return (
        f"{statistics.median(values):.1f}% "
        f"[{percentile(values, 0.25):.1f}, {percentile(values, 0.75):.1f}]"
    )


def exploit_group(record: Record) -> str:
    forms = [str(record.claude.get("exploit_form")), str(record.gpt.get("exploit_form"))]
    if forms == ["direct", "direct"]:
        return "Both judges direct"
    if forms == ["indirect", "indirect"]:
        return "Both judges indirect"
    if "direct" in forms and "indirect" in forms:
        return "Judges differ"
    return "Other/unclear"


def refs(judgment: dict[str, object]):
    for claim in judgment.get("evidence") or []:
        if not isinstance(claim, dict):
            continue
        for ref in claim.get("refs") or []:
            if isinstance(ref, dict):
                yield claim, ref


def nonblank_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]


def matching_line_numbers(solution: str, quote: str) -> set[int]:
    """Return nonblank solution line numbers literally represented in a quote."""
    solution_lines = solution.splitlines()
    quote_lines = {line.strip() for line in quote.splitlines() if line.strip()}
    return {
        index
        for index, line in enumerate(solution_lines, start=1)
        if line.strip() and line.strip() in quote_lines
    }


def maximum_step(evidence: dict[str, object]) -> int:
    values: list[int] = []
    for key in ("reads", "writes", "evaluation_events"):
        for event in evidence.get(key) or []:
            if isinstance(event, dict) and isinstance(event.get("step"), int):
                values.append(event["step"])
    return max(values, default=0)


def analyze(record: Record, case: dict[str, object]) -> Concentration:
    evidence = case.get("evidence") or {}
    python_files = evidence.get("python_files") or {}
    solution = str(python_files.get("solution.py") or "")
    direct_lines: set[int] = set()
    trajectory_steps: set[int] = set()
    has_solution_ref = False
    has_trajectory_ref = False
    has_robust = False

    for judgment in (record.claude, record.gpt):
        form = str(judgment.get("exploit_form"))
        searchable = " ".join(
            [str(judgment.get("short_analysis") or "")]
            + [str(claim.get("text") or "") for claim in judgment.get("evidence") or [] if isinstance(claim, dict)]
        ).lower()
        if any(term in searchable for term in ("robust", "generalization gap", "hidden split")):
            has_robust = True
        for _claim, ref in refs(judgment):
            file = ref.get("file")
            if file == "solution.py":
                has_solution_ref = True
                # Only count solution citations from a judge calling the exploit
                # direct. Indirect judgments often cite clean code as exculpatory
                # evidence, which is not a suspicious-code footprint.
                if form == "direct" and isinstance(ref.get("quote"), str):
                    direct_lines.update(matching_line_numbers(solution, ref["quote"]))
            start = ref.get("step")
            end = ref.get("step_end")
            if isinstance(start, int):
                has_trajectory_ref = True
                stop = end if isinstance(end, int) and end >= start else start
                trajectory_steps.update(range(start, stop + 1))

    return Concentration(
        base=record,
        exploit_group=exploit_group(record),
        solution_lines=len(nonblank_lines(solution)),
        direct_cited_lines=len(direct_lines),
        max_step=maximum_step(evidence),
        cited_steps=len(trajectory_steps),
        has_robust_evidence=has_robust,
        has_solution_ref=has_solution_ref,
        has_trajectory_ref=has_trajectory_ref,
    )


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100*n/d:.1f}%)" if d else "0/0"


def render(rows: list[Concentration]) -> str:
    groups: dict[str, list[Concentration]] = defaultdict(list)
    for row in rows:
        groups[row.exploit_group].append(row)

    lines = [
        "# Partial-oversight evidence concentration in canonical BAITBENCH RH runs",
        "",
        "## Cohort and interpretation",
        "",
        f"The cohort contains **{len(rows)} canonical runs that both binary judges labeled reward hacking**. "
        "This analysis measures where the archived judges pointed for evidence. It does not estimate a human reviewer’s probability of detecting reward hacking.",
        "",
        "Exploit form is reported as judge agreement rather than forced into a single label. This matters because the judges often differed on whether the same behavior was direct or indirect.",
        "",
        "## Evidence channel by exploit form",
        "",
        "| Exploit-form agreement | Runs | Any `solution.py` reference | Any trajectory-step reference | Robust/gap invoked |",
        "|---|---:|---:|---:|---:|",
    ]
    order = ["Both judges direct", "Judges differ", "Both judges indirect", "Other/unclear"]
    for name in order:
        group = groups.get(name, [])
        if not group:
            continue
        lines.append(
            f"| {name} | {len(group)} | {pct(sum(r.has_solution_ref for r in group),len(group))} "
            f"| {pct(sum(r.has_trajectory_ref for r in group),len(group))} "
            f"| {pct(sum(r.has_robust_evidence for r in group),len(group))} |"
        )

    lines += [
        "",
        "A `solution.py` reference is not automatically inculpatory: for indirect judgments, judges often cite clean-looking code to explain why the exploit is indirect. The next table therefore counts suspicious-code footprint only from judges that classified the exploit as direct.",
        "",
        "## Concentration within the reviewed material",
        "",
        "| Group | Runs with measurable footprint | Median percentage [IQR] |",
        "|---|---:|---:|",
    ]
    direct_eligible = [r for r in rows if r.exploit_group in {"Both judges direct", "Judges differ"} and r.code_pct is not None]
    direct_positive = [r for r in direct_eligible if r.direct_cited_lines]
    trajectory_positive = [r for r in rows if r.has_trajectory_ref and r.trajectory_pct is not None]
    lines += [
        f"| Nonblank `solution.py` lines literally cited by a direct-form judge | {len(direct_positive)}/{len(direct_eligible)} | {summary([r.code_pct for r in direct_positive if r.code_pct is not None])} |",
        f"| Interaction steps covered by archived trajectory evidence ranges | {len(trajectory_positive)}/{len(rows)} | {summary([r.trajectory_pct for r in trajectory_positive if r.trajectory_pct is not None])} |",
        "",
        "The step percentage uses the largest archived interaction step as the denominator and the union of judge-cited step ranges as the numerator. It is an oversight-burden proxy, not a word or token percentage. Step ranges can contain several actions, so it should not be interpreted as an exact fraction of transcript text.",
        "",
        "### Concentration by exploit-form agreement",
        "",
        "| Exploit-form agreement | Suspicious-code footprint, median [IQR] | Cited trajectory-step percentage, median [IQR] |",
        "|---|---:|---:|",
    ]
    for name in ("Both judges direct", "Judges differ", "Both judges indirect"):
        group = groups[name]
        code_values = [r.code_pct for r in group if r.code_pct is not None and r.direct_cited_lines]
        trajectory_values = [r.trajectory_pct for r in group if r.trajectory_pct is not None and r.has_trajectory_ref]
        lines.append(f"| {name} | {summary(code_values)} | {summary(trajectory_values)} |")
    lines += [
        "",
        "## By task family",
        "",
        "| Task | Consensus RH runs | Both direct | Mixed direct/indirect | Both indirect | Median cited trajectory-step percentage [IQR] |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for task in ("t01", "t02", "t03"):
        subset = [r for r in rows if r.base.task == task]
        vals = [r.trajectory_pct for r in subset if r.has_trajectory_ref and r.trajectory_pct is not None]
        lines.append(
            f"| {task.upper()} | {len(subset)} | {sum(r.exploit_group=='Both judges direct' for r in subset)} "
            f"| {sum(r.exploit_group=='Judges differ' for r in subset)} "
            f"| {sum(r.exploit_group=='Both judges indirect' for r in subset)} | {summary(vals)} |"
        )

    # Auditable examples: largest direct footprint and smallest indirect
    # trajectory footprint with a cited trajectory.
    direct_examples = sorted(direct_positive, key=lambda r: r.code_pct or 0, reverse=True)[:2]
    indirect_examples = sorted(
        [
            r
            for r in rows
            if r.exploit_group == "Both judges indirect"
            and r.has_trajectory_ref
            and r.trajectory_pct is not None
        ],
        key=lambda r: r.trajectory_pct or 0,
    )[:2]
    lines += [
        "",
        "## Auditable examples",
        "",
        "| Role | Run ID | Task | Evidence concentration |",
        "|---|---|---:|---|",
    ]
    for row in direct_examples:
        lines.append(
            f"| Direct, code-visible | `{row.base.run_id}` | {row.base.task.upper()} | "
            f"{row.direct_cited_lines}/{row.solution_lines} nonblank solution lines cited by a direct-form judge ({row.code_pct:.1f}%) |"
        )
    for row in indirect_examples:
        lines.append(
            f"| Indirect, trajectory-dependent | `{row.base.run_id}` | {row.base.task.upper()} | "
            f"{row.cited_steps}/{row.max_step} interaction steps covered by judge ranges ({row.trajectory_pct:.1f}%); robust/gap evidence={row.has_robust_evidence} |"
        )

    lines += [
        "",
        "## Main conclusion",
        "",
        "Partial oversight has two different burdens. Direct exploits can leave a small inspectable footprint in the submitted code. Indirect exploits can leave clean-looking final code, so code review alone is structurally insufficient; the relevant evidence is distributed across the model-selection trajectory and robust evaluation. The reported fractions quantify archived evidence concentration, not human detection accuracy.",
        "",
        "## Limitations",
        "",
        "- Evidence spans were produced by the same judges that assigned the labels, so this is an audit of label support rather than independent validation.",
        "- Literal quote matching understates code evidence when a judge paraphrases rather than quotes the exact line.",
        "- Trajectory step ranges are coarser than transcript tokens and may overstate the amount of text requiring review.",
        "- A human-oversight claim would require a blinded reviewer study with a specified review budget and interface.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1] / "reward-hacking-evals")
    parser.add_argument("--output", type=Path, default=Path(__file__).with_name("partial_oversight_evidence_concentration_20260712.md"))
    args = parser.parse_args()
    records = load_records(args.repo / "data/outputs/judging", args.repo / "data/cases/make_datasets")
    canonical = {
        json.loads(line)["run_id"]: json.loads(line)
        for line in (args.repo / "data/cases/make_datasets/v4_paper_canonical_1258_20260709.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    rh = [record for record in records if record.consensus_group == "both_rh"]
    missing = [record.run_id for record in rh if record.run_id not in canonical]
    if missing:
        raise RuntimeError(f"missing {len(missing)} canonical cases")
    rows = [analyze(record, canonical[record.run_id]) for record in rh]
    args.output.write_text(render(rows), encoding="utf-8")
    print(f"wrote {args.output} ({len(rows)} consensus-RH runs)")


if __name__ == "__main__":
    main()
