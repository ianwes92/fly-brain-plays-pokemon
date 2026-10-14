#!/usr/bin/env python3
"""One Threads post a day, at 18:00, to @example.user: real numbers + a clip.

Ian's request (2026-10-07, relayed by obsfly). Three templates, rotated:
  A  daily stats      most days
  B  science angle    Wednesdays and Saturdays (the asked-for 1-2x a week)
  C  milestone        replaces the day's post when something real happened
                      since the last post: a badge, an evolution, a catch, or
                      an area visited for the first time this run
Level-ups do NOT trigger C: the team levels up most days, so they would
replace A almost every day. LEVEL_IS_MILESTONE flips that.

EVERY NUMBER IS MEASURED. The game's counters (tiles, bumps, spikes) are per
process and reset on every restart, so `sample` (a 10-minute timer) keeps a
running total that survives restarts, and a post's "last 24h" is the
difference between two samples. When fewer than 20 hours of samples exist the
post names the real window ("Last 9h") instead of pretending. Anything that
cannot be read is left out of the post, never guessed.

The clip is posted the way Stories are: rendered 1080x1920 by ig_story.render,
served once through ig_story's one-file cloudflared tunnel, fetched by Meta,
torn down. A milestone's Short is used when it has one, else a clean clip of
current play.

The token (60 days) lives in ~/.config/flybrain/threads.json, 0600, never in
git or the backup. It is refreshed weekly; #fly-log is told when it is under
7 days from expiry.

Usage:
  threads_post.py set-token       read the token from stdin (not argv: no shell history)
  threads_post.py status          account + token age; posts nothing
  threads_post.py sample          record the counters (the 10-minute timer)
  threads_post.py dry-run [A|B|C] today's post text and a rendered clip; publishes nothing
  threads_post.py run             the scheduled job (does nothing until `enable`)
  threads_post.py enable|disable  Ian's approval switch for real posts
"""
import json
import os
import re
import shutil
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))
import ig_story as ig                      # noqa: E402  the clip, render and tunnel

CFG_F = os.path.expanduser("~/.config/flybrain/threads.json")
STATE_F = os.path.expanduser("~/.config/flybrain/threads_state.json")
STATS_F = os.path.expanduser("~/.config/flybrain/stats_log.json")
PLACES_F = os.path.expanduser("~/.config/flybrain/places.json")
EVENTS_F = ig.EVENTS_F
RUNS_DIR = os.path.expanduser("~/flybrain/runs")
SHORTS = ig.SHORTS
API = "https://graph.threads.net/v1.0"
GAME = ig.GAME

TOKEN_LIFE = 60 * 86400
REFRESH_EVERY = 7 * 86400
WARN_LEFT = 7 * 86400
KEEP_SAMPLES_S = 8 * 86400
FULL_DAY_H = 20                 # below this many hours of samples, the post names the window
MAX_CHARS = 500
from flybrain.site import TWITCH_URL as LINK, YOUTUBE_URL as YOUTUBE   # noqa: E402
# Ian, 2026-10-07: promote both; chat control happens only on Twitch.
WATCH = f"🎮 Chat controls it on {LINK}\n📺 Also live on {YOUTUBE}"
TAG = "#FruitFlyPlaysPokemon"
SCIENCE_TAG = "#neuroscience"
SCIENCE_DAYS = {2, 5}           # tm_wday: Wednesday, Saturday
LEVEL_IS_MILESTONE = False
CLIP_WAIT_S = 20 * 60


def log(msg):
    print(f"{time.strftime('%F %T')} threads: {msg}", flush=True)


def alert(text):
    log(f"!!! {text}")
    hook = (ig.load_json(ig.DISCORD_F).get("webhooks") or {}).get("fly-log")
    if not hook:
        return
    body = {"embeds": [{"title": "Threads post needs attention", "description": text,
                        "color": 0xED4245}], "allowed_mentions": {"parse": []}}
    try:
        req = urllib.request.Request(hook, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "flybrain-threads/1.0"})
        urllib.request.urlopen(req, timeout=15).read()
    except Exception as exc:
        log(f"could not post the alert: {type(exc).__name__}")


# -- the API ------------------------------------------------------------------
class ThreadsError(Exception):
    pass


def api(method, path, token, base=API, **params):
    params["access_token"] = token
    q = urllib.parse.urlencode(params)
    url, data = f"{base}/{path}", None
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
        raise ThreadsError(f"HTTP {e.code}: {msg[:300]}")
    except urllib.error.URLError as e:
        raise ThreadsError(f"network: {e.reason}")


