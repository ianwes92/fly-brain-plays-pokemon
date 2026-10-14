"""Science-tab thought data: kinds, clean split, map checks, per-minute rate,
persistence across restarts, and the summary the relay serves."""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import thoughts as T

ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1

V = {"obstacle_lr": (0.6, 0.1), "people_lr": (0.0, 0.0), "exits_lr": (0.0, 0.4)}
check("away from obstacle", T.checks("something close - turning right", V)
      == [("obstacle_in_view", True), ("turned_away_from_obstacle", True)])
check("toward obstacle", T.checks("something close - turning left", V)[1] == ("turned_away_from_obstacle", False))
check("door", T.checks("a door on the right - going for it", V) == [("door_in_range", True), ("door_on_named_side", True)])
check("person absent", T.checks("thinks someone's on the left - going over", V)[0] == ("person_in_range", False))
check("stops not scored", T.checks("walked into something - stopping", V) == [])
check("no view, no checks", T.checks("a door on the left - x", None) == [])

d = tempfile.mkdtemp()
now = time.mktime(time.strptime("2026-10-07 14:00:00", "%Y-%m-%d %H:%M:%S"))
L = T.ThoughtLog(folder=d)
for i in range(121):
    L.tick(now + i * 0.5, True)                    # 60 s of play
L.add("a door on the right - going for it", True, V, now=now + 1)
L.add("a door on the left - going for it", False, V, now=now + 2)        # assisted: not scored
L.add("walked into something - stopping", True, None, now=now + 3)
L.add("thinks it's blocked - stopping", True, None, now=now + 4)
L.add("a door on the right - going for it", True, V, now=now + 5, battle=True)   # battle: not scored
L.flush(now + 60)
s = T.summary(1, folder=d, now=now + 60)
k = s["kinds"]["a_door_on_the_X_going_for_it"]
check("counted", k["n"] == 3 and k["clean"] == 2)
check("scored own, non-battle only", k["accuracy"]["door_on_named_side"] == {"ok": 1, "n": 1, "pct": 100.0})
check("rate per minute of play", s["days"][0]["per_min"] == 5.0)
check("phantom stops", s["stops"] == {"real": 1, "nothing_in_the_way": 1, "phantom_pct": 50.0})
L2 = T.ThoughtLog(folder=d)                         # a restart picks up the day
L2.add("a door on the right - going for it", True, V, now=now + 100)
L2.flush(now + 100)
check("survives restart", T.summary(1, folder=d, now=now + 100)["kinds"]["a_door_on_the_X_going_for_it"]["n"] == 4)
L2.add("a door on the right - x", True, V, now=now + 86400)           # next day: new file
L2.flush(now + 86400)
check("day rolls over", len(T.summary(2, folder=d, now=now + 86400)["days"]) == 2)
check("empty folder", T.summary(3, folder=tempfile.mkdtemp())["kinds"] == {})
json.dumps(s)
print(f"test_thoughts: {ok} checks passed")
