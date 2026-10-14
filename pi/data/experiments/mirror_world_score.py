"""Scores mirror_world.jsonl exactly as mirror_world_PREREG.md states. Written
before any data; do not edit after the first row."""
import json
import math
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(os.path.join(HERE, "mirror_world.jsonl")) if l.strip()]
by = {(r["seed"], r["arm"]): r for r in rows}
seeds = sorted(s for s in {s for s, _ in by} if (s, "normal") in by and (s, "mirror") in by)
T = 2.201                                        # two-sided 5%, 11 d.f.


def L(r):
    return 100.0 * r["left"] / max(r["left"] + r["right"], 1)


def tstat(xs):
    m, sd = st.mean(xs), st.stdev(xs)
    se = sd / math.sqrt(len(xs))
    return m, (m / se if se else float("nan")), (m - T * se, m + T * se)


def decompose(f, label, unit):
    ln = [f(by[(s, "normal")]) for s in seeds]
    lm = [f(by[(s, "mirror")]) for s in seeds]
    I = [(a + b) / 2 - 50 for a, b in zip(ln, lm)]
    E = [(a - b) / 2 for a, b in zip(ln, lm)]
    pre = tstat([a - 50 for a in ln])
    i, e = tstat(I), tstat(E)
    print(f"\n{label} ({unit}, n={len(seeds)} paired seeds, |t|>{T})")
    print(f"  normal {st.mean(ln):.1f}   mirror {st.mean(lm):.1f}")
    print(f"  precondition (normal differs from 50): {pre[0]:+.2f}, t={pre[1]:+.2f}")
    print(f"  INTRINSIC     I = {i[0]:+.2f}  t={i[1]:+.2f}  95% CI [{i[2][0]:+.2f}, {i[2][1]:+.2f}]")
    print(f"  ENVIRONMENTAL E = {e[0]:+.2f}  t={e[1]:+.2f}  95% CI [{e[2][0]:+.2f}, {e[2][1]:+.2f}]")
    return pre, i, e


pre, i, e = decompose(L, "PRIMARY: left share in the fly's own frame", "% left")
if abs(pre[1]) < T or pre[0] > 0:
    verdict = "PRECONDITION FAILED: the normal arm does not lean right in this harness"
else:
    parts = []
    if abs(i[1]) >= T and i[0] < 0:
        parts.append("an INTRINSIC (brain) component")
    if abs(e[1]) >= T:
        parts.append("an ENVIRONMENTAL/readout component")
    if parts:
        verdict = "SHOWN: " + " and ".join(parts)
    elif (i[2][1] - i[2][0]) / 2 > 5 or (e[2][1] - e[2][0]) / 2 > 5:
        verdict = "INCONCLUSIVE: neither component significant and a CI is wider than +/-5 pp"
    elif max(abs(x) for x in (*i[2], *e[2])) <= 2.5:
        verdict = "CLEAN NULL: both components within +/-2.5 pp"
    else:
        verdict = "NOT SHOWN: neither component significant"
print("\nVERDICT (primary):", verdict)
decompose(lambda r: 100.0 * (1 - r["turn_below_zero"]), "SECONDARY: brain turn signal above zero", "% of frames")
print("\nTurn sources per arm (totals):")
for arm in ("normal", "mirror"):
    tot = {}
    for s in seeds:
        for k, v in by[(s, arm)]["by_source"].items():
            tot[k] = tot.get(k, 0) + v
    print(f"  {arm}: {tot}")
