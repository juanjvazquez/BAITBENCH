#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VPS_JSONL="$ROOT/data/raw/make_datasets/analysis_exports/synthetic_540_claude_robust_scores.jsonl"
LOCAL_JSONL="$ROOT/data/outputs/make_datasets/claude_oom_retry_robust_scores.jsonl"
OUT_JSONL="$ROOT/data/outputs/make_datasets/synthetic_540_claude_robust_scores_merged.jsonl"

python3 - "$VPS_JSONL" "$LOCAL_JSONL" "$OUT_JSONL" <<'PY'
import json
import sys
from pathlib import Path

vps_path = Path(sys.argv[1])
local_path = Path(sys.argv[2])
out_path = Path(sys.argv[3])

headers = []
footers = []
records = {}

def load(path: Path, source: str) -> None:
    if not path.exists():
        return
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            obj = json.loads(line)
            t = obj.get("type")
            if t == "header":
                header = dict(obj)
                header["source"] = source
                headers.append(header)
            elif t == "footer":
                footer = dict(obj)
                footer["source"] = source
                footers.append(footer)
            elif t == "record":
                run_id = obj.get("run_id")
                if isinstance(run_id, str):
                    rec = dict(obj)
                    rec["merged_from"] = source
                    records[run_id] = rec

load(vps_path, "vps")
load(local_path, "local_retry")

ordered = [records[k] for k in sorted(records)]

out_path.parent.mkdir(parents=True, exist_ok=True)
with out_path.open("w", encoding="utf-8") as handle:
    handle.write(json.dumps({
        "type": "header",
        "sources": [str(vps_path), str(local_path)],
        "record_count": len(ordered),
    }) + "\n")
    for rec in ordered:
        handle.write(json.dumps(rec) + "\n")
    handle.write(json.dumps({
        "type": "footer",
        "record_count": len(ordered),
        "sources_seen": [h.get("source") for h in headers] + [f.get("source") for f in footers],
    }) + "\n")

print(f"wrote {len(ordered)} records to {out_path}")
PY
