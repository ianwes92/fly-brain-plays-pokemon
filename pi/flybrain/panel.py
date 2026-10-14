"""The control panel's door into the Pi: authentication and the status report.

obsfly's panel runs on the Windows box (Ian wants it there), so it reaches the
Pi over the LAN through the relay -- the one service that stays up while the
game is down or reloading, so the panel can still say what is wrong. Control
requests are passed on to the game on localhost; the game refuses them from
anywhere else.

Security, in order: the request must come from a private (LAN) address; it
must carry the bearer token from PANEL_F (0600, generated here on first use,
copied to the panel once by Ian -- never sent between sessions); the token is
compared in constant time; a client gets at most RATE requests a second; and
every control action is logged with its source address.
"""
import hmac
import ipaddress
import json
import os
import secrets
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request

PANEL_F = os.path.expanduser("~/.config/flybrain/panel.json")
CFG = os.path.expanduser("~/.config/flybrain")
PLAY = os.path.expanduser("~/flybrain/play")
LOG_F = os.path.join(PLAY, "panel.log")
RATE = 50                       # requests per second per client (was 10: obsfly's HUD polls several feeds at 2 Hz and hit 429s)


def panel_token(path=PANEL_F):
    try:
        with open(path) as f:
            t = json.load(f).get("token")
        if t:
            return t
    except (OSError, ValueError):
        pass
    t = secrets.token_urlsafe(32)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as f:
        json.dump({"token": t}, f)
    os.replace(tmp, path)
    return t


_hits, _hlock = {}, threading.Lock()


def check(client_ip, auth_header, token, now=None):
    """None if allowed, else (http status, reason)."""
    now = time.time() if now is None else now
    try:
        ip = ipaddress.ip_address(client_ip.replace("::ffff:", ""))
    except ValueError:
        return 403, "bad address"
    if not (ip.is_private or ip.is_loopback):
        return 403, "LAN only"
    given = (auth_header or "")
    given = given[7:] if given.startswith("Bearer ") else ""
    if not given or not hmac.compare_digest(given.encode(), token.encode()):
        return 401, "bad or missing token"
    with _hlock:
        recent = [t for t in _hits.get(client_ip, []) if now - t < 1.0]
        if len(recent) >= RATE:
            return 429, "too many requests"
        recent.append(now)
        _hits[client_ip] = recent
    return None


def log_action(client_ip, req, result):
    try:
        with open(LOG_F, "a") as f:
            f.write(f"{time.strftime('%F %T')} {client_ip} {json.dumps(req)} -> {json.dumps(result)}\n")
    except OSError:
        pass


def _json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _last_line(path, contains=""):
    try:
        with open(path, errors="replace") as f:
            lines = [l.rstrip() for l in f if contains in l]
        return lines[-1] if lines else None
    except OSError:
        return None


def _age(path):
    try:
        return round(time.time() - os.path.getmtime(path))
    except OSError:
        return None


def _timer(unit):
    try:
        out = subprocess.run(["systemctl", "--user", "show", unit, "-p", "ActiveState",
                              "-p", "NextElapseUSecRealtime", "-p", "LastTriggerUSec"],
                             capture_output=True, text=True, timeout=5).stdout
        return dict(l.split("=", 1) for l in out.splitlines() if "=" in l)
    except Exception:
        return None


def _service(unit):
    try:
        return subprocess.run(["systemctl", "--user", "is-active", unit],
                              capture_output=True, text=True, timeout=5).stdout.strip()
    except Exception:
        return None


def _ig_days_left(name="instagram.json"):
    """Days of life left on a Meta token as the Pi holds it (refreshed
    weekly here, so a copy held elsewhere has its own, older clock)."""
    c = _json(os.path.join(CFG, name))
    return round((c["expires_at"] - time.time()) / 86400, 1) if c.get("expires_at") else None


