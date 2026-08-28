#!/usr/bin/env bash
set -u

BASE_DIR="${1:-/home/team/make_datasets_analysis}"
STATUS_FILE="$BASE_DIR/status/robust_per_run_resume.status"
MASTER_LOG="$BASE_DIR/logs/robust_per_run_resume_master.log"
POSTRUN_PY="$BASE_DIR/remote_make_datasets_postrun.py"
MANIFEST_PATH="$BASE_DIR/synthetic_leakage_batch_manifest_540_latest.json"

mkdir -p "$BASE_DIR/logs" "$BASE_DIR/status"

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

pending_run_ids() {
  local agent="$1"
  python3 - "$MANIFEST_PATH" "$BASE_DIR/synthetic_540_${agent}_robust_scores.jsonl" "$agent" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
agent = sys.argv[3]

entries = json.loads(manifest_path.read_text())
done = set()
if output_path.exists():
    with output_path.open() as handle:
        for line in handle:
            obj = json.loads(line)
            if obj.get("type") != "record":
                continue
            run_id = obj.get("run_id")
            if isinstance(run_id, str):
                done.add(run_id)

for entry in entries:
    if entry.get("agent") != agent:
        continue
    run_id = entry["run_id"]
    if run_id not in done:
        print(run_id)
PY
}

lookup_status() {
  local agent="$1"
  local run_id="$2"
  python3 - "$BASE_DIR/synthetic_540_${agent}_robust_scores.jsonl" "$run_id" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
run_id = sys.argv[2]
status = "missing"
if path.exists():
    with path.open() as handle:
        for line in handle:
            obj = json.loads(line)
            if obj.get("type") != "record":
                continue
            if obj.get("run_id") == run_id:
                status = str(obj.get("status", "missing"))
print(status)
PY
}

run_agent_pending() {
  local agent="$1"
  local log_path="$BASE_DIR/logs/robust_${agent}_per_run_resume.log"
  mapfile -t pending < <(pending_run_ids "$agent")
  if [[ "${#pending[@]}" -eq 0 ]]; then
    log "SKIP robust_${agent} (no pending runs)"
    return 0
  fi

  log "START robust_${agent} per-run resume pending=${#pending[@]}"
  for run_id in "${pending[@]}"; do
    log "RUN ${agent} ${run_id}"
    if python3 "$POSTRUN_PY" run-robust-evals \
      --manifest "$MANIFEST_PATH" \
      --agent "$agent" \
      --evidence "$BASE_DIR/synthetic_540_${agent}_evidence.jsonl" \
      --output "$BASE_DIR/synthetic_540_${agent}_robust_scores.jsonl" \
      --run-id "$run_id" \
      --concurrency 1 \
      --resume >>"$log_path" 2>&1; then
      status="$(lookup_status "$agent" "$run_id")"
      log "DONE ${agent} ${run_id} status=${status}"
    else
      code=$?
      status="$(lookup_status "$agent" "$run_id")"
      log "FAIL ${agent} ${run_id} exit=${code} status=${status}"
    fi
  done
}

trap 'mark_failed' EXIT

echo running > "$STATUS_FILE"
printf 'evidence_ready\nrobust_status=running\n' > "$BASE_DIR/status/current_state.txt"

run_agent_pending claude
run_agent_pending kimi

remaining="$( (pending_run_ids claude; pending_run_ids kimi) | wc -l | tr -d ' ' )"
if [[ "$remaining" == "0" ]]; then
  echo ok > "$STATUS_FILE"
  printf 'evidence_ready\nrobust_status=ok\n' > "$BASE_DIR/status/current_state.txt"
  log "ALL PER-RUN RESUME STEPS COMPLETED"
else
  echo partial > "$STATUS_FILE"
  printf 'evidence_ready\nrobust_status=partial\n' > "$BASE_DIR/status/current_state.txt"
  log "PER-RUN RESUME FINISHED WITH remaining=${remaining}"
fi

trap - EXIT
