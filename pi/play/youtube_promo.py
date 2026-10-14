#!/usr/bin/env python3
"""Point YouTube viewers at Twitch, where the fly can actually be controlled.

Ian's decision (2026-10-03, relayed by obsfly): "YouTube will essentially
promote the twitch stream." So this is OUTBOUND ONLY. It never reads YouTube
chat: polling liveChatMessages for a 24/7 stream costs ~86,000 units a day
against a 10,000 default quota, and the only thing chat would be read for --
commands -- is exactly what YouTube viewers are being sent to Twitch to do.

Restream sends the SAME picture to both platforms, so none of this can be on
screen: a "watch on Twitch" banner would also show on Twitch, promoting Twitch
to the people already there. Everything lives in YouTube-only text: the
broadcast title and description, and messages in YouTube chat.

What it does, and what each costs (YouTube Data API units, default 10,000/day,
reset at midnight Pacific):
  * a promo line in chat every PROMO_EVERY (15 min)    50 each, ~4,800/day
  * milestones (catch, badge, evolution, wipe)         50 each, ~1,000/day
  * the title and description, set once per broadcast  50 each
  * finding the live broadcast before each post         1 each
A daily budget is kept on disk and enforced HERE rather than discovered from a
403 at 9pm: promos stop at PROMO_CAP, so milestones -- the posts with news in
them -- always have the last stretch of the quota to themselves.

THE LIVE CHAT ID IS NEVER CACHED FOR LONG. A 24/7 stream is not one broadcast:
a "Stream now" session that drops and resumes can come back as a new broadcast
with a new liveChatId. A poster that resolved the id once would work for a day
and then post into a dead chat for ever, silently. So the broadcast is looked
up before posting (1 unit), and a chat that has ended is forgotten at once.

THE TOKEN IS OURS, unlike the Discord poster's Twitch token. Nothing else on
this machine holds a Google credential, so there is no second refresher to
race. Google refresh tokens die after 7 days if the OAuth consent screen is
left in "Testing"; it must be published to Production.

Usage:
  youtube_promo.py setup CLIENT_SECRET.json   store the OAuth client (TV/limited-input type)
  youtube_promo.py login                      device flow: open a URL, type a code
  youtube_promo.py status                     what it would do now; spends 1 unit
  youtube_promo.py                            run (the systemd service)
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
from zoneinfo import ZoneInfo

CFG_F = os.path.expanduser("~/.config/flybrain/youtube.json")
STATE_F = os.path.expanduser("~/.config/flybrain/youtube_state.json")
# Units spent by the relay's /panel/youtube probe (panel.youtube_probe), which
# shares this project's quota but must not write STATE_F. Not counting it let
# obsfly's 20 s refresher spend ~8,600 units/day unseen; quota ran out on
# 2026-10-07 with this counter at 5,745 (found 2026-10-08).
PROBE_F = os.path.expanduser("~/.config/flybrain/youtube_probe_quota.json")
# Written by the relay's yt_end (panel.youtube_end): {"ended": id, "at": t}.
# The keeper treats that broadcast as finished for good and starts a fresh
# one the moment video arrives (2026-10-08: YouTube keeps no VOD of a live
# stream over 12 h, and one broadcast had run 40 h+).
END_F = os.path.expanduser("~/.config/flybrain/youtube_end.json")
END_WINDOW_S = 30 * 60      # an end this recent is still waiting for its replacement
FAST_KEEP_S = 15            # how often the keeper looks while it is
END_GAP_WAIT_S = 300        # after an end: wait to see OBS stop sending, at most this long
TWITCH_F = os.path.expanduser("~/.config/flybrain/twitch.json")
EVENTS_URL = "http://127.0.0.1:8766"

API = "https://www.googleapis.com/youtube/v3"
DEVICE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/youtube"

POLL = 60
PROMO_EVERY = 15 * 60       # obsfly: not every 10 min -- quota-tight AND too frequent
QUIET_AFTER_NEWS = 5 * 60   # a promo straight after a milestone post reads as spam
OFFAIR_RECHECK = 5 * 60     # off air, a promo stays "due"; without this the lookup ran
                            # every minute -- 1,440 units a day spent finding nothing
COST_LIST, COST_WRITE = 1, 50
DAILY_QUOTA = 10_000
PROMO_CAP = 7_000           # promos stop here...
HARD_CAP = 9_500            # ...milestones and titles stop here; the rest is margin
QUOTA_TZ = ZoneInfo("America/Los_Angeles")
MAX_CHAT = 200              # YouTube rejects longer chat messages

NEWS_KINDS = {"catch", "badge", "evolve", "faint", "reset"}     # levels are not news here

# The OAuth app is in Google's "Testing" state (publishing needs a home page,
# privacy policy and terms URLs -- obsfly's hypothesis, 2026-10-03), and in
# Testing a refresh token dies SEVEN DAYS after login. That is a weekly,
# silent death unless it is made loud, and this project has been burned by
# exactly that class of failure repeatedly (chat:edit, the title guard, the
# audio listener). So: a warning a day ahead, and an alert when it dies, both
# in the log AND in Discord #fly-log, where Ian will actually see them.
TESTING_TTL = 7 * 86400
WARN_BEFORE = 86400
DISCORD_F = os.path.expanduser("~/.config/flybrain/discord.json")
ALERT_CHANNEL = "fly-log"
RELOGIN = "python3 ~/flybrain/play/youtube_promo.py login"

TITLE = "Fruit Fly Plays Pokemon 24/7 - chat controls it live on Twitch"
RUN_F = os.path.expanduser("~/.config/flybrain/run.json")


def title(path=None):
    """TITLE led by the run number, "Run 4 · ..." (Ian, 2026-10-08), so viewers
    coming back can see it is a new run. Plain TITLE if run.json is unreadable."""
    try:
        with open(path or RUN_F) as f:
            rid = int(json.load(f)["id"])
        # same naming as flybrain/runinfo.name: runs 1-3 test, 4+ "Lab Run N"
        return f"{'Lab Run ' + str(rid - 3) if rid >= 4 else 'Test Run ' + str(rid)} · {TITLE}"
    except (OSError, ValueError, KeyError, TypeError):
        return TITLE
CATEGORY = "20"             # Gaming (Ian, 2026-10-03). The promoter sends it on every
                            # update, so it is set HERE, not in Studio, or it would be
                            # reverted. Only part=snippet is ever written: audience
                            # (madeForKids) lives in status and is never touched.
PROMOS = (
    "chat can actually control the fly on Twitch: {url}",
    "want to train it yourself? !pain and !pleasure work on Twitch: {url}",
    "this fly takes requests - on Twitch: {url}",
)
NEWS_LINE = {
    "catch": "{text} -- help it catch more on Twitch: {url}",
    "badge": "{text} -- cheer it on (and steer it) on Twitch: {url}",
    "evolve": "{text} -- train it live on Twitch: {url}",
    "faint": "{text} -- it needs help, and Twitch chat can give it: {url}",
    "reset": "{text} Watch it learn from scratch, and steer it on Twitch: {url}",
}


def description(url):
    return (
        f"Control this fly live on Twitch: {url}\n"
        "\n"
        "On Twitch, chat can:\n"
        "  !pain / !pleasure -- train the fly's brain; it learns from what you do\n"
        "  drive it with arrow and button commands when it gets stuck\n"
        "  spend channel points to give it pleasure or pain\n"
        "\n"
        "What this is: a simulation of a real fruit fly's brain -- 139,255 neurons "
        "wired exactly as mapped by the FlyWire connectome -- running on a Raspberry "
        "Pi 5 and playing Pokemon FireRed. Nobody scripts its moves: what it sees "
        "goes into its eyes, and what its neurons do comes out as button presses.\n"
        "\n"
        "Connectome data: FlyWire (flywire.ai), Princeton University and the FlyWire "
        "Consortium. Dorkenwald et al. and Schlegel et al., Nature 2024."
    )


# -- files ----------------------------------------------------------------
def load_json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {} if default is None else default


def save_private(path, data):
    """Atomic and 0600: the config holds a refresh token that can post as the
    channel, and half a state file would re-announce the backlog."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, stat.S_IRUSR | stat.S_IWUSR)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def twitch_url():
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from flybrain.site import TWITCH_CHANNEL
    ch = load_json(TWITCH_F).get("channel") or TWITCH_CHANNEL
    return f"twitch.tv/{ch.lstrip('#').lower()}"


