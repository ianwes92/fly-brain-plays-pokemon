"""The scorecard for ethogram_now.jsonl, on the measures its docstring names.

Each measure is paired across seeds (fly minus its own matched control, same
seed, same maps) and reported as a paired t. Six seeds, so t = 2.57 is the
5% two-sided threshold; the sign convention is stated per line so a "win"
cannot be read into the wrong direction after the fact.
"""
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
rows = [json.loads(l) for l in open(os.path.join(HERE, "ethogram_recheck.jsonl")) if l.strip()]
by = {(r["seed"], r["condition"]): r for r in rows}
seeds = sorted({s for s, _ in by} if by else [])
seeds = [s for s in seeds if (s, "fly") in by and (s, "random") in by]
CRIT = 2.571                                    # two-sided 5%, 5 d.f.


def paired(f, label, want, scale=1.0):
    d = [(f(by[(s, "fly")]) - f(by[(s, "random")])) * scale for s in seeds]
    m, sd = st.mean(d), st.stdev(d)
    t = m / (sd / len(d) ** 0.5) if sd else float("nan")
    fly = st.mean([f(by[(s, "fly")]) for s in seeds]) * scale
    rnd = st.mean([f(by[(s, "random")]) for s in seeds]) * scale
    hit = (t >= CRIT if want == "+" else t <= -CRIT)
    print(f"  {label:22s} fly {fly:8.3f}   coin {rnd:8.3f}   diff {m:+8.3f}  "
          f"t = {t:+6.2f}   {'SHOWN' if hit else 'not shown'}  (wanted {want})")
    return hit


print(f"{len(seeds)} paired seeds; |t| > {CRIT} needed\n")
print("PRE-REGISTERED MEASURES (fly vs its own matched random walker)")
res = {}
res["thigmotaxis"] = paired(lambda r: r["thigmotaxis"] - r["map_baseline"],
                            "thigmotaxis - map", "+")
res["wall_following"] = paired(lambda r: r["wall_following"], "wall following", "+")
res["exploration"] = paired(lambda r: r["explore_per_move"], "new tiles per move", "+")
res["collision"] = paired(lambda r: r["bumps_per_tile"], "bumps per tile", "-")

print("\nDESCRIPTIVE (no null model: these describe, they do not test)")
for s in seeds:
    f = by[(s, "fly")]
    tot = f["left"] + f["right"]
    print(f"  seed {s}: turns {f['left']:3d}L/{f['right']:3d}R "
          f"({100 * f['left'] / tot:4.1f}% left)   bout median {f['bout_median_ms']:6.1f} ms "
          f"(cv {f['bout_cv']:.2f}, n={f['n_bouts']})   moves {f['moves']:4d}")
L = sum(by[(s, "fly")]["left"] for s in seeds)
R = sum(by[(s, "fly")]["right"] for s in seeds)
print(f"  ALL: {L}L/{R}R = {100 * L / (L + R):.1f}% left "
      f"(a real fly is near 50; before the readouts were re-centred this was 6.8)")
print(f"  moves: fly {st.mean([by[(s, 'fly')]['moves'] for s in seeds]):.0f}   "
      f"coin {st.mean([by[(s, 'random')]['moves'] for s in seeds]):.0f}")
print(f"  walking fraction (fly, what the coin was matched to): "
      f"{st.mean([by[(s, 'fly')]['walking_frac'] for s in seeds]):.3f}")

print(f"\nVERDICT: {sum(res.values())} of {len(res)} pre-registered measures shown "
      f"({', '.join(k for k, v in res.items() if v) or 'none'})")
