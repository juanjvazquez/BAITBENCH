# Data manifest

The included code and derived evaluation data are released under the
repository's MIT License.

## Included

The repository includes the derived data needed to reproduce BAITBENCH results:

- the canonical packaged cases and per-case records consumed by the verified
  paper analyses under `reward-hacking-evals/data/cases/`;
- the full 1,258-case paper dataset at
  `reward-hacking-evals/data/cases/make_datasets/v4_paper_canonical_1258_20260709.jsonl`;
- the authoritative original, neutral-judge, OpenCode-scaffold,
  transcript-awareness, and logging-ablation judgments
  under `reward-hacking-evals/data/outputs/judging/`;
- compact evidence, robust scores, and manifests under
  `reward-hacking-evals/data/outputs/make_datasets/`;
- derived reports, annotations, and camera-ready analysis outputs.

The full canonical paper dataset contains 1,258 unique run IDs. Its SHA-256 is:

```text
6e382e83faba05f9c31cf773f2a1e5f990cf0c99aec0d7bead3ececf796ca1a3
```

## Excluded

The consolidation deliberately excludes source run material and local state:

- all directories named `runs`;
- `reward-hacking-evals/data/raw/`;
- raw transcripts and mirrored VPS sessions;
- generated task workspaces and archived generated datasets;
- temporary robust-evaluation workspaces and dependency caches;
- virtual environments, tool caches, Git metadata, `.env` files, SSH keys,
  and provider credentials;
- local VPS backups and historical artifact copies outside this repository.
- duplicate aggregate case files, judge retries, smoke tests, and superseded
  intermediate exports that are not consumed by the reproduction command.

The included canonical cases contain the evidence presented to the judges.
They are derived evaluation records, not executable agent run directories.

## Rebuilding from raw runs

Raw runs are unnecessary for reproducing the published statistics. They are
required only to rebuild canonical cases from the original agent execution.
That provenance workflow remains documented in `reward-hacking-evals/README.md`;
the raw artifacts should be distributed as a separately versioned dataset if
they are released.
