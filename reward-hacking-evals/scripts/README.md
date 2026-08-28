# scripts

Operational scripts for mirroring artifacts and maintaining the eval workspace.

These scripts are meant to run from the standalone `reward-hacking-evals` repo.
They resolve paths relative to this repo root rather than the older umbrella
workspace.

Local-first entrypoints:

- `build_make_datasets_local.sh` rerun `make_datasets` evidence extraction and robust evals from the local raw mirror, then build canonical JSON cases.
- `mirror_autoresearch_run_artifacts.sh` mirror the VPS workspace artifacts for `autoresearch-modal-runs/` and `autoresearch-posttraining-runs/`.
- `build_autoresearch_judge_inputs_from_vps.sh` build canonical JSON cases plus derived XML judge inputs from the mirrored autoresearch runs and fetch matching viewer `transcript.md?max_output_lines=0` / `run.json` files when available.
- `run_overnight_judging.sh` run the prompt-based judge across one or more datasets and one or more backends, writing resumable outputs under `data/outputs/judging/<run_label>/`. It stages `binary -> family -> full classification`.
- `summarize_judging_run.sh` validate classification outputs, compute inter-rater agreement, and write a compact summary for a judging run.

Legacy / reference mirroring helpers:

- `mirror_make_datasets_analysis.sh` mirrors historical VPS-produced packaged outputs.
- `mirror_make_datasets_raw_540.sh` refreshes the canonical `make_datasets` raw mirror.
- `mirror_autoresearch_vps_raw.sh` mirrors raw Claude/Codex VPS session logs for optional provenance debugging.

Note:

- a few mirroring helpers still optionally locate a separate `repo_make_datasets` checkout
- if it is not adjacent to this repo, set `MAKE_DATASETS_SOURCE_ROOT=/abs/path/to/repo_make_datasets`