# -- HTTP -----------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, code, reason, body=""):
        super().__init__(f"HTTP {code} {reason}")
        self.code, self.reason, self.body = code, reason, body


def http(url, data=None, headers=None, method=None, form=False, timeout=20):
    """JSON in, JSON out. Raises ApiError carrying YouTube's reason string
    (quotaExceeded, liveChatEnded, ...) because the reason decides what to do."""
    h = dict(headers or {})
    body = None
    if data is not None:
        if form:
            body = urllib.parse.urlencode(data).encode()
            h["Content-Type"] = "application/x-www-form-urlencoded"
        else:
            body = json.dumps(data).encode()
            h["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=body, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        try:
            text = e.read().decode(errors="replace")
        except Exception:
            text = ""
        reason = ""
        try:
            err = json.loads(text).get("error", {})
            if isinstance(err, dict):
                reason = (err.get("errors") or [{}])[0].get("reason", "") or err.get("status", "")
            else:
                reason = str(err)              # the OAuth endpoints: "authorization_pending"
        except (ValueError, AttributeError, IndexError):
            pass
        raise ApiError(e.code, reason, text[:300])


# -- quota ----------------------------------------------------------------
def quota_day(now=None):
    """The quota resets at midnight Pacific, not UTC and not local time."""
    t = datetime.datetime.fromtimestamp(time.time() if now is None else now, QUOTA_TZ)
    return t.strftime("%Y-%m-%d")


def spent(state, now=None):
    q = state.get("quota") or {}
    return q.get("used", 0) if q.get("day") == quota_day(now) else 0


def probe_spent(now=None, path=None):
    try:
        with open(path or PROBE_F) as f:
            q = json.load(f)
    except (OSError, ValueError):
        return 0
    return q.get("used", 0) if q.get("day") == quota_day(now) else 0


def can_spend(state, cost, cap, now=None):
    return spent(state, now) + probe_spent(now) + cost <= cap


def charge(state, cost, now=None):
    day = quota_day(now)
    q = state.get("quota") or {}
    if q.get("day") != day:
        q = {"day": day, "used": 0}
    q["used"] = q.get("used", 0) + cost
    state["quota"] = q


# -- the client -----------------------------------------------------------
class YouTube:
    def __init__(self, cfg, state, save=None, clock=time.time, http_fn=None):
        self.cfg, self.state = cfg, state
        self.save = save or (lambda: None)
        self.clock = clock
        self.http = http_fn or http

    # tokens
    def token(self):
        if self.cfg.get("access_token") and self.cfg.get("expires_at", 0) > self.clock() + 120:
            return self.cfg["access_token"]
        if not self.cfg.get("refresh_token"):
            raise ApiError(401, "not_logged_in")
        try:
            d = self.http(TOKEN_URL, {"client_id": self.cfg["client_id"],
                                      "client_secret": self.cfg.get("client_secret", ""),
                                      "refresh_token": self.cfg["refresh_token"],
                                      "grant_type": "refresh_token"}, form=True)
        except ApiError as e:
            if e.code in (400, 401) and "invalid_grant" in (e.reason + e.body):
                raise ApiError(401, "login_expired", e.body)
            raise
        self.cfg["access_token"] = d["access_token"]
        self.cfg["expires_at"] = self.clock() + int(d.get("expires_in", 3600))
        if d.get("refresh_token"):             # Google rarely rotates it; keep it if it does
            self.cfg["refresh_token"] = d["refresh_token"]
        self.save()
        return self.cfg["access_token"]

    def call(self, path, cost, cap, data=None, method=None, params=None):
        """One metered API call. Charged BEFORE it is made: a call that fails
        still costs quota on YouTube's side, so counting only successes would
        let the books drift under the real figure."""
        if not can_spend(self.state, cost, cap, self.clock()):
            raise ApiError(429, "localBudget")
        charge(self.state, cost, self.clock())
        url = f"{API}/{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        try:
            return self.http(url, data, {"Authorization": "Bearer " + self.token()}, method)
        except ApiError as e:
            if e.code == 401:                  # expired early: refresh once and retry
                self.cfg["expires_at"] = 0
                charge(self.state, cost, self.clock())
                return self.http(url, data, {"Authorization": "Bearer " + self.token()}, method)
            raise

    # the broadcast
    def live_broadcast(self):
        """The broadcast that is live right now, or None. broadcastType=all, or
        the persistent "Stream now" broadcast -- the one a Restream key feeds --
        is not listed at all."""
        d = self.call("liveBroadcasts", COST_LIST, HARD_CAP, params={
            "part": "snippet,status", "broadcastStatus": "active",
            "broadcastType": "all", "maxResults": 5})
        for b in d.get("items", []) or []:
            if (b.get("snippet") or {}).get("liveChatId"):
                return b
        return None

    def viewers(self, video_id):
        """How many are watching right now, or None if YouTube will not say
        (it omits the figure when the count is hidden or the stream is not
        live). 1 unit."""
        d = self.call("videos", COST_LIST, PROMO_CAP, params={
            "part": "liveStreamingDetails", "id": video_id})
        items = d.get("items") or []
        n = ((items[0] if items else {}).get("liveStreamingDetails") or {}).get("concurrentViewers")
        try:
            return int(n)
        except (TypeError, ValueError):
            return None

    def say(self, chat_id, text, cap):
        text = " ".join(str(text).split())
        if len(text) > MAX_CHAT:
            text = text[:MAX_CHAT - 1].rstrip() + "…"
        return self.call("liveChat/messages", COST_WRITE, cap, method="POST",
                         params={"part": "snippet"},
                         data={"snippet": {"liveChatId": chat_id, "type": "textMessageEvent",
                                           "textMessageDetails": {"messageText": text}}})

    def set_title(self, b, title, desc):
        """Set title and description on the live broadcast, only if they
        differ. Through videos.update (a broadcast IS a video, same id), not
        liveBroadcasts.update: that one demands snippet.scheduledStartTime,
        and the persistent Stream-now broadcast has none -- sending
        actualStartTime in its place was refused live on 2026-10-03 with
        scheduledStartTimeModificationNotAllowed. videos.update needs only
        title, description and the category, which the broadcast does carry."""
        sn = b.get("snippet") or {}
        if (sn.get("title") == title and sn.get("description") == desc
                and sn.get("categoryId") == CATEGORY):
            return False
        body = {"id": b["id"], "snippet": {"title": title, "description": desc,
                                           "categoryId": CATEGORY}}
        self.call("videos", COST_WRITE, HARD_CAP, data=body, method="PUT",
                  params={"part": "snippet"})
        return True


# -- keeping YouTube live without anyone ---------------------------------------
# 2026-10-06: YouTube's own auto-created broadcasts (enableAutoStart=true) sat
# in "Preparing" with healthy video for over two hours. They refuse manual
# transitions (403 invalidTransition), so no restart could fix it; a broadcast
# we create ourselves (auto-start AND auto-stop off) went live in 19 s. Ian:
# "make sure all of this is able to self manage". So this does it unattended.
KEEP_EVERY = 120            # check while Twitch is live; 1 unit a check when fine
STUCK_CHECKS = 2            # receiving-but-not-live this many checks in a row (~4 min)
MAX_GOLIVES_PER_HOUR = 2    # then stop and say so, rather than churn broadcasts
OFF_COMPLETE_S = 20 * 60    # Twitch off this long: close our broadcast, not leave it "live"


def find_stream(yt):
    """(stream id, receiving?) of the channel's ingest stream."""
    d = yt.call("liveStreams", COST_LIST, HARD_CAP, params={"part": "id,status", "mine": "true"})
    items = d.get("items") or []
    for it in items:
        if (it.get("status") or {}).get("streamStatus") == "active":
            return it["id"], True
    return (items[0]["id"] if items else None), False


def go_live(yt, stream_id, url, wait=time.sleep):
    """Create our own broadcast, bind it, testing, live. Returns its id or raises."""
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    b = yt.call("liveBroadcasts", COST_WRITE, HARD_CAP, method="POST",
                params={"part": "snippet,status,contentDetails"},
                data={"snippet": {"title": title(), "description": description(url),
                                  "scheduledStartTime": now},
                      "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
                      "contentDetails": {"enableAutoStart": False, "enableAutoStop": False,
                                         "monitorStream": {"enableMonitorStream": True}}})
    bid = b["id"]
    yt.call("liveBroadcasts/bind", COST_WRITE, HARD_CAP, method="POST",
            params={"id": bid, "streamId": stream_id, "part": "id,contentDetails"})
    for target in ("testing", "live"):
        yt.call("liveBroadcasts/transition", COST_WRITE, HARD_CAP, method="POST",
                params={"broadcastStatus": target, "id": bid, "part": "status"})
        for _ in range(30):
            wait(4)
            st = yt.call("liveBroadcasts", COST_LIST, HARD_CAP,
                         params={"part": "status", "id": bid})["items"][0]["status"]["lifeCycleStatus"]
            if st == target:
                break
        else:
            raise ApiError(0, f"stuck before {target} (state {st})")
    try:
        yt.call("videos", COST_WRITE, HARD_CAP, method="PUT", params={"part": "snippet"},
                data={"id": bid, "snippet": {"title": title(), "description": description(url),
                                             "categoryId": CATEGORY}})
    except ApiError:
        pass                                  # cosmetic
    return bid


def end_pending(k, now, path=None):
    """The yt_end waiting for a fresh broadcast, or None."""
    try:
        with open(path or END_F) as f:
            e = json.load(f)
    except (OSError, ValueError):
        return None
    if not e.get("ended") or now - float(e.get("at", 0)) > END_WINDOW_S:
        return None
    return None if k.get("end_done") == e.get("at") else e


def keep_alive(yt, twitch_live, url=None, log=print, alert_fn=None, wait=time.sleep):
    """One check. Returns what it did: 'ok', 'waiting', 'golive', 'gave-up',
    'not-receiving', 'completed', 'off' or None (not due)."""
    s, now = yt.state, yt.clock()
    url = url or twitch_url()
    alert_fn = alert_fn or (lambda t: alert(t, log))
    k = s.setdefault("keeper", {})
    end = end_pending(k, now)
    if now - k.get("at", 0) < (FAST_KEEP_S if end else KEEP_EVERY):
        return None
    k["at"] = now
    if end and k.get("own") == end["ended"]:
        k["own"] = None                       # ended on purpose: never close or revive it
    if twitch_live is False:
        # Off on purpose (or OBS down). Close OUR broadcast after a while so
        # YouTube does not show a dead "live" page for hours.
        if end:
            k["end_gap"] = end["at"]          # OBS stopped: the maintenance gap is seen
        k.setdefault("off_since", now)
        own = k.get("own")
        if own and now - k["off_since"] > OFF_COMPLETE_S:
            try:
                yt.call("liveBroadcasts/transition", COST_WRITE, HARD_CAP, method="POST",
                        params={"broadcastStatus": "complete", "id": own, "part": "status"})
                log(f"youtube: stream off {OFF_COMPLETE_S // 60}+ min; closed broadcast {own}")
            except ApiError as e:
                log(f"youtube: could not close {own}: {e.reason}")
            k["own"] = None
            return "completed"
        return "off"
    k.pop("off_since", None)
    if twitch_live is None:
        return "off"                          # cannot tell; do nothing rather than guess
    b = yt.live_broadcast()
    if b is not None and end and b.get("id") == end["ended"]:
        b = None                              # still listed for a moment after ending
    if b is not None:
        k["stuck"] = 0
        return "ok"
    sid, receiving = find_stream(yt)
    if end and k.get("end_gap") != end["at"]:
        # After a maintenance end, OBS is still sending for a few seconds
        # before it stops. Going live then fails the moment OBS stops
        # (19:40 on 2026-10-08: errorStreamInactive, and one of the hour's
        # two go-live attempts wasted). Wait to see the video STOP first --
        # or 5 min, in case the gap was too short to catch.
        if not receiving or yt.clock() - float(end["at"]) > END_GAP_WAIT_S:
            k["end_gap"] = end["at"]
        if receiving:
            return "waiting"
    if not receiving:
        k["stuck"] = 0
        k["norecv"] = k.get("norecv", 0) + 1
        if k["norecv"] == 5:                  # ~10 min: OBS's YouTube output is down
            alert_fn("Twitch is live but YouTube has received no video for about 10 minutes. "
                     "The YouTube output in OBS has probably stopped -- restart it in OBS.")
        return "not-receiving"
    k["norecv"] = 0
    k["stuck"] = k.get("stuck", 0) + 1
    if k["stuck"] < STUCK_CHECKS and not end:
        return "waiting"                      # give YouTube's own auto-start a chance
    recent = [t for t in k.get("golives", []) if now - t < 3600]
    if len(recent) >= MAX_GOLIVES_PER_HOUR:
        if not k.get("gave_up_alerted"):
            k["gave_up_alerted"] = True
            alert_fn("YouTube is receiving the stream but will not go live, and starting a "
                     f"fresh broadcast {MAX_GOLIVES_PER_HOUR} times in the last hour did not hold. "
                     "Check YouTube Studio; Twitch is unaffected.")
        return "gave-up"
    k["golives"] = recent + [now]
    try:
        bid = go_live(yt, sid, url, wait=wait)
    except ApiError as e:
        log(f"youtube: go-live failed: {e} {e.reason}")
        return "gave-up" if len(k["golives"]) >= MAX_GOLIVES_PER_HOUR else "waiting"
    k.update(own=bid, stuck=0, gave_up_alerted=False)
    if end:
        k["end_done"] = end["at"]
        log(f"youtube: maintenance ended {end['ended']} (VOD); fresh broadcast {bid} now live")
        return "golive"
    log(f"youtube: was stuck (receiving, not live) -- started our own broadcast {bid}, now live")
    return "golive"


# -- the loop -------------------------------------------------------------
def get_events(since):
    try:
        with urllib.request.urlopen(f"{EVENTS_URL}/events?since={int(since)}", timeout=10) as r:
            return (json.loads(r.read()) or {}).get("events", [])
    except Exception:
        return []                              # game restarting; ids mean nothing is lost


def alert(text, log=print):
    """Loud: the log, plus one Discord message. #fly-log is public, so the
    wording is what a viewer may read too -- no credentials, no pings."""
    log(f"youtube: !!! {text}")
    hook = (load_json(DISCORD_F).get("webhooks") or {}).get(ALERT_CHANNEL)
    if not hook:
        return False
    body = {"embeds": [{"title": "YouTube promoter needs attention", "description": text,
                        "color": 0xED4245}], "allowed_mentions": {"parse": []}}
    try:
        req = urllib.request.Request(hook, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "User-Agent": "flybrain-youtube/1.0"})
        urllib.request.urlopen(req, timeout=15).read()
        return True
    except Exception as exc:
        log(f"youtube: could not post the alert to Discord: {type(exc).__name__}")
        return False


