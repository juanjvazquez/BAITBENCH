#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SUMMARY_PY="$ROOT/src/scoring/summarize_judging_run.py"

if [[ $# -lt 1 ]]; then
  echo "Usage: bash scripts/summarize_judging_run.sh <run-root> [extra args...]" >&2
  exit 2
fi

python3 "$SUMMARY_PY" "$@"
