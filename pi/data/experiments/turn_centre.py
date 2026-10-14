"""Is the fly's turn signal centred on zero? (Cheap check before a full A/B.)

HYPOTHESIS, from the failure of avoid_side.py: two branches choose a side with
`-1 if self.turn > 0 else 1` -- wall-ahead always has, and the rejected avoid
fix did too. Spontaneous turns fire only at the turn signal's p99.5 / p0.5
extremes, so its CENTRE never mattered to them. A sign test is entirely about
the centre. If the bulk of the signal sits below zero, `turn > 0` mostly says
"right", and wall-ahead has been adding right bias all along.

MEASURED IN PLAY, not calibration: the signal's distribution over every step,
and its value at the exact moments wall-ahead turns fire, with the side each
of those turns took.

KILL CRITERION, fixed now: if the in-play median is within +/-5 Hz of zero AND
wall-ahead turns split within 40-60%, the hypothesis is dead and no A/B is run.
"""
import os
import shutil
import statistics as st
import sys

import numpy as np

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain import game                                    # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402
from flybrain.engine import Params                           # noqa: E402
from flybrain.mood import Mood                               # noqa: E402
from flybrain.nose import Nose, kc_signatures                # noqa: E402

START = os.path.expanduser("~/flybrain/play/ethogram_start")
SCRATCH = "/tmp/claude-1000/turn_centre"
PLAY_MS = 180_000.0


def run(seed):
    os.makedirs(SCRATCH, exist_ok=True)
    rom, live_sav, live_state = game.default_paths()
    cal = os.path.join(SCRATCH, f"c-{seed}.sav")
    run_sav = os.path.join(SCRATCH, f"r-{seed}.sav")
    shutil.copyfile(live_sav, cal)
    shutil.copyfile(START + ".sav", run_sav)
    cx = Connectome()
    P = Params(al_ln_inhibitory=True)
    g = game.Game(cx, rom, cal, live_state, params=P, seed=seed, learn=True)
    g.calibrate(verbose=False)
    g.load(rom.replace("ultraviolet", "firered"), run_sav, None)
    g.gba.load_state(START + ".ss")
    for _ in range(30):
        g.gba.frame(0)
    g.nose = Nose(cx)
    g.mood = Mood(cx, signatures=kc_signatures(cx, params=P, log=lambda *a: None))
    g.auto_rewards = False
    h = g.hands
    every, at_wall, wall_sides = [], [], []
    last_wall = h.stats.get("wall", 0)
    end = g.brain.t_ms + PLAY_MS
    while g.brain.t_ms < end:
        before = h.heading
        turn_now = h.turn
        g.step()
        if g.brain.steps % 10 == 0:
            every.append(h.turn)
        w = h.stats.get("wall", 0)
        if w > last_wall:
            last_wall = w
            at_wall.append(turn_now)
            wall_sides.append("left" if (h.heading - before) % 4 == 3 else "right")
    row = dict(seed=seed, n=len(every),
               median=float(np.median(every)), mean=float(np.mean(every)),
               frac_pos=float(np.mean(np.array(every) > 0)),
               p05=float(np.percentile(every, 5)), p95=float(np.percentile(every, 95)),
               turn_left=float(h.turn_left * h.turn_scale),
               turn_right=float(h.turn_right * h.turn_scale),
               wall_n=len(at_wall),
               wall_median=float(np.median(at_wall)) if at_wall else float("nan"),
               wall_left=wall_sides.count("left"), wall_right=wall_sides.count("right"))
    g.close()
    return row


def main():
    rows = []
    for seed in (int(a) for a in sys.argv[1:]) if len(sys.argv) > 1 else (0, 1, 2):
        r = run(seed)
        rows.append(r)
        wl, wr = r["wall_left"], r["wall_right"]
        print(f"seed {r['seed']}: in-play median {r['median']:+7.1f} Hz  mean {r['mean']:+7.1f}  "
              f"above zero {r['frac_pos']:.1%}  (p5 {r['p05']:+.0f}, p95 {r['p95']:+.0f})  | "
              f"wall-ahead turns {wl}L/{wr}R, signal there {r['wall_median']:+.1f} Hz", flush=True)
    med = st.mean(r["median"] for r in rows)
    L = sum(r["wall_left"] for r in rows)
    R = sum(r["wall_right"] for r in rows)
    share = L / max(L + R, 1)
    print(f"\nmean in-play median {med:+.1f} Hz; wall-ahead turns {L}L/{R}R = {share:.1%} left")
    dead = abs(med) <= 5.0 and 0.40 <= share <= 0.60
    print("VERDICT:", "HYPOTHESIS DEAD -- signal is centred and wall turns are even; no A/B"
          if dead else "HYPOTHESIS ALIVE -- worth a pre-registered A/B on a median split")


if __name__ == "__main__":
    main()
