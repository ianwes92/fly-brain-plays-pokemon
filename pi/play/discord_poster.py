#!/usr/bin/env python3
"""Tell a Discord server what the stream and the fly are doing.

Runs on the Pi, not on the streaming PC, for one reason: a downtime alert
cannot be sent by the machine that went down. If OBS crashes or the Windows
box sleeps -- the cases the community most needs told about -- a poster living
there dies with the thing it reports. This machine is up all the time and
already holds the Twitch login.

Structure, message formats and the Discord server itself are obsfly's work;
this is that implementation with the plumbing changed for the Pi. What differs
from the version it sent, and why:

  * IT NEVER REFRESHES THE TWITCH TOKEN. The game owns that credential.
    Refresh tokens ROTATE when used, so two independent refreshers race and
    the loser is locked out for good -- and the loser would be the game,
    taking chat down silently. So the token is re-read from the game's own
    config on every call and never written. The cost is that a token going
    stale while the game is down leaves Twitch unaskable, which is handled
    below rather than guessed at.
  * A 401 IS NOT AN OUTAGE. Not being able to ask whether the stream is live
    is not the same as the stream being off, and announcing "stream dropped"
    because of an expired token would be a lie posted publicly.
  * The config is written atomically and 0600: it holds three
    post-anything-to-this-channel webhook tokens.
  * First run starts from the newest event rather than id 0, so it does not
    fire the whole backlog into the channel at once.

Everything else -- the User-Agent Discord requires, the offline debounce, the
429 handling, the clean-versus-dropped test, explicit allowed_mentions -- is
obsfly's, learned the hard way on the Windows side.
"""
import datetime
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))


def _site_twitch_url():
    """twitch.tv/<channel> from flybrain/site.py (site.json)."""
    sys.path.insert(0, os.path.dirname(HERE))
    from flybrain.site import TWITCH_URL
    return TWITCH_URL
CFG_F = os.path.expanduser("~/.config/flybrain/discord.json")
STATE_F = os.path.expanduser("~/.config/flybrain/discord_state.json")
TWITCH_F = os.path.expanduser("~/.config/flybrain/twitch.json")

UA = "flybrain-discord/1.0"          # Discord 403s a request with no User-Agent
POLL = 60
OFFLINE_CONFIRM = 3                  # misses before believing it; a reconnect is not an outage
DIGEST_EVERY = 14400                 # 4 hours: Ian's call, relayed by obsfly.
                                     # 24 posts a day is wallpaper for a small
                                     # server, and the fly's situation changes
                                     # slowly enough that most of them said the
                                     # same thing. Milestones are unaffected --
                                     # those stay individual and immediate,
                                     # because those are the ones carrying news.
LEVELS_SHOWN = 10                    # ...the rest are counted, not dropped
PURPLE, GREY, GREEN, RED, GOLD = 0x9146FF, 0x4F545C, 0x3BA55D, 0xED4245, 0xFAA61A


