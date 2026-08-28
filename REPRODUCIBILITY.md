# Reproducing BAITBENCH results

The single supported entry point is:

```bash
python3 reproduce.py --quick  # structural and data-integrity check
python3 reproduce.py          # full Monte Carlo analysis
```

Both commands first run `verify_repository.py`, which checks the canonical
dataset identity, required files, excluded run directories, and common secret
patterns. The analysis then runs entirely from the included derived data.

## Outputs

| Paper result | Generated artifact |
|---|---|
| Pooled and task-family reward-hacking rates | `paper_tables/task-rates.json` |
| Model-level reward-hacking rates | `paper_tables/model-rates.json` |
| Engagement-conditional rates | `paper_tables/engagement-rates.json` |
| Prompt-condition effects | `cluster_bootstrap.json`, `paper_tables/model-prompt-rates.json` |
| Original-judge agreement | `paper_tables/judge-agreement.json` |
| Neutral-judge audit | `neutral_judge.json`, `neutral_judge.md` |
| Same-family judge analysis | `judge_family_bias.json` |
| OpenCode versus native scaffold | `bayesian_harness.json` |
| Validity-logging ablation | `validity_logging_ablation.md` |
| Robustness and degradation | `paper_tables/robustness-coverage.json`, `paper_tables/robust-gap-summary.json`, `paper_tables/task-degradation.json` |
| Observable behavior and qualitative analyses | `observable_behavior.md`, `evaluator_calls_and_judge_reasons.md`, `partial_oversight.md`, `qualitative_examples.md` |

All artifacts are written beneath the selected output directory. Every run
also writes `run_manifest.json`, recording commands, parameters, input hashes,
return codes, and audit results.

## Scientific units

The rate analyses operate on canonical agent runs. Cluster bootstraps resample
dataset instances defined by task family, dataset size, and seed, preserving
paired prompt conditions and model observations within each instance. The
exact implementation and random seeds are recorded by the generated manifest.