def check_login_age(yt, alert_fn):
    """Warn once, a day before the Testing-mode token is due to die."""
    at = yt.cfg.get("logged_in_at")
    if not at or yt.state.get("warned_for") == at:
        return
    if yt.cfg.get("testing") is False:
        return     # issued after the app was published (2026-10-06): no 7-day limit
    left = at + TESTING_TTL - yt.clock()
    if left <= WARN_BEFORE:
        yt.state["warned_for"] = at
        alert_fn("The YouTube login runs out in about "
                 f"{max(0, int(left // 3600))} h (Google Testing mode, 7-day limit). "
                 f"Renew it on the Pi: `{RELOGIN}`. Twitch is unaffected.")


def news_line(ev, url):
    tpl = NEWS_LINE.get(ev.get("kind"))
    return tpl.format(text=ev.get("text", ""), url=url) if tpl else None


def tick(yt, events_fn=get_events, url=None, log=print, alert_fn=None):
    """One pass. Returns what it did, for tests and the log. Never raises for
    an API problem: a promoter must not crash-loop over a quota day."""
    s, now = yt.state, yt.clock()
    url = url or twitch_url()
    alert_fn = alert_fn or (lambda t: alert(t, log))
    done = []
    if s.get("dead_token") and s["dead_token"] == yt.cfg.get("refresh_token"):
        return done                            # alerted already; waiting for a new login
    check_login_age(yt, alert_fn)
    if "last_event" not in s:                  # first run: start from now, not the backlog
        evs = events_fn(0)
        s["last_event"] = max([e["id"] for e in evs] or [0])
        yt.save()
    news = [e for e in events_fn(s["last_event"]) if e.get("id", 0) > s["last_event"]]
    lines = []
    for ev in news:
        s["last_event"] = max(s["last_event"], ev["id"])
        line = news_line(ev, url)
        if line:
            lines.append(line)
    promo_due = now - s.get("last_promo", 0) >= PROMO_EVERY \
        and now - s.get("last_news", 0) >= QUIET_AFTER_NEWS
    if not lines and not promo_due:
        yt.save()
        return done
    if not lines and not s.get("was_live") and now - s.get("offair_check", 0) < OFFAIR_RECHECK:
        return done
    if lines and not can_spend(s, COST_LIST + COST_WRITE, HARD_CAP, now):
        log(f"youtube: quota budget spent for {quota_day(now)}; {len(lines)} milestone(s) not posted")
        yt.save()
        return done
    if not lines and not can_spend(s, COST_LIST + COST_WRITE, PROMO_CAP, now):
        yt.save()
        return done                            # promos have used their share today
    try:
        b = yt.live_broadcast()
    except ApiError as e:
        if e.reason == "login_expired":
            s["dead_token"] = yt.cfg.get("refresh_token")
            alert_fn("The YouTube login has EXPIRED, so YouTube title and chat promos have "
                     f"stopped. Renew it on the Pi: `{RELOGIN}`. Twitch is unaffected.")
        else:
            log(f"youtube: could not look up the broadcast: {e} {e.reason}")
        yt.save()
        return done
    if b is None:
        if s.get("was_live"):
            log("youtube: no live broadcast; holding promos until there is one")
        s["was_live"] = False
        s["offair_check"] = now
        yt.save()
        return done                            # milestones while off-air are dropped, not queued
    if not s.get("was_live"):
        log(f"youtube: live broadcast {b['id']} found")
    elif s.get("broadcast") and s["broadcast"] != b["id"]:
        # A rollover between two checks shows no gap, so it used to pass
        # silently. Logged now: obsfly's twice-daily restarts make these
        # routine, and the log is how anyone can tell the promoter followed.
        log(f"youtube: broadcast rolled over {s['broadcast']} -> {b['id']}")
    s["broadcast"] = b["id"]
    s["was_live"] = True
    chat = b["snippet"]["liveChatId"]
    if s.get("titled") != b["id"]:
        try:
            if yt.set_title(b, title(), description(url)):
                log(f"youtube: title and description set on {b['id']}")
                done.append("title")
            s["titled"] = b["id"]
        except ApiError as e:
            log(f"youtube: title update failed: {e} {e.reason}")
            s["titled"] = b["id"]              # one try per broadcast; do not burn 50/min on it
    to_send = [(l, HARD_CAP, "news") for l in lines]
    if not lines and promo_due:
        # Ian, 2026-10-04: no promo to an empty room. Overnight the chat
        # filled with hours of bot-only lines nobody saw, which is what a
        # newcomer then scrolls back through. The 15-minute rhythm is kept
        # (a skipped slot counts as a promo), so this costs 1 unit a slot.
        # A count YouTube will not give (None) is not zero: post as before.
        try:
            n = yt.viewers(b["id"])
            # Kept for the panel (obsfly): the last count read and when. None
            # means YouTube hid it -- not the same fact as nobody watching.
            s["viewers"], s["viewers_at"] = n, now
        except ApiError:
            n = None
        if n == 0:
            s["last_promo"] = now
            done.append("skipped")
            yt.save()
            return done
        i = s.get("promo_i", 0) % len(PROMOS)
        to_send.append((PROMOS[i].format(url=url), PROMO_CAP, "promo"))
    for text, cap, what in to_send:
        try:
            yt.say(chat, text, cap)
        except ApiError as e:
            log(f"youtube: chat post failed: {e} {e.reason}")
            break                              # liveChatEnded etc.: next tick looks it up afresh
        if what == "news":
            s["last_news"] = now
        else:
            s["last_promo"] = now
            s["promo_i"] = s.get("promo_i", 0) + 1
        done.append(what)
    yt.save()
    return done


