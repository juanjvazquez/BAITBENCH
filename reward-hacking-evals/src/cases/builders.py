from __future__ import annotations

from pathlib import Path
from typing import Any

from .constants import AUTORESEARCH_CONTENT_LIMITS, MAKE_DATASETS_FAMILY_CONTEXT
from .schema import make_case_record


def truncate_text(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    head = max(1, limit // 2)
    tail = max(1, limit - head)
    return f"{text[:head].rstrip()}\n\n[... truncated ...]\n\n{text[-tail:].lstrip()}"


def build_make_datasets_case(
    evidence_record: dict[str, Any],
    robust_record: dict[str, Any] | None,
    *,
    evidence_ref: str,
    robust_ref: str | None,
) -> dict[str, Any]:
    manifest_entry = evidence_record.get("manifest_entry", {})
    family_code = str(manifest_entry.get("family_code", ""))
    task_context = {
        "run_id": evidence_record.get("run_id"),
        "task_id": manifest_entry.get("task_id"),
        "family_code": family_code,
        "family_context": MAKE_DATASETS_FAMILY_CONTEXT.get(family_code, ""),
        "prompt_condition": manifest_entry.get("prompt_condition"),
        "rows": manifest_entry.get("rows"),
        "seed": manifest_entry.get("seed"),
        "agent": manifest_entry.get("agent"),
        "model": manifest_entry.get("model"),
    }

    robust_evaluation: dict[str, Any] | None
    if robust_record is None:
        robust_evaluation = None
    else:
        # Prefer agent-reported test_score (from results.tsv keep rows). Fall
        # back to recomputed_test_score from prior `evaluate.py` re-run, which
        # was emitted by the visible_test_score_info pipeline (workspace
        # metrics.json / run.log fallbacks). This recovers ~167 cases that
        # had test_score=null in the agent-self-report but a populated
        # recomputed score on disk.
        test_score = robust_record.get("test_score")
        recomputed_test_score = robust_record.get("recomputed_test_score")
        if test_score is None and recomputed_test_score is not None:
            test_score = recomputed_test_score
        # If we just filled test_score from the recompute, derive
        # generalization_gap consistently.
        gap = robust_record.get("generalization_gap")
        robust_score = robust_record.get("robust_score")
        if gap is None and test_score is not None and robust_score is not None:
            metric = robust_record.get("metric")
            if metric == "accuracy":
                gap = test_score - robust_score
            elif metric == "rmse":
                gap = robust_score - test_score
        if robust_record.get("status") == "ok":
            robust_evaluation = {
                "status": "ok",
                "metric": robust_record.get("metric"),
                "test_score": test_score,
                "test_score_source": robust_record.get("test_score_source"),
                "recomputed_test_score": recomputed_test_score,
                "robust_score": robust_score,
                "generalization_gap": gap,
                "robust_metrics": robust_record.get("robust_metrics"),
            }
        else:
            robust_evaluation = {
                "status": "error",
                "metric": robust_record.get("metric"),
                "test_score": test_score,
                "test_score_source": robust_record.get("test_score_source"),
                "recomputed_test_score": recomputed_test_score,
                "robust_error": robust_record.get("robust_error"),
            }

    keep_rows = [
        row
        for row in evidence_record.get("trajectory", [])
        if isinstance(row, dict) and str(row.get("status", "")).strip().lower() == "keep"
    ]
    error_text = evidence_record.get("error")
    derived_signals = {
        "has_packaging_error": isinstance(error_text, str) and bool(error_text),
        "has_robust_error": robust_evaluation is not None and robust_evaluation.get("status") == "error",
        "generalization_gap": None if robust_evaluation is None else robust_evaluation.get("generalization_gap"),
        "keep_row_count": len(keep_rows),
        "evaluation_event_count": len(evidence_record.get("evaluation_events", [])),
        "write_file_count": len(evidence_record.get("write_files", [])),
    }

    metadata = {
        "run_id": evidence_record.get("run_id"),
        "agent": manifest_entry.get("agent"),
        "model": manifest_entry.get("model"),
        "prompt_condition": manifest_entry.get("prompt_condition"),
        "task_id": manifest_entry.get("task_id"),
        "family_code": family_code,
    }
    raw_refs = {
        "evidence_jsonl": evidence_ref,
        "robust_jsonl": robust_ref,
        "run_bundle": str(evidence_record.get("run_id", "")),
    }
    package_counts = {
        "python_file_count": len(evidence_record.get("python_files", {})),
        "written_file_count": len(evidence_record.get("written_files", {})),
        "trajectory_row_count": len(evidence_record.get("trajectory", [])),
        "evaluation_event_count": len(evidence_record.get("evaluation_events", [])),
        "read_event_count": len(evidence_record.get("reads", [])),
        "write_event_count": len(evidence_record.get("writes", [])),
    }
    evidence = {
        "task_context": task_context,
        "workspace_files": evidence_record.get("workspace_files", []),
        "agent_exit_code": evidence_record.get("agent_exit_code"),
        "read_files": evidence_record.get("read_files", []),
        "write_files": evidence_record.get("write_files", []),
        "reads": evidence_record.get("reads", []),
        "writes": evidence_record.get("writes", []),
        "trajectory": evidence_record.get("trajectory", []),
        "evaluation_events": evidence_record.get("evaluation_events", []),
        "written_files": evidence_record.get("written_files", {}),
        "python_files": evidence_record.get("python_files", {}),
        "robust_evaluation": robust_evaluation,
        "packaging_error": error_text,
    }

    return make_case_record(
        run_id=str(evidence_record.get("run_id", "")),
        source_project="make_datasets",
        source_variant=family_code or "unknown_family",
        metadata=metadata,
        raw_refs=raw_refs,
        package_counts=package_counts,
        evidence=evidence,
        derived_signals=derived_signals,
    )


def build_autoresearch_case(
    *,
    run_id: str,
    source_kind: str,
    run_root: str,
    workspace_dir: str,
    transcript_path: str | None,
    run_json_path: str | None,
    run_data: dict[str, Any] | None,
    workspace_inventory: list[dict[str, Any]],
    workspace_contents: dict[str, str],
    results_summary: dict[str, Any],
    run_log_summary: dict[str, Any],
    transcript_summary: dict[str, Any],
    events: list[dict[str, Any]],
    derived_signals: dict[str, Any],
) -> dict[str, Any]:
    metadata_block = run_data.get("metadata", {}) if isinstance(run_data, dict) else {}
    prompt_text = run_data.get("prompt") if isinstance(run_data, dict) else None

    filtered_contents: dict[str, str] = {}
    omitted_large_files: list[dict[str, Any]] = []
    for item in workspace_inventory:
        relative_path = str(item["relative_path"])
        content = workspace_contents.get(relative_path)
        if content is None:
            continue
        limit = AUTORESEARCH_CONTENT_LIMITS.get(Path(relative_path).name)
        if limit is None:
            filtered_contents[relative_path] = content
            continue
        filtered_contents[relative_path] = truncate_text(content, limit)
        if len(content) > limit:
            omitted_large_files.append(
                {
                    "relative_path": relative_path,
                    "original_size_chars": len(content),
                    "truncated_to_chars": len(filtered_contents[relative_path]),
                }
            )

    metadata = {
        "run_id": run_id,
        "source_kind": source_kind,
        "agent": metadata_block.get("agent"),
        "model": metadata_block.get("model"),
        "status": metadata_block.get("status"),
        "date": metadata_block.get("date"),
        "start_time": metadata_block.get("start_time"),
        "start_time_iso": metadata_block.get("start_time_iso"),
        "wall_time_seconds": metadata_block.get("wall_time_seconds"),
        "prompt": prompt_text,
    }
    raw_refs = {
        "run_root": run_root,
        "workspace_dir": workspace_dir,
        "transcript_path": transcript_path,
        "run_json_path": run_json_path,
    }
    package_counts = {
        "workspace_file_count": len(workspace_inventory),
        "results_row_count": results_summary.get("row_count", 0),
        "transcript_event_count": transcript_summary.get("event_count", 0),
        "protected_mutation_count": len(derived_signals.get("protected_mutation_paths", [])),
        "training_command_count": len(transcript_summary.get("training_commands", [])),
    }
    evidence = {
        "sanitized_run_data": run_data,
        "workspace_inventory": workspace_inventory,
        "workspace_contents": filtered_contents,
        "omitted_large_files": omitted_large_files,
        "results_summary": results_summary,
        "run_log_summary": run_log_summary,
        "transcript_summary": transcript_summary,
        "events": events,
    }

    return make_case_record(
        run_id=run_id,
        source_project="autoresearch",
        source_variant=source_kind,
        metadata=metadata,
        raw_refs=raw_refs,
        package_counts=package_counts,
        evidence=evidence,
        derived_signals=derived_signals,
    )


def build_posttrainbench_case(
    *,
    run_id: str,
    source_variant: str,
    metadata: dict[str, Any],
    raw_refs: dict[str, Any],
    package_counts: dict[str, Any],
    evidence: dict[str, Any],
    derived_signals: dict[str, Any],
) -> dict[str, Any]:
    return make_case_record(
        run_id=run_id,
        source_project="posttrainbench",
        source_variant=source_variant,
        metadata=metadata,
        raw_refs=raw_refs,
        package_counts=package_counts,
        evidence=evidence,
        derived_signals=derived_signals,
    )
