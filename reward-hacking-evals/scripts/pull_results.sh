#!/usr/bin/env bash
# Convenience: pull selected paths from the rh-evals-results Modal volume to
# the laptop's data/ tree. The Modal volume is the canonical store; the
# laptop tree is just a read-only mirror you populate on demand.
#
# Usage:
#   bash scripts/pull_results.sh                                # pull cases + summaries
#   bash scripts/pull_results.sh judging/<label>                # pull one judging label
#   bash scripts/pull_results.sh evidence/vps2                  # pull a single subtree
#   bash scripts/pull_results.sh --all                          # pull everything (large)

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/load_env.sh" || true

MODAL_BIN="${MODAL_BIN:-modal}"
if ! command -v "$MODAL_BIN" >/dev/null 2>&1; then
  MODAL_BIN="$HOME/Documents/Codex/2026-04-26-modal-token-set-token-id-ak/.venv-modal/bin/modal"
fi
if ! command -v "$MODAL_BIN" >/dev/null 2>&1; then
  echo "pull_results.sh: cannot find 'modal' CLI" >&2
  exit 1
fi

VOLUME="rh-evals-results"
DATA_DIR="$ROOT/data"

map_path() {
  # Convert volume path -> laptop path.
  local p="$1"
  case "$p" in
    cases/*)        printf '%s/cases/make_datasets/%s' "$DATA_DIR" "${p#cases/}" ;;
    evidence/*)     printf '%s/outputs/make_datasets/%s' "$DATA_DIR" "${p#evidence/}" ;;
    robust_scores/*)printf '%s/outputs/make_datasets/%s' "$DATA_DIR" "${p#robust_scores/}" ;;
    judging/*)      printf '%s/outputs/judging/%s' "$DATA_DIR" "${p#judging/}" ;;
    summaries/*)    printf '%s/reports/%s' "$DATA_DIR" "${p#summaries/}" ;;
    *)              printf '%s/raw/make_datasets/_other/%s' "$DATA_DIR" "$p" ;;
  esac
}

pull() {
  local src="$1"
  local dst
  dst="$(map_path "$src")"
  mkdir -p "$(dirname "$dst")"
  echo "  pull $src  ->  $dst"
  "$MODAL_BIN" volume get "$VOLUME" "/$src" "$dst" --force 2>&1 | tail -3
}

if [[ "${1:-}" == "--all" ]]; then
  TARGETS=(cases evidence robust_scores judging summaries)
elif [[ -n "${1:-}" ]]; then
  TARGETS=("$@")
else
  TARGETS=(cases summaries)
fi

echo "Pulling from Modal volume '$VOLUME' (profile $(${MODAL_BIN} profile current))"
for t in "${TARGETS[@]}"; do
  pull "$t"
done
echo "Done."
