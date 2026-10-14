"""Does real side information beat the even-handed coin? (Follow-up to avoid_median.)

avoid_median.py was ADOPTED: the avoidance side is now the turn signal split at
its running median -- even-handed, which cured the circling (left share 37% ->
45%). But it is a coin: it cannot know where the obstacle is, so it turns
TOWARD it about half the time.

mirror_sides.py then found the DNp35/DNp18 mirror pairs DO know the side, in
play, on every seed: truly-left +22 to +25 Hz, truly-right -26 to -34 Hz,
corr +0.57 to +0.62 (the readout used to notice obstacles: corr +0.26, and a
right obstacle reads positive).

ARMS (same seed, same start snapshot, one difference)
  median   current default: notice with WALL_READOUT, side = turn signal vs its median
  mirror   notice with WALL_READOUT (unchanged), side = sign of the mirror pairs

PRE-REGISTERED, fixed before any result of this run was seen:
  PRIMARY    bumps per tile, mirror minus median, paired. NEGATIVE is better.
             The whole point of knowing the side is turning AWAY from walls.
  GUARDS     must not be significantly worse, or the change is rejected:
               new tiles per move   (higher is better)
               distinct tiles       (higher is better)
             These carry the old worry behind turn balance -- circling -- in
             the form that actually matters for progress.
  REPORTED, NOT A GUARD: left-turn share. Stated in advance, not after: if
             obstacles genuinely sit on one side more often (an earlier
             measurement: 11.1% left vs 7.1% right of frames), CORRECT
             avoidance turns away from that side more. Rejecting on balance
             would penalise the right behaviour. If balance collapses AND
             exploration falls, the guards catch it.
  DECISION   adopt "mirror" only if the primary improves with |t| > 2.571
             (6 seeds, 5 d.f.) AND neither guard is significantly worse.
"""
import json
import os
import statistics as st
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/flybrain"))

OUT = os.path.join(HERE, "avoid_mirror.jsonl")
SEEDS = range(6)
ARMS = ("median", "mirror")


def run_arm(arm):
    os.environ["FLYBRAIN_AVOID_SIDE"] = arm
    import ethogram_now as eth
    os.environ["SCRATCH"] = f"/tmp/claude-1000/avoidmir_{arm}"
    eth.SCRATCH = os.environ["SCRATCH"]
    for seed in SEEDS:
        row = eth.run(seed, "fly")
        row["arm"] = arm
        with open(OUT, "a") as f:
            f.write(json.dumps(row) + "\n")
        n = row["left"] + row["right"]
        print(f"{arm:6s} seed {seed}: bumps/tile {row['bumps_per_tile']:.3f}  "
              f"distinct {row['distinct']:3d}  new/move {row['explore_per_move']:.3f}  "
              f"turns {row['left']}L/{row['right']}R ({row['left'] / max(n, 1):.1%} left)  "
              f"avoid={row['stats'].get('avoid', 0)}  ({row['wall_s']:.0f} s)", flush=True)


def score():
    rows = [json.loads(l) for l in open(OUT) if l.strip()]
    by = {(r["seed"], r["arm"]): r for r in rows}
    seeds = sorted(s for s in SEEDS if all((s, a) in by for a in ARMS))
    if len(seeds) < 2:
        print("not enough paired seeds yet"); return
    crit = {6: 2.571, 5: 2.776, 4: 3.182, 3: 4.303, 2: 12.71}.get(len(seeds), 2.571)

    def paired(f, label, better):
        d = [f(by[(s, "mirror")]) - f(by[(s, "median")]) for s in seeds]
        m, sd = st.mean(d), (st.stdev(d) if len(d) > 1 else 0.0)
        t = m / (sd / len(d) ** 0.5) if sd else float("nan")
        new = st.mean(f(by[(s, "mirror")]) for s in seeds)
        old = st.mean(f(by[(s, "median")]) for s in seeds)
        good = (m < 0) if better == "-" else (m > 0)
        sig = abs(t) > crit
        print(f"  {label:20s} mirror {new:8.3f}   median {old:8.3f}   diff {m:+8.3f}   "
              f"t = {t:+6.2f}   {'BETTER' if good else 'WORSE'} "
              f"({'significant' if sig else 'not significant'})")
        return good, sig

    print(f"{len(seeds)} paired seeds; |t| > {crit} needed\n")
    print("PRIMARY")
    p_good, p_sig = paired(lambda r: r["bumps_per_tile"], "bumps per tile", "-")
    print("\nGUARDS (must not be significantly worse)")
    g1 = paired(lambda r: r["explore_per_move"], "new tiles per move", "+")
    g2 = paired(lambda r: r["distinct"], "distinct tiles", "+")
    print("\nREPORTED, NOT A GUARD")
    for arm in ARMS:
        L = sum(by[(s, arm)]["left"] for s in seeds)
        R = sum(by[(s, arm)]["right"] for s in seeds)
        print(f"  {arm:6s}: {L}L/{R}R = {L / max(L + R, 1):.1%} left")
    broken = [lab for (good, sig), lab in ((g1, "exploration"), (g2, "distinct")) if sig and not good]
    print()
    if p_good and p_sig and not broken:
        print("VERDICT: ADOPT 'mirror' -- bumps fell significantly and no guard broke")
    elif broken:
        print(f"VERDICT: REJECT -- guard(s) significantly worse: {', '.join(broken)}")
    else:
        print("VERDICT: NOT SHOWN -- keep 'median'; the primary did not clear the bar")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "score":
        score()
    else:
        run_arm(sys.argv[1])
