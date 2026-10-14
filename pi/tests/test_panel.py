"""The control panel's door: LAN-only, token-checked, rate-limited, logged;
'take' is !menu's admin mode; panel presses stay off the leaderboard."""
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import panel as P
from flybrain import twitch as tw

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


T = "s3cret-token"
# -- the door -----------------------------------------------------------------------
check("LAN + right token: allowed", P.check("192.168.0.10", "Bearer " + T, T, now=1.0) is None)
check("a public address is refused", P.check("8.8.8.8", "Bearer " + T, T)[0] == 403)
check("a wrong token is refused", P.check("192.168.0.10", "Bearer nope", T)[0] == 401)
check("no token is refused", P.check("192.168.0.10", None, T)[0] == 401)
check("a token without 'Bearer' is refused", P.check("192.168.0.10", T, T)[0] == 401)
check("IPv4-mapped LAN addresses work", P.check("::ffff:10.0.0.5", "Bearer " + T, T, now=1.0) is None)
P._hits.clear()
res = [P.check("192.168.0.9", "Bearer " + T, T, now=5.0 + i * 0.001) for i in range(P.RATE + 2)]
check("more than RATE a second is throttled", res[:P.RATE] == [None] * P.RATE and res[P.RATE][0] == 429)
check("and recovers a second later", P.check("192.168.0.9", "Bearer " + T, T, now=7.0) is None)
d = tempfile.mkdtemp()
tf = os.path.join(d, "panel.json")
t1 = P.panel_token(tf)
check("a token is generated on first use", len(t1) >= 40)
check("and kept", P.panel_token(tf) == t1)
check("in a 0600 file", oct(os.stat(tf).st_mode & 0o777) == "0o600")

# -- the actions -----------------------------------------------------------------------
now = [0.0]
p = tw.PaidActions(clock=lambda: now[0])
check("press before take is refused", p.panel("press", "a") == (False, "take control first"))
okk, note = p.panel("take")
check("take = admin mode, with the banner", okk and note == tw.ADMIN_BANNER and p.admin[0] == "streamer")
check("and taps START, then says admin is on", p.tick()[:2] == [("admin", True, "taken"), ("tap", "start")])
check("take again is harmless", p.panel("take") == (True, "already held by the panel") and p.tick() == [])
check("a press is a tap", p.panel("press", "up") == (True, "streamer: UP") and p.tick() == [("tap", "up")])
check("START works while held", p.panel("press", "start")[0] and p.tick() == [("tap", "start")])
check("an unknown key is refused", p.panel("press", "select")[0] is False)
check("chat cannot press meanwhile", p.feed(("control", "a", "viewer")) is None and p.tick() == [])
check("release hands back", p.panel("release")[0] and ("admin", False, "released") in p.tick())
check("release when not held says so", p.panel("release")[0] is False)
check("nudge adds to the fly like chat", p.panel("nudge", "left")[0] and p.tick()[0][0] == "nudge")
check("START is never a nudge", p.panel("nudge", "start")[0] is False)
check("panel presses never reach the leaderboard", p.trainers == {} and p.drivers == {})
p.feed(("steer", 5.0, "payer", "Payer")); now[0] += 1; p.tick()
okk, note = p.panel("take")
check("refused while a viewer's paid steering runs", not okk and "Payer" in note)
now[0] += 10; p.tick()
p.feed(tw.interpret({"user": "examplechannel", "name": "examplechannel", "text": "!menu", "bits": 0,
                     "uid": next(iter(tw.ADMIN_IDS))}))
okk, note = p.panel("take")
check("refused while Ian holds !menu, and says so", not okk and "!menu" in note)

# -- stim: free-running, rate-limited, off the leaderboard -----------------------
now2 = [100.0]
q = tw.PaidActions(clock=lambda: now2[0])
check("stim needs no take", q.panel("stim", "pleasure") == (True, "streamer: PLEASURE"))
check("it goes through the same paced stim queue as chat", ("stim", "pleasure", "streamer") in q.tick())
check("a second stim within 1 s is refused", q.panel("stim", "pain")[0] is False)
now2[0] += 1.01
check("after a second it works", q.panel("stim", "pain")[0])
check("an unknown stim is refused", q.panel("stim", "tickle")[0] is False)
check("panel stims never reach the leaderboard", q.trainers == {} and q.drivers == {})
check("it shows in the on-screen feed as the streamer", any("streamer  PAIN" in t for t in q.recent(99)))
check("stim is advertised", "stim" in tw.PANEL_ACTIONS)

