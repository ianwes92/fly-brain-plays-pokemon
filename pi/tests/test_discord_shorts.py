"""A milestone and its Short go to Discord as ONE message -- or the text alone.

obsfly's rule: two posts for the same catch reads as a bug. So a milestone with
a Short is held until the video exists, then sent with it in one multipart
webhook call. Every way of not getting the video -- a failed render, a file too
big, Discord refusing it, or it simply never arriving -- must still post the
text, and must never post twice.
"""
import json
import os
import sys
import tempfile
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))

import discord_poster as dp

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


dp.CFG = {"webhooks": {"fly-progress": "https://discord.test/progress"}}
sent = []                       # (url, content-type, body bytes)
fail_with = {"code": None}


class Resp:
    def read(self):
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def fake_urlopen(req, timeout=None):
    if fail_with["code"] is not None:
        code, fail_with["code"] = fail_with["code"], None      # fail once
        raise urllib.error.HTTPError(req.full_url, code, "no", {}, None)
    sent.append((req.full_url, dict(req.header_items()).get("Content-type", ""), req.data or b""))
    return Resp()


dp.urllib.request.urlopen = fake_urlopen
tmp = tempfile.mkdtemp()


def clip(name, size=50_000):
    path = os.path.join(tmp, name)
    with open(path, "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp42" + b"v" * size)
    return path


def held(path, text="CAUGHT PIDGEY!", age=0.0, now=1_000_000.0):
    return {"id": 7, "kind": "catch", "text": text, "short": path, "first_seen": now - age}


NOW = 1_000_000.0

# -- ready: ONE message carrying the embed and the video -------------------
sent.clear()
p = clip("ready.mp4")
s = {"pending": [held(p)]}
left = dp.settle_pending(s, now=NOW)
check("exactly one request, not two", len(sent) == 1)
url, ctype, body = sent[0]
check("it is multipart", ctype.startswith("multipart/form-data; boundary="))
boundary = ctype.split("boundary=", 1)[1]
check("the body uses that boundary", body.startswith(f"--{boundary}".encode())
      and body.rstrip().endswith(f"--{boundary}--".encode()))
check("it carries payload_json", b'name="payload_json"' in body)
check("it carries the file as files[0]", b'name="files[0]"; filename="ready.mp4"' in body)
check("as video/mp4", b"Content-Type: video/mp4" in body)
check("with the video's bytes intact", open(p, "rb").read() in body)
pj = body.split(b"Content-Type: application/json\r\n\r\n", 1)[1].split(b"\r\n", 1)[0]
payload = json.loads(pj)
check("the embed is the milestone", payload["embeds"][0]["description"] == "CAUGHT PIDGEY!")
check("and nobody is pinged", payload["allowed_mentions"] == {"parse": []})
check("it is no longer pending", left == 0 and s["pending"] == [])

# -- still rendering: wait, post nothing -----------------------------------
sent.clear()
s = {"pending": [held(os.path.join(tmp, "not-yet.mp4"), age=30)]}
check("a Short still rendering keeps the news held", dp.settle_pending(s, now=NOW) == 1)
check("and nothing is posted yet", sent == [])

# -- a .part file is NOT a finished video ----------------------------------
sent.clear()
partial = os.path.join(tmp, "half.mp4")
open(partial + ".part", "wb").write(b"half written")
s = {"pending": [held(partial, age=30)]}
dp.settle_pending(s, now=NOW)
check("a half-written .part is never uploaded", sent == [])

# -- render failed: text alone, straight away -------------------------------
sent.clear()
bad = os.path.join(tmp, "broke.mp4")
open(bad + ".failed", "w").write("render failed\n")
s = {"pending": [held(bad, age=5)]}
check("a failed render does not wait out the timeout", dp.settle_pending(s, now=NOW) == 0)
check("it posts the text once", len(sent) == 1)
check("as JSON, not multipart", sent[0][1] == "application/json")
check("with the milestone in it", b"CAUGHT PIDGEY!" in sent[0][2])

# -- never arrived: text after the timeout ----------------------------------
sent.clear()
s = {"pending": [held(os.path.join(tmp, "lost.mp4"), age=dp.SHORT_WAIT_S + 1)]}
check("an overdue Short is given up on", dp.settle_pending(s, now=NOW) == 0)
check("and the text still goes out, once", len(sent) == 1 and sent[0][1] == "application/json")

# -- too big for Discord: text alone, nothing huge is sent ------------------
sent.clear()
was = dp.SHORT_MAX_BYTES
dp.SHORT_MAX_BYTES = 10_000
try:
    big = clip("big.mp4", size=50_000)
    s = {"pending": [held(big)]}
    dp.settle_pending(s, now=NOW)
finally:
    dp.SHORT_MAX_BYTES = was
check("an oversized clip is not uploaded", all(not c.startswith("multipart") for _, c, _ in sent))
check("the text posts instead, once", len(sent) == 1)
check("post_with_video says why", "too large" in dp.post_with_video("fly-progress", {"title": "x"}, big)
      if os.path.getsize(big) > 10_000 and setattr(dp, "SHORT_MAX_BYTES", 10_000) is None else True)
dp.SHORT_MAX_BYTES = was

# -- Discord refuses the file (413): text alone ------------------------------
sent.clear()
p2 = clip("refused.mp4")
fail_with["code"] = 413
s = {"pending": [held(p2)]}
dp.settle_pending(s, now=NOW)
check("a 413 falls back to the text", len(sent) == 1 and sent[0][1] == "application/json")
fail_with["code"] = 413
check("and names the reason", "413" in dp.post_with_video("fly-progress", {"title": "x"}, p2))

# -- several at once: each settled on its own terms --------------------------
sent.clear()
s = {"pending": [held(clip("a.mp4"), "CAUGHT A!"),
                 held(os.path.join(tmp, "b.mp4"), "CAUGHT B!", age=10),
                 held(os.path.join(tmp, "c.mp4"), "CAUGHT C!", age=dp.SHORT_WAIT_S + 5)]}
left = dp.settle_pending(s, now=NOW)
check("one ready, one waiting, one overdue -> one left", left == 1)
check("the waiting one is the one kept", s["pending"][0]["text"] == "CAUGHT B!")
check("two posts went out, one per event", len(sent) == 2)

# -- the poll slows down again when nothing is pending ----------------------
check("fast polling is faster than the normal poll", dp.PENDING_POLL_S < dp.POLL)
check("the size guard sits under Discord's 10 MB", dp.SHORT_MAX_BYTES < 10 * 1024 * 1024)

print(f"{ok} checks pass")
