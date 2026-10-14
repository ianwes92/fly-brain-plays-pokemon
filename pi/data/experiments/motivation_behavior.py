"""Do the urges change behaviour? Pre-registered, paired by seed.

The claim under test is the one the urges were built for: after too long in a
room the fly should work its way toward an exit, and when it has not spoken to
anyone for a while it should work its way toward a person.

CONDITIONS (identical in every other respect, same seed, same starting save)
  urges  normal Game._motivate: gains 0.25 + 0.75 * urge, pleasure and pain.
  flat   gains pinned at 0.625 (the midpoint of that range) for both channels,
         urges pinned at 0.5, no pleasure or pain bursts.
A sham, not an off switch: the exit and person channels carry the same picture
with about the same average drive, so what differs is only whether the pull
grows with time. Learning is DISABLED in both arms, so nothing here depends on
the mushroom body -- which, per the replication in the README, does not reach
the motor neurons anyway.

PRIMARY MEASURE  approach index toward exits, late minus early, urges minus flat.
  approach index = fraction of tile-moves that shorten the Chebyshev distance
  to the nearest exit visible within 7 tiles (an exit must be visible from both
  the tile left and the tile entered, and the map must not have changed).
  early = moves made < 20 s after entering the current map
  late  = moves made > 45 s after entering it   (leave urge ~0.28 vs ~0.53)
  A run contributes only if both bins hold >= 10 qualifying moves.
DECISION RULE  paired t-test across seeds, two-sided, alpha 0.05. Nothing else
  in this file overrides that, however good it looks.

SECONDARY (reported, not decisive): the same index for people, binned on time
  since the last conversation (< 30 s vs > 70 s); exits taken per minute;
  conversations per minute; tiles moved; wall bumps.

Resumable: one JSON line per finished run, fsynced. Re-running skips what is
already in the file.
"""
import json
import os
import shutil
import sys
import time

import numpy as np

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain import game                                    # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402
from flybrain.game import GBA, HEADINGS, A                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "motivation_behavior.jsonl")
BACKUP = os.path.expanduser("~/flybrain/play/backup-20260917-134047")
SCRATCH = os.environ.get("SCRATCH", "/tmp/claude-1000/motivation_behavior")

SEEDS = range(8)
CONDITIONS = ("urges", "flat")
PLAY_MS = 180_000.0
EARLY_MAP_MS, LATE_MAP_MS = 20_000.0, 45_000.0
EARLY_TALK_MS, LATE_TALK_MS = 30_000.0, 70_000.0
RADIUS = 7
MIN_MOVES = 10


def flat_motivate(self, pos):
    """The sham: same average drive on both channels, no growth, no valence."""
    self.hands.urge_leave = self.hands.urge_talk = 0.5
    self.eyes.gain_exits = self.eyes.gain_people = 0.625
    self.eyes.pleasure_hz = self.eyes.pain_hz = 0.0


def nearest(grid, kind):
    """Chebyshev distance from the player to the nearest tile of `kind`."""
    ys, xs = np.nonzero(grid == kind)
    if len(ys) == 0:
        return None
    r = grid.shape[0] // 2
    return int(np.maximum(np.abs(ys - r), np.abs(xs - r)).min())


class Bins:
    """Approach counts, split into an early and a late time bin."""

    def __init__(self):
        self.hit = {"early": 0, "late": 0}
        self.n = {"early": 0, "late": 0}

    def add(self, bin_, approached):
        if bin_ is None:
            return
        self.n[bin_] += 1
        self.hit[bin_] += bool(approached)

    def index(self, bin_):
        return self.hit[bin_] / self.n[bin_] if self.n[bin_] else None

    def as_dict(self):
        return {"early": self.index("early"), "late": self.index("late"),
                "n_early": self.n["early"], "n_late": self.n["late"]}