# -- the YouTube probe shares one answer per 120 s, so a loop cannot burn quota --------
P._yt.update(at=1000.0, out={"live": True, "broadcast_id": "B", "checked_at": 1000.0, "cached": False})
r = P.youtube_probe(now=1010.0)
check("a probe within 20 s reuses the last answer", r["cached"] and r["broadcast_id"] == "B")
check("and says so, with its real check time", r["checked_at"] == 1000.0)
check("a probe within 120 s still reuses it", P.youtube_probe(now=1110.0)["cached"])
P._yt.update(at=0.0, out=None)

# -- 2026-10-08: the probe's units are booked where the promoter counts them ----------
import tempfile as _tf
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))
import youtube_promo as _yp
_d = _tf.mkdtemp()
_saved = (_yp.PROBE_F, _yp.STATE_F, _yp.CFG_F, _yp.YouTube)
_yp.PROBE_F, _yp.STATE_F, _yp.CFG_F = (os.path.join(_d, n) for n in ("probe.json", "state.json", "cfg.json"))
_day = _yp.quota_day(5000.0)
with open(_yp.STATE_F, "w") as f:
    json.dump({"quota": {"day": _day, "used": 100}}, f)


class _FakeYT:
    def __init__(self, cfg, state, save):
        self.state = state

    def call(self, path, cost, cap, **kw):
        if not _yp.can_spend(self.state, cost, cap, 5000.0):
            raise RuntimeError("cap")
        _yp.charge(self.state, cost, 5000.0)
        return {"items": [{"status": {"streamStatus": "active"}}]}

    def live_broadcast(self):
        self.call("liveBroadcasts", 1, _yp.HARD_CAP)
        return {"id": "B", "status": {"lifeCycleStatus": "live"}}


_yp.YouTube = _FakeYT
try:
    r = P.youtube_probe(now=5000.0)
    check("a real probe spends 2 units and says so", r["units"] == 2 and r["receiving"])
    check("booked in PROBE_F for today", _yp.probe_spent(5000.0) == 2)
    with open(_yp.STATE_F) as f:
        check("the promoter's own state is untouched", json.load(f)["quota"]["used"] == 100)
    check("and the promoter's budget now sees them", not _yp.can_spend({"quota": {"day": _day, "used": 100}},
                                                                       _yp.HARD_CAP - 101, _yp.HARD_CAP, 5000.0))
    P._yt.update(at=0.0, out=None)
    P.youtube_probe(now=5000.0 + 200)
    check("a second probe adds to it", _yp.probe_spent(5000.0) == 4)
finally:
    _yp.PROBE_F, _yp.STATE_F, _yp.CFG_F, _yp.YouTube = _saved
    P._yt.update(at=0.0, out=None)

# -- tools.twitch (2026-10-08): read-only, cached, never refreshes --------------
import tempfile as _tf2
_cp = os.path.join(_tf2.mkdtemp(), "twitch.json")
json.dump({"oauth": "oauth:AAA", "client_id": "C"}, open(_cp, "w"))
calls = []


def _fake(url, headers):
    calls.append(url)
    if url.endswith("/validate"):
        return 200, {"expires_in": 9000}
    if "/streams?" in url:
        return 200, {"data": [{"viewer_count": 7, "title": "Run 4 · Fruit Fly", "started_at": "2026-10-08T12:00:00Z"}]}
    return 200, {"data": [{"title": "offline title"}]}


P._tw.update(at=0.0, out=None)
r = P.twitch_status("connected", now=10_000.0, cfg_path=_cp, fetch=_fake)
check("live with viewers, title, start, token time",
      (r["live"], r["viewers"], r["title"], r["started_at"], r["token_s"], r["chat"])
      == (True, 7, "Run 4 · Fruit Fly", "2026-10-08T12:00:00Z", 9000, "connected"))
