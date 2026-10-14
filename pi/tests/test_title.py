"""The stream title tells a stranger what just happened.

Counters do not earn their place: "1 badge, 3 caught" never changes in a way
worth re-reading, while "Caught a Rattata" and "Beat Brock @ Pewter City!" do.
Twitch allows 140 characters and the wanted format nearly fills them, so what
is tested hardest is what gets dropped when it will not fit -- whole segments,
never half a word, and never the two that make the stream findable and
joinable.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import twitch                                     # noqa: E402
from flybrain import runinfo as _runinfo
_runinfo.RUN_F = "/nonexistent/run.json"   # titles here are tested without a run number
from flybrain.twitch import TITLE_MAX, stream_title             # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


class FakeDiary:
    def __init__(self, entries):
        self.entries = [(0.0, k, t) for k, t in entries]


team = [{"name": "WARTORTLE", "level": 25, "hp": 70, "egg": False},
        {"name": "RATTATA", "level": 7, "hp": 22, "egg": False}]
prog = {"badges": 1, "dex_owned": 3}
diary = FakeDiary([("caught", "CAUGHT PIDGEY!"), ("caught", "CAUGHT RATTATA!")])

print("1. the shape asked for")
t = stream_title(prog, team, diary)
print(f"    {t}")
check("names the game in words people search for", t.startswith("Fruit Fly Plays Pokemon 24/7"), t)
check("the party lead, title case, lowercase lv", "Wartortle lv 25" in t, t)
check("the most recent catch by name", "Caught a Rattata" in t, t)
# The town USED to be here, and was removed on purpose 2026-09-27: it was the
# only location-shaped phrase in the line, so readers took it for where the fly
# is -- which a milestone can never be, because it persists while the fly walks
# on. Where the fly is now has its own segment.
check("the badge is an event, not a place", "Beat Brock!" in t, t)
check("and carries no town to be misread", "Pewter City" not in t, t)
check("the call to action advertises training", "!pain !pleasure" in t, t)
check("segments joined with ' - '", t.count(" - ") >= 3, t)
check("fits Twitch", len(t) <= TITLE_MAX, f"{len(t)} chars")

print("2. the newest catch wins, and only real species count")
check("most recent, not first", "Rattata" in stream_title(prog, team, diary), "")
d2 = FakeDiary([("caught", "CAUGHT RATTATA!"), ("caught", "NEW POKEDEX ENTRY (4 caught)")])
check("a pokedex line is not a species", "Caught a Rattata" in stream_title(prog, team, d2),
      stream_title(prog, team, d2))
d_ev = FakeDiary([("caught", "CAUGHT RATTATA!"), ("evolve", "SQUIRTLE EVOLVED INTO WARTORTLE!")])
check("an evolution is not read as the latest catch",
      "Caught a Rattata" in stream_title(prog, team, d_ev), stream_title(prog, team, d_ev))
d3 = FakeDiary([("caught", "CAUGHT MR. MIME!")])
check("two-word names read properly", "Caught a Mr. Mime" in stream_title(prog, team, d3),
      stream_title(prog, team, d3))

print("3. early game degrades cleanly")
early = stream_title({"badges": 0}, [{"name": "CHARMANDER", "level": 5, "egg": False}], None)
print(f"    {early}")
check("no badge, no catch: still a sentence",
      early == "Fruit Fly Plays Pokemon 24/7 - Charmander lv 5 - !pain !pleasure to train it",
      early)
check("never the word None", "None" not in early and "None" not in t)
bare = stream_title({}, [], None)
check("nothing at all still advertises the stream",
      bare == "Fruit Fly Plays Pokemon 24/7 - !pain !pleasure to train it", bare)

print("4. over-long titles drop whole segments, in order")
long_team = [{"name": "CRABOMINABLE", "level": 100, "egg": False}]
long_diary = FakeDiary([("caught", "CAUGHT CRABOMINABLE!")])
t8 = stream_title({"badges": 8}, long_team, long_diary)
print(f"    {t8}  [{len(t8)} chars]")
check("still fits", len(t8) <= TITLE_MAX, f"{len(t8)}")
check("the head survives", t8.startswith("Fruit Fly Plays Pokemon 24/7"), t8)
check("the call to action survives", t8.endswith("!pain !pleasure to train it"), t8)
check("nothing is cut mid-word", not t8.endswith(("-", " ", "…")) and ".." not in t8, t8)
# force a drop by making every segment long
huge = stream_title({"badges": 3}, [{"name": "X" * 40, "level": 100, "egg": False}],
                    FakeDiary([("caught", "CAUGHT " + "Y" * 40 + "!")]))
print(f"    {huge}  [{len(huge)} chars]")
check("a pathological case still fits", len(huge) <= TITLE_MAX, f"{len(huge)}")
check("and still has both ends", huge.startswith("Fruit Fly Plays Pokemon 24/7")
      and huge.endswith("!pain !pleasure to train it"), huge)

print("5. it names the strongest, not whoever is in front")
front_rat = [{"name": "RATTATA", "level": 7, "egg": False},
             {"name": "WARTORTLE", "level": 25, "egg": False}]
t5 = stream_title(prog, front_rat, diary)
check("a low-level Pokemon walking in front is not the headline",
      "Wartortle lv 25" in t5 and "Rattata lv 7" not in t5, t5)
check("an egg is never the headline",
      "Wartortle lv 25" in stream_title(prog, [{"name": "EGG", "level": 60, "egg": True}]
                                        + front_rat, diary), "")

print("6. it remembers what it set, so it never argues with a hand edit")
import tempfile
memo = os.path.join(tempfile.mkdtemp(), "title.json")
check("nothing remembered on a fresh machine", twitch.recall_title(memo) == "")
twitch.remember_title("Fruit Fly Playing Pokemon - Wartortle lv 26", memo)
check("what we set survives a restart",
      twitch.recall_title(memo) == "Fruit Fly Playing Pokemon - Wartortle lv 26",
      twitch.recall_title(memo))
twitch.remember_title("a newer one", memo)
check("and is replaced, not appended", twitch.recall_title(memo) == "a newer one")
open(memo, "w").write("{ not json")
check("a damaged memo means we simply do not remember", twitch.recall_title(memo) == "")

print("7. the scope is still requested")
check("channel:manage:broadcast in SCOPES", "channel:manage:broadcast" in twitch.SCOPES)

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
# Ian, 2026-10-08: the run number leads the title.
def _run_prefix():
    import json as _j, tempfile as _t, os as _o
    from flybrain import twitch as _tw
    d = _t.mkdtemp(); rf = _o.path.join(d, "run.json")
    _j.dump({"id": 4, "started": "2026-10-08T13:20:00"}, open(rf, "w"))
    _runinfo.RUN_F = rf
    t = _tw.stream_title({"badges": 0}, [], None, place=None)
    assert t.startswith("Lab Run 1 · Fruit Fly Plays Pokemon 24/7"), t
    assert len(t) <= _tw.TITLE_MAX
    assert _runinfo.tag(rf).startswith("LAB RUN 1 · DAY "), _runinfo.tag(rf)
    _runinfo.RUN_F = "/nonexistent/run.json"
    assert _tw.stream_title({"badges": 0}, [], None).startswith("Fruit Fly")
    print("test_title: run prefix checks passed")


_run_prefix()


sys.exit(0 if ok else 1)


print("8. where the fly IS, and the milestone that is not a place")
# Ian read "Beat Brock @ Pewter City!" as a claim about the fly's position and
# reported it as wrong twice -- once by editing the title by hand to say
# "@ Mt. Moon". The data was right and the presentation was not: it was the
# only location-shaped phrase in the line, and a milestone persists while the
# fly keeps walking, so that reading is wrong permanently.
team = [{"name": "WARTORTLE", "level": 31, "egg": False}]
prog = {"badges": 1, "dex_owned": 4, "dex_seen": 21}


class _D:
    entries = [(0, "caught", "CAUGHT MAGIKARP!")]


t = stream_title(prog, team, _D(), place=103)
check("the gym no longer carries a town", "@ Pewter City" not in t, t)
check("but the badge is still news", "Beat Brock!" in t)
check("and the live place is named", "@ Route 3" in t)
check("it still fits", len(t) <= TITLE_MAX, f"{len(t)} chars")

check("an unknown place is omitted, not numbered",
      "@" not in stream_title(prog, team, _D(), place=9999))
check("...and no bare id leaks in", "9999" not in stream_title(prog, team, _D(), place=9999))
check("no place at all still works", "Beat Brock!" in stream_title(prog, team, _D()))

# the drop order: the milestone goes before the location
long_team = [{"name": "CHARIZARDOSAURUS", "level": 100, "egg": False}]


class _LD:
    entries = [(0, "caught", "CAUGHT KANGASKHANIMUS!")]


big = stream_title({"badges": 8, "dex_owned": 99, "dex_seen": 150}, long_team, _LD(), place=103)
check("a crowded title still fits", len(big) <= TITLE_MAX, f"{len(big)} chars")
check("the hook is never dropped", big.startswith(TITLE_HEAD))
check("the call to action is never dropped", TITLE_CTA in big)
if "Beat Giovanni!" not in big:
    check("the location outlived the milestone", "@ Route 3" in big, big)


print("9. the place table says nothing rather than something wrong")
from flybrain.twitch import PLACES                                # noqa: E402
check("Kanto starts where the decomp says", PLACES.get(88) == "Pallet Town")
check("and ends at the Kanto boundary", PLACES.get(142) == "the Power Plant")
check("the id confirmed against the screen is Route 3", PLACES.get(103) == "Route 3")
check("Mt. Moon is where the fly has been", PLACES.get(127) == "Mt. Moon")
# Hoenn ids belong to a different game and must never reach a title.
check("no Hoenn id is named", all(PLACES.get(i) is None for i in range(0, 88)))
# placeholder names in the data are worse than nothing
for i in (143, 155, 158, 171, 173, 187):
    check(f"Sevii id {i} stays unnamed", PLACES.get(i) is None)
check("the catch-all area stays unnamed", PLACES.get(196) is None)
# The two Pokecenter sections are buildings, not progress, and are absent.
check("a Pokecenter section is not an area", PLACES.get(99) is None)
check("...nor is the other", PLACES.get(100) is None)
# This run is Kanto: Sevii starts at 143 and is deliberately left out.
check("Sevii is out of scope", all(k < 143 for k in PLACES))
check("exactly the 53 Kanto areas",
      set(PLACES) == set(range(88, 143)) - {99, 100})
# every name must be short enough to be droppable rather than fatal
check("no name is absurdly long", max(len(v) for v in PLACES.values()) < 25)
check("no name is empty", all(v.strip() for v in PLACES.values()))


print("10. Ian's 24/7 format, and its worst case")
from flybrain.twitch import GYMS                                   # noqa: E402


class _C:
    def __init__(self, c):
        self.entries = [(0, "caught", f"CAUGHT {c}!")]


ian = stream_title({"badges": 1}, [{"name": "WARTORTLE", "level": 35, "egg": False}],
                   _C("SPEAROW"), place=125)
check("Ian's example, exactly",
      ian == "Fruit Fly Plays Pokemon 24/7 - Wartortle lv 35, Caught a Spearow - "
             "@ Route 25 - Beat Brock! - !pain !pleasure to train it", ian)
longest = max(PLACES, key=lambda k: len(PLACES[k]))
worst = stream_title({"badges": 3}, [{"name": "CHARMELEON", "level": 100, "egg": False}],
                     _C("FARFETCH'D"), place=longest)
check("the worst case still fits", len(worst) <= TITLE_MAX, f"{len(worst)}: {worst}")
check("and keeps the 24/7 hook", worst.startswith("Fruit Fly Plays Pokemon 24/7"))
check("and the free commands", worst.endswith("!pain !pleasure to train it"))


print("ALL PASS" if ok else "FAILURES ABOVE")

