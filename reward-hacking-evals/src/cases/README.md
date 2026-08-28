# cases

Shared case schema, source-specific case builders, and derived evidence flags.

The canonical cross-project evidence format is JSON:

- one per-run case JSON under `data/cases/<source>/records/`
- one aggregate `cases.jsonl` under `data/cases/<source>/`

XML judge packages are optional renderings derived from those case records.
