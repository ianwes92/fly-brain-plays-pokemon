#!/usr/bin/env python3
"""Live numbers for the "Fly Brain Live Science" Twitch panel extension.

Requested 2026-10-07 by obsfly for Ian. Each minute, while the stream is live,
one small JSON snapshot is broadcast to the extension through Twitch's
Extension PubSub. Every 10 minutes (and on start) the same snapshot is also
stored as the extension's broadcaster configuration, so a viewer who opens the
panel sees numbers at once rather than waiting for the next broadcast.

Real numbers only, read from what the Pi already measures:
  attrib     steps over the LAST 24 HOURS, by the fly alone vs chat-helped
             (data/samples: restart-proof totals; null until 1 h of samples)
  learning   the learning ledger: depression in effect now, and what each
             source (chat / game / stillness rule / before the ledger) still holds
  brain      mean firing (Hz) of brain regions (game /health brain_hz)
  thoughts   per minute of play today, % of stops with nothing in the way
             (data/thoughts), and the caption on screen now
  team       strongest Pokemon, its level, badges, caught
  experiment one line from ~/.config/flybrain/ext_experiment.txt

Auth: a JWT signed HS256 with the extension secret (stored 0600 in
~/.config/flybrain/twitch_ext.json, never in git or the backup), role
"external", 2-minute lifetime, made fresh for every call.

Usage:
  ext_publisher.py set-secret     read the base64 secret from stdin
  ext_publisher.py dry-run        print the payload and its size; sends nothing
  ext_publisher.py once           one broadcast + one config write
  ext_publisher.py run            the service loop
"""
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

CFG_F = os.path.expanduser("~/.config/flybrain/twitch_ext.json")
EXPERIMENT_F = os.path.expanduser("~/.config/flybrain/ext_experiment.txt")
SAMPLES = os.path.expanduser("~/flybrain/data/samples")
DISCORD_F = os.path.expanduser("~/.config/flybrain/discord.json")
GAME = "http://127.0.0.1:8766"
API = "https://api.twitch.tv/helix"
from flybrain.site import TWITCH_EXT_CLIENT_ID as CLIENT_ID, TWITCH_BROADCASTER_ID as BROADCASTER  # noqa: E402
EVERY_S = 60
CONFIG_EVERY_S = 600
LIVE_EVERY_S = 300              # how often to ask Twitch whether the stream is live
ALERT_AFTER_S = 15 * 60
MAX_BYTES = 5000


def log(msg):
    print(f"{time.strftime('%F %T')} ext: {msg}", flush=True)


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def save_private(path, data):
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, path)


# -- the JWT ---------------------------------------------------------------------
def _b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def jwt(secret_b64, now=None):
    now = int(time.time() if now is None else now)
    head = {"alg": "HS256", "typ": "JWT"}
    claims = {"exp": now + 120, "user_id": BROADCASTER, "role": "external",
              "channel_id": BROADCASTER, "pubsub_perms": {"send": ["broadcast"]}}
    signing = (_b64url(json.dumps(head, separators=(",", ":")).encode()) + "." +
               _b64url(json.dumps(claims, separators=(",", ":")).encode()))
    sig = hmac.new(base64.b64decode(secret_b64), signing.encode(), hashlib.sha256).digest()
    return signing + "." + _b64url(sig)


# -- the payload -------------------------------------------------------------------
def _kv(line):
    return {k: v for k, v in re.findall(r"(\w+)=(\S+)", line)}


def parse_health(text):
    lines = {l.split(":", 1)[0]: l.split(":", 1)[1] for l in text.splitlines() if ":" in l}
    out = {}
    b = _kv(lines.get("brain_hz", ""))
    if b:
        f = lambda k: round(float(b[k]), 2) if k in b else None
        out["brain"] = {"eyes": f("sc_optic"), "cx": f("cx_central_complex"), "kc": f("kc"),
                        "pam": f("pam"), "ppl1": f("ppl1"), "motor": f("sc_motor")}
    L = lines.get("learning", "")
    m = re.search(r"depressed_now=([\d.]+)", L)
    o = re.search(r"outstanding\[([^\]]*)\]", L)
    if m:
        frm = {k: round(float(v), 1) for k, v in _kv(o.group(1) if o else "").items()}
        out["learning"] = {"in_effect": round(float(m.group(1)), 1),
                           "from": {k: frm.get(k, 0.0) for k in ("human", "game", "rule", "before_ledger")}}
    t = _kv(lines.get("team", ""))
    lead = re.search(r"lead=(.+)$", lines.get("team", ""))
    if t.get("lv", "-") != "-":
        out["team"] = {"lead": lead.group(1).strip() if lead else None, "lv": int(t["lv"]),
                       "badges": int(t.get("badges", 0)), "caught": int(t.get("caught", 0))}
    out["now"] = lines.get("thinking", "").strip() or None
    out["playing"] = "\nphase=playing " in "\n" + text
    return out


def steps_24h(folder=SAMPLES, now=None):
    """(clean, assisted) steps over the last 24 h, or None with under an hour of data."""
    now = time.time() if now is None else now
    rows = []
    for d in (now - 86400, now):
        try:
            with open(os.path.join(folder, time.strftime("%Y-%m-%d", time.localtime(d)) + ".jsonl")) as f:
                rows += [json.loads(x) for x in f if x.strip()]
        except (OSError, ValueError):
            pass
    rows = sorted({r["t"]: r for r in rows if "tiles_clean" in r and r["t"] >= now - 86400 - 600}.values(),
                  key=lambda r: r["t"])
    if len(rows) < 2 or rows[-1]["t"] - rows[0]["t"] < 3600:
        return None
    a, b = rows[0], rows[-1]
    return {"tiles_clean": b["tiles_clean"] - a["tiles_clean"],
            "tiles_assisted": b["tiles_assisted"] - a["tiles_assisted"],
            "window": "last 24 h" if b["t"] - a["t"] >= 20 * 3600
            else f"last {round((b['t'] - a['t']) / 3600)} h"}


