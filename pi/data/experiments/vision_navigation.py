"""Does obstacle vision make the fly navigate better?

Vision on vs vision off (obstacle input zeroed, obstacle turns disabled -- the
system as it was), same brain seed and savestate, paired by seed.

PRIMARY    wall bumps per 10 tiles moved; vision counts as better only if the
           paired reduction has t >= 2.571 (two-sided p < 0.05, df = 5).
SECONDARY  tiles moved, distinct tiles, avoidance turns.
Caveat: a wild battle also reads as a bump (pushing without moving); it can
happen in either condition, so it adds noise rather than bias.
Resumable: one JSON line per finished run.
"""
import os, sys, json, time
sys.path.insert(0, "~/flybrain")
import numpy as np
from flybrain.connectome import Connectome
from flybrain import game

HERE = os.path.dirname(os.path.abspath(__file__))
CK = os.path.join(HERE, "vision_navigation.jsonl")
logf = open(os.path.join(HERE, "vision_navigation.log"), "a")
def log(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); logf.write(s + "\n"); logf.flush(); os.fsync(logf.fileno())

SEEDS, PLAY_MS = range(6), 45000.0
done = {}
if os.path.exists(CK):
    for line in open(CK):
        line = line.strip().strip("\x00")
        if line:
            r = json.loads(line); done[(r["seed"], r["vision"])] = r

cx = Connectome()
real_view = game.obstacle_view
for seed in SEEDS:
    for vision in (True, False):
        if (seed, vision) in done:
            continue
        game.obstacle_view = real_view if vision else (lambda grid, heading: (0.0, 0.0))
        g = game.Game(cx, *game.default_paths(), seed=seed)
        g.calibrate(verbose=False)
        if not vision:
            g.hands.obstacle_thresh = float("inf")
        bumps, was, t0 = 0, False, time.time()
        end = g.brain.t_ms + PLAY_MS
        while g.brain.t_ms < end:
            g.step()
            if g.brain.steps % 17 == 0:
                if g.eyes.touching and not was:
                    bumps += 1
                was = g.eyes.touching
        h = g.hands
        rec = {"seed": seed, "vision": vision, "bumps": bumps, "moved": g.steps_moved,
               "distinct": len(g.visited), "avoid": h.stats.get("avoid", 0),
               "turns": h.stats["turns"], "bump_rate": 10.0 * bumps / max(g.steps_moved, 1),
               "wall_s": round(time.time() - t0, 1)}
        g.close()
        with open(CK, "a") as f:
            f.write(json.dumps(rec) + "\n"); f.flush(); os.fsync(f.fileno())
        done[(seed, vision)] = rec
        log(f"  seed {seed} vision {'ON ' if vision else 'OFF'}: bumps {bumps:3d}, moved {rec['moved']:3d}, "
            f"distinct {rec['distinct']:3d}, avoid turns {rec['avoid']:3d}, bumps/10 tiles {rec['bump_rate']:.2f}")
game.obstacle_view = real_view

log("\nVISION ON vs OFF (paired by seed; ON minus OFF)")
for key, label, primary in (("bump_rate", "bumps per 10 tiles", True), ("moved", "tiles moved", False),
                            ("distinct", "distinct tiles", False), ("avoid", "avoidance turns", False)):
    on = np.array([done[(s, True)][key] for s in SEEDS], float)
    off = np.array([done[(s, False)][key] for s in SEEDS], float)
    d = on - off; sd = d.std(ddof=1)
    t = d.mean() / (sd / np.sqrt(len(d))) if sd > 0 else 0.0
    log(f"  {'PRIMARY  ' if primary else 'secondary'} {label:20s} on {on.mean():7.2f}  off {off.mean():7.2f}  "
        f"diff {d.mean():+7.2f} ± {sd:5.2f}  t={t:+5.2f}")
    if primary:
        verdict = "VISION HELPS" if (t <= -2.571) else "NOT SHOWN"
        log(f"  primary verdict (fewer bumps, t <= -2.571): {verdict}")
