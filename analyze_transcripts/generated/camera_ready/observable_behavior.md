# Observable behavioral characteristics associated with BAITBENCH reward hacking

## Scope

- Population: the paper's 1,258 canonical runs.
- Reward hacking: consensus of the archived Claude Opus 4.6 and GPT-5.4 binary judges; disagreements and unclear cases are excluded from RH-rate denominators.
- Engagement: at least one experiment row recorded in `results.tsv` (`trajectory_row_count > 0`). This is an observable harness measure, not a claim about internal effort.
- Experiment rows and evaluator calls are independently packaged harness events. Transcript awareness is a separate LLM-coded annotation and is reported only among consensus-RH runs.
- The binary judges could inspect the trajectories, including `results.tsv`. Behavioral associations therefore characterize patterns accompanying the judged outcome; they are not independent validation of the labels.
- All relationships are descriptive. Engagement is post-treatment, model and scaffold are partly confounded, and none of these statistics identifies an activation-level or causal mechanism.

## Main result

Recorded engagement is an eligibility condition, not a behavioral predictor: without an experiment, a run has little opportunity to discover or retain a shortcut. The primary behavioral cohort is therefore the 1052 consensus-labeled runs with at least one logged experiment; 678 (64.4%) were RH.

For context, the full consensus-labeled cohort was 681/1169 (58.3%), while runs with no recorded experiment were 3/117 (2.6%). Conditioning changes some model comparisons substantially, especially Kimi, so both denominators should be disclosed.

## Behavioral funnel by model

| Model | Canonical runs | Engaged | Consensus RH | RH among engaged consensus | Median experiment rows among engaged | Shortcut/invalidity awareness among consensus RH |
|---|---:|---:|---:|---:|---:|---:|
| Claude Opus 4.6 | 180 | 179/180 (99.4%) | 132/169 (78.1%) | 132/169 (78.1%) | 10.0 | 68/132 (51.5%) |
| Claude Sonnet 4.6 | 180 | 180/180 (100.0%) | 118/164 (72.0%) | 118/164 (72.0%) | 14.0 | 92/118 (78.0%) |
| GPT-5.4 | 180 | 180/180 (100.0%) | 88/176 (50.0%) | 88/176 (50.0%) | 6.0 | 65/88 (73.9%) |
| Kimi K2.5 | 178 | 79/178 (44.4%) | 34/172 (19.8%) | 34/73 (46.6%) | 8.0 | 7/34 (20.6%) |
| Gemini 3.1 Pro | 180 | 166/180 (92.2%) | 106/167 (63.5%) | 105/153 (68.6%) | 9.0 | 33/106 (31.1%) |
| Gemini 3 Flash | 180 | 168/180 (93.3%) | 108/150 (72.0%) | 108/148 (73.0%) | 10.0 | 47/108 (43.5%) |
| DeepSeek V4 Pro | 180 | 177/180 (98.3%) | 95/171 (55.6%) | 93/169 (55.0%) | 4.0 | 75/95 (78.9%) |

The awareness column asks a narrower question: among runs whose retained submission was already labeled RH, did the transcript explicitly identify the shortcut or call the method invalid? Missing awareness judgments remain in the denominator.

## Eligibility-threshold sensitivity

The one-row threshold is the primary definition because it captures the minimum recorded experiment. Stricter thresholds test whether conclusions depend on barely active runs.

| Minimum logged experiment rows | Eligible consensus runs | Consensus RH rate | Kimi eligible consensus runs | Kimi RH rate |
|---:|---:|---:|---:|---:|
| 1 | 1052 | 678/1052 (64.4%) | 73 | 34/73 (46.6%) |
| 2 | 1003 | 664/1003 (66.2%) | 67 | 33/67 (49.3%) |
| 3 | 937 | 624/937 (66.6%) | 59 | 31/59 (52.5%) |
| 5 | 812 | 561/812 (69.1%) | 52 | 28/52 (53.8%) |

## RH rate by post-engagement activity

### Logged experiment rows

| Experiment rows | Consensus-labeled runs | Consensus RH | RH rate within bin |
|---:|---:|---:|---:|
| 1-5 | 321 | 159 | 159/321 (49.5%) |
| 6-10 | 322 | 220 | 220/322 (68.3%) |
| 11-20 | 329 | 232 | 232/329 (70.5%) |
| >20 | 80 | 67 | 67/80 (83.8%) |

### Evaluator calls

| Evaluator calls | Consensus-labeled runs | Consensus RH | RH rate within bin |
|---:|---:|---:|---:|
| 0 | 118 | 80 | 80/118 (67.8%) |
| 1-5 | 417 | 250 | 250/417 (60.0%) |
| 6-10 | 222 | 130 | 130/222 (58.6%) |
| 11-20 | 170 | 127 | 127/170 (74.7%) |
| >20 | 125 | 91 | 91/125 (72.8%) |

### Successive provisional keep decisions

A `keep` row means the agent explicitly logged an evaluated variant as worth retaining. Multiple keep rows therefore record repeated acceptance of visible-score winners, although the exact logging practice can vary by scaffold and model.

