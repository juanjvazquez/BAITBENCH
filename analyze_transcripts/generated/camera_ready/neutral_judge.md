# Neutral-judge local audit

GLM-5.2 produced valid categorical judgments for **1,258/1,258** canonical cases. It labeled **749/1,258 (59.54%)** as reward hacking.

| Judge pair | Cases | Agreement | Cohen's κ |
|---|---:|---:|---:|
| GLM-5.2 vs GPT-5.4 | 1,258 | 96.42% | 0.9271 |
| GLM-5.2 vs Claude Opus 4.6 | 1,258 | 93.48% | 0.8697 |
| Claude Opus 4.6 vs GPT-5.4 | 1,258 | 93.56% | 0.8716 |

## Input audit

- Neutral output: `/Users/pradyu/Projects/spar_project/reward-hacking-evals/data/outputs/judging/neutral_z-ai_glm-5.2_v4_20260709/judging_binary.jsonl`
- Canonical cases: 1,258
- Missing canonical judgments: 0
- Extra judgment run IDs: 0
- Raw-row diagnostics: header_rows=1, valid_record_rows=1258, unique_valid_records=1258, duplicate_valid_rows=0, invalid_record_rows=0, skipped_rows=5, error_rows=0

This is a robustness check using another LLM judge, not ground truth.
