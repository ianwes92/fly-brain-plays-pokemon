#!/usr/bin/env python3
"""Post the fly's best recent moment as an Instagram Story, twice a day.

Ian's request (2026-10-04, relayed by obsfly): two Stories a day at peak
times, each a recent highlight with burned-in text saying what it is and where
to watch. Two constraints shape everything here:

  * NO STICKERS. Stories published through the API cannot carry link
    stickers, polls or mentions, so the Twitch and YouTube addresses are
    RENDERED INTO THE VIDEO. Nothing here is tappable, by design.
  * META FETCHES THE FILE FROM A PUBLIC URL; there is no byte upload. So for
    the length of one post a cloudflared quick tunnel (no account, no domain)
    exposes a tiny server that serves exactly ONE file, at an unguessable
    path, and nothing else -- never the Pi. It is torn down straight after.

The clip is the best milestone Short since the last Story (badge > evolution >
catch), re-laid-out at 1080x1920 with the text inside Instagram's safe area
(its UI covers roughly the top 250 px and bottom 340 px). If nothing worth
posting happened, or the stream is not live, the slot is SKIPPED: a stale clip
or an advert for a dead stream is worse than no Story.

Usage:
  ig_story.py set-token TOKEN     store the 60-day token (0600); clear your shell history after
  ig_story.py status              account, publishing limit, token age; posts nothing
  ig_story.py post --live 20      TEST: capture 20 s of the live stream and post it
  ig_story.py post --clip PATH    post a given Short
  ig_story.py post --now          post a clean clip of current play (the 12:00 fallback)
  ig_story.py run                 the scheduled job: pick, skip or post
"""
import http.server
import json
import os
import re
import secrets
import shutil
import socketserver
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

CFG_F = os.path.expanduser("~/.config/flybrain/instagram.json")
STATE_F = os.path.expanduser("~/.config/flybrain/instagram_state.json")
EVENTS_F = os.path.expanduser("~/.config/flybrain/events.json")
DISCORD_F = os.path.expanduser("~/.config/flybrain/discord.json")
SHORTS = os.path.expanduser("~/flybrain/shorts")
CLOUDFLARED = os.path.expanduser("~/.local/bin/cloudflared")
RELAY = "http://127.0.0.1:8765"
API = "https://graph.instagram.com/v21.0"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
FONT_REG = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

W, H = 1080, 1920
RANK = {"badge": 3, "evolve": 2, "catch": 1}     # levels have no Short; wipes are not promotion
# Ian, 2026-10-05: the 12:00 slot is a GUARANTEED daily post -- a milestone if
# there is one, otherwise a clip of current play. 20:00 stays milestone-only,
# so a quiet day gives exactly one Story. Two slots of correct skips had left
# the account promoting nothing for over a day.
GUARANTEED_HOURS = {12}
GAME = "http://127.0.0.1:8766"
ACTIVITY_WAIT_S = 20 * 60        # how long the fallback waits for visible movement
REFRESH_EVERY = 7 * 86400                       # Meta allows a refresh once the token is 24 h old
TOKEN_LIFE = 60 * 86400
WARN_LEFT = 7 * 86400
LINE1 = "A FRUIT FLY'S BRAIN IS PLAYING POKEMON"
LINE2 = "139,255 neurons - nobody scripts it"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.site import TWITCH_URL as _TW, YOUTUBE_URL as _YT   # noqa: E402
LIVE1 = f"LIVE 24/7   {_TW}"
LIVE2 = _YT

# Where the game and the brain map sit in each source, as ffmpeg crop boxes
# (w:h:x:y). The Short is shorts.py's 720x1280 layout; the live window is
# playview's 800x450 (game 480x320 top-left, brain map 320x320 beside it).
CROPS = {"short": ("720:480:0:200", "600:520:60:680"),   # brain map, above the Short's own footer
         "live": ("480:320:0:0", "320:320:480:0")}


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def save_private(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, path)


