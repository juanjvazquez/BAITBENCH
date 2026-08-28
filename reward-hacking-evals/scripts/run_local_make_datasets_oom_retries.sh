#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

resolve_repo_make_datasets() {
  local candidate
  if [[ -n "${MAKE_DATASETS_SOURCE_ROOT:-}" && -d "${MAKE_DATASETS_SOURCE_ROOT}" ]]; then
    printf '%s\n' "$(cd "${MAKE_DATASETS_SOURCE_ROOT}" && pwd)"
    return 0
  fi
  for candidate in \
    "$ROOT/../repo_make_datasets" \
    "$ROOT/../2026-04-20-i-need-to-evaluate-all-the/repo_make_datasets"
  do
    if [[ -d "$candidate" ]]; then
      printf '%s\n' "$(cd "$candidate" && pwd)"
      return 0
    fi
  done
  candidate="$(find "$ROOT/.." -maxdepth 2 -type d -name repo_make_datasets -print -quit 2>/dev/null || true)"
  if [[ -n "$candidate" && -d "$candidate" ]]; then
    printf '%s\n' "$(cd "$candidate" && pwd)"
    return 0
  fi
  return 1
}

MANIFEST="$ROOT/data/raw/make_datasets/analysis_exports/synthetic_leakage_batch_manifest_540_latest.json"
EVIDENCE="$ROOT/data/raw/make_datasets/analysis_exports/synthetic_540_claude_evidence.jsonl"
REMOTE_BASE="$ROOT/data/raw/make_datasets/run_bundles"
OUT_DIR="$ROOT/data/outputs/make_datasets"
TMP_ROOT="$ROOT/data/outputs/make_datasets/.tmp/robust_eval"
SUBSET_MANIFEST="$OUT_DIR/claude_oom_retry_manifest.json"
OUT_JSONL="$OUT_DIR/claude_oom_retry_robust_scores.jsonl"

mkdir -p "$OUT_DIR" "$TMP_ROOT"

MAKE_DATASETS_SOURCE="$(resolve_repo_make_datasets || true)"
if [[ -z "$MAKE_DATASETS_SOURCE" ]]; then
  echo "Could not locate repo_make_datasets." >&2
  echo "Set MAKE_DATASETS_SOURCE_ROOT=/abs/path/to/repo_make_datasets and retry." >&2
  exit 1
fi

python3 - "$MANIFEST" "$SUBSET_MANIFEST" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
out_path = Path(sys.argv[2])
target_ids = {
    "claude_claude-opus-4-6_regression_task_01_n100k_s05_validity_20260413_145332",
    "claude_claude-opus-4-6_regression_task_01_n100k_s10_score_20260413_145307",
    "claude_claude-opus-4-6_regression_task_01_n100k_s10_validity_20260413_145358",
}
entries = json.loads(manifest_path.read_text())
subset = [entry for entry in entries if entry["run_id"] in target_ids]
out_path.write_text(json.dumps(subset, indent=2) + "\n", encoding="utf-8")
print(f"wrote {len(subset)} entries to {out_path}")
PY

uv run \
  "$MAKE_DATASETS_SOURCE/scripts/remote_make_datasets_postrun.py" run-robust-evals \
  --manifest "$SUBSET_MANIFEST" \
  --evidence "$EVIDENCE" \
  --output "$OUT_JSONL" \
  --remote-base "$REMOTE_BASE" \
  --temp-root "$TMP_ROOT" \
  --agent claude \
  --concurrency 1 \
  --timeout-seconds 1800 \
  --resume
