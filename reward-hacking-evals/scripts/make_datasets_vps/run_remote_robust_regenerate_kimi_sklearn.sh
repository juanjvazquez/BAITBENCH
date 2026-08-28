#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BASE_DIR:-/home/team/make_datasets_analysis}"
STATUS_DIR="$BASE_DIR/status"
LOG_DIR="$BASE_DIR/logs"
BACKUP_DIR="$BASE_DIR/backups/kimi_sklearn_regen_$(date +%Y%m%d_%H%M%S)"
SCRIPT="$BASE_DIR/remote_make_datasets_postrun.py"
MANIFEST="$BASE_DIR/kimi_sklearn_subset_manifest.json"
STATUS_FILE="$STATUS_DIR/robust_regenerate_kimi_sklearn.status"
PATCH_OUTPUT="$BASE_DIR/synthetic_540_kimi_robust_scores_sklearn_regen.jsonl"
CANONICAL_OUTPUT="$BASE_DIR/synthetic_540_kimi_robust_scores.jsonl"

mkdir -p "$STATUS_DIR" "$LOG_DIR" "$BACKUP_DIR"

mark_failed() {
  echo "failed" > "$STATUS_FILE"
  printf 'evidence_ready\nrobust_status=failed\n' > "$STATUS_DIR/current_state.txt"
}

trap mark_failed ERR

echo "running" > "$STATUS_FILE"
printf 'evidence_ready\nrobust_status=running\n' > "$STATUS_DIR/current_state.txt"

cp "$CANONICAL_OUTPUT" "$BACKUP_DIR/"

python3 "$SCRIPT" run-robust-evals \
  --manifest "$MANIFEST" \
  --evidence "$BASE_DIR/synthetic_540_kimi_evidence.jsonl" \
  --output "$PATCH_OUTPUT" \
  --agent kimi \
  --concurrency 2 \
  --timeout-seconds 600 \
  > "$LOG_DIR/robust_kimi_sklearn_regen.log" 2>&1

python3 - <<'PY'
import json
from pathlib import Path

base = Path("/home/team/make_datasets_analysis")
orig = base / "synthetic_540_kimi_robust_scores.jsonl"
patch = base / "synthetic_540_kimi_robust_scores_sklearn_regen.jsonl"
merged = base / "synthetic_540_kimi_robust_scores.jsonl.tmp"

patch_records = {}
patch_footer = None
with patch.open(encoding="utf-8") as handle:
    for line in handle:
        obj = json.loads(line)
        if obj.get("type") == "record":
            patch_records[obj["run_id"]] = obj
        elif obj.get("type") == "footer":
            patch_footer = obj

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
PY

trap - ERR
echo "ok" > "$STATUS_FILE"
printf 'evidence_ready\nrobust_status=ok\n' > "$STATUS_DIR/current_state.txt"
echo "KIMI SKLEARN REGEN COMPLETE"