def log(msg):
    print(f"{time.strftime('%F %T')} ig: {msg}", flush=True)


def alert(text):
    """Into #fly-log, public, no pings -- like the YouTube token warning."""
    log(f"!!! {text}")
    hook = (load_json(DISCORD_F).get("webhooks") or {}).get("fly-log")
    if not hook:
        return
    body = {"embeds": [{"title": "Instagram Stories need attention", "description": text,
                        "color": 0xED4245}], "allowed_mentions": {"parse": []}}
    try:
        req = urllib.request.Request(hook, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "flybrain-ig/1.0"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as exc:
        log(f"could not post the alert: {type(exc).__name__}")


# -- the API ------------------------------------------------------------------
class IGError(Exception):
    pass


def api(method, path, token, **params):
    params["access_token"] = token
    q = urllib.parse.urlencode(params)
    url, data = f"{API}/{path}", None
    if method == "GET":
        url += "?" + q
    else:
        data = q.encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body).get("error", {}).get("message", body)
        except ValueError:
            msg = body
        raise IGError(f"HTTP {e.code}: {msg[:300]}")


def token(cfg, now=None):
    """The token, refreshed weekly. A 60-day token that is never refreshed
    must be remade by hand, so the refresh is automatic and its failure loud."""
    now = time.time() if now is None else now
    t = cfg.get("token")
    if not t:
        raise IGError("no token: run `ig_story.py set-token TOKEN`")
    got = cfg.get("refreshed_at") or cfg.get("set_at") or now
    if now - got > REFRESH_EVERY:
        try:
            d = api("GET", "refresh_access_token", t, grant_type="ig_refresh_token")
            cfg.update(token=d["access_token"], refreshed_at=now,
                       expires_at=now + int(d.get("expires_in", TOKEN_LIFE)))
            save_private(CFG_F, cfg)
            # Whether Meta handed back a NEW string matters to anyone holding
            # a copy of the token (obsfly's panel): logged so it is known.
            log("token refreshed (" + ("SAME string" if d["access_token"] == t
                                       else "NEW string -- copies elsewhere are now stale") + ")")
            t = cfg["token"]
        except (IGError, KeyError) as exc:
            log(f"token refresh failed: {exc}")
    left = (cfg.get("expires_at") or got + TOKEN_LIFE) - now
    if left < WARN_LEFT:
        alert(f"The Instagram token expires in {max(0, int(left // 86400))} day(s) and refreshing "
              "it has failed. Make a new one and run `python3 ~/flybrain/play/ig_story.py "
              "set-token TOKEN` on the Pi. Twitch and YouTube are unaffected.")
    return t


# -- the clip -----------------------------------------------------------------
def _txt(d, name, text):
    p = os.path.join(d, name)
    with open(p, "w") as f:
        f.write(text)
    return p


def fit(text, size, width=W - 80):
    """Shrink a line until it fits the width (DejaVu Bold is ~0.62 em per char)."""
    return int(min(size, width / max(1, 0.62 * len(text))))


