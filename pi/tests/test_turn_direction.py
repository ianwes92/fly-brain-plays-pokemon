"""The turn the fly decides is the turn applied to the game.

Regression (2026-10-05..07): the mirrored-world switch shared the name
`mirror` with the DNp35/DNp18 side readout, which read() rewrites every frame.
The readout is almost never exactly zero, so nearly every live turn was
applied REVERSED. The applied heading must follow the decision whatever the
readout says, and reverse only when the experiment switch is on."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.connectome import Connectome
from flybrain.game import Hands

ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1

cx = Connectome()

def one_turn(step, readout, mirror_world=False):
    h = Hands(cx)
    h.mirror_world = mirror_world
    h.obstacle, h.exits, h.exit_ahead = 0.0, 0.0, -1e9
    h.obstacle_thresh = 1e9
    h._attraction = lambda: step                 # the decision: +1 right, -1 left
    h.mirror = readout                           # the side readout, whatever it reads
    h.heading, h.last_turn, h.commit_until = 0, -1e9, -1e9
    h.walk_base, h.walk, h.mdn_rate, h.mdn_base = 1.0, 0.0, 0.0, 0.0
    h.decide(10_000.0)
    return (h.heading - 0) % 4, h.stats

for readout in (0.0, 37.5, -12.0):
    hd, st = one_turn(+1, readout)
    check(f"right decided -> right applied (readout {readout})", hd == 1 and st.get("right") == 1)
    hd, st = one_turn(-1, readout)
    check(f"left decided -> left applied (readout {readout})", hd == 3 and st.get("left") == 1)
hd, st = one_turn(+1, 37.5, mirror_world=True)
check("mirrored world reverses the applied turn, stats keep the brain's", hd == 3 and st.get("right") == 1)
print(f"test_turn_direction: {ok} checks passed")
