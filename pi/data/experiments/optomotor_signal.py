"""Is there anything for an optomotor loop to correct in THIS game?

The reflex nulls image slip: in a real fly, walking drifts and vision pulls it
straight. Pokemon is a grid -- pressing up moves exactly up -- so the fly may
never slip at all, and a loop with nothing to correct is worth nothing. This
measures, in play: how much lateral image motion there actually is, whether it
lines up with the fly's own idea of its heading, and what the HS cells (the
optomotor output, +389 Hz to a driven slip) do while the game is running.
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

PLAY_MS = float(os.environ.get("PLAY_MS", 120_000))


def main():
    rom, sav, state = gm.default_paths()
    tmp = tempfile.mkdtemp(); work = os.path.join(tmp, "t.sav")
    shutil.copyfile(sav, work)
    cx = Connectome(); P = Params(al_ln_inhibitory=True)
    g = gm.Game(cx, rom, work, state, params=P, seed=0, learn=True)
    g.calibrate(verbose=False)
    resume = gm.resume_path_for(sav)
    if os.path.exists(resume):
        g.gba.load_state(resume)
        for _ in range(30):
            g.gba.frame(0)
    g.nose = Nose(cx)
    g.mood = Mood(cx, signatures=kc_signatures(cx, params=P, log=lambda *a: None))
    g.auto_rewards = False

    name = np.where(cx.labels["cell_type"] != "", cx.labels["cell_type"],
                    cx.labels["hemibrain_type"]).astype(str)
    side = np.asarray(cx.labels["side"]).astype(str)
    hsL = np.flatnonzero(np.char.startswith(name, "HS") & (side == "left"))
    hsR = np.flatnonzero(np.char.startswith(name, "HS") & (side == "right"))

    rows = []
    prev_tile = [None]
    HEAD_DX = [hx for _, hx, _, _ in gm.HEADINGS]
    HEAD_DY = [hy for _, _, hy, _ in gm.HEADINGS]
    e, h, b = g.eyes, g.hands, g.brain
    last = -1
    end = b.t_ms + PLAY_MS
    while b.t_ms < end:
        g.step()
        if g._team_frames == last:
            continue
        last = g._team_frames
        moved = g._last_tile is not None and g._last_tile != prev_tile[0]
        prev_tile[0] = g._last_tile
        rows.append((float(e.lat), float(e.fwd), float(e.vx), float(e.vy),
                     float(b.rate[hsL].mean()), float(b.rate[hsR].mean()),
                     int(h.heading), bool(h.walking), bool(moved),
                     float(HEAD_DX[h.heading]), float(HEAD_DY[h.heading])))
    g.close()

    a = np.array([r[:6] for r in rows])
    walking = np.array([r[7] for r in rows])
    moved = np.array([r[8] for r in rows])
    hdx = np.array([r[9] for r in rows]); hdy = np.array([r[10] for r in rows])
    lat, fwd, vx, vy, HL, HR = a.T
    print(f"{len(rows)} game frames, walking on {100*walking.mean():.0f}% of them\n")
    print("LATERAL IMAGE SLIP -- what the reflex would correct")
    print(f"  |lat| median {np.median(np.abs(lat)):.3f} px/frame, "
          f"p90 {np.percentile(np.abs(lat), 90):.2f}, max {np.abs(lat).max():.1f}")
    print(f"  frames with any slip at all (|lat| >= 1): {100*(np.abs(lat) >= 1).mean():.1f}%")
    print(f"  while walking:                            {100*(np.abs(lat[walking]) >= 1).mean():.1f}%")
    print(f"\nFORWARD FLOW, for comparison (the fly does move)")
    print(f"  |fwd| median {np.median(np.abs(fwd)):.3f}, p90 {np.percentile(np.abs(fwd), 90):.2f}")
    print(f"\nHS CELLS IN PLAY (they gave +389 Hz to a driven slip)")
    print(f"  left  mean {HL.mean():7.2f} Hz   right mean {HR.mean():7.2f} Hz")
    print(f"  left-right difference: median {np.median(HL-HR):+.2f}, "
          f"p5 {np.percentile(HL-HR,5):+.2f}, p95 {np.percentile(HL-HR,95):+.2f}")
    strong = np.abs(lat) >= 1
    if strong.sum() > 10:
        print(f"  when the image really slips, difference {np.median((HL-HR)[strong]):+.2f} "
              f"vs {np.median((HL-HR)[~strong]):+.2f} otherwise")
    slip = np.abs(lat) >= 1
    print(f"\nIS THE SLIP THE FLY MOVING, OR THE SCENE MOVING?")
    print(f"  slip while the fly changed tile:     {100*(slip & moved).sum()/max(slip.sum(),1):5.1f}% of slip frames")
    print(f"  slip while the fly did NOT move:     {100*(slip & ~moved).sum()/max(slip.sum(),1):5.1f}%")
    print(f"  (slip with no movement is the world moving -- an NPC, an animation,")
    print(f"   a transition -- and a reflex that corrects for it steers at ghosts)")
    # does the observed motion agree with the heading the fly believes it has?
    mv = moved & (np.abs(vx) + np.abs(vy) > 0)
    if mv.sum() > 20:
        agree = (np.sign(vx[mv]) == np.sign(hdx[mv])) & (np.sign(vy[mv]) == np.sign(hdy[mv]))
        print(f"\nHEADING MODEL, while actually moving ({mv.sum()} frames)")
        print(f"  motion direction agrees with the heading the fly believes: {100*agree.mean():.1f}%")
    np.save(os.path.join(os.path.dirname(os.path.abspath(__file__)), "optomotor_signal.npy"), a)


if __name__ == "__main__":
    main()