def token(cfg, now=None, call=api, save=lambda c: ig.save_private(CFG_F, c), warn=alert):
    now = time.time() if now is None else now
    t = cfg.get("token")
    if not t:
        raise ThreadsError("no token: run `threads_post.py set-token` and paste it")
    got = next((cfg[k] for k in ("refreshed_at", "set_at") if cfg.get(k) is not None), now)
    if now - got > REFRESH_EVERY:
        try:
            d = call("GET", "refresh_access_token", t, base="https://graph.threads.net",
                     grant_type="th_refresh_token")
            cfg.update(token=d["access_token"], refreshed_at=now,
                       expires_at=now + int(d.get("expires_in", TOKEN_LIFE)))
            save(cfg)
            log("token refreshed (" + ("SAME string" if d["access_token"] == t else "NEW string") + ")")
            t = cfg["token"]
        except (ThreadsError, KeyError, ValueError) as exc:
            log(f"token refresh failed: {exc}")
    left = (cfg.get("expires_at") or got + TOKEN_LIFE) - now
    if left < WARN_LEFT:
        warn(f"The Threads token expires in {max(0, int(left // 86400))} day(s) and refreshing "
             "it has failed. Make a new long-lived token in the Meta dashboard and run "
             "`python3 ~/flybrain/play/threads_post.py set-token` on the Pi (paste it, then Enter, "
             "then Ctrl-D). Nothing else is affected.")
    return t


# -- the numbers ----------------------------------------------------------------
def parse_health(text):
    """The counters a post uses, from the game's /health. Missing -> absent."""
    out = {}
    pats = {"frames": r"^ok frames=(\d+)", "tiles": r"moved: tiles=(\d+)",
            "bumps": r"moved:.* bumps=(\d+)", "spikes": r"spikes=(\d+)",
            "place": r"place=(-?\d+)", "people": r"learned: people=([-+]?[\d.]+)",
            "exits": r"learned:.* exits=([-+]?[\d.]+)", "lv": r"^team: lv=(\d+)",
            "badges": r"^team:.* badges=(\d+)", "caught": r"^team:.* caught=(\d+)",
            # gauges for the run reports' time series (2026-10-08), not totalled
            "furthest": r"^progress: furthest=(\d+)", "areas": r"^progress:.* areas=(\d+)",
            "human_share": r"human_share_5min=([\d.]+)"}
    for k, p in pats.items():
        m = re.search(p, text, re.M)
        if m:
            out[k] = float(m.group(1)) if k in ("people", "exits", "human_share") else int(m.group(1))
    m = re.search(r"^cb2:.* battle=(True|False)", text, re.M)
    if m:
        out["battle"] = m.group(1) == "True"
    m = re.search(r"^attrib: (.*)$", text, re.M)
    if m:
        for k, v in re.findall(r"(\w+)=(\d+)\b", m.group(1)):
            if k in EXTRA:
                out[k] = int(v)
    m = re.search(r"^team:.* lead=(.+)$", text, re.M)
    if m and m.group(1).strip() not in ("", "-"):
        out["lead"] = m.group(1).strip()
    out["playing"] = "\nphase=playing " in "\n" + text
    return out


def game_health():
    try:
        return parse_health(urllib.request.urlopen(GAME + "/health", timeout=4).read().decode())
    except Exception:
        return None


CORE = ("tiles", "bumps", "spikes")
# Fly-alone vs human-assisted (flybrain/attrib.py), and pain/pleasure by source.
EXTRA = ("tiles_clean", "tiles_assisted", "bumps_clean", "bumps_assisted",
         "human_pain", "human_pleasure", "game_pain", "game_pleasure", "rule_pain")
COUNTERS = CORE + EXTRA
# What the fly is like at the moment of the sample, for charts (not totalled).
GAUGES = ("battle", "people", "exits", "furthest", "areas", "human_share")


def _rows(log_d):
    """Samples as dicts. The first version stored [t, tiles, bumps, spikes]."""
    out = []
    for x in log_d.get("samples") or []:
        out.append(x if isinstance(x, dict) else {"t": x[0], **dict(zip(CORE, x[1:]))})
    return out


