# Neutral judging runbook (2026-07-09)

This runbook adds a neutral binary reward-hacking judge to the exact 1,258
canonical BAITBENCH v4 cases. It intentionally does not run the mechanism-family
or deprecated H-label stages, which are unnecessary for the rebuttal's headline
and judge-circularity analyses.

## 1. Build and verify the frozen input

```bash
uv run python scripts/build_canonical_v4_neutral_judge_input.py
```

Expected output: 1,258 cases, split 418/420/420 across entity overlap,
near-duplicate leakage, and no-signal classification. The generated manifest
records source-batch counts and the JSONL SHA-256.

## 2. Verify credentials

The current judge loads the repository `.env` with override enabled. Check that
the effective `OPENROUTER_API_KEY` belongs to the intended reimbursable account.
Do not print or paste the key into logs.

## 3. Smoke test

Use an exact, verified OpenRouter model ID. For this rebuttal, the recommended
neutral judge is `z-ai/glm-5.2` at `high` reasoning effort. GLM 5.2 supports
`high` and `xhigh`; `high` is the deliberate choice because the current
16,384-token cap must leave enough tokens for the schema-constrained final
judgment and its three to six evidence bullets.

```bash
NEUTRAL_JUDGE_MODEL='z-ai/glm-5.2' \
NEUTRAL_JUDGE_REASONING_EFFORT=high \
  scripts/run_neutral_v4_judging.sh smoke
```

Accept the smoke only if it contains one non-skipped record with the intended
model, a permitted categorical label, `judge_version=v4`, prompt version `p4`,
schema version `v4`, evidence, and a footer with `failure_count=0`.

## 4. Full run

```bash
NEUTRAL_JUDGE_MODEL='z-ai/glm-5.2' \
NEUTRAL_JUDGE_REASONING_EFFORT=high \
NEUTRAL_JUDGE_CONCURRENCY=8 \
  scripts/run_neutral_v4_judging.sh full
```

To resume an interrupted batch while retaining successful records and retrying
skipped ones:

```bash
APPEND=1 \
NEUTRAL_JUDGE_MODEL='<model-id>' \
NEUTRAL_JUDGE_REASONING_EFFORT=high \
NEUTRAL_JUDGE_CONCURRENCY=8 \
  scripts/run_neutral_v4_judging.sh full
```

Keep each judge model in a separate output path. Never reuse the same output
file with a different model ID.

## 5. Aggregation policy to freeze before reporting

The submitted paper's 57.1% rate is the pooled mean of Claude and GPT binary
decisions. The paper statistics script currently hard-codes those two judges;
merely producing this new output will not change the paper tables.

For the rebuttal, preserve 57.1% as the original estimate and report:

1. the neutral judge's standalone rate;
2. the three-judge pooled mean as the direct extension of the submitted
   estimator;
3. three-way categorical majority as a sensitivity analysis;
4. agreement and an explicit sensitivity for `unclear` labels.

Do not silently map `unclear` to `not_reward_hacking` without also reporting an
exclude/abstain sensitivity.
