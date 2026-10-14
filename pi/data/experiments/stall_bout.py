"""Does ending a bout on a stall stop the fly grinding into walls?

Giving the bout hysteresis got the fly moving but more than doubled wall bumps
per tile moved (0.265 vs 0.117, t=+2.99): a sustained bout holds the direction
key against a wall because nothing ends it when the fly stops getting
anywhere. The bristle mechanosensors already report that -- Eyes drives them at
150 Hz whenever the heading is held and the tile does not change -- and
STALL_READOUT reads it out of ten descending neurons (composite +3857 Hz,
t=+939, 10/10 held on fresh seeds; DNp09, the stop neuron known from real
flies, responds too at t=+4.33). This asks whether using it helps.

PHASE A, DISCOVERY (seeds 100-103) picks the threshold rule, since the two
candidates disagreed on a single seed and choosing on that seed would be
tuning on the test set:
  sensitive     operating point maximising true-positive minus false-positive
                rate (catches ~99% of stalls, fires on ~30% of other frames)
  conservative  99th percentile of the not-stuck distribution, the rule the
                other action-triggering readouts use (catches under half)
Chosen by bumps per tile moved, lower is better.

PHASE B, VALIDATION (seeds 0-7, never used in phase A) compares the chosen rule
against stall detection off, everything else identical.
  PRIMARY  wall bumps per tile moved, chosen minus off, paired t-test across
           8 seeds, two-sided, alpha 0.05.
  SECONDARY  tiles moved, fraction of frames walking, maps entered, bouts,
           stalls detected, and the game's own progress fields.

Resumable: one fsynced JSON line per run.
"""
import json
import os
import shutil
import statistics as st
import sys
import time

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain import game                                    # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "stall_bout.jsonl")
BACKUP = os.path.expanduser("~/flybrain/play/backup-20260917-134047")
SCRATCH = os.environ.get("SCRATCH", "/tmp/claude-1000/stall_bout")

DISCOVERY_SEEDS = (100, 101, 102, 103)
VALIDATION_SEEDS = tuple(range(8))
PLAY_MS = 180_000.0


def run(seed, condition):
    """condition: sensitive | conservative | off"""
    os.makedirs(SCRATCH, exist_ok=True)
    rom, live_sav, live_state = game.default_paths()
    calib_sav = os.path.join(SCRATCH, f"c-{seed}-{condition}.sav")
    run_sav = os.path.join(SCRATCH, f"r-{seed}-{condition}.sav")
    shutil.copyfile(live_sav, calib_sav)
    shutil.copyfile(os.path.join(BACKUP, "newgame.sav"), run_sav)

    g = game.Game(Connectome(), rom, calib_sav, live_state, seed=seed, learn=False)
    g.calibrate(verbose=False)
    chk = list(g.stall_check)
    if condition == "off":
        g.hands.stall_thresh = 1e9
    elif condition == "conservative" and g.hands.stall_thresh < 1e8:
        # Recompute the old way from the same warm-up samples the sensitive
        # rule used, so the two differ only in where the line is drawn.
        g.hands.stall_thresh = float(max(0.5 * g._stall_clear_p95 + 0.5 * chk[0],
                                         g._stall_clear_p99))
    thresh = g.hands.stall_thresh

    g.load(rom, run_sav, None)
    g.gba.load_state(os.path.join(BACKUP, "newgame.resume.ss"))
    for _ in range(30):
        g.gba.frame(0)

    p0 = g.gba.progress()
    walking = total = 0
    started = time.time()
    end = g.brain.t_ms + PLAY_MS
    while g.brain.t_ms < end:
        g.step()
        if g.brain.steps % 100:
            continue
        total += 1
        walking += bool(g.hands.walking)

    row = {"seed": seed, "condition": condition,
           "bumps": g.wall_bumps, "tiles": g.steps_moved,
           "bumps_per_tile": g.wall_bumps / max(g.steps_moved, 1),
           "walking_frac": walking / max(total, 1),
           "maps": len(g.maps), "distinct_tiles": len(g.visited),
           "stall_thresh": thresh, "stall_check": chk,
           "stats": dict(g.hands.stats),
           "progress_before": p0, "progress_after": g.gba.progress(),
           "wall_s": round(time.time() - started, 1)}
    g.close()
    return row


def load():
    rows = {}
    if os.path.exists(OUT):
        for line in open(OUT):
            if line.strip():
                r = json.loads(line)
                rows[(r["seed"], r["condition"])] = r
    return rows


def do(seed, condition, rows):
    if (seed, condition) in rows:
        return rows[(seed, condition)]
    r = run(seed, condition)
    with open(OUT, "a") as f:
        f.write(json.dumps(r) + "\n")
        f.flush()
        os.fsync(f.fileno())
    rows[(seed, condition)] = r
    print(f"seed {seed} {condition:12s}: {r['bumps']:3d} bumps / {r['tiles']:4d} tiles "
          f"= {r['bumps_per_tile']:.3f}, walking {100*r['walking_frac']:4.1f}%, "
          f"{r['stats'].get('stalled', 0):3d} stalls, {r['wall_s']}s", flush=True)
    return r


def main():
    rows = load()
    print("PHASE A: discovery, which threshold rule", flush=True)
    means = {}
    for cond in ("sensitive", "conservative"):
        vals = [do(s, cond, rows)["bumps_per_tile"] for s in DISCOVERY_SEEDS]
        means[cond] = st.mean(vals)
        print(f"  {cond:12s} mean bumps/tile {means[cond]:.3f}", flush=True)
    chosen = min(means, key=means.get)
    print(f"  CHOSEN: {chosen}\n", flush=True)

    print("PHASE B: validation on fresh seeds", flush=True)
    for s in VALIDATION_SEEDS:
        do(s, chosen, rows)
        do(s, "off", rows)
    d = [rows[(s, chosen)]["bumps_per_tile"] - rows[(s, "off")]["bumps_per_tile"]
         for s in VALIDATION_SEEDS]
    m, sd = st.mean(d), st.stdev(d)
    t = m / (sd / len(d) ** 0.5)
    print(f"\nPRIMARY bumps per tile, {chosen} minus off: mean {m:+.3f}, sd {sd:.3f}, "
          f"t = {t:+.2f}, n = {len(d)}")
    print(f"VERDICT: {'SHOWN' if abs(t) > 2.365 else 'NOT SHOWN'} (t crit 2.365)")


if __name__ == "__main__":
    main()