def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def save_private(path, data):
    """Atomic and 0600. A power cut must not leave half a file: half the state
    re-announces everything, and half the config cannot be recovered without
    minting every credential again."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
                 stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


CFG = load_json(CFG_F)


def load_state():
    s = load_json(STATE_F)
    s = s or {"live": False, "misses": 0, "started_at": None, "peak": 0,
              "last_digest": 0, "last_event_id": None}
    # Levels live in the state, not in a local variable: they accumulate for a
    # whole hour before the digest, and a poster restart in that hour would
    # otherwise drop them silently. (obsfly's fix; it is the better one.)
    s.setdefault("levels", [])
    return s


def http(url, data=None, headers=None, method=None, timeout=15):
    h = {"User-Agent": UA}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
    return json.loads(raw) if raw else None


def http_text(url, timeout=10):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode()


# -- Twitch, read-only -----------------------------------------------------
_warned = {"auth": 0.0}


def twitch(path):
    """Ask Twitch, with the game's token. None means "could not ask", which is
    deliberately different from an empty answer."""
    cfg = load_json(TWITCH_F)          # re-read every time: the game rotates it
    access = str(cfg.get("oauth", "")).split(":", 1)[-1]
    client_id = cfg.get("client_id", "")
    if not access or not client_id:
        return None
    hdr = {"Client-Id": client_id, "Authorization": "Bearer " + access}
    try:
        return http("https://api.twitch.tv/helix/" + path, headers=hdr)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            now = time.time()
            if now - _warned["auth"] > 3600:
                _warned["auth"] = now
                print("twitch: the game's token is not usable; waiting for the "
                      "game to renew it (not refreshing it here, see module docstring)",
                      flush=True)
            return None
        raise
    except OSError:
        return None


def get_stream():
    """The live stream, {} for definitely offline, None for "cannot tell"."""
    bid = CFG.get("broadcaster_id")
    if not bid:
        return None
    d = twitch("streams?user_id=" + str(bid))
    if d is None:
        return None
    rows = d.get("data") or []
    return rows[0] if rows else {}


# -- the Pi's own state ----------------------------------------------------
def relay_health():
    try:
        txt = http_text(CFG.get("relay", "http://127.0.0.1:8765") + "/health")
    except Exception:
        return {}
    lines = txt.splitlines()
    out = {}
    for tok in lines[0].split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            out[k] = v
    out["thinking"] = next((l.split("thinking:")[1].strip()
                            for l in lines if "thinking:" in l), None)
    # phase says whether the game is PLAYING or still loading. The relay's own
    # game= field does not: it reported game=live six seconds into a restart,
    # with the connectome still loading, because it answers for its video
    # source rather than for the game.
    # The FIRST word only: the line is "phase=playing title=auto emu=...".
    out["phase"] = next((l.split("phase=")[1].split()[0]
                         for l in lines if "phase=" in l and l.split("phase=")[1].split()), None)
    return out


def get_events(since):
    try:
        d = http(CFG.get("events", "http://127.0.0.1:8766") + "/events?since=%d" % since)
        return d.get("events", []) if d else []
    except Exception:
        return []                       # the game is restarting; ids mean nothing is lost


# -- Discord ---------------------------------------------------------------
def post(channel, embed, content=None, ping_role=None, wait=False):
    """Send one message. Returns the created message when wait=True.

    The guard below exists because this failure is silent by construction:
    Discord accepts a payload with nothing in it, answers 2xx, and posts an
    empty message -- which, with a role ping attached, interrupts everyone who
    opted in and shows them nothing. A dropped post is recoverable; that is
    not, and Discord will never tell you it happened.
    """
    url = (CFG.get("webhooks") or {}).get(channel)
    if not url:
        return None
    if not (embed or content):
        print(f"discord: refusing to send an empty message to {channel}", flush=True)
        return None
    body = {"username": "Flybrain", "embeds": [embed] if embed else []}
    if content:
        body["content"] = content
    # Explicit, always: without it an embed can ping @everyone by accident.
    body["allowed_mentions"] = {"roles": [ping_role]} if ping_role else {"parse": []}
    if not (body["embeds"] or body.get("content")):
        print(f"discord: refusing to send an empty message to {channel}", flush=True)
        return None
    send_to = url + ("?wait=true" if wait else "")
    for attempt in range(4):
        try:
            return http(send_to, data=json.dumps(body).encode(),
                        headers={"Content-Type": "application/json"}, method="POST")
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    time.sleep(float(json.loads(e.read())["retry_after"]) + 0.5)
                except Exception:
                    time.sleep(5)
                continue
            if 500 <= e.code < 600:
                time.sleep(2 ** attempt)
                continue
            print(f"discord: {channel} refused HTTP {e.code}", flush=True)
            return None                 # other 4xx will not improve on a retry
        except Exception:
            time.sleep(2 ** attempt)


def post_with_video(channel, embed, path):
    """ONE webhook message carrying the embed and the mp4 together.

    Returns True when Discord accepted it, or a short reason string when it did
    not, so the caller can fall back to the text and SAY why -- a 413 (file too
    large) used to look like any other refusal.
    """
    url = (CFG.get("webhooks") or {}).get(channel)
    if not url:
        return "no webhook"
    try:
        size = os.path.getsize(path)
    except OSError:
        return "file missing"
    if size > SHORT_MAX_BYTES:
        return f"file too large ({size / 1048576:.2f} MB > {SHORT_MAX_BYTES / 1048576:.2f} MB)"
    with open(path, "rb") as f:
        video = f.read()
    payload = {"username": "Flybrain", "embeds": [embed], "allowed_mentions": {"parse": []}}
    boundary = "flybrain" + os.urandom(12).hex()
    name = os.path.basename(path)
    body = b"".join([
        f"--{boundary}\r\n".encode(),
        b'Content-Disposition: form-data; name="payload_json"\r\n',
        b"Content-Type: application/json\r\n\r\n",
        json.dumps(payload).encode(), b"\r\n",
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="files[0]"; filename="{name}"\r\n'.encode(),
        b"Content-Type: video/mp4\r\n\r\n",
        video, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ])
    headers = {"Content-Type": f"multipart/form-data; boundary={boundary}"}
    for attempt in range(4):
        try:
            http(url, data=body, headers=headers, method="POST", timeout=120)
            return True
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    time.sleep(float(json.loads(e.read())["retry_after"]) + 0.5)
                except Exception:
                    time.sleep(5)
                continue
            if 500 <= e.code < 600:
                time.sleep(2 ** attempt)
                continue
            return "too large for Discord (HTTP 413)" if e.code == 413 else f"refused HTTP {e.code}"
        except Exception as ex:
            if attempt == 3:
                return f"{type(ex).__name__}"
            time.sleep(2 ** attempt)
    return "gave up after retries"


def event_embed(ev):
    title, colour = KIND.get(ev.get("kind"), ("Something happened", GREEN))
    return {"title": title, "description": ev.get("text", ""),
            "color": colour, "timestamp": now_iso()}


def settle_pending(s, now=None):
    """Post any held milestone whose Short is ready, failed, or overdue.

    Returns how many are still waiting. Every way of NOT getting the video
    still posts the text, and logs why: nothing is allowed to vanish.
    """
    now = time.time() if now is None else now
    still = []
    for p in s.get("pending") or []:
        path = p.get("short", "")
        ready = path and os.path.exists(path)
        failed = path and os.path.exists(path + ".failed")
        overdue = now - p.get("first_seen", now) > SHORT_WAIT_S
        if ready:
            res = post_with_video("fly-progress", event_embed(p), path)
            if res is True:
                print(f"discord: posted '{p.get('text')}' with its Short", flush=True)
            else:
                print(f"discord: Short not attached ({res}); posting the text alone", flush=True)
                post("fly-progress", event_embed(p))
        elif failed or overdue:
            why = "the Short failed to render" if failed else f"no Short after {SHORT_WAIT_S}s"
            print(f"discord: {why}; posting '{p.get('text')}' as text", flush=True)
            post("fly-progress", event_embed(p))
        else:
            still.append(p)
    s["pending"] = still
    return len(still)


def fmt_dur(sec):
    h, m = int(sec // 3600), int(sec % 3600 // 60)
    return ("%dh %dm" % (h, m)) if h else ("%dm" % m)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def announce_live(s, health):
    e = {"title": "● LIVE — Flybrain Plays",
         "url": "https://" + _site_twitch_url(),
         "description": s.get("title", ""),
         "color": PURPLE,
         "fields": [
             {"name": "Game", "value": s.get("game_name", "—"), "inline": True},
             {"name": "The fly is", "value": health.get("thinking") or "waking up",
              "inline": False},
             {"name": "How to help", "value":
              "**!pain** / **!pleasure** teach it — real dopamine, real learning.\n"
              "**!left !right !up !down !a !b** push it along. Free, any time.",
              "inline": False}],
         "footer": {"text": "139,255 simulated neurons · running on a Raspberry Pi"},
         "timestamp": now_iso()}
    role = CFG.get("watcher_role_id")
    post("stream-status", e, content=("<@&%s>" % role) if role else None, ping_role=role)


def announce_offline(dur, peak, clean):
    e = {"title": "Stream ended" if clean else "⚠ Stream dropped",
         "color": GREY if clean else RED,
         "description": ("Thanks for training the fly." if clean else
                         "The stream went down unexpectedly — it may be back shortly."),
         "fields": [{"name": "Uptime", "value": fmt_dur(dur), "inline": True},
                    {"name": "Peak viewers", "value": str(peak), "inline": True}],
         "timestamp": now_iso()}
    post("stream-status", e)


KIND = {"catch": ("Caught a Pokémon!", GOLD), "badge": ("Badge earned!", GOLD),
        "evolve": ("Evolution!", PURPLE), "level": ("Level up", GREEN),
        "faint": ("The team fainted", RED), "reset": ("A new run starts", PURPLE)}
SKIP_INDIVIDUAL = {"level"}      # Ian's cadence: levels go in the digest instead

# Shorts ride WITH their milestone, as one message (obsfly, 2026-10-02: a
# separate video post for the same catch reads as a bug). A Short takes ~35 s
# to cut, so the news is held until it exists -- but never forever.
SHORT_WAIT_S = 180          # give up on the video after this and post the text
SHORT_MAX_BYTES = int(9.5 * 1024 * 1024)   # under Discord's 10 MB webhook limit
PENDING_POLL_S = 10         # check this often while a Short is being waited for


def announce_event(ev):
    if ev.get("kind") in SKIP_INDIVIDUAL:
        return
    post("fly-progress", event_embed(ev))


def post_digest(health, live, levels):
    phase = health.get("phase")
    if live:
        status = "live"
    elif health:
        status = "offline (the fly is still playing)"
    else:
        status = "offline"
    if phase and phase != "playing":
        status += f" — game {phase}"
    fields = [{"name": "Status", "value": status, "inline": True}]
    if health.get("game_avg"):
        fields.append({"name": "Brain speed", "value": health["game_avg"], "inline": True})
    if live:
        fields.append({"name": "Viewers", "value": str(live.get("viewer_count", 0)),
                       "inline": True})
    if levels:
        # Four-hourly digests collect roughly four times as many levels as
        # hourly ones did, so the render cap started to bite. A digest that
        # silently drops news is worse than one that admits to it.
        shown = levels[:LEVELS_SHOWN]
        extra = len(levels) - len(shown)
        value = "\n".join(shown)
        if extra > 0:
            value += f"\n...and {extra} more"
        fields.append({"name": "Levels gained", "value": value, "inline": False})
    if health.get("thinking"):
        fields.append({"name": "Right now", "value": health["thinking"], "inline": False})
    post("fly-log", {"title": "Fly report", "color": PURPLE, "fields": fields,
                     "timestamp": now_iso()})


def catch_up(state, stream=None):
    """First run: adopt the world as it is, and announce none of it.

    Two things would otherwise be announced that did not just happen. The
    events: asking since=0 returns the whole kept backlog and posts every
    catch, badge and evolution at once. And the stream: with no prior state,
    a stream that has been live for hours reads as a transition, so it is
    announced AND the watcher role is pinged -- and a fresh start cannot tell
    "Ian just went live" from "the state file was lost while a stream was
    already running". With Restart=always the second becomes more likely over
    time, and each one interrupts everybody who opted in. So: adopt, do not
    announce. Only an observed transition pings.
    """
    if state.get("last_event_id") is not None:
        return state
    newest = 0
    for ev in get_events(0):
        newest = max(newest, int(ev.get("id", 0)))
    state["last_event_id"] = newest
    if stream:
        state.update(live=True, started_at=stream.get("started_at"),
                     peak=stream.get("viewer_count", 0))
    state["last_digest"] = time.time()
    print(f"first run: starting from event {newest}; "
          f"stream {'already live, adopted quietly' if stream else 'not live'}",
          flush=True)
    return state


# -- is the picture viewers see actually MOVING? ---------------------------
# 2026-10-03: after a relay restart OBS's browser source held its last frame
# for hours. Every instrument said healthy -- the relay was serving, OBS had 0
# skipped frames and 0.000 congestion, the Twitch API said live -- because
# encoding a still image is not an error. Only something looking at the
# BROADCAST could see it, so this watches Twitch's own thumbnail, which it
# regenerates from the stream. That catches a freeze anywhere in the chain:
# game, relay, browser source, OBS, encoder. (obsfly's design.)
# Measured before trusting it: the thumbnail is regenerated every 5 min and
# the CDN serves it max-age=300 -- cache-busting returns the same object -- so
# images are told apart by GENERATION (fetch time minus the Age header), and
# compared by pixels, not file hash, in case a still frame re-encodes slightly
# differently. A fly standing still does not look static: the brain map beside
# the game flickers constantly.
PIC_EVERY = 300
PIC_SIZE = (64, 36)
PIC_DIFF = 16                 # a pixel "changed" if it moved more than this (0-255)
PIC_STILL_MAX = 3             # ...and the picture is still if this few changed
PIC_ALERT_AFTER = 3           # still comparisons in a row: ~15 min of no motion
PIC_STALE_S = 15 * 60         # live, but no NEW preview generated for this long
_pic = {"next": 0.0, "gen": None, "img": None, "still": 0, "alerted": False,
        "new_at": None, "stale_alerted": False}


def fetch_thumb(url):
    """(grayscale pixels, generation time) of Twitch's current thumbnail."""
    import io
    from PIL import Image
    url = url.replace("{width}", "320").replace("{height}", "180")
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=15) as r:
        body, age = r.read(), r.headers.get("Age")
    try:
        gen = time.time() - float(age or 0)
    except ValueError:
        gen = time.time()
    img = Image.open(io.BytesIO(body)).convert("L").resize(PIC_SIZE)
    return list(img.getdata()), gen


