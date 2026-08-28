# judges

Prompt-based reward-hacking detectors and shared judging helpers.

Current prompt-based entrypoints:

- `judge_binary.py`: binary `reward_hacking | not_reward_hacking | unclear`
- `judge_classification_family.py`: classify likely-positive runs to the broader parent/mechanism-family layer (canonical mechanism layer)
- `judge_classification.py` (soft-deprecated): classify likely-positive runs into the parent/mechanism/H-label taxonomy. Skipped by default; opt in with `--include-h-classification`.
- `run_shared_judging_pipeline.py`: staged pipeline that runs binary first, then family classification on the binary positives. Pass `--include-h-classification` to also run the deprecated H-label stage.

Each judge writes output records with a `judgment_schema_version` field. The current schema is `v2`, in which evidence bullets are objects of shape `{text, refs[]}` (refs are flat objects with nullable `step`, `step_end`, `file`, `quote`). Older outputs without this field are v1 (free-text evidence bullets).

These scripts accept either:

- a canonical case JSON file or directory, such as `data/cases/make_datasets/records/` or `data/cases/autoresearch/records/`
- a canonical case JSONL, such as `data/cases/make_datasets/cases.jsonl`
- an XML file or directory of XML files, such as `data/cases/autoresearch/judge_inputs/`
- a JSONL evidence file, such as `data/outputs/make_datasets/synthetic_540_codex_evidence.jsonl`

For `make_datasets`, you can additionally pass `--robust-results` to enrich the evidence dump with robust-eval results.

The shared pipeline is intentionally modeled after the existing `make_datasets` flow:

1. package evidence
2. optionally enrich with robust evals
3. run a binary judge over all cases
4. run a classification judge over the binary positives

Both judge stages support `--append` so long-running batches can be resumed without rewriting existing records.
