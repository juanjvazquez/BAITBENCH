#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
  echo "Usage: $0 RUN_ID [TMUX_SESSION]" >&2
  exit 2
fi

RUN_ID="$1"
SESSION="${2:-$(printf '%s' "$RUN_ID" | tr -c '[:alnum:]_' '_' | cut -c 1-80)}"

HOST="${VPS_HOST:-team@87.99.129.5}"
KEY="${VPS_KEY:-$HOME/.ssh/autoresearch}"
REMOTE_BASE="${VPS_RUNS_DIR:-/home/team/make_datasets_runs}"
UV="${VPS_UV:-/home/team/.local/bin/uv}"
CLAUDE_ENV_FILE="${VPS_CLAUDE_ENV_FILE:-/home/team/.config/anthropic.env}"

ssh -i "$KEY" "$HOST" bash -s -- \
  "$RUN_ID" \
  "$SESSION" \
  "$REMOTE_BASE" \
  "$UV" \
  "$CLAUDE_ENV_FILE" <<'REMOTE'
set -euo pipefail

RUN_ID="$1"
SESSION="$2"
REMOTE_BASE="$3"
UV="$4"
CLAUDE_ENV_FILE="$5"

RUN_DIR="$REMOTE_BASE/$RUN_ID"
WORKSPACE_DIR="$RUN_DIR/workspace"

if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "tmux session already exists: $SESSION" >&2
  exit 1
fi

if [ ! -d "$WORKSPACE_DIR" ]; then
  echo "missing workspace: $WORKSPACE_DIR" >&2
  exit 1
fi

METADATA=$(python3 - "$RUN_DIR/metadata.json" <<'PY'
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

metadata_path = Path(sys.argv[1])
metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
num_hours = float(metadata["num_hours"])
timeout_seconds = math.ceil(num_hours * 3600)
if timeout_seconds <= 0:
    raise SystemExit(f"num_hours must produce a positive timeout: {num_hours}")
agent = metadata["agent"]
model = metadata["model"]
codex_effort = metadata["codex_reasoning_effort"]
claude_effort = metadata.get("claude_effort", "medium")
print(f"{timeout_seconds}\t{agent}\t{model}\t{codex_effort}\t{claude_effort}")
PY
)
IFS=$'\t' read -r TIMEOUT_SECONDS AGENT MODEL CODEX_EFFORT CLAUDE_EFFORT <<< "$METADATA"

rm -f \
  "$WORKSPACE_DIR/.timer_start" \
  "$RUN_DIR/transcript.json" \
  "$RUN_DIR/agent_stderr.log" \
  "$RUN_DIR/agent_exit_code.txt" \
  "$RUN_DIR/run_agent.sh"

if [ "$AGENT" = "codex" ]; then
  AGENT_COMMAND="cat program.md | timeout '${TIMEOUT_SECONDS}s' codex exec --json --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox --model '$MODEL' -c 'model_reasoning_effort=\"$CODEX_EFFORT\"' -"
elif [ "$AGENT" = "claude" ]; then
  if [ ! -f "$CLAUDE_ENV_FILE" ]; then
    echo "missing Claude API env file: $CLAUDE_ENV_FILE" >&2
    exit 1
  fi
  AGENT_COMMAND="set -a && source '$CLAUDE_ENV_FILE' && set +a && cat program.md | timeout '${TIMEOUT_SECONDS}s' claude --bare --print --verbose --output-format stream-json --input-format text --model '$MODEL' --effort '$CLAUDE_EFFORT' --dangerously-skip-permissions"
else
  echo "unsupported agent in metadata: $AGENT" >&2
  exit 1
fi

cat > "$RUN_DIR/run_agent.sh" <<EOF
#!/usr/bin/env bash
set -uo pipefail

status=1
write_exit_code() {
  printf '%s\n' "\$status" > '$RUN_DIR/agent_exit_code.txt'
}
trap write_exit_code EXIT

cd '$WORKSPACE_DIR' || {
  status=\$?
  exit "\$status"
}

if '$UV' sync && '$UV' run timer.py; then
  set +e
  $AGENT_COMMAND > ../transcript.json 2> ../agent_stderr.log
  status=\$?
else
  status=\$?
fi

exit "\$status"
EOF
chmod +x "$RUN_DIR/run_agent.sh"

tmux new -d -s "$SESSION" "bash '$RUN_DIR/run_agent.sh'"

echo "started tmux session: $SESSION"
echo "timeout seconds: $TIMEOUT_SECONDS"
echo "agent: $AGENT"
echo "model: $MODEL"
echo "workspace: $WORKSPACE_DIR"
echo "transcript: $RUN_DIR/transcript.json"
echo "stderr: $RUN_DIR/agent_stderr.log"
REMOTE
