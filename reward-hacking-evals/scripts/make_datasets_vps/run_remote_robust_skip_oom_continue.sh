#!/usr/bin/env bash
set -u

BASE_DIR="${1:-/home/team/make_datasets_analysis}"
STATUS_FILE="$BASE_DIR/status/robust_skip_oom_continue.status"
MASTER_LOG="$BASE_DIR/logs/robust_skip_oom_continue_master.log"
POSTRUN_PY="$BASE_DIR/remote_make_datasets_postrun.py"
MANIFEST_PATH="$BASE_DIR/synthetic_leakage_batch_manifest_540_latest.json"

mkdir -p "$BASE_DIR/logs" "$BASE_DIR/status"

timestamp() {
  date '+%Y-%m-%d %H:%M:%S'
}

log() {
  printf '[%s] %s\n' "$(timestamp)" "$*" | tee -a "$MASTER_LOG"
}

write_state() {
  printf 'evidence_ready\nrobust_status=%s\n' "$1" > "$BASE_DIR/status/current_state.txt"
}

mark_failed() {
  echo failed > "$STATUS_FILE"
  write_state failed
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

record_exists() {
  local agent="$1"
  local run_id="$2"
  python3 - "$BASE_DIR/synthetic_540_${agent}_robust_scores.jsonl" "$run_id" <<'PY'
import json
import sys
from pathlib import Path

path = Path(sys.argv[1])
run_id = sys.argv[2]
found = False
if path.exists():
    with path.open() as handle:
        for line in handle:
            obj = json.loads(line)
            if obj.get("type") != "record":
                continue
            if obj.get("run_id") == run_id:
                found = True
                break
print("yes" if found else "no")
PY
}

append_skip_record() {
  local agent="$1"
  local run_id="$2"
  local reason="$3"
  python3 - "$MANIFEST_PATH" "$BASE_DIR/synthetic_540_${agent}_robust_scores.jsonl" "$run_id" "$reason" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])
run_id = sys.argv[3]
reason = sys.argv[4]

entries = json.loads(manifest_path.read_text())
entry = next((e for e in entries if e["run_id"] == run_id), None)
if entry is None:
    raise SystemExit(f"missing manifest entry for {run_id}")

record = {
    "type": "record",
    "run_id": run_id,
    "manifest_entry": entry,
    "status": "error",
    "metric": None,
    "test_score": None,
    "robust_error": {
        "returncode": None,
        "output": reason,
    },
    "skipped": True,
    "skip_reason": "oom_or_infrastructure_failure",
}
with output_path.open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record))
    handle.write("\n")
PY
}

run_one_isolated() {
  local agent="$1"
  local run_id="$2"
  local log_path="$BASE_DIR/logs/robust_${agent}_skip_oom_continue.log"
  local cmd=(
    python3 "$POSTRUN_PY" run-robust-evals
    --manifest "$MANIFEST_PATH"
    --agent "$agent"
    --evidence "$BASE_DIR/synthetic_540_${agent}_evidence.jsonl"
    --output "$BASE_DIR/synthetic_540_${agent}_robust_scores.jsonl"
    --run-id "$run_id"
    --concurrency 1
    --resume
  )

  local unit_output=""
  if ! unit_output="$(systemd-run --user --wait --collect "${cmd[@]}" 2>&1)"; then
    printf '%s\n' "$unit_output" >> "$log_path"
  else
    printf '%s\n' "$unit_output" >> "$log_path"
  fi

  if [[ "$(record_exists "$agent" "$run_id")" == "yes" ]]; then
    log "DONE ${agent} ${run_id} recorded"
    return 0
  fi

  append_skip_record "$agent" "$run_id" "Skipped after isolated robust eval failed before writing a record. systemd-run output: ${unit_output}"
  log "SKIP ${agent} ${run_id} no record written; marked skipped"
  return 0
}

run_agent_pending() {
  local agent="$1"
  local log_path="$BASE_DIR/logs/robust_${agent}_skip_oom_continue.log"
  : > "$log_path"

  mapfile -t pending < <(pending_run_ids "$agent")
  if [[ "${#pending[@]}" -eq 0 ]]; then
    log "SKIP robust_${agent} (no pending runs)"
    return 0
  fi

  log "START robust_${agent} skip-oom continue pending=${#pending[@]}"
  for run_id in "${pending[@]}"; do
    log "RUN ${agent} ${run_id}"
    run_one_isolated "$agent" "$run_id"
  done
}

trap 'mark_failed' EXIT

echo running > "$STATUS_FILE"
write_state running

# Known single-run OOM blockers on this VPS. Mark them once and move on.
for run_id in \
  claude_claude-opus-4-6_regression_task_01_n100k_s05_validity_20260413_145332 \
  claude_claude-opus-4-6_regression_task_01_n100k_s10_score_20260413_145307 \
  claude_claude-opus-4-6_regression_task_01_n100k_s10_validity_20260413_145358
do
  if [[ "$(record_exists claude "$run_id")" == "no" ]]; then
    append_skip_record claude "$run_id" "Skipped due to repeated VPS OOM failures on isolated robust eval for large Claude t01 n100k runs."
    log "PRE-SKIP claude ${run_id}"
  fi
done

run_agent_pending claude
run_agent_pending kimi

remaining="$( (pending_run_ids claude; pending_run_ids kimi) | wc -l | tr -d ' ' )"
if [[ "$remaining" == "0" ]]; then
  echo ok > "$STATUS_FILE"
  write_state ok
  log "ALL SKIP-OOM CONTINUE STEPS COMPLETED"
else
  echo partial > "$STATUS_FILE"
  write_state partial
  log "SKIP-OOM CONTINUE FINISHED WITH remaining=${remaining}"
fi

trap - EXIT
