"""Do the game's own rewards change what the fly DOES, or only its mood bar?

The stream rewards the fly for the game going well -- a catch or a level sends
pleasure presses, a wipe sends pain (playview.GAME_REWARDS) -- and the mood bar
visibly moves. That is not the same as behaviour changing, and the claim the
stream makes is the behavioural one. Nothing has ever tested it.

The mechanism makes a directional prediction, so this is a real test rather
than a fishing trip. Mood.apply() sets, from valence:
    turn_scale = 1 + 0.6*joy - 0.6*fear   (bigger swing needed to turn when
                                           content; turns come easily in fear)
    stop_thresh *= 1 - 0.3*joy            (content bouts run longer)
    start_thresh *= 1 - 0.5*fear          (frightened bouts start sooner)

PRIMARY  spontaneous turns per minute over the 90 s after the presses, each
  arm minus the same seed's untouched control, paired across 6 seeds:
      pleasure  predicted BELOW control   (t <= -2.571)
      pain      predicted ABOVE control   (t >= +2.571)
  Spontaneous = turns that neither avoidance nor attraction asked for; those
  two are driven by what is in view, which the reward does not change.
SECONDARY  walking bout median (pleasure longer), tiles moved, wall bumps.
MANIPULATION CHECK  mood valence at the end of the window must actually differ
  in the right direction -- if the press never landed, the arms say nothing.
GUARD  the three arms are the same seed from the same save and are identical
  up to the moment of the press, so `pre` (the stats at that moment) must
  match across arms. Any seed where it does not is dropped, and the row says
  so: it would mean the arms were not comparable in the first place.

The presses are delivered through Game.stimulate, which is exactly the path
GAME_REWARDS uses, and in the same amounts (caught = 2 pleasure, faint = 2 pain).
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
OUT = os.path.join(HERE, "game_rewards.jsonl")
START = os.path.expanduser("~/flybrain/play/ethogram_start")   # a walkable FireRed state
SCRATCH = os.environ.get("SCRATCH", "/tmp/claude-1000/rewards")

SEEDS = range(6)
ARMS = ("none", "pleasure", "pain")
WARM_MS = 60_000.0          # settle, identical in every arm
MEASURE_MS = 90_000.0       # the window the primary is counted over
PRESSES = 2                 # what a catch or a faint actually sends
GAP_MS = 2_000.0            # playview paces queued presses this far apart
CRIT = 2.571                # two-sided 5%, 5 d.f.


def snapshot(g):
    s = dict(g.hands.stats)
    s["tiles"] = g.steps_moved
    s["bumps"] = g.wall_bumps
    return s


def spontaneous(d):
    """Turns the fly made for its own reasons, not because of what it saw."""
    driven = (d.get("avoid", 0) + d.get("toward_exit", 0) + d.get("away_from_exit", 0)
              + d.get("toward_person", 0) + d.get("away_from_person", 0)
              + d.get("wall", 0) + d.get("approach", 0))
    return max(0, d.get("turns", 0) - driven)


def run(seed, arm):
    os.makedirs(SCRATCH, exist_ok=True)
    rom, live_sav, live_state = game.default_paths()
    calib_sav = os.path.join(SCRATCH, f"c-{seed}-{arm}.sav")
    run_sav = os.path.join(SCRATCH, f"r-{seed}-{arm}.sav")
    shutil.copyfile(live_sav, calib_sav)
    shutil.copyfile(START + ".sav", run_sav)

    cx = Connectome()
    P = Params(al_ln_inhibitory=True)
    g = game.Game(cx, rom, calib_sav, live_state, params=P, seed=seed, learn=True)
    g.calibrate(verbose=False)
    g.load(rom.replace("ultraviolet", "firered"), run_sav, None)
    g.gba.load_state(START + ".ss")
    for _ in range(30):
        g.gba.frame(0)
    g.nose = Nose(cx)
    g.mood = Mood(cx, signatures=kc_signatures(cx, params=P, log=lambda *a: None))
    g.auto_rewards = False                  # only this script rewards the fly

    started = time.time()
    end_warm = g.brain.t_ms + WARM_MS
    while g.brain.t_ms < end_warm:
        g.step()
    pre = snapshot(g)
    mood_before = g.mood.valence

    # The reward itself, paced the way the window paces queued presses.
    if arm != "none":
        sent, next_at = 0, g.brain.t_ms
        while sent < PRESSES:
            if g.brain.t_ms >= next_at:
                g.stimulate(arm)
                sent += 1
                next_at = g.brain.t_ms + GAP_MS
            g.step()

    bouts, bout_start, was_walking = [], None, False
    end = g.brain.t_ms + MEASURE_MS
    while g.brain.t_ms < end:
        g.step()
        walking = g.hands.walking
        if walking and not was_walking:
            bout_start = g.brain.t_ms
        elif was_walking and not walking and bout_start is not None:
            bouts.append(g.brain.t_ms - bout_start)
        was_walking = walking
    post = snapshot(g)

    d = {k: post.get(k, 0) - pre.get(k, 0) for k in set(post) | set(pre)}
    minutes = MEASURE_MS / 60_000.0
    row = {"seed": seed, "arm": arm,
           "spont_per_min": spontaneous(d) / minutes,
           "turns_per_min": d.get("turns", 0) / minutes,
           "bout_median_ms": float(np.median(bouts)) if bouts else None,
           "n_bouts": len(bouts),
           "tiles": d.get("tiles", 0), "bumps": d.get("bumps", 0),
           "mood_before": mood_before, "mood_after": g.mood.valence,
           "pre": pre, "delta": d, "wall_s": round(time.time() - started, 1)}
    g.close()
    return row


def main():
    done = {}
    if os.path.exists(OUT):
        for line in open(OUT):
            if line.strip():
                r = json.loads(line)
                done[(r["seed"], r["arm"])] = r
    for seed in SEEDS:
        for arm in ARMS:
            if (seed, arm) in done:
                continue
            r = run(seed, arm)
            with open(OUT, "a") as f:
                f.write(json.dumps(r) + "\n"); f.flush(); os.fsync(f.fileno())
            done[(seed, arm)] = r
            print(f"seed {seed} {arm:8s}: spontaneous {r['spont_per_min']:5.1f}/min, "
                  f"all turns {r['turns_per_min']:5.1f}/min, bout "
                  f"{r['bout_median_ms'] or 0:6.0f}ms, tiles {r['tiles']:3d}, "
                  f"mood {r['mood_before']:+.2f} -> {r['mood_after']:+.2f} "
                  f"[{r['wall_s']:.0f}s]", flush=True)
    score(done)


def score(done):
    seeds = [s for s in SEEDS if all((s, a) in done for a in ARMS)]
    usable = [s for s in seeds if done[(s, "pleasure")]["pre"] == done[(s, "none")]["pre"]
              == done[(s, "pain")]["pre"]]
    print(f"\n{len(seeds)} complete seeds, {len(usable)} with arms identical before the press")
    if len(usable) < 3:
        print("  too few comparable seeds to judge")
        return
    for arm, want in (("pleasure", "-"), ("pain", "+")):
        d = [done[(s, arm)]["spont_per_min"] - done[(s, "none")]["spont_per_min"] for s in usable]
        m, sd = st.mean(d), st.stdev(d)
        t = m / (sd / len(d) ** 0.5) if sd else float("nan")
        hit = (t <= -CRIT) if want == "-" else (t >= CRIT)
        print(f"  PRIMARY {arm:8s} spontaneous turns/min vs control: {m:+6.2f} "
              f"t = {t:+5.2f}  (wanted {want})  {'SHOWN' if hit else 'not shown'}")
        mv = [done[(s, arm)]["mood_after"] - done[(s, "none")]["mood_after"] for s in usable]
        print(f"      manipulation check: mood {st.mean(mv):+.3f} vs control "
              f"({'right way' if (st.mean(mv) > 0) == (arm == 'pleasure') else 'WRONG WAY'})")
        for k in ("bout_median_ms", "tiles", "bumps"):
            vals = [(done[(s, arm)][k] or 0) - (done[(s, "none")][k] or 0) for s in usable]
            print(f"      {k:16s} {st.mean(vals):+8.2f}")


if __name__ == "__main__":
    main()
