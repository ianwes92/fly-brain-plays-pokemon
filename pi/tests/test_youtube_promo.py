"""YouTube promotes Twitch: outbound only, within quota, never into a dead chat.

The silent failures guarded here: the quota running out mid-evening because
nothing counted it, promos eating the share milestones need, a cached chat id
that stops working when the broadcast rotates, the backlog fired on first run,
and a title update retried every minute at 50 units a go.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))

import youtube_promo as yp
import tempfile as _tmp
yp.PROBE_F = _tmp.mktemp()          # never the live probe tally (panel.youtube_probe)
yp.RUN_F = "/nonexistent/run.json"  # titles tested without a run number
yp.END_F = _tmp.mktemp()             # never the live maintenance-end marker

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


class FakeYT:
    """Stands in for Google: records calls, serves a broadcast."""
    def __init__(self):
        self.calls = []
        self.live = {"id": "B1", "snippet": {"liveChatId": "C1", "title": "old",
                                             "description": "", "scheduledStartTime": "2026-10-03T00:00:00Z"}}
        self.fail_chat = None

    def __call__(self, url, data=None, headers=None, method=None, form=False, timeout=20):
        self.calls.append((url, data, method))
        if url == yp.TOKEN_URL:
            return {"access_token": "AT", "expires_in": 3600}
        if "liveBroadcasts?" in url and method is None:
            return {"items": [self.live] if self.live else []}
        if "videos?" in url and method == "PUT":
            return {}
        if "videos?" in url and "liveStreamingDetails" in url:
            v = getattr(self, "viewers", 3)
            det = {} if v is None else {"concurrentViewers": str(v)}
            return {"items": [{"liveStreamingDetails": det}]}
        if "liveChat/messages" in url:
            if self.fail_chat:
                raise yp.ApiError(403, self.fail_chat)
            return {}
        raise AssertionError(url)

    def posts(self):
        return [d["snippet"]["textMessageDetails"]["messageText"] for u, d, m in self.calls
                if "liveChat/messages" in u]

    def chats(self):
        return [d["snippet"]["liveChatId"] for u, d, m in self.calls if "liveChat/messages" in u]


now = [1_790_000_000.0]
events = []


def ev_fn(since):
    return [e for e in events if e["id"] > since]


def client(fake, state=None):
    return yp.YouTube({"client_id": "x", "refresh_token": "R"}, state if state is not None else {},
                      clock=lambda: now[0], http_fn=fake)


URL = "twitch.tv/examplechannel"
quiet = lambda m: None

# -- first run: the backlog is not news ------------------------------------
events[:] = [{"id": 1, "kind": "catch", "text": "CAUGHT PIDGEY!"},
             {"id": 2, "kind": "badge", "text": "EARNED A BADGE!"}]
f = FakeYT()
yt = client(f)
did = yt and yp.tick(yt, ev_fn, URL, quiet)
check("first run posts no old milestones", not any("PIDGEY" in p for p in f.posts()))
check("it starts from the newest event", yt.state["last_event"] == 2)
check("but the first promo goes out (none has been sent yet)", did == ["title", "promo"])
check("title set once on the new broadcast", sum(1 for u, d, m in f.calls if m == "PUT") == 1)
put = [d for u, d, m in f.calls if m == "PUT"][0]
check("the title is the Twitch-promoting one", put["snippet"]["title"] == yp.TITLE)
check("the description leads with the Twitch link", put["snippet"]["description"].startswith(f"Control this fly live on Twitch: {URL}"))
check("it credits FlyWire", "FlyWire" in put["snippet"]["description"])
check("it goes through videos.update, which needs no scheduledStartTime",
      any("/videos?" in u for u, d, m in f.calls if m == "PUT") and "scheduledStartTime" not in put["snippet"])
check("the category is Gaming", put["snippet"]["categoryId"] == "20")
check("only the snippet is written, never status (madeForKids)",
      all("part=snippet" in u and "status" not in u for u, d, m in f.calls if m == "PUT")
      and all("status" not in d for u, d, m in f.calls if m == "PUT"))
check("the promo carries the link", URL in f.posts()[0])

# -- nothing due: no calls at all ------------------------------------------
n = len(f.calls)
now[0] += 60
check("nothing due -> nothing done", yp.tick(yt, ev_fn, URL, quiet) == [])
check("and no quota spent finding the broadcast", len(f.calls) == n)

# -- the title is not set again on the same broadcast -----------------------
now[0] += yp.PROMO_EVERY
yp.tick(yt, ev_fn, URL, quiet)
check("no second title update on the same broadcast", sum(1 for u, d, m in f.calls if m == "PUT") == 1)
check("the promos rotate", f.posts()[0] != f.posts()[1])

# -- a milestone posts at once, with the link -------------------------------
events.append({"id": 3, "kind": "catch", "text": "CAUGHT SPEAROW!"})
now[0] += 60
did = yp.tick(yt, ev_fn, URL, quiet)
check("a catch is posted", did == ["news"] and "CAUGHT SPEAROW!" in f.posts()[-1])
check("with the Twitch link", URL in f.posts()[-1])
events.append({"id": 4, "kind": "level", "text": "WARTORTLE grew to Lv 35"})
now[0] += 60
check("a level is not news on YouTube", yp.tick(yt, ev_fn, URL, quiet) == [])
check("but it is consumed, not re-read for ever", yt.state["last_event"] == 4)

# -- no promo straight after a milestone ------------------------------------
yt.state["last_promo"] = 0
now[0] += 60
check("a promo waits after a milestone post", yp.tick(yt, ev_fn, URL, quiet) == [])
now[0] += yp.QUIET_AFTER_NEWS
check("then goes out", yp.tick(yt, ev_fn, URL, quiet) == ["promo"])

# -- the broadcast rotates: the new chat is used, the new one titled --------
f.live = {"id": "B2", "snippet": {"liveChatId": "C2", "title": "x", "description": "",
                                  "actualStartTime": "2026-10-04T00:00:00Z"}}
now[0] += yp.PROMO_EVERY
did = yp.tick(yt, ev_fn, URL, quiet)
check("a new broadcast is titled", "title" in did)
check("and posted into ITS chat, not the cached old one", f.chats()[-1] == "C2")

# -- a silent rollover (no off-air gap) is still logged -----------------------
rl = []
f.live = {"id": "B3", "snippet": {"liveChatId": "C3", "title": yp.TITLE, "description": yp.description(URL),
                                  "categoryId": "20"}}
now[0] += yp.PROMO_EVERY
yp.tick(yt, ev_fn, URL, rl.append)
check("a rollover between checks is logged", any("rolled over B2 -> B3" in l for l in rl))

# -- a dead chat stops the round without crashing ---------------------------
f.fail_chat = "liveChatEnded"
now[0] += yp.PROMO_EVERY
logs = []
check("a dead chat posts nothing and does not raise", yp.tick(yt, ev_fn, URL, logs.append) == [])
check("and says why", any("liveChatEnded" in l for l in logs))
f.fail_chat = None
check("the promo is still due, so it retries next tick", yp.tick(yt, ev_fn, URL, quiet) == ["promo"])

# -- off air: nothing posted, milestones not queued -------------------------
f.live = None
events.append({"id": 5, "kind": "badge", "text": "EARNED A BADGE!"})
now[0] += yp.PROMO_EVERY
n = len(f.posts())
check("off air nothing is posted", yp.tick(yt, ev_fn, URL, quiet) == [] and len(f.posts()) == n)
f.live = {"id": "B2", "snippet": {"liveChatId": "C2", "title": yp.TITLE, "description": yp.description(URL)}}
now[0] += 60
yp.tick(yt, ev_fn, URL, quiet)
check("an off-air badge is not dumped late when it comes back",
      "EARNED A BADGE!" not in " ".join(f.posts()[n:]))
check("its id was still consumed", yt.state["last_event"] == 5)

# -- off air, the lookup is throttled -------------------------------------
f7 = FakeYT(); f7.live = None
yt7 = client(f7, {"last_event": 99})
for _ in range(10):
    now[0] += 60
    yp.tick(yt7, ev_fn, URL, quiet)
looks = sum(1 for u, d, m in f7.calls if "liveBroadcasts?" in u)
check(f"off air it looks every 5 min, not every minute ({looks} in 10 min)", looks <= 3)
f7.live = {"id": "B9", "snippet": {"liveChatId": "C9", "title": yp.TITLE, "description": yp.description(URL)}}
now[0] += yp.OFFAIR_RECHECK
check("and finds the broadcast once it comes up", "promo" in yp.tick(yt7, ev_fn, URL, quiet))

# -- the quota is counted and split ------------------------------------------
st = {"quota": {"day": yp.quota_day(now[0]), "used": yp.PROMO_CAP - 10}, "last_event": 5}
f2 = FakeYT()
yt2 = client(f2, st)
check("promos stop at their cap", yp.tick(yt2, ev_fn, URL, quiet) == [] and f2.posts() == [])
events.append({"id": 6, "kind": "evolve", "text": "WARTORTLE EVOLVED INTO BLASTOISE!"})
check("but a milestone still has room above it", "news" in yp.tick(yt2, ev_fn, URL, quiet))
st["quota"]["used"] = yp.HARD_CAP - 10
events.append({"id": 7, "kind": "catch", "text": "CAUGHT RATTATA!"})
logs = []
check("nothing at all past the hard cap", yp.tick(yt2, ev_fn, URL, logs.append) == [])
check("and the log says the budget is spent", any("budget" in l for l in logs))
check("the hard cap leaves margin under YouTube's 10,000", yp.HARD_CAP < yp.DAILY_QUOTA)
check("a write costs 50, a lookup 1", yp.COST_WRITE == 50 and yp.COST_LIST == 1)

# -- the quota day is Pacific midnight -------------------------------------
import datetime
pt = yp.QUOTA_TZ
before = datetime.datetime(2026, 10, 3, 23, 59, tzinfo=pt).timestamp()
after = datetime.datetime(2026, 10, 4, 0, 1, tzinfo=pt).timestamp()
check("the day turns at midnight Pacific", yp.quota_day(before) != yp.quota_day(after))
s3 = {"quota": {"day": yp.quota_day(before), "used": 9000}}
check("yesterday's spending does not count today", yp.spent(s3, after) == 0)

# -- budget at the planned cadence fits -------------------------------------
per_day = 96 * (yp.COST_WRITE + yp.COST_LIST) + 20 * (yp.COST_WRITE + yp.COST_LIST)
check(f"96 promos + 20 milestones ({per_day} units) fits under the promo cap + news room",
      96 * 51 <= yp.PROMO_CAP and per_day <= yp.HARD_CAP)

# -- long text is trimmed, not refused --------------------------------------
f3 = FakeYT()
yt3 = client(f3, {"quota": {}})
yt3.say("C", "x" * 400, yp.HARD_CAP)
check("a message over 200 chars is trimmed to fit", len(f3.posts()[0]) <= yp.MAX_CHAT)

# -- token refresh happens once, then is reused ----------------------------
f4 = FakeYT()
yt4 = client(f4, {})
yt4.live_broadcast(); yt4.live_broadcast()
check("one refresh serves many calls", sum(1 for u, d, m in f4.calls if u == yp.TOKEN_URL) == 1)

# -- persistent "Stream now" broadcasts are included ------------------------
check("the lookup asks for broadcastType=all",
      any("broadcastType=all" in u for u, d, m in f4.calls))

# -- the weekly Testing-mode death is LOUD, once, and recovers -------------
class Expired(FakeYT):
    def __call__(self, url, data=None, headers=None, method=None, form=False, timeout=20):
        if url == yp.TOKEN_URL and self.dead:
            raise yp.ApiError(400, "invalid_grant", '{"error": "invalid_grant"}')
        return super().__call__(url, data, headers, method, form, timeout)

f5 = Expired(); f5.dead = True
alerts = []
yt5 = yp.YouTube({"client_id": "x", "refresh_token": "OLD"}, {"last_event": 99},
                 clock=lambda: now[0], http_fn=f5)
now[0] += yp.PROMO_EVERY
check("an expired login posts nothing and does not raise",
      yp.tick(yt5, ev_fn, URL, quiet, alerts.append) == [])
check("it raises an alert", len(alerts) == 1 and "EXPIRED" in alerts[0])
check("the alert says exactly how to fix it", yp.RELOGIN in alerts[0])
check("and that Twitch is fine", "Twitch is unaffected" in alerts[0])
for _ in range(5):
    now[0] += yp.PROMO_EVERY
    yp.tick(yt5, ev_fn, URL, quiet, alerts.append)
check("it alerts ONCE, not every minute for a week", len(alerts) == 1)
n = len(f5.calls)
yp.tick(yt5, ev_fn, URL, quiet, alerts.append)
check("and stops hammering Google's token endpoint", len(f5.calls) == n)
f5.dead = False
yt5.cfg["refresh_token"] = "NEW"            # what main() does on seeing a new login
now[0] += yp.PROMO_EVERY
check("a new login brings it straight back", "promo" in yp.tick(yt5, ev_fn, URL, quiet, alerts.append))

# -- a day's warning before the 7-day limit -------------------------------
warn = []
yt6 = yp.YouTube({"client_id": "x", "refresh_token": "R", "logged_in_at": now[0]},
                 {"last_event": 99}, clock=lambda: now[0], http_fn=FakeYT())
yp.tick(yt6, ev_fn, URL, quiet, warn.append)
check("no warning on day one", warn == [])
now[0] += yp.TESTING_TTL - yp.WARN_BEFORE + 60
yp.tick(yt6, ev_fn, URL, quiet, warn.append)
check("a warning a day before it dies", len(warn) == 1 and yp.RELOGIN in warn[0])
now[0] += 3600
yp.tick(yt6, ev_fn, URL, quiet, warn.append)
check("only once per login", len(warn) == 1)
yt6.cfg["logged_in_at"] = now[0]
now[0] += yp.TESTING_TTL - yp.WARN_BEFORE + 60
yp.tick(yt6, ev_fn, URL, quiet, warn.append)
check("and again for the next login's week", len(warn) == 2)

# -- no promo to an empty room (Ian, 2026-10-04) ----------------------------
f8 = FakeYT(); f8.viewers = 0
f8.live = {"id": "B8", "snippet": {"liveChatId": "C8", "title": yp.TITLE,
                                   "description": yp.description(URL), "categoryId": "20"}}
yt8 = client(f8, {"last_event": 99, "was_live": True})
now[0] += yp.PROMO_EVERY
check("0 viewers: the promo is skipped", yp.tick(yt8, ev_fn, URL, quiet) == ["skipped"] and f8.posts() == [])
now[0] += 60
check("and the slot is spent, so it does not re-check every minute", yp.tick(yt8, ev_fn, URL, quiet) == [])
f8.viewers = 2
now[0] += yp.PROMO_EVERY
check("someone watching: it posts", yp.tick(yt8, ev_fn, URL, quiet) == ["promo"])
check("the count read is kept for the panel", yt8.state["viewers"] == 2 and yt8.state["viewers_at"] == now[0])
f8.viewers = None
now[0] += yp.PROMO_EVERY
check("a hidden count is not zero: it posts", yp.tick(yt8, ev_fn, URL, quiet) == ["promo"])
check("and is stored as null, not 0", yt8.state["viewers"] is None)
f8.viewers = 0
events.append({"id": 100, "kind": "badge", "text": "EARNED A BADGE!"})
now[0] += 60
check("milestones still go out to an empty room (they are news)", yp.tick(yt8, ev_fn, URL, quiet) == ["news"])
check("the interval is still 15 minutes", yp.PROMO_EVERY == 15 * 60)

yt7 = yp.YouTube({"client_id": "x", "refresh_token": "R", "logged_in_at": now[0], "testing": False},
                 {"last_event": 99}, clock=lambda: now[0], http_fn=FakeYT())
w7 = []
now[0] += yp.TESTING_TTL
yp.check_login_age(yt7, w7.append)
check("a production token never gets the 7-day warning", w7 == [])
print(f"{ok} checks pass")
