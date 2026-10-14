"""Attribution: a frame is the fly's own only with no human key for CLEAN_MS;
tiles/bumps/progress split clean vs assisted; stims split by source; the
journal records every input with context and never raises."""
import json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from flybrain.attrib import Attribution, Journal, relation, CLEAN_MS
from flybrain.game import UP, DOWN, LEFT, RIGHT, A
from flybrain import playview as pv
from flybrain.events import EventLog

ok = 0
def check(name, cond):
    global ok
    assert cond, name
    ok += 1

a = Attribution()
a.frame(0, "fly"); a.moved()
check("never touched -> clean", a.tiles["clean"] == 1 and a.credit()["by"] == "fly")
check("since None", a.since_human_s() is None and a.credit()["since_human_s"] is None)
a.frame(1000, "nudge"); a.moved(); a.bumped()
check("nudge frame assisted", a.tiles["assisted"] == 1 and a.bumps["assisted"] == 1)
a.frame(1000 + CLEAN_MS - 1, "fly"); a.moved()
check("inside the window still assisted", a.tiles["assisted"] == 2)
check("credit assisted", a.credit()["by"] == "assisted")
a.frame(1000 + CLEAN_MS, "fly"); a.moved(); a.bumped()
check("after the window clean", a.tiles["clean"] == 2 and a.bumps["clean"] == 1)
check("since_human_s", abs(a.since_human_s() - CLEAN_MS / 1000) < 1e-9)
for st in ("driver", "admin"):
    a.frame(20000, st); a.moved()
    check(f"{st} assisted", a.credit()["by"] == "assisted" and a.credit()["control"] == st)
check("frames by state", a.frames["fly"] == 3 and a.frames["nudge"] == 1
      and a.frames["driver"] == 1 and a.frames["admin"] == 1)
check("clean frames", a.frames["clean"] == 2)
try:
    a.frame(0, "bogus"); check("bad state raises", False)
except ValueError:
    check("bad state raises", True)

b = Attribution()
for i in range(100):
    b.frame(i * 100, "nudge" if i < 25 else "fly")
check("human share", abs(b.human_share() - 0.25) < 1e-9)
c = Attribution()
c.frame(0, "nudge")
for i in range(1, 400):
    c.frame(i * 1000, "fly")
check("share forgets after 5 min", c.human_share() == 0.0)

d = Attribution()
d.stim("pain", True); d.stim("pain", False); d.stim("pleasure", True); d.stim("bogus", True)
check("stims by source", d.stims == {"human_pain": 1, "human_pleasure": 1, "game_pain": 1, "rule_pain": 0,
                                     "game_pleasure": 0})
h = d.health()
check("health fields", h.startswith("attrib: fly_frames=0") and "tiles_clean=0" in h
      and "human_pain=1" in h and "since_human_s=-" in h)

check("together", relation("up", UP) == "together")
check("against", relation("up", DOWN) == "against")
check("apart", relation("left", UP) == "apart")
check("diagonal-ish apart", relation("left", UP | RIGHT) == "apart")
check("fly idle", relation("up", A) == "fly_idle")
check("non-direction", relation("a", UP) is None)

tmp = tempfile.mkdtemp()
j = Journal(folder=os.path.join(tmp, "inputs"), clock=lambda: 1791400000.0)
j.write({"user": "x", "kind": "control"})
j.progress({"kind": "area", "text": "Route 2", "by": "fly"})
lines = open(j.path(1791400000.0)).read().splitlines()
check("journal line", json.loads(lines[0]) == {"user": "x", "kind": "control", "t": 1791400000.0})
check("progress file", os.path.exists(os.path.join(tmp, "inputs", "progress.jsonl")))
bad = Journal(folder="/proc/nope")
bad.write({"user": "x"})
check("journal never raises", bad.errors == 1)

class G:
    place, battle, fly_keys = 89, False, DOWN
    attrib = Attribution()
r = pv._event_record(G, ("control", "up", "spammer"), "ok")
check("control record", r["user"] == "spammer" and r["key"] == "up" and r["vs_fly"] == "against"
      and r["place"] == 89 and r["outcome"] == "ok")
r = pv._event_record(G, ("free", "pain", "spammer", "Spammer"), "cooldown_user")
check("free stim record", r["kind"] == "free_stim" and r["key"] == "pain"
      and r["outcome"] == "cooldown_user" and "vs_fly" not in r)
check("paid stim", pv._event_record(G, ("stim", "pain", 3, "Big"), "ok")["n"] == 3)
check("steer", pv._event_record(G, ("steer", 12.5, "u", "U"), "queued")["seconds"] == 12.5)
check("cheer", pv._event_record(G, ("cheer", 50, "U"), "ok")["bits"] == 50)
check("admin", pv._event_record(G, ("admin", True, "me", "Me", "1"), "admin")["key"] == "on")
check("unknown kind kept", pv._event_record(G, ("weird", 1), None)["raw"] == ["weird", "1"])

ev = EventLog(path=os.path.join(tmp, "events.json"))
ev.add("catch", "CAUGHT PIDGEY!", credit={"by": "assisted", "since_human_s": 2.0})
check("event carries credit", ev.events[-1]["by"] == "assisted")
ev.add("catch", "CAUGHT RATTATA!")
check("no credit, no field", "by" not in ev.events[-1])

# the feed outcomes the journal reads
from flybrain.twitch import PaidActions
t = [0.0]
p = PaidActions(clock=lambda: t[0])
p.feed(("control", "up", "u")); check("ok", p.last_outcome == "ok")
p.feed(("control", "up", "u")); check("spam is allowed (Ian, 2026-10-08)", p.last_outcome == "ok")
p.feed(("free", "pain", "u", "U")); check("free ok", p.last_outcome == "ok")
p.feed(("free", "pain", "u", "U")); check("cooldown user", p.last_outcome == "cooldown_user")
p.feed(("steer", 10, "w", "W")); check("queued", p.last_outcome == "queued")
t[0] = 1.0
p.feed(("control", "up", "u")); check("refused driver", p.last_outcome == "refused_driver")
p.feed(("control", "up", "w")); check("ok driver", p.last_outcome == "ok_driver")

print(f"test_attrib: {ok} checks passed")
