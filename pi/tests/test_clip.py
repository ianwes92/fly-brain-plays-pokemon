"""Clipping a milestone must never cost the stream a frame.

The Twitch call can take fifteen seconds and the drawing thread is the only
thing producing the picture, so this checks the work really does leave that
thread, that the link is held back until it plays, and that the ordinary case
of nobody broadcasting stays quiet.
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from flybrain import playview as pv
from flybrain import twitch as tw

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


class FakeChat:
    def __init__(self):
        self.cfg = {"channel": "examplechannel", "client_id": "x", "oauth": "oauth:t"}
        self.said = []

    def say(self, text):
        self.said.append(text)
        return True


class FakeDiary:
    def __init__(self):
        self.entries = []

    def add(self, kind, text):
        self.entries.append((kind, text))
        return True


def run_with(clip_result, announce=True, chat=None, gap=None, now=None):
    """Drive clip_later with make_clip stubbed, and wait for its thread."""
    chat = FakeChat() if chat is None else chat
    diary = FakeDiary()
    slept = []
    real = tw.make_clip
    tw.make_clip = lambda cfg, **kw: clip_result
    try:
        th = pv.clip_later(chat, diary, "GOT THE BOULDERBADGE!", announce,
                           gap=gap, now=now, sleep=slept.append)
        if th is not None:
            th.join(timeout=5.0)
    finally:
        tw.make_clip = real
    return chat, diary, slept, th


# -- the happy path -------------------------------------------------------
pv.clip_later.last = 0.0
chat, diary, slept, th = run_with(("https://clips.twitch.tv/abc", ""))
check("it started a thread", th is not None)
check("the thread finished", not th.is_alive())
check("chat got the link", any("clips.twitch.tv/abc" in s for s in chat.said))
check("the news is in the line", any("BOULDERBADGE" in s for s in chat.said))
check("the diary kept it", any("clips.twitch.tv/abc" in t for _, t in diary.entries))
check("it waited before saying it", slept == [tw.CLIP_READY_S])

# -- the work does not happen on the calling thread ------------------------
pv.clip_later.last = 0.0
import threading
here = threading.current_thread()
seen = []


def slow_clip(cfg, **kw):
    seen.append(threading.current_thread())
    return ("https://clips.twitch.tv/z", "")


real = tw.make_clip
tw.make_clip = slow_clip
try:
    th = pv.clip_later(FakeChat(), FakeDiary(), "EVOLVED", False, sleep=lambda s: None)
    th.join(timeout=5.0)
finally:
    tw.make_clip = real
check("the Twitch call ran somewhere else", seen and seen[0] is not here)

# -- one clip at a time ---------------------------------------------------
pv.clip_later.last = 0.0
run_with(("https://clips.twitch.tv/one", ""))
_, _, _, again = run_with(("https://clips.twitch.tv/two", ""))
check("a second clip straight after is declined", again is None)

# ...but the gap is measured from the last clip, so it opens again
pv.clip_later.last = time.time() - (tw.CLIP_GAP_S + 1)
_, _, _, later = run_with(("https://clips.twitch.tv/three", ""))
check("after the gap it clips again", later is not None)

# -- not broadcasting is the normal case, and says nothing ----------------
pv.clip_later.last = 0.0
chat, diary, slept, th = run_with((None, "not live"))
check("nothing said when not live", chat.said == [])
check("nothing kept when not live", diary.entries == [])
check("a failed clip does not use up the gap", pv.clip_later.last == 0.0)

# -- a refusal must not hold the next one off either ----------------------
pv.clip_later.last = 0.0
run_with((None, "HTTP 401 missing scope"))
_, _, _, after_fail = run_with(("https://clips.twitch.tv/ok", ""))
check("a clip can follow a failure", after_fail is not None)

# -- announcements off: keep it, do not say it ----------------------------
pv.clip_later.last = 0.0
chat, diary, _, _ = run_with(("https://clips.twitch.tv/quiet", ""), announce=False)
check("silent when announcements are off", chat.said == [])
check("still kept in the diary", any("quiet" in t for _, t in diary.entries))

# -- no Twitch at all -----------------------------------------------------
pv.clip_later.last = 0.0
check("no chat, no clip", pv.clip_later(None, FakeDiary(), "x", True) is None)


class NoCfg:
    cfg = None


check("no login, no clip", pv.clip_later(NoCfg(), FakeDiary(), "x", True) is None)

# -- only the news worth keeping ------------------------------------------
check("badges are clipped", "badge" in pv.CLIP_OF)
check("evolutions are clipped", "evolve" in pv.CLIP_OF)
check("catches are clipped", "caught" in pv.CLIP_OF)
check("levels are not", "level" not in pv.CLIP_OF)

# -- the scope it needs is actually asked for -----------------------------
check("clips:edit is in the scopes", "clips:edit" in tw.SCOPES)

print(f"{ok} checks pass")
