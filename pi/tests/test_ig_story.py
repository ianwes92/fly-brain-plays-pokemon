"""Story selection: the best milestone with a FINISHED Short since the last
Story, ranked badge > evolution > catch; nothing new means skip."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))
import ig_story as s

ok = 0
def check(name, cond):
    global ok
    assert cond, name
    ok += 1

d = tempfile.mkdtemp()
def clip(n, failed=False):
    p = os.path.join(d, n); open(p, "wb").write(b"x")
    if failed: open(p + ".failed", "w").write("x")
    return p

evs = [{"id": 20, "kind": "catch", "text": "CAUGHT PIDGEY!", "short": clip("a.mp4")},
       {"id": 21, "kind": "level", "text": "lv"},
       {"id": 22, "kind": "evolve", "text": "EVOLVED!", "short": clip("b.mp4")},
       {"id": 23, "kind": "catch", "text": "CAUGHT RATTATA!", "short": clip("c.mp4")},
       {"id": 24, "kind": "badge", "text": "BADGE!", "short": clip("d.mp4", failed=True)},
       {"id": 25, "kind": "faint", "text": "wiped", "short": clip("e.mp4")},
       {"id": 26, "kind": "catch", "text": "CAUGHT X!", "short": os.path.join(d, "missing.mp4")}]
check("an evolution beats catches", s.pick({"last_event": 0}, evs)["id"] == 22)
check("a badge whose Short failed is not picked", s.pick({"last_event": 0}, evs)["kind"] != "badge")
check("a wipe is never promotion", s.pick({"last_event": 24}, evs) is None)
check("only events since the last Story", s.pick({"last_event": 22}, evs)["id"] == 23)
check("a Short that does not exist is skipped", s.pick({"last_event": 25}, evs) is None)
check("nothing new: skip", s.pick({"last_event": 99}, evs) is None)
check("text fits the width", all(0.62 * len(t) * s.fit(t, 54) <= s.W - 80 + 1
      for t in ("WARTORTLE EVOLVED INTO BLASTOISE!", s.LINE1, "CAUGHT FARFETCH'D!")))
# -- Ian's rule (2026-10-05): 12:00 is guaranteed, 20:00 milestone-only -----
check("12:00 with a milestone posts it", s.decide(12, True, True, True) == "milestone")
check("12:00 with nothing falls back to current play", s.decide(12, False, True, True) == "fallback")
check("20:00 with nothing skips", s.decide(20, False, True, True) == "skip")
check("20:00 with a milestone posts it", s.decide(20, True, True, True) == "milestone")
check("12:00 with the game down skips WITH an alert", s.decide(12, True, False, True) == "skip-alert")
check("12:00 with Twitch offline skips with an alert", s.decide(12, False, True, False) == "skip-alert")
check("20:00 offline also skips with an alert", s.decide(20, True, True, False) == "skip-alert")
check("Twitch unknown (None) is not 'offline'", s.decide(12, False, True, None) == "fallback")
print(f"{ok} checks pass")
