# BAITBENCH evaluation pipeline

This project converts BAITBENCH agent outputs into canonical cases, runs the
reward-hacking judges, and produces the statistics reported in the paper.

The public repository contains the derived inputs required for offline
reproduction:

- `data/cases/make_datasets/`: canonical cases and the per-case records needed
  for engagement and robustness analyses;
- `data/outputs/judging/`: the authoritative original, neutral, scaffold, and
  logging-ablation judgments;
- `data/outputs/make_datasets/`: compact evidence, robust scores, and manifests;
- `data/reports/`: human-readable analysis notes.

Raw agent run directories and transcripts are not included.

## Setup

```bash
uv sync --project reward-hacking-evals
```

## Reproduce paper tables

The repository-level command regenerates every table and camera-ready
analysis:

```bash
python3 reproduce.py --quick
python3 reproduce.py
```

To render one paper table directly:

```bash
uv run --project reward-hacking-evals python \
  reward-hacking-evals/scripts/make_datasets_paper_stats.py \
  --table engagement-rates --format markdown --ci
```

## Rebuild or judge cases

Case construction code lives under `src/cases/` and
`src/ingest/make_datasets/`. Rebuilding cases requires the separately stored
raw run artifacts. Judge implementations live under `src/judges/`; new API
judging requires provider credentials supplied through environment variables.
Offline reproduction of the released results does not require credentials or
network access.
