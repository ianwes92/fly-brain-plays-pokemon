"""The on-screen story: honest counters a newcomer can read in one glance.

Every line must come from something that survives a restart, so a counter
never resets to zero in front of the people it is meant to impress, and no
line may guess at something there is no record of (there is no trustworthy
run start date, so there is no "Day N").
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from flybrain.playview import story_lines

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


class Paid:
    def __init__(self, trainers):
        self.trainers = trainers


ROUTE_25 = 125
been = {88, 89, 90, 91, 101, 102, 103, 104, 124, 125, 126, 127}
lines = story_lines({"badges": 1, "dex_owned": 4}, been, ROUTE_25,
                    Paid({"examplechannel": 44, "lucasvarg658": 28, "x": 90}))

check("three lines when everything is known", len(lines) == 3)
check("areas come from the persisted set", lines[0].startswith("12 AREAS EXPLORED"))
check("and name the furthest place", "ROUTE 25" in lines[0])
check("training totals every press", "162 TIMES" in lines[1])
check("and counts the people", lines[1].startswith("3 PEOPLE HAVE"))
check("progress from the save", "1 BADGE" in lines[2] and "4 POKEMON CAUGHT" in lines[2])

# -- grammar that would look broken on stream -----------------------------
one = story_lines({"badges": 1, "dex_owned": 1}, been, ROUTE_25, Paid({"solo": 3}))
check("one trainer is a person, not people", "1 PERSON HAS TRAINED" in one[1])
check("one badge is singular", "1 BADGE " in one[2] and "1 BADGES" not in one[2])
two = story_lines({"badges": 2, "dex_owned": 9}, been, ROUTE_25, None)
check("two badges are plural", "2 BADGES" in two[-1])

# -- nothing known, nothing claimed ---------------------------------------
check("an empty run says nothing rather than zeros",
      story_lines({}, set(), None, Paid({})) == [])
check("no leaderboard is not 'zero people trained it'",
      not any("TRAINED" in l for l in story_lines({}, been, ROUTE_25, Paid({}))))
check("a missing paid object is survivable",
      isinstance(story_lines({"badges": 1}, been, ROUTE_25, None), list))
check("an unnameable furthest place still counts the areas",
      story_lines({}, been, 9999, None)[0] == "12 AREAS EXPLORED")

# -- no invented history ---------------------------------------------------
for l in lines:
    check(f"no guessed date in '{l}'", "DAY " not in l)

# -- every line fits the slot ----------------------------------------------
for l in lines + one + two:
    check(f"fits the under-brain slot: '{l}'", len(l) <= 58)

print(f"{ok} checks pass")
