"""What counts as news on the stream: a badge, a catch, a level, a wipe.

The party gets reordered and Pokemon faint and heal, so comparisons are by
name and by the best level seen, not by slot. None of that should read as news.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.game import milestones                      # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


def mon(name, level, hp=20, max_hp=20, egg=False):
    return {"name": name, "nickname": name, "species": 1, "level": level,
            "hp": hp, "max_hp": max_hp, "egg": egg}


P = lambda badges=0, owned=0: {"badges": badges, "dex_owned": owned, "dex_seen": owned}
kinds = lambda evs: [k for k, _ in evs]

print("1. the first snapshot is not news")
check("no team, no history", milestones(None, P(), None, []) == [])
check("a team already there is not 'caught'", kinds(milestones(None, P(0, 1), None, [mon("SQUIRTLE", 12)])) == [])

print("2. catching, levelling, badges")
before, team0 = P(0, 1), [mon("SQUIRTLE", 12)]
evs = milestones(before, P(0, 2), team0, [mon("SQUIRTLE", 12), mon("PIDGEY", 3)])
check("a new Pokemon is a catch", evs == [("caught", "CAUGHT PIDGEY!")], str(evs))
evs = milestones(before, P(0, 1), team0, [mon("SQUIRTLE", 13)])
check("a level up is news", evs == [("level", "SQUIRTLE grew to Lv 13")], str(evs))
evs = milestones(before, P(1, 1), team0, team0)
check("a badge is news", evs == [("badge", "EARNED A BADGE!  (1 in total)")], str(evs))

print("3. the things that are NOT news")
check("nothing changed", milestones(before, P(0, 1), team0, team0) == [])
check("fainting alone", kinds(milestones(before, P(0, 1), team0, [mon("SQUIRTLE", 12, hp=0)])) == ["faint"])
two = [mon("SQUIRTLE", 12), mon("PIDGEY", 5)]
check("reordering the party",
      milestones(before, P(0, 2), two, [two[1], two[0]]) == [], str(milestones(before, P(0, 2), two, [two[1], two[0]])))
check("healing at the centre",
      milestones(before, P(0, 1), [mon("SQUIRTLE", 12, hp=0)], [mon("SQUIRTLE", 12)]) == [])
check("an egg has no level to grow",
      milestones(before, P(0, 1), team0, team0 + [mon("EGG", 0, egg=True)]) == [])

print("4. a wipe, once, and only from a healthy team")
down = [mon("SQUIRTLE", 12, hp=0), mon("PIDGEY", 5, hp=0)]
check("whole team down is news", ("faint", "the whole team fainted") in milestones(before, P(0, 2), two, down))
check("...but not repeated every second", milestones(before, P(0, 2), down, down) == [])

print("5. several at once")
evs = milestones(P(0, 1), P(1, 2), team0, [mon("SQUIRTLE", 14), mon("NIDORAN", 6)])
check("badge + catch + level together", set(kinds(evs)) == {"badge", "caught", "level"}, str(evs))

print("restart: a party seen for the first time is not news")
team = [{"name": "WARTORTLE", "level": 26, "hp": 70, "egg": False},
        {"name": "RATTATA", "level": 7, "hp": 22, "egg": False}]
prog = {"badges": 1, "dex_owned": 3}
check("no team before, progress present: says nothing",
      milestones(prog, prog, [], team) == [],
      str(milestones(prog, prog, [], team)))
check("but a genuine catch, with a team before, still counts",
      [k for k, _ in milestones(prog, prog, team, team + [
          {"name": "PIDGEY", "level": 3, "hp": 12, "egg": False}])] == ["caught"])

print("a cartridge switch must not leave a stale party behind")
check("Game.load clears the team and progress it was holding",
      "self.team_now, self.progress_now = None, None" in
      open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "flybrain", "game.py")).read().split("def load(")[1][:900],
      "see Game.load")

print("evolution is not a catch, and a garbled read is not news")
squirtle = [{"name": "SQUIRTLE", "level": 15, "hp": 40, "egg": False}]
wartortle = [{"name": "WARTORTLE", "level": 16, "hp": 40, "egg": False}]
ev = milestones(prog, prog, squirtle, wartortle)
check("SQUIRTLE -> WARTORTLE reads as an evolution",
      [k for k, _ in ev] == ["evolve"] and "EVOLVED" in ev[0][1], str(ev))
check("and a genuine new arrival is still a catch",
      [k for k, _ in milestones(prog, prog, squirtle,
                                squirtle + [{"name": "PIDGEY", "level": 3, "hp": 9,
                                             "egg": False}])] == ["caught"])
garbled = [{"name": "#22502", "level": 41, "hp": 4, "egg": False}] + squirtle
check("an unresolved species is ignored, not announced",
      milestones(prog, prog, squirtle, garbled) == [],
      str(milestones(prog, prog, squirtle, garbled)))

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
