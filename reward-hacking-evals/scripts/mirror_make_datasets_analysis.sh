#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/data/raw/make_datasets/analysis_exports"

mkdir -p "$DEST"

rsync -az \
  -e "ssh -i ~/.ssh/autoresearch" \
  --include '*/' \
  --include '*.json' \
  --include '*.jsonl' \
  --include 'logs/***' \
  --include 'status/***' \
  --exclude '*' \
  team@87.99.129.5:/home/team/make_datasets_analysis/ \
  "$DEST/"
