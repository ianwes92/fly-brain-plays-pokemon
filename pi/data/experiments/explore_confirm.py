"""Is the median fix's exploration gain real? Six more seeds, exploration PRIMARY.

avoid_median.py ADOPTED the median-split avoidance side on turn balance. On the
way it showed distinct tiles 56.3 -> 75.8 (+35%), but only t=+1.56 on 6 seeds:
not significant, so not claimable. avoid_mirror.py then taught the lesson that
exploration, not collision avoidance, is the measure that matters for progress.

This is a CONFIRMATORY run, pre-registered before seeds 6-11 were seen:
  PRIMARY    distinct tiles, median minus readout, paired over ALL 12 seeds
             (0-5 from avoid_median.jsonl, 6-11 run here). Higher is better.
  DECISION   the exploration gain may be CLAIMED only if |t| > 2.201
             (12 seeds, 11 d.f., two-sided 5%) in the + direction.
  This changes no code: "median" is already the default. It decides only what
  can honestly be said about it in public.

Seeds 0-5 are reused, not re-run: the simulation is deterministic -- two
separate controls have reproduced earlier runs to the digit on every seed.
"""
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/flybrain"))

OUT = os.path.join(HERE, "avoid_median.jsonl")       # appended to: one dataset
NEW_SEEDS = range(6, 12)
ALL_SEEDS = range(12)
CRIT = {12: 2.201, 11: 2.228, 10: 2.262, 9: 2.306, 8: 2.365, 7: 2.447}


def run_arm(arm):
    os.environ["FLYBRAIN_AVOID_SIDE"] = arm
    import ethogram_now as eth
    os.environ["SCRATCH"] = f"/tmp/claude-1000/confirm_{arm}"
    eth.SCRATCH = os.environ["SCRATCH"]
    for seed in NEW_SEEDS:
        row = eth.run(seed, "fly")
        row["arm"] = arm
        with open(OUT, "a") as f:
            f.write(json.dumps(row) + "\n")
        print(f"{arm:7s} seed {seed}: distinct {row['distinct']:3d}  "
              f"new/move {row['explore_per_move']:.3f}  bumps/tile {row['bumps_per_tile']:.3f}  "
              f"({row['wall_s']:.0f} s)", flush=True)


def score():
    rows = [json.loads(l) for l in open(OUT) if l.strip()]
    by = {(r["seed"], r["arm"]): r for r in rows}
    seeds = sorted(s for s in ALL_SEEDS if (s, "median") in by and (s, "readout") in by)
    d = [by[(s, "median")]["distinct"] - by[(s, "readout")]["distinct"] for s in seeds]
    m, sd = st.mean(d), st.stdev(d)
    t = m / (sd / len(d) ** 0.5)
    crit = CRIT.get(len(seeds), 2.571)
    new = st.mean(by[(s, "median")]["distinct"] for s in seeds)
    old = st.mean(by[(s, "readout")]["distinct"] for s in seeds)
    print(f"{len(seeds)} paired seeds; t > {crit} needed\n")
    print(f"PRIMARY  distinct tiles   median {new:6.1f}   readout {old:6.1f}   "
          f"diff {m:+6.1f} ({m / old:+.0%})   t = {t:+.2f}")
    for s in seeds:
        print(f"    seed {s:2d}: {by[(s, 'readout')]['distinct']:3d} -> {by[(s, 'median')]['distinct']:3d}")
    print()
    print("VERDICT:", f"CLAIMABLE -- the median fix explores {m / old:.0%} more ground"
          if t > crit else "NOT SHOWN -- do not claim an exploration gain in public")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "score":
        score()
    else:
        run_arm(sys.argv[1])
