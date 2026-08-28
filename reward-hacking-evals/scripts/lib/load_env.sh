#!/usr/bin/env bash
# Source this from any pipeline script: `source "$(dirname "$0")/lib/load_env.sh"`
#
# - Loads .env from repo root (if present)
# - Validates required vars
# - For SSH_KEY_VPS{1,2}: if the value is private-key contents (begins with
#   "-----BEGIN"), writes to a 0600 tempfile and re-exports the var to that
#   path so downstream scripts can use `ssh -i "$SSH_KEY_VPS1"` unchanged.
# - For OPENROUTER_API_KEY with stray trailing space in its name, normalises.

set -euo pipefail

_repo_root() {
  local d
  d="${BASH_SOURCE[0]:-${(%):-%x}}"
  d="$(cd "$(dirname "$d")/../.." && pwd)"
  printf '%s' "$d"
}

REPO_ROOT="$(_repo_root)"
ENV_FILE="${ENV_FILE:-$REPO_ROOT/.env}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
fi

# (.env values must be quoted; see scripts/bootstrap_env.sh.)

_materialize_ssh_key() {
  local var="$1"
  local val="${!var:-}"
  if [[ -z "$val" ]]; then return 0; fi

  # Already a path -> nothing to do.
  if [[ "$val" != *"-----BEGIN"* ]]; then
    if [[ ! -f "$val" ]]; then
      echo "load_env.sh: $var=$val (file does not exist)" >&2
    fi
    return 0
  fi

  # Contents -> temp file with 0600 perms.
  local tmpdir tmpfile
  tmpdir="${TMPDIR:-/tmp}/rh-evals-ssh-$$"
  mkdir -p "$tmpdir"
  chmod 700 "$tmpdir"
  tmpfile="$tmpdir/$(printf '%s' "$var" | tr '[:upper:]' '[:lower:]')"
  printf '%s' "$val" > "$tmpfile"
  # Ensure trailing newline (OpenSSH is picky about key files).
  if [[ "$(tail -c1 "$tmpfile" | wc -l)" -eq 0 ]]; then
    printf '\n' >> "$tmpfile"
  fi
  chmod 600 "$tmpfile"
  export "$var=$tmpfile"
}

_materialize_ssh_key SSH_KEY_VPS1
_materialize_ssh_key SSH_KEY_VPS2

# Validate required vars (no exit on the loader itself; just warn).
_warn_unset() {
  local var="$1"
  if [[ -z "${!var:-}" ]]; then
    echo "load_env.sh: warning: $var is not set" >&2
  fi
}

_warn_unset OPENAI_API_KEY
_warn_unset ANTHROPIC_API_KEY
_warn_unset OPENROUTER_API_KEY
_warn_unset SSH_HOST_VPS1
_warn_unset SSH_HOST_VPS2
_warn_unset SSH_KEY_VPS1
_warn_unset SSH_KEY_VPS2
