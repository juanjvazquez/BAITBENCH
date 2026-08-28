# Prompt + Schema + Case-Text Version Timeline

> ## ⚠️ DEPRECATED — do not rely on this document
>
> **Status:** superseded as of 2026-05-12.
>
> This was generated on 2026-05-07 from an *uncommitted working tree* (when HEAD was `f41dba9`). Its central warning — "the p3/v3 prod runs were judged against prompt/case-text that exists only in the uncommitted working tree, so a fresh `git clone` can't regenerate their prompts" — **is no longer true.** Those edits were subsequently committed (`d5d3845` trim make_datasets family_context, `c9d13a7` exploit_form axis + GLM hardening + H-judge rename, `7229d7f` consolidate version constants, `31e05af` stamp `judge_input_hash`), and as of the merge of `feat/modal-pipeline` → `main` the `MAKE_DATASETS_FAMILY_CONTEXT` hashes at HEAD (`t01 e3c851b4 / t02 ce128523 / t03 96e8f2fc`) and the `developer_instructions("make_datasets")` content match what the `vps1_20260506_234816` / `vps2_20260506_234825` (p3/v3) runs actually saw. The reproducibility gap is closed.
>
> The commit-timeline table below (pre-`f41dba9` history) is still accurate as history, but the "working tree drift" / "fresh clone can't reproduce" framing is stale. For the current state of the p3/v3 run analysis see `data/reports/full_run_analysis_20260507.md`; for the live prompt/schema/case-text content see `src/judges/prompt_blocks.py`, `src/judges/judge_binary.py`, and `src/cases/constants.py` (the source of truth, version-stamped by `JUDGE_VERSION`).
>
> ---

**Generated:** 2026-05-07 (working copy, not committed)
**Branch:** feat/modal-pipeline
**Scope:** every commit on this branch that touched `src/judges/{prompt_blocks,judge_binary,judge_classification,judge_classification_family}.py` or `src/cases/constants.py`, cross-referenced with the four non-smoke judging labels on the `rh-evals-results` Modal volume.

---

## TL;DR

