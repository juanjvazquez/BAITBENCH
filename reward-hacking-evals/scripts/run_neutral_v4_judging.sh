#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-}"

if [[ "$MODE" != "smoke" && "$MODE" != "full" ]]; then
  echo "usage: NEUTRAL_JUDGE_MODEL=<openrouter-model-id> $0 <smoke|full>" >&2
  exit 2
fi

MODEL="${NEUTRAL_JUDGE_MODEL:?set NEUTRAL_JUDGE_MODEL to an exact OpenRouter model ID}"
BACKEND="${NEUTRAL_JUDGE_BACKEND:-openrouter}"
CONCURRENCY="${NEUTRAL_JUDGE_CONCURRENCY:-8}"
REASONING_EFFORT="${NEUTRAL_JUDGE_REASONING_EFFORT:-}"
APPEND="${APPEND:-0}"
MODEL_SLUG="$(printf '%s' "$MODEL" | tr '/:' '__')"

CANONICAL_INPUT="$ROOT/data/cases/make_datasets/v4_paper_canonical_1258_20260709.jsonl"
SMOKE_INPUT="$ROOT/data/cases/make_datasets/v4_t01_local_20260516/claude_claude-opus-4-6_regression_task_01_n100_s01_score_20260413_145159.json"

if [[ "$MODE" == "smoke" ]]; then
  INPUT="${INPUT:-$SMOKE_INPUT}"
  OUTPUT="${OUTPUT:-$ROOT/data/outputs/judging/neutral_smoke_${MODEL_SLUG}.jsonl}"
  CONCURRENCY=1
else
  if [[ ! -f "$CANONICAL_INPUT" ]]; then
    uv run python "$ROOT/scripts/build_canonical_v4_neutral_judge_input.py"
  fi
  INPUT="${INPUT:-$CANONICAL_INPUT}"
  OUTPUT="${OUTPUT:-$ROOT/data/outputs/judging/neutral_${MODEL_SLUG}_v4_20260709/judging_binary.jsonl}"
  if [[ -f "$OUTPUT" && "$APPEND" != "1" ]]; then
    echo "refusing to overwrite existing production output: $OUTPUT" >&2
    echo "set APPEND=1 to resume successful records and retry skipped records" >&2
    exit 2
  fi
fi

if [[ -n "$REASONING_EFFORT" ]]; then
  export OPENROUTER_REASONING_EFFORT="$REASONING_EFFORT"
fi

cmd=(
  uv run python "$ROOT/src/judges/judge_binary.py"
  "$INPUT"
  --output "$OUTPUT"
  --source-project make_datasets
  --backend "$BACKEND"
  --model "$MODEL"
  --concurrency "$CONCURRENCY"
)
if [[ "$APPEND" == "1" ]]; then
  cmd+=(--append)
fi

echo "mode=$MODE"
echo "backend=$BACKEND"
echo "model=$MODEL"
echo "input=$INPUT"
echo "output=$OUTPUT"
echo "concurrency=$CONCURRENCY"
echo "reasoning_effort=${REASONING_EFFORT:-provider-default}"
printf 'command='
printf ' %q' "${cmd[@]}"
printf '\n'

"${cmd[@]}"
