"""Extension publisher: JWT shape and signature, payload from /health,
offline means no publish and no alert, alert once after 15 min of failures,
config every 10 min."""
import base64, hashlib, hmac, json, os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "play"), ROOT]
import ext_publisher as x
ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1
sec = base64.b64encode(b"0123456789abcdef0123456789abcdef").decode()
tok = x.jwt(sec, now=1000)
h, c, sig = tok.split(".")
pad = lambda s: s + "=" * (-len(s) % 4)
claims = json.loads(base64.urlsafe_b64decode(pad(c)))
check("claims", claims == {"exp": 1120, "user_id": "123456789", "role": "external",
                           "channel_id": "123456789", "pubsub_perms": {"send": ["broadcast"]}})
want = base64.urlsafe_b64encode(hmac.new(base64.b64decode(sec), f"{h}.{c}".encode(), hashlib.sha256).digest()).rstrip(b"=").decode()
check("HS256 signature", sig == want)
H = """ok frames=1
phase=playing title=auto emu=5
team: lv=22 badges=0 caught=4 lead=MR. MIME
learning: depressed_now=3500.123 human_share=0.010 fingerprint=ab recovered=1.000 outstanding[before_ledger=3465.000 human=35.123] depressed_total[human=36.000]
brain_hz: sc_optic=0.03 sc_motor=1.71 kc=0.01 pam=0.05 ppl1=2.50 cx_central_complex=0.43
thinking: a door on the left - going for it
"""
p = x.parse_health(H)
check("team", p["team"] == {"lead": "MR. MIME", "lv": 22, "badges": 0, "caught": 4})
check("learning", p["learning"] == {"in_effect": 3500.1, "from": {"human": 35.1, "game": 0.0, "rule": 0.0, "before_ledger": 3465.0}})
check("brain", p["brain"]["ppl1"] == 2.5 and p["brain"]["eyes"] == 0.03 and p["brain"]["motor"] == 1.71)
check("caption", p["now"] == "a door on the left - going for it")
pl, s = x.build(H)
check("under 5 KB", len(s.encode()) < 5000 and json.loads(s)["team"]["lv"] == 22)
check("no health -> nulls, not invented", x.build("")[0]["team"] is None)

t = [0.0]
L = x.Loop(clock=lambda: t[0])
sent, conf, warns = [], [], []
def send(s, k): sent.append(s)
def config(s, k): conf.append(s)
check("offline: nothing sent", L.step(sec, health=lambda: H, live=lambda: False, send=send, config=config, warn=warns.append) == "offline" and not sent)
t[0] = 400.0
check("live: sent + config", L.step(sec, health=lambda: H, live=lambda: True, send=send, config=config, warn=warns.append) == "sent" and len(sent) == 1 and len(conf) == 1)
t[0] = 460.0
L.step(sec, health=lambda: H, live=lambda: True, send=send, config=config, warn=warns.append)
check("config only every 10 min", len(sent) == 2 and len(conf) == 1)
def bad(s, k): raise x.SendError("HTTP 401")
for i in range(20):
    t[0] = 500.0 + i * 60
    L.step(sec, health=lambda: H, live=lambda: True, send=bad, config=config, warn=warns.append)
check("alert once after 15 min", len(warns) == 1 and "15 minutes" in warns[0])
t[0] = 2000.0
L.step(sec, health=lambda: H, live=lambda: True, send=send, config=config, warn=warns.append)
check("recovers and re-arms", not L.alerted)
print(f"test_ext_publisher: {ok} checks passed")
