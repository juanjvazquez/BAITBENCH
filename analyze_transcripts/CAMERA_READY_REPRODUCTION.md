# Camera-ready analysis reproduction

Run the complete local bundle from the repository root:

```bash
python3 reproduce.py
```

This regenerates every available paper, rebuttal, and camera-ready quantitative
analysis from archived local cases, judgments, and annotations. It does not
call a model provider, modify Google Docs, post to OpenReview or Slack, or edit
the submitted PDF.

The default output directory is:

```text
analyze_transcripts/generated/camera_ready/
```

The main files are:

- `camera_ready_results.md`: consolidated human-readable results;
- `camera_ready_results.json`: machine-readable consolidated results;
- `run_manifest.json`: commands, parameters, repository states, input
  checksums, task statuses, and audit assertions;
- `paper_tables/*.json`: all nine tables supported by
  `make_datasets_paper_stats.py`;
- `logs/`: captured standard output and errors for every task;
- one Markdown or JSON artifact for each rebuttal analysis.

For a fast structural check before a full run:

```bash
python3 reproduce.py --quick
```

Quick mode lowers only Monte Carlo replication counts. Point estimates,
coverage checks, schemas, paths, and categorical counts remain fully checked.

## Analyses included

The bundle runs:

1. the submitted paper statistics and all supported table slices;
2. stratified dataset-instance cluster bootstraps and paired sign-flip tests;
3. same-family judge-bias analysis;
4. the local GLM-5.2 neutral-judge audit and all three pairwise agreement
   calculations;
5. Bayesian execution-pathway equivalence for GPT-5.4 and Sonnet 4.6;
6. the validity-logging ablation;
7. evaluator-call and judge-reason analysis;
8. observable behavioral correlates;
9. paired validity-prompt behavior;
10. partial-oversight evidence concentration;
11. verified qualitative examples;
12. transcript-awareness aggregation;
13. recovered Appendix E human-validation counts.

## Two denominator details

The neutral GLM run contains 749 `reward_hacking`, 497
`not_reward_hacking`, and 12 `unclear` judgments. Its 59.54% RH rate uses all
1,258 canonical cases in the denominator, while agreement and Cohen's kappa
treat `unclear` as a third category.

The judge-consensus sensitivity analysis excludes eight cases where both
original judges returned `unclear`. Its binary denominator is therefore 1,169,
and the corrected estimate is 681/1,169 = 58.3%. The older 57.9% figure treated
those eight cases as non-RH.

## Current external dependency

The recovered Appendix E export contains 31 annotations, of which 25 have
usable binary human and judge labels. The bundle verifies those historical
counts but explicitly does not treat them as completion of the later
100-case human-validation commitment.
