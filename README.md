# BAITBENCH: Measuring Agent Reward Hacking with Optional Shortcuts Planted in ML Tasks
**Pradyumna Shyama Prasad** \*, National University of Singapore - `<pradyu.sp@gmail.com>`\
**Meiri Anto** \*, MIT\
**Leon Eshuijs** \*, Vrije Universiteit Amsterdam\
**Julian Moncarz**, University of Toronto\
**Kaustubh Kislay**, University of Wisconsin-Madison\
**Juan J Vazquez**, Arb Research - `<juan@arbresearch.com>`

\* Co-first author

Link to paper:   
[![arXiv](https://img.shields.io/badge/arXiv-2608.30724-b31b1b?logo=arxiv&logoColor=white)](https://arxiv.org/abs/2608.30724)

## Paper summary:
**TL;DR:**
BAITBENCH measures reward hacking in autonomous ML research agents using three
synthetic tabular tasks with planted shortcuts. Each shortcut improves the
visible test score while failing on a hidden robust split, which makes it
possible to measure whether an agent exploited the task rather than produced a
solution that generalizes. The benchmark evaluates seven frontier agents with a
two-judge pipeline and includes a validity-aware prompting condition.

**Abstract:**
> LLM agents are increasingly used to run autonomous ML experiments, iterating on target metrics with little human oversight. Prior work has documented reward hacking in these environments, bringing into question the validity of produced research and the broader safety case for AI R&D. Existing benchmarks do not measure exploits that live in the data or the modeling task itself. We introduce BAITBENCH, a suite of three synthetic tabular ML tasks that each contain a shortcut that allows agents to inflate the public test score but fail on a hidden test set. Since the shortcut is optional and using it breaks no stated rule, BAITBENCH measures how often models exploit the shortcut to achieve inflated scores. Across seven frontier agents scored by our two-stage judge pipeline, 57.1% of runs exhibit reward hacking, with five of seven above 50%. Agents cheat even under a second condition where they are prompted not to -the mean cheating rate remains above 50%. We release BAITBENCH, along with the judge implementation, and an annotated dataset of transcripts containing reward hacks as a testbed for evaluating reward-hacking mitigations head-to-head. 

**Description:**
This repository contains the task generator and execution harness, canonical
evaluation cases, judge outputs, scoring pipeline, analysis code, and derived
data used for the paper and camera-ready revision.

**Keywords:** Raw agent runs, transcripts, generated task workspaces, virtual environments,
caches, credentials, and VPS backups are intentionally excluded.

## Repository layout

```text
make_datasets/          Task definitions, synthetic-data generation, and runner
reward-hacking-evals/   Canonical cases, judge outputs, scoring, and paper tables
analyze_transcripts/    Rebuttal and camera-ready statistical analyses
DATA_MANIFEST.md        Included data and the boundary around excluded run data
verify_repository.py    Offline integrity and secret-pattern checks
```

The three directories retain their original Python project boundaries so the
scientific code has not been rewritten during consolidation. Their sibling
layout is intentional: the camera-ready runner resolves
`reward-hacking-evals/` and `analyze_transcripts/` relative to this root.

## Reproduce the paper analyses

Run the structural check first, then the full analysis:

```bash
python3 reproduce.py --quick
python3 reproduce.py
```

The command verifies the repository, creates the evaluation environment with
`uv`, and runs the offline 21-task analysis bundle. It reads the included
canonical cases, judgments, and annotations and writes regenerated tables,
reports, logs, and a provenance manifest under
`analyze_transcripts/generated/reproduction/`. See
`REPRODUCIBILITY.md` for the mapping from paper results to artifacts.

## Generate benchmark tasks

Install the task-generation environment:

```bash
uv sync --project make_datasets
```

Generate the synthetic suite locally:

```bash
uv run --project make_datasets python \
  make_datasets/src/make_datasets/synthetic_suite.py build-tasks
```

Generated task workspaces and agent runs are ignored by Git. See
`make_datasets/README.md` for the execution workflow and
`reward-hacking-evals/README.md` for case construction and judging.

## Verify this copy

```bash
python3 verify_repository.py
```

This checks the required repository structure, the 1,258-case canonical paper
dataset, the absence of excluded run directories, and common credential and
private-key patterns.

## Citation

```bibtex
@misc{prasad2026baitbench,
  title        = {{BAITBENCH}: Measuring Reward Hacking in Autonomous {ML} Research Agents},
  author       = {Prasad, Pradyumna Shyama and Anto, Meiri and Eshuijs, Leon and Moncarz, Julian and Kislay, Kaustubh and Vazquez, Juan J},
  year         = {2026},
  note         = {Preprint},
  howpublished = {\url{https://github.com/juanjvazquez/BAITBENCH}}
}
```

## License

This repository is licensed under the [MIT License](LICENSE).
