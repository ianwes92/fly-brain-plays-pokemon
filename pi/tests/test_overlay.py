"""/overlay (everything the window draws, as data) and the bare game stream (2026-10-09)."""
import json, os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import playview as pv, game as gm, stream

ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1

class NS:
    def __init__(self, **k): self.__dict__.update(k)

class Paid:
    trainers = {"bob": 3, "ann": 5}
    chat_log = [(1.0, "bob  LEFT"), (2.0, "ann  A")]
    def chat_status(self): return (True, ("left", "bob", 1.0), 2, {"left": 2})

hands = NS(people=0.0, exits=0.0, obstacle=0.0, people_thresh=1.0, exit_thresh=1.0, obstacle_thresh=1.0)
g = NS(team_now=[{"name": "CHARMANDER", "nickname": "CHARMANDER", "level": 15, "hp": 30, "max_hp": 38, "egg": False}],
       team_calm=None, progress_now={"badges": 0, "dex_owned": 2, "dex_seen": 9}, progress_calm=None,
       hands=hands, pull="together")
ui = {"banner": ("RUN 5: THE FLY PICKED BULBASAUR!", 10), "call": ("THE FLY IS GOING NOWHERE (40s)", "press !up"),
      "ticker": [("bob: PAIN", 5)], "paid": Paid(), "diary": NS(entries=[[1.0, "caught", "CAUGHT RATTATA!"]]),
      "science": NS(own=(1000, 0.9), trainers=[("bob", 12.3)]),
      "learning": NS(ledger={"outstanding": {"game": 30.0, "rule": 10.0}}),
      "stim": {"kind": "pain", "source": "human", "t": 5.0}, "thought": "wandering", "still_s": 12.0,
      "story": ["6 AREAS EXPLORED"]}
o = pv.overlay_snapshot(g, ui, now=1.0)
json.dumps(o)
check("team", o["team"][0] == {"species": "CHARMANDER", "nickname": "CHARMANDER", "lv": 15, "hp": 30, "hp_max": 38, "egg": False})
check("progress", o["progress"] == {"badges": 0, "caught": 2, "seen": 9})
check("banner and call", o["banner"]["text"].startswith("RUN 5") and o["call"]["ask"] == "press !up")
check("bar", o["bar"]["headline"] == "bob: LEFT" and o["bar"]["rotating"] and o["bar"]["now"] in o["bar"]["rotating"])
check("seeing rows", [s["label"] for s in o["seeing"]] == ["WALLS", "PEOPLE", "DOORS"])
check("science", o["science"]["steps_today"] == 1000 and o["science"]["taught"] == {"game": 0.75, "rule": 0.25})
check("feeds", o["chat_feed"][-1]["text"] == "ann  A" and o["leaderboard"][0] == ("ann", 5) or o["leaderboard"][0] == ["ann", 5])
check("cached 0.5 s", pv.overlay_snapshot(g, ui, now=1.2) is o)

fs = stream.FrameServer(port=0)
fs.publish_game(np.zeros((160, 240, 3), np.uint8))
check("no watcher, no encoding", fs._game_png is None)
fs._game_watchers = 1
fs.publish_game(np.zeros((160, 240, 3), np.uint8))
check("watched: a PNG", fs._game_png[:8] == b"\x89PNG\r\n\x1a\n" and fs._game_count == 1)
print(f"test_overlay: {ok} checks passed")

# opinions + experiment (added 2026-10-09)
import tempfile, time
g.mood = NS(opinion={"people": -0.61, "exits": 0.05, "x": 1})
pv._overlay_cache.update(at=-1, out=None)
o = pv.overlay_snapshot(g, ui, now=5.0)
check("opinions", o["opinions"] == {"people": -0.61, "exits": 0.05})
np_ = os.path.join(tempfile.mkdtemp(), "notice.json")
json.dump({"name": "X", "start": "2026-10-09T00:30:00", "posted": {}}, open(np_, "w"))
t0 = time.mktime(time.strptime("2026-10-09T00:30:00", "%Y-%m-%dT%H:%M:%S"))
check("pending", pv._experiment_now(np_, t0 - 60)["status"] == "pending")
check("running", pv._experiment_now(np_, t0 + 60)["status"] == "running")
json.dump({"name": "X", "start": "2026-10-09T00:30:00", "posted": {"result": t0 + 9000}}, open(np_, "w"))
check("done", pv._experiment_now(np_, t0 + 10000)["status"] == "done")
check("gone a day after", pv._experiment_now(np_, t0 + 9000 + 90000) is None)
print(f"test_overlay: {ok} checks passed (incl. opinions/experiment)")

# chat_feed ts (epoch) and paid stims in the feed (2026-10-09)
from flybrain.twitch import PaidActions
pa = PaidActions(clock=lambda: 100.0)
pa.feed(("stim", "pain", 3, "Carol"))
check("a cheered stim shows in the chat feed", list(pa.chat_log)[-1][1] == "Carol  PAIN x3")
Paid.clock = lambda self: 50.0
pv._overlay_cache.update(at=-1, out=None)
o = pv.overlay_snapshot(g, ui, now=9.0)
check("ts is epoch", abs(o["chat_feed"][-1]["ts"] - (time.time() - 48.0)) < 2)
print(f"test_overlay: {ok} checks passed (incl. chat feed)")

# per-press vs and pressed/pressed_by (2026-10-13)
log = [(49.0, "bob", "left", "against"), (49.5, "Payer", "up", "full")]
check("vs matched by who/key/time", pv._press_vs(log, "bob  LEFT", 49.2) == "against")
check("full for a driver", pv._press_vs(log, "Payer  UP", 49.6) == "full")
check("stims have none", pv._press_vs(log, "bob  PAIN", 49.2) is None)
g2 = NS(**{**g.__dict__})
g2.sent_keys, g2.fly_keys, g2.chat_keys = gm.LEFT | gm.A, gm.A, gm.LEFT
g2.hands, g2.brain = NS(heading=1, bout=True, stats={}), NS(rate=np.arange(10.0), t_ms=1.0)
g2.admin = g2.taken_over = False; g2._override_keys = 0; g2.menu_since = None; g2.battle = False
g2.eyes = NS(rates={}); g2.place = 101; g2.mood = None; g2.nose = None
pv._live_cache.update(at=-1, out=None)
lv = pv.live_snapshot(g2, {}, now=50.0)
check("pressed and by both", lv["pressed"] == "a" and lv["pressed_by"] == "both")
print(f"test_overlay: {ok} checks passed (incl. vs/pressed)")
