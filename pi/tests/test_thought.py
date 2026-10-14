"""The caption must say what the fly actually decided.

Every line it can print corresponds to a counter that decide() itself
incremented, so the commentary on stream is the fly's own reasoning rather
than a guess from its position. What is checked here: the right branch is
named, the side is the side the brain reported, and nothing is announced
twice or invented when nothing happened.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.playview import _thought, THOUGHT_STATS          # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


class Fly:
    """Stands in for Hands: the counters and the three lateral readouts."""

    def __init__(self, **sig):
        self.stats = {}
        self.turned_side = sig.get("turned")
        self.obstacle = sig.get("obstacle", 0.0)
        self.exits = sig.get("exits", 0.0)
        self.people = sig.get("people", 0.0)

    def did(self, stat, n=1):
        self.stats[stat] = self.stats.get(stat, 0) + n
        return self


print("1. nothing happened, nothing is said")
f, seen = Fly(), {}
f.did("turns", 500)                        # a fly mid-stream, with history
_thought(f, seen)
check("history is not replayed on the first frame", _thought(f, seen) is None)
check("silent at rest", _thought(f, seen) is None)
f.did("turns")
check("a turn is reported", _thought(f, seen) == "wandering")
f.turned_side = "left"; f.did("turns")
check("...with the way it actually turned", _thought(f, seen) == "wandering - turning left")
f.turned_side = None
check("but only once", _thought(f, seen) is None)

print("2. each branch names itself")
cases = [
    ({"stalled": 1}, {}, "thinks it's blocked - stopping", None),
    ({"stalled": 1}, {}, "walked into something - stopping", True),
    ({"stalled": 1}, {}, "thinks it's blocked - stopping", False),
    ({"approach": 1}, {}, "a way out, straight ahead", None),
    ({"avoid": 1}, {"obstacle": +300.0, "turned": "right"}, "something close - turning right", None),
    ({"avoid": 1}, {"obstacle": +300.0, "turned": "left"}, "something close - turning left", None),
    ({"toward_exit": 1}, {"exits": +400.0}, "a door on the left - going for it", None),
    ({"away_from_exit": 1}, {"exits": -400.0}, "a door on the right - keeping away", None),
    ({"toward_person": 1}, {"people": -400.0}, "thinks someone's on the right - going over", None),
    ({"away_from_person": 1}, {"people": +400.0}, "thinks someone's on the left - keeping away", None),
    ({"wall": 1}, {"turned": "left"}, "wall ahead - turning left", True),
    ({"wall": 1}, {"turned": "left"}, "thinks there's a wall ahead - turning left", False),
]
for stats, sig, want, solid in cases:
    f = Fly(**sig)
    seen = {}
    _thought(f, seen)                      # first call takes the counters as they are
    for k, n in stats.items():
        f.did(k, n)
    got = _thought(f, seen, solid=solid)
    check(f"{list(stats)[0]:18s} -> {want!r}", got == want, f"got {got!r}")

print("3. the fly's own priorities decide what gets said")
f, seen = Fly(obstacle=+300.0), {}
_thought(f, seen)
f.did("stalled").did("avoid").did("turns")
check("a stall outranks the turn it also recorded",
      _thought(f, seen) == "thinks it's blocked - stopping")
f, seen = Fly(obstacle=+300.0), {}
_thought(f, seen)
f.did("avoid").did("turns")
check("avoiding outranks plain wandering (every avoid is also a turn)",
      _thought(f, seen) == "something close - turning")

print("4. counters are followed, not re-counted")
f, seen = Fly(exits=+400.0), {}
_thought(f, seen)
f.did("toward_exit")
check("a new event is caught", _thought(f, seen) == "a door on the left - going for it")
check("and not repeated on the next frame", _thought(f, seen) is None)
f.did("toward_exit", 3)
check("several at once still report once", _thought(f, seen) == "a door on the left - going for it")
check("every branch it reads is a counter decide() writes",
      set(THOUGHT_STATS) <= {"stalled", "approach", "avoid", "wall", "turns",
                             "toward_exit", "away_from_exit", "toward_person",
                             "away_from_person"}, str(THOUGHT_STATS))

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