check("no refresh call, ever", not any("token" in u.split("/")[-1] for u in calls))
n = len(calls)
r2 = P.twitch_status("disconnected", now=10_030.0, cfg_path=_cp, fetch=_fake)
check("cached for 60 s, chat state still current", len(calls) == n and r2["chat"] == "disconnected")
P._tw.update(at=0.0, out=None)
r3 = P.twitch_status(None, now=20_000.0, cfg_path=_cp, fetch=lambda u, h: (401, {}))
check("a dead token says so instead of failing", r3["token_s"] is None and "401" in r3["why"] and r3["live"] is None)
# the end-to-end relay test below must not reach Twitch: a cached answer that never expires
P._tw.update(at=float("inf"), out={"live": None, "title": None, "token_s": None, "chat": None})

# -- "chat": exactly a chat !command from the broadcaster ------------------------
now3 = [500.0]
c = tw.PaidActions(clock=lambda: now3[0])
tw_login = tw.broadcaster_login()
okk, note = c.panel("chat", "left")
check("chat works without take", okk and note == f"{tw_login}: LEFT")
act = c.tick()
check("it is a nudge alongside the fly, credited to the broadcaster", act and act[0][:3] == ("nudge", "left", tw_login))
check("it counts like a chat press", c.presses == 1 and c.last_press[1] == tw_login)
check("no pace: presses can be spammed, as in chat (Ian, 2026-10-08)", c.panel("chat", "up")[0] is True)
c.feed(("control", "up", tw_login))
check("and right after his own chat press too", c.panel("chat", "down")[0] is True)
check("START outside !menu is refused, as in chat", c.panel("chat", "start")[0] is False)
check("presses never reach the leaderboard", c.trainers == {} and c.drivers == {})
check("chat is advertised", "chat" in tw.PANEL_ACTIONS)

# -- end to end: a test relay in front of a fake game ---------------------------------
seen = []


class FakeGame(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        body = b"ok frames=5\nphase=playing title=auto emu=123\n"
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        req = json.loads(self.rfile.read(n))
        seen.append((self.client_address[0], req))
        body = json.dumps({"ok": True, "note": "fake " + req["action"]}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers()
        self.wfile.write(body)


game = HTTPServer(("127.0.0.1", 0), FakeGame)
threading.Thread(target=game.serve_forever, daemon=True).start()
home = tempfile.mkdtemp()
os.makedirs(os.path.join(home, ".config", "flybrain")); os.makedirs(os.path.join(home, "flybrain", "play"))
tok = "end-to-end-token-0123456789abcdef0123456789"
json.dump({"token": tok}, open(os.path.join(home, ".config", "flybrain", "panel.json"), "w"))
port = 8797
env = dict(os.environ, HOME=home, PYTHONPATH=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
relay = subprocess.Popen([sys.executable, "-m", "flybrain.relay", str(port), f"http://127.0.0.1:{game.server_address[1]}"],
                         env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
try:
    time.sleep(2.5)

    def call(method, path, body=None, token=tok):
        h = {"Content-Type": "application/json"}
        if token:
            h["Authorization"] = "Bearer " + token
        r = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method=method, headers=h,
                                   data=json.dumps(body).encode() if body is not None else None)
        try:
            with urllib.request.urlopen(r, timeout=10) as x:
                return x.status, json.loads(x.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    code, out = call("GET", "/panel/status")
    check("status answers with the token", code == 200 and out["game"]["phase"] == "playing"
          and out["game"]["emu"] == "123")
    check("status reports the tools", set(out["tools"]) >= {"discord", "youtube", "instagram", "threads", "watchdog"})
    check("status advertises capabilities", out.get("capabilities") == list(tw.PANEL_ACTIONS))
    code, o2 = call("POST", "/panel/control", {"action": "stim", "kind": "pain"})
    check("'kind' is passed through as the stim kind", seen[-1][1] == {"action": "stim", "key": "pain"})
    check("status refuses without the token", call("GET", "/panel/status", token=None)[0] == 401)
    code, out = call("POST", "/panel/control", {"action": "take", "extra": "dropped"})
    check("control is passed to the game", code == 200 and out == {"ok": True, "note": "fake take"})
    check("from localhost, with only action and key", seen[-1][0] == "127.0.0.1"
          and seen[-1][1] == {"action": "take", "key": None})
    check("control refuses a bad token", call("POST", "/panel/control", {"action": "take"}, token="x")[0] == 401)
    log = open(os.path.join(home, "flybrain", "play", "panel.log")).read()
    check("control actions are logged with the source address", "127.0.0.1" in log and '"take"' in log)
finally:
    relay.terminate(); relay.wait(5); game.shutdown()
print(f"{ok} checks pass")
