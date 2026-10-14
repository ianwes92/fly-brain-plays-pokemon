"""Does a busy screen make the fly slow?

The retina is driven by how much the picture changed: rate = 10 + 40*bright +
900*change, clipped at 150 Hz. A room full of animated sprites should therefore
cost far more than a quiet one -- more afferent spikes, more synaptic scatter,
a slower step. If that is what the stream's speed drops are, the fix is in the
drive, not in the threading.
"""
import os
import shutil
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain import game as gm                              # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402
from flybrain.engine import Params                           # noqa: E402
from flybrain.mood import Mood                               # noqa: E402
from flybrain.nose import Nose, kc_signatures                # noqa: E402

PLAY_MS = float(os.environ.get("PLAY_MS", 90_000))


def main():
    rom, sav, state = gm.default_paths()
    tmp = tempfile.mkdtemp(); work = os.path.join(tmp, "t.sav")
    shutil.copyfile(sav, work)
    cx = Connectome(); P = Params(al_ln_inhibitory=True)
    g = gm.Game(cx, rom, work, state, params=P, seed=0, learn=True)
    g.calibrate(verbose=False)
    resume = gm.resume_path_for(sav)
    if os.path.exists(resume):                  # where the fly actually is
        g.gba.load_state(resume)
        for _ in range(30):
            g.gba.frame(0)
    g.nose = Nose(cx)
    g.mood = Mood(cx, signatures=kc_signatures(cx, params=P, log=lambda *a: None))
    g.auto_rewards = False

    rows = []
    e, b = g.eyes, g.brain
    last = -1
    end = b.t_ms + PLAY_MS
    t_frame = time.perf_counter()
    spikes = 0
    while b.t_ms < end:
        spikes += len(g.step())
        if g._team_frames == last:
            continue
        last = g._team_frames
        now = time.perf_counter()
        rows.append((float(e.change), float(e.rates.get("eye", 0.0)),
                     spikes, (now - t_frame) * 1000.0))
        t_frame, spikes = now, 0
    g.close()

    a = np.array(rows)
    change, eye_hz, spk, ms = a.T
    a = a[1:]                                    # drop the first, it includes setup
    change, eye_hz, spk, ms = a.T
    print(f"{len(a)} game frames from where the fly is now\n")
    print(f"  picture change  median {np.median(change):.4f}  p90 {np.percentile(change,90):.4f}")
    print(f"  retina drive    median {np.median(eye_hz):6.1f} Hz  p90 {np.percentile(eye_hz,90):6.1f} Hz "
          f"(clipped at 150)   at the clip on {100*(eye_hz>=149.9).mean():.0f}% of frames")
    print(f"  spikes/frame    median {np.median(spk):6.0f}      p90 {np.percentile(spk,90):6.0f}")
    print(f"  wall ms/frame   median {np.median(ms):6.1f}      p90 {np.percentile(ms,90):6.1f}")
    q = np.quantile(eye_hz, [0, 0.25, 0.5, 0.75, 1.0])
    print(f"\n  cost against how hard the retina is driven:")
    for lo, hi in zip(q[:-1], q[1:]):
        m = (eye_hz >= lo) & (eye_hz <= hi)
        if m.sum() > 5:
            print(f"    retina {lo:6.1f}-{hi:6.1f} Hz: {np.median(spk[m]):6.0f} spikes, "
                  f"{np.median(ms[m]):6.1f} ms per frame  ({m.sum()} frames)")
    r = np.corrcoef(eye_hz, ms)[0, 1]
    print(f"\n  correlation between retina drive and frame cost: r = {r:+.2f}")
    print(f"  correlation between spikes and frame cost:       r = {np.corrcoef(spk, ms)[0,1]:+.2f}")


if __name__ == "__main__":
    main()
