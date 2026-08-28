#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/data/raw/make_datasets/all_run_transcripts"

mkdir -p "$DEST"

TMP_FILES="$(mktemp)"
trap 'rm -f "$TMP_FILES"' EXIT

ssh -i ~/.ssh/autoresearch team@87.99.129.5 \
  "python3 - <<'PY'
from pathlib import Path

base = Path('/home/team/make_datasets_runs')
for run_dir in sorted(path for path in base.iterdir() if path.is_dir()):
    for rel in (
        'transcript.json',
        'metadata.json',
        'agent_stderr.log',
        'agent_exit_code.txt',
    ):
        candidate = run_dir / rel
        if candidate.exists():
            print(f'{run_dir.name}/{rel}')
PY" > "$TMP_FILES"

rsync -az \
  -e "ssh -i ~/.ssh/autoresearch" \
  --files-from="$TMP_FILES" \
  team@87.99.129.5:/home/team/make_datasets_runs/ \
  "$DEST/"

python3 - <<PY
from pathlib import Path

base = Path("$DEST")
runs = sorted(path for path in base.iterdir() if path.is_dir())
transcripts = list(base.glob("*/transcript.json"))
metadata = list(base.glob("*/metadata.json"))
stderr_logs = list(base.glob("*/agent_stderr.log"))
exit_codes = list(base.glob("*/agent_exit_code.txt"))

print(f"Mirrored make_datasets run dirs: {len(runs)}")
print(f"Mirrored make_datasets transcripts: {len(transcripts)}")
print(f"Mirrored make_datasets metadata: {len(metadata)}")
print(f"Mirrored make_datasets stderr logs: {len(stderr_logs)}")
print(f"Mirrored make_datasets exit codes: {len(exit_codes)}")
PY
