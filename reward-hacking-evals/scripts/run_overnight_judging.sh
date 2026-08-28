#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PIPELINE_PY="$ROOT/src/judges/run_shared_judging_pipeline.py"
SUMMARY_PY="$ROOT/src/scoring/summarize_judging_run.py"

timestamp() {
  date +"%Y-%m-%dT%H:%M:%S"
}

default_model_for_backend() {
  local backend="$1"
  case "$backend" in
    openai)
      printf 'gpt-5.4'
      ;;
    anthropic)
      printf 'claude-sonnet-4-6'
      ;;
    openrouter)
      printf 'z-ai/glm-5.1'
      ;;
    *)
      printf '%s' "$backend"
      ;;
  esac
}

safe_model_dir_name() {
  local model="$1"
  printf '%s' "${model//\//__}"
}

RUN_LABEL="${RUN_LABEL:-$(date +"%Y%m%d_%H%M%S")}"
OUT_ROOT="${OUT_ROOT:-$ROOT/data/outputs/judging/$RUN_LABEL}"
DATASETS="${DATASETS:-make_datasets autoresearch}"
BACKENDS="${BACKENDS:-openai anthropic openrouter}"
CONCURRENCY="${CONCURRENCY:-6}"
PREFIX="${PREFIX:-judging}"
APPEND="${APPEND:-1}"
LIMIT="${LIMIT:-}"
AUTO_SUMMARIZE="${AUTO_SUMMARIZE:-1}"

mkdir -p "$OUT_ROOT"

echo "[$(timestamp)] Starting overnight judging run"
echo "[$(timestamp)] OUT_ROOT=$OUT_ROOT"
echo "[$(timestamp)] DATASETS=$DATASETS"
echo "[$(timestamp)] BACKENDS=$BACKENDS"

for dataset in $DATASETS; do
  case "$dataset" in
    make_datasets)
      input_path="$ROOT/data/cases/make_datasets/cases.jsonl"
      source_project="make_datasets"
      ;;
    autoresearch)
      input_path="$ROOT/data/cases/autoresearch/cases.jsonl"
      source_project="autoresearch"
      ;;
    *)
      echo "Unknown dataset: $dataset" >&2
      exit 2
      ;;
  esac

  if [[ ! -f "$input_path" ]]; then
    echo "Missing input cases file for $dataset: $input_path" >&2
    exit 2
  fi

  for backend_spec in $BACKENDS; do
    backend_alias="$backend_spec"
    backend="$backend_spec"
    fixed_model="auto"

    # Optional backend-spec syntax:
    #   alias=backend:model
    # Example:
    #   openrouter_flash=openrouter:google/gemini-2.5-flash
    if [[ "$backend_spec" == *=* ]]; then
      backend_alias="${backend_spec%%=*}"
      backend_model_spec="${backend_spec#*=}"
      backend="${backend_model_spec%%:*}"
      if [[ "$backend_model_spec" == *:* ]]; then
        fixed_model="${backend_model_spec#*:}"
      fi
    fi

    backend_upper="$(printf '%s' "$backend_alias" | tr '[:lower:]-' '[:upper:]_')"
    model_var="MODEL_${backend_upper}"
    concurrency_var="CONCURRENCY_${backend_upper}"
    model="${!model_var:-$fixed_model}"
    backend_concurrency="${!concurrency_var:-$CONCURRENCY}"

    resolved_model="$model"
    if [[ "$resolved_model" == "auto" ]]; then
      resolved_model="$(default_model_for_backend "$backend")"
    fi

    model_dir_name="$(safe_model_dir_name "$resolved_model")"
    out_dir="$OUT_ROOT/$dataset/$model_dir_name"
    mkdir -p "$out_dir"
    log_path="$out_dir/${PREFIX}.log"

    cmd=(
      python3 "$PIPELINE_PY"
      "$input_path"
      --output-dir "$out_dir"
      --prefix "$PREFIX"
      --source-project "$source_project"
      --backend "$backend"
      --model "$model"
      --concurrency "$backend_concurrency"
    )
    if [[ "$APPEND" == "1" ]]; then
      cmd+=(--append)
    fi
    if [[ -n "$LIMIT" ]]; then
      cmd+=(--limit "$LIMIT")
    fi

    {
      echo "[$(timestamp)] dataset=$dataset backend=$backend alias=$backend_alias model=$resolved_model out_dir=$model_dir_name concurrency=$backend_concurrency"
      printf '[$(timestamp)] command='
      printf ' %q' "${cmd[@]}"
      printf '\n'
      "${cmd[@]}"
    } 2>&1 | tee "$log_path"
  done
done

if [[ "$AUTO_SUMMARIZE" == "1" ]]; then
  echo "[$(timestamp)] Building post-run summary"
  python3 "$SUMMARY_PY" "$OUT_ROOT"
fi

echo "[$(timestamp)] Finished overnight judging run"
