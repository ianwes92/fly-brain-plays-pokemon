"""The on-screen readout has to say what the fly's readout actually says.

The stream's whole claim is that a real brain is seeing and deciding. This
panel is the only place a viewer can check that, so it must not be decorative:
the side, the strength and the moment it crosses into action all have to match
the numbers `Hands.decide` turns on.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from flybrain.playview import SEE_SPAN, seeing_rows


class FakeHands:
    def __init__(self, obstacle=0.0, people=0.0, exits=0.0,
                 obstacle_thresh=100.0, people_thresh=200.0, exit_thresh=100.0):
        self.obstacle, self.people, self.exits = obstacle, people, exits
        self.obstacle_thresh = obstacle_thresh
        self.people_thresh = people_thresh
        self.exit_thresh = exit_thresh


ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


def by_name(hands):
    return {lab: rest for lab, *rest in seeing_rows(hands)}


# -- the three channels are all there, in a fixed order -------------------
labels = [r[0] for r in seeing_rows(FakeHands())]
check("three channels", labels == ["WALLS", "PEOPLE", "DOORS"])

# -- nothing in view reads as nothing -------------------------------------
for lab, (where, at, lit) in by_name(FakeHands()).items():
    check(f"{lab} centred when silent", where == 0.0)
    check(f"{lab} unlit when silent", not lit)

# -- + is the left, matching the thought caption's own convention ---------
r = by_name(FakeHands(people=300.0, people_thresh=200.0))["PEOPLE"]
check("someone on the left reads left", r[0] > 0)
r = by_name(FakeHands(people=-300.0, people_thresh=200.0))["PEOPLE"]
check("someone on the right reads right", r[0] < 0)

# -- it lights exactly when the fly would act, not before -----------------
check("under threshold is unlit", not by_name(FakeHands(exits=99.0, exit_thresh=100.0))["DOORS"][2])
check("at the threshold is lit", by_name(FakeHands(exits=100.0, exit_thresh=100.0))["DOORS"][2])
check("over the threshold is lit", by_name(FakeHands(exits=140.0, exit_thresh=100.0))["DOORS"][2])
check("lit on the right too", by_name(FakeHands(exits=-140.0, exit_thresh=100.0))["DOORS"][2])

# -- the tick sits where the signal starts to matter ----------------------
lit = by_name(FakeHands(obstacle=101.0, obstacle_thresh=100.0))["WALLS"]
check("mark is inside the bar", 0.0 < lit[1] < 1.0)
check("a just-lit signal sits at the mark", abs(lit[0] - lit[1]) < 0.02)
check("span puts the mark well short of the end", lit[1] == 1.0 / SEE_SPAN)

# -- each channel keeps its own scale, because the tuner moves them -------
# people rare (371 Hz) and doors common (108 Hz) is a real pair, measured.
same = by_name(FakeHands(people=371.0, people_thresh=371.0,
                         exits=108.0, exit_thresh=108.0))
check("both read the same at their own thresholds",
      abs(same["PEOPLE"][0] - same["DOORS"][0]) < 1e-9)
check("both lit", same["PEOPLE"][2] and same["DOORS"][2])

# -- a huge signal cannot run off the end of the bar ----------------------
r = by_name(FakeHands(obstacle=50_000.0, obstacle_thresh=100.0))["WALLS"]
check("pinned at the left end", r[0] == 1.0)
r = by_name(FakeHands(obstacle=-50_000.0, obstacle_thresh=100.0))["WALLS"]
check("pinned at the right end", r[0] == -1.0)

# -- before calibration there is no threshold to draw ---------------------
blank = by_name(FakeHands(people=500.0, people_thresh=1e9))["PEOPLE"]
check("uncalibrated draws no mark", blank[1] is None)
check("uncalibrated is unlit", not blank[2])
check("uncalibrated shows no signal", blank[0] == 0.0)
zero = by_name(FakeHands(exits=500.0, exit_thresh=0.0))["DOORS"]
check("a zero threshold is not a divide by zero", zero[1] is None)

# -- a missing attribute must not take the stream down --------------------
class Bare:
    pass


rows = seeing_rows(Bare())
check("missing readouts still return three rows", len(rows) == 3)
check("missing readouts are blank", all(r[1] == 0.0 and not r[3] for r in rows))

print(f"{ok} checks pass")
