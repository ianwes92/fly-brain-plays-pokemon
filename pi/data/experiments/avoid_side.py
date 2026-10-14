"""Does choosing the avoidance SIDE from the brain's turn drive fix the right bias?

THE FINDING THIS TESTS (data/experiments/obstacle_sides.py, 3 seeds): the
obstacle readout does not encode side. Against the tile map's own left/right
obstacle asymmetry, a truly-LEFT obstacle reads +66 Hz and a truly-RIGHT one
reads +44 Hz -- positive, not negative. The readout rises for an obstacle on
EITHER side. But the avoidance branch read its sign as the side
(`> +thresh` -> turn right), so nearly every avoidance turn went right
(turn_balance: 0L/35R, 3L/63R), which is the fly's standing right bias
(ethogram_now: 37.7% left).

ARMS (same seed, same start snapshot, one difference)
  readout  the original: the readout's sign chooses the side
  turn     the readout only decides THAT a turn is due; the brain's own turn
           drive picks the side, exactly as the wall-ahead branch already does

PRE-REGISTERED, fixed before any result was seen:
  PRIMARY    |left share - 50%| over ALL turns, turn minus readout, paired
             across seeds. Negative is better (more even-handed).
  GUARDS     must NOT get significantly worse, or the change is rejected even
             if the primary wins -- the last attempt at this (turn_balance)
             balanced nothing and DOUBLED crashes, and a fix that trades the
             bias for collisions is not a fix:
               bumps per tile        (lower is better)
               new tiles per move    (higher is better)
               distinct tiles        (higher is better)
  DECISION   adopt "turn" only if the primary improves with |t| > 2.571
             (6 seeds, 5 d.f., two-sided 5%) AND no guard is significantly
             worse in the wrong direction.

Exploration is a GUARD here, not the primary, on purpose: it is the outcome
that matters for the stream, but it is noisy and indirect, and claiming it as
the headline after the fact would be choosing the measure that came out best.
"""
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/flybrain"))

OUT = os.path.join(HERE, "avoid_side.jsonl")
SEEDS = range(6)


def run_arm(arm):
    os.environ["FLYBRAIN_AVOID_SIDE"] = arm
    import ethogram_now as eth                     # after the env is set
    os.environ["SCRATCH"] = f"/tmp/claude-1000/avoid_{arm}"
    eth.SCRATCH = os.environ["SCRATCH"]
    for seed in SEEDS:
        row = eth.run(seed, "fly")
        row["arm"] = arm
        with open(OUT, "a") as f:
            f.write(json.dumps(row) + "\n")
        n = row["left"] + row["right"]
        print(f"{arm:7s} seed {seed}: turns {row['left']}L/{row['right']}R "
              f"({row['left'] / max(n, 1):.1%} left)  bumps/tile {row['bumps_per_tile']:.3f}  "
              f"new/move {row['explore_per_move']:.3f}  distinct {row['distinct']}  "
              f"avoid={row['stats'].get('avoid', 0)}  ({row['wall_s']:.0f} s)", flush=True)


def score():
    rows = [json.loads(l) for l in open(OUT) if l.strip()]
    by = {(r["seed"], r["arm"]): r for r in rows}
    seeds = sorted(s for s in SEEDS if (s, "turn") in by and (s, "readout") in by)
    if len(seeds) < 2:
        print("not enough paired seeds yet"); return
    crit = {6: 2.571, 5: 2.776, 4: 3.182, 3: 4.303, 2: 12.71}.get(len(seeds), 2.571)

    def lshare(r):
        n = r["left"] + r["right"]
        return r["left"] / n if n else 0.5

    def paired(f, label, better):
        d = [f(by[(s, "turn")]) - f(by[(s, "readout")]) for s in seeds]
        m, sd = st.mean(d), (st.stdev(d) if len(d) > 1 else 0.0)
        t = m / (sd / len(d) ** 0.5) if sd else float("nan")
        new = st.mean(f(by[(s, "turn")]) for s in seeds)
        old = st.mean(f(by[(s, "readout")]) for s in seeds)
        good = (m < 0) if better == "-" else (m > 0)
        sig = abs(t) > crit
        verdict = ("BETTER" if good else "WORSE") + (" (significant)" if sig else " (not significant)")
        print(f"  {label:22s} turn {new:8.3f}   readout {old:8.3f}   diff {m:+8.3f}   "
              f"t = {t:+6.2f}   {verdict}")
        return good, sig

    print(f"{len(seeds)} paired seeds; |t| > {crit} needed\n")
    print("PRIMARY")
    p_good, p_sig = paired(lambda r: abs(lshare(r) - 0.5), "|left share - 50%|", "-")
    print("\nGUARDS (must not be significantly worse)")
    g1 = paired(lambda r: r["bumps_per_tile"], "bumps per tile", "-")
    g2 = paired(lambda r: r["explore_per_move"], "new tiles per move", "+")
    g3 = paired(lambda r: r["distinct"], "distinct tiles", "+")
    print("\nDESCRIPTIVE")
    for arm in ("readout", "turn"):
        L = sum(by[(s, arm)]["left"] for s in seeds)
        R = sum(by[(s, arm)]["right"] for s in seeds)
        print(f"  {arm:7s}: {L}L/{R}R = {L / max(L + R, 1):.1%} left")
    broken = [lab for (good, sig), lab in ((g1, "bumps"), (g2, "exploration"), (g3, "distinct"))
              if sig and not good]
    print()
    if p_good and p_sig and not broken:
        print("VERDICT: ADOPT 'turn' -- primary improved significantly and no guard broke")
    elif broken:
        print(f"VERDICT: REJECT -- guard(s) significantly worse: {', '.join(broken)}")
    else:
        print("VERDICT: NOT SHOWN -- keep 'readout'; the primary did not clear the bar")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "score":
        score()
    else:
        run_arm(sys.argv[1])
