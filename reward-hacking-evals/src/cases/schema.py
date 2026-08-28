from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CASE_KIND = "reward_hacking_case"
CASE_SCHEMA_VERSION = "reward_hacking_case_v1"


def make_case_record(
    *,
    run_id: str,
    source_project: str,
    source_variant: str,
    metadata: dict[str, Any],
    raw_refs: dict[str, Any],
    package_counts: dict[str, Any],
    evidence: dict[str, Any],
    derived_signals: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "record",
        "kind": CASE_KIND,
        "schema_version": CASE_SCHEMA_VERSION,
        "run_id": run_id,
        "source_project": source_project,
        "source_variant": source_variant,
        "metadata": metadata,
        "raw_refs": raw_refs,
        "package_counts": package_counts,
        "evidence": evidence,
        "derived_signals": derived_signals,
    }


def is_case_record(obj: dict[str, Any]) -> bool:
    return (
        isinstance(obj, dict)
        and obj.get("kind") == CASE_KIND
        and obj.get("schema_version") == CASE_SCHEMA_VERSION
        and isinstance(obj.get("run_id"), str)
        and isinstance(obj.get("source_project"), str)
    )


def case_dump_payload(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": record.get("schema_version"),
        "run_id": record.get("run_id"),
        "source_project": record.get("source_project"),
        "source_variant": record.get("source_variant"),
        "metadata": record.get("metadata", {}),
        "raw_refs": record.get("raw_refs", {}),
        "package_counts": record.get("package_counts", {}),
        "evidence": record.get("evidence", {}),
        "derived_signals": record.get("derived_signals", {}),
    }


def case_dump_text(record: dict[str, Any]) -> str:
    return json.dumps(case_dump_payload(record), ensure_ascii=True, indent=2)


def write_case_json(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=True, indent=2) + "\n", encoding="utf-8")


def append_case_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True))
        handle.write("\n")
