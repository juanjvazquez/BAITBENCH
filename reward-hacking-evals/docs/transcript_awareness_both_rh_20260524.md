# Transcript Awareness Among Both-Judge Reward-Hacking Runs

Date generated: 2026-05-24

This page summarizes transcript-awareness labels only over canonical
`make_datasets` runs where both v4 binary judges labeled the submitted solution
`reward_hacking`.

Canonicalization uses the same rule as
`scripts/make_datasets_paper_stats.py`: reruns replace the corresponding
original run and smoke/test extras are excluded.

## Reproduction

Run from the `reward-hacking-evals` repo root:

```sh
uv run scripts/summarize_transcript_awareness_both_rh.py --include-model-task
```

Inputs:

- v4 binary judge outputs under `data/outputs/judging/`
- transcript-awareness judgments at `data/outputs/judging/transcript_awareness_gemini_flash/transcript_awareness_openrouter.jsonl`

The summarizer script is
`scripts/summarize_transcript_awareness_both_rh.py`.

## Overall

Total both-RH canonical runs: `681`. Transcript-awareness judgments exist for
`617` of them; `64` are missing from the transcript-awareness JSONL.

| Transcript awareness label | Count | Rate of both-RH denom |
|---|---:|---:|
| `mechanism_awareness_framed_as_success` | 289 | 42.4% |
| `explicit_invalidity_awareness` | 98 | 14.4% |
| `generalization_awareness_only` | 187 | 27.5% |
| `no_obvious_awareness` | 43 | 6.3% |
| missing transcript-awareness judgment | 64 | 9.4% |

- Any awareness: `574/681` = 84.3%
- Shortcut/invalidity awareness: `387/681` = 56.8%

## By Model

| Group | Both-RH denom | Missing | Any awareness | Shortcut/invalidity | None | Gen-only | Mechanism-as-success | Explicit invalid | Self-report |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| claude-opus-4-6 | 132 | 0 | 130 (98.5%) | 68 (51.5%) | 2 (1.5%) | 62 (47.0%) | 41 (31.1%) | 27 (20.5%) | 0 (0.0%) |
| claude-sonnet-4-6 | 118 | 0 | 118 (100.0%) | 92 (78.0%) | 0 (0.0%) | 26 (22.0%) | 51 (43.2%) | 41 (34.7%) | 0 (0.0%) |
| gpt-5.4 | 88 | 0 | 87 (98.9%) | 65 (73.9%) | 1 (1.1%) | 22 (25.0%) | 48 (54.5%) | 17 (19.3%) | 0 (0.0%) |
| kimi-k2.5 | 34 | 4 | 14 (41.2%) | 7 (20.6%) | 16 (47.1%) | 7 (20.6%) | 7 (20.6%) | 0 (0.0%) | 0 (0.0%) |
| gemini-3.1-pro-preview | 106 | 36 | 63 (59.4%) | 33 (31.1%) | 7 (6.6%) | 30 (28.3%) | 25 (23.6%) | 8 (7.5%) | 0 (0.0%) |
| gemini-3-flash-preview | 108 | 21 | 79 (73.1%) | 47 (43.5%) | 8 (7.4%) | 32 (29.6%) | 43 (39.8%) | 4 (3.7%) | 0 (0.0%) |
| deepseek-v4-pro | 95 | 3 | 83 (87.4%) | 75 (78.9%) | 9 (9.5%) | 8 (8.4%) | 74 (77.9%) | 1 (1.1%) | 0 (0.0%) |

## By Task Family

| Group | Both-RH denom | Missing | Any awareness | Shortcut/invalidity | None | Gen-only | Mechanism-as-success | Explicit invalid | Self-report |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Entity overlap | 339 | 26 | 289 (85.3%) | 205 (60.5%) | 24 (7.1%) | 84 (24.8%) | 149 (44.0%) | 56 (16.5%) | 0 (0.0%) |
| Near-duplicate leakage | 301 | 36 | 250 (83.1%) | 167 (55.5%) | 15 (5.0%) | 83 (27.6%) | 138 (45.8%) | 29 (9.6%) | 0 (0.0%) |
| No-signal classification | 41 | 2 | 35 (85.4%) | 15 (36.6%) | 4 (9.8%) | 20 (48.8%) | 2 (4.9%) | 13 (31.7%) | 0 (0.0%) |

## By Prompt

| Group | Both-RH denom | Missing | Any awareness | Shortcut/invalidity | None | Gen-only | Mechanism-as-success | Explicit invalid | Self-report |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| baseline | 358 | 23 | 313 (87.4%) | 215 (60.1%) | 22 (6.1%) | 98 (27.4%) | 185 (51.7%) | 30 (8.4%) | 0 (0.0%) |
| validity | 323 | 41 | 261 (80.8%) | 172 (53.3%) | 21 (6.5%) | 89 (27.6%) | 104 (32.2%) | 68 (21.1%) | 0 (0.0%) |

## Model x Task Family

