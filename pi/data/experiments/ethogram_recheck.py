"""RE-CHECK (2026-10-03): ethogram_now.py UNCHANGED except the output file.

The 2026-09-19 run showed one pre-registered measure: bumps per tile, fly
0.106 vs matched coin 0.178, t=-4.19. The fly has changed since (stall rest,
B-threshold calibration, the median turn rule adopted 2026-10-02), so the
claim is re-tested on the current code with the same seeds, maps, control,
measures and threshold (|t| > 2.571, 5 d.f.). Scored by
ethogram_recheck_score.py. Run alongside the live stream at nice 19, which
slows the stream but not the result: the simulation is deterministic in
brain time.

What does this fly actually do NOW -- measured as a fly, against a coin.

The game was being used as a benchmark it cannot pass: Pokemon rewards
sustained goal-directed sequences, and this animal is reactive, with no state
outliving a 600 ms decision (see flybrain-navigation notes; the central complex
has no ring attractor to give it one). Six pre-registered attempts to make it
progress produced one win, and that was a plumbing fix. So the yardstick
changes: measure behaviours a real Drosophila has, where the connectome is
doing the work, each against a null model -- otherwise the numbers say nothing.

MEASURES
  thigmotaxis      Flies hug walls (centrophobism, well documented). Fraction
                   of DISTINCT tiles visited that touch a wall, against the
                   map's OWN fraction of walkable tiles that touch a wall. A
                   corridor is wall-adjacent whatever the fly does, so only the
                   difference from that baseline is behaviour -- and counting
                   distinct tiles rather than time stops an agent that is
                   simply stuck against a wall from scoring as a wall-follower,
                   which is exactly what the random control does.
  wall_following   Consecutive MOVES where the fly left one wall-adjacent tile
                   for another: travelling along a wall, not resting on it.
  exploration      Distinct tiles per move -- how much new ground a step buys.
                   Compared with a matched random walker (below).
  collision        Wall bumps per tile moved.
  turn symmetry    Left versus right turns. A real fly is roughly balanced;
                   ours was 82/6 before the readouts were re-centred, so this
                   is a standing regression check as much as an ethogram entry.
  bout structure   Walking bout durations. Fly walking comes in bouts; the
                   median and the coefficient of variation describe the shape.

CONTROL  a matched random walker: the same walking bout rate and turn rate the
  fly itself produced in that run, but directions chosen at random and no
  vision. It answers "would a coin do this?" for every measure above. It is
  run in the same maps from the same save, so the geometry is identical.

This is the same comparison as ethogram.jsonl, re-run on the fly as it now
runs, because that first baseline predates most of what the fly is: the
antennal lobe's local neurons inhibitory, the bout stall stop, scents, mood and
its learned opinions, and FireRed instead of Ultra Violet. Then it was NOT
distinguishable from a random walker on any measure (bumps/tile t=-2.24 with
2.57 needed, exploration t=+0.39, no thigmotaxis, fewer tiles t=-2.65).

Two things differ from that run besides the fly. The control's walking is now
matched to the fly's OWN measured walking fraction and bout length from the
same seed, rather than a rough guess from its tile count -- the old matching
was crude enough that "fewer tiles moved" may have been the control's doing.
And the fly here gets no chat presses and no game rewards: this is the animal
on its own, which is what the comparison is about.
"""
import json
import os
import shutil
import statistics as st
import sys
import time

import numpy as np

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain import game                                    # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402
from flybrain.engine import Params                           # noqa: E402
from flybrain.mood import Mood                               # noqa: E402
from flybrain.nose import Nose, kc_signatures                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "ethogram_recheck.jsonl")
START = os.path.expanduser("~/flybrain/play/ethogram_start")   # the live FireRed game
SCRATCH = os.environ.get("SCRATCH", "/tmp/claude-1000/ethogram_recheck")

SEEDS = range(6)
CONDITIONS = ("fly", "random")
PLAY_MS = 240_000.0


def wall_adjacent(g, pos):
    """Does the fly's own tile touch a solid one?"""
    grid = g.gba.solid_grid(pos, radius=1)
    return bool(grid[0, 1] or grid[2, 1] or grid[1, 0] or grid[1, 2])