def add_sample(log_d, h, now):
    """Fold one reading into the running totals. A restart shows as the frame
    counter going backwards; the new process's counts are then all new. A
    counter the game does not report (an older build) is simply not totalled."""
    if not h or not all(k in h for k in CORE + ("frames",)):
        return log_d
    last = log_d.get("last")
    tot = dict(log_d.get("total") or {})
    restarted = last is None or h["frames"] < last.get("frames", 0)
    for k in COUNTERS:
        if k not in h:
            continue
        prev = 0 if restarted else last.get(k, 0)
        tot[k] = tot.get(k, 0) + max(0, h[k] - prev)
    log_d["last"] = {k: h[k] for k in COUNTERS + ("frames",) if k in h}
    log_d["total"] = tot
    samples = [x for x in _rows(log_d) if now - x["t"] <= KEEP_SAMPLES_S]
    samples.append({"t": now, **tot})
    log_d["samples"] = samples
    return log_d


def window(log_d, now, hours=24):
    """(deltas, hours covered) across the last `hours`, or (None, 0). Deltas
    hold every counter known at both ends; a counter added later gets
    deltas[k + "_h"], the hours IT covers, so its own window can be named."""
    s = _rows(log_d)
    start = [x for x in s if x["t"] >= now - hours * 3600 - 600]
    if len(start) < 2:
        return None, 0
    b = s[-1]
    hours_ = (b["t"] - start[0]["t"]) / 3600
    if hours_ < 1:                  # too short to say anything honest about a day
        return None, 0
    out = {}
    for k in COUNTERS:
        if k not in b:
            continue
        a = next((x for x in start if k in x), None)
        if a is None or (b["t"] - a["t"]) / 3600 < 1:
            continue
        out[k] = b[k] - a[k]
        out[k + "_h"] = (b["t"] - a["t"]) / 3600
    if not all(k in out for k in CORE):
        return None, 0
    return out, hours_


