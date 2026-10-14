"""Twitch chat bridge, paid with Bits: parsing, prices, queues, and a live run.

No real Twitch involved: a local server speaks the same tagged IRC lines
Twitch does -- including cheers carrying a bits= tag and a PING the client
must answer -- and the bridge must turn them into exactly the paid actions
the viewers bought, and nothing else.
"""
import json
import os
import socket
import sys
import tempfile
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import twitch                                             # noqa: E402
from flybrain.twitch import ChatBridge, PaidActions, interpret, parse   # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


def cheer_line(user, bits, text, display=None):
    display = display or user.capitalize()
    return (f"@badge-info=;badges=bits/100;bits={bits};color=;display-name={display};"
            f"emotes=;id=abc;mod=0;room-id=1;subscriber=0;tmi-sent-ts=1;turbo=0;user-id=9;"
            f"user-type= :{user}!{user}@{user}.tmi.twitch.tv PRIVMSG #flychannel :{text}")


print("1. parsing, with and without Twitch tags")
m = parse(cheer_line("alice", 300, "cheer300 pain", display="Alice\\sB"))
check("bits read from the tag", m["bits"] == 300, str(m))
check("display name unescaped", m["name"] == "Alice B", m["name"])
check("login lower-cased", m["user"] == "alice")
m2 = parse(":bob!bob@bob.tmi.twitch.tv PRIVMSG #flychannel :!left")
check("untagged chat still parses, 0 bits", m2 and m2["bits"] == 0 and m2["text"] == "!left")
check("PING is not chat", parse("PING :tmi.twitch.tv") is None)
check("bits in the TEXT are not bits", parse(":e!e@e PRIVMSG #c :cheer1000 pain")["bits"] == 0)

print("2. what a cheer buys")
ev = lambda bits, text, user="alice": interpret({"user": user, "name": user.title(), "text": text, "bits": bits})
check("cheer100 pain -> one press", ev(100, "cheer100 pain") == ("stim", "pain", 1, "Alice"))
check("cheer500 pleasure -> five", ev(500, "cheer500 pleasure") == ("stim", "pleasure", 5, "Alice"))
check("cheer250 shock -> two (remainder unused)", ev(250, "cheer250 shock") == ("stim", "pain", 2, "Alice"))
check("cheer50 pain -> below the price, just a cheer", ev(50, "cheer50 pain") == ("cheer", 50, "Alice"))
check("cheer50 steer -> 5 s of control", ev(50, "cheer50 steer") == ("steer", 5.0, "alice", "Alice"))
check("cheer100 with no keyword -> just a cheer", ev(100, "cheer100 go fly go") == ("cheer", 100, "Alice"))
check("free !pain is a free press", ev(0, "!pain") == ("free", "pain", "alice", "Alice"))
check("free !pleasure too", ev(0, "!pleasure") == ("free", "pleasure", "alice", "Alice"))
check("!treat is an alias", ev(0, "!treat") == ("free", "pleasure", "alice", "Alice"))
check("a cheer naming pain still buys presses", ev(300, "cheer300 pain") == ("stim", "pain", 3, "Alice"))
check("!left with no bits -> a control request", ev(0, "!left") == ("control", "left", "alice"))
check("ordinary chat ignored", ev(0, "hello fly") is None)

print("3. paid bookkeeping (fake clock)")
now = [100.0]
pa = PaidActions(clock=lambda: now[0])
pa.feed(("stim", "pain", 3, "Alice"))
first = pa.tick()
check("first queued press goes at once", first == [("stim", "pain", "Alice")], str(first))
check("the next waits for the gap", pa.tick() == [])
now[0] += twitch.STIM_GAP_S
check("...then comes", pa.tick() == [("stim", "pain", "Alice")])
check("one still queued", pa.status()[3] == 1)
pa.feed(("control", "left", "bob"))
check("nobody has bought control: anyone's key nudges the fly",
      pa.tick() == [("nudge", "left", "bob", twitch.PRESS_MS)])