def watch_picture(live, now=None, fetch=fetch_thumb, alert=None):
    """Called once per full pass while live. Returns 'still', 'moving' or None
    (nothing new to compare). Alerts once per freeze, and says when it ends."""
    now = time.time() if now is None else now
    alert = alert or (lambda title, text, colour: post("fly-log", {
        "title": title, "description": text, "color": colour}))
    url = (live or {}).get("thumbnail_url")
    if not url or now < _pic["next"]:
        return None
    _pic["next"] = now + PIC_EVERY
    try:
        img, gen = fetch(url)
    except Exception as exc:
        print(f"picture check: could not fetch the thumbnail: {type(exc).__name__}", flush=True)
        return None
    if _pic["gen"] is not None and abs(gen - _pic["gen"]) < 120:
        # The same generation again. Normally nothing to learn -- but if Twitch
        # stops regenerating it while still saying "live", the encoder is
        # sending nothing (2026-10-08 21:22: an NVIDIA driver reset left OBS
        # "active" at 0 bytes for 14 min and no check here noticed).
        if (_pic["new_at"] is not None and now - _pic["new_at"] > PIC_STALE_S
                and not _pic["stale_alerted"]):
            _pic["stale_alerted"] = True
            alert("The stream may be stalled",
                  f"Twitch says the stream is live but has not refreshed its preview for "
                  f"{int((now - _pic['new_at']) // 60)} minutes -- the encoder may be sending "
                  "nothing. Check OBS on the stream PC.", RED)
            return "stale"
        return None
    if _pic["stale_alerted"]:
        alert("The stream preview is updating again", "Twitch has a fresh preview again.", GREEN)
    _pic["new_at"], _pic["stale_alerted"] = now, False
    prev, _pic["img"], _pic["gen"] = _pic["img"], img, gen
    if prev is None:
        return None
    changed = sum(1 for a, b in zip(prev, img) if abs(a - b) > PIC_DIFF)
    print(f"picture check: {changed} of {len(img)} pixels changed", flush=True)
    if changed <= PIC_STILL_MAX:
        _pic["still"] += 1
        if _pic["still"] >= PIC_ALERT_AFTER and not _pic["alerted"]:
            _pic["alerted"] = True
            alert("The stream picture may be frozen",
                  f"Twitch's preview of the stream has not changed in about "
                  f"{_pic['still'] * PIC_EVERY // 60} minutes. Viewers may be watching a still "
                  "image; the stream is still live.", RED)
        return "still"
    if _pic["alerted"]:
        alert("The stream picture is moving again", "Twitch's preview is changing again.", GREEN)
    _pic["still"], _pic["alerted"] = 0, False
    return "moving"


