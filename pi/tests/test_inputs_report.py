"""Per-viewer report: counts, refusals, pain-only and spammy flags, delivered
stims folded into the requester, the fly's own vs assisted progress."""
import json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "play"))
import inputs_report as r

ok = 0
def check(name, cond):
    global ok
    assert cond, name
    ok += 1

T = 1791400000.0
recs = []
for i in range(6):
    recs.append({"t": T + i * 60, "user": "painguy", "kind": "free_stim", "key": "pain",
                 "outcome": "ok" if i % 2 == 0 else "cooldown_user", "name": "PainGuy"})
recs.append({"t": T, "user": "PainGuy", "kind": "stim_delivered", "key": "pain", "outcome": "human"})
recs.append({"t": T, "user": "the game", "kind": "stim_delivered", "key": "pleasure", "outcome": "game"})
for i in range(30):
    recs.append({"t": T + i, "user": "masher", "kind": "control", "key": "up",
                 "outcome": "paced" if i % 3 else "ok", "vs_fly": "against" if i % 2 else "together"})
recs.append({"t": T, "user": "helper", "kind": "control", "key": "left", "outcome": "ok", "vs_fly": "together"})
recs.append({"t": T, "user": "helper", "kind": "free_stim", "key": "pain", "outcome": "ok", "name": "Helper"})
recs.append({"t": T, "user": "rich", "kind": "steer", "key": None, "outcome": "queued", "seconds": 20.0})
recs.append({"t": T, "user": "rich", "kind": "paid_stim", "key": "pleasure", "outcome": "ok", "n": 4})
u = r.per_user(recs)
check("pain-only flagged", u["painguy"]["flags"] == ["pain-only"] and u["painguy"]["pain"] == 6)
check("refusals counted", u["painguy"]["refused"]["cooldown_user"] == 3 and u["painguy"]["accepted"] == 3)
check("delivered folded into login", u["painguy"]["delivered"]["pain"] == 1)
check("game stims not a user", "the game" not in u)
check("spammy flagged", "spammy" in u["masher"]["flags"] and "pain-only" not in u["masher"]["flags"])
check("vs fly", u["masher"]["vs_fly"]["against"] == 15)
check("one pain + steering is not pain-only", u["helper"]["flags"] == [])
check("paid stim n", u["rich"]["pleasure"] == 4 and u["rich"]["steer_s"] == 20.0)
check("rate", u["masher"]["per_min"] == 30.0)

fly = r.fly_summary([{"kind": "area", "text": "Route 2", "by": "fly"},
                     {"kind": "catch", "text": "CAUGHT X", "by": "assisted", "since_human_s": 1.0}])
check("fly summary", fly["by_fly"] == 1 and fly["assisted"] == 1)
txt = r.text_report(u, fly, 24)
check("text has flags", "painguy  [pain-only]" in txt and "1 on its own" in txt)

d = tempfile.mkdtemp()
import time
day = time.strftime("%Y-%m-%d", time.localtime(T))
with open(os.path.join(d, day + ".jsonl"), "w") as f:
    f.write(json.dumps(recs[0]) + "\nnot json\n" + json.dumps(dict(recs[0], t=T - 99999)) + "\n")
got = r.load(d, since=T - 10)
check("load filters time + skips junk", len(got) == 1)
check("empty folder", r.load(tempfile.mkdtemp()) == [] and r.load_progress(tempfile.mkdtemp()) == [])
print(f"test_inputs_report: {ok} checks passed")
