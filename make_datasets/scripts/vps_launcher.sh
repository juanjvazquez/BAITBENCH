#!/usr/bin/env bash
# VPS-side launcher. Runs entirely on the VPS — no SSH, no local Mac dependency.
# Usage: bash vps_launcher.sh [--max-codex N] [--max-claude N] [--max-opencode-gpt N] [--max-opencode-claude N] [--max-opencode-sonnet N] [--poll N]
set -euo pipefail

MAX_CODEX=4
MAX_CLAUDE=1
MAX_OPENCODE_GPT=4
MAX_OPENCODE_CLAUDE=2
MAX_OPENCODE_SONNET=2
MAX_OPENCODE_GEMINI_PRO=2
MAX_OPENCODE_GEMINI_FLASH=4
MAX_OPENCODE_DEEPSEEK=2
MAX_KIMI=4
POLL=15
RUN_GLOB="${VPS_RUN_GLOB:-*2026041*}"
MIN_OPENROUTER_CREDITS="${MIN_OPENROUTER_CREDITS:-50}"
ONLY_AGENT="${VPS_ONLY_AGENT:-}"
BASE="${VPS_RUNS_DIR:-$HOME/make_datasets_runs}"
UV="$HOME/.local/bin/uv"
OPENCODE="$HOME/.opencode/bin/opencode"
CLAUDE_ENV_FILE="$HOME/.config/anthropic.env"
LOG="$BASE/vps_launcher.log"

# Parse args
while [[ $# -gt 0 ]]; do
  case "$1" in
    --max-codex) MAX_CODEX="$2"; shift 2;;
    --max-claude) MAX_CLAUDE="$2"; shift 2;;
    --max-opencode-gpt) MAX_OPENCODE_GPT="$2"; shift 2;;
    --max-opencode-claude) MAX_OPENCODE_CLAUDE="$2"; shift 2;;
    --max-opencode-sonnet) MAX_OPENCODE_SONNET="$2"; shift 2;;
    --max-opencode-gemini-pro) MAX_OPENCODE_GEMINI_PRO="$2"; shift 2;;
    --max-opencode-gemini-flash) MAX_OPENCODE_GEMINI_FLASH="$2"; shift 2;;
    --max-opencode-deepseek) MAX_OPENCODE_DEEPSEEK="$2"; shift 2;;
    --max-kimi) MAX_KIMI="$2"; shift 2;;
    --poll) POLL="$2"; shift 2;;
    *) echo "unknown arg: $1" >&2; exit 1;;
  esac
done

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*" | tee -a "$LOG"; }

log "vps_launcher started: max_codex=$MAX_CODEX max_claude=$MAX_CLAUDE max_opencode_gpt=$MAX_OPENCODE_GPT max_opencode_claude=$MAX_OPENCODE_CLAUDE max_opencode_sonnet=$MAX_OPENCODE_SONNET poll=$POLL"

active_count() {
  local prefix="$1"
  tmux ls 2>/dev/null | awk -F: '{print $1}' | grep -c "^${prefix}_" || true
}

openrouter_remaining_credits() {
  set -a
  source "$HOME/.config/openrouter.env"
  set +a
  curl -fsS https://openrouter.ai/api/v1/credits \
    -H "Authorization: Bearer $OPENROUTER_API_KEY" \
    | jq -r '.data.total_credits - .data.total_usage'
}

require_openrouter_budget() {
  local remaining
  remaining=$(openrouter_remaining_credits)
  if awk -v remaining="$remaining" -v minimum="$MIN_OPENROUTER_CREDITS" \
    'BEGIN { exit !(remaining < minimum) }'; then
    log "stop (OpenRouter budget guard): remaining=$remaining minimum=$MIN_OPENROUTER_CREDITS"
    exit 75
  fi
  log "OpenRouter remaining credits: $remaining"
}

session_name() {
  printf '%s' "$1" | tr -c '[:alnum:]_' '_' | cut -c 1-80
}