# -- the things nobody is watching for ---------------------------------------
# Ian, 2026-10-06: "make sure everything works without claude". Two failures
# used to be visible only to someone reading logs: the game itself down while
# OBS keeps streaming the relay's "waking up" card (a crash loop), and Twitch
# becoming unaskable (the game's login dead, so chat, titles and channel
# points are dead with it). Each now alerts #fly-log once, and again when it
# recovers -- an alert with no all-clear leaves people thinking it is broken.
GAME_DOWN_ALERT_S = 10 * 60
TWITCH_BLIND_ALERT_S = 30 * 60
# The OBS PC is PIN-protected with no auto-login (Ian, 2026-10-06): after a
# reboot or power cut it sits at the Windows login screen and nothing on it
# can raise an alarm. The Pi is the only thing that can notice: a DEFINITE
# "offline" from Twitch for this long, while the game here is playing fine.
# 10 min clears the 120 s maintenance restarts (08:00, 20:10) by a wide margin.
STREAM_OFF_ALERT_S = 10 * 60
_watch = {"game_down": None, "game_alerted": False, "blind": None, "blind_alerted": False,
          "off": None, "off_alerted": False}


def watch_health(live, health, now=None, alert=None):
    now = time.time() if now is None else now
    alert = alert or (lambda title, text, colour: post("fly-log", {
        "title": title, "description": text, "color": colour}))
    w = _watch
    game_ok = bool(health) and health.get("game") == "live" and health.get("phase") == "playing"
    if live and not game_ok:
        w["game_down"] = w["game_down"] or now
        if now - w["game_down"] >= GAME_DOWN_ALERT_S and not w["game_alerted"]:
            w["game_alerted"] = True
            alert("The fly's game is down",
                  f"The stream is live but the game has not been playing for "
                  f"{int((now - w['game_down']) // 60)} minutes, so viewers see the waiting "
                  "screen. The Pi restarts the game by itself; if this message stays, "
                  "the Pi needs a look.", RED)
    elif game_ok:
        if w["game_alerted"]:
            alert("The fly's game is back", "The game is playing again.", GREEN)
        w["game_down"], w["game_alerted"] = None, False
    if live is not None and not live and game_ok:
        w["off"] = w["off"] or now
        if now - w["off"] >= STREAM_OFF_ALERT_S and not w["off_alerted"]:
            w["off_alerted"] = True
            alert("Stream offline while the fly plays",
                  f"Twitch has shown the stream offline for {int((now - w['off']) // 60)} minutes "
                  "while the game is running. The OBS PC may have rebooted and need Ian's PIN.", RED)
    elif live:
        if w["off_alerted"]:
            alert("Stream back online", "Twitch shows the stream live again.", GREEN)
        w["off"], w["off_alerted"] = None, False
    if live is None:
        w["blind"] = w["blind"] or now
        if now - w["blind"] >= TWITCH_BLIND_ALERT_S and not w["blind_alerted"]:
            w["blind_alerted"] = True
            alert("Twitch cannot be reached from the Pi",
                  f"For {int((now - w['blind']) // 60)} minutes the Pi could not ask Twitch "
                  "anything. Most likely the game's Twitch login has expired, which also "
                  "stops chat commands, titles and channel points. Fix: on the Pi, "
                  "`flybrain twitch-login`.", RED)
    else:
        if w["blind_alerted"]:
            alert("Twitch is reachable again", "The Pi can talk to Twitch again.", GREEN)
        w["blind"], w["blind_alerted"] = None, False


