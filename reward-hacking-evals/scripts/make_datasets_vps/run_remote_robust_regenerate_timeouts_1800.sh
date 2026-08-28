#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${BASE_DIR:-/home/team/make_datasets_analysis}"
STATUS_DIR="$BASE_DIR/status"
LOG_DIR="$BASE_DIR/logs"
BACKUP_DIR="$BASE_DIR/backups/timeout_1800_regen_$(date +%Y%m%d_%H%M%S)"
SCRIPT="$BASE_DIR/remote_make_datasets_postrun.py"
MANIFEST="$BASE_DIR/timeout_subset_manifest.json"
STATUS_FILE="$STATUS_DIR/robust_regenerate_timeouts_1800.status"
TIMEOUT_SECONDS="${TIMEOUT_SECONDS:-1800}"

mkdir -p "$STATUS_DIR" "$LOG_DIR" "$BACKUP_DIR"

mark_failed() {
  echo "failed" > "$STATUS_FILE"
  printf 'evidence_ready\nrobust_status=failed\n' > "$STATUS_DIR/current_state.txt"
}

trap mark_failed ERR

echo "running" > "$STATUS_FILE"
printf 'evidence_ready\nrobust_status=running\n' > "$STATUS_DIR/current_state.txt"

for agent in codex claude kimi; do
  cp "$BASE_DIR/synthetic_540_${agent}_robust_scores.jsonl" "$BACKUP_DIR/"
done

run_agent() {
  local agent="$1"
  python3 "$SCRIPT" run-robust-evals \
    --manifest "$MANIFEST" \
    --evidence "$BASE_DIR/synthetic_540_${agent}_evidence.jsonl" \
    --output "$BASE_DIR/synthetic_540_${agent}_robust_scores_timeout_1800_regen.jsonl" \
    --agent "$agent" \
    --concurrency 1 \
    --timeout-seconds "$TIMEOUT_SECONDS" \
    > "$LOG_DIR/robust_${agent}_timeout_1800_regen.log" 2>&1
}

manifest_has_agent() {
  local agent="$1"
  grep -q "\"${agent}_" "$MANIFEST"
}

if manifest_has_agent codex; then
  run_agent codex
fi
if manifest_has_agent claude; then
  run_agent claude
fi
if manifest_has_agent kimi; then
  run_agent kimi
fi

python3 - <<'PY'
import json
from pathlib import Path

base = Path("/home/team/make_datasets_analysis")

def merge_records(agent: str) -> None:
    orig = base / f"synthetic_540_{agent}_robust_scores.jsonl"
    patch = base / f"synthetic_540_{agent}_robust_scores_timeout_1800_regen.jsonl"
    merged = base / f"synthetic_540_{agent}_robust_scores.jsonl.tmp"

    if not patch.exists():
        return

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

for agent in ("codex", "claude", "kimi"):
    merge_records(agent)
PY

trap - ERR
echo "ok" > "$STATUS_FILE"
printf 'evidence_ready\nrobust_status=ok\n' > "$STATUS_DIR/current_state.txt"
echo "TIMEOUT 1800 REGEN COMPLETE"
