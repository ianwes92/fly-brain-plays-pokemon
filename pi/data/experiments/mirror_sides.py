"""Do the DNp35/DNp18 mirror pairs tell left from right IN PLAY? (Cheap check.)

CONTEXT: the readout the fly uses to NOTICE obstacles (WALL_READOUT, ten
descending neurons) cannot say which side they are on -- a truly-right obstacle
reads +44 Hz, not negative (obstacle_sides.py). Today's fix side-steps that with
an even-handed coin: the turn signal split at its running median. That cures
the circling, but the fly still turns TOWARD the obstacle half the time.

The idea to test later: keep WALL_READOUT for noticing (it is strong and fast;
the one previous attempt to replace it wholesale nearly doubled bumps because a
weaker signal crosses the threshold later) and take only the SIDE from the
DNp35/DNp18 mirror pairs, which the code says track one-sided LPLC1 input:
an obstacle on the left raises left DNp35 and right DNp18, lowers their
partners. These are descending neurons, downstream of the connectome -- not
LPLC1 itself, which would just be reading back the input we inject.

MEASURED HERE, in play, against the tile map's own left/right asymmetry:
    side = (L DNp35 + R DNp18) - (R DNp35 + L DNp18)        + = left

KILL CRITERION, fixed now: worth an A/B only if, on every seed, the median
side reading for truly-LEFT obstacles is above that for truly-RIGHT ones AND the
truly-RIGHT median is negative after centring -- i.e. it gets the SIGN right,
which is the exact thing the current readout fails at.
"""
import os
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain import game                                    # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402
from flybrain.engine import Params                           # noqa: E402
from flybrain.mood import Mood                               # noqa: E402
from flybrain.nose import Nose, kc_signatures                # noqa: E402

START = os.path.expanduser("~/flybrain/play/ethogram_start")
SCRATCH = "/tmp/claude-1000/mirror_sides"
PLAY_MS = 180_000.0


def pairs(cx):
    name = np.where(cx.labels["cell_type"] != "", cx.labels["cell_type"],
                    cx.labels["hemibrain_type"])
    side = cx.labels["side"]
    desc = cx.labels["super_class"] == "descending"
    pick = lambda t, s_: np.flatnonzero(desc & (name == t) & (side == s_))
    up = np.concatenate([pick("DNp35", "left"), pick("DNp18", "right")])
    down = np.concatenate([pick("DNp35", "right"), pick("DNp18", "left")])
    return up, down


def run(seed):
    os.makedirs(SCRATCH, exist_ok=True)
    rom, live_sav, live_state = game.default_paths()
    cal = os.path.join(SCRATCH, f"c-{seed}.sav")
    run_sav = os.path.join(SCRATCH, f"r-{seed}.sav")
    shutil.copyfile(live_sav, cal)
    shutil.copyfile(START + ".sav", run_sav)
    cx = Connectome()
    up, down = pairs(cx)
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
    sig, asym, presence = [], [], []
    end = g.brain.t_ms + PLAY_MS
    while g.brain.t_ms < end:
        g.step()
        if g.brain.steps % 10:
            continue
        r = g.brain.rate
        sig.append(float(r[up].sum() - r[down].sum()))
        a = g.eyes.seen
        asym.append(float(a[0] - a[1]))
        presence.append(float(g.hands.obstacle))
    g.close()
    sig, asym = np.array(sig), np.array(asym)
    even = np.abs(asym) < 0.05
    centre = float(np.median(sig[even])) if even.sum() >= 5 else 0.0
    s = sig - centre
    L, R = s[asym >= 0.1], s[asym <= -0.1]
    return dict(seed=seed, n_up=len(up), n_down=len(down), n_left=len(L), n_right=len(R),
                left_med=float(np.median(L)) if len(L) else float("nan"),
                right_med=float(np.median(R)) if len(R) else float("nan"),
                corr=float(np.corrcoef(s[np.abs(asym) >= 0.1], asym[np.abs(asym) >= 0.1])[0, 1])
                if (np.abs(asym) >= 0.1).sum() > 5 else float("nan"))


def main():
    rows = []
    seeds = [int(a) for a in sys.argv[1:]] or [0, 1, 2]
    for seed in seeds:
        r = run(seed)
        rows.append(r)
        print(f"seed {r['seed']}: pairs {r['n_up']}+{r['n_down']} neurons | truly-LEFT n={r['n_left']:4d} "
              f"median {r['left_med']:+7.2f} Hz | truly-RIGHT n={r['n_right']:4d} median "
              f"{r['right_med']:+7.2f} Hz | corr {r['corr']:+.2f}", flush=True)
    ok = all(r["left_med"] > r["right_med"] and r["right_med"] < 0 for r in rows
             if not np.isnan(r["left_med"]) and not np.isnan(r["right_med"]))
    print("\nVERDICT:", "GETS THE SIGN RIGHT -- worth a pre-registered A/B"
          if ok else "DOES NOT get the sign right on every seed -- no A/B")


if __name__ == "__main__":
    main()
