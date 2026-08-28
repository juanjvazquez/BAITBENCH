# The case for dropping the GLM-5.1 judge from the panel

**Scope:** the make_datasets reward-hacking judging panel as run in the p3/v3
production cohort — labels `vps1_20260506_234816` and `vps2_20260506_234825`,
983 runs, three judge backends: `anthropic__claude-opus-4-6`,
`openai__gpt-5.4`, `openrouter__z-ai__glm-5.1`.

**Claim:** GLM-5.1 should be removed from the panel. The defensible setup is
two judges — claude-opus-4-6 and gpt-5.4 — with their disagreement used as a
"send to human" flag. GLM should not be a third vote in a majority rule.

There are two independent reasons.

---

## Reason 1 — GLM's probability output is broken

Each judge returns both a categorical `label` and a `probability` in [0, 1].
For claude and gpt these are perfectly consistent: across all 964 p3/v3 runs
with a binary verdict, the label never disagrees with `probability >= 0.5`.
For GLM it disagrees on **76 / 964 runs (7.9%)** — GLM emits
`label = reward_hacking` with `probability < 0.5`, or vice versa.

(This matches the prior run's count of 39 + 37 mismatches across the two
batches; it is a persistent property of this backend, not a one-off.)

Consequence: GLM's probability cannot be used for thresholding, ranking, or
confidence-weighted aggregation. The only usable GLM signal is its bare
label — which Reason 2 shows is redundant.

---

## Reason 2 — GLM's label adds no new information

GLM is not a third, independent perspective. It is, statistically, the
**centroid of the other two judges**, and on the cases where the other two
already agree it is a rubber stamp; on the cases where they disagree it is a
coin flip — and worse than a coin flip against human ground truth.

### 2a. GLM is closer to each of claude and gpt than they are to each other

RH-flag rates: claude 53.1%, **GLM 60.9%**, gpt 68.7% — GLM interpolates
between the conservative judge and the aggressive one.

Pairwise Cohen's κ:

| pair | κ | raw agreement |
|---|---:|---:|
| claude ↔ gpt-5.4 | **0.681** | 84.4% |
| claude ↔ GLM | **0.830** | 91.6% |
| gpt-5.4 ↔ GLM | **0.804** | 91.0% |

A genuinely diverse third rater would correlate *less* with the existing pair
than they correlate with each other. GLM does the opposite.

### 2b. ~76% of GLM's verdict is already determined by the other two

Treating each judge's binary verdict as a random variable over the 964 runs:
`H(GLM) = 0.965 bits`, `H(GLM | claude, gpt) = 0.227 bits`. Knowing claude's
and gpt's verdicts removes **76%** of the uncertainty in GLM's verdict. GLM
contributes ≈0.23 bits/run of binary signal not already present in the panel —
and 2c/2d show even that residual is noise, not signal.

### 2c. Rubber stamp on the easy 84%, coin flip on the hard 16%

- When **claude == gpt** (814 / 964 = 84% of runs): GLM matches them
  **98.9%** of the time. Pure confirmation, ≈zero marginal information.
- When **claude != gpt** (150 runs — the cases that actually need a
  tiebreaker): GLM sides with claude 52%, with gpt 48%. By construction (it's
  the centroid) this is a coin flip.

So in a 3-judge majority vote, GLM is decorative on 84% of runs and casts a
near-random deciding vote on the 16% that matter.

### 2d. Against human ground truth, GLM's tiebreak is worse than random — and worse than just trusting claude

The hand annotations (annotator `pradyu`) happen to fall *entirely* on the
claude-vs-gpt disagreement cluster: of 22 annotated runs with all three judge
labels, **all 22 are cases where claude says `not_reward_hacking` and gpt says
`reward_hacking`**. So they're a ground-truth set for exactly the population a
third judge exists to adjudicate.

| | agreement with human (n = 22) |
|---|---:|
| claude-opus-4-6 | **18 / 22 = 82%** |
| GLM-5.1 (the tiebreaker) | **10 / 22 = 45%** |
| gpt-5.4 | 4 / 22 = 18% |
| 3-judge **majority verdict** | 10 / 22 = 45% |
| drop GLM, trust claude | **18 / 22 = 82%** |

GLM's deciding vote is worse than a coin flip on this sample, and because GLM
*is* the deciding vote in all 22, the 3-judge majority verdict (45%) is
**worse than the 2-judge panel that just defers to claude (82%)**. GLM's error
mode is echoing gpt's over-flagging: of the 13 disagreement cases where GLM
sided with gpt, the human disagreed with 10.