def twitch_is_live():
    """True / False / None (cannot tell), from the Discord poster's Twitch check."""
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import discord_poster as dp
        st = dp.get_stream()
        return None if st is None else bool(st)
    except Exception:
        return None


def make_client():
    """The client, with a save that writes what it is USING NOW.

    2026-10-06: save() wrote the dicts captured here at start-up. When main()
    swapped in a fresh login (yt.cfg = disk), every later save wrote the OLD
    credentials back over it -- a new, permanent token was silently replaced
    by the one about to expire. Now it writes yt.cfg / yt.state, and never
    overwrites a NEWER login on disk (one written by `login` mid-tick)."""
    yt = YouTube(load_json(CFG_F), load_json(STATE_F))

    def save():
        disk = load_json(CFG_F)
        if (disk.get("refresh_token") and disk["refresh_token"] != yt.cfg.get("refresh_token")
                and disk.get("logged_in_at", 0) > yt.cfg.get("logged_in_at", 0)):
            yt.cfg = disk                      # a newer login landed: adopt it, never clobber it
        else:
            save_private(CFG_F, yt.cfg)
        save_private(STATE_F, yt.state)
    yt.save = save
    return yt


def main():
    yt = make_client()
    if not yt.cfg.get("refresh_token"):
        print("youtube: not logged in; run `youtube_promo.py login`. Waiting.", flush=True)
    while True:
        disk = load_json(CFG_F)
        if disk.get("refresh_token") and disk["refresh_token"] != yt.cfg.get("refresh_token"):
            yt.cfg = disk                      # a new login was done while running
            print("youtube: new login picked up", flush=True)
        if yt.cfg.get("refresh_token"):
            try:
                keep_alive(yt, twitch_is_live(), log=lambda m: print(m, flush=True))
            except Exception as exc:           # never let the keeper stop the promoter
                print(f"youtube: keeper failed: {type(exc).__name__}: {exc}", flush=True)
            try:
                for what in tick(yt, log=lambda m: print(m, flush=True)):
                    print(f"youtube: posted {what} ({spent(yt.state)} units today)", flush=True)
            except Exception as exc:           # a bug must not crash-loop the service
                print(f"youtube: tick failed: {type(exc).__name__}: {exc}", flush=True)
        else:
            yt.cfg = load_json(CFG_F)          # pick up a login done while running
        # while a maintenance end waits for its fresh broadcast, look often
        time.sleep(FAST_KEEP_S if end_pending(yt.state.get("keeper", {}), time.time()) else POLL)


