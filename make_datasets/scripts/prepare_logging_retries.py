#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path

from make_datasets.run_task import (
    Agent,
    ClaudeEffort,
    CodexReasoningEffort,
    prepare_run,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--agents", required=True)
    parser.add_argument("--num-hours", type=float, default=1.0)
    parser.add_argument("--retry-invalid", action="store_true")
    parser.add_argument("--exclude-manifest", type=Path, action="append", default=[])
    args = parser.parse_args()

    selected = {item.strip() for item in args.agents.split(",") if item.strip()}
    source = json.loads(args.manifest.read_text(encoding="utf-8"))
    already_retried: set[str] = set()
    for manifest_path in args.exclude_manifest:
        for retry in json.loads(manifest_path.read_text(encoding="utf-8")):
            retry_of = retry.get("retry_of")
            if retry_of:
                already_retried.add(str(retry_of))
    retries: list[dict[str, object]] = []
    for entry in source:
        if entry["agent"] not in selected:
            continue
        if str(entry["run_id"]) in already_retried:
            continue
        exit_path = Path(entry["run_dir"]) / "agent_exit_code.txt"
        if not exit_path.exists():
            continue
        if exit_path.read_text().strip() == "0":
            results_path = Path(entry["run_dir"]) / "workspace" / "results.tsv"
            lines = (
                results_path.read_text(encoding="utf-8", errors="replace").splitlines()
                if results_path.exists()
                else []
            )
            header = lines[0].split("\t") if lines else []
            is_invalid = len(lines) < 2 or len(header) < 6
            if not args.retry_invalid or not is_invalid:
                continue
        metadata = prepare_run(
            str(entry["task_id"]),
            agent=Agent(str(entry["agent"])),
            model=str(entry["model"]),
            codex_reasoning_effort=CodexReasoningEffort.medium,
            claude_effort=ClaudeEffort.medium,
            num_hours=args.num_hours,
            launch=False,
            overwrite=False,
        )
        retries.append(
            {
                **entry,
                "run_id": metadata.run_id,
                "run_dir": metadata.run_dir,
                "num_hours": metadata.num_hours,
                "retry_of": entry["run_id"],
            }
        )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(retries, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"prepared": len(retries), "output": str(args.output)}))


if __name__ == "__main__":
    main()