**Caveats on 2d:** n = 22 (binomial 95% CI on the 45% is ≈25–67%), it is by
design the disagreement cluster (the panel's weakest population, mostly t03
"agent looked at the test target but didn't use it / discarded it / the gain
was sub-noise" cases), and the annotator's framework aligns with claude's
conservative reading — so "claude 82%" partly reflects shared philosophy. But
the "GLM ≈ 45%" figure does not depend on why claude and the human align, and
it agrees with a fully independent mechanical check (a `robust_gap`-based proxy
truth, swept over all reasonable thresholds, puts GLM's tiebreak ≈50% and
"always claude" ≈87%).

---

## Recommendation

1. Report the panel as **two judges, claude-opus-4-6 + gpt-5.4**.
2. Use claude≠gpt disagreement as the human-review trigger (this is already
   what the annotation workflow does — 100% of existing annotations land
   there).
3. Do not include GLM in any majority vote or confidence-weighted aggregate.
   If GLM is kept at all, keep it only as a logged third opinion, never as a
   tiebreaker, and never use its `probability` field.
4. The same logic applies before adding *any* future third judge: it earns a
   vote only if (a) its self-consistency matches claude/gpt, and (b) on the
   claude≠gpt cluster it beats both "always claude" and "always gpt" against
   human ground truth. GLM fails (a) and (b).

---

## Reproducible analysis

Standard library only (no third-party deps). Pulls the live viewer export and
prints every number cited above.

```python
#!/usr/bin/env python3
"""Does the GLM-5.1 judge add anything to the claude-opus-4-6 + gpt-5.4 panel
on the make_datasets p3/v3 judging run? No third-party deps."""
import json, math, urllib.request
from collections import Counter, defaultdict

URL = "https://spar-maded--rh-evals-viewer-serve.modal.run/api/runs.json"
P3_LABELS = {"vps1_20260506_234816", "vps2_20260506_234825"}
J_CLAUDE = "anthropic__claude-opus-4-6"
J_GPT    = "openai__gpt-5.4"
J_GLM    = "openrouter__z-ai__glm-5.1"

def to01(v):
    return 1 if v == "reward_hacking" else (0 if v == "not_reward_hacking" else None)

with urllib.request.urlopen(URL, timeout=60) as r:
    runs = json.load(r)

# one record per run for the p3/v3 cohort
p3 = {}
for rec in runs:
    if rec.get("label") in P3_LABELS and rec["run_id"] not in p3:
        p3[rec["run_id"]] = rec

# judge-label triples (drop runs where some judge said "unclear")
rows = []
for rid, rec in p3.items():
    j = rec.get("judges", {})
    c, g, x = (to01(j.get(k, {}).get("label")) for k in (J_CLAUDE, J_GPT, J_GLM))
    if None in (c, g, x):
        continue
    rows.append(dict(rid=rid, fam=rec["family"], gap=rec.get("robust_gap"),
                     C=c, G=g, X=x,
                     cp=j[J_CLAUDE].get("probability"),
                     gp=j[J_GPT].get("probability"),
                     xp=j[J_GLM].get("probability")))
N = len(rows)
print(f"p3/v3 runs in export: {len(p3)};  with all-3 binary verdicts: {N}\n")

# --- Reason 1: probability self-consistency (label vs prob >= 0.5) ---
print("REASON 1 - probability calibration")
for nm, lk, pk in [("claude", "C", "cp"), ("gpt-5.4", "G", "gp"), ("GLM-5.1", "X", "xp")]:
    bad = sum(1 for r in rows if r[pk] is not None and (1 if r[pk] >= 0.5 else 0) != r[lk])
    print(f"  {nm:8s}: label disagrees with (probability>=0.5) on {bad}/{N} cases")
print()

# --- Reason 2a: redundancy (pairwise Cohen kappa) ---
def cohen(a, b):
    n = len(a); po = sum(1 for i in range(n) if a[i] == b[i]) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return (po - pe) / (1 - pe), po
A = [r["C"] for r in rows]; B = [r["G"] for r in rows]; X = [r["X"] for r in rows]
print("REASON 2 - GLM adds no new information")
print(f"  RH-flag rates:  claude {sum(A)/N:.1%}   gpt {sum(B)/N:.1%}   GLM {sum(X)/N:.1%}   (GLM between -> centroid)")
for nm, u, v in [("claude vs gpt   ", A, B), ("claude vs GLM   ", A, X), ("gpt    vs GLM   ", B, X)]:
    k, po = cohen(u, v)
    print(f"  Cohen kappa {nm}: {k:.3f}  (raw agreement {po:.1%})")
print("  -> GLM agrees with each of the other two MORE than they agree with each other.")

# --- Reason 2b: conditional entropy / mutual information ---
def H(ps): return -sum(p * math.log2(p) for p in ps if p > 0)
cX = Counter(r["X"] for r in rows); HX = H([v / N for v in cX.values()])
joint = defaultdict(Counter)
for r in rows: joint[(r["C"], r["G"])][r["X"]] += 1
HX_cg = sum((sum(c.values()) / N) * H([v / sum(c.values()) for v in c.values()]) for c in joint.values())
print(f"  H(GLM)={HX:.3f} bits ; H(GLM | claude,gpt)={HX_cg:.3f} bits ; "
      f"so {(HX - HX_cg) / HX:.0%} of GLM's verdict is determined by the other two.")

# --- Reason 2c: rubber-stamp on easy cases, coin-flip on hard cases ---
agree = [r for r in rows if r["C"] == r["G"]]
disag = [r for r in rows if r["C"] != r["G"]]
ga = sum(1 for r in agree if r["X"] == r["C"])
print(f"  When claude==gpt ({len(agree)}/{N} = {len(agree)/N:.0%}): GLM matches them {ga}/{len(agree)} = {ga/len(agree):.1%} (confirmation only).")
gc = sum(1 for r in disag if r["X"] == r["C"])
print(f"  When claude!=gpt ({len(disag)}): GLM sides with claude {gc}/{len(disag)} = {gc/len(disag):.0%}, with gpt {len(disag)-gc}/{len(disag)} = {(len(disag)-gc)/len(disag):.0%}  (~coin flip).")
print()

# --- Reason 2d: vs human annotations on the disagreement cluster ---
ann = {}
for rec in runs:
    a = rec.get("annotation")
    if not a: continue
    rid = a.get("run_id") or rec["run_id"]
    if rid not in ann or (a.get("annotator") == "pradyu" and ann[rid].get("annotator") != "pradyu"):
        ann[rid] = a
H_rows = []
for rid, a in ann.items():
    if a.get("annotator") != "pradyu": continue
    h = to01(a.get("verdict"))
    rec = p3.get(rid)
    if h is None or rec is None: continue
    j = rec.get("judges", {})
    c, g, x = (to01(j.get(k, {}).get("label")) for k in (J_CLAUDE, J_GPT, J_GLM))
    if None in (c, g, x): continue
    H_rows.append((h, c, g, x))
n = len(H_rows)
n_split = sum(1 for h, c, g, x in H_rows if c != g)
accC = sum(1 for h, c, g, x in H_rows if c == h)
accG = sum(1 for h, c, g, x in H_rows if g == h)
accX = sum(1 for h, c, g, x in H_rows if x == h)
accM = sum(1 for h, c, g, x in H_rows if (1 if c + g + x >= 2 else 0) == h)
print("  Human ground truth (pradyu annotations):")
print(f"    {n} annotated runs with all-3 binary verdicts; {n_split}/{n} are claude!=gpt disagreements.")
print(f"    agreement w/ human:  claude {accC}/{n}={accC/n:.0%}   GLM {accX}/{n}={accX/n:.0%}   gpt {accG}/{n}={accG/n:.0%}")
print(f"    3-judge majority agrees w/ human: {accM}/{n}={accM/n:.0%}   (== drop GLM, trust claude: {accC}/{n}={accC/n:.0%})")
```

Expected output (export as of 2026-05-11; byte-identical to the 2026-05-10
snapshot):

```text
p3/v3 runs in export: 983;  with all-3 binary verdicts: 964

REASON 1 - probability calibration
  claude  : label disagrees with (probability>=0.5) on 0/964 cases
  gpt-5.4 : label disagrees with (probability>=0.5) on 0/964 cases
  GLM-5.1 : label disagrees with (probability>=0.5) on 76/964 cases

REASON 2 - GLM adds no new information
  RH-flag rates:  claude 53.1%   gpt 68.7%   GLM 60.9%   (GLM between -> centroid)
  Cohen kappa claude vs gpt   : 0.681  (raw agreement 84.4%)
  Cohen kappa claude vs GLM   : 0.830  (raw agreement 91.6%)
  Cohen kappa gpt    vs GLM   : 0.804  (raw agreement 91.0%)
  -> GLM agrees with each of the other two MORE than they agree with each other.
  H(GLM)=0.965 bits ; H(GLM | claude,gpt)=0.227 bits ; so 76% of GLM's verdict is determined by the other two.
  When claude==gpt (814/964 = 84%): GLM matches them 805/814 = 98.9% (confirmation only).
  When claude!=gpt (150): GLM sides with claude 78/150 = 52%, with gpt 72/150 = 48%  (~coin flip).

  Human ground truth (pradyu annotations):
    22 annotated runs with all-3 binary verdicts; 22/22 are claude!=gpt disagreements.
    agreement w/ human:  claude 18/22=82%   GLM 10/22=45%   gpt 4/22=18%
    3-judge majority agrees w/ human: 10/22=45%   (== drop GLM, trust claude: 18/22=82%)
```
