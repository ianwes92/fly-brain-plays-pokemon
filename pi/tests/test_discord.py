"""The poster must not lie to a public channel, and must not break the game.

Three failure modes are worth real checks: announcing an outage that is not
one (a stale token is not a dead stream), posting the whole backlog the first
time it runs, and touching the Twitch credential the game depends on.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))

import discord_poster as dp

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


tmp = tempfile.mkdtemp()
posts = []
dp.CFG = {"webhooks": {"stream-status": "https://discord.test/a",
                       "fly-progress": "https://discord.test/b",
                       "fly-log": "https://discord.test/c"},
          "broadcaster_id": "123456789",
          "watcher_role_id": "100000000000000000"}


class FakeResponse:
    """Stand in for urlopen, so the REAL http() runs and its headers can be
    read -- the User-Agent is added in there, so stubbing http() instead
    would test the stub."""

    def __init__(self, body=b""):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    posts.append((req.full_url,
                  json.loads(req.data.decode()) if req.data else None,
                  dict(req.header_items())))
    return FakeResponse(fake_urlopen.body)


fake_urlopen.body = b""
dp.urllib.request.urlopen = fake_urlopen

# -- every post carries the header Discord requires, and cannot ping all ---
posts.clear()
dp.post("fly-progress", {"title": "x"})
url, body, headers = posts[0]
check("User-Agent is set", headers.get("User-agent") == dp.UA)
check("allowed_mentions is explicit", "allowed_mentions" in body)
check("nothing is pinged by default", body["allowed_mentions"] == {"parse": []})

posts.clear()
dp.announce_live({"title": "t", "game_name": "g"}, {"thinking": "wandering"})
_, body, _ = posts[0]
check("go-live pings only the watcher role",
      body["allowed_mentions"] == {"roles": ["100000000000000000"]})
check("go-live mentions that role in content", "100000000000000000" in body["content"])

# -- an unknown channel is a no-op, not a crash ---------------------------
posts.clear()
dp.post("no-such-channel", {"title": "x"})
check("unknown channel posts nothing", posts == [])

# -- levels do not post individually; the rest do -------------------------
posts.clear()
dp.announce_event({"kind": "level", "text": "WARTORTLE grew to Lv 29"})
check("levels are held back", posts == [])
for kind in ("catch", "badge", "evolve", "faint"):
    posts.clear()
    dp.announce_event({"kind": kind, "text": "something"})
    check(f"{kind} posts", len(posts) == 1)
    check(f"{kind} goes to fly-progress", posts[0][0].endswith("/b"))

# -- THE TWITCH TOKEN IS NEVER WRITTEN ------------------------------------
tf = os.path.join(tmp, "twitch.json")
original = {"oauth": "oauth:abc", "client_id": "cid", "refresh_token": "r1",
            "channel": "examplechannel"}
with open(tf, "w") as f:
    json.dump(original, f)
dp.TWITCH_F = tf


def raise_401(req, timeout=None):
    import urllib.error
    raise urllib.error.HTTPError(req.full_url, 401, "Unauthorized", {}, None)


dp.urllib.request.urlopen = raise_401
check("a 401 gives 'cannot tell', not an answer", dp.twitch("streams?user_id=1") is None)
with open(tf) as f:
    check("the game's token file is untouched after a 401", json.load(f) == original)

# -- and a token it cannot use is NOT an outage ---------------------------
check("get_stream says 'cannot tell'", dp.get_stream() is None)

# a definite empty answer is different from that
fake_urlopen.body = json.dumps({"data": []}).encode()
dp.urllib.request.urlopen = fake_urlopen
check("no stream is an empty dict, not None", dp.get_stream() == {})
fake_urlopen.body = json.dumps({"data": [{"viewer_count": 3, "title": "t"}]}).encode()
check("a live stream comes back", dp.get_stream().get("viewer_count") == 3)

# missing credentials also read as "cannot tell"
dp.TWITCH_F = os.path.join(tmp, "nothing.json")
check("no token file is 'cannot tell'", dp.twitch("streams") is None)
dp.TWITCH_F = tf

# -- first run starts from the newest event, not from zero ----------------
dp.get_events = lambda since: [{"id": 41, "kind": "catch", "text": "a"},
                               {"id": 42, "kind": "badge", "text": "b"}]
s = dp.catch_up({"last_event_id": None})
check("first run skips the backlog", s["last_event_id"] == 42)
s = dp.catch_up({"last_event_id": 7})
check("a resumed run keeps its place", s["last_event_id"] == 7)
s = dp.catch_up({"last_event_id": 0})
check("zero is a real position, not 'unset'", s["last_event_id"] == 0)

# -- state is written atomically and privately ----------------------------
sf = os.path.join(tmp, "state.json")
dp.save_private(sf, {"last_event_id": 5})
check("state saved 0600", oct(os.stat(sf).st_mode & 0o777) == "0o600")
check("state reads back", dp.load_json(sf)["last_event_id"] == 5)
check("no leftover tmp file", not os.path.exists(sf + ".tmp"))
with open(sf, "w") as f:
    f.write("{half written")
check("a damaged state file does not crash the poster", dp.load_json(sf) == {})

# -- clean versus dropped -------------------------------------------------
posts.clear()
fake_urlopen.body = b""
dp.urllib.request.urlopen = fake_urlopen
dp.announce_offline(3600, 5, clean=True)
check("a deliberate stop reads as ended", posts[0][1]["embeds"][0]["title"] == "Stream ended")
posts.clear()
dp.announce_offline(3600, 5, clean=False)
check("a broken stop reads as dropped", "dropped" in posts[0][1]["embeds"][0]["title"])
check("and is coloured differently", posts[0][1]["embeds"][0]["color"] == dp.RED)

# -- the digest tells the truth while the game is loading -----------------
posts.clear()
dp.post_digest({"phase": "calibrating", "game_avg": "0.67x"}, None, [])
fields = posts[0][1]["embeds"][0]["fields"]
check("digest names the phase", any("calibrating" in str(f["value"]) for f in fields))
posts.clear()
dp.post_digest({"phase": "playing"}, {"viewer_count": 9}, ["WARTORTLE grew to Lv 29"])
fields = posts[0][1]["embeds"][0]["fields"]
check("digest does not say 'playing' as a fault",
      not any("playing" in str(f["value"]) for f in fields))
check("digest carries the held-back levels",
      any("Lv 29" in str(f["value"]) for f in fields))
check("digest goes to fly-log", posts[0][0].endswith("/c"))

# -- an empty payload is REFUSED, never sent -------------------------------
# A blank message with a role ping interrupts everyone who opted in and shows
# them nothing, and Discord accepts it with a 2xx rather than complaining.
posts.clear()
check("no embed and no content is refused", dp.post("fly-log", None) is None)
check("...and nothing went out", posts == [])
posts.clear()
check("an empty dict embed is refused too", dp.post("fly-log", {}) is None)
check("...and nothing went out either", posts == [])
posts.clear()
dp.post("fly-log", None, content="just words")
check("content alone is allowed through", len(posts) == 1)
check("and carries an empty embeds LIST, not a missing key",
      posts[0][1]["embeds"] == [])

# -- a fresh start adopts the world without announcing it ------------------
dp.get_events = lambda since: [{"id": 9, "kind": "catch", "text": "a"}]
posts.clear()
s0 = dp.catch_up({"last_event_id": None},
                 {"started_at": "2026-09-26T15:13:34Z", "viewer_count": 4})
check("it adopts a stream already live", s0["live"] is True)
check("it keeps that stream's real start time",
      s0["started_at"] == "2026-09-26T15:13:34Z")
check("it adopts the viewer count", s0["peak"] == 4)
check("it announces NOTHING while adopting", posts == [])
check("it also skips the event backlog", s0["last_event_id"] == 9)
check("and arms the digest clock so none fires at once", s0["last_digest"] > 0)

posts.clear()
s1 = dp.catch_up({"last_event_id": None}, None)
check("a fresh start with no stream stays offline", not s1.get("live"))
check("and still says nothing", posts == [])

# -- a longer digest interval must not drop news silently -----------------
posts.clear()
dp.post_digest({"phase": "playing"}, None, [f"MON{i} grew to Lv {i}" for i in range(14)])
val = [f["value"] for f in posts[0][1]["embeds"][0]["fields"] if f["name"] == "Levels gained"][0]
check("it shows the cap", val.count("grew to Lv") == dp.LEVELS_SHOWN)
check("and admits to the rest", "...and 4 more" in val)
posts.clear()
dp.post_digest({"phase": "playing"}, None, ["ONE grew to Lv 9"])
val = [f["value"] for f in posts[0][1]["embeds"][0]["fields"] if f["name"] == "Levels gained"][0]
check("a short list says nothing about more", "more" not in val)
check("exactly at the cap says nothing either",
      "more" not in (lambda: (posts.clear(),
                              dp.post_digest({"phase": "playing"}, None,
                                             [f"M{i}" for i in range(dp.LEVELS_SHOWN)]),
                              [f["value"] for f in posts[0][1]["embeds"][0]["fields"]
                               if f["name"] == "Levels gained"][0])[-1])())
check("the digest interval is the four hours Ian asked for", dp.DIGEST_EVERY == 14400)

# -- durations read as English --------------------------------------------
check("minutes", dp.fmt_dur(1800) == "30m")
check("hours and minutes", dp.fmt_dur(3660) == "1h 1m")

print(f"{ok} checks pass")
