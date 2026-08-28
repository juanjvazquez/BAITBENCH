# cases

Canonical normalized case files grouped by source dataset.

Each source should follow the same pattern:

- `records/<RUN_ID>.json` for the canonical per-run case
- `cases.jsonl` for the aggregate corpus
- optional derived formats such as `judge_inputs/*.xml`

The JSON case is the source of truth. Derived XML exists only for judge compatibility.
