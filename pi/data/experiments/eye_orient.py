"""Which way round is the recovered eye map -- and does an image get through?

The layout comes out of an eigenvector, so its rotation and handedness are
arbitrary. But the fly already has readouts calibrated as left versus right
(WALL_READOUT: + means an obstacle on the fly's left, validated on fresh
seeds). So: light up one half of the map, then the other, and see which way
the readout moves. The rotation that separates the two halves most strongly IS
the horizontal axis of the visual field, and the sign says which half is left.

This also answers the prior question, which matters more: does a retinotopic
drive REACH the readouts at all? The pathway is retina -> lamina -> medulla ->
lobula -> LPLC1 -> descending neurons, five stages, and nothing in this model
has ever been driven from the photoreceptor end before. If the answer is that
nothing moves, real vision is not worth building on this connectome and it is
better to learn that here than after days of work.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain.connectome import Connectome                   # noqa: E402
from flybrain.engine import Brain, Params                    # noqa: E402
from flybrain.game import WALL_READOUT, PEOPLE_READOUT, EXITS_READOUT, _by_root  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
MAP = os.path.join(HERE, "eye_map.npz")
HZ = float(os.environ.get("HZ", 150.0))
MS = int(os.environ.get("MS", 500))
SEEDS = (0, 1, 2)


def main():
    if not os.path.exists(MAP):
        raise SystemExit("run eye_map.py first")
    m = np.load(MAP)
    cx = Connectome()
    pr_index, pr_col = m["pr_index"], m["pr_column"]
    cols = np.concatenate([m["left_cols"], m["right_cols"]])
    xy = np.concatenate([m["left_xy"], m["right_xy"]])
    print(f"{len(pr_index)} photoreceptors placed across {len(set(pr_col.tolist()))} columns")

    idx_w, sign_w = _by_root(cx, WALL_READOUT)
    idx_p, sign_p = _by_root(cx, PEOPLE_READOUT)
    idx_x, sign_x = _by_root(cx, EXITS_READOUT)

    def readouts(b):
        return (float((b.rate[idx_w] * sign_w).sum()),
                float((b.rate[idx_p] * sign_p).sum()),
                float((b.rate[idx_x] * sign_x).sum()))

    def drive_half(angle, positive):
        """Photoreceptors in the half-plane on one side of a line at `angle`."""
        u = xy[:, 0] * np.cos(angle) + xy[:, 1] * np.sin(angle)
        want = np.flatnonzero(u > 0 if positive else u < 0)
        keep = np.isin(pr_col, want)
        return pr_index[keep]

    print(f"\n{'rotation':>9} {'lit half':>9} {'wall':>9} {'people':>9} {'doors':>9}  spikes")
    best = (0.0, None)
    for deg in (0, 30, 60, 90, 120, 150):
        ang = np.radians(deg)
        res = {}
        for positive in (True, False):
            pop = drive_half(ang, positive)
            if len(pop) < 50:
                res[positive] = (float("nan"),) * 3
                continue
            got = []
            for s in SEEDS:
                b = Brain(cx, Params(al_ln_inhibitory=True), seed=s)
                for _ in range(250):
                    b.step()
                base = readouts(b)
                spikes = 0
                for _ in range(MS):
                    b.drive(pop, HZ)
                    spikes += len(b.step())
                now = readouts(b)
                got.append([now[k] - base[k] for k in range(3)])
            res[positive] = tuple(np.mean(got, axis=0))
            print(f"{deg:8d}d {'+' if positive else '-':>9} "
                  f"{res[positive][0]:+9.1f} {res[positive][1]:+9.1f} "
                  f"{res[positive][2]:+9.1f}  {spikes/MS:5.1f}/step  ({len(pop)} lit)")
        sep = abs(res[True][0] - res[False][0])
        if not np.isnan(sep) and sep > best[0]:
            best = (sep, deg, res[True][0], res[False][0])
    print()
    if best[1] is None:
        print("nothing separated the halves: an image does not reach the readouts")
    else:
        sep, deg, plus, minus = best
        print(f"strongest separation at {deg} degrees: "
              f"one half {plus:+.1f} Hz, the other {minus:+.1f} Hz, gap {sep:.1f}")
        print(f"  for comparison, driving LPLC1 directly gives about 1200 Hz")
        if sep < 50:
            print("  -- which is too small to steer by. An image does not carry.")
        else:
            side = "positive" if plus > minus else "negative"
            print(f"  the fly's LEFT is the {side} side of that axis "
                  f"(+ on the wall readout means an obstacle on the left)")


if __name__ == "__main__":
    main()