now[0] += twitch.STIM_GAP_S
pa.tick()
pa.feed(("steer", 5.0, "bob", "Bob"))
pa.feed(("steer", 3.0, "carol", "Carol"))
d = pa.status()
check("first buyer drives, second waits", d[0] == "Bob" and d[2] == 1, str(d))
pa.feed(("control", "left", "bob"))
check("the driver's command goes through", ("control", "left", "Bob") in pa.tick())
pa.feed(("control", "up", "carol"))
check("someone else's is ignored", pa.tick() == [])
now[0] += 0.05
pa.feed(("control", "right", "bob"))
check("the driver may spam too (no pace since 2026-10-08)", ("control", "right", "Bob") in pa.tick())
pa.feed(("steer", 10.0, "bob", "Bob"))
check("buying again while driving adds time", abs(pa.status()[1] - 14.95) < 0.01, f"{pa.status()[1]:.2f}s")
now[0] += 15.0
d = pa.status()
check("when time runs out the next in line drives", d[0] == "Carol" and d[2] == 0, str(d))
pa.feed(("control", "up", "carol"))
check("...and her commands now work", ("control", "up", "Carol") in pa.tick())
now[0] += 3.1
check("line empty: nobody steering", pa.status()[0] is None)

print("3b. free presses: cooldowns, and dropped rather than queued")
now2 = [500.0]
fp = PaidActions(clock=lambda: now2[0])
fp.feed(("free", "pain", "alice", "Alice"))
check("first free press goes through", fp.tick() == [("stim", "pain", "Alice")])
fp.feed(("free", "pain", "bob", "Bob"))
now2[0] += twitch.STIM_GAP_S
check("no shared gap since 2026-10-08: another viewer gets through", fp.tick() == [("stim", "pain", "Bob")])
fp.feed(("free", "pain", "alice", "Alice"))
check("but one viewer's own cooldown still holds", fp.tick() == [] and fp.status()[3] == 0)
fp.feed(("free", "pleasure", "bob", "Bob"))
check("same viewer, other kind: still their own cooldown", fp.tick() == [])
now2[0] += twitch.FREE_USER_S
fp.feed(("free", "pleasure", "bob", "Bob"))
check("after their own cooldown, allowed", fp.tick() == [("stim", "pleasure", "Bob")])
fp.feed(("stim", "pain", 2, "Carol"))
now2[0] += twitch.STIM_GAP_S
check("cheered presses are still queued, not dropped",
      fp.tick() == [("stim", "pain", "Carol")] and fp.status()[3] == 1)

print("4. live bridge against a fake Twitch server")
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0)); srv.listen(1)
port = srv.getsockname()[1]
got = {"login": [], "pong": False}


def server():
    c, _ = srv.accept()
    c.settimeout(3.0)
    buf = b""
    while buf.count(b"\r\n") < 4:                 # CAP, PASS, NICK, JOIN
        buf += c.recv(1024)
    got["login"] = buf.decode().split("\r\n")[:4]
    c.sendall(b"PING :tmi.twitch.tv\r\n")
    try:
        got["pong"] = b"PONG :tmi.twitch.tv" in c.recv(1024)
    except socket.timeout:
        pass
    for line in (cheer_line("viewer1", 200, "cheer200 pain"),
                 cheer_line("viewer2", 30, "cheer30 steer"),
                 ":viewer2!v@v PRIVMSG #flychannel :!up",
                 ":viewer4!v@v PRIVMSG #flychannel :!pleasure",
                 ":viewer3!v@v PRIVMSG #flychannel :just chatting",
                 cheer_line("viewer3", 100, "cheer100 love this")):
        c.sendall((line + "\r\n").encode())
    time.sleep(0.5)
    c.close()


th = threading.Thread(target=server, daemon=True); th.start()
cfg = {"nick": "FlyBot", "oauth": "oauth:secret123", "channel": "flychannel"}
logs = []
br = ChatBridge(cfg, host="127.0.0.1", port=port, use_tls=False, log=logs.append)
br.start()
th.join(timeout=5)
time.sleep(0.3)
br.stop()
events = []
while not br.events.empty():
    events.append(br.events.get())
