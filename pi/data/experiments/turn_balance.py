"""Does the mirror-paired wall readout even out the fly's turning?

The fly turns right about twice as often as left (ethogram: 431 left, 826
right). The cause found: the wall readout was not made of mirror pairs, so it
answered an obstacle on the left with +162 Hz and one on the right with +22 Hz
-- the wrong sign (wall_symmetry.log). A new readout of ten mirror pairs reads
+104 / -64 in play. This asks whether that actually evens out the turning,
rather than assuming it.

CONDITIONS (same seed, same save, same starting point, one difference)
  new   the mirror-paired readout and the crosstalk table measured for it
  old   the readout and crosstalk in use before

PRIMARY  the share of AVOIDANCE turns that go left, |share - 50%|, new minus
  old, paired across seeds. Lower is better: an even-handed fly turns away
  from obstacles in both directions. Avoidance turns only, because those are
  the ones this readout drives; spontaneous turns have their own per-side
  thresholds and are even by construction.
SECONDARY  the same for all turns, how many turns happen at all, wall bumps
  per tile, and tiles moved -- a readout that balances turning by never
  firing, or by making the fly crash more, is not an improvement.

Both arms see the same rooms: the fly starts from one snapshot each time.
"""
import json
import os
import shutil
import statistics as st
import sys

sys.path.insert(0, os.path.expanduser("~/flybrain"))
import numpy as np                                            # noqa: E402
from flybrain import game                                     # noqa: E402
from flybrain.connectome import Connectome                    # noqa: E402
from flybrain.engine import Params                            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "turn_balance.jsonl")
START = "/tmp/claude-1000/bias/"          # the FireRed snapshot both arms start from
SEEDS = (0, 1, 2, 3, 4)
PLAY_MS = 150_000.0

NEW_READOUT = game.WALL_READOUT
NEW_CROSSTALK = game.CROSSTALK.copy()
# What was in use before the mirror pairs (kept so the comparison is real).
OLD_READOUT = (
    (720575940627645514, +1), (720575940620054694, +1), (720575940623276168, -1),
    (720575940612804862, +1), (720575940618440000, -1), (720575940628437291, +1),
    (720575940627949589, +1), (720575940632903737, +1), (720575940622673860, +1),
    (720575940643347616, +1),
)
OLD_CROSSTALK = np.array([[362.9, 24.6, 133.3],
                          [-121.2, 1652.5, -135.8],
                          [-4.2, 70.4, 1390.4]])


def run(seed, which):
    game.WALL_READOUT = NEW_READOUT if which == "new" else OLD_READOUT
    m = NEW_CROSSTALK if which == "new" else OLD_CROSSTALK
    game.CROSSTALK = m
    game.UNMIX = np.diag(np.diag(m)) @ np.linalg.inv(m)

    rom, live_sav, live_state = game.default_paths()
    shutil.copyfile(live_sav, START + f"c-{seed}-{which}.sav")
    shutil.copyfile(START + "t.sav", START + f"r-{seed}-{which}.sav")
    cx = Connectome()
    g = game.Game(cx, rom, START + f"c-{seed}-{which}.sav", live_state,
                  params=Params(al_ln_inhibitory=True), seed=seed, learn=False)
    g.calibrate(verbose=False)
    g.load(rom.replace("ultraviolet", "firered"), START + f"r-{seed}-{which}.sav", None)
    g.gba.load_state(START + "t.ss")
    for _ in range(30):
        g.gba.frame(0)

    # Which way each avoidance turn went: the readout's sign decides it.
    left_avoid = right_avoid = 0
    seen = 0
    h = g.hands
    end = g.brain.t_ms + PLAY_MS
    while g.brain.t_ms < end:
        before = h.stats.get("avoid", 0)
        g.step()
        if h.stats.get("avoid", 0) > before:
            # decide() turns right when the obstacle reads left, and the other
            # way round; the sign at that moment says which happened.
            if h.obstacle > 0:
                right_avoid += 1
            else:
                left_avoid += 1
        seen += 1
    total = left_avoid + right_avoid
    row = {"seed": seed, "which": which,
           "avoid_left": left_avoid, "avoid_right": right_avoid,
           "avoid_left_share": left_avoid / total if total else None,
           "turns_left": h.stats.get("left", 0), "turns_right": h.stats.get("right", 0),
           "turns": h.stats.get("turns", 0), "tiles": g.steps_moved, "bumps": g.wall_bumps,
           "bumps_per_tile": g.wall_bumps / max(g.steps_moved, 1),
           "threshold": h.obstacle_thresh, "stats": dict(h.stats)}
    g.close()
    return row


def main():
    done = {}
    if os.path.exists(OUT):
        for line in open(OUT):
            if line.strip():
                r = json.loads(line)
                done[(r["seed"], r["which"])] = r
    for seed in SEEDS:
        for which in ("new", "old"):
            if (seed, which) in done:
                continue
            r = run(seed, which)
            with open(OUT, "a") as f:
                f.write(json.dumps(r) + "\n"); f.flush(); os.fsync(f.fileno())
            done[(seed, which)] = r
            sh = r["avoid_left_share"]
            print(f"seed {seed} {which:3s}: avoid turns {r['avoid_left']}L/{r['avoid_right']}R"
                  f" (left share {'n/a' if sh is None else f'{100*sh:.0f}%'}), "
                  f"all turns {r['turns_left']}L/{r['turns_right']}R, "
                  f"bumps/tile {r['bumps_per_tile']:.3f}, tiles {r['tiles']}", flush=True)

    pairs = [(done[(s, "new")], done[(s, "old")]) for s in SEEDS
             if (s, "new") in done and (s, "old") in done]
    d = [abs((n["avoid_left_share"] or 0.5) - 0.5) - abs((o["avoid_left_share"] or 0.5) - 0.5)
         for n, o in pairs]
    if len(d) > 1:
        m, sd = st.mean(d), st.stdev(d)
        t = m / (sd / len(d) ** 0.5) if sd else float("nan")
        print(f"\nPRIMARY |left share - 50%|, new minus old: {m:+.3f} (sd {sd:.3f}), t = {t:+.2f}, n = {len(d)}")
        print(f"  new: {100*st.mean([abs((n['avoid_left_share'] or 0.5) - 0.5) for n, _ in pairs]):.1f}% off even"
              f"   old: {100*st.mean([abs((o['avoid_left_share'] or 0.5) - 0.5) for _, o in pairs]):.1f}% off even")
        for k in ("turns", "bumps_per_tile", "tiles"):
            print(f"  {k:15s} new {st.mean([n[k] for n, _ in pairs]):8.3f}   "
                  f"old {st.mean([o[k] for _, o in pairs]):8.3f}")


if __name__ == "__main__":
    main()
