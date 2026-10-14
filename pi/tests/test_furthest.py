"""The title's location is how far the run has GOT, not where the fly is.

Ian asked for this and the data backs him: `place` read Route 3 while the run
was at Mt. Moon, so a live location segment would have announced the fly moving
BACKWARDS through the game. The fly wanders; a progress marker should not.

The visited set is permanent and written to disk, so a wrong entry is a wrong
entry for ever. That is why a new area has to hold for NEW_PLACE_FRAMES before
it counts, and why nothing unnameable may enter at all.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flybrain.game import Game
from flybrain.twitch import PLACES

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


tmp = tempfile.mkdtemp()


class Runner(Game):
    """Only the place tracker, with no emulator or brain behind it."""

    def __init__(self, memo, been=None, furthest=None, ready=True):
        self.PLACES_MEMO = memo
        self.places_been = set(been or ())
        self.furthest = furthest
        self._place_run = (None, 0)
        self._places_ready = ready


def walk(g, pid, frames):
    for _ in range(frames):
        g.note_place(pid)


HOLD = Game.NEW_PLACE_FRAMES
MT_MOON, ROUTE_3, ROUTE_4, CERULEAN = 127, 103, 104, 91

# -- a new area only counts once it has been held ------------------------
g = Runner(os.path.join(tmp, "a.json"), been={MT_MOON}, furthest=MT_MOON)
walk(g, CERULEAN, HOLD - 1)
check("a brief flicker does not advance the run", g.furthest == MT_MOON)
check("and does not enter the permanent set", CERULEAN not in g.places_been)
walk(g, CERULEAN, 1)
check("holding it does advance", g.furthest == CERULEAN)
check("and records it", CERULEAN in g.places_been)

# -- a run of frames must be CONSECUTIVE ---------------------------------
g = Runner(os.path.join(tmp, "b.json"), been={MT_MOON}, furthest=MT_MOON)
for _ in range(HOLD - 1):
    g.note_place(CERULEAN)
g.note_place(ROUTE_4)            # interrupted
for _ in range(HOLD - 1):
    g.note_place(CERULEAN)
check("an interrupted run does not count", g.furthest == MT_MOON)

# -- THE POINT: it never goes backwards ----------------------------------
g = Runner(os.path.join(tmp, "c.json"), been={ROUTE_3, MT_MOON}, furthest=MT_MOON)
walk(g, ROUTE_3, HOLD * 3)
check("walking back to a known area does not move the marker", g.furthest == MT_MOON)
walk(g, MT_MOON, HOLD * 2)
check("nor does re-entering the current one", g.furthest == MT_MOON)
walk(g, ROUTE_4, HOLD)
check("only somewhere NEW moves it", g.furthest == ROUTE_4)
walk(g, ROUTE_3, HOLD * 3)
check("and it stays moved", g.furthest == ROUTE_4)

# -- every named area counts, not only routes ----------------------------
# Ian: "a new area in general, not just routes."
g = Runner(os.path.join(tmp, "d.json"), been={ROUTE_3}, furthest=ROUTE_3)
for pid, what in ((127, "a cave"), (126, "a forest"), (91, "a city"),
                  (134, "a building"), (140, "a tower"), (136, "the safari zone")):
    walk(g, pid, HOLD)
    check(f"{what} counts as a new area", g.furthest == pid)

# -- what must NEVER enter the permanent set -----------------------------
g = Runner(os.path.join(tmp, "e.json"), been={MT_MOON}, furthest=MT_MOON)
for pid, why in ((99, "a Pokecenter section"), (100, "the other Pokecenter"),
                 (0, "a Hoenn id"), (87, "the last Hoenn id"),
                 (155, "a placeholder Sevii name"), (172, "another placeholder"),
                 (196, "the catch-all area"), (9999, "a nonsense id"),
                 (-1, "the not-read-yet value")):
    walk(g, pid, HOLD * 2)
    check(f"{why} never advances the marker", g.furthest == MT_MOON)
    check(f"{why} never enters the set", pid not in g.places_been)

# -- and the marker is always printable ----------------------------------
check("whatever the marker is, the title can name it",
      g.furthest is None or g.furthest in PLACES)

# -- it survives a restart, or the whole design fails --------------------
path = os.path.join(tmp, "f.json")
g = Runner(path, been={MT_MOON}, furthest=MT_MOON)
walk(g, CERULEAN, HOLD)
check("advancing wrote the file", os.path.exists(path))
saved = json.load(open(path))
check("the set was saved", CERULEAN in saved["been"])
check("the marker was saved", saved["furthest"] == CERULEAN)
check("written atomically, no tmp left", not os.path.exists(path + ".tmp"))

back = Runner(path)
back._load_places()
check("a restart keeps the marker", back.furthest == CERULEAN)
check("a restart keeps the set", CERULEAN in back.places_been)
walk(back, CERULEAN, HOLD * 2)
check("and does not re-announce a known area", back.furthest == CERULEAN)

# -- a lost file seeds from the route played, never empty ----------------
fresh = Runner(os.path.join(tmp, "missing.json"))
fresh._load_places()
check("a lost file does not start blank", len(fresh.places_been) > 0)
check("it starts where the run actually is", fresh.furthest == MT_MOON)
check("everything seeded is nameable",
      all(p in PLACES for p in fresh.places_been))
broken = os.path.join(tmp, "broken.json")
open(broken, "w").write("{ truncated")
b2 = Runner(broken)
b2._load_places()
check("a damaged file seeds rather than regressing", b2.furthest == MT_MOON)

# -- NOTHING is recorded before the run's own save is loaded -------------
# Calibration steps the game on a DIFFERENT cartridge state. Its map readings
# were being recorded as places this run had reached, and since the set saves
# as soon as it changes, that overwrote the seeded history with one element --
# after which walking back into Route 3 would have counted as new and moved
# the marker BACKWARDS.
cal = Runner(os.path.join(tmp, "cal.json"), been={MT_MOON}, furthest=MT_MOON, ready=False)
walk(cal, CERULEAN, HOLD * 3)
check("calibration cannot advance the marker", cal.furthest == MT_MOON)
check("calibration cannot write to the set", CERULEAN not in cal.places_been)
check("and cannot create the file", not os.path.exists(os.path.join(tmp, "cal.json")))
cal._places_ready = True
walk(cal, CERULEAN, HOLD)
check("once the real save is in, it tracks again", cal.furthest == CERULEAN)

print(f"{ok} checks pass")