check("asks for tags first, then logs in",
      got["login"] == ["CAP REQ :twitch.tv/tags twitch.tv/commands", "PASS oauth:secret123",
                       "NICK flybot", "JOIN #flychannel"], str([l.split()[0] for l in got["login"]]))
check("answers the server's PING", got["pong"])
check("cheers become exactly what was bought",
      events == [("stim", "pain", 2, "Viewer1"), ("steer", 3.0, "viewer2", "Viewer2"),
                 ("control", "up", "viewer2"), ("free", "pleasure", "viewer4", "viewer4"),
                 ("cheer", 100, "Viewer3")], str(events))
check("the token never reaches the log", not any("secret123" in l for l in logs), str(logs))

print("5. credentials file must be private")
d = tempfile.mkdtemp()
path = os.path.join(d, "twitch.json")
with open(path, "w") as f:
    json.dump(cfg, f)
os.chmod(path, 0o644)
try:
    twitch.load_config(path); refused = False
except SystemExit:
    refused = True
check("world-readable file refused", refused)
os.chmod(path, 0o600)
check("private file accepted", twitch.load_config(path)["channel"] == "flychannel")

print("6. logging in and renewing, against a fake Twitch identity server")
import io, stat, contextlib, urllib.parse as up
from http.server import BaseHTTPRequestHandler, HTTPServer
calls = {"token": 0, "refresh": 0, "validate_expires": 14000}


class IdHandler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def _json(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)

    def do_POST(self):
        form = dict(up.parse_qsl(self.rfile.read(int(self.headers["Content-Length"])).decode()))
        if self.path.endswith("/device"):
            # The scopes asked for must include chat:edit, or the bot connects,
            # reads, and silently cannot say anything (which is what happened).
            ok_ = (form.get("client_id") == "CID"
                   and set(form.get("scopes", "").split()) == set(twitch.SCOPES))
            self._json(200 if ok_ else 400, {"device_code": "DEV", "user_code": "ABCD-EFGH",
                        "verification_uri": "https://www.twitch.tv/activate",
                        "expires_in": 1800, "interval": 0})
        elif self.path.endswith("/token") and form.get("grant_type", "").endswith("device_code"):
            calls["token"] += 1
            if calls["token"] == 1:
                self._json(400, {"status": 400, "message": "authorization_pending"})
            else:
                self._json(200, {"access_token": "ACCESS1", "refresh_token": "REFRESH1", "expires_in": 14000})
        elif self.path.endswith("/token") and form.get("grant_type") == "refresh_token":
            calls["refresh"] += 1
            good = form.get("client_id") == "CID" and form.get("refresh_token", "").startswith("REFRESH")
            n = calls["refresh"] + 1
            self._json(200 if good else 400, {"access_token": f"ACCESS{n}", "refresh_token": f"REFRESH{n}"})

    def do_GET(self):
        if self.path.endswith("/validate"):
            if self.headers.get("Authorization", "").startswith("OAuth ACCESS"):
                self._json(200, {"login": "flyowner", "expires_in": calls["validate_expires"]})
            else:
                self._json(401, {"status": 401, "message": "invalid access token"})