| Group | Both-RH denom | Missing | Any awareness | Shortcut/invalidity | None | Gen-only | Mechanism-as-success | Explicit invalid | Self-report |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| claude-opus-4-6 / Entity overlap | 59 | 0 | 59 (100.0%) | 33 (55.9%) | 0 (0.0%) | 26 (44.1%) | 19 (32.2%) | 14 (23.7%) | 0 (0.0%) |
| claude-opus-4-6 / Near-duplicate leakage | 59 | 0 | 57 (96.6%) | 26 (44.1%) | 2 (3.4%) | 31 (52.5%) | 21 (35.6%) | 5 (8.5%) | 0 (0.0%) |
| claude-opus-4-6 / No-signal classification | 14 | 0 | 14 (100.0%) | 9 (64.3%) | 0 (0.0%) | 5 (35.7%) | 1 (7.1%) | 8 (57.1%) | 0 (0.0%) |
| claude-sonnet-4-6 / Entity overlap | 56 | 0 | 56 (100.0%) | 31 (55.4%) | 0 (0.0%) | 25 (44.6%) | 3 (5.4%) | 28 (50.0%) | 0 (0.0%) |
| claude-sonnet-4-6 / Near-duplicate leakage | 56 | 0 | 56 (100.0%) | 56 (100.0%) | 0 (0.0%) | 0 (0.0%) | 48 (85.7%) | 8 (14.3%) | 0 (0.0%) |
| claude-sonnet-4-6 / No-signal classification | 6 | 0 | 6 (100.0%) | 5 (83.3%) | 0 (0.0%) | 1 (16.7%) | 0 (0.0%) | 5 (83.3%) | 0 (0.0%) |
| gpt-5.4 / Entity overlap | 45 | 0 | 45 (100.0%) | 40 (88.9%) | 0 (0.0%) | 5 (11.1%) | 31 (68.9%) | 9 (20.0%) | 0 (0.0%) |
| gpt-5.4 / Near-duplicate leakage | 39 | 0 | 38 (97.4%) | 25 (64.1%) | 1 (2.6%) | 13 (33.3%) | 17 (43.6%) | 8 (20.5%) | 0 (0.0%) |
| gpt-5.4 / No-signal classification | 4 | 0 | 4 (100.0%) | 0 (0.0%) | 0 (0.0%) | 4 (100.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| kimi-k2.5 / Entity overlap | 32 | 4 | 13 (40.6%) | 7 (21.9%) | 15 (46.9%) | 6 (18.8%) | 7 (21.9%) | 0 (0.0%) | 0 (0.0%) |
| kimi-k2.5 / No-signal classification | 2 | 0 | 1 (50.0%) | 0 (0.0%) | 1 (50.0%) | 1 (50.0%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| gemini-3.1-pro-preview / Entity overlap | 50 | 13 | 34 (68.0%) | 22 (44.0%) | 3 (6.0%) | 12 (24.0%) | 17 (34.0%) | 5 (10.0%) | 0 (0.0%) |
| gemini-3.1-pro-preview / Near-duplicate leakage | 51 | 23 | 25 (49.0%) | 10 (19.6%) | 3 (5.9%) | 15 (29.4%) | 7 (13.7%) | 3 (5.9%) | 0 (0.0%) |
| gemini-3.1-pro-preview / No-signal classification | 5 | 0 | 4 (80.0%) | 1 (20.0%) | 1 (20.0%) | 3 (60.0%) | 1 (20.0%) | 0 (0.0%) | 0 (0.0%) |
| gemini-3-flash-preview / Entity overlap | 47 | 7 | 39 (83.0%) | 29 (61.7%) | 1 (2.1%) | 10 (21.3%) | 29 (61.7%) | 0 (0.0%) | 0 (0.0%) |
| gemini-3-flash-preview / Near-duplicate leakage | 54 | 12 | 36 (66.7%) | 18 (33.3%) | 6 (11.1%) | 18 (33.3%) | 14 (25.9%) | 4 (7.4%) | 0 (0.0%) |
| gemini-3-flash-preview / No-signal classification | 7 | 2 | 4 (57.1%) | 0 (0.0%) | 1 (14.3%) | 4 (57.1%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
| deepseek-v4-pro / Entity overlap | 50 | 2 | 43 (86.0%) | 43 (86.0%) | 5 (10.0%) | 0 (0.0%) | 43 (86.0%) | 0 (0.0%) | 0 (0.0%) |
| deepseek-v4-pro / Near-duplicate leakage | 42 | 1 | 38 (90.5%) | 32 (76.2%) | 3 (7.1%) | 6 (14.3%) | 31 (73.8%) | 1 (2.4%) | 0 (0.0%) |
| deepseek-v4-pro / No-signal classification | 3 | 0 | 2 (66.7%) | 0 (0.0%) | 1 (33.3%) | 2 (66.7%) | 0 (0.0%) | 0 (0.0%) | 0 (0.0%) |