def game_health(upstream):
    """The game's /health, parsed into the fields a panel wants."""
    try:
        text = urllib.request.urlopen(upstream + "/health", timeout=2).read().decode()
    except Exception as e:
        return {"up": False, "why": type(e).__name__}
    out = {"up": True}
    ctl = next((l for l in text.splitlines() if l.startswith("control:")), None)
    for line in text.splitlines():
        for tok in line.split():
            if "=" in tok:
                k, _, v = tok.partition("=")
                if k in ("phase", "title", "emu", "frames", "place", "furthest", "areas",
                         "connected", "driver", "still", "menu", "held", "by", "secs_left"):
                    out[k] = v
    if ctl:
        out["control"] = {"held": out.pop("held", "False") == "True",
                          "by": None if out.get("by", "-") == "-" else out.get("by"),
                          "secs_left": int(out["secs_left"]) if out.get("secs_left", "-").isdigit() else None}
        out.pop("by", None); out.pop("secs_left", None)
    return out


_tw = {"at": 0.0, "out": None}
TW_CACHE_S = 60.0
_tw_lock = threading.Lock()
from .site import TWITCH_BROADCASTER_ID as BROADCASTER_ID     # noqa: E402


def twitch_status(chat=None, now=None, cfg_path=None, fetch=None):
    """tools.twitch for obsfly's panel (2026-10-08): live, viewers, title,
    started_at, token_s, chat, checked. READ-ONLY on the token: it is the game
    bridge's (twitch.json), and a second refresher would lock the bridge out --
    which is what a revoked grant looked like on 2026-10-08. Cached TW_CACHE_S.
    chat: the game's own "chat connected=" reading, passed in.
    fetch(url, headers) -> (code, body) is for tests."""
    now = time.time() if now is None else now
    with _tw_lock:
        if _tw["out"] is not None and now - _tw["at"] < TW_CACHE_S:
            return dict(_tw["out"], chat=chat or _tw["out"].get("chat"))
        out = {"live": None, "viewers": None, "title": None, "started_at": None,
               "token_s": None, "chat": chat, "checked": now}
        try:
            from . import twitch as tw
            cfg = _json(cfg_path or tw.CONFIG)
            access = (cfg.get("oauth") or "").split(":", 1)[-1]
            if fetch is None:
                def fetch(url, headers):
                    req = urllib.request.Request(url, headers=headers)
                    try:
                        with urllib.request.urlopen(req, timeout=10) as r:
                            return r.status, json.loads(r.read() or b"{}")
                    except urllib.error.HTTPError as e:
                        return e.code, {}
            code, who = fetch(f"{tw.ID_BASE}/validate", {"Authorization": f"OAuth {access}"})
            if code != 200:
                out["why"] = f"token invalid (HTTP {code})"
            else:
                out["token_s"] = int(who.get("expires_in", 0))
                h = {"Client-Id": cfg.get("client_id", ""), "Authorization": f"Bearer {access}"}
                code, st = fetch(f"{tw.API_BASE}/streams?user_id={BROADCASTER_ID}", h)
                rows = (st or {}).get("data") or []
                if code == 200:
                    out["live"] = bool(rows)
                    if rows:
                        out.update(viewers=rows[0].get("viewer_count"), title=rows[0].get("title"),
                                   started_at=rows[0].get("started_at"))
                if out["title"] is None:            # offline: the channel's title
                    code, ch = fetch(f"{tw.API_BASE}/channels?broadcaster_id={BROADCASTER_ID}", h)
                    rows = (ch or {}).get("data") or []
                    if code == 200 and rows:
                        out["title"] = rows[0].get("title")
        except Exception as e:                      # the panel must always answer
            out["why"] = f"{type(e).__name__}"
        _tw.update(at=now, out=out)
        return out


