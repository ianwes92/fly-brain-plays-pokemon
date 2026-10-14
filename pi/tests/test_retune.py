"""The attraction thresholds must follow the room the fly is in NOW.

A threshold is only sensible against how common the thing is: someone is
clearly off to one side on ~33% of frames out in a town and ~1.5% inside a
house, and a threshold set for the first fires almost entirely on noise in the
second. The tuner estimates that base rate from its own recent history, so
what is tested here is how fast it notices the room changed -- measured
before this fix at a full 90 s window, which is far too slow to follow a fly
walking through a door.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import game as gm                                 # noqa: E402
from flybrain.connectome import Connectome                      # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


cx = Connectome()
h = gm.Hands(cx)
rng = np.random.default_rng(0)


def feed(frames, door_fraction, signal=200.0):
    """Frames in a room where a door is clearly to one side that often."""
    for _ in range(frames):
        lop = rng.random() < door_fraction
        l, r = (1.0, 0.0) if lop else (0.0, 0.0)
        h.exits = signal if lop else rng.normal(0, 25)
        h.people = 0.0
        h.observe_things((0.0, 0.0), (l, r))


print("1. a door-rich corridor")
feed(gm.RETUNE_SAMPLES, 0.40)
base_rich = h.retuned.get("exits", (None, None, None, None))[3]
check("the tuner sees doors as common", base_rich is not None and base_rich > 0.3,
      f"base rate {base_rich}")

print("2. the fly walks into a room with almost no doors")
seen = []
for block in range(6):                       # 6 x 300 frames = 1800, about 30 s
    feed(300, 0.02)
    seen.append(h.retuned["exits"][3])
print("    base rate the tuner is using, every 300 frames after the change:")
print("      " + "  ".join(f"{b:.2f}" for b in seen))
check("it has noticed within ~10 s (600 frames)", seen[1] < 0.25, f"{seen[1]:.2f}")
check("and has caught up within ~15 s (900 frames)", seen[2] < 0.08, f"{seen[2]:.2f}")
check("without the long window it would still read the corridor",
      base_rich > 0.3 and seen[-1] < 0.05, f"{base_rich:.2f} -> {seen[-1]:.2f}")

print("3. the threshold follows the base rate up, not just down")
feed(2000, 0.45)
check("a door-rich room raises it again", h.retuned["exits"][3] > 0.3,
      f"base rate {h.retuned['exits'][3]:.2f}")
check("and the distributions still come from the long window "
      "(enough evidence to judge shape)", gm.RETUNE_SAMPLES > gm.RETUNE_BASE * 3,
      f"{gm.RETUNE_SAMPLES} vs {gm.RETUNE_BASE}")

print("5. a room with nothing in it must not lower the bar")
h2 = gm.Hands(cx)
feed_h = h2
def feed2(frames, door_fraction, signal=200.0):
    for _ in range(frames):
        lop = rng.random() < door_fraction
        l, r = (1.0, 0.0) if lop else (0.0, 0.0)
        h2.exits = signal if lop else rng.normal(0, 25)
        h2.people = 0.0
        h2.observe_things((0.0, 0.0), (l, r))
feed2(gm.RETUNE_SAMPLES, 0.40)
feed2(gm.RETUNE_BASE + 100, 0.0)      # long enough that the recent window is ALL empty
settled = h2.exit_thresh
feed2(3000, 0.0)                      # and now a long stretch of nothing
check("with nothing in view at all, the threshold is left exactly alone",
      h2.exit_thresh == settled, f"{settled:.1f} -> {h2.exit_thresh:.1f}")
check("and it is nowhere near the floor a collapse would reach",
      h2.exit_thresh > 40, f"{h2.exit_thresh:.1f}")
feed2(2000, 0.45)                     # doors again: it must still be able to move
check("and it still adapts once there is something to learn from",
      h2.exit_thresh != settled, f"{h2.exit_thresh:.1f}")

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
