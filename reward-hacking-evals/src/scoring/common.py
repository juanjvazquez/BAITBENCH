from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterator


def iter_jsonl_records(path: Path) -> Iterator[dict[str, Any]]:
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            stripped = line.strip()
            if not stripped:
                continue
            obj = json.loads(stripped)
            if obj.get("type") == "record":
                yield obj


def load_judge_records(path: Path) -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = {}
    for record in iter_jsonl_records(path):
        run_id = record.get("run_id")
        if isinstance(run_id, str):
            records[run_id] = record
    return records


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")
