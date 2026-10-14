"""Give the ring its self-motion input and see whether a heading appears.

The central complex is silent in this simulation: heading decodes out of EPG at
0.294 +/- 0.061 against a shuffled floor of 0.254, chance 0.25 (heading_code.jsonl).
That is expected -- nothing feeds it. In a real fly the EPG bump is rotated by
PEN neurons carrying self-motion, and anchored by visual landmarks through ER.
This game gives no panoramic landmark (the camera never rotates; only the sprite
turns), so the honest input is the one the fly itself generates: an efference
copy of its own turn command, which is what reaches PEN through the noduli.

So: every time the fly turns, one side's PEN gets a brief burst. Nothing else.
Heading is NEVER injected -- that would be decoding what we just wrote in. If
the ring integrates those turn pulses, heading becomes decodable from EPG and
survives while the fly stands still. If the network has no attractor dynamics,
it stays at chance, and goal-directed navigation needs a different foundation.

CONDITIONS (paired by seed)
  pen   PEN gets the turn efference copy
  off   no extra input at all, as today

PRIMARY MEASURE  EPG heading-decoding accuracy on held-out frames (nearest
  class mean, trained on the first half of the run), pen minus off, paired
  t-test across 6 seeds, two-sided, alpha 0.05.
SECONDARY  accuracy while standing still (no visual motion to read), the same
  decode from PEN itself, and the control populations from heading_code.py.

Also reports bump localisation: if a ring attractor forms, EPG activity should
concentrate in a few neurons rather than spreading over all of them, so the
participation ratio of the EPG rate vector is logged per condition.
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
OUT = os.path.join(HERE, "pen_integration.jsonl")
BACKUP = os.path.expanduser("~/flybrain/play/backup-20260917-134047")
SCRATCH = os.environ.get("SCRATCH", "/tmp/claude-1000/pen_integration")

SEEDS = range(6)
CONDITIONS = ("pen", "off")
PLAY_MS = 240_000.0
SAMPLE_EVERY = 100
PULSE_MS = 200.0            # how long a turn's efference copy lasts
PULSE_HZ = 80.0


def decode(X, y, train):
    test = ~train
    if train.sum() < 20 or test.sum() < 20 or len(np.unique(y[train])) < 2:
        return float("nan")
    mu, sd = X[train].mean(0), X[train].std(0) + 1e-9
    Z, Zt = (X[train] - mu) / sd, (X[test] - mu) / sd
    classes = np.unique(y[train])
    centres = np.stack([Z[y[train] == c].mean(0) for c in classes])
    pred = classes[np.argmin(((Zt[:, None, :] - centres[None]) ** 2).sum(2), axis=1)]
    return float((pred == y[test]).mean())


def participation_ratio(R):
    """1 = all activity in one neuron, N = spread evenly over N."""
    s = R.sum(1, keepdims=True)
    p = R / np.where(s > 0, s, 1)
    return float(np.mean(1.0 / np.maximum((p ** 2).sum(1), 1e-9)))


def run(seed, condition):
    os.makedirs(SCRATCH, exist_ok=True)
    rom, live_sav, live_state = game.default_paths()
    calib_sav = os.path.join(SCRATCH, f"calib-{seed}-{condition}.sav")
    run_sav = os.path.join(SCRATCH, f"run-{seed}-{condition}.sav")
    shutil.copyfile(live_sav, calib_sav)
    shutil.copyfile(os.path.join(BACKUP, "newgame.sav"), run_sav)

    cx = Connectome()
    L = cx.labels
    name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"])
    side = np.asarray(L["side"])
    rng = np.random.default_rng(seed)
    epg = np.flatnonzero(np.char.startswith(name, "EPG"))
    pen_all = np.char.startswith(name, "PEN")
    pen_l = np.flatnonzero(pen_all & (side == "left"))
    pen_r = np.flatnonzero(pen_all & (side == "right"))
    optic = np.flatnonzero(np.char.startswith(np.asarray(L["super_class"]), "optic"))
    pops = {"EPG": epg, "PEN": np.flatnonzero(pen_all),
            "random": rng.choice(len(cx.root_id), len(epg), replace=False),
            "visual": rng.choice(optic, len(epg), replace=False)}

    g = game.Game(cx, rom, calib_sav, live_state, seed=seed, learn=False)
    g.calibrate(verbose=False)
    g.load(rom, run_sav, None)
    g.gba.load_state(os.path.join(BACKUP, "newgame.resume.ss"))
    for _ in range(30):
        g.gba.frame(0)

    rates, heads, walking = [], [], []
    last_heading = g.hands.heading
    pulse_until, pulse_idx = -1.0, None
    turns = 0
    end = g.brain.t_ms + PLAY_MS
    while g.brain.t_ms < end:
        if condition == "pen":
            h = g.hands.heading
            if h != last_heading:
                # Which way did it turn? Headings run clockwise, so +1 (mod 4)
                # is a right turn. Side assignment is a convention, like the
                # direction the fly turns away from an obstacle; what matters
                # is that it is consistent.
                step = (h - last_heading) % 4
                if step in (1, 3):
                    pulse_idx = pen_r if step == 1 else pen_l
                    pulse_until = g.brain.t_ms + PULSE_MS
                    turns += 1
                last_heading = h
            if pulse_idx is not None and g.brain.t_ms < pulse_until:
                g.brain.drive(pulse_idx, PULSE_HZ)
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
    half[: len(y) // 2] = True
    yshuf = np.random.default_rng(seed + 99).permutation(y)

    row = {"seed": seed, "condition": condition, "samples": int(len(y)),
           "turns_pulsed": turns, "tiles": g.steps_moved, "maps": len(g.maps),
           "still_frac": float((~w).mean()),
           "epg_participation": participation_ratio(R[:, epg]),
           "epg_n": int(len(epg))}
    for label, idx in pops.items():
        X = R[:, idx]
        row[label] = {"all": decode(X, y, half),
                      "still": decode(X[~w], y[~w], half[~w]),
                      "shuffled": decode(X, yshuf, half)}
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
        r = run(seed, condition)
        with open(OUT, "a") as f:
            f.write(json.dumps(r) + "\n")
            f.flush()
            os.fsync(f.fileno())
        print(f"seed {seed} {condition:3s}: EPG all {r['EPG']['all']:.3f} "
              f"still {r['EPG']['still']:.3f} shuffled {r['EPG']['shuffled']:.3f} | "
              f"PEN {r['PEN']['all']:.3f} | visual {r['visual']['all']:.3f} | "
              f"{r['turns_pulsed']} pulses, spread {r['epg_participation']:.1f}/{r['epg_n']}",
              flush=True)


if __name__ == "__main__":
    main()