def render(src, kind, caption, out, seconds=None):
    """A 1080x1920 Story from a Short (kind 'short') or the live relay
    ('live'). Game and brain are cropped from the source and re-laid-out; all
    text sits between y=250 and y=1580, clear of Instagram's own overlays."""
    gcrop, bcrop = CROPS[kind]
    d = tempfile.mkdtemp(dir="/dev/shm" if os.path.isdir("/dev/shm") else None)
    try:
        t1, t2, t3 = _txt(d, "1", LINE1), _txt(d, "2", LINE2), _txt(d, "3", caption.upper())
        t4, t5 = _txt(d, "4", LIVE1), _txt(d, "5", LIVE2)
        s1, s3 = fit(LINE1, 50), fit(caption, 54)
        graph = (
            f"color=black:s={W}x{H}:r=20[bg];"
            f"[0:v]split[a][b];"
            f"[a]crop={gcrop},scale=1080:720:flags=neighbor[g];"
            f"[b]crop={bcrop},scale=300:-2:flags=neighbor[m];"
            f"[bg][g]overlay=0:460:shortest=1[x];[x][m]overlay=390:1190[y];"
            f"[y]drawtext=fontfile={FONT}:textfile={t1}:fontcolor=white:fontsize={s1}:x=(w-text_w)/2:y=262,"
            f"drawtext=fontfile={FONT_REG}:textfile={t2}:fontcolor=0xDDDDDD:fontsize=38:x=(w-text_w)/2:y=330,"
            f"drawtext=fontfile={FONT}:textfile={t3}:fontcolor=0xFFD778:fontsize={s3}:x=(w-text_w)/2:y=388,"
            f"drawtext=fontfile={FONT}:textfile={t4}:fontcolor=white:fontsize=44:box=1:"
            f"boxcolor=0x9146FF@0.85:boxborderw=12:x=(w-text_w)/2:y=1500,"
            f"drawtext=fontfile={FONT}:textfile={t5}:fontcolor=white:fontsize=40:box=1:"
            f"boxcolor=0xCC0000@0.85:boxborderw=10:x=(w-text_w)/2:y=1560[v]")
        inp = (["-f", "mpjpeg", "-t", str(seconds), "-i", src] if kind == "live" else ["-i", src])
        cmd = (["nice", "-n", "10", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y"] + inp +
               ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
                "-filter_complex", graph, "-map", "[v]", "-map", "1:a", "-shortest",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p",
                "-r", "20", "-c:a", "aac", "-b:a", "64k", "-movflags", "+faststart",
                "-f", "mp4", out + ".part"])
        r = subprocess.run(cmd, capture_output=True, timeout=(seconds or 30) * 20 + 120)
        if r.returncode != 0 or not os.path.exists(out + ".part"):
            raise IGError("render failed: " + r.stderr.decode(errors="replace")[-300:])
        os.replace(out + ".part", out)
        return out
    finally:
        shutil.rmtree(d, ignore_errors=True)


# -- the one-file public server ---------------------------------------------------
def serve_one(path):
    """Serve exactly one file at an unguessable name on 127.0.0.1; 404 for
    everything else. Range requests are honoured -- video fetchers use them."""
    name = secrets.token_urlsafe(18) + ".mp4"
    size = os.path.getsize(path)

    class One(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _head(self, code, length, extra=()):
            self.send_response(code)
            self.send_header("Content-Type", "video/mp4")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(length))
            for k, v in extra:
                self.send_header(k, v)
            self.end_headers()

        def _go(self, body):
            if self.path.split("?")[0] != "/" + name:
                self.send_response(404); self.end_headers(); return
            start, end = 0, size - 1
            m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
            if m and (m.group(1) or m.group(2)):
                if m.group(1):
                    start = int(m.group(1)); end = int(m.group(2) or end)
                else:
                    start = size - int(m.group(2))
                end = min(end, size - 1)
                self._head(206, end - start + 1, [("Content-Range", f"bytes {start}-{end}/{size}")])
            else:
                self._head(200, size)
            if body:
                with open(path, "rb") as f:
                    f.seek(start)
                    left = end - start + 1
                    while left > 0:
                        chunk = f.read(min(65536, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)

        def do_GET(self):
            self._go(True)

        def do_HEAD(self):
            self._go(False)

    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), One)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, name


def tunnel(port, wait=60):
    """A cloudflared quick tunnel to 127.0.0.1:port. Returns (process, https url)."""
    p = subprocess.Popen([CLOUDFLARED, "tunnel", "--no-autoupdate", "--url",
                          f"http://127.0.0.1:{port}"],
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    end, url = time.time() + wait, None
    while time.time() < end and url is None:
        line = p.stdout.readline()
        if not line:
            break
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", line)
        if m:
            url = m.group(0)
    if url is None:
        p.kill()
        raise IGError("cloudflared gave no tunnel URL")
    threading.Thread(target=lambda: [None for _ in p.stdout], daemon=True).start()   # drain
    return p, url


def reachable(url, wait=90):
    """Check the public URL actually serves the file before asking Meta to
    fetch it: a fresh trycloudflare name can take a few seconds to resolve."""
    end = time.time() + wait
    while time.time() < end:
        try:
            req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "flybrain-ig"})
            with urllib.request.urlopen(req, timeout=10) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(3)
    return False


