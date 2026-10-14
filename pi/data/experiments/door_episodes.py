"""Does the approach lock help the fly find doors? Episode-based, paired by seed.

Supersedes door_finding.py, which timed only the FIRST exit of a run: one
number per 180 s, sd 35 s across seeds, powered to detect roughly a 40 s
effect and duly came back null. Here every room the fly enters is an episode
-- entered at t0, left at t1 -- so a run yields as many measurements as it
makes moves between maps.

CONDITIONS (same seed, same starting save, same calibration, one difference)
  lock    exit-ahead channel (EXIT_AHEAD_READOUT) and commitment window live.
  nolock  exit_ahead_thresh = 1e9, commit_ms = 0: decide() falls through
          exactly as it did before the change.
Both arms get the head-on bias correction, which is a fix to the lateral
readout rather than part of the lock, and must not be confounded with it.

PRIMARY MEASURE  mean seconds per completed room episode, per run, lock minus
  nolock, paired t-test across 8 seeds, two-sided, alpha 0.05. The final,
  unfinished episode of each run is dropped (it is censored by the clock, not
  by the fly), and a run needs >= 2 completed episodes to count.

SECONDARY  episodes completed per run, median episode length, tiles, bumps,
  fraction of frames locked, turn stats, and the calibration numbers.

Runs are 300 s to give several episodes each. Resumable: one fsynced JSON
line per finished run.
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
OUT = os.path.join(HERE, "door_episodes.jsonl")
BACKUP = os.path.expanduser("~/flybrain/play/backup-20260917-134047")
SCRATCH = os.environ.get("SCRATCH", "/tmp/claude-1000/door_episodes")

SEEDS = range(8)
CONDITIONS = ("lock", "nolock")
PLAY_MS = 300_000.0


def run(seed, condition):
    os.makedirs(SCRATCH, exist_ok=True)
    rom, live_sav, live_state = game.default_paths()
    calib_sav = os.path.join(SCRATCH, f"calib-{seed}-{condition}.sav")
    run_sav = os.path.join(SCRATCH, f"run-{seed}-{condition}.sav")
    shutil.copyfile(live_sav, calib_sav)        # never the live files themselves
    shutil.copyfile(os.path.join(BACKUP, "newgame.sav"), run_sav)

    g = game.Game(Connectome(), rom, calib_sav, live_state, seed=seed, learn=False)
    g.calibrate(verbose=False)
    cal = {"exit_ahead_thresh": g.hands.exit_ahead_thresh,
           "exit_ahead_check": list(g.exit_ahead_check),
           "exit_bias_check": list(g.exit_bias_check),
           "exit_bias": [g.hands.exit_bias_a, g.hands.exit_bias_b],
           "obstacle": g.hands.obstacle_thresh, "exits": g.hands.exit_thresh}
    armed = g.hands.exit_ahead_thresh < 1e8
    if condition == "nolock":
        g.hands.exit_ahead_thresh = 1e9
        g.hands.commit_ms = 0.0
    g.load(rom, run_sav, None)
    g.gba.load_state(os.path.join(BACKUP, "newgame.resume.ss"))
    for _ in range(30):
        g.gba.frame(0)

    t0 = g.brain.t_ms
    cur_map, entered = None, t0
    episodes = []
    locked = total = 0
    started = time.time()
    while g.brain.t_ms < t0 + PLAY_MS:
        g.step()
        if g.brain.steps % 17:
            continue
        total += 1
        locked += bool(g.hands.locked)
        pos = g.gba.player()
        if pos is None:
            continue
        if cur_map is None:
            cur_map, entered = pos[:2], g.brain.t_ms
        elif pos[:2] != cur_map:
            episodes.append((g.brain.t_ms - entered) / 1000.0)
            cur_map, entered = pos[:2], g.brain.t_ms

    mean_ep = sum(episodes) / len(episodes) if episodes else None
    row = {
        "seed": seed, "condition": condition,
        "episodes": episodes, "n_episodes": len(episodes), "mean_episode_s": mean_ep,
        "maps": len(g.maps), "tiles": g.steps_moved, "bumps": g.wall_bumps,
        "locked_frac": locked / max(total, 1), "armed": armed,
        "stats": dict(g.hands.stats), "calibration": cal,
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
            print(f"  !! seed {seed} {condition}: approach lock never armed", flush=True)
        mean = f"{row['mean_episode_s']:.1f}s" if row["mean_episode_s"] else "n/a"
        print(f"seed {seed} {condition:6s}: {row['n_episodes']} episodes, mean {mean}, "
              f"{row['tiles']} tiles, {row['bumps']} bumps, "
              f"locked {100*row['locked_frac']:.1f}%, {row['wall_s']}s", flush=True)


if __name__ == "__main__":
    main()
