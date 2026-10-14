"""The standing hints must step aside while the game is talking.

Before this, the hints sat on top of the game's own dialogue box and chopped
its text in half -- a sign in the Wireless Club read "This is the POKeMON
WIRELESS CLUB ONL" on the live stream. The detector is a brightness test on
the two strips either side of the hints, where nothing is ever drawn over the
game.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from flybrain.playview import textbox_open

W, H = 240, 160


def scene(floor=90):
    """A GBA frame of ordinary map, no box."""
    return np.full((H, W, 3), floor, np.uint8)


def with_box(shot, top=108, pale=250):
    """Paint FireRed's dialogue box across the bottom."""
    out = shot.copy()
    out[top:H - 8, 4:W - 4] = pale
    return out


ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


# -- the two states it has to tell apart ---------------------------------
check("plain map is not a box", not textbox_open(scene()))
check("dialogue box is a box", textbox_open(with_box(scene())))

# -- it must not be fooled by what is drawn over the middle --------------
covered = with_box(scene())
covered[210 // 2:, 60 // 2:420 // 2] = 0        # the hints, blacked out
check("box seen through blacked-out hints", textbox_open(covered))
plain = scene()
plain[210 // 2:, 60 // 2:420 // 2] = 255        # a white overlay, no box
check("white overlay alone is not a box", not textbox_open(plain))

# -- brightness of the map underneath should not flip it -----------------
check("pale sand floor is not a box", not textbox_open(scene(floor=180)))
check("pale grey cobbles (Pewter) are not a box", not textbox_open(scene(floor=225)))
check("dark cave is not a box", not textbox_open(scene(floor=20)))
check("box over a pale floor still reads", textbox_open(with_box(scene(180))))

# -- the top half of the screen is none of its business ------------------
sky = scene()
sky[:100] = 255
check("bright sky is not a box", not textbox_open(sky))

# -- the real thing, when the captures are around ------------------------
frames = os.path.join(os.path.dirname(os.path.abspath(__file__)), "frames")
if os.path.isdir(frames):
    from PIL import Image
    for name in sorted(os.listdir(frames)):
        if not name.endswith(".png"):
            continue
        want = name.startswith("box")
        shot = np.asarray(Image.open(os.path.join(frames, name)).convert("RGB"))
        check(f"{name} -> {want}", textbox_open(shot) is want)

print(f"{ok} checks pass")
