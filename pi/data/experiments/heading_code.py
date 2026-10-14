"""Does the fly's central complex carry its heading?

Goal-directed navigation needs a heading signal that persists. The ring
attractor's wedge identities could not be recovered from FlyWire connectivity
(spectral embedding radius sd/mean 0.35-0.38, similarity-vs-angle correlation
only -0.30/-0.35 thresholded and unthresholded alike), so this does not
assume them. It asks the empirical question instead: while the fly plays, can
its heading be read out of EPG population activity?

DECODER  nearest class mean on z-scored rates, trained on the first half of a
run and tested on the second. Chance is 25% (four headings), and the same
decoder is run on shuffled labels to show the floor.

CONTROLS, because heading correlates with what is on screen -- facing a wall
looks different from facing a corridor, so almost any visual neuron can
predict heading without holding a heading:
  random     51 random neurons, the same population size as EPG
  visual     51 random neurons from the optic lobes
  descending 51 random descending neurons
EPG is only interesting if it beats these, and especially if it beats them
WHILE STANDING STILL, where there is no visual motion to read.

Reported per population: accuracy over all frames, accuracy over frames where
the fly is not walking, and the shuffled floor.
"""
import json
import os
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain import game                                    # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "heading_code.jsonl")
BACKUP = os.path.expanduser("~/flybrain/play/backup-20260917-134047")
SCRATCH = os.environ.get("SCRATCH", "/tmp/claude-1000/heading_code")
SEEDS = (0, 1, 2, 3)
PLAY_MS = 240_000.0
SAMPLE_EVERY = 100          # brain steps, so ~10 samples a second


def decode(X, y, train):
    """Nearest class mean, fitted on `train` and scored on the rest."""
    test = ~train
    if train.sum() < 20 or test.sum() < 20 or len(np.unique(y[train])) < 2:
        return float("nan")
    mu, sd = X[train].mean(0), X[train].std(0) + 1e-9
    Z, Zt = (X[train] - mu) / sd, (X[test] - mu) / sd
    classes = np.unique(y[train])
    centres = np.stack([Z[y[train] == c].mean(0) for c in classes])
    pred = classes[np.argmin(((Zt[:, None, :] - centres[None]) ** 2).sum(2), axis=1)]
    return float((pred == y[test]).mean())


def run(seed):
    os.makedirs(SCRATCH, exist_ok=True)
    rom, live_sav, live_state = game.default_paths()
    calib_sav = os.path.join(SCRATCH, f"calib-{seed}.sav")
    run_sav = os.path.join(SCRATCH, f"run-{seed}.sav")
    shutil.copyfile(live_sav, calib_sav)
    shutil.copyfile(os.path.join(BACKUP, "newgame.sav"), run_sav)

    cx = Connectome()
    L = cx.labels
    name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"])
    rng = np.random.default_rng(seed)
    epg = np.flatnonzero(np.char.startswith(name, "EPG"))
    pen = np.flatnonzero(np.char.startswith(name, "PEN"))
    n_cx = len(epg)
    optic = np.flatnonzero(np.char.startswith(np.asarray(L["super_class"]), "optic"))
    desc = np.flatnonzero(np.asarray(L["super_class"]) == "descending")
    pops = {
        "EPG": epg,
        "PEN": pen,
        "random": rng.choice(len(cx.root_id), n_cx, replace=False),
        "visual": rng.choice(optic, n_cx, replace=False),
        "descending": rng.choice(desc, n_cx, replace=False),
    }

    g = game.Game(cx, rom, calib_sav, live_state, seed=seed, learn=False)
    g.calibrate(verbose=False)
    g.load(rom, run_sav, None)
    g.gba.load_state(os.path.join(BACKUP, "newgame.resume.ss"))
    for _ in range(30):
        g.gba.frame(0)

    rates, heads, walking = [], [], []
    end = g.brain.t_ms + PLAY_MS
    while g.brain.t_ms < end:
        g.step()
        if g.brain.steps % SAMPLE_EVERY:
            continue
        rates.append(g.brain.rate.copy())
        heads.append(g.hands.heading)
        walking.append(g.hands.walking)
    R = np.array(rates, dtype=np.float32)
    y = np.array(heads)
    w = np.array(walking, dtype=bool)
    half = np.zeros(len(y), bool)
    half[: len(y) // 2] = True          # train on the first half, test on the second

    result = {"seed": seed, "samples": int(len(y)),
              "heading_counts": {int(c): int((y == c).sum()) for c in np.unique(y)},
              "still_frames": int((~w).sum()), "tiles": g.steps_moved, "maps": len(g.maps)}
    yshuf = np.random.default_rng(seed + 99).permutation(y)
    for label, idx in pops.items():
        X = R[:, idx]
        result[label] = {
            "all": decode(X, y, half),
            "still": decode(X[~w], y[~w], half[~w]),
            "shuffled": decode(X, yshuf, half),
        }
    g.close()
    return result


def main():
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            if line.strip():
                done.add(json.loads(line)["seed"])
    for seed in [s for s in SEEDS if s not in done]:
        r = run(seed)
        with open(OUT, "a") as f:
            f.write(json.dumps(r) + "\n")
            f.flush()
            os.fsync(f.fileno())
        print(f"seed {seed}: {r['samples']} samples, {r['still_frames']} standing still, "
              f"headings {r['heading_counts']}", flush=True)
        for k in ("EPG", "PEN", "random", "visual", "descending"):
            v = r[k]
            print(f"   {k:11s} all {v['all']:.3f}   still {v['still']:.3f}   "
                  f"shuffled {v['shuffled']:.3f}", flush=True)


if __name__ == "__main__":
    main()