# -- posting ----------------------------------------------------------------------
def publish(path, tok):
    srv, name = serve_one(path)
    proc = None
    try:
        proc, base = tunnel(srv.server_address[1])
        url = f"{base}/{name}"
        if not reachable(url):
            raise IGError("the tunnel never served the clip")
        log("clip is public for this post only")
        c = api("POST", "me/media", tok, media_type="STORIES", video_url=url)
        cid = c.get("id")
        if not cid:
            raise IGError(f"no creation id: {c}")
        for _ in range(60):                       # up to ~5 min of processing
            time.sleep(5)
            st = api("GET", cid, tok, fields="status_code,status")
            code = st.get("status_code")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                raise IGError(f"Instagram could not process the clip: {st}")
        else:
            raise IGError("Instagram took too long to process the clip")
        done = api("POST", "me/media_publish", tok, creation_id=cid)
        return done.get("id")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(10)
            except subprocess.TimeoutExpired:
                proc.kill()
        srv.shutdown()
        srv.server_close()
        log("tunnel and server torn down")


def pick(state, events=None, shorts=SHORTS):
    """The best milestone with a finished Short since the last Story, or None."""
    evs = events if events is not None else (load_json(EVENTS_F).get("events") or [])
    last = state.get("last_event", 0)
    best = None
    for e in evs:
        if e.get("id", 0) <= last or e.get("kind") not in RANK or not e.get("short"):
            continue
        p = e["short"]
        if not os.path.exists(p) or os.path.exists(p + ".failed"):
            continue
        key = (RANK[e["kind"]], e["id"])
        if best is None or key > best[0]:
            best = (key, e)
    return best[1] if best else None


def twitch_live():
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import discord_poster as dp
        s = dp.get_stream()
        return None if s is None else bool(s)
    except Exception:
        return None


def game_state():
    """(playing?, seconds since the fly last changed tile or None if moving)."""
    try:
        text = urllib.request.urlopen(GAME + "/health", timeout=4).read().decode()
    except Exception:
        return False, None
    playing = "\nphase=playing " in "\n" + text
    m = re.search(r"still=(\d+)s", text)
    return playing, (int(m.group(1)) if m else None)


def decide(hour, has_milestone, game_up, live):
    """What this slot does: 'milestone', 'fallback', 'skip' or 'skip-alert'.
    A dead feed is never advertised as LIVE 24/7; on the guaranteed slot that
    is worth an alert, because the post was promised."""
    guaranteed = hour in GUARANTEED_HOURS
    if not game_up or live is False:
        return "skip-alert"          # every stream-down skip is visible (Ian, 2026-10-05)
    if has_milestone:
        return "milestone"
    return "fallback" if guaranteed else "skip"


