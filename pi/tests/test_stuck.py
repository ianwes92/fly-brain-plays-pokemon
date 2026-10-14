"""When the fly is getting nowhere, the stream asks chat for help.

Measured before this was built: the fly changes tile about once a second in
normal play, but 11% of the time it is in a stretch with no movement at all,
some over 90 seconds. Those stretches are the dead air. What is tested here is
that the call fires on THOSE and not on ordinary play -- a prompt that shows
during normal walking is clutter, and one that never shows is pointless.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.playview import STUCK_S, stuck_call                # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


class Fly:
    def __init__(self, menu=None, still=None, battle=False):
        self.menu_since, self.still_since = menu, still
        self._battle = battle

    def in_battle(self):
        return self._battle


NOW = 1_000_000.0

print("1. normal play says nothing")
check("moving every second", stuck_call(Fly(still=NOW - 1000), NOW) is None)
check("a short menu visit", stuck_call(Fly(menu=NOW - 8000), NOW) is None)
check("nothing at all recorded yet", stuck_call(Fly(), NOW) is None)
check("just under the threshold stays quiet",
      stuck_call(Fly(still=NOW - (STUCK_S - 1) * 1000), NOW) is None)

print("2. a real stretch of nothing asks for help")
lost = stuck_call(Fly(menu=NOW - 40_000), NOW)
check("a long menu is called out", lost is not None and "MENU" in lost[0], str(lost))
check("and names the keys that back out of one",
      lost and "!b" in lost[1] and "!a" in lost[1], str(lost))
stuck = stuck_call(Fly(still=NOW - 40_000), NOW)
check("going nowhere is called out", stuck is not None and "NOWHERE" in stuck[0], str(stuck))
check("and asks for a direction",
      stuck and all(k in stuck[1] for k in ("!up", "!down", "!left", "!right")), str(stuck))
check("the call says how long it has been", "40s" in lost[0] and "40s" in stuck[0],
      f"{lost[0]} / {stuck[0]}")

print("3. a menu takes priority over standing still")
both = stuck_call(Fly(menu=NOW - 30_000, still=NOW - 90_000), NOW)
check("in a menu, ask for the menu keys, not directions", "MENU" in both[0], str(both))

print("4. the thresholds are the ones the measurement justified")
check("fires well above the 1s median between moves", STUCK_S >= 15, f"{STUCK_S}s")
check("but below the 90s+ stretches that lose viewers", STUCK_S <= 45, f"{STUCK_S}s")


print("5. a battle is not a menu")
# Watched live: the fly drifts onto RUN -- bottom right of the four -- and
# hammers it against a trainer, which cannot be run from. That is the longest
# dead air on the stream, and "press !b to back out" is wrong advice twice
# over: B does not leave a battle and RUN does not work there.
fight = stuck_call(Fly(menu=NOW - 40_000, battle=True), NOW)
check("a battle is called a battle", fight is not None and "BATTLE" in fight[0], str(fight))
check("it is not called a menu", fight is not None and "MENU" not in fight[0])
check("chat is pointed at FIGHT", fight is not None and "FIGHT" in fight[1])
check("chat is NOT told to back out", fight is not None and "back out" not in fight[1])
check("it still says how long", fight is not None and "40s" in fight[0])

menu_only = stuck_call(Fly(menu=NOW - 40_000, battle=False), NOW)
check("a real menu still says menu", menu_only is not None and "MENU" in menu_only[0])
check("and still says back out", menu_only is not None and "back out" in menu_only[1])
check("a battle that is not stuck yet says nothing",
      stuck_call(Fly(menu=NOW - 8000, battle=True), NOW) is None)

print("ALL PASS" if ok else "FAILURES ABOVE")