| Question | Answer |
|---|---|
| Are the four prod judging runs on the same prompt? | **No.** Three distinct effective prompt states across them. |
| Is `judge_prompt_version` reliable as a "same prompt" key? | **Partially.** Two earliest runs have it absent. The two newest runs (`p3`) genuinely share a prompt. |
| Did anyone edit the prompt without bumping the version? | **Yes.** Commit [7dd7de4](https://github.com/spar-maded-2026/reward-hacking-evals/commit/7dd7de4) reshaped all three judges' prompts (Tier 2 refs) but didn't introduce `JUDGE_PROMPT_VERSION` until the next commit ([e1665d8](https://github.com/spar-maded-2026/reward-hacking-evals/commit/e1665d8)). |
| Did case-text drift independently of prompt version? | **Yes.** [7915d73](https://github.com/spar-maded-2026/reward-hacking-evals/commit/7915d73) expanded `MAKE_DATASETS_FAMILY_CONTEXT` (4-sentence facts) without touching `JUDGE_PROMPT_VERSION`. The current working tree has trimmed it again — also without bumping. |
| Working tree drift right now? | **No.** Hashes match `f41dba9`. The "trimmed cases/constants.py" referenced in conversation has already been committed. |

**Coordination status of the four prod runs:**

| Run | When | Stamped (prompt, schema) | Effective at that commit | Coordinated with… |
|---|---|---|---|---|
| `vps1_overnight_20260426_044000` | 2026-04-26 04:40 (run start) | (absent, absent) | `46af767` content state | (alone — no other run on the same content state) |
| `vps2_20260506_191541` | 2026-05-06 19:15 | (absent, v2) | `7dd7de4` / `8b1ce97` content state | (alone — distinct prompt content, no other run shares it) |
| `vps1_20260506_234816` | 2026-05-06 23:48 | (p3, v3) | working-tree content (uncommitted) | **paired with `vps2_20260506_234825`** ✓ |
| `vps2_20260506_234825` | 2026-05-06 23:48 | (p3, v3) | working-tree content (uncommitted) | **paired with `vps1_20260506_234816`** ✓ |

So **only 2 of 4 prod runs** are pairwise comparable. The other two are each on their own prompt-state island.

**Caveat on the "p3 / v3" runs**: the prompt + case-text content these runs were judged against exists *only in the uncommitted working tree* — `f41dba9` is the latest pushed commit but stamps p3 with **different** prompt content than what these runs actually saw. The version-stamping infrastructure was added before the file restructure that centralized the constants in `prompt_blocks.py` was committed. So even the "comparable pair" is reproducible only against the uncommitted code state, not against any pushed commit. **This means a fresh `git clone` would not let you regenerate these runs' prompts.**

---

## Commit timeline (prompt + schema constants + content hashes)

Hashes are SHA-256 truncated to 8 hex chars. `binary` / `family` / `classification` columns are `developer_instructions("make_datasets")` content hashes — the actual prompt text the judge sends, derived live from the source at each commit.

`t01` / `t02` / `t03` columns are hashes of the per-family context strings in `src/cases/constants.py:MAKE_DATASETS_FAMILY_CONTEXT`. These appear **inside the case dump** the judge receives (not inside `developer_instructions`), so they're an independent drift axis.

| Commit | When (local) | Prompt v | Schema v | Binary prompt | Family prompt | Class. prompt | t01 ctx | t02 ctx | t03 ctx |
|---|---|---|---|---|---|---|---|---|---|
| [`46af767`](https://github.com/spar-maded-2026/reward-hacking-evals/commit/46af767) | 2026-04-26 05:07 | absent | absent | `41c56fa3` | `3f246667` | `1af2f3d9` | `8bae3d39` | `fb310d55` | `20aa7476` |
| [`48d676e`](https://github.com/spar-maded-2026/reward-hacking-evals/commit/48d676e) | 2026-04-26 21:36 | absent | absent | `41c56fa3` | `06ec97d5` ← **changed** | `1af2f3d9` | `8bae3d39` | `fb310d55` | `20aa7476` |
| [`7dd7de4`](https://github.com/spar-maded-2026/reward-hacking-evals/commit/7dd7de4) | 2026-05-06 17:25 | absent | **v2** ← schema introduced | `44012d41` ← all three change | `945350c5` | `e4802cf9` | `8bae3d39` | `fb310d55` | `20aa7476` |
| [`8b1ce97`](https://github.com/spar-maded-2026/reward-hacking-evals/commit/8b1ce97) | 2026-05-06 19:47 | absent | v2 | `44012d41` (same) | `945350c5` (same) | `e4802cf9` (same) | `8bae3d39` | `fb310d55` | `20aa7476` |
| [`e1665d8`](https://github.com/spar-maded-2026/reward-hacking-evals/commit/e1665d8) | 2026-05-06 20:55 | **p2** ← prompt introduced | v2 | `8a50c94c` ← all three change | `3a3b2e29` | `b32a2122` | `8bae3d39` | `fb310d55` | `20aa7476` |
| [`7915d73`](https://github.com/spar-maded-2026/reward-hacking-evals/commit/7915d73) | 2026-05-06 21:19 | p2 | v2 | `8a50c94c` (same) | `3a3b2e29` (same) | `b32a2122` (same) | `7e22a2f9` ← **all three change** | `813bb31b` | `7e4f67dd` |
| [`f41dba9`](https://github.com/spar-maded-2026/reward-hacking-evals/commit/f41dba9) | 2026-05-07 09:51 | **p3** | **v3** | `8b286cdd` ← all three change | `a615e4ee` | `7a4ff4b3` | `7e22a2f9` (same) | `813bb31b` (same) | `7e4f67dd` (same) |
| **(working tree, 2026-05-07)** | — | p3 | v3 | `8b286cdd` (same) | `a615e4ee` (same) | `7a4ff4b3` (same) | `e3c851b4` ← **all three change** | `ce128523` | `96e8f2fc` |

### Observations

- **Schema constant introduced at `7dd7de4`** (Tier 2 evidence refs work). The prompt text changed too in this commit, but `JUDGE_PROMPT_VERSION` wasn't created until the following commit.
- **Prompt version constant introduced at `e1665d8`** as `"p2"`. Bumped to `"p3"` at `f41dba9`.
- **Schema bumped v2 → v3** at `f41dba9`, alongside the p3 prompt bump. Coordinated.
- **Case-text drift events:**
  - `7915d73`: t01/t02/t03 contexts all rewritten (the "verbatim source-repo construction facts" expansion). **Prompt version was NOT bumped** even though this materially changes what the model reads. This is the discipline failure your timeline flagged.
  - `f41dba9`: case texts unchanged at this commit (still the expanded version from `7915d73`).
  - **Current working tree: case texts have been trimmed again** (`e3c851b4` / `ce128523` / `96e8f2fc` differ from `f41dba9`'s hashes). This change appears to already be on disk; whether it's committed depends on what's actually pushed past `f41dba9` — checking next.

### Working tree state (uncommitted edits)

`f41dba9` is HEAD. `git status` reports six uncommitted modified files plus untracked items:

```
 M src/cases/constants.py
 M src/judges/backends.py
 M src/judges/judge_binary.py
 D src/judges/judge_classification.py
 M src/judges/run_shared_judging_pipeline.py
 M src/scoring/aggregate_judgments.py
?? src/judges/judge_classification_h_deprecated.py
```

Effects on the version surface:

- **`JUDGMENT_SCHEMA_VERSION` and `JUDGE_PROMPT_VERSION` were both moved from per-judge files into `src/judges/prompt_blocks.py`** (centralized). Pushed commits up through `f41dba9` still show the old per-judge layout. The current `prompt_blocks.py` reads `JUDGE_PROMPT_VERSION = "p3"` and `JUDGMENT_SCHEMA_VERSION = "v3"`.
- **`judge_classification.py` was renamed to `judge_classification_h_deprecated.py`** — the rename is uncommitted (deletion of the old name, untracked addition of the new name).
- **`MAKE_DATASETS_FAMILY_CONTEXT` was rewritten in the working tree** to a different shape (multi-line concatenated strings rather than a single string per family). Hashes `e3c851b4 / ce128523 / 96e8f2fc` are **only in the working tree**.
- **`backends.py`** is also modified — most likely the openrouter default model, but I haven't inspected.

**The two `(p3, v3)` runs were judged against this uncommitted working-tree state.** Reproducing those runs' prompts requires the working-tree content; pushing those uncommitted edits would make the historical record reproducible from git alone.

---

## Per-label provenance (the four non-smoke prod runs)

| Label | First record (UTC) | Stamped prompt_v | Stamped schema_v | Effective commit | Effective binary prompt hash | t01/t02/t03 hash |
|---|---|---|---|---|---|---|
| `vps1_overnight_20260426_044000` | ~2026-04-26 04:40 | `None` | `None` | between `46af767` and `48d676e` | `41c56fa3` | `8bae3d39` / `fb310d55` / `20aa7476` |
| `vps2_20260506_191541` | 2026-05-06 23:15 UTC | `None` | `'v2'` | `8b1ce97` (after kimi→glm, before prompt p2) | `44012d41` | `8bae3d39` / `fb310d55` / `20aa7476` |
| `vps1_20260506_234816` | 2026-05-07 03:48 UTC | `'p3'` | `'v3'` | working-tree (uncommitted) | (working-tree state, not at `f41dba9`) | `e3c851b4` / `ce128523` / `96e8f2fc` |
| `vps2_20260506_234825` | 2026-05-07 03:48 UTC | `'p3'` | `'v3'` | working-tree (uncommitted) | (working-tree state, not at `f41dba9`) | `e3c851b4` / `ce128523` / `96e8f2fc` |

### What each pair shares

- **`vps1_20260506_234816` and `vps2_20260506_234825`**: identical binary prompt hash AND identical case-text hashes for all three families. **Genuinely directly comparable.** ✓
- **`vps2_20260506_191541` (`p1/v2`)**: distinct prompt (`44012d41` vs `8b286cdd`) — the `7dd7de4` Tier 2 prompt text, before p2 was named. Same case texts as the legacy `8bae3d39` set. **Not directly comparable to either earlier or later runs.**
- **`vps1_overnight_20260426_044000` (`p1/v1`)**: distinct prompt (`41c56fa3` — the original initialization-commit prompts), same legacy case texts. **Not directly comparable to anything else on the volume.**

### Implication for cross-run analysis

If a researcher compares `vps1_overnight_20260426_044000` verdicts against `vps1_20260506_234816` verdicts to argue "the new prompt detects more reward hacking", they're reading **two different prompts on two different case texts** — at least four content axes have shifted between those runs. The viewer's section split correctly puts them in separate buckets.

The two `20260506_234816 / 234825` runs are the only pair you can compare directly without confounds.

---

## Discipline gaps surfaced

1. **Prompt edits without version bumps**: `7dd7de4` (Tier 2 refs) reshaped all three judges' prompts before `JUDGE_PROMPT_VERSION` existed; that's excusable. But **the discipline still isn't formalized** — there's no pre-commit hook, no docstring rule, no test that says "if `developer_instructions(...)` hash changes, `JUDGE_PROMPT_VERSION` must bump."

2. **Case-text edits without prompt-version bumps**: `7915d73` rewrote `MAKE_DATASETS_FAMILY_CONTEXT` for all three families. The judges' system prompt didn't change but the case dump they read materially did. Two paths to fix this going forward:
   - Treat case-text as part of the prompt-version axis: any edit to `MAKE_DATASETS_FAMILY_CONTEXT` requires a `JUDGE_PROMPT_VERSION` bump.
   - Or introduce a separate `case_text_version` constant stamped on the *case* records.

3. **The two newest prod runs were judged against uncommitted code.** `git status` shows six modified files including `cases/constants.py`, `judge_binary.py`, `prompt_blocks.py`, the rename `judge_classification.py → judge_classification_h_deprecated.py`, etc. Those edits are present in the deployed Modal image (since the deploy reads the working tree, not `git HEAD`) but not pushed. **Reproducing the prompt these runs saw requires the working-tree content, not any pushed commit.** A reviewer 6 months from now `git clone`-ing the repo and running `git checkout f41dba9` will get a different prompt than what these runs actually used.

---

## Suggestion (no action taken yet)

A defensive `scripts/snapshot_prompts.py` that, given a commit, dumps the developer-instructions text + family-context strings to `data/reports/prompt_snapshots/<prompt_version>_<commit>.json`. Run before every full prod judging run. The snapshot file is the source of truth for "what was the model actually reading at this run"; the version constants become a secondary index. Would close the discipline gap without requiring a pre-commit hook.

---

## Methodology notes

- Commit list extracted via `git log --all --pretty=format:"%h %ai %s" -- <file>...` over the five tracked files.
- Per-commit prompt hashes computed by checking out each commit, importing the judge module fresh (`importlib.reload`-equivalent via `sys.modules` clear), and SHA-256ing the result of `developer_instructions("make_datasets")`. Truncated to 8 hex chars for legibility.
- Case-text hashes computed by regex-extracting each `"tNN": (...)` block from `MAKE_DATASETS_FAMILY_CONTEXT` and hashing the whitespace-normalized concatenation.
- Per-label provenance derived by: (a) reading one binary record from each label on the volume; (b) reading the stamped `judge_prompt_version` and `judgment_schema_version` fields verbatim; (c) mapping the label's first-record UTC timestamp to the most-recent commit whose prompt/schema constants match the stamps and whose timestamp precedes the run.
- This report is *not* committed. Path: `data/reports/version_timeline.md` (working copy only).
