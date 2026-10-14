"""Is the fly reading people and doors correctly, right now?

The window captions what it decides ("someone on the left - going over"), and
those captions are only worth showing if the readout they come from tracks
what is really on screen. This compares, frame by frame:

  TRUTH      Eyes.seen_people / seen_exits -- built from the game's own tile
             map, (left, right) in the fly's own frame of reference.
  READOUT    Hands.people / Hands.exits -- the descending-neuron signals the
             fly actually steers by, after crosstalk unmixing and centring.

REPORTED
  - how often anything is in view at all (if the room is empty, nothing here
    means much, and the caption should be saying "wandering");
  - correlation of readout with truth, and with the SIDE of truth;
  - precision of the claim the viewer reads: when the fly says "someone on
    the left", how often is there really a person, and really on the left;
  - the same for doors;
  - and how often a thing IS in view and the fly says nothing (misses).
"""
import json
import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain import game as gm                              # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402
from flybrain.engine import Params                           # noqa: E402
from flybrain.mood import Mood                               # noqa: E402
from flybrain.nose import Nose, kc_signatures                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "thing_reading.jsonl")
PLAY_MS = float(os.environ.get("PLAY_MS", 120_000))


def main():
    rom, sav, state = gm.default_paths()
    tmp = tempfile.mkdtemp()
    work = os.path.join(tmp, "t.sav")
    shutil.copyfile(sav, work)
    resume = gm.resume_path_for(sav)
    cx = Connectome()
    P = Params(al_ln_inhibitory=True)
    g = gm.Game(cx, rom, work, state, params=P, seed=0, learn=True)
    g.calibrate(verbose=False)
    if os.path.exists(resume):                 # start where the fly actually is
        g.gba.load_state(resume)
        for _ in range(30):
            g.gba.frame(0)
    g.nose = Nose(cx)
    g.mood = Mood(cx, signatures=kc_signatures(cx, params=P, log=lambda *a: None))
    g.auto_rewards = False

    rows = []
    h, e = g.hands, g.eyes
    seen_counts = {"toward_person": 0, "away_from_person": 0,
                   "toward_exit": 0, "away_from_exit": 0}
    frames = 0
    end = g.brain.t_ms + PLAY_MS
    last_frame = -1
    while g.brain.t_ms < end:
        g.step()
        if g._team_frames == last_frame:
            continue
        last_frame = g._team_frames
        frames += 1
        fired = {}
        for k in seen_counts:
            n = h.stats.get(k, 0)
            fired[k] = n > seen_counts[k]
            seen_counts[k] = n
        rows.append({
            "p_true_l": float(e.seen_people[0]), "p_true_r": float(e.seen_people[1]),
            "x_true_l": float(e.seen_exits[0]), "x_true_r": float(e.seen_exits[1]),
            "p_read": float(h.people), "x_read": float(h.exits),
            "p_thresh": float(h.people_thresh), "x_thresh": float(h.exit_thresh),
            **{k: bool(v) for k, v in fired.items()},
        })
    g.close()

    with open(OUT, "w") as f:
        for r in rows:
            f.write(json.dumps(r) + "\n")

    a = {k: np.array([r[k] for r in rows]) for k in rows[0]}
    print(f"{frames} game frames\n")
    for kind, tl, tr, read, th, tow, awy in (
            ("PEOPLE", "p_true_l", "p_true_r", "p_read", "p_thresh",
             "toward_person", "away_from_person"),
            ("DOORS", "x_true_l", "x_true_r", "x_read", "x_thresh",
             "toward_exit", "away_from_exit")):
        L, R, sig = a[tl], a[tr], a[read]
        present = (L + R) > 0
        side = L - R                       # + = on the left, as the readout reads
        print(f"{kind}")
        print(f"  in view at all:        {100*present.mean():5.1f}% of frames")
        if present.sum() > 10:
            print(f"  readout vs truth:      r = {np.corrcoef(sig[present], side[present])[0,1]:+.3f} "
                  f"(when something is in view)")
            print(f"  readout vs truth:      r = {np.corrcoef(sig, side)[0,1]:+.3f} (all frames)")
            print(f"  signal when in view:   {sig[present].mean():+8.1f} Hz   "
                  f"when empty: {sig[~present].mean():+8.1f} Hz   "
                  f"threshold {a[th][0]:.0f}")
        claims = a[tow] | a[awy]
        n = int(claims.sum())
        print(f"  the fly announced one: {n} times")
        if n:
            real = present[claims]
            right_side = (np.sign(sig[claims]) == np.sign(side[claims])) & real
            print(f"    ...and one was really in view: {100*real.mean():5.1f}%")
            print(f"    ...and on the side it said:    {100*right_side.mean():5.1f}%")
        if present.sum():
            missed = present & ~claims
            print(f"  in view but nothing said: {100*missed.sum()/present.sum():5.1f}% of frames with one\n")
        else:
            print()


if __name__ == "__main__":
    main()
