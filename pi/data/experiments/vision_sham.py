"""Real obstacle vision vs sham vision.

Sham vision replays the fly's own real obstacle views from a random moment at
least 5 s earlier (continuous playback, re-drawn about once a second). It
drives LPLC1 with realistic views -- similar amount, timing and left/right mix
-- but carries no information about where the player is now. Both conditions
calibrate identically on real views; only play differs. So any difference is
spatial information, not extra arousal from the input itself.

PRIMARY    bump fraction = bumps / (bumps + tiles moved), per run, in [0, 1].
           A seed counts only if both runs made >= 5 attempts. Real vision
           wins only if its fraction is lower with two-sided p < 0.05
           (critical t by degrees of freedom from the table below).
SECONDARY  tiles moved, distinct tiles, avoidance turns.
Resumable: one JSON line per finished run.
"""
import os, sys, json, time, types
sys.path.insert(0, "~/flybrain")
import numpy as np
from flybrain.connectome import Connectome
from flybrain import game

HERE = os.path.dirname(os.path.abspath(__file__))
CK = os.path.join(HERE, "vision_sham.jsonl")
logf = open(os.path.join(HERE, "vision_sham.log"), "a")
def log(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); logf.write(s + "\n"); logf.flush(); os.fsync(logf.fileno())

SEEDS, PLAY_MS, MIN_ATTEMPTS = range(8), 60000.0, 5
T_CRIT = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365}

def instrument(eyes, sham, rng):
    """Record every real view; in sham mode, replace it with a replayed past one."""
    eyes.history, eyes.sham, eyes.cursor, eyes.jump_at = [], False, 0, 0
    real_update = eyes.update
    def update(self, *args, **kw):
        real_update(*args, **kw)
        self.history.append(self.seen)
        if self.sham and len(self.history) > 400:
            n = len(self.history)
            if self.cursor >= self.jump_at or self.cursor >= n - 300:
                self.cursor = int(rng.integers(0, n - 300))   # >= 5 s in the past
                self.jump_at = self.cursor + 60                # about one second of playback
            self.seen = self.history[self.cursor]
            self.cursor += 1
            clip = lambda v: float(min(max(v, 0.0), 150.0))
            self.rates["obstacle_left"] = clip(60.0 * self.seen[0])
            self.rates["obstacle_right"] = clip(60.0 * self.seen[1])
    eyes.update = types.MethodType(update, eyes)

done = {}
if os.path.exists(CK):
    for line in open(CK):
        line = line.strip().strip("\x00")
        if line:
            r = json.loads(line); done[(r["seed"], r["cond"])] = r

cx = Connectome()
for seed in SEEDS:
    for cond in ("real", "sham"):
        if (seed, cond) in done:
            continue
        t0 = time.time()
        g = game.Game(cx, *game.default_paths(), seed=seed)
        instrument(g.eyes, sham=False, rng=np.random.default_rng(1000 + seed))
        g.calibrate(verbose=False)
        g.eyes.sham = (cond == "sham")
        bumps, was = 0, False
        end = g.brain.t_ms + PLAY_MS
        while g.brain.t_ms < end:
            g.step()
            if g.brain.steps % 17 == 0:
                if g.eyes.touching and not was:
                    bumps += 1
                was = g.eyes.touching
        h = g.hands
        attempts = bumps + g.steps_moved
        rec = {"seed": seed, "cond": cond, "bumps": bumps, "moved": g.steps_moved,
               "distinct": len(g.visited), "avoid": h.stats.get("avoid", 0), "turns": h.stats["turns"],
               "attempts": attempts, "bump_frac": (bumps / attempts) if attempts else None,
               "wall_s": round(time.time() - t0, 1)}
        g.close()
        with open(CK, "a") as f:
            f.write(json.dumps(rec) + "\n"); f.flush(); os.fsync(f.fileno())
        done[(seed, cond)] = rec
        bf = "n/a" if rec["bump_frac"] is None else f"{rec['bump_frac']:.2f}"
        log(f"  seed {seed} {cond}: bumps {bumps:3d}, moved {g.steps_moved:3d}, distinct {rec['distinct']:3d}, "
            f"avoid {rec['avoid']:2d}, turns {rec['turns']:3d}, bump fraction {bf}")

log("\nREAL vs SHAM VISION (paired by seed; real minus sham)")
valid = [s for s in SEEDS if all(done[(s, c)]["attempts"] >= MIN_ATTEMPTS for c in ("real", "sham"))]
log(f"  seeds with >= {MIN_ATTEMPTS} attempts in both runs: {len(valid)} of {len(SEEDS)} {valid}")
if len(valid) >= 2:
    d = np.array([done[(s, "real")]["bump_frac"] - done[(s, "sham")]["bump_frac"] for s in valid])
    sd = d.std(ddof=1); t = d.mean() / (sd / np.sqrt(len(d))) if sd > 0 else 0.0
    crit = T_CRIT[len(d) - 1]
    log(f"  PRIMARY   bump fraction    real {np.mean([done[(s,'real')]['bump_frac'] for s in valid]):.3f}  "
        f"sham {np.mean([done[(s,'sham')]['bump_frac'] for s in valid]):.3f}  diff {d.mean():+.3f} ± {sd:.3f}  "
        f"t={t:+.2f} (need <= -{crit})  {int((d < 0).sum())}/{len(d)} seeds lower with real vision")
    log(f"  primary verdict: {'REAL VISION HELPS' if t <= -crit else 'NOT SHOWN'}")
else:
    log("  primary verdict: INCONCLUSIVE (too few seeds with enough attempts)")
for key, label in (("moved", "tiles moved"), ("distinct", "distinct tiles"), ("avoid", "avoidance turns")):
    d = np.array([done[(s, "real")][key] - done[(s, "sham")][key] for s in SEEDS], float)
    sd = d.std(ddof=1); t = d.mean() / (sd / np.sqrt(len(d))) if sd > 0 else 0.0
    log(f"  secondary {label:16s} real {np.mean([done[(s,'real')][key] for s in SEEDS]):6.2f}  "
        f"sham {np.mean([done[(s,'sham')][key] for s in SEEDS]):6.2f}  diff {d.mean():+6.2f}  t={t:+.2f}")
