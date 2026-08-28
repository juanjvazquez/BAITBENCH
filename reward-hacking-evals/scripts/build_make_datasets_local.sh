#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
POSTRUN_PY="$ROOT/src/ingest/make_datasets/local_postrun.py"
CASES_PY="$ROOT/src/cases/build_make_datasets_cases.py"
OUTPUT_DIR="${OUTPUT_DIR:-$ROOT/data/outputs/make_datasets}"
CASES_DIR="${CASES_DIR:-$ROOT/data/cases/make_datasets/records}"
CASES_JSONL="${CASES_JSONL:-$ROOT/data/cases/make_datasets/cases.jsonl}"
MANIFEST_PATH="${MANIFEST_PATH:-$ROOT/data/raw/make_datasets/analysis_exports/synthetic_leakage_batch_manifest_540_latest.json}"
RUNS_BASE="${RUNS_BASE:-$ROOT/data/raw/make_datasets/run_bundles}"
ROBUST_CONCURRENCY="${ROBUST_CONCURRENCY:-4}"

mkdir -p "$OUTPUT_DIR" "$OUTPUT_DIR/logs" "$OUTPUT_DIR/status"
mkdir -p "$CASES_DIR"

STATUS_FILE="$OUTPUT_DIR/status/local_postrun.status"
STATE_FILE="$OUTPUT_DIR/status/current_state.txt"
MASTER_LOG="$OUTPUT_DIR/logs/local_postrun_master.log"

timestamp() {
  date "+%Y-%m-%d %H:%M:%S"
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*" | tee -a "$MASTER_LOG"
}

run_step() {
  local name="$1"
  shift
  local log_path="$OUTPUT_DIR/logs/${name}.log"
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
extract_ok=1
robust_ok=1
cases_ok=0
echo "running" > "$STATUS_FILE"
echo "extracting_evidence" > "$STATE_FILE"

for agent in codex claude kimi; do
  if ! run_step "extract_${agent}" \
    python3 "$POSTRUN_PY" extract-evidence \
      --manifest "$MANIFEST_PATH" \
      --runs-base "$RUNS_BASE" \
      --agent "$agent" \
      --resume \
      --output "$OUTPUT_DIR/synthetic_540_${agent}_evidence.jsonl"
  then
    overall_ok=1
    extract_ok=0
  fi
done

if [[ "$extract_ok" -eq 1 ]]; then
  echo "evidence_ready" > "$STATE_FILE"
else
  echo "extract_failed" > "$STATE_FILE"
fi

for agent in codex claude kimi; do
  if ! run_step "robust_${agent}" \
    python3 "$POSTRUN_PY" run-robust-evals \
      --manifest "$MANIFEST_PATH" \
      --runs-base "$RUNS_BASE" \
      --agent "$agent" \
      --resume \
      --concurrency "$ROBUST_CONCURRENCY" \
      --evidence "$OUTPUT_DIR/synthetic_540_${agent}_evidence.jsonl" \
      --output "$OUTPUT_DIR/synthetic_540_${agent}_robust_scores.jsonl"
  then
    overall_ok=1
    robust_ok=0
  fi
done

if [[ "$robust_ok" -eq 1 ]]; then
  echo "robust_status=ok" >> "$STATE_FILE"
  if ! run_step "build_cases" \
    python3 "$CASES_PY" \
      --input-dir "$OUTPUT_DIR" \
      --output-dir "$CASES_DIR" \
      --output-jsonl "$CASES_JSONL"
  then
    overall_ok=1
    cases_ok=0
  else
    cases_ok=1
  fi
fi

if [[ "$overall_ok" -eq 0 ]]; then
  echo "cases_status=ok" >> "$STATE_FILE"
  echo "ok" > "$STATUS_FILE"
  log "ALL STEPS COMPLETED"
else
  if [[ "$cases_ok" -eq 0 || "$robust_ok" -eq 0 ]]; then
    echo "cases_status=failed" >> "$STATE_FILE"
  fi
  if [[ "$robust_ok" -eq 0 ]]; then
    echo "robust_status=failed" >> "$STATE_FILE"
  fi
  echo "failed" > "$STATUS_FILE"
  log "LOCAL POSTRUN COMPLETED WITH FAILURES"
fi

exit "$overall_ok"