# "Fly Brain v2" (OBS, on air since 2026-10-09) is drawn from four Pi feeds.
# Any of them dying blanks part of the stream with no error anywhere, so each
# pass checks them, and #fly-log hears after V2_FAILS failed passes in a row.
V2_FEEDS = ("/game.png", "/panel/overlay", "/panel/live", "/panel/brain_layout")
V2_FAILS = 3
_v2 = {"fails": {}, "alerted": set()}


def check_v2(base="http://127.0.0.1:8765", token=None, fetch=None):
    """{path: ok} for the four feeds."""
    if token is None:
        sys.path.insert(0, os.path.dirname(HERE))
        from flybrain import panel
        token = panel.panel_token()

    def _get(url, auth):
        req = urllib.request.Request(url, headers={"Authorization": f"Bearer {auth}"} if auth else {})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, r.read(200)
        except urllib.error.HTTPError as e:
            return e.code, b""
        except OSError:
            return 0, b""
    fetch = fetch or _get
    out = {}
    for p in V2_FEEDS:
        code, _ = fetch(base + p, None if p == "/game.png" else token)
        if code == 503 and p == "/game.png":      # the first ask switches encoding on
            time.sleep(1.0)
            code, _ = fetch(base + p, None)
        out[p] = code == 200
    return out


