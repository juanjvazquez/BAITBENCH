#!/usr/bin/env bash
# Pull human annotations from the rh-evals-results Modal volume into
# the repo at data/annotations_backup/, then create a git commit
# so they're preserved off-volume.
#
# The Modal volume is the canonical store, but it's a single point of
# failure. A periodic git-committed snapshot means a `modal volume
# delete` (or any other one-shot mistake) doesn't lose annotations
# the team has spent time creating.
#
# Usage:
#   bash scripts/backup_annotations.sh             # pull + commit, no push
#   bash scripts/backup_annotations.sh --push      # also push to origin
#   bash scripts/backup_annotations.sh --no-commit # pull only, skip commit
#
# Idempotent: re-running pulls fresh files but only commits if something
# actually changed. Logs to /tmp/backup_annotations.log per repo rules.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/scripts/lib/load_env.sh" || true

MODAL_BIN="${MODAL_BIN:-modal}"
if ! command -v "$MODAL_BIN" >/dev/null 2>&1; then
  MODAL_BIN="$HOME/Documents/Codex/2026-04-26-modal-token-set-token-id-ak/.venv-modal/bin/modal"
fi
if ! command -v "$MODAL_BIN" >/dev/null 2>&1; then
  echo "backup_annotations.sh: cannot find 'modal' CLI" >&2
  exit 1
fi

VOLUME="rh-evals-results"
BACKUP_DIR="$ROOT/data/annotations_backup"
LOG_FILE="/tmp/backup_annotations.log"

PUSH=0
DO_COMMIT=1
for arg in "$@"; do
  case "$arg" in
    --push)      PUSH=1 ;;
    --no-commit) DO_COMMIT=0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

mkdir -p "$BACKUP_DIR"
# Make sure the directory shows up in git even when there are no
# annotations yet, so a future pull into a clean checkout finds it.
touch "$BACKUP_DIR/.gitkeep"

echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] backing up /annotations/ from $VOLUME -> $BACKUP_DIR" 2>&1 | tee -a "$LOG_FILE"

# `modal volume get` clobbers the destination cleanly. Pull the whole
# /annotations/ subtree (per-run JSON + _history.jsonl). Wipe the
# backup dir first so deletions on the volume propagate to the backup
# (otherwise stale annotation files would linger in git forever).
find "$BACKUP_DIR" -mindepth 1 ! -name '.gitkeep' -delete

# `modal volume get` exits non-zero when the source path is empty;
# tolerate that so the first run before any annotations exist still
# completes (just produces an empty backup dir + .gitkeep).
if ! "$MODAL_BIN" volume get "$VOLUME" /annotations "$BACKUP_DIR" --force 2>&1 | tee -a "$LOG_FILE"; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] no annotations on volume yet (or pull failed); continuing" 2>&1 | tee -a "$LOG_FILE"
fi

# `modal volume get` may nest the contents inside a directory named
# 'annotations'. Flatten so files live directly under $BACKUP_DIR.
if [[ -d "$BACKUP_DIR/annotations" ]]; then
  shopt -s dotglob
  mv "$BACKUP_DIR/annotations/"* "$BACKUP_DIR/" 2>/dev/null || true
  rmdir "$BACKUP_DIR/annotations"
  shopt -u dotglob
fi

ANN_COUNT=$(find "$BACKUP_DIR" -maxdepth 1 -name '*.json' | wc -l | tr -d ' ')
HIST_LINES=0
if [[ -f "$BACKUP_DIR/_history.jsonl" ]]; then
  HIST_LINES=$(wc -l < "$BACKUP_DIR/_history.jsonl" | tr -d ' ')
fi
echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] backed up $ANN_COUNT annotation file(s), $HIST_LINES history line(s)" 2>&1 | tee -a "$LOG_FILE"

if [[ "$DO_COMMIT" -eq 0 ]]; then
  echo "skipping commit (--no-commit)"
  exit 0
fi

# Stage just the backup dir; don't sweep up unrelated working-tree edits.
cd "$ROOT"
git add data/annotations_backup

if git diff --cached --quiet; then
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] no annotation changes since last backup; nothing to commit" 2>&1 | tee -a "$LOG_FILE"
  exit 0
fi

TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
COMMIT_MSG="chore(annotations): backup $ANN_COUNT annotation(s), $HIST_LINES history line(s) at $TS"
git commit -m "$COMMIT_MSG" 2>&1 | tee -a "$LOG_FILE"

if [[ "$PUSH" -eq 1 ]]; then
  BRANCH="$(git rev-parse --abbrev-ref HEAD)"
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] pushing $BRANCH to origin" 2>&1 | tee -a "$LOG_FILE"
  git push origin "$BRANCH" 2>&1 | tee -a "$LOG_FILE"
else
  echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] committed locally; pass --push to also push to origin"
fi
