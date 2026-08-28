#!/usr/bin/env bash
# One-time helper: read your local SSH private keys and write them into .env
# (creating .env from .env.example if needed).
#
# Idempotent — re-running this is safe; it replaces existing SSH_KEY_VPS{1,2}
# blocks, leaving the rest of .env alone.
#
# After this, scripts/lib/load_env.sh will materialise the keys to 0600 tempfiles
# at source-time so existing `ssh -i $SSH_KEY_VPS1 ...` patterns keep working.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
EXAMPLE_FILE="$ROOT/.env.example"

KEY_VPS1="${KEY_VPS1:-$HOME/.ssh/autoresearch}"
KEY_VPS2="${KEY_VPS2:-$HOME/.ssh/autoresearch_2}"
HOST_VPS1="${HOST_VPS1:-team@87.99.129.5}"
HOST_VPS2="${HOST_VPS2:-team@206.189.230.132}"

if [[ ! -f "$KEY_VPS1" ]]; then
  echo "bootstrap_env.sh: missing $KEY_VPS1" >&2
  exit 1
fi
if [[ ! -f "$KEY_VPS2" ]]; then
  echo "bootstrap_env.sh: missing $KEY_VPS2" >&2
  exit 1
fi

if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$EXAMPLE_FILE" ]]; then
    cp "$EXAMPLE_FILE" "$ENV_FILE"
    echo "Created .env from .env.example. Fill in the API keys, then re-run this." >&2
    exit 2
  fi
  echo "bootstrap_env.sh: no .env or .env.example found in $ROOT" >&2
  exit 1
fi

# Strip any existing assignments and rewrite cleanly.
python3 - <<PY
import re
from pathlib import Path

env_file = Path("$ENV_FILE")
key_vps1 = Path("$KEY_VPS1").read_text()
key_vps2 = Path("$KEY_VPS2").read_text()
host_vps1 = "$HOST_VPS1"
host_vps2 = "$HOST_VPS2"
text = env_file.read_text()

def strip_var(content: str, name: str) -> str:
    # Strip a NAME=... line. If the value is opened with " and not closed on
    # the same line, also strip subsequent lines until the closing ".
    out = []
    skip_until_close = False
    for line in content.splitlines(keepends=False):
        if skip_until_close:
            if line.rstrip().endswith('"'):
                skip_until_close = False
            continue
        m = re.match(rf'^{re.escape(name)}=(.*)$', line)
        if m:
            val = m.group(1)
            if val.startswith('"') and not val.endswith('"'):
                skip_until_close = True
            continue
        out.append(line)
    return "\n".join(out)

for name in ("SSH_KEY_VPS1", "SSH_KEY_VPS2", "SSH_HOST_VPS1", "SSH_HOST_VPS2"):
    text = strip_var(text, name)
text = text.rstrip() + "\n\n"
text += f"SSH_HOST_VPS1='{host_vps1}'\n"
text += f"SSH_HOST_VPS2='{host_vps2}'\n\n"
text += f'SSH_KEY_VPS1="{key_vps1.rstrip()}\n"\n\n'
text += f'SSH_KEY_VPS2="{key_vps2.rstrip()}\n"\n'

env_file.write_text(text)
PY

chmod 600 "$ENV_FILE"

echo "Wrote SSH key contents to $ENV_FILE."
echo "  - SSH_KEY_VPS1 ($KEY_VPS1)"
echo "  - SSH_KEY_VPS2 ($KEY_VPS2)"
echo "Done. Confirm with:  grep -c '^SSH_KEY_VPS' $ENV_FILE   # expect 2"
