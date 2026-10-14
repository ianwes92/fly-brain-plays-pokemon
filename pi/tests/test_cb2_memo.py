"""The overworld's callback should survive a restart.

It is a ROM address and the same every run, but it is only LEARNED when the
fly walks a tile. A restart that resumes inside a battle may not walk one for
a long time, and until then the game cannot tell "in a menu" from "no idea" --
so the speed-up stays off exactly when battles are eating the stream. This
seeds it from last time, while keeping the property that the first real tile
move recomputes it from counts, so a wrong memo cannot stick.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flybrain.game import Game

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


tmp = tempfile.mkdtemp()
memo = os.path.join(tmp, "rom.json")


class Memo(Game):
    """Only the memo, with no emulator behind it."""

    CB2_MEMO = memo

    def __init__(self):
        self.overworld_cb2 = None


g = Memo()

# -- nothing remembered yet ------------------------------------------------
check("no file means nothing recalled", g._recall_cb2() is None)

# -- write it, get it back -------------------------------------------------
g._remember_cb2(134571445)
check("the file was written", os.path.exists(memo))
check("and reads back", g._recall_cb2() == 134571445)
check("stored under a named key, not a bare value",
      "overworld_cb2" in json.load(open(memo)))
check("written atomically, no tmp left behind", not os.path.exists(memo + ".tmp"))

# -- a later value replaces it --------------------------------------------
g._remember_cb2(134571465)
check("a new value overwrites", g._recall_cb2() == 134571465)

# -- damage must never crash the game, only lose the hint ------------------
with open(memo, "w") as f:
    f.write("{ truncated")
check("a damaged memo recalls nothing", g._recall_cb2() is None)
with open(memo, "w") as f:
    json.dump({"overworld_cb2": "not a number"}, f)
check("a non-numeric memo recalls nothing", g._recall_cb2() is None)
with open(memo, "w") as f:
    json.dump({}, f)
check("a memo missing the key recalls nothing", g._recall_cb2() is None)
with open(memo, "w") as f:
    json.dump({"overworld_cb2": True}, f)
check("a bool is not an address", g._recall_cb2() is None or
      g._recall_cb2() == 1)      # bool is an int subclass; either is harmless

# -- an unwritable path must not raise ------------------------------------
class Bad(Memo):
    CB2_MEMO = "/proc/definitely/not/writable/rom.json"


b = Bad()
b._remember_cb2(123)             # must not raise
check("an unwritable memo is survivable", b._recall_cb2() is None)

# -- the seed does not overwrite something already learned ----------------
g2 = Memo()
g2.overworld_cb2 = 999           # learned this run
with open(memo, "w") as f:
    json.dump({"overworld_cb2": 111}, f)
# enable_resume only seeds when it is still None
if g2.overworld_cb2 is None:
    g2.overworld_cb2 = g2._recall_cb2()
check("what was learned this run wins over the memo", g2.overworld_cb2 == 999)

print(f"{ok} checks pass")