def build(health_text, now=None):
    now = time.time() if now is None else now
    h = parse_health(health_text) if health_text else {}
    from flybrain import thoughts
    ts = thoughts.summary(1, now=now)
    today = ts["days"][-1] if ts["days"] else {}
    try:
        with open(EXPERIMENT_F) as f:
            exp = f.read().strip()[:160] or None
    except OSError:
        exp = None
    p = {"t": int(now), "attrib": steps_24h(now=now), "learning": h.get("learning"),
         "brain": h.get("brain"),
         "thoughts": {"per_min": today.get("per_min"), "phantom_pct": ts["stops"]["phantom_pct"],
                      "now": h.get("now")},
         "team": h.get("team"), "experiment": exp}
    s = json.dumps(p, separators=(",", ":"))
    if len(s.encode()) > MAX_BYTES:
        p["thoughts"]["now"] = None
        s = json.dumps(p, separators=(",", ":"))
    return p, s


# -- sending ------------------------------------------------------------------------
class SendError(Exception):
    pass


def _call(method, path, body, secret):
    req = urllib.request.Request(API + path, data=json.dumps(body).encode(), method=method,
                                 headers={"Client-Id": CLIENT_ID, "Content-Type": "application/json",
                                          "Authorization": "Bearer " + jwt(secret)})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status
    except urllib.error.HTTPError as e:
        raise SendError(f"{method} {path}: HTTP {e.code} {e.read().decode(errors='replace')[:200]}")
    except (urllib.error.URLError, OSError) as e:
        raise SendError(f"{method} {path}: {type(e).__name__}")


def broadcast(s, secret):
    return _call("POST", "/extensions/pubsub", {"target": ["broadcast"], "broadcaster_id": BROADCASTER,
                                                "is_global_broadcast": False, "message": s}, secret)


def store_config(s, secret):
    return _call("PUT", "/extensions/configurations",
                 {"extension_id": CLIENT_ID, "segment": "broadcaster", "broadcaster_id": BROADCASTER,
                  "version": "1", "content": s}, secret)


def alert(text):
    log(f"!!! {text}")
    hook = (load_json(DISCORD_F).get("webhooks") or {}).get("fly-log")
    if not hook:
        return
    body = {"embeds": [{"title": "Twitch science panel needs attention", "description": text,
                        "color": 0xED4245}], "allowed_mentions": {"parse": []}}
    try:
        req = urllib.request.Request(hook, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "flybrain-ext/1.0"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as exc:
        log(f"could not post the alert: {type(exc).__name__}")


def game_health():
    try:
        return urllib.request.urlopen(GAME + "/health", timeout=4).read().decode()
    except Exception:
        return None


def twitch_live():
    try:
        import discord_poster as dp
        s = dp.get_stream()
        return None if s is None else bool(s)
    except Exception:
        return None


class Loop:
    """State for the service loop, separate so the rules can be tested."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.last_ok = clock()
        self.alerted = False
        self.last_config = -1e18
        self.live, self.live_at = None, -1e18

    def step(self, secret, health=game_health, live=twitch_live, send=broadcast,
             config=store_config, warn=alert):
        now = self.clock()
        if now - self.live_at >= LIVE_EVERY_S:
            self.live, self.live_at = live(), now
        if self.live is False:
            self.last_ok = now                  # offline is not a failure
            return "offline"
        text = health()
        if not text:
            return self._failed(now, "the game is not answering", warn)
        _p, s = build(text, now)
        try:
            send(s, secret)
            if now - self.last_config >= CONFIG_EVERY_S:
                config(s, secret)
                self.last_config = now
        except SendError as exc:
            return self._failed(now, str(exc), warn)
        if self.alerted:
            log("publishing works again")
        self.last_ok, self.alerted = now, False
        return "sent"

    def _failed(self, now, why, warn):
        log(f"not published: {why}")
        if not self.alerted and now - self.last_ok >= ALERT_AFTER_S:
            warn(f"The Twitch science panel has not been updated for 15 minutes: {why}. "
                 "The stream itself is unaffected.")
            self.alerted = True
        return "failed"


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    cfg = load_json(CFG_F)
    if cmd == "set-secret":
        sec = sys.stdin.read().strip()
        try:
            if len(base64.b64decode(sec, validate=True)) < 16:
                raise ValueError
        except ValueError:
            sys.exit("that does not look like a base64 extension secret")
        cfg["secret"] = sec
        save_private(CFG_F, cfg)
        print("stored (0600)")
    elif cmd == "dry-run":
        p, s = build(game_health())
        print(json.dumps(p, indent=1))
        print(f"{len(s.encode())} bytes (limit {MAX_BYTES}); nothing sent")
    elif cmd == "once":
        _p, s = build(game_health())
        broadcast(s, cfg["secret"])
        store_config(s, cfg["secret"])
        print(f"broadcast + config stored ({len(s.encode())} bytes)")
    elif cmd == "run":
        if not cfg.get("secret"):
            sys.exit("no secret: run `ext_publisher.py set-secret`")
        loop = Loop()
        while True:
            try:
                loop.step(cfg["secret"])
            except Exception as exc:                    # never let one bad minute stop it
                log(f"error: {type(exc).__name__}: {exc}")
            time.sleep(EVERY_S)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
