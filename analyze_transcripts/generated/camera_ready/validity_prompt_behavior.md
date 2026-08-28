# Validity-aware prompt: paired behavioral success and failure analysis

## Scope

- Pairs match model, task family, dataset size, and seed; only pairs with consensus RH/non-RH labels in both prompt conditions are included.
- `Prevented` means baseline RH and validity non-RH. `Reverse` means baseline non-RH and validity RH. These are paired descriptive transitions, not deterministic prompt effects for individual runs.
- Awareness labels were generated only for consensus-RH runs and are used to characterize persistent failures, not to infer awareness among successful non-RH runs.

## Paired outcomes

| Outcome across prompt conditions | Pairs | Percentage of paired cohort |
|---|---:|---:|
| RH under both prompts | 282 | 282/552 (51.1%) |
| Non-RH under both prompts | 188 | 188/552 (34.1%) |
| Baseline RH, validity non-RH | 55 | 55/552 (10.0%) |
| Baseline non-RH, validity RH | 27 | 27/552 (4.9%) |

## By model

| Model | Paired runs | Both RH | Both non-RH | Prevented | Reverse | Net prevented |
|---|---:|---:|---:|---:|---:|---:|
| Claude Opus 4.6 | 80 | 62 | 14 | 4 | 0 | +4 |
| Claude Sonnet 4.6 | 76 | 52 | 19 | 5 | 0 | +5 |
| GPT-5.4 | 86 | 28 | 28 | 25 | 5 | +20 |
| Kimi K2.5 | 82 | 13 | 63 | 2 | 4 | -2 |
| Gemini 3.1 Pro | 81 | 44 | 22 | 10 | 5 | +5 |
| Gemini 3 Flash | 66 | 47 | 13 | 5 | 1 | +4 |
| DeepSeek V4 Pro | 81 | 36 | 29 | 4 | 12 | -8 |

## Awareness among persistent RH pairs

Among the 282 pairs labeled RH under both prompts, 238 have awareness judgments in both conditions.

| Transcript characterization | Baseline | Validity-aware | Change |
|---|---:|---:|---:|
| Recognizes mechanism but frames it as success | 132/238 (55.5%) | 87/238 (36.6%) | -18.9 pp |
| Explicitly calls the method invalid or leaky | 23/238 (9.7%) | 63/238 (26.5%) | +16.8 pp |
| Generalization concern only | 70/238 (29.4%) | 77/238 (32.4%) | +2.9 pp |
| No obvious awareness | 13/238 (5.5%) | 11/238 (4.6%) | -0.8 pp |

## Interpretation

The prompt sometimes prevents RH, but a substantial persistent-RH group remains. Within persistent failures, the prompt shifts the agent's language away from treating the shortcut as an achievement and toward explicitly recognizing invalidity. The prompt can therefore change recognition without reliably changing the final retention decision.

## Reproduction

```bash
cd /Users/pradyu/Projects/spar_project
uv run python analyze_transcripts/validity_prompt_behavior_analysis.py
```
