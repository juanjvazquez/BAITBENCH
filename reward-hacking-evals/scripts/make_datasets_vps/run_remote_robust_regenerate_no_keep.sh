#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BASE_DIR:-/home/team/make_datasets_analysis}"
STATUS_DIR="$BASE_DIR/status"
LOG_DIR="$BASE_DIR/logs"
BACKUP_DIR="$BASE_DIR/backups/no_keep_regen_$(date +%Y%m%d_%H%M%S)"
SCRIPT="$BASE_DIR/remote_make_datasets_postrun.py"
MANIFEST="$BASE_DIR/no_keep_subset_manifest.json"

mkdir -p "$STATUS_DIR" "$LOG_DIR" "$BACKUP_DIR"

echo "running" > "$STATUS_DIR/robust_regenerate_no_keep.status"
printf 'evidence_ready\nrobust_status=running\n' > "$STATUS_DIR/current_state.txt"

cp "$BASE_DIR/synthetic_540_claude_robust_scores.jsonl" "$BACKUP_DIR/"
cp "$BASE_DIR/synthetic_540_kimi_robust_scores.jsonl" "$BACKUP_DIR/"

python3 "$SCRIPT" run-robust-evals \
  --manifest "$MANIFEST" \
  --evidence "$BASE_DIR/synthetic_540_claude_evidence.jsonl" \
  --output "$BASE_DIR/synthetic_540_claude_robust_scores_no_keep_regen.jsonl" \
  --agent claude \
  --concurrency 1 \
  --timeout-seconds 600 \
  > "$LOG_DIR/robust_claude_no_keep_regen.log" 2>&1

python3 "$SCRIPT" run-robust-evals \
  --manifest "$MANIFEST" \
  --evidence "$BASE_DIR/synthetic_540_kimi_evidence.jsonl" \
  --output "$BASE_DIR/synthetic_540_kimi_robust_scores_no_keep_regen.jsonl" \
  --agent kimi \
  --concurrency 2 \
  --timeout-seconds 600 \
  > "$LOG_DIR/robust_kimi_no_keep_regen.log" 2>&1

python3 - <<'PY'
import json
from pathlib import Path

base = Path("/home/team/make_datasets_analysis")

def merge_records(orig_name: str, patch_name: str) -> None:
    orig = base / orig_name
    patch = base / patch_name
    merged = base / (orig_name + ".tmp")

    patch_records = {}
    patch_header = None
    patch_footer = None
    with patch.open(encoding="utf-8") as handle:
        for line in handle:
            obj = json.loads(line)
            if obj.get("type") == "header":
                patch_header = obj
            elif obj.get("type") == "footer":
                patch_footer = obj
            elif obj.get("type") == "record":
                patch_records[obj["run_id"]] = obj

    with orig.open(encoding="utf-8") as src, merged.open("w", encoding="utf-8") as dst:
        record_count = 0
        for line in src:
            obj = json.loads(line)
            if obj.get("type") == "record":
                obj = patch_records.get(obj["run_id"], obj)
                record_count += 1
            elif obj.get("type") == "footer" and patch_footer is not None:
                obj = {**obj, "record_count": record_count}
            dst.write(json.dumps(obj))
            dst.write("\n")

    merged.replace(orig)

merge_records("synthetic_540_claude_robust_scores.jsonl", "synthetic_540_claude_robust_scores_no_keep_regen.jsonl")
merge_records("synthetic_540_kimi_robust_scores.jsonl", "synthetic_540_kimi_robust_scores_no_keep_regen.jsonl")
PY

echo "ok" > "$STATUS_DIR/robust_regenerate_no_keep.status"
printf 'evidence_ready\nrobust_status=ok\n' > "$STATUS_DIR/current_state.txt"
echo "NO-KEEP REGEN COMPLETE"