def status(upstream, relay):
    """Everything the panel shows, in one JSON object."""
    g = game_health(upstream)
    disc = _json(os.path.join(CFG, "discord_state.json"))
    yt = _json(os.path.join(CFG, "youtube_state.json"))
    from .twitch import PANEL_ACTIONS
    return {
        "time": time.time(),
        # What /panel/control implements, so the panel never offers a button
        # the Pi cannot do. "stim" needs no "take".
        "capabilities": list(PANEL_ACTIONS),
        "game": g,
        "relay": relay,
        "tools": {
            "twitch": twitch_status("connected" if g.get("connected") == "True"
                                    else "disconnected" if g.get("connected") == "False"
                                    else None),
            "discord": {"service": _service("flybrain-discord"), "live": disc.get("live"),
                        "last_event_id": disc.get("last_event_id"),
                        "state_age_s": _age(os.path.join(CFG, "discord_state.json")),
                        "picture": _last_line(os.path.join(PLAY, "discord.log"), "picture check")},
            "youtube": {"service": _service("flybrain-youtube"), "was_live": yt.get("was_live"),
                        "viewers": yt.get("viewers"),
                        "viewers_age_s": (round(time.time() - yt["viewers_at"])
                                          if yt.get("viewers_at") else None),
                        "quota": yt.get("quota"), "last_promo": yt.get("last_promo"),
                        # units the /panel/youtube probe spent (same quota)
                        "probe_quota": _json(os.path.join(CFG, "youtube_probe_quota.json")) or None,
                        "last_log": _last_line(os.path.join(PLAY, "youtube.log"))},
            "instagram": {"timer": _timer("flybrain-igstory.timer"),
                          "token_days_left": _ig_days_left(),
                          "last_log": _last_line(os.path.join(PLAY, "ig_story.log"))},
            "threads": {"timer": _timer("flybrain-threads.timer"),
                        "token_days_left": _ig_days_left("threads.json"),
                        "enabled": bool(_json(os.path.join(CFG, "threads.json")).get("enabled")),
                        "last_post": {k: _json(os.path.join(CFG, "threads_state.json")).get(k)
                                      for k in ("last_date", "last_kind", "last_id")},
                        "last_log": _last_line(os.path.join(PLAY, "threads.log"))},
            "backup": {"timer": _timer("flybrain-backup.timer"),
                       "last_log": _last_line(os.path.join(PLAY, "backup.log"))},
            "watchdog": {"service": _service("flybrain-watchdog"),
                         "last_log": _last_line(os.path.join(PLAY, "watchdog.log"))},
            "relay": {"service": _service("flybrain-relay")},
        },
    }


_yt = {"at": 0.0, "out": None}
# Probes closer together than this share one answer. Was 20 s: obsfly's panel
# refreshed every 20 s, 24/7, ~8,600 units/day, and nothing counted them (the
# YouTube quota ran out on 2026-10-07). 120 s caps it near 1,440/day whatever
# a client does, and every unit is now booked (youtube_promo.PROBE_F).
YT_MIN_GAP_S = 120.0
_yt_lock = threading.Lock()


def _book_units(yp, used, now):
    """Add units the relay spent to youtube_promo.PROBE_F, which the promoter's
    budget counts (the relay never writes the promoter's own state)."""
    if used <= 0:
        return
    day = yp.quota_day(now)
    pq = _json(yp.PROBE_F)
    pq = {"day": day, "used": (pq.get("used", 0) if pq.get("day") == day else 0) + used}
    tmp = yp.PROBE_F + ".tmp"
    with open(tmp, "w") as f:
        json.dump(pq, f)
    os.replace(tmp, yp.PROBE_F)


