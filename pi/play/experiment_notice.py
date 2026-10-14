#!/usr/bin/env python3
"""Say in public what an experiment will test BEFORE it runs (Ian, 2026-10-08:
"Post intentions of experiment in YouTube and Twitch"), the way a
preregistration is posted before the data.

data/experiments/notice.json holds the plan:
  {"name": ..., "start": "YYYY-MM-DDTHH:MM:SS",
   "lines": {"t-1h": "...", "start": "...", "result": "..."},
   "ext": "...<=160 chars for the Twitch panel while pending...",
   "posted": {stage: epoch}}
`experiment_notice.py post STAGE` sends lines[STAGE] once to Twitch chat, YouTube
live chat and Discord #stream-status, and (for t-1h / start) puts "ext" on the
Twitch science panel. The result line is posted only after obsfly's blind
recompute agrees, by hand: `post result`.

Twitch: one short IRC session with the bridge's token, read only (the bridge is
the token's only refresher). YouTube: the promoter's client, its units booked
to the probe tally its budget counts. Never raises: a channel that fails is
logged and the others still go.
"""
import json
import os
import socket
import ssl
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
NOTICE = os.path.join(ROOT, "data", "experiments", "notice.json")
CFG = os.path.expanduser("~/.config/flybrain")
EXT_F = os.path.join(CFG, "ext_experiment.txt")
LOG = os.path.join(HERE, "experiment_notice.log")


def log(msg):
    line = f"{time.strftime('%F %T')} notice: {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def twitch_say(text):
    from flybrain import twitch as tw
    cfg = tw.load_config()
    raw = socket.create_connection((tw.HOST, tw.PORT), timeout=20)
    s = ssl.create_default_context().wrap_socket(raw, server_hostname=tw.HOST)
    try:
        s.sendall(f"PASS {cfg['oauth']}\r\nNICK {cfg['nick'].lower()}\r\n".encode())
        buf, t0 = b"", time.time()
        while b" 001 " not in buf and time.time() - t0 < 15:
            chunk = s.recv(4096)
            if not chunk:
                break
            buf += chunk
            if b"Login authentication failed" in buf:
                raise RuntimeError("Twitch login refused")
        if b" 001 " not in buf:
            raise RuntimeError("no welcome from Twitch")
        s.sendall(f"PRIVMSG #{cfg['channel']} :{text}\r\n".encode())
        time.sleep(1.5)                       # let it leave before we hang up
        s.sendall(b"QUIT\r\n")
    finally:
        s.close()


def youtube_say(text):
    import youtube_promo as yp
    from flybrain import panel
    now = time.time()
    state = {"quota": dict((panel._json(yp.STATE_F).get("quota") or {}))}
    before = yp.spent(state, now)
    try:
        yt = yp.YouTube(panel._json(yp.CFG_F), state, save=lambda: None)
        b = yt.live_broadcast()
        if b is None:
            raise RuntimeError("no live YouTube broadcast")
        yt.say(b["snippet"]["liveChatId"], text, yp.HARD_CAP)
    finally:
        panel._book_units(yp, yp.spent(state, now) - before, now)


def discord_post(title, text):
    hook = (json.load(open(os.path.join(CFG, "discord.json"))).get("webhooks") or {}).get("stream-status")
    if not hook:
        raise RuntimeError("no stream-status webhook")
    body = {"embeds": [{"title": title, "description": text, "color": 0x3498DB}],
            "allowed_mentions": {"parse": []}}
    req = urllib.request.Request(hook, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "flybrain/1.0"})
    urllib.request.urlopen(req, timeout=15).read()


def post(stage, path=NOTICE, senders=None):
    n = json.load(open(path))
    if stage in n.get("posted", {}):
        log(f"{stage} already posted; not again")
        return False
    text = n["lines"][stage]
    senders = senders or {"twitch": twitch_say, "youtube": youtube_say,
                          "discord": lambda t: discord_post(f"🧪 Experiment: {n['name']}", t)}
    done = []
    for name, fn in senders.items():
        try:
            fn(text)
            done.append(name)
        except Exception as e:                # one channel failing must not stop the rest
            log(f"{stage}: {name} failed: {type(e).__name__}: {e}")
    if stage in ("today", "t-1h", "start") and n.get("ext"):
        with open(EXT_F, "w") as f:
            f.write(n["ext"][:160])
        done.append("ext")
    n.setdefault("posted", {})[stage] = time.time()
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(n, f, indent=1)
    os.replace(tmp, path)
    log(f"{stage} posted to {', '.join(done) or 'nothing'}: {text}")
    return bool(done)


if __name__ == "__main__":
    if len(sys.argv) < 3 or sys.argv[1] != "post":
        sys.exit(__doc__)
    post(sys.argv[2])