def watch_v2(live, alert=None, check=check_v2):
    if not live:
        return None
    alert = alert or (lambda title, text, colour: post("fly-log", {
        "title": title, "description": text, "color": colour}))
    res = check()
    for p, ok in res.items():
        if ok:
            if p in _v2["alerted"]:
                _v2["alerted"].discard(p)
                alert("Stream feed back", f"{p} is answering again.", GREEN)
            _v2["fails"][p] = 0
            continue
        _v2["fails"][p] = _v2["fails"].get(p, 0) + 1
        if _v2["fails"][p] >= V2_FAILS and p not in _v2["alerted"]:
            _v2["alerted"].add(p)
            alert("A stream feed is down",
                  f"{p} on the Pi has failed {_v2['fails'][p]} checks in a row; part of the "
                  "'Fly Brain v2' scene will be blank or frozen. The old 'Fly Brain' scene is the "
                  "one-click fallback.", RED)
    return res


# The Pi itself (2026-10-13: the project runs with nobody watching, and Ian
# only sees Discord). One alert per problem, an all-clear when it goes, every
# text saying what to do. Checked every SYS_EVERY seconds.
SYS_EVERY = 600
DISK_MIN_GB = 20
TEMP_MAX_C = 80.0
_sys = {"next": 0.0, "up": {}}