for dir in "$BASE"/$RUN_GLOB/; do
  [ ! -d "$dir" ] && continue
  [ ! -d "$dir/workspace" ] && continue
  [ -f "$dir/agent_exit_code.txt" ] && continue

  RUN_ID=$(basename "$dir")
  SESSION=$(session_name "$RUN_ID")

  # Skip if tmux session already exists
  if tmux has-session -t "$SESSION" 2>/dev/null; then
    log "skip (active): $RUN_ID"
    continue
  fi

  # Read metadata
  METADATA=$(python3 - "$dir/metadata.json" <<'PY'
import json, math, sys
from pathlib import Path
m = json.loads(Path(sys.argv[1]).read_text())
timeout = math.ceil(float(m["num_hours"]) * 3600)
print(f"{timeout}\t{m['agent']}\t{m['model']}\t{m.get('codex_reasoning_effort','medium')}\t{m.get('claude_effort','medium')}")
PY
  )
  IFS=$'\t' read -r TIMEOUT AGENT MODEL CODEX_EFFORT CLAUDE_EFFORT <<< "$METADATA"

  if [ -n "$ONLY_AGENT" ] && [ "$AGENT" != "$ONLY_AGENT" ]; then
    continue
  fi

  # Determine concurrency limit
  if [ "$AGENT" = "codex" ]; then
    MAX=$MAX_CODEX
    PREFIX="codex"
  elif [ "$AGENT" = "claude" ]; then
    MAX=$MAX_CLAUDE
    PREFIX="claude"
  elif [ "$AGENT" = "opencode_gpt" ]; then
    MAX=$MAX_OPENCODE_GPT
    PREFIX="opencode_gpt"
  elif [ "$AGENT" = "opencode_claude" ]; then
    MAX=$MAX_OPENCODE_CLAUDE
    PREFIX="opencode_claude"
  elif [ "$AGENT" = "opencode_sonnet" ]; then
    MAX=$MAX_OPENCODE_SONNET
    PREFIX="opencode_sonnet"
  elif [ "$AGENT" = "opencode_gemini_pro" ]; then
    MAX=$MAX_OPENCODE_GEMINI_PRO
    PREFIX="opencode_gemini_pro"
  elif [ "$AGENT" = "opencode_gemini_flash" ]; then
    MAX=$MAX_OPENCODE_GEMINI_FLASH
    PREFIX="opencode_gemini_flash"
  elif [ "$AGENT" = "opencode_deepseek" ]; then
    MAX=$MAX_OPENCODE_DEEPSEEK
    PREFIX="opencode_deepseek"
  elif [ "$AGENT" = "kimi" ]; then
    MAX=$MAX_KIMI
    PREFIX="kimi"
  else
    log "skip (unknown agent $AGENT): $RUN_ID"
    continue
  fi

  # Wait for slot
  while true; do
    # Multiple family-specific dispatchers may run concurrently. Hold a
    # per-agent lock through the subsequent tmux creation so they cannot all
    # observe the same free slot and oversubscribe the VPS together.
    exec 9>"$BASE/.dispatch_${PREFIX}.lock"
    flock 9
    ACTIVE=$(active_count "$PREFIX")
    [ "$ACTIVE" -lt "$MAX" ] && break
    flock -u 9
    sleep "$POLL"
  done

  case "$AGENT" in
    opencode_gpt|opencode_claude|opencode_sonnet|opencode_gemini_pro|opencode_gemini_flash|opencode_deepseek|kimi)
      require_openrouter_budget
      ;;
  esac

  # Build run_agent.sh
  WORKSPACE="$dir/workspace"
  rm -f "$WORKSPACE/.timer_start" "$dir/transcript.json" "$dir/agent_stderr.log" "$dir/agent_exit_code.txt" "$dir/run_agent.sh"

  if [ "$AGENT" = "codex" ]; then
    AGENT_CMD="cat program.md | timeout '${TIMEOUT}s' codex exec --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox --model '$MODEL' -c 'model_reasoning_effort=\"$CODEX_EFFORT\"' -"
  elif [ "$AGENT" = "claude" ]; then
    AGENT_CMD="set -a && source '$CLAUDE_ENV_FILE' && set +a && cat program.md | timeout '${TIMEOUT}s' claude --bare --print --verbose --output-format stream-json --input-format text --model '$MODEL' --effort '$CLAUDE_EFFORT' --dangerously-skip-permissions"
  elif [ "$AGENT" = "opencode_gpt" ]; then
    AGENT_CMD="set -a && source '$HOME/.config/openrouter.env' && set +a && cat program.md | timeout '${TIMEOUT}s' '$OPENCODE' run --format json --model openrouter/openai/gpt-5.4 --variant high --auto"
  elif [ "$AGENT" = "opencode_claude" ]; then
    AGENT_CMD="set -a && source '$HOME/.config/openrouter.env' && set +a && cat program.md | timeout '${TIMEOUT}s' '$OPENCODE' run --format json --model openrouter/anthropic/claude-opus-4.6 --variant high --auto"
  elif [ "$AGENT" = "opencode_sonnet" ]; then
    AGENT_CMD="set -a && source '$HOME/.config/openrouter.env' && set +a && cat program.md | timeout '${TIMEOUT}s' '$OPENCODE' run --format json --model openrouter/anthropic/claude-sonnet-4.6 --variant high --auto"
  elif [ "$AGENT" = "opencode_gemini_pro" ]; then
    AGENT_CMD="set -a && source '$HOME/.config/openrouter.env' && set +a && cat program.md | timeout '${TIMEOUT}s' '$OPENCODE' run --format json --model openrouter/google/gemini-3.1-pro-preview --auto"
  elif [ "$AGENT" = "opencode_gemini_flash" ]; then
    AGENT_CMD="set -a && source '$HOME/.config/openrouter.env' && set +a && cat program.md | timeout '${TIMEOUT}s' '$OPENCODE' run --format json --model openrouter/google/gemini-3-flash-preview --auto"
  elif [ "$AGENT" = "opencode_deepseek" ]; then
    AGENT_CMD="set -a && source '$HOME/.config/openrouter.env' && set +a && cat program.md | timeout '${TIMEOUT}s' '$OPENCODE' run --format json --model openrouter/deepseek/deepseek-v4-pro --auto"
  elif [ "$AGENT" = "kimi" ]; then
    AGENT_CMD="set -a && source '$HOME/.config/openrouter.env' && set +a && cat program.md | timeout '${TIMEOUT}s' '$OPENCODE' run --format json --model openrouter/moonshotai/kimi-k2.5 --auto"
  fi

  cat > "$dir/run_agent.sh" <<EOF
#!/usr/bin/env bash
set -uo pipefail

status=1
write_exit_code() {
  printf '%s\n' "\$status" > '$dir/agent_exit_code.txt'
}
trap write_exit_code EXIT

cd '$WORKSPACE' || {
  status=\$?
  exit "\$status"
}

if '$UV' sync && '$UV' run timer.py; then
  set +e
  $AGENT_CMD > ../transcript.json 2> ../agent_stderr.log
  status=\$?
else
  status=\$?
fi

exit "\$status"
EOF
  chmod +x "$dir/run_agent.sh"

  tmux new -d -s "$SESSION" "bash '$dir/run_agent.sh'"
  flock -u 9
  log "started $AGENT $RUN_ID"
done

log "vps_launcher: all runs dispatched"