def run(seed, condition, rates=None):
    os.makedirs(SCRATCH, exist_ok=True)
    rom, live_sav, live_state = game.default_paths()
    calib_sav = os.path.join(SCRATCH, f"c-{seed}-{condition}.sav")
    run_sav = os.path.join(SCRATCH, f"r-{seed}-{condition}.sav")
    shutil.copyfile(live_sav, calib_sav)
    shutil.copyfile(START + ".sav", run_sav)

    cx = Connectome()
    P = Params(al_ln_inhibitory=True)          # the brain as the game runs it
    g = game.Game(cx, rom, calib_sav, live_state, params=P, seed=seed, learn=True)
    g.calibrate(verbose=False)
    g.load(rom.replace("ultraviolet", "firered"), run_sav, None)
    g.gba.load_state(START + ".ss")
    for _ in range(30):
        g.gba.frame(0)
    g.nose = Nose(cx)                          # scents, mood and opinions, as in play
    g.mood = Mood(cx, signatures=kc_signatures(cx, params=P, log=lambda *a: None))
    g.auto_rewards = False

    rng = np.random.default_rng(1000 + seed)
    occupied, touching, moves_along = [], [], []
    last_adj = False
    bouts, bout_start = [], None
    was_walking = False
    last_pos = None
    maps_seen = {}
    started = time.time()
    end = g.brain.t_ms + PLAY_MS

    # The matched random walker replays the fly's own bout and turn rates.
    rnd_walking, rnd_next = False, 0.0
    walk_samples = []
    while g.brain.t_ms < end:
        if condition == "random":
            t = g.brain.t_ms
            if t >= rnd_next:
                # Flip walking on or off, and pick a fresh heading, at the
                # rates the fly produced in its own run of this seed.
                rnd_walking = rng.random() < rates["walking_frac"]
                g.hands.heading = int(rng.integers(4))
                rnd_next = t + rng.exponential(rates["mean_bout_ms"])
            g.hands.force_walk = rnd_walking
        g.step()
        if g.brain.steps % 50:
            continue
        pos = g.gba.player()
        if pos is None or not g.in_overworld():
            continue
        walking = bool(g.hands.keys & (game.UP | game.DOWN | game.LEFT | game.RIGHT))
        walk_samples.append(walking)
        if walking and not was_walking:
            bout_start = g.brain.t_ms
        elif was_walking and not walking and bout_start is not None:
            bouts.append(g.brain.t_ms - bout_start)
        was_walking = walking
        if pos != last_pos:
            adj = wall_adjacent(g, pos)
            if last_pos is not None and pos[:2] == last_pos[:2]:
                moves_along.append(adj and last_adj)
            last_adj = adj
            occupied.append(pos)
            touching.append(adj)
            if pos[:2] not in maps_seen:
                shape = g.gba.map_shape()
                if shape:
                    maps_seen[pos[:2]] = (shape[1], shape[2])
            last_pos = pos

    walk_tiles = sum(v[0] for v in maps_seen.values())
    adj_tiles = sum(v[1] for v in maps_seen.values())
    row = {
        "seed": seed, "condition": condition,
        "moves": len(occupied), "distinct": len(set(occupied)),
        "maps": len(maps_seen),
        "thigmotaxis_time": float(np.mean(touching)) if touching else None,
        "thigmotaxis": (float(np.mean([t for p, t in
                        {p: t for p, t in zip(occupied, touching)}.items()]))
                        if touching else None),
        "wall_following": float(np.mean(moves_along)) if moves_along else None,
        "map_baseline": adj_tiles / walk_tiles if walk_tiles else None,
        "explore_per_move": len(set(occupied)) / max(len(occupied), 1),
        "bumps": g.wall_bumps,
        "bumps_per_tile": g.wall_bumps / max(g.steps_moved, 1),
        "left": g.hands.stats.get("left", 0), "right": g.hands.stats.get("right", 0),
        "bout_median_ms": st.median(bouts) if bouts else None,
        "bout_cv": (st.stdev(bouts) / st.mean(bouts)) if len(bouts) > 1 and st.mean(bouts) else None,
        "n_bouts": len(bouts),
        "walking_frac": float(np.mean(walk_samples)) if walk_samples else 0.0,
        "stats": dict(g.hands.stats),
        "wall_s": round(time.time() - started, 1),
    }
    g.close()
    return row


def main():
    rows = {}
    if os.path.exists(OUT):
        for line in open(OUT):
            if line.strip():
                r = json.loads(line)
                rows[(r["seed"], r["condition"])] = r

    for seed in SEEDS:
        if (seed, "fly") not in rows:
            r = run(seed, "fly")
            with open(OUT, "a") as f:
                f.write(json.dumps(r) + "\n"); f.flush(); os.fsync(f.fileno())
            rows[(seed, "fly")] = r
            print(f"seed {seed} fly   : thigmo {r['thigmotaxis']:.3f} vs map {r['map_baseline']:.3f}, "
                  f"{r['distinct']:3d}/{r['moves']:4d} new/moves, bumps/tile {r['bumps_per_tile']:.3f}, "
                  f"turns {r['left']}L/{r['right']}R, bout {r['bout_median_ms']}ms", flush=True)
        if (seed, "random") not in rows:
            f_ = rows[(seed, "fly")]
            # Matched to what the fly itself did on this seed, measured the
            # same way, so the control walks as much as the fly does.
            rates = {"walking_frac": f_["walking_frac"],
                     "mean_bout_ms": f_["bout_median_ms"] or 500.0}
            r = run(seed, "random", rates)
            with open(OUT, "a") as f:
                f.write(json.dumps(r) + "\n"); f.flush(); os.fsync(f.fileno())
            rows[(seed, "random")] = r
            print(f"seed {seed} random: thigmo {r['thigmotaxis']:.3f} vs map {r['map_baseline']:.3f}, "
                  f"{r['distinct']:3d}/{r['moves']:4d} new/moves, bumps/tile {r['bumps_per_tile']:.3f}",
                  flush=True)


if __name__ == "__main__":
    main()
