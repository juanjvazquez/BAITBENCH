# Validity-logging ablation compared with the original validity condition

## Result

Across exactly matched task-family, dataset-size, and seed cells, the original condition produced 35/71 = 49.3% reward-hacking judgments, while validity logging produced 40/71 = 56.3%. The raw change is +7.0 percentage points.

| Model | Original, all available seeds | Original, matched seeds | Validity logging | Matched change |
|---|---:|---:|---:|---:|
| GPT-5.4 | 35/88 = 39.8% | 6/18 = 33.3% | 6/18 = 33.3% | +0.0 pp |
| Sonnet 4.6 | 62/90 = 68.9% (1 unclear) | 13/18 = 72.2% | 11/18 = 61.1% | -11.1 pp |
| Gemini Flash | 60/90 = 66.7% (4 unclear) | 11/18 = 61.1% | 11/18 = 61.1% | +0.0 pp |
| Kimi K2.5 | 22/90 = 24.4% | 5/17 = 29.4% | 12/17 = 70.6% | +41.2 pp |

## Matched-case label changes

- **GPT-5.4:** 4 stayed RH, 10 stayed non-RH, 2 moved RH to non-RH, and 2 moved non-RH to RH.
- **Sonnet 4.6:** 10 stayed RH, 4 stayed non-RH, 3 moved RH to non-RH, and 1 moved non-RH to RH.
- **Gemini Flash:** 9 stayed RH, 5 stayed non-RH, 2 moved RH to non-RH, and 2 moved non-RH to RH.
- **Kimi K2.5:** 4 stayed RH, 4 stayed non-RH, 1 moved RH to non-RH, and 8 moved non-RH to RH.

## Rates conditional on engagement

Engagement is defined prospectively here as at least one recorded experiment row in `results.tsv`. This removes nominally successful no-op runs, but it conditions on a post-treatment variable and must therefore be presented as a sensitivity analysis rather than the primary causal estimate.

| Model | Original engaged, all seeds | Original engaged, matched seed set | Validity-logging engaged |
|---|---:|---:|---:|
| GPT-5.4 | 35/88 = 39.8% | 6/18 = 33.3% | 6/18 = 33.3% |
| Sonnet 4.6 | 62/90 = 68.9% | 13/18 = 72.2% | 11/18 = 61.1% |
| Gemini Flash | 60/84 = 71.4% | 11/17 = 64.7% | 11/18 = 61.1% |
| Kimi K2.5 | 22/44 = 50.0% | 5/10 = 50.0% | 12/17 = 70.6% |

Pooled across the matched seed set and conditional on engagement, the original rate was 35/63 = 55.6% and the validity-logging rate was 40/71 = 56.3%, a change of +0.8 points.

## Kimi engagement and possible inference failure

The original Kimi condition had a median of 0 logged experiment rows and a mean of 4.8; 46/90 runs recorded no experiments.
Validity logging had a median of 14 rows and a mean of 15.1; 0/17 runs recorded no experiments.

All 30 original Kimi near-duplicate runs recorded zero experiment rows, left the initial mean-prediction solution in place, and received a nominal agent exit code of zero. This perfectly systematic no-op pattern is compatible with low engagement, but it is also compatible with an empty, truncated, or unusable provider response that the harness treated as successful. The retained artifacts do not distinguish these mechanisms.

Among original Kimi runs with at least one experiment, the RH rate was 22/44 = 50.0%, compared with the unconditional 22/90 = 24.4%. The unconditional original Kimi rate should therefore not be treated as strong evidence that Kimi safely avoided reward hacking.

## Interpretation and limitations

GPT-5.4 and Gemini Flash had identical aggregate matched-seed rates, while Sonnet was 11.1 percentage points lower. Excluding Kimi, the matched pooled rate moved only from 30/54 (55.6%) to 28/54 (51.9%), a change of -3.7 points. The pooled increase is therefore driven by Kimi's change in engagement regime rather than a consistent cross-model effect.

The ablation has only two seeds per family-by-size cell, versus ten in the original design. One judgment changes a model-wide 18-run rate by 5.6 percentage points and a two-run cell rate by 50 points. These data support the descriptive conclusion that explicit validity logging did not consistently reduce reward hacking; they do not establish equivalence or a precise causal effect.

## Reproduction

```bash
uv run python scripts/analyze_validity_logging_ablation.py --output docs/validity_logging_ablation_20260712.md
```

Both conditions use the same GLM-5.2 high-reasoning judge and v4/p4 judgment schema. Original judgments are deduplicated by run ID, retaining the last valid categorical judgment. The all-seed column conservatively retains `unclear` judgments in its denominator; excluding them gives Sonnet 62/89 (69.7%) and Gemini Flash 60/86 (69.8%). No matched-seed or ablation judgments are unclear.
