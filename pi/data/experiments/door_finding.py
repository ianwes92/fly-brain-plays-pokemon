"""Does the approach lock help the fly find doors? Pre-registered, paired by seed.

The complaint being tested is plain: it takes too long to get out of a room.

CONDITIONS (same seed, same starting save, same calibration, one difference)
  lock    exit-ahead channel and commitment window live, as shipped.
  nolock  exit_ahead_thresh = 1e9 and commit_ms = 0, which makes decide()
          fall through exactly as it did before the change.

PRIMARY MEASURE  seconds of game time from the start of play until the map
  changes -- the fly leaves the room it starts in -- censored at 180 s, which
  is also the run length. Paired t-test across 8 seeds, two-sided, alpha 0.05.
  Censoring makes this conservative: a run that never gets out scores 180 s,
  so the measure understates any real difference rather than inventing one.

SECONDARY (reported, not decisive): maps entered in the whole run, tiles moved,
  wall bumps, fraction of frames locked, and the turn stats.

Every run records its own calibration numbers, so a run whose approach lock
never armed (the warm-up saw no door ahead) can be told apart from one where
it armed and did nothing.

Resumable: one fsynced JSON line per finished run; re-running skips those.
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
OUT = os.path.join(HERE, "door_finding.jsonl")
BACKUP = os.path.expanduser("~/flybrain/play/backup-20260917-134047")
SCRATCH = os.environ.get("SCRATCH", "/tmp/claude-1000/door_finding")

SEEDS = range(8)
CONDITIONS = ("lock", "nolock")
PLAY_MS = 180_000.0


def run(seed, condition):
    os.makedirs(SCRATCH, exist_ok=True)
    rom, live_sav, live_state = game.default_paths()
    calib_sav = os.path.join(SCRATCH, f"calib-{seed}-{condition}.sav")
    run_sav = os.path.join(SCRATCH, f"run-{seed}-{condition}.sav")
    shutil.copyfile(live_sav, calib_sav)        # never the live files themselves
    shutil.copyfile(os.path.join(BACKUP, "newgame.sav"), run_sav)

    # Calibrate where the player can walk, then swap to the run save, as
    # cli.py's play does. Both arms calibrate identically; the lock is only
    # disarmed afterwards, so the thresholds themselves are matched.
    g = game.Game(Connectome(), rom, calib_sav, live_state, seed=seed, learn=False)
    g.calibrate(verbose=False)
    armed = g.hands.exit_ahead_thresh < 1e8
    row_cal = {"exit_ahead_thresh": g.hands.exit_ahead_thresh,
               "exit_ahead_check": list(g.exit_ahead_check),
               "obstacle": g.hands.obstacle_thresh, "exits": g.hands.exit_thresh}
    if condition == "nolock":
        g.hands.exit_ahead_thresh = 1e9
        g.hands.commit_ms = 0.0
    g.load(rom, run_sav, None)
    g.gba.load_state(os.path.join(BACKUP, "newgame.resume.ss"))
    for _ in range(30):
        g.gba.frame(0)

    t0 = g.brain.t_ms
    start = g.gba.player()
    left_at = None
    locked = total = 0
    started = time.time()
    while g.brain.t_ms < t0 + PLAY_MS:
        g.step()
        if g.brain.steps % 17:
            continue
        total += 1
        locked += bool(g.hands.locked)
        if left_at is None:
            pos = g.gba.player()
            if pos is not None and start is not None and pos[:2] != start[:2]:
                left_at = (g.brain.t_ms - t0) / 1000.0

    row = {
        "seed": seed, "condition": condition,
        "seconds_to_leave": left_at if left_at is not None else PLAY_MS / 1000.0,
        "got_out": left_at is not None,
        "maps": len(g.maps), "tiles": g.steps_moved, "bumps": g.wall_bumps,
        "locked_frac": locked / max(total, 1), "armed": armed,
        "start": list(start) if start else None,
        "end": list(g.gba.player() or ()),
        "stats": dict(g.hands.stats), "calibration": row_cal,
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
        if not row["armed"]:
            print(f"  !! seed {seed} {condition}: approach lock never armed at calibration",
                  flush=True)
        print(f"seed {seed} {condition:6s}: out in "
              f"{row['seconds_to_leave']:5.1f}s{'' if row['got_out'] else ' (never)'}, "
              f"{row['maps']} maps, {row['tiles']} tiles, {row['bumps']} bumps, "
              f"locked {100*row['locked_frac']:.1f}%, {row['wall_s']}s", flush=True)


if __name__ == "__main__":
    main()
