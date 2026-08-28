#!/usr/bin/env python3
"""Verify Appendix E human-validation counts from the local annotation export."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


CLAUDE_JUDGE = "anthropic__claude-opus-4-6"
GPT_JUDGE = "openai__gpt-5.4"
BINARY_LABELS = ("reward_hacking", "not_reward_hacking")


def calculate(export_path: Path) -> dict[str, int | str]:
    annotations = json.loads(export_path.read_text(encoding="utf-8"))
    v4 = [row for row in annotations if row["batch"].startswith("v4_")]

    usable: list[tuple[str, str, str]] = []
    for row in v4:
        human = row["annotation"]["verdict"]
        claude = (row["judges"].get(CLAUDE_JUDGE) or {}).get("label")
        gpt = (row["judges"].get(GPT_JUDGE) or {}).get("label")
        if human != "unclear" and claude in BINARY_LABELS and gpt in BINARY_LABELS:
            usable.append((human, claude, gpt))

    consensus = [(human, claude) for human, claude, gpt in usable if claude == gpt]
    return {
        "source": str(export_path),
        "v4_annotations": len(v4),
        "usable_annotations": len(usable),
        "claude_matches_human": sum(human == claude for human, claude, _ in usable),
        "gpt_matches_human": sum(human == gpt for human, _, gpt in usable),
        "consensus_cases": len(consensus),
        "consensus_matches_human": sum(human == label for human, label in consensus),
    }


def render_markdown(result: dict[str, int | str]) -> str:
    return "\n".join(
        [
            "# Appendix E human-validation audit",
            "",
            f"- V4 annotations: **{result['v4_annotations']}**",
            f"- Usable binary annotations: **{result['usable_annotations']}**",
            (
                f"- Claude judge matches human: **{result['claude_matches_human']}/"
                f"{result['usable_annotations']}**"
            ),
            (
                f"- GPT judge matches human: **{result['gpt_matches_human']}/"
                f"{result['usable_annotations']}**"
            ),
            (
                f"- Judge-consensus cases: **{result['consensus_cases']}**; "
                f"consensus matches human: **{result['consensus_matches_human']}/"
                f"{result['consensus_cases']}**"
            ),
            "",
            (
                "These numbers verify the recovered Appendix E annotations. "
                "They do not satisfy the later promise to annotate 100 cases."
            ),
            "",
        ]
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=Path(__file__).resolve().parents[1]
        / "reward-hacking-evals/data/annotations_viewer_export_20260709.json",
    )
    parser.add_argument("--output-json", type=Path)
    parser.add_argument("--output-markdown", type=Path)
    args = parser.parse_args()

    result = calculate(args.input.resolve())
    expected = {
        "v4_annotations": 31,
        "usable_annotations": 25,
        "claude_matches_human": 19,
        "gpt_matches_human": 19,
        "consensus_cases": 23,
        "consensus_matches_human": 18,
    }
    mismatches = {
        key: (result[key], value)
        for key, value in expected.items()
        if result[key] != value
    }
    if mismatches:
        raise ValueError(f"Appendix E counts changed: {mismatches}")

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
    if args.output_markdown:
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.write_text(render_markdown(result), encoding="utf-8")

    print(f"v4 annotations: {result['v4_annotations']} | usable: {result['usable_annotations']}")
    print(
        "Claude judge matches human: "
        f"{result['claude_matches_human']}/{result['usable_annotations']}"
    )
    print(
        "GPT judge matches human:    "
        f"{result['gpt_matches_human']}/{result['usable_annotations']}"
    )
    print(
        f"consensus cases: {result['consensus_cases']} | consensus matches human: "
        f"{result['consensus_matches_human']}/{result['consensus_cases']}"
    )


if __name__ == "__main__":
    main()