def run_day(now=None, runs=RUNS_DIR):
    """Day N of the current run: the newest archive in runs/ is when it began."""
    best = None
    try:
        for name in os.listdir(runs):
            m = re.search(r"-(\d{8})-\d{6}$", name)
            if m and (best is None or m.group(1) > best):
                best = m.group(1)
    except OSError:
        return None
    if best is None:
        return None
    start = time.mktime(time.strptime(best, "%Y%m%d"))
    today = time.localtime(time.time() if now is None else now)
    return int((time.mktime(time.strptime(time.strftime("%Y%m%d", today), "%Y%m%d")) - start)
               // 86400) + 1


def place_name(pid):
    from flybrain.twitch import PLACES
    return PLACES.get(pid)


def nice(name):
    return " ".join(w.capitalize() for w in str(name).split())


def big(n):
    if n >= 1e9:
        return f"{n / 1e9:.1f} billion"
    if n >= 1e6:
        return f"{n / 1e6:.0f} million"
    return f"{n:,}"


# -- milestones ---------------------------------------------------------------
HELPED = " Chat lent a hand on this one. 🤝"
PROGRESS_F = os.path.expanduser("~/flybrain/data/inputs/progress.jsonl")


def area_credit(name, path=PROGRESS_F):
    """'fly' / 'assisted' for the latest first visit to `name`, or None."""
    by = None
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("kind") == "area" and r.get("text") == name:
                    by = r.get("by")
    except OSError:
        pass
    return by


def milestone(state, events, places, credit_of=area_credit):
    """The best real milestone since the last post: (text, short path or None, newest id).
    A milestone made with a human hand on the keys says so (attrib.py)."""
    from flybrain.twitch import GYMS
    last = state.get("last_event", 0)
    # Only THIS run's news: event ids keep counting across resets, so without
    # this run 2's badge was about to be posted as run 4's (audit, 2026-10-08).
    last = max([last] + [e.get("id", 0) for e in events if e.get("kind") == "reset"])
    rank = {"badge": 5, "evolve": 4, "catch": 3, "level": 1 if LEVEL_IS_MILESTONE else 0}
    best = None
    for e in events:
        r = rank.get(e.get("kind"), 0)
        if e.get("id", 0) <= last or r == 0:
            continue
        if best is None or (r, e["id"]) > (best[0], best[1]["id"]):
            best = (r, e)
    newest = max([e.get("id", 0) for e in events] or [last])
    if best:
        e, txt = best[1], best[1].get("text", "")
        if e["kind"] == "badge":
            m = re.search(r"\((\d+) in total\)", txt)
            n = int(m.group(1)) if m else 0
            who = f" beat {GYMS[n - 1][0]} and" if 0 < n <= len(GYMS) else ""
            line = f"The fly just{who} earned {'its first' if n == 1 else 'a'} gym badge!"
        elif e["kind"] == "evolve":
            m = re.match(r"(.+?) EVOLVED INTO (.+?)!?$", txt)
            line = (f"The fly's {nice(m.group(1))} just evolved into {nice(m.group(2))}!"
                    if m else f"The fly just saw an evolution: {txt}")
        elif e["kind"] == "catch":
            m = re.match(r"CAUGHT (.+?)!?$", txt)
            line = f"The fly just caught a {nice(m.group(1))}!" if m else f"The fly just: {txt}"
        else:
            m = re.match(r"(.+?) grew to Lv (\d+)", txt)
            line = (f"The fly's {nice(m.group(1))} just hit Lv {m.group(2)}!" if m else txt)
        short = e.get("short")
        if short and (not os.path.exists(short) or os.path.exists(short + ".failed")):
            short = None
        return line + (HELPED if e.get("by") == "assisted" else ""), short, newest
    seen = set(state.get("places_seen") or [])
    new = [p for p in (places.get("been") or []) if p not in seen]
    names = [place_name(p) for p in new if place_name(p)]
    if names:
        line = f"The fly just reached {names[-1]} for the first time!"
        return line + (HELPED if credit_of(names[-1]) == "assisted" else ""), None, newest
    return None, None, newest


# -- the templates ------------------------------------------------------------
RUN_F = os.path.expanduser("~/.config/flybrain/run.json")


def run_prefix(path=None):
    """'Run 4 · ' (Ian, 2026-10-08: every post names the run), or ''."""
    try:
        with open(path or RUN_F) as f:
            rid = int(json.load(f)["id"])
        # same naming as flybrain/runinfo.name: runs 1-3 test, 4+ "Lab Run N"
        return f"{'Lab Run ' + str(rid - 3) if rid >= 4 else 'Test Run ' + str(rid)} · "
    except (OSError, ValueError, KeyError, TypeError):
        return ""


def compose(kind, day, h, win, hours, places, ms_line=None):
    """The post text. Lines that need a number we do not have are dropped."""
    lines = []
    head = f"Day {day} of a fruit fly brain playing Pokémon 🪰🧠" if day else \
        "A fruit fly brain is playing Pokémon 🪰🧠"
    here = place_name((h or {}).get("place"))
    furthest = place_name(places.get("furthest"))
    if kind == "C":
        lines.append(f"🚨 {ms_line}")
        lines.append("Nobody scripts it: a full simulated fly brain (139,255 neurons) plays "
                     "live, 24/7.")
        lines += [WATCH, TAG]
    elif kind == "B":
        lines.append("Two kinds of behaviour in one fly brain 🧠")
        # The fly's OWN moves only: a move made while chat was pressing (or
        # within attrib.CLEAN_MS of it) says nothing about the reflex. With no
        # fly-alone numbers yet, the line is left out, never filled from the
        # mixed counts.
        own = (win or {}).get("tiles_clean", 0) + (win or {}).get("bumps_clean", 0)
        if own > 0:
            pct = round(100 * win["bumps_clean"] / own)
            span = _span(win["tiles_clean_h"])
            lines.append(f"Hard-wired: walk into something, stop and turn. A reflex, not "
                         f"learning: {pct}% of its own moves {span} ended in a bump "
                         f"(chat's presses left out).")
        if h and "people" in h and "exits" in h:
            def feel(v):
                return "likes" if v > 0.05 else "dislikes" if v < -0.05 else "is neutral on"
            shaped = ""
            if win and "human_pain" in win and "game_pain" in win:
                hp = win["human_pain"] + win.get("human_pleasure", 0)
                gp = win["game_pain"] + win.get("game_pleasure", 0) + win.get("rule_pain", 0)
                shaped = (f", shaped {_span(win['human_pain_h'])} by {hp} chat !pain/!pleasure "
                          f"and {gp} automatic ones")
            lines.append(f"Learned: dopamine sets its opinions. It {feel(h['people'])} people "
                         f"({h['people']:+.2f}) and {feel(h['exits'])} exits "
                         f"({h['exits']:+.2f}){shaped}.")
        lines.append("Watch it decide live:")
        lines.append(WATCH)
        lines.append(SCIENCE_TAG)
    else:
        lines.append(head)
        bits = []
        if win:
            label = "Last 24h" if hours >= FULL_DAY_H else f"Last {max(1, round(hours))}h"
            steps = f"{win['tiles']:,} steps"
            own = win.get("tiles_clean", 0) + win.get("tiles_assisted", 0)
            # only when the split covers the same span as the total
            if own and abs(win.get("tiles_clean_h", 0) - win["tiles_h"]) < 0.5:
                steps += f" ({round(100 * win['tiles_clean'] / own)}% on its own)"
            bits.append(steps)
            if win["spikes"] > 0:
                bits.append(f"{big(win['spikes'])} neuron spikes")
            stat = f"{label}: " + ", ".join(bits)
        else:
            stat = ""
        if h and h.get("lead") and h.get("lv"):
            stat += (f", {nice(h['lead'])} at Lv {h['lv']}" if stat
                     else f"Its strongest Pokémon: {nice(h['lead'])}, Lv {h['lv']}")
        if stat:
            lines.append(stat + ".")
        if here and furthest and here != furthest:
            lines.append(f"Right now it's in {here}; the furthest it has been is {furthest}.")
        elif here:
            lines.append(f"Right now it's exploring {here}.")
        lines.append("It's a full simulated fly brain (139,255 neurons), live 24/7.")
        lines += [WATCH, TAG]
    lines[0] = run_prefix() + lines[0]
    text = "\n".join(lines)
    while len(text) > MAX_CHARS and len(lines) > 3:
        lines.pop(-3)                       # drop the line before the link and tag
        text = "\n".join(lines)
    return text


def _span(h):
    return "today" if h >= FULL_DAY_H else f"in the last {max(1, round(h))}h"


def choose(now, ms_line):
    if ms_line:
        return "C"
    return "B" if time.localtime(now).tm_wday in SCIENCE_DAYS else "A"


# -- the clip ------------------------------------------------------------------
MIN_CLIP_S = 15


def duration(path):
    import subprocess
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "csv=p=0", path], capture_output=True, text=True, timeout=30)
        return float(r.stdout.strip())
    except (ValueError, OSError, subprocess.SubprocessError):
        return 0.0