def run(seed, condition):
    os.makedirs(SCRATCH, exist_ok=True)
    sav = os.path.join(SCRATCH, f"run-{seed}-{condition}.sav")
    shutil.copyfile(os.path.join(BACKUP, "newgame.sav"), sav)

    # Calibrate where the player can walk, then swap to the run save -- the
    # same order as cli.py's play. Warming up on the title screen instead
    # leaves obstacle_thresh at its 10 Hz default, and avoidance then wins
    # every decision and the attraction branch under test never runs.
    rom, live_sav, live_state = game.default_paths()
    calib_sav = os.path.join(SCRATCH, f"calib-{seed}-{condition}.sav")
    shutil.copyfile(live_sav, calib_sav)       # never the live files themselves

    cx = Connectome()
    g = game.Game(cx, rom, calib_sav, live_state, seed=seed, learn=False)
    if condition == "flat":
        g._motivate = flat_motivate.__get__(g)
    g.calibrate(verbose=False)
    checks = {k: list(v) for k, v in g.attraction_check.items()}
    thresholds = {"obstacle": g.hands.obstacle_thresh, "wall": g.hands.wall_thresh,
                  "people": g.hands.people_thresh, "exits": g.hands.exit_thresh}
    g.load(rom, sav, None)
    g.gba.load_state(os.path.join(BACKUP, "newgame.resume.ss"))
    for _ in range(30):
        g.gba.frame(0)

    exits_bins, people_bins = Bins(), Bins()
    last_pos = last_de = last_dp = None
    exits_taken = talks = 0
    talk_log = {}
    # Both clocks start when play starts, which is also when the fly's own
    # Motivation first sees this map -- otherwise calibration time counts as
    # time in the room and the early bin is empty.
    t0 = g.brain.t_ms
    map_since = talk_since = t0
    end = t0 + PLAY_MS
    started = time.time()

    while g.brain.t_ms < end:
        g.step()
        if g.brain.steps % 8:
            continue
        t = g.brain.t_ms
        pos = g.gba.player()
        if pos is None or not g.in_overworld():
            continue

        # A conversation: A pressed facing a person, one per person per 8 s.
        if (g.hands.keys & A) and g.eyes.scene is not None:
            r = g.eyes.scene.shape[0] // 2
            _, hx, hy, _ = HEADINGS[g.hands.heading]
            if g.eyes.scene[r + hy, r + hx] == GBA.PERSON:
                key = (pos[:2], pos[2] + hx, pos[3] + hy)
                if t - talk_log.get(key, -1e12) >= 8000.0:
                    talk_log[key] = t
                    talks += 1
                    talk_since = t

        if pos == last_pos:
            continue
        if last_pos is not None and pos[:2] != last_pos[:2]:
            exits_taken += 1
            map_since = t
            last_pos, last_de, last_dp = pos, None, None
            continue

        grid = g.gba.scene_grid(pos, radius=RADIUS)
        de, dp = nearest(grid, GBA.EXIT), nearest(grid, GBA.PERSON)
        if last_pos is not None:
            on_map = t - map_since
            map_bin = ("early" if on_map < EARLY_MAP_MS
                       else "late" if on_map > LATE_MAP_MS else None)
            since_talk = t - talk_since
            talk_bin = ("early" if since_talk < EARLY_TALK_MS
                        else "late" if since_talk > LATE_TALK_MS else None)
            if de is not None and last_de is not None:
                exits_bins.add(map_bin, de < last_de)
            if dp is not None and last_dp is not None:
                people_bins.add(talk_bin, dp < last_dp)
        last_pos, last_de, last_dp = pos, de, dp

    minutes = PLAY_MS / 60_000.0
    row = {
        "seed": seed, "condition": condition,
        "exits": exits_bins.as_dict(), "people": people_bins.as_dict(),
        "exits_taken": exits_taken, "exits_per_min": exits_taken / minutes,
        "talks": talks, "talks_per_min": talks / minutes,
        "tiles": g.steps_moved, "maps": len(g.maps), "bumps": g.wall_bumps,
        "stats": dict(g.hands.stats), "motivation": dict(g.motivation.counts),
        # Calibration provenance. If toward_exit + toward_person is 0 the
        # branch under test never ran and the primary measures nothing --
        # which is exactly how the first 16-run set was wasted.
        "thresholds": thresholds, "attraction_check": checks,
        "wall_s": round(time.time() - started, 1),
    }
    g.close()
    return row


def main():
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            line = line.strip()
            if line:
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
        e, st_ = row["exits"], row["stats"]
        attr = st_.get("toward_exit", 0) + st_.get("toward_person", 0)
        if attr == 0:
            print(f"  !! seed {seed} {condition}: attraction branch never fired "
                  f"(thresholds {row['thresholds']}) -- run is not usable", flush=True)
        print(f"seed {seed} {condition:5s}: exits idx early {e['early']} late {e['late']} "
              f"(n {e['n_early']}/{e['n_late']}), took {row['exits_taken']} exits, "
              f"{row['talks']} talks, {row['tiles']} tiles, {row['wall_s']}s", flush=True)


if __name__ == "__main__":
    main()
