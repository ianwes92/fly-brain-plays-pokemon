"""Stillness rule (Ian 2026-10-07): 60 s still -> pain, then every 10 s until
it moves; never while a human holds the controls; 60 s again after they let go."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.playview import StillPain
from flybrain.attrib import Attribution

ok = 0
def check(name, cond):
    global ok
    assert cond, name
    ok += 1

def run(p, seq, blocked=()):
    return [s for s in seq if p.check(s, blocked=s in blocked)]

p = StillPain()
fired = run(p, [None] + [x * 0.5 for x in range(0, 200)])        # 0..99.5 s still
check("first at 60, then every 10", fired == [60.0, 70.0, 80.0, 90.0])
check("count", p.fired == 4)
check("moving resets", run(p, [None, 5.0, 30.0, 59.5]) == [])
check("...and 60 again", run(p, [60.0]) == [60.0])
q = StillPain()
check("new stretch (counter dropped) resets", run(q, [55.0, 61.0, 3.0, 50.0, 62.0]) == [61.0, 62.0])
r = StillPain()
seq = [x * 1.0 for x in range(0, 140)]
blocked = set(x * 1.0 for x in range(30, 80))                  # a human held control 30..79 s
f = run(r, seq, blocked)
check("blocked: nothing while held", all(x not in blocked for x in f))
check("exactly 60 after the last held second", f[0] == 139.0)
check("None never fires", not StillPain().check(None))
a = Attribution()
a.stim("pain", human=False, source="rule")
check("rule pain counted apart", a.stims["rule_pain"] == 1 and a.stims["game_pain"] == 0)
check("health shows it", "rule_pain=1" in a.health())
print(f"test_still_pain: {ok} checks passed")


# Ian, 2026-10-08: a dialogue box PAUSES the clock; it runs on afterwards.
def _pause_checks():
    p = StillPain()
    fired = [t for t in range(0, 200) if p.check(float(t), paused=20 <= t < 120)]
    # 20 s before the box, 100 s paused, then 40 more: first pain at 160
    assert fired[0] == 160, fired
    assert fired[1] == 170, fired
    assert round(p.paused_s) == 100, p.paused_s
    p2 = StillPain()
    assert not any(p2.check(float(t), paused=True) for t in range(0, 500))
    assert p2.line().startswith("still_rule: fired=0 paused_dialogue_s=499 dialogue_now=True")
    # moving resets as before, paused or not
    p3 = StillPain()
    for t in range(0, 50):
        p3.check(float(t), paused=t < 30)
    p3.check(None)
    assert [t for t in range(0, 100) if p3.check(float(t))][0] == 60
    print("test_still_pain: dialogue pause checks passed")


_pause_checks()


def _shadow_line():
    a, b = StillPain(), StillPain()
    for t in range(0, 130):
        a.check(float(t), paused=t < 100)
        b.check(float(t))
    assert a.fired == 0 and b.fired == 7, (a.fired, b.fired)
    assert a.line(b).endswith(" without_pause=7"), a.line(b)
    assert "without_pause" not in a.line()
    print("test_still_pain: shadow line checks passed")


_shadow_line()


# Ian, 2026-10-08: the stillness clock runs in the overworld only.
def _overworld_only():
    from flybrain.playview import OverworldStill, StillPain as SP

    class G:
        menu_since = None
        still_since = None
    g, ow, rule = G(), OverworldStill(), SP()
    g.still_since = 0.0                       # stopped on a grass tile at t=0
    assert ow.update(g, 5_000) == 5.0
    g.menu_since = 6_000                      # a wild battle starts
    fired = [t for t in range(6_000, 200_000, 1000) if rule.check(ow.update(g, t))]
    assert fired == [], fired                 # 194 s of battle: no pain
    assert ow.update(g, 100_000) is None
    g.menu_since = None                       # back in the world, still on the same tile
    assert ow.update(g, 200_000) == 0.0       # a fresh clock, not 200 s
    assert ow.update(g, 230_000) == 30.0
    first = [t for t in range(200_000, 300_000, 1000) if rule.check(ow.update(g, t))][0]
    assert first == 260_000, first            # 60 s after coming back
    print("test_still_pain: overworld-only checks passed")


_overworld_only()
