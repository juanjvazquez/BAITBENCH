#!/usr/bin/env python3
"""Rebuttal statistics for BAITBENCH EMNLP reviews.

Addresses, using the paper's canonical run-collapse (imported from
make_datasets_paper_stats.py):

  R1  Headline disaggregated by task family + pooled, with run-level
      cluster-bootstrap CIs; pooled excluding no-signal; agreement-only
      variant.                              (bU2i C1, bootstrap-unit C2)
  R2  Paired prompt-condition tests per model + pooled: paired bootstrap
      CI + sign-flip permutation p-values.  (bU2i W2/C2, Uese)
  R3  Same-family judge-bias analysis.      (bU2i W3, Uese, 2Vrn)
"""
import importlib.util
import math
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path("/Users/pradyu/Projects/spar_project/reward-hacking-evals")
spec = importlib.util.spec_from_file_location(
    "paper_stats", REPO / "scripts/make_datasets_paper_stats.py"
)
ps = importlib.util.module_from_spec(spec)
sys.modules["paper_stats"] = ps
spec.loader.exec_module(ps)

records = ps.load_records(REPO / "data/outputs/judging")
print(f"canonical runs: {len(records)} (paper: 1258)")

RH = "reward_hacking"
SIZE_SEED_RE = re.compile(r"_n(100k|10k|100)_s(\d+)_")

def judge_avg(r):
    return ((r.claude_label == RH) + (r.gpt_label == RH)) / 2.0

def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")

def boot_ci(values, stat=mean, n=10000, seed=0):
    rnd = random.Random(seed)
    m = len(values)
    samples = sorted(
        stat([values[rnd.randrange(m)] for _ in range(m)]) for _ in range(n)
    )
    return samples[int(0.025 * n)], samples[int(0.975 * n)]

def fmt(v, lo, hi, pp=False):
    if pp:
        return f"{v:+.1f} pp [{lo:+.1f}, {hi:+.1f}]"
    return f"{v:.1%} [{lo:.1%}, {hi:.1%}]"

# ---------------------------------------------------------------- R1
print("\n== R1. Headline disaggregation (unit: canonical run; judge-run "
      "decisions pooled within run; 10k run-level cluster bootstrap) ==")
def headline(subset, label, seed):
    vals = [judge_avg(r) for r in subset]
    lo, hi = boot_ci(vals, seed=seed)
    print(f"  {label:42s} {fmt(mean(vals), lo, hi)}  (runs={len(subset)})")

headline(records, "Pooled (all tasks)", 1)
for task in ["Entity overlap", "Near-duplicate leakage", "No-signal classification"]:
    headline([r for r in records if r.task == task], task, 2)
headline([r for r in records if r.task != "No-signal classification"],
         "Pooled excl. no-signal", 3)
agree = [r for r in records if r.claude_label == r.gpt_label]
agree_rh = [1.0 if r.claude_label == RH else 0.0 for r in agree]
lo, hi = boot_ci(agree_rh, seed=4)
print(f"  {'Judge-consensus runs only (both agree)':42s} "
      f"{fmt(mean(agree_rh), lo, hi)}  (runs={len(agree)})")

# ---------------------------------------------------------------- R2
print("\n== R2. Paired prompt-condition effects (pair = model/task/size/seed; "
      "outcome = judge-average RH; sign-flip permutation, 20k) ==")
by_key = {}
for r in records:
    m = SIZE_SEED_RE.search(r.run_id)
    if not m:
        continue
    by_key[(r.model, r.task, m.group(1), m.group(2), r.prompt)] = r

pairs_by_model = defaultdict(list)
seen = set()
for (model, task, size, seed_s, prompt), r in by_key.items():
    base_key = (model, task, size, seed_s)
    if base_key in seen:
        continue
    b = by_key.get(base_key + ("baseline",))
    v = by_key.get(base_key + ("validity",))
    if b is not None and v is not None:
        seen.add(base_key)
        pairs_by_model[model].append(judge_avg(b) - judge_avg(v))

def perm_p(deltas, n=20000, seed=0):
    rnd = random.Random(seed)
    obs = abs(mean(deltas))
    hits = 0
    for _ in range(n):
        s = sum(d if rnd.random() < 0.5 else -d for d in deltas)
        if abs(s / len(deltas)) >= obs - 1e-12:
            hits += 1
    return hits / n

print(f"  {'model':26s} {'pairs':>5s}  {'paired delta (B-V)':28s} {'perm p':>8s}")
all_deltas = []
for model in ps.MODEL_ORDER:
    d = pairs_by_model.get(model, [])
    if not d:
        continue
    all_deltas.extend(d)
    lo, hi = boot_ci(d, seed=5)
    p = perm_p(d, seed=6)
    star = " *" if p < 0.05 else ""
    print(f"  {model:26s} {len(d):5d}  "
          f"{fmt(100*mean(d), 100*lo, 100*hi, pp=True):28s} {p:8.4f}{star}")
lo, hi = boot_ci(all_deltas, seed=7)
p = perm_p(all_deltas, seed=8)
print(f"  {'POOLED':26s} {len(all_deltas):5d}  "
      f"{fmt(100*mean(all_deltas), 100*lo, 100*hi, pp=True):28s} {p:8.4f}"
      f"{' *' if p < 0.05 else ''}")

# ---------------------------------------------------------------- R3
print("\n== R3. Same-family judge bias (excess of (ClaudeJ - GPTJ) delta on "
      "own-family runs vs non-family runs; paired run-level bootstrap) ==")
FAMILY = {"claude-opus-4-6": "anthropic", "claude-sonnet-4-6": "anthropic",
          "gpt-5.4": "openai"}
def jd(r):  # judge delta for a run
    return (1.0 if r.claude_label == RH else 0.0) - (1.0 if r.gpt_label == RH else 0.0)

print(f"  {'model':26s} {'n':>4s} {'ClaudeJ':>8s} {'GPTJ':>8s} {'delta':>8s}")
for model in ps.MODEL_ORDER:
    sub = [r for r in records if r.model == model]
    c = mean([1.0 if r.claude_label == RH else 0.0 for r in sub])
    g = mean([1.0 if r.gpt_label == RH else 0.0 for r in sub])
    print(f"  {model:26s} {len(sub):4d} {c:8.1%} {g:8.1%} {c-g:+8.1%}")

neutral = [r for r in records if FAMILY.get(r.model) is None]
print(f"\n  neutral-run judge offset: {mean([jd(r) for r in neutral]):+.1%} "
      f"(n={len(neutral)})")

def excess_ci(fam_runs, neu_runs, n=10000, seed=9):
    rnd = random.Random(seed)
    out = []
    for _ in range(n):
        f = [fam_runs[rnd.randrange(len(fam_runs))] for _ in fam_runs]
        u = [neu_runs[rnd.randrange(len(neu_runs))] for _ in neu_runs]
        out.append(mean([jd(r) for r in f]) - mean([jd(r) for r in u]))
    out.sort()
    return mean([jd(r) for r in fam_runs]) - mean([jd(r) for r in neu_runs]), \
           out[int(0.025 * n)], out[int(0.975 * n)]

for fam, label in [("anthropic", "Claude judge on Claude-family runs"),
                   ("openai", "GPT judge on GPT-family runs")]:
    fam_runs = [r for r in records if FAMILY.get(r.model) == fam]
    v, lo, hi = excess_ci(fam_runs, neutral)
    print(f"  {label} (n={len(fam_runs)}): excess {v:+.1%} [{lo:+.1%}, {hi:+.1%}]")