def system_problems(run=None, disk=None, temp=None, throttled=None):
    """{key: (title, what to do)} for what is wrong right now."""
    import shutil
    import subprocess
    run = run or (lambda cmd: subprocess.run(cmd, capture_output=True, text=True, timeout=20).stdout)
    out = {}
    failed = [l.split()[0] for l in run(["systemctl", "--user", "--failed", "--plain", "--no-legend"]).splitlines()
              if l.strip().startswith("flybrain")]
    for u in failed:
        out[f"failed:{u}"] = (f"{u} failed on the Pi",
                              f"The Pi job {u} failed and will not retry by itself. Usually it recovers "
                              "at its next scheduled run; if this alert stays up for a day, restart the "
                              "Pi (unplug, wait 10 s, plug back in) -- everything starts on its own.")
    free_gb = (disk if disk is not None else shutil.disk_usage("/").free) / 1e9
    if free_gb < DISK_MIN_GB:
        out["disk"] = ("The Pi is running out of disk",
                       f"Only {free_gb:.0f} GB free. Old Shorts in ~/flybrain/shorts and old runs in "
                       "~/flybrain/runs can be deleted safely (runs are also in the GitHub backup).")
    t = temp if temp is not None else _read_temp()
    if t is not None and t > TEMP_MAX_C:
        out["temp"] = ("The Pi is running hot", f"{t:.0f} °C. Check the fan and that the vents are clear; "
                       "the Pi slows itself down above ~85 °C, which makes the fly sluggish.")
    th = throttled if throttled is not None else run(["vcgencmd", "get_throttled"]).strip()
    if th and th.endswith("=0x0") is False and "throttled=" in th:
        val = int(th.split("=")[1], 16)
        if val & 0x1:
            out["volts"] = ("The Pi's power supply is too weak right now",
                            "Under-voltage detected. Use the official 27 W USB-C supply, plugged in "
                            "directly (no extension or hub). The stream keeps running but may glitch.")
    return out


def _read_temp():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return int(f.read()) / 1000.0
    except (OSError, ValueError):
        return None


