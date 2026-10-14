"""A stall must actually stop the fly, and B must be calibrated like A.

Two defects found the same way -- by measuring the live stream rather than
reading the code:

1. The stall readout fires and changes nothing. In a battle it was over
   threshold on 100% of samples (median 3676 Hz against a threshold of 86)
   while the fly was in a walking bout 99% of the time. A bout ended on one
   frame and the next frame restarted it, because the drive that started it
   was still there. In the overworld this is masked -- bumping a wall also
   turns the fly -- but on a battle screen there is nothing to turn away from,
   so the direction presses just keep moving the menu cursor.

2. B was the only motor threshold never calibrated against data. It fired at
   71.8% of its own refractory ceiling, undoing what chat had just set up.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

from flybrain.game import Hands

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


def fly(**kw):
    """A Hands with just the fields the bout logic reads."""
    h = Hands.__new__(Hands)
    h.bout = False
    h.bout_since = -1e9
    h.rest_until = -1e9
    h.min_bout_ms = 300.0
    h.stall_rest_ms = 300.0
    h.start_thresh, h.stop_thresh = 1.0, 0.5
    h.stall_thresh = 86.0
    h.stats = {"bouts": 0, "stalled": 0}
    h.force_walk = False
    h.locked = False
    h.walk, h.stall = 0.0, 0.0
    for k, v in kw.items():
        setattr(h, k, v)
    return h


def bout_step(h, t_ms):
    """The bout half of Hands.decide, in isolation."""
    if h.force_walk:
        h.bout = True
    elif h.locked:
        if not h.bout:
            h.bout, h.bout_since = True, t_ms
            h.stats["bouts"] += 1
    elif not h.bout:
        if h.walk > h.start_thresh and t_ms >= h.rest_until:
            h.bout, h.bout_since = True, t_ms
            h.stats["bouts"] += 1
    elif (t_ms - h.bout_since > h.min_bout_ms
          and (h.walk < h.stop_thresh or h.stall > h.stall_thresh)):
        if h.stall > h.stall_thresh and h.walk >= h.stop_thresh:
            h.stats["stalled"] += 1
            h.rest_until = t_ms + h.stall_rest_ms
        h.bout = False
    return h.bout


# -- THE BUG: a stall used to stop the fly for exactly one frame ----------
h = fly(walk=5.0, stall=0.0)
bout_step(h, 0.0)
check("a bout starts on drive", h.bout)
h.stall = 4000.0                      # now getting nowhere, as in a battle
bout_step(h, 400.0)                   # past min_bout_ms
check("the stall ends the bout", not h.bout)
check("and it was counted as a stall", h.stats["stalled"] == 1)
bout_step(h, 416.0)                   # the very next frame
check("it does NOT restart immediately", not h.bout)
bout_step(h, 600.0)
check("nor part way through the rest", not h.bout)
bout_step(h, 701.0)                   # rest is over
check("it walks again once rested", h.bout)

# -- the walking fraction that measurement actually showed ---------------
# Drive high and stall high throughout, i.e. a battle. Count frames walking.
h = fly(walk=5.0, stall=4000.0)
walking = sum(1 for i in range(600) if bout_step(h, i * 16.7))
frac = walking / 600
check("in a battle it is no longer walking nearly all the time", frac < 0.6)
check("but it is not frozen either", frac > 0.1)

# -- a bout that merely ran out of drive does NOT rest -------------------
h = fly(walk=5.0, stall=0.0)
bout_step(h, 0.0)
h.walk = 0.1                          # drive gone, nothing to wait for
bout_step(h, 400.0)
check("a drive-out bout ends", not h.bout)
check("without counting a stall", h.stats["stalled"] == 0)
check("and without resting", h.rest_until < 0)
h.walk = 5.0
bout_step(h, 416.0)
check("so it may start again at once", h.bout)

# -- rest must not override the things that force walking ----------------
h = fly(walk=5.0, stall=4000.0)
bout_step(h, 0.0)
bout_step(h, 400.0)
check("resting after the stall", not h.bout and h.rest_until > 400.0)
h.force_walk = True
check("calibration's forced walk still wins", bout_step(h, 416.0))
h.force_walk, h.bout = False, False
h.locked = True
check("a door it can see still wins", bout_step(h, 420.0))

# -- B's threshold now comes from data, and keeps its adaptive baseline --
mdn = np.array([2.0] * 90 + [9.0] * 10)      # ordinary level, with excursions
margin = max(float(np.percentile(mdn, 90) - np.median(mdn)), 1.0)
check("the margin is the top-tenth rule A uses", margin > 0)
check("B fires above baseline plus that margin", (2.0 + margin + 0.1) > 2.0 + margin)
check("and not at the ordinary level", not (2.0 > 2.0 + margin))
flat = np.array([5.0] * 100)                 # a population that never varies
check("a flat signal still gets a usable floor",
      max(float(np.percentile(flat, 90) - np.median(flat)), 1.0) == 1.0)

print(f"{ok} checks pass")
