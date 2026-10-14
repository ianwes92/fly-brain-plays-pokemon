"""A repeated chat key is a new key-down (Ian, 2026-10-13: "I can't press the same button sometimes")."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import game as gm
A, UP = gm.A, gm.UP
k, until, gap = gm.nudge_window(0.0, 0, -1.0, A, 700.0, 1.0)
assert (k, until, gap) == (A, 700.0, None)                        # a first press: no gap
k2, until2, gap2 = gm.nudge_window(300.0, k, until, A, 700.0, 1.0)
assert gap2 is not None and 300 + 2 * gm.GBA_FRAME_MS < gap2 < 300 + 3 * gm.GBA_FRAME_MS
assert until2 == gap2 + 700.0                                     # full press after the gap
mix = lambda t: gm.mix_keys(t, 0, k2, until2, A, gap2, 0, -1.0)
assert mix(310.0) & A == 0, "released during the gap"
assert mix(gap2 + 1) & A, "pressed again after it"
assert gm.mix_keys(310.0, A, k2, until2, A, gap2, 0, -1.0) & A == 0, "even if the fly holds A too"
k3, until3, gap3 = gm.nudge_window(300.0, A, 700.0, UP, 700.0, 1.0)
assert gap3 is None and k3 == UP                                  # a different key: no gap
_, _, gfast = gm.nudge_window(300.0, A, 700.0, A, 700.0, 3.0)
assert gfast - 300.0 < gap2 - 300.0                               # menus run 3x: shorter in brain time
assert gm.mix_keys(10.0, UP, A, 700.0, 0, -1.0, gm.B, 20.0) == gm.B   # a paid driver overrides all
print("test_repress: 9 checks passed")