def clip_now(wait_s=ACTIVITY_WAIT_S, sleep=time.sleep):
    """A clean clip of current play, biased toward the fly visibly moving:
    wait (up to wait_s) until it has changed tile recently, then ask the game
    for the last 30 s. After the wait it clips anyway -- the post is promised."""
    end = time.time() + wait_s
    while time.time() < end:
        up, still = game_state()
        if up and (still is None or still < 15):
            break
        sleep(20)
    req = urllib.request.Request(GAME + "/clip", data=b"{}", method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as r:
        d = json.loads(r.read() or b"{}")
    if not d.get("ok"):
        raise IGError(f"the game could not cut a clip: {d.get('why')}")
    return d["path"], d["caption"]


def run(state, cfg, hour=None):
    hour = time.localtime().tm_hour if hour is None else hour
    ev = pick(state)
    newest = max([e.get("id", 0) for e in (load_json(EVENTS_F).get("events") or [])] or [0])
    up, _ = game_state()
    what = decide(hour, ev is not None, up, twitch_live())
    if what == "skip":
        log("skipped: " + ("nothing worth a Story since the last one" if up and ev is None
                           else "the game or the stream is not live"))
        return None
    if what == "skip-alert":
        alert("An Instagram Story slot was skipped because the game or the stream is not "
              "live -- a 'LIVE 24/7' Story over a dead feed would mislead people.")
        return None
    tok = token(cfg)
    out = os.path.join(tempfile.gettempdir(), f"story-{int(time.time())}.mp4")
    made = None
    try:
        if what == "milestone":
            src, cap = ev["short"], ev["text"]
        else:
            src, cap = clip_now()
            made = src
        render(src, "short", cap, out)
        mid = publish(out, tok)
    finally:
        for p in (out, made):
            if p and os.path.exists(p):
                os.remove(p)
    state.update(last_event=newest, last_post=time.time(), last_media=mid, last_text=cap,
                 last_kind=what)
    save_private(STATE_F, state)
    log(f"posted Story {mid} ({what}): {cap}")
    return mid


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    cfg, state = load_json(CFG_F), load_json(STATE_F)
    if cmd == "set-token" and len(argv) == 3:
        cfg.update(token=argv[2].strip(), set_at=time.time(), refreshed_at=time.time(),
                   expires_at=time.time() + TOKEN_LIFE)
        save_private(CFG_F, cfg)
        me = api("GET", "me", cfg["token"], fields="id,username,account_type")
        print(f"stored (0600). token works for @{me.get('username')} ({me.get('account_type')}).")
        print("now clear it from your shell history: history -d $(history 1 | awk '{print $1}')")
    elif cmd == "status":
        tok = token(cfg)
        me = api("GET", "me", tok, fields="id,username,account_type")
        lim = api("GET", "me/content_publishing_limit", tok, fields="quota_usage,config")
        age = (time.time() - (cfg.get("refreshed_at") or cfg.get("set_at") or time.time())) / 86400
        print(f"@{me.get('username')} {me.get('account_type')} | publishing: {lim} | "
              f"token refreshed {age:.1f} days ago | last Story: {state.get('last_text')}")
    elif cmd == "post" and len(argv) == 3 and argv[2] == "--now":
        up, _ = game_state()
        if not up or twitch_live() is False:
            sys.exit("not posting: the game or the stream is not live")
        tok = token(cfg)
        src, cap = clip_now()
        out = os.path.join(tempfile.gettempdir(), "story-now.mp4")
        try:
            render(src, "short", cap, out)
            shutil.copyfile(out, os.path.join(SHORTS, "last-story.mp4"))
            mid = publish(out, tok)
        finally:
            for p in (out, src):
                if os.path.exists(p):
                    os.remove(p)
        state.update(last_post=time.time(), last_media=mid, last_text=cap, last_kind="fallback")
        save_private(STATE_F, state)
        print(f"published Story {mid}: {cap}")
    elif cmd == "post" and len(argv) >= 4 and argv[2] in ("--live", "--clip"):
        tok = token(cfg)
        out = os.path.join(tempfile.gettempdir(), "story-manual.mp4")
        if argv[2] == "--live":
            render(f"{RELAY}/stream.mjpg", "live", "LIVE RIGHT NOW", out, seconds=int(argv[3]))
        else:
            render(argv[3], "short", argv[4] if len(argv) > 4 else "A NEW MOMENT", out)
        keep = os.path.join(os.path.expanduser("~/flybrain/shorts"), "last-story.mp4")
        shutil.copyfile(out, keep)
        print("rendered; a copy is at", keep)
        mid = publish(out, tok)
        os.remove(out)
        print("published Story", mid)
    elif cmd == "run":
        try:
            run(state, cfg)
        except IGError as exc:
            alert(f"Today's Instagram Story could not be posted: {exc}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