idsrv = HTTPServer(("127.0.0.1", 0), IdHandler)
threading.Thread(target=idsrv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{idsrv.server_address[1]}/oauth2"
cfgpath = os.path.join(tempfile.mkdtemp(), "sub", "twitch.json")
shown = io.StringIO()
cfg2 = twitch.device_login("CID", "#MyChannel", path=cfgpath, base=base,
                           show=lambda s: shown.write(s + "\n"), sleep=lambda s: None)
out = shown.getvalue()
check("shows the activation page and code", "twitch.tv/activate" in out and "ABCD-EFGH" in out)
check("waited through 'authorization_pending'", calls["token"] == 2, str(calls))
check("nick comes from the approving account", cfg2["nick"] == "flyowner")
check("channel normalised", cfg2["channel"] == "mychannel")
saved = json.load(open(cfgpath))
check("saved with the tokens", saved["oauth"] == "oauth:ACCESS1" and saved["refresh_token"] == "REFRESH1"
      and saved["client_id"] == "CID")
check("file is private (600)", stat.S_IMODE(os.stat(cfgpath).st_mode) == 0o600,
      oct(stat.S_IMODE(os.stat(cfgpath).st_mode)))
check("no token printed", "ACCESS1" not in out and "REFRESH1" not in out)
check("and load_config accepts it", twitch.load_config(cfgpath)["nick"] == "flyowner")

logs2 = []
br2 = ChatBridge(dict(saved), log=logs2.append, path=cfgpath, id_base=base)
br2._ensure_token()
check("a healthy token is left alone", calls["refresh"] == 0 and br2.cfg["oauth"] == "oauth:ACCESS1")
calls["validate_expires"] = 120
br2._ensure_token()
check("a token about to expire is renewed", calls["refresh"] == 1 and br2.cfg["oauth"] == "oauth:ACCESS2",
      br2.cfg["oauth"][:12])
check("the renewed pair is saved", json.load(open(cfgpath))["refresh_token"] == "REFRESH2")
calls["validate_expires"] = 14000
br2.cfg["oauth"] = "oauth:DEAD"
br2._ensure_token()
check("a dead token is renewed", br2.cfg["oauth"] == "oauth:ACCESS3")
check("renewal never logs a token", not any("ACCESS" in l or "REFRESH" in l for l in logs2), str(logs2))
idsrv.shutdown()

print("7. rehearsing a cheer in the live game")
from flybrain.twitch import rehearsal_line
line = rehearsal_line("Tester", 300, "cheer300 pain")
m = parse(line)
check("rehearsal line parses like a real cheer", m["bits"] == 300 and m["text"] == "cheer300 pain")
check("marked TEST on screen", m["name"] == "TEST tester", m["name"])
check("and buys what it says", interpret(m) == ("stim", "pain", 3, "TEST tester"))
check("odd characters cannot break the line", parse(rehearsal_line("a b;c\r\nPRIVMSG", 1, "x"))["user"]
      == "abcprivmsg")
from flybrain.stream import FrameServer
import urllib.request, urllib.error
fs = FrameServer(port=0, log=lambda *a: None)
port2 = fs._httpd.server_address[1]
fs.start()
posted = []
def post(body, host="127.0.0.1"):
    req = urllib.request.Request(f"http://{host}:{port2}/test-cheer", method="POST",
                                 data=json.dumps(body).encode())
    try:
        with urllib.request.urlopen(req, timeout=3) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
code, _ = post({"bits": 100, "text": "cheer100 pain"})
check("refused when Twitch is off", code == 409, str(code))
fs.inject = lambda req: (posted.append(req), interpret(parse(rehearsal_line(req["user"], req["bits"], req["text"]))))[1]
code, body = post({"user": "t", "bits": 200, "text": "cheer200 pleasure"})
check("accepted from the Pi itself", code == 200 and body["event"] == ["stim", "pleasure", 2, "TEST t"], str(body))
import socket as _s
lan = [a for a in _s.gethostbyname_ex(_s.gethostname())[2] if not a.startswith("127.")]
lan_ip = lan[0] if lan else None
if lan_ip is None:
    try:
        u = _s.socket(_s.AF_INET, _s.SOCK_DGRAM); u.connect(("192.0.2.1", 9)); lan_ip = u.getsockname()[0]; u.close()
    except OSError:
        lan_ip = None
if lan_ip and not lan_ip.startswith("127."):
    code, _ = post({"user": "x", "bits": 100, "text": "cheer100 pain"}, host=lan_ip)
    check(f"refused from the network address ({lan_ip})", code == 403, str(code))
else:
    print("  SKIP  no LAN address to test the network refusal from")
fs.stop()

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
