"""The lateral readouts must stay centred, or the fly can only turn one way.

In play the obstacle readout sat at +137 Hz with the world dead symmetric and
crossed its negative threshold on 0.0% of 5294 frames: every avoidance turn
went right, because a left one was arithmetically unreachable. read() now
subtracts a running median. These check that it does, and that the fix does
not eat a real one-sided signal.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.connectome import Connectome     # noqa: E402
from flybrain.game import Hands                # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


class StubBrain:
    """Just the rate vector the readouts index into."""

    def __init__(self, n):
        self.rate = np.zeros(n, dtype=np.float32)


cx = Connectome()
n = len(cx.root_id)

print("1. a constant offset is centred out")
h = Hands(cx)
h.unmix = False                      # isolate one readout from the others
b = StubBrain(n)
b.rate[h.wall_idx[0]] = 200.0 / h.wall_sign[0]      # a steady lopsided reading
for _ in range(400):
    h.read(b)
check("offset is learned", abs(h.lat_center[0] - 200.0) < 1.0, f"centre {h.lat_center[0]:+.1f} Hz")
check("centred signal sits near zero", abs(h.obstacle) < 1.0, f"{h.obstacle:+.1f} Hz")

print("2. both directions are reachable once centred")
h.obstacle_thresh = 50.0
b.rate[h.wall_idx[0]] = 300.0 / h.wall_sign[0]      # now a wall really is on one side
h.read(b)
right = h.obstacle > h.obstacle_thresh
b.rate[h.wall_idx[0]] = 100.0 / h.wall_sign[0]      # ...and now on the other
h.read(b)
left = h.obstacle < -h.obstacle_thresh
check("a swing one way passes the threshold", right, f"{right}")
check("a swing the other way passes it too", left, f"{left}")

print("3. the centre is learned, not assumed")
h2 = Hands(cx)
h2.unmix = False
check("starts uncentred", np.allclose(h2.lat_center, 0.0))
b2 = StubBrain(n)
b2.rate[h2.wall_idx[0]] = 500.0 / h2.wall_sign[0]
for _ in range(299):                 # below the sample count it should not act
    h2.read(b2)
check("waits for enough history", np.allclose(h2.lat_center, 0.0), f"{h2.lat_center[0]:+.1f}")

print("4. the warm-up's forced walking does not feed the centre")
h3 = Hands(cx)
h3.unmix = False
h3.force_walk = True
b3 = StubBrain(n)
b3.rate[h3.wall_idx[0]] = 400.0 / h3.wall_sign[0]
for _ in range(400):
    h3.read(b3)
check("no history collected while forced", h3._lat_n == 0, f"n={h3._lat_n}")

print("5. the head-on exit offset is subtracted in proportion to the head-on signal")
h4 = Hands(cx)
h4.unmix = False
h4.exit_bias_a, h4.exit_bias_b = 0.0, -0.5     # a door ahead drags exits negative
b4 = StubBrain(n)
# Five of the ten head-on neurons also sit in the lateral readout, three of
# them with the opposite sign -- which is why a door straight ahead read as a
# door on the right in the first place. Drive one that belongs to the head-on
# set alone, so this checks the correction and not the overlap.
only_ahead = [k for k, i in enumerate(h4.exit_ahead_idx) if i not in set(h4.exit_idx)]
check("some head-on neurons are not in the lateral readout", len(only_ahead) > 0,
      f"{len(only_ahead)} of {len(h4.exit_ahead_idx)}")
k = only_ahead[0]
b4.rate[h4.exit_ahead_idx[k]] = 100.0 / h4.exit_ahead_sign[k]
h4.read(b4)
check("correction scales with the head-on readout", abs(h4.exits - 50.0) < 1e-3,
      f"exits {h4.exits:+.1f} Hz with head-on {h4.exit_ahead:+.1f} Hz")

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
