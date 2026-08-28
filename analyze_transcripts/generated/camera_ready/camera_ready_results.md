# BAITBENCH camera-ready local analysis bundle

Run mode: **full**. Audit checks: **27/27 passed**.

## Headline and task-family sensitivity

| Quantity | Estimate | 95% cluster-bootstrap interval |
|---|---:|---:|
| Pooled | 57.1% | [55.2%, 58.9%] |
| Entity overlap | 82.5% | [80.0%, 84.9%] |
| Near-duplicate leakage | 72.5% | [70.0%, 74.9%] |
| No-signal classification | 16.3% | [11.9%, 20.7%] |
| Excluding no-signal | 77.5% | [75.8%, 79.2%] |
| Binary judge consensus only | 58.3% | [56.1%, 60.3%] |

The binary-consensus denominator excludes eight cases where both judges returned `unclear`; the correct sensitivity estimate is 681/1,169 = 58.3%.

## Paired validity-prompt effects

| Model | Baseline minus validity | 95% cluster-bootstrap interval | Sign-flip p |
|---|---:|---:|---:|
| Claude Opus 4.6 | +5.6 pp | [+0.6, +10.6] pp | 0.0866 |
| Claude Sonnet 4.6 | +8.9 pp | [+2.8, +15.0] pp | 0.0109 |
| GPT-5.4 | +24.4 pp | [+15.6, +33.3] pp | 0.0000 |
| Kimi K2.5 | +0.0 pp | [-5.7, +5.7] pp | 1.0000 |
| Gemini 3.1 Pro | +7.8 pp | [+0.0, +16.1] pp | 0.1144 |
| Gemini 3 Flash | +5.0 pp | [-2.2, +12.2] pp | 0.2644 |
| DeepSeek V4 Pro | -8.3 pp | [-17.2, +0.6] pp | 0.0988 |
| **Pooled** | **+6.2 pp** | **[+2.9, +9.5] pp** | **0.0012** |

## Neutral judging and same-family bias

GLM-5.2 labeled **749/1,258 (59.54%)** cases RH.

| Comparison | Estimate | 95% interval |
|---|---:|---:|
| Claude-family excess judge gap | -0.82 pp | [-4.2, +2.5] pp |
| GPT-family excess judge gap | +2.51 pp | [-0.1, +4.9] pp |

| Judge pair | Agreement | Cohen's κ |
|---|---:|---:|
| GLM-5.2 vs GPT-5.4 | 96.42% | 0.9271 |
| GLM-5.2 vs Claude Opus 4.6 | 93.48% | 0.8697 |
| Claude Opus 4.6 vs GPT-5.4 | 93.56% | 0.8716 |

## Execution-pathway control

| Model | Pairs | Native RH | OpenCode RH | Equal-stratum posterior mean | 95% credible interval |
|---|---:|---:|---:|---:|---:|
| GPT-5.4 | 112 | 57/112 (50.9%) | 65/112 (58.0%) | +4.7 pp | [-3.8, +13.2] pp |
| Claude Sonnet 4.6 | 97 | 69/97 (71.1%) | 65/97 (67.0%) | -4.2 pp | [-11.4, +3.3] pp |

## Other reproduced analyses

- Validity-logging matched rates: original 35/71 (49.3%), logging 40/71 (56.3%); conditional on recorded engagement, 35/63 (55.6%) versus 40/71 (56.3%).
- Observable behavior: the strongest within-cell correlate is the count of experiment rows marked `keep`; see `observable_behavior.md` for the AUC, cluster-bootstrap interval, and stability checks.
- Prompt behavior: 552 paired consensus comparisons comprise 282 RH→RH, 188 non-RH→non-RH, 55 RH→non-RH, and 27 non-RH→RH pairs.
- Partial oversight: 681 consensus-RH cases were audited; direct-form evidence cited a median 8.7% of nonblank submitted-code lines across 409 measurable cases.
- Transcript awareness: 387/681 (56.8%) consensus-RH runs explicitly recognized the shortcut or its invalidity.
- Recovered human validation: 25 usable binary cases, with 18/23 consensus judgments matching the human label. This does not complete the later 100-case annotation commitment.

## Generated artifacts

- Machine-readable consolidated results: `camera_ready_results.json`
- Task execution and checksums: `run_manifest.json`
- Per-analysis reports: the Markdown and JSON files beside this report
- Paper-statistics tables: `paper_tables/*.json`
- Captured standard output and errors: `logs/`
