#!/usr/bin/env bash
set -u

BASE_DIR="${1:-/home/team/make_datasets_analysis}"
MANIFEST_PATH="${2:-$BASE_DIR/synthetic_leakage_batch_manifest_540_latest.json}"
POSTRUN_PY="${3:-$BASE_DIR/remote_make_datasets_postrun.py}"

mkdir -p "$BASE_DIR" "$BASE_DIR/logs" "$BASE_DIR/status"

STATUS_FILE="$BASE_DIR/status/postrun_540.status"
MASTER_LOG="$BASE_DIR/logs/postrun_540_master.log"

timestamp() {
  date "+%Y-%m-%d %H:%M:%S"
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*" | tee -a "$MASTER_LOG"
}

run_step() {
  local name="$1"
  shift
  local log_path="$BASE_DIR/logs/${name}.log"
  log "START $name"
  if "$@" >>"$log_path" 2>&1; then
    log "DONE  $name"
    return 0
  fi
  local code=$?
  log "FAIL  $name (exit $code)"
  return "$code"
}

overall_ok=0
echo "running" > "$STATUS_FILE"

for agent in codex claude kimi; do
  if ! run_step "extract_${agent}" \
    python3 "$POSTRUN_PY" extract-evidence \
      --manifest "$MANIFEST_PATH" \
      --agent "$agent" \
      --output "$BASE_DIR/synthetic_540_${agent}_evidence.jsonl"
  then
    overall_ok=1
  fi
done

for agent in codex claude kimi; do
  if ! run_step "robust_${agent}" \
    python3 "$POSTRUN_PY" run-robust-evals \
      --manifest "$MANIFEST_PATH" \
      --agent "$agent" \
      --evidence "$BASE_DIR/synthetic_540_${agent}_evidence.jsonl" \
      --output "$BASE_DIR/synthetic_540_${agent}_robust_scores.jsonl" \
      --concurrency 4
  then
    overall_ok=1
  fi
done

if [[ "$overall_ok" -eq 0 ]]; then
  echo "ok" > "$STATUS_FILE"
  log "ALL STEPS COMPLETED"
else
  echo "failed" > "$STATUS_FILE"
  log "POSTRUN COMPLETED WITH FAILURES"
fi

exit "$overall_ok"
