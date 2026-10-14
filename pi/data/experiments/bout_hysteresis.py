"""Does giving the walking bout hysteresis get the fly moving? Paired by seed.

The old rule set stop_thresh = min(p15 of walking drive, start_thresh). Walking
drive always sits above resting drive, so it collapsed to exactly start_thresh:
one value for both starting and stopping a bout, so the bout chattered as the
drive jittered across it. Measured consequence: the fly stood still 66% of the
time and took 144 bouts in 90 s (heading_code.jsonl).

CONDITIONS (same seed, same save, same calibration, one line different)
  hyst  stop_thresh = min(p15 walking, p50 resting) -- a bout ends only when
        drive falls back to an ordinarily resting level.
  flat  stop_thresh = start_thresh, the old collapsed rule.

PRIMARY MEASURE  fraction of sampled frames in which the fly is walking, hyst
  minus flat, paired t-test across 8 seeds, two-sided, alpha 0.05. Bounded in
  0..1, so it cannot blow up the way a tile count can.

SECONDARY  tiles moved, distinct tiles visited, maps entered, bouts started,
  wall bumps, and the game's own progress fields (badges, party, dex, money)
  so that any real advance shows up rather than being inferred from motion.

Resumable: one fsynced JSON line per finished run.
"""
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain import game                                    # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "bout_hysteresis.jsonl")
BACKUP = os.path.expanduser("~/flybrain/play/backup-20260917-134047")
SCRATCH = os.environ.get("SCRATCH", "/tmp/claude-1000/bout_hysteresis")

SEEDS = range(8)
CONDITIONS = ("hyst", "flat")
PLAY_MS = 180_000.0


def run(seed, condition):
    os.makedirs(SCRATCH, exist_ok=True)
    rom, live_sav, live_state = game.default_paths()
    calib_sav = os.path.join(SCRATCH, f"calib-{seed}-{condition}.sav")
    run_sav = os.path.join(SCRATCH, f"run-{seed}-{condition}.sav")
    shutil.copyfile(live_sav, calib_sav)
    shutil.copyfile(os.path.join(BACKUP, "newgame.sav"), run_sav)

    g = game.Game(Connectome(), rom, calib_sav, live_state, seed=seed, learn=False)
    g.calibrate(verbose=False)
    if condition == "flat":
        g.hands.stop_thresh = g.hands.start_thresh      # the old collapsed rule
    band = [g.hands.start_thresh, g.hands.stop_thresh]
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
    p1 = g.gba.progress()

    row = {
        "seed": seed, "condition": condition,
        "walking_frac": walking / max(total, 1),
        "tiles": g.steps_moved, "distinct_tiles": len(g.visited), "maps": len(g.maps),
        "bumps": g.wall_bumps, "band": band,
        "stats": dict(g.hands.stats),
        "progress_before": p0, "progress_after": p1,
        "wall_s": round(time.time() - started, 1),
    }
    g.close()
    return row


def main():
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            if line.strip():
                r = json.loads(line)
                done.add((r["seed"], r["condition"]))
    todo = [(s, c) for s in SEEDS for c in CONDITIONS if (s, c) not in done]
    print(f"{len(done)} runs already done, {len(todo)} to go", flush=True)
    for seed, condition in todo:
        row = run(seed, condition)
        with open(OUT, "a") as f:
            f.write(json.dumps(row) + "\n")
            f.flush()
            os.fsync(f.fileno())
        print(f"seed {seed} {condition:4s}: walking {100*row['walking_frac']:4.1f}%, "
              f"{row['tiles']:4d} tiles, {row['distinct_tiles']:4d} distinct, "
              f"{row['maps']} maps, {row['stats'].get('bouts', 0):4d} bouts, "
              f"band {row['band'][0]:.2f}/{row['band'][1]:.2f}, {row['wall_s']}s", flush=True)


if __name__ == "__main__":
    main()