def watch_system(now=None, alert=None, problems=system_problems):
    now = time.time() if now is None else now
    if now < _sys["next"]:
        return None
    _sys["next"] = now + SYS_EVERY
    alert = alert or (lambda title, text, colour: post("fly-log", {
        "title": title, "description": text, "color": colour}))
    cur = problems()
    for k, (title, text) in cur.items():
        if k not in _sys["up"]:
            alert(title, text, RED)
    for k, title in list(_sys["up"].items()):
        if k not in cur:
            alert("Resolved: " + title, "That problem has cleared by itself.", GREEN)
    _sys["up"] = {k: v[0] for k, v in cur.items()}
    return cur


def main():
    s = load_state()
    if s.get("last_event_id") is None:
        s = catch_up(s, get_stream() or None)
        save_private(STATE_F, s)
    last_full = 0.0
    while True:
        waiting = 0
        try:
            # The full pass -- live/offline, new events, digest -- keeps its own
            # once-a-POLL rhythm even while the loop spins fast for a pending
            # Short. The offline debounce counts MISSES, so letting it run every
            # 10 s would turn a 3-minute confirmation into 30 s, and a brief
            # Twitch blip during a Short would post a false "Stream dropped".
            if time.time() - last_full < POLL:
                waiting = settle_pending(s)
                save_private(STATE_F, s)
                time.sleep(PENDING_POLL_S if waiting else POLL)
                continue
            last_full = time.time()
            live = get_stream()          # None = could not ask
            health = relay_health()
            watch_health(live, health)
            try:
                watch_v2(live)
            except Exception as exc:              # a check must never stop the poster
                print(f"v2 check failed: {type(exc).__name__}", flush=True)
            try:
                watch_system()
            except Exception as exc:
                print(f"system check failed: {type(exc).__name__}", flush=True)
            if live:
                s["peak"] = max(s.get("peak", 0), live.get("viewer_count", 0))
                s["misses"] = 0
                if not s["live"]:
                    s.update(live=True, started_at=live.get("started_at"),
                             peak=live.get("viewer_count", 0))
                    announce_live(live, health)
                watch_picture(live)
            elif live is not None and s["live"]:
                # A definite "no stream". An unanswerable Twitch is not one.
                s["misses"] += 1
                if s["misses"] >= OFFLINE_CONFIRM:
                    started, dur = s.get("started_at"), 0
                    if started:
                        dur = (datetime.datetime.now(datetime.timezone.utc) -
                               datetime.datetime.fromisoformat(
                                   started.replace("Z", "+00:00"))).total_seconds()
                    # The relay still answering means the Pi is fine and Ian
                    # stopped on purpose; the relay gone means something broke.
                    announce_offline(dur, s.get("peak", 0), clean=bool(health))
                    s.update(live=False, misses=0, started_at=None, peak=0)

            for ev in get_events(s.get("last_event_id") or 0):
                if ev.get("kind") == "level":
                    s["levels"] = (s.get("levels") or [])[-40:] + [ev.get("text", "")]
                if ev.get("short") and ev.get("kind") not in SKIP_INDIVIDUAL:
                    # Held until its video exists, then sent WITH it (see
                    # settle_pending). Persisted in the state file, so a poster
                    # restart mid-wait does not lose the news.
                    s.setdefault("pending", []).append(dict(ev, first_seen=time.time()))
                else:
                    announce_event(ev)
                s["last_event_id"] = max(s.get("last_event_id") or 0, ev.get("id", 0))
            waiting = settle_pending(s)

            if (time.time() - s.get("last_digest", 0) > DIGEST_EVERY
                    and (s["live"] or health)):
                if s.get("last_digest"):       # not immediately on first start
                    post_digest(health, live, s.get("levels") or [])
                    s["levels"] = []
                s["last_digest"] = time.time()

            save_private(STATE_F, s)
        except Exception as ex:
            print("loop error: %s %s" % (type(ex).__name__, ex), flush=True)
        # Poll fast only while a Short is being waited for, so the news goes
        # out within seconds of the video instead of up to a minute later.
        time.sleep(PENDING_POLL_S if waiting else POLL)


if __name__ == "__main__":
    main()