# -- setup and login ------------------------------------------------------
def setup(path):
    """Store the OAuth client from the JSON Google Cloud lets you download."""
    d = load_json(path)
    c = d.get("installed") or d.get("web") or d
    if not c.get("client_id"):
        sys.exit(f"no client_id in {path}")
    cfg = load_json(CFG_F)
    cfg.update({"client_id": c["client_id"], "client_secret": c.get("client_secret", "")})
    save_private(CFG_F, cfg)
    print(f"stored OAuth client {c['client_id'][:12]}... in {CFG_F} (0600)")


def login():
    cfg = load_json(CFG_F)
    if not cfg.get("client_id"):
        sys.exit("no OAuth client yet: run `youtube_promo.py setup CLIENT_SECRET.json` first")
    d = http(DEVICE_URL, {"client_id": cfg["client_id"], "scope": SCOPE}, form=True)
    print(f"\n  open  {d['verification_url']}\n  enter {d['user_code']}\n"
          f"  (sign in as the YouTube channel; this code lasts {d['expires_in'] // 60} min)\n",
          flush=True)
    wait, end = int(d.get("interval", 5)), time.time() + int(d["expires_in"]) - 10
    while time.time() < end:
        time.sleep(wait)
        try:
            t = http(TOKEN_URL, {"client_id": cfg["client_id"],
                                 "client_secret": cfg.get("client_secret", ""),
                                 "device_code": d["device_code"],
                                 "grant_type": "urn:ietf:params:oauth:grant-type:device_code"},
                     form=True)
        except ApiError as e:
            if e.reason == "authorization_pending":
                continue
            if e.reason == "slow_down":
                wait += 5
                continue
            sys.exit(f"login failed: {e} {e.reason}")
        cfg.update({"refresh_token": t["refresh_token"], "access_token": t["access_token"],
                    "logged_in_at": time.time(),
                    # The OAuth app went to Production on 2026-10-06, so tokens
                    # issued from now on do not die after 7 days.
                    "testing": False,
                    "expires_at": time.time() + int(t.get("expires_in", 3600))})
        save_private(CFG_F, cfg)
        print("logged in; the service picks this up within a minute")
        return
    sys.exit("the code expired before it was entered; run login again")


def status():
    yt = make_client()
    if not yt.cfg.get("client_id"):
        sys.exit("no OAuth client yet: run `youtube_promo.py setup CLIENT_SECRET.json`")
    if not yt.cfg.get("refresh_token"):
        sys.exit("OAuth client stored, not logged in: run `youtube_promo.py login`")
    print(f"spent today ({quota_day()} Pacific): {spent(yt.state)} units "
          f"(promos stop at {PROMO_CAP}, everything at {HARD_CAP})")
    b = yt.live_broadcast()
    yt.save()
    if b is None:
        print("no live broadcast right now")
    else:
        sn = b["snippet"]
        print(f"live: {b['id']}  title={sn.get('title')!r}  chat={sn.get('liveChatId', '')[:12]}...")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    if cmd == "setup" and len(sys.argv) == 3:
        setup(sys.argv[2])
    elif cmd == "login":
        login()
    elif cmd == "status":
        status()
    elif cmd == "run":
        main()
    else:
        sys.exit(__doc__)
