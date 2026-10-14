"""GET /live for the panel's live view (obsfly, 2026-10-08)."""
import os, sys, json
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import playview as pv, game as gm

ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1

class NS:
    def __init__(self, **k): self.__dict__.update(k)

rate = np.arange(10, dtype=float)
groups = {"sc_optic": np.array([1, 3]), "kc": np.array([4]), "sc_motor": np.array([9])}
g = NS(hands=NS(heading=1, bout=True, stats={"left": 3, "right": 4, "a": 5, "b": 6}),
       brain=NS(rate=rate, t_ms=10_000.0), admin=False, taken_over=False, chat_keys=0,
       fly_keys=gm.LEFT, _override_keys=0, mood=NS(valence=-0.25, label="uneasy"),
       nose=NS(level={"people": 0.4, "exits": 0.0}), team_calm=[{"name": "SQUIRTLE", "level": 12}],
       progress_calm={"badges": 0, "dex_owned": 1}, menu_since=None, battle=False,
       eyes=NS(rates={"pain": 0.0, "eye": 80.0}), place=101)
out = pv.live_snapshot(g, groups, "wandering - turning left", 12.34, now=1.0)
json.dumps(out)
check("regions fresh from the brain", out["regions"] == {"eyes": 2.0, "kc": 4.0, "motor": 9.0})
check("the fly's key", out["keys"] == "left" and out["by"] == "fly")
check("heading in degrees", out["heading"] == 90 and out["walking"] is True)
check("mood and smell", out["mood"] == {"valence": -0.25, "label": "uneasy", "smell_people": 0.4, "smell_exits": 0.0})
check("counters, place, lead", out["turns"] == {"left": 3, "right": 4, "a": 5, "b": 6}
      and out["place"] == "Route 1" and out["lead"] == "SQUIRTLE Lv 12")
check("still and thought", out["still_s"] == 12.3 and out["thinking"].startswith("wandering"))
g.chat_keys = gm.UP
check("cached for 0.25 s", pv.live_snapshot(g, groups, now=1.1) is out)
out2 = pv.live_snapshot(g, groups, now=2.0)
check("chat's key", out2["keys"] == "up" and out2["by"] == "chat")
g.menu_since, g.battle = 4_000.0, True
out3 = pv.live_snapshot(g, groups, now=3.0)
check("in battle: no heading, menu seconds", out3["heading"] is None and out3["menu_s"] == 6.0 and out3["battle"])
print(f"test_live: {ok} checks passed")

# the GPU brain layer's spike bitset (2026-10-09)
import base64, tempfile
lp = os.path.join(tempfile.mkdtemp(), "layout.json")
json.dump({"neurons": [{"i": 0, "_n": 5}, {"i": 1, "_n": 2}, {"i": 2, "_n": 9}]}, open(lp, "w"))
sb = pv.SpikeBits(10, lp)
sb.add([np.array([2, 3]), np.array([9])])
bits = np.unpackbits(np.frombuffer(base64.b64decode(sb.take()), dtype=np.uint8), bitorder="little")[:3]
check("spikes map to sample indices", list(bits) == [0, 1, 1])
check("and are cleared by taking", not np.unpackbits(np.frombuffer(base64.b64decode(sb.take()), dtype=np.uint8)).any())
check("no layout: no spk", pv.SpikeBits(10, lp + "x").take() is None)
print(f"test_live: {ok} checks passed (incl. spike bits)")
