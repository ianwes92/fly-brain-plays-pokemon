"""Does the obstacle readout answer a left obstacle and a right one equally?

The fly turns right far more than left (ethogram_now: 37.7% left over 1,423
turns; turn_balance: avoidance turns 0L/35R, 3L/63R). Spontaneous turns are
even, because they already have a threshold PER SIDE. Avoidance turns use ONE
symmetric threshold, set from |readout| pooled over left- and right-lopsided
views together. If the readout's response to a real left obstacle is much
larger than to a real right one, that single threshold is easy to cross on the
left and hard on the right -- and every avoidance turn goes right.

This measures the per-side response against ground truth (the tile map's own
left-minus-right obstacle asymmetry), on the calibration samples, for several
seeds. It changes nothing.

Prediction if the hypothesis is right: median readout when the obstacle is
truly on the LEFT is well above the median magnitude when it is truly on the
RIGHT, and the pooled threshold sits between them.
"""
import os
import statistics as st
import sys

import numpy as np

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain import game                                    # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402

SEEDS = (0, 1, 2)


def main():
    cx = Connectome()
    rows = []
    for seed in SEEDS:
        g = game.Game(cx, *game.default_paths(), seed=seed)
        g.calibrate(verbose=False)
        ob, asym = g._cal_ob, g._cal_asym
        even = np.abs(asym) < 0.05
        centre = float(np.median(ob[even])) if even.sum() >= 5 else 0.0
        ob = ob - centre
        left, right = asym >= 0.1, asym <= -0.1
        L = ob[left]
        R = ob[right]
        noise = np.abs(ob[even])
        row = dict(seed=seed, n_left=int(left.sum()), n_right=int(right.sum()),
                   n_even=int(even.sum()),
                   left_med=float(np.median(L)) if len(L) else float("nan"),
                   right_med=float(np.median(R)) if len(R) else float("nan"),
                   noise95=float(np.percentile(noise, 95)) if len(noise) else float("nan"),
                   thresh=float(g.hands.obstacle_thresh))
        rows.append(row)
        print(f"seed {seed}: truly-LEFT n={row['n_left']:3d} median readout {row['left_med']:+7.1f} Hz | "
              f"truly-RIGHT n={row['n_right']:3d} median {row['right_med']:+7.1f} Hz | "
              f"noise p95 {row['noise95']:6.1f} | pooled threshold +/-{row['thresh']:.1f}", flush=True)
        g.close()

    print()
    lm = [r["left_med"] for r in rows if not np.isnan(r["left_med"])]
    rm = [abs(r["right_med"]) for r in rows if not np.isnan(r["right_med"])]
    th = [r["thresh"] for r in rows]
    print(f"mean response to a LEFT obstacle : {st.mean(lm):+.1f} Hz")
    print(f"mean response to a RIGHT obstacle: {st.mean(rm):.1f} Hz (magnitude)")
    print(f"mean pooled threshold            : {st.mean(th):.1f} Hz")
    print(f"left/right response ratio        : {st.mean(lm) / max(st.mean(rm), 1e-9):.2f}")
    print(f"sign check: right obstacles read NEGATIVE as they should? "
          f"{all(r['right_med'] < 0 for r in rows if not np.isnan(r['right_med']))}")


if __name__ == "__main__":
    main()
