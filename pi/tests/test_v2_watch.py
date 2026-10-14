"""The four feeds "Fly Brain v2" is drawn from are watched (2026-10-09)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))
import discord_poster as dp
alerts = []
state = {"ok": True}
def check():
    return {p: (state["ok"] or p != "/panel/overlay") for p in dp.V2_FEEDS}
w = lambda: dp.watch_v2({"x": 1}, alert=lambda *a: alerts.append(a), check=check)
assert dp.watch_v2(None, check=check) is None            # offline: nothing checked
w(); assert alerts == []
state["ok"] = False
w(); w(); assert alerts == []                             # two misses: quiet
w(); assert len(alerts) == 1 and "/panel/overlay" in alerts[0][1]
w(); assert len(alerts) == 1                              # once
state["ok"] = True
w(); assert len(alerts) == 2 and "back" in alerts[1][0]
calls = []
def fetch(url, auth):
    calls.append((url, auth))
    return (503, b"") if url.endswith("/game.png") and len(calls) == 1 else (200, b"")
res = dp.check_v2(base="http://x", token="T", fetch=fetch)
assert all(res.values()) and calls[0] == ("http://x/game.png", None) and calls[2][1] == "T", calls
print("test_v2_watch: 8 checks passed")