def make_clip(short, out, caption, wait_s=CLIP_WAIT_S):
    """A rendered vertical clip: the milestone's Short, else current play."""
    made = None
    try:
        if short:
            src = short
        else:
            src, _cap = ig.clip_now(wait_s=wait_s)
            made = src
            if duration(src) < MIN_CLIP_S:
                # the game's clip buffer refills after a restart; wait it out once
                os.remove(src)
                made = None
                time.sleep(MIN_CLIP_S + 15)
                src, _cap = ig.clip_now(wait_s=0)
                made = src
        ig.render(src, "short", caption, out)
    finally:
        if made and os.path.exists(made):
            os.remove(made)
    return out


def publish(path, text, tok):
    srv, name = ig.serve_one(path)
    proc = None
    try:
        proc, base = ig.tunnel(srv.server_address[1])
        url = f"{base}/{name}"
        if not ig.reachable(url):
            raise ThreadsError("the tunnel never served the clip")
        c = api("POST", "me/threads", tok, media_type="VIDEO", video_url=url, text=text)
        cid = c.get("id")
        if not cid:
            raise ThreadsError(f"no container id: {c}")
        for _ in range(60):
            time.sleep(5)
            st = api("GET", cid, tok, fields="status,error_message")
            if st.get("status") == "FINISHED":
                break
            if st.get("status") in ("ERROR", "EXPIRED"):
                raise ThreadsError(f"Threads could not process the clip: {st}")
        else:
            raise ThreadsError("Threads took too long to process the clip")
        return api("POST", "me/threads_publish", tok, creation_id=cid).get("id")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(10)
            except Exception:
                proc.kill()
        srv.shutdown()
        srv.server_close()
        log("tunnel and server torn down")


# -- the job ---------------------------------------------------------------------
def build(state, now=None, force=None):
    """Everything about today's post except the clip: (kind, text, short, caption, newest)."""
    now = time.time() if now is None else now
    h = game_health()
    places = ig.load_json(PLACES_F)
    events = ig.load_json(EVENTS_F).get("events") or []
    ms_line, short, newest = milestone(state, events, places)
    kind = force or choose(now, ms_line)
    if kind == "C" and not ms_line:
        ms_line = "(no milestone since the last post -- C would not run today)"
    win, hours = window(ig.load_json(STATS_F), now)
    text = compose(kind, run_day(now), h, win, hours, places, ms_line)
    caption = {"C": (ms_line or "").replace("The fly just ", "").rstrip("!")[:40] or "A NEW MOMENT",
               "B": "WATCH IT DECIDE LIVE"}.get(kind, "TODAY ON THE STREAM")
    return kind, text, (short if kind == "C" else None), caption, newest, h, places