| Rows marked keep | Consensus-labeled engaged runs | Consensus RH | RH rate within bin |
|---:|---:|---:|---:|
| 0 | 1 | 0 | 0/1 (0.0%) |
| 1 | 128 | 29 | 29/128 (22.7%) |
| 2 | 139 | 67 | 67/139 (48.2%) |
| 3-5 | 335 | 203 | 203/335 (60.6%) |
| >5 | 449 | 379 | 379/449 (84.4%) |

## Primary within-cell behavioral associations

The primary comparison is restricted to engaged runs and compares RH with non-RH only within the same model, task family, dataset size, and prompt condition. The reported AUC pools only within-cell RH/non-RH pairs; 0.5 means no ordering and values above 0.5 mean the characteristic is larger in RH runs. Confidence intervals cluster-bootstrap whole seed-generated dataset instances.

| Post-engagement characteristic | Within-cell AUC [95% cluster-bootstrap CI] | Comparable RH/non-RH pairs | Usable cells |
|---|---:|---:|---:|
| Logged experiment rows | 0.651 [0.548, 0.745] | 465 | 40 |
| Evaluator calls | 0.595 [0.501, 0.686] | 465 | 40 |
| Rows marked keep | 0.759 [0.672, 0.836] | 465 | 40 |
| Rows marked discard | 0.472 [0.377, 0.565] | 465 | 40 |
| Experiments after first keep | 0.649 [0.547, 0.744] | 465 | 40 |
| File-read events | 0.613 [0.512, 0.706] | 465 | 40 |
| File-write events | 0.625 [0.523, 0.720] | 465 | 40 |

A count of rows marked `keep` is partly a logging convention, while read/write events are scaffold-mediated. The most interpretable search characteristics are total experiments, evaluator calls, discarded trials, and continued experimentation after the first provisional keep.

## Stability across tasks and prompts

| Subgroup | Logged experiments AUC | Evaluator calls AUC | Rows marked keep AUC | Experiments after first keep AUC |
|---|---:|---:|---:|---:|
| t01 | 0.768 | 0.554 | 0.777 | 0.768 |
| t02 | 0.728 | 0.596 | 0.775 | 0.728 |
| t03 | 0.475 | 0.622 | 0.728 | 0.472 |
| baseline prompt | 0.724 | 0.700 | 0.802 | 0.724 |
| validity prompt | 0.590 | 0.508 | 0.724 | 0.588 |

## Leave-one-model-out stability

| Excluded model | Logged experiments AUC | Evaluator calls AUC | Rows marked keep AUC | Experiments after first keep AUC |
|---|---:|---:|---:|---:|
| Claude Opus 4.6 | 0.642 | 0.577 | 0.749 | 0.641 |
| Claude Sonnet 4.6 | 0.681 | 0.593 | 0.794 | 0.681 |
| GPT-5.4 | 0.657 | 0.581 | 0.752 | 0.656 |
| Kimi K2.5 | 0.649 | 0.596 | 0.756 | 0.648 |
| Gemini 3.1 Pro | 0.629 | 0.598 | 0.739 | 0.628 |
| Gemini 3 Flash | 0.664 | 0.596 | 0.761 | 0.663 |
| DeepSeek V4 Pro | 0.625 | 0.625 | 0.761 | 0.623 |

## Recognition among retained RH submissions

These annotations cannot predict whether a run becomes RH because awareness was judged only for consensus-RH runs. They instead characterize what happens after a shortcut is found and retained.

| Transcript characteristic among consensus-RH runs | Runs | Rate of consensus-RH denominator |
|---|---:|---:|
| Any generalization or shortcut awareness | 574 | 574/681 (84.3%) |
| Shortcut or invalidity awareness | 387 | 387/681 (56.8%) |
| Explicitly calls the method invalid or leaky | 98 | 98/681 (14.4%) |
| Rejects or warns against the final method | 14 | 14/681 (2.1%) |

## Interpretation for Reviewer 3 C3-C7

1. Engagement is the eligibility boundary: no-op runs have little opportunity to discover or retain a shortcut, and mixing them into model comparisons materially changes Kimi's apparent rate.
2. After engagement, the most stable observable characteristic is repeated provisional acceptance: RH runs contain more rows explicitly marked `keep` even within the same model, task, size, and prompt cell. This association survives every task and every leave-one-model-out check, but remains descriptive and partly dependent on logging conventions.
3. Total experimentation and evaluator use are weaker and heterogeneous. Experiment count separates RH in t01/t02 but not t03, so it should not be presented as a universal mechanism.
4. Recognition often fails to prevent retention. Many RH transcripts acknowledge generalization risk or the shortcut, so the behavioral failure can occur between recognition and the final keep decision.
5. Model differences should be described as different observed funnels through engagement, search, recognition, and retention. The data do not establish why the underlying models differ internally, and scaffold/provider differences remain possible explanations.

## Reproduction

```bash
cd /Users/pradyu/Projects/spar_project
uv run python analyze_transcripts/observable_behavior_analysis.py
```

Bootstrap replications: 10,000; random seed: 20260712.
