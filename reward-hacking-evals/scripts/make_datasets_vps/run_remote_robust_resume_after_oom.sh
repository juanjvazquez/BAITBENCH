#!/usr/bin/env bash
set -euo pipefail

BASE_DIR="${1:-/home/team/make_datasets_analysis}"
STATUS_FILE="$BASE_DIR/status/robust_resume_after_oom.status"
MASTER_LOG="$BASE_DIR/logs/robust_resume_after_oom_master.log"
POSTRUN_PY="$BASE_DIR/remote_make_datasets_postrun.py"
MANIFEST_PATH="$BASE_DIR/synthetic_leakage_batch_manifest_540_latest.json"
ARCHIVE_DIR="$BASE_DIR/failed_attempts/20260421_resume_after_oom"

mkdir -p "$BASE_DIR/logs" "$BASE_DIR/status" "$ARCHIVE_DIR"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*" | tee -a "$MASTER_LOG"
}

mark_failed() {
  echo failed > "$STATUS_FILE"
  printf 'evidence_ready\nrobust_status=failed\n' > "$BASE_DIR/status/current_state.txt"
}

trap 'mark_failed' EXIT

trim_last_record() {
  local src="$1"
  local backup="$2"
  python3 - "$src" "$backup" <<'PY'
import json
import shutil
import sys
from pathlib import Path

src = Path(sys.argv[1])
backup = Path(sys.argv[2])
shutil.copy2(src, backup)

lines = src.read_text(encoding="utf-8").splitlines()
records = []
header = []
for line in lines:
    obj = json.loads(line)
    if obj.get("type") == "record":
        records.append(line)
    else:
        header.append(line)

if records:
    records = records[:-1]

with src.open("w", encoding="utf-8") as handle:
    for line in header:
        handle.write(line + "\n")
    for line in records:
        handle.write(line + "\n")
PY
}

run_agent_resume() {
  local agent="$1"
  local concurrency="$2"
  local log_path="$BASE_DIR/logs/robust_${agent}_resume.log"
  log "START robust_${agent} resume concurrency=${concurrency}"
  python3 "$POSTRUN_PY" run-robust-evals \
    --manifest "$MANIFEST_PATH" \
    --agent "$agent" \
    --evidence "$BASE_DIR/synthetic_540_${agent}_evidence.jsonl" \
    --output "$BASE_DIR/synthetic_540_${agent}_robust_scores.jsonl" \
    --concurrency "$concurrency" \
    --resume >>"$log_path" 2>&1
  log "DONE robust_${agent} resume"
}

echo running > "$STATUS_FILE"
printf 'evidence_ready\nrobust_status=running\n' > "$BASE_DIR/status/current_state.txt"

CLAUDE_OUTPUT="$BASE_DIR/synthetic_540_claude_robust_scores.jsonl"
if [[ -f "$CLAUDE_OUTPUT" ]]; then
  trim_last_record \
    "$CLAUDE_OUTPUT" \
    "$ARCHIVE_DIR/synthetic_540_claude_robust_scores_before_resume.jsonl"
  log "Trimmed last Claude record so resume reruns the failed boundary task"
fi

run_agent_resume claude 1
run_agent_resume kimi 1

echo ok > "$STATUS_FILE"
printf 'evidence_ready\nrobust_status=ok\n' > "$BASE_DIR/status/current_state.txt"
log "ALL RESUME STEPS COMPLETED"
trap - EXIT