def run(state, cfg):
    if not cfg.get("enabled"):
        log("not enabled yet (Ian approves the text first): nothing posted")
        return None
    today = time.strftime("%F")
    if state.get("last_date") == today:
        log("already posted today")
        return None
    kind, text, short, caption, newest, h, places = build(state)
    if not h or not h.get("playing") or ig.twitch_live() is False:
        alert("Today's Threads post was skipped because the game or the stream is not live -- "
              "a post sending people to a dead stream would mislead them.")
        return None
    tok = token(cfg)
    out = os.path.join(tempfile.gettempdir(), f"threads-{int(time.time())}.mp4")
    try:
        make_clip(short, out, caption)
        pid = publish(out, text, tok)
    finally:
        if os.path.exists(out):
            os.remove(out)
    state.update(last_date=today, last_post=time.time(), last_id=pid, last_kind=kind,
                 last_text=text, last_event=newest, places_seen=places.get("been") or [])
    ig.save_private(STATE_F, state)
    log(f"posted {pid} (template {kind}): {text!r}")
    return pid


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    cfg, state = ig.load_json(CFG_F), ig.load_json(STATE_F)
    if "last_event" not in state and cmd in ("dry-run", "run", "enable"):
        # The milestone baseline: only what happens from here on counts, so
        # the first post does not announce run 1's evolution or old areas.
        state["last_event"] = max([e.get("id", 0) for e in
                                   ig.load_json(EVENTS_F).get("events") or []] or [0])
        state["places_seen"] = ig.load_json(PLACES_F).get("been") or []
        ig.save_private(STATE_F, state)
    if cmd == "set-token":
        t = sys.stdin.read().strip()
        if not t or any(c.isspace() for c in t):
            sys.exit("no token read (paste it, Enter, then Ctrl-D)")
        me = api("GET", "me", t, fields="id,username")
        now = time.time()
        cfg.update(token=t, set_at=now, refreshed_at=now, expires_at=now + TOKEN_LIFE,
                   user_id=me.get("id"), username=me.get("username"))
        ig.save_private(CFG_F, cfg)
        print(f"stored (0600). token works for @{me.get('username')}.")
    elif cmd == "status":
        tok = token(cfg)
        me = api("GET", "me", tok, fields="id,username")
        age = (time.time() - (cfg.get("refreshed_at") or cfg.get("set_at") or time.time())) / 86400
        left = ((cfg.get("expires_at") or 0) - time.time()) / 86400
        print(f"@{me.get('username')} | token refreshed {age:.1f} days ago, ~{left:.0f} days left | "
              f"enabled={bool(cfg.get('enabled'))} | last post: {state.get('last_date')} "
              f"({state.get('last_kind')})")
    elif cmd == "sample":
        d = ig.load_json(STATS_F)
        now = time.time()
        add_sample(d, game_health(), now)
        ig.save_private(STATS_F, d)
        # stats_log keeps 8 days for the post's windows; this keeps every
        # sample indefinitely (backed up), one file per day.
        if d.get("samples") and d["samples"][-1].get("t") == now:
            folder = os.path.expanduser("~/flybrain/data/samples")
            os.makedirs(folder, exist_ok=True)
            h = game_health() or {}
            row = dict(d["samples"][-1], **{k: h[k] for k in GAUGES if k in h})
            with open(os.path.join(folder, time.strftime("%Y-%m-%d") + ".jsonl"), "a") as f:
                f.write(json.dumps(row) + "\n")
    elif cmd == "dry-run":
        force = argv[2].upper() if len(argv) > 2 and argv[2].upper() in "ABC" else None
        kind, text, short, caption, *_ = build(state, force=force)
        print(f"--- template {kind} ({len(text)} chars) ---\n{text}\n---")
        out = os.path.join(SHORTS, f"threads-dryrun-{kind}.mp4")
        make_clip(short, out, caption, wait_s=60)
        print(f"clip ({'milestone Short' if short else 'current play'}): {out}")
        print("nothing was published")
    elif cmd in ("enable", "disable"):
        cfg["enabled"] = cmd == "enable"
        ig.save_private(CFG_F, cfg)
        print(f"real posts {'ENABLED' if cfg['enabled'] else 'disabled'}")
    elif cmd == "run":
        try:
            run(state, cfg)
        except (ThreadsError, ig.IGError, OSError) as exc:
            alert(f"Today's Threads post could not be published: {exc}")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
