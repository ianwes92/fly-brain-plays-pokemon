"""The mirrored-world test. Design, measures and decision rules are fixed in
mirror_world_PREREG.md (written before any data). This file only runs it.

Each run is ethogram_now.run() -- the same start state, calibration, 240 s of
brain time, no chat, no rewards -- in one of two worlds: normal, or mirrored
(FLYBRAIN_MIRROR=1: lateral senses swapped, chosen turns applied reversed).
Also logged per run, for the secondary measure: the fraction of sampled frames
where the brain's turn signal is below zero, and below its own median.
Resumable: rows already in the .jsonl are skipped.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/flybrain"))
OUT = os.path.join(HERE, "mirror_world.jsonl")
SEEDS = range(12)
ARMS = ("normal", "mirror")


def done():
    if not os.path.exists(OUT):
        return set()
    return {(r["seed"], r["arm"]) for r in map(json.loads, filter(str.strip, open(OUT)))}


def run_one(seed, arm):
    os.environ["FLYBRAIN_MIRROR"] = "1" if arm == "mirror" else ""
    import numpy as np
    from flybrain import game
    import ethogram_now as eth
    eth.SCRATCH = f"/tmp/claude-1000/mirror_{arm}"
    # Sample the turn signal by wrapping Hands.decide for this run only.
    samples = []
    orig = game.Hands.decide

    def decide(self, t_ms):
        k = orig(self, t_ms)
        samples.append(self.turn)
        return k
    game.Hands.decide = decide
    try:
        row = eth.run(seed, "fly")
    finally:
        game.Hands.decide = orig
    a = np.asarray(samples[len(samples) // 10:])          # skip the first 10% (warm-up)
    row.update(arm=arm, mirror=arm == "mirror",
               turn_below_zero=float(np.mean(a < 0)) if a.size else None,
               turn_samples=int(a.size),
               by_source={k: row["stats"].get(k, 0) for k in
                          ("avoid", "wall", "wall_left", "wall_right", "toward_exit",
                           "toward_person", "away_from_exit", "away_from_person")})
    return row


def main():
    have = done()
    for seed in SEEDS:
        for arm in ARMS:
            if (seed, arm) in have:
                continue
            r = run_one(seed, arm)
            with open(OUT, "a") as f:
                f.write(json.dumps(r) + "\n"); f.flush(); os.fsync(f.fileno())
            n = r["left"] + r["right"]
            print(f"seed {seed:2d} {arm:6s}: {r['left']}L/{r['right']}R "
                  f"({100 * r['left'] / max(n, 1):.1f}% left), turn<0 {r['turn_below_zero']:.3f}, "
                  f"bumps/tile {r['bumps_per_tile']:.3f}", flush=True)


if __name__ == "__main__":
    main()
