# scoring

Metrics, agreement summaries, and error reports for reward-hacking judgments.

Main entrypoints:

- `aggregate_judgments.py`: compute inter-rater agreement across binary or classification judge JSONLs.
- `validate_judgments.py`: validate full classification outputs against the canonical taxonomy.
- `summarize_judging_run.py`: walk an overnight judging run directory, validate classification outputs, aggregate agreement, and write `summary.json` plus `summary.md`.
