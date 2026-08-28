#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/data/raw/make_datasets/run_bundles"

resolve_repo_make_datasets() {
  local candidate
  if [[ -n "${MAKE_DATASETS_SOURCE_ROOT:-}" && -d "${MAKE_DATASETS_SOURCE_ROOT}" ]]; then
    printf '%s\n' "$(cd "${MAKE_DATASETS_SOURCE_ROOT}" && pwd)"
    return 0
  fi
  for candidate in \
    "$ROOT/../repo_make_datasets" \
    "$ROOT/../2026-04-20-i-need-to-evaluate-all-the/repo_make_datasets"
  do
    if [[ -d "$candidate" ]]; then
      printf '%s\n' "$(cd "$candidate" && pwd)"
      return 0
    fi
  done
  candidate="$(find "$ROOT/.." -maxdepth 2 -type d -name repo_make_datasets -print -quit 2>/dev/null || true)"
  if [[ -n "$candidate" && -d "$candidate" ]]; then
    printf '%s\n' "$(cd "$candidate" && pwd)"
    return 0
  fi
  return 1
}

MAKE_DATASETS_SOURCE="$(resolve_repo_make_datasets || true)"
if [[ -z "$MAKE_DATASETS_SOURCE" ]]; then
  echo "Could not locate repo_make_datasets." >&2
  echo "Set MAKE_DATASETS_SOURCE_ROOT=/abs/path/to/repo_make_datasets and retry." >&2
  exit 1
fi

MANIFEST="$MAKE_DATASETS_SOURCE/runs/batches/synthetic_leakage_batch_manifest_540_latest.json"

mkdir -p "$DEST"

TMP_FILES="$(mktemp)"
TMP_EXISTING="$(mktemp)"
trap 'rm -f "$TMP_FILES" "$TMP_EXISTING"' EXIT

python3 - "$MANIFEST" "$TMP_FILES" <<'PY'
import json
import sys
from pathlib import Path

manifest_path = Path(sys.argv[1])
output_path = Path(sys.argv[2])

workspace_files = [
    "README.md",
    "evaluate.py",
    "experimental_setup.json",
    "metrics.json",
    "modal_app.py",
    "modal_dispatch.py",
    "prepare.py",
    "program.md",
    "pyproject.toml",
    "results.md",
    "results.tsv",
    "run.log",
    "solution.py",
    "task.json",
    "test.csv",
    "timer.py",
    "train.csv",
    "train_sft.py",
    "uv.lock",
]

entries = json.loads(manifest_path.read_text())
paths: list[str] = []
for entry in entries:
    run_id = entry["run_id"]
    paths.extend(
        [
            f"{run_id}/transcript.json",
            f"{run_id}/metadata.json",
            f"{run_id}/agent_stderr.log",
            f"{run_id}/agent_exit_code.txt",
            f"{run_id}/.private_task_assets/robust_test.csv",
        ]
    )
    for name in workspace_files:
        paths.append(f"{run_id}/workspace/{name}")

output_path.write_text("\n".join(paths) + "\n", encoding="utf-8")
PY

ssh -i ~/.ssh/autoresearch team@87.99.129.5 \
  "python3 -c 'import sys; from pathlib import Path; base=Path(\"/home/team/make_datasets_runs\"); [print(rel) for rel in (line.strip() for line in sys.stdin) if rel and (base / rel).exists()]'" \
  < "$TMP_FILES" > "$TMP_EXISTING"

rsync -az \
  -e "ssh -i ~/.ssh/autoresearch" \
  --files-from="$TMP_EXISTING" \
  team@87.99.129.5:/home/team/make_datasets_runs/ \
  "$DEST/"
