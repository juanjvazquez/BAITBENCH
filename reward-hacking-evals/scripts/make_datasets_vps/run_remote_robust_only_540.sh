#!/usr/bin/env bash
set -u

BASE_DIR="${1:-/home/team/make_datasets_analysis}"
STATUS_FILE="$BASE_DIR/status/robust_only_540.status"
MASTER_LOG="$BASE_DIR/logs/robust_only_540_master.log"
POSTRUN_PY="$BASE_DIR/remote_make_datasets_postrun.py"
MANIFEST_PATH="$BASE_DIR/synthetic_leakage_batch_manifest_540_latest.json"

mkdir -p "$BASE_DIR/logs" "$BASE_DIR/status"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*" | tee -a "$MASTER_LOG"
}

run_step() {
  local agent="$1"
  local log_path="$BASE_DIR/logs/robust_${agent}_rerun.log"
  log "START robust_${agent}"
  if python3 "$POSTRUN_PY" run-robust-evals \
      --manifest "$MANIFEST_PATH" \
      --agent "$agent" \
      --evidence "$BASE_DIR/synthetic_540_${agent}_evidence.jsonl" \
      --output "$BASE_DIR/synthetic_540_${agent}_robust_scores.jsonl" \
      --concurrency 4 >>"$log_path" 2>&1; then
    log "DONE robust_${agent}"
    return 0
  fi
  local code=$?
  log "FAIL robust_${agent} (exit $code)"
  return "$code"
}

echo running > "$STATUS_FILE"
overall_ok=0

for agent in codex claude kimi; do
  if ! run_step "$agent"; then
    overall_ok=1
  fi
done

if [[ "$overall_ok" -eq 0 ]]; then
  echo ok > "$STATUS_FILE"
  log "ALL ROBUST RERUN STEPS COMPLETED"
else
  echo failed > "$STATUS_FILE"
  log "ROBUST RERUN COMPLETED WITH FAILURES"
fi

printf 'evidence_ready\nrobust_status=%s\n' "$(cat "$STATUS_FILE")" > "$BASE_DIR/status/current_state.txt"
exit "$overall_ok"
