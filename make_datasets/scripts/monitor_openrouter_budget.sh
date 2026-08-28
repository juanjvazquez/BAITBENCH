#!/usr/bin/env bash
set -u

ENV_FILE="${OPENROUTER_ENV_FILE:-$HOME/Projects/spar_project/make_datasets/.env}"
MIN_CREDITS="${MIN_OPENROUTER_CREDITS:-50}"
INTERVAL="${OPENROUTER_MONITOR_INTERVAL:-120}"
VPS_HOST="${VPS_HOST:-root@46.62.161.234}"
VPS_KEY="${VPS_KEY:-$HOME/.ssh/hetzner_rebuttal_20260709}"
LOG="${OPENROUTER_MONITOR_LOG:-/tmp/openrouter_budget_monitor.log}"

log() {
  printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" | tee -a "$LOG"
}

stop_workloads() {
  pkill -f 'judge_binary.py.*z-ai/glm-5.2' 2>/dev/null || true
  ssh -i "$VPS_KEY" "$VPS_HOST" '
    pkill -f /root/make_datasets/scripts/vps_launcher.sh 2>/dev/null || true
    tmux ls 2>/dev/null | awk -F: "/^opencode_(gpt|claude|sonnet)_/ {print \$1}" \
      | while read -r session; do tmux kill-session -t "$session"; done
  ' || true
}

set -a
source "$ENV_FILE"
set +a

while true; do
  payload=$(curl -fsS https://openrouter.ai/api/v1/credits \
    -H "Authorization: Bearer $OPENROUTER_API_KEY" 2>/dev/null) || {
      log "credit query failed; retrying"
      sleep "$INTERVAL"
      continue
    }
  remaining=$(jq -r '.data.total_credits - .data.total_usage' <<<"$payload")
  total=$(jq -r '.data.total_credits' <<<"$payload")
  usage=$(jq -r '.data.total_usage' <<<"$payload")
  log "total=$total usage=$usage remaining=$remaining minimum=$MIN_CREDITS"
  if awk -v remaining="$remaining" -v minimum="$MIN_CREDITS" \
    'BEGIN { exit !(remaining < minimum) }'; then
    log "budget guard triggered; stopping GLM judge and OpenCode workloads"
    stop_workloads
    exit 75
  fi
  sleep "$INTERVAL"
done
