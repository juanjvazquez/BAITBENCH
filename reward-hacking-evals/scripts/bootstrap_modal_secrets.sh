#!/usr/bin/env bash
# Create / update Modal Secrets on the spar-maded profile from local .env.
#
# Idempotent (uses `modal secret create --force`).
#
# Secrets created:
#   - judge-anthropic     ANTHROPIC_API_KEY=...
#   - judge-openai        OPENAI_API_KEY=...
#   - judge-openrouter    OPENROUTER_API_KEY=...
#   - ssh-vps1            SSH_PRIVATE_KEY=<contents of $SSH_KEY_VPS1>
#                         SSH_HOST=<contents of $SSH_HOST_VPS1>
#   - ssh-vps2            SSH_PRIVATE_KEY=<contents of $SSH_KEY_VPS2>
#                         SSH_HOST=<contents of $SSH_HOST_VPS2>
#
# Modal apps mount these via modal.Secret.from_name("...").

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/load_env.sh"

PROFILE="${MODAL_PROFILE:-spar-maded}"
MODAL_BIN="${MODAL_BIN:-modal}"
if ! command -v "$MODAL_BIN" >/dev/null 2>&1; then
  # Fallback: the venv install used elsewhere in this account.
  MODAL_BIN="$HOME/Documents/Codex/2026-04-26-modal-token-set-token-id-ak/.venv-modal/bin/modal"
fi
if ! command -v "$MODAL_BIN" >/dev/null 2>&1; then
  echo "bootstrap_modal_secrets.sh: cannot find 'modal' CLI" >&2
  exit 1
fi

echo "Using profile: $PROFILE"
echo "Using modal:   $MODAL_BIN"
"$MODAL_BIN" profile activate "$PROFILE" >/dev/null

require() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "bootstrap_modal_secrets.sh: $name unset (check .env)" >&2
    exit 1
  fi
}

require ANTHROPIC_API_KEY
require OPENAI_API_KEY
require OPENROUTER_API_KEY
require SSH_KEY_VPS1
require SSH_KEY_VPS2
require SSH_HOST_VPS1
require SSH_HOST_VPS2

run_secret() {
  local name="$1"; shift
  echo "  -> $name"
  "$MODAL_BIN" secret create --force "$name" "$@" >/dev/null
}

# ---- API key secrets ----
run_secret judge-anthropic   "ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
run_secret judge-openai      "OPENAI_API_KEY=$OPENAI_API_KEY"
run_secret judge-openrouter  "OPENROUTER_API_KEY=$OPENROUTER_API_KEY"

# ---- SSH secrets ----
# load_env.sh has materialised SSH_KEY_VPS{1,2} to 0600 tempfile paths.
SSH_PRIVATE_KEY_VPS1="$(cat "$SSH_KEY_VPS1")"
SSH_PRIVATE_KEY_VPS2="$(cat "$SSH_KEY_VPS2")"

run_secret ssh-vps1 \
  "SSH_PRIVATE_KEY=$SSH_PRIVATE_KEY_VPS1" \
  "SSH_HOST=$SSH_HOST_VPS1"
run_secret ssh-vps2 \
  "SSH_PRIVATE_KEY=$SSH_PRIVATE_KEY_VPS2" \
  "SSH_HOST=$SSH_HOST_VPS2"

echo ""
echo "Done. Created/updated 5 Modal secrets on profile '$PROFILE':"
echo "  judge-anthropic, judge-openai, judge-openrouter, ssh-vps1, ssh-vps2"
echo ""
echo "Verify with:"
echo "  $MODAL_BIN secret list"