def youtube_end(now=None, client=None):
    """End the live YouTube broadcast now (obsfly's maintenance, 2026-10-08).

    YouTube archives a live stream only if it is 12 h or shorter, and the
    keeper had held one broadcast live for 40 h+ across every maintenance
    window, so no VOD was being kept. Maintenance calls this just before it
    stops OBS; the promoter's keeper sees youtube_promo.END_F, never revives
    the ended broadcast, and starts a fresh one as soon as video arrives.
    Returns {"ok": True, "ended": id} or {"ok": False, "why": ...}.
    client: a youtube_promo.YouTube for tests."""
    import sys
    now = time.time() if now is None else now
    sys.path.insert(0, PLAY)
    import youtube_promo as yp
    with _yt_lock:
        state = {"quota": dict((_json(yp.STATE_F).get("quota") or {}))}
        before = yp.spent(state, now)
        try:
            yt = client or yp.YouTube(_json(yp.CFG_F), state, save=lambda: None)
            yt.state = state
            b = yt.live_broadcast()
            if b is None:
                return {"ok": False, "why": "no live broadcast"}
            bid = b["id"]
            yt.call("liveBroadcasts/transition", yp.COST_WRITE, yp.HARD_CAP, method="POST",
                    params={"broadcastStatus": "complete", "id": bid, "part": "status"})
            tmp = yp.END_F + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"ended": bid, "at": now}, f)
            os.replace(tmp, yp.END_F)
            _yt.update(at=0.0, out=None)              # the probe must not report it live
            return {"ok": True, "ended": bid}
        except Exception as e:
            return {"ok": False, "why": f"{type(e).__name__}: {getattr(e, 'reason', '') or e}"[:200]}
        finally:
            _book_units(yp, yp.spent(state, now) - before, now)


def youtube_probe(now=None):
    """Is YouTube live RIGHT NOW? Asked at call time, for obsfly's maintenance
    retry loop -- the promoter's own view can be 5 minutes stale, and acting
    on stale data restarts streams that had already recovered.

    Two facts, 1 unit each: the broadcast (is there a live one, and its id)
    and the ingest stream (is YouTube RECEIVING video at all). "Receiving
    but no live broadcast" is the stuck-in-Preparing case that a stop/start
    of the encoder cures. Uses the promoter's credentials read-only: nothing
    here writes its state file, so the two can never clobber each other.
    Results are shared for YT_MIN_GAP_S so a tight loop cannot burn quota."""
    with _yt_lock:                  # concurrent requests share one probe
        return _youtube_probe(now)


def _youtube_probe(now=None):
    import sys
    now = time.time() if now is None else now
    if _yt["out"] is not None and now - _yt["at"] < YT_MIN_GAP_S:
        return dict(_yt["out"], cached=True)
    sys.path.insert(0, PLAY)
    import youtube_promo as yp
    out = {"checked_at": now, "cached": False}
    # The promoter's spend so far, so the probe obeys the same caps; what the
    # probe charges on top is added to PROBE_F (STATE_F stays the promoter's).
    q = dict((_json(yp.STATE_F).get("quota") or {}))
    state = {"quota": q}
    before = yp.spent(state, now)
    try:
        yt = yp.YouTube(_json(yp.CFG_F), state, save=lambda: None)
        b = yt.live_broadcast()
        out.update(live=b is not None, broadcast_id=b and b["id"],
                   life_cycle=b and (b.get("status") or {}).get("lifeCycleStatus"))
        d = yt.call("liveStreams", 1, yp.HARD_CAP, params={"part": "status", "mine": "true"})
        streams = [(i.get("status") or {}) for i in d.get("items", [])]
        out["ingest"] = [{"stream_status": st.get("streamStatus"),
                          "health": (st.get("healthStatus") or {}).get("status")} for st in streams]
        out["receiving"] = any(x["stream_status"] == "active" for x in out["ingest"])
    except Exception as e:
        out.update(live=None, why=f"{type(e).__name__}: {getattr(e, 'reason', '') or e}")
    used = yp.spent(state, now) - before
    _book_units(yp, used, now)
    out["units"] = used
    # The promoter's keeper, so maintenance can tell "Pi is still starting the
    # fresh broadcast" from "stuck" and not reattach the encoder mid-go-live.
    k = (_json(yp.STATE_F).get("keeper") or {})
    e = _json(yp.END_F)
    out["keeper"] = {"own": k.get("own"),
                     "end_pending": bool(e.get("ended")) and k.get("end_done") != e.get("at")
                     and now - float(e.get("at", 0)) < yp.END_WINDOW_S,
                     "last_golive": max(k.get("golives") or [0]) or None}
    _yt.update(at=now, out=out)
    return out

