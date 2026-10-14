"""Motivation bookkeeping, driven by synthetic game events."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.motivation import Motivation
ok = True
def check(name, cond, extra=""):
    global ok; ok &= bool(cond); print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}")

print("1. urges grow with time and reset when acted on")
m = Motivation()
m.on_map(0, (1, 1))
check("leave urge starts at 0", m.leave_urge(0) == 0)
check("leave urge ~0.63 after 60 s", abs(m.leave_urge(60000) - 0.632) < 0.01, f"{m.leave_urge(60000):.3f}")
check("talk urge ~0.63 after 90 s", abs(m.talk_urge(90000) - 0.632) < 0.01, f"{m.talk_urge(90000):.3f}")
m.on_map(60000, (1, 2))
check("leave urge resets on a new map", m.leave_urge(60000) == 0)
m.on_talk(90000, ("npc", 1))
check("talk urge resets after talking", m.talk_urge(90000) == 0)

print("2. leaving is pleasure, sized by the urge")
m = Motivation(); m.on_map(0, (1, 1)); m.on_map(60000, (1, 2))
check("pleasure burst", m.pleasure(60100) and not m.pain(60100))
check("level ~ urge", abs(m.pleasure_level - 0.632) < 0.01, f"{m.pleasure_level:.2f}")
check("burst ends", not m.pleasure(61000))

print("3. going straight back is pain; coming back later is not")
m = Motivation(); m.on_map(0, (1, 1)); m.on_map(30000, (1, 2)); m.on_map(40000, (1, 1))
check("back within 20 s -> pain", m.pain(40100) and m.counts["pain"] == 1)
m = Motivation(); m.on_map(0, (1, 1)); m.on_map(30000, (1, 2)); m.on_map(80000, (1, 1))
check("back after 50 s -> pleasure", m.pleasure(80100) and m.counts["pain"] == 0)
m = Motivation(); m.on_map(0, (1, 1)); m.on_map(30000, (1, 2)); m.on_map(35000, (1, 3))
check("onward to a different map quickly -> pleasure", m.counts["pain"] == 0)

print("4. mashing A through one dialogue is one conversation")
m = Motivation()
for t in range(10000, 16000, 250):
    m.on_talk(t, ("npc", 1))
check("one talk counted", m.counts["talks"] == 1, f"{m.counts['talks']}")

print("5. the third conversation with the same person within 2 min is pain")
m = Motivation()
m.on_talk(10000, "A"); m.on_talk(40000, "A")
check("two talks: both pleasure", m.counts["pleasure"] == 2 and m.counts["pain"] == 0)
m.on_talk(70000, "A")
check("third within 2 min: pain", m.counts["pain"] == 1 and m.pain(70100))
m.on_talk(75000, "B")
check("a different person: pleasure", m.counts["pleasure"] == 3)
m2 = Motivation(); m2.on_talk(0, "A"); m2.on_talk(100000, "A"); m2.on_talk(230000, "A")
check("three talks spread over 230 s: no pain", m2.counts["pain"] == 0)

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
