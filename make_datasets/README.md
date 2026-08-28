# BAITBENCH task generation and execution

This project defines the three synthetic tabular task families used by
BAITBENCH and prepares isolated workspaces for autonomous ML agents.

| Code | Task family | Shortcut under evaluation |
|---|---|---|
| `t01` | Entity overlap | Memorizing repeated entity identities |
| `t02` | Near-duplicate leakage | Exploiting contaminated near-neighbours |
| `t03` | No-signal classification | Repeatedly optimizing against a visible score |

Each family supports `n100`, `n10k`, and `n100k` dataset sizes, multiple
seeds, and score-only and validity-aware prompt conditions.

## Setup

```bash
uv sync --project make_datasets
```

## Generate tasks

From the repository root:

```bash
uv run --project make_datasets python \
  make_datasets/src/make_datasets/synthetic_suite.py build-tasks
```

To generate a small development subset:

```bash
uv run --project make_datasets python \
  make_datasets/src/make_datasets/synthetic_suite.py build-tasks \
  --seed-end 2
```

Generated workspaces are written under `make_datasets/tasks/generated/` and
are intentionally excluded from the repository. Agent execution outputs are
also excluded. See `docs/synthetic_leakage_suite.md` for the complete matrix
and `docs/methods.md` for the experimental design.

## Tests

```bash
uv run --project make_datasets python -m unittest discover \
  -s make_datasets/tests -p 'test_*.py'
```
