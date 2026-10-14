"""The temporary title notice (the run reset): worded from the date so it
can never go stale, placed second, never dropped, and the title still fits."""
import datetime
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import twitch as tw
from flybrain import runinfo as _runinfo
_runinfo.RUN_F = "/nonexistent/run.json"   # titles here are tested without a run number

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


d = tempfile.mkdtemp()
f = os.path.join(d, "n.json")
json.dump({"what": "reset", "on": "2026-10-04"}, open(f, "w"))
D = datetime.date
check("the day before: RESET TOMORROW", tw.title_notice(f, D(2026, 10, 3)) == "RESET TOMORROW")
check("on the day: RESET TODAY", tw.title_notice(f, D(2026, 10, 4)) == "RESET TODAY")
check("the day after: gone by itself", tw.title_notice(f, D(2026, 10, 5)) is None)
check("further ahead: the date", tw.title_notice(f, D(2026, 10, 1)) == "RESET SUN OCT 4")
check("no file: nothing", tw.title_notice(os.path.join(d, "none.json")) is None)
open(f, "w").write("{broken")
check("a broken file: nothing, not a crash", tw.title_notice(f) is None)

# Today's real title, with the notice.
team = [{"name": "BLASTOISE", "level": 36}, {"name": "RATTATA", "level": 11}]
diary = type("D", (), {"entries": [[1, "caught", "CAUGHT SPEAROW!"]]})()
t = tw.stream_title({"badges": 1}, team, diary, place=125, notice="RESET TOMORROW")
print(" ", len(t), t)
check("the notice is the second thing read", t.startswith(tw.TITLE_HEAD + " - RESET TOMORROW - "))
check("it fits", len(t) <= tw.TITLE_MAX)
check("the call to action stays", t.endswith(tw.TITLE_CTA))

# Worst case: the longest names Kanto offers.
longest_mon = max(tw._nice(n) for n in ("Charmeleon", "Kangaskhan", "Farfetch'd", "Victreebel",
                                         "Butterfree", "Hitmonchan")).__len__()
team = [{"name": "KANGASKHAN", "level": 100}]
diary = type("D", (), {"entries": [[1, "caught", "CAUGHT FARFETCH'D!"]]})()
longest_place = max(tw.PLACES, key=lambda k: len(tw.PLACES[k]))
for badges in range(0, 9):
    t = tw.stream_title({"badges": badges}, team, diary, place=longest_place, notice="RESET TOMORROW")
    check(f"worst case with {badges} badges fits 140", len(t) <= tw.TITLE_MAX)
    check(f"and keeps the notice ({badges} badges)", "RESET TOMORROW" in t)
t = tw.stream_title({"badges": 8}, team, diary, place=longest_place, notice="RESET TOMORROW")
print(" ", len(t), t)
check("when tight, the milestone goes before the place", "@ " in t or "Beat" not in t)

# Without a notice nothing changes.
t0 = tw.stream_title({"badges": 1}, [{"name": "BLASTOISE", "level": 36}],
                     type("D", (), {"entries": [[1, "caught", "CAUGHT SPEAROW!"]]})(), place=125)
check("no notice, the old title exactly", t0 == "Fruit Fly Plays Pokemon 24/7 - Blastoise lv 36, Caught a Spearow"
      " - @ Route 25 - Beat Brock! - !pain !pleasure to train it")
print(f"{ok} checks pass")
