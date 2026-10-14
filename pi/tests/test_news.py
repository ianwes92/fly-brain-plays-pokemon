"""One achievement must reach every place that reports it.

This is the link that went unverified all day: a milestone fires the buzzer,
the Twitch chat line and the diary, and it is also supposed to reach the
numbered event feed that Discord reads. On a real game those are hours apart
-- the fly went 8.3 hours without one -- so waiting was never going to confirm
it. Driven here through the real `record_news`, with the real Diary and
EventLog, so the two histories are checked against each other rather than
against a mock.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

from flybrain import playview as pv
from flybrain.diary import Diary
from flybrain.events import EventLog
from flybrain.game import milestones

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


class FakeChat:
    def __init__(self):
        self.cfg = None          # no Twitch login -> clip_later declines
        self.said = []

    def say(self, text):
        self.said.append(text)
        return True


tmp = tempfile.mkdtemp()


def fresh():
    return (Diary(path=os.path.join(tmp, f"d{fresh.n}.json")),
            EventLog(path=os.path.join(tmp, f"e{fresh.n}.json")),
            FakeChat())


fresh.n = 0


def once(kind, text, announce=True):
    fresh.n += 1
    diary, events, chat = fresh()
    pv.record_news(kind, text, diary, events, chat, announce)
    return diary, events, chat


# -- the basic promise: one achievement, every channel -------------------
diary, events, chat = once("badge", "EARNED A BADGE!  (2 in total)")
check("the diary kept it", len(diary.entries) == 1)
check("the event feed kept it", len(events.since(0)) == 1)
check("chat was told", chat.said == ["EARNED A BADGE!  (2 in total)"])
check("the event carries the same words", events.since(0)[0]["text"] == diary.entries[0][2])

# -- the diary and the feed must never disagree --------------------------
fresh.n += 1
diary, events, chat = fresh()
news = [("caught", "CAUGHT RATTATA!"), ("level", "WARTORTLE grew to Lv 29"),
        ("evolve", "SQUIRTLE EVOLVED INTO WARTORTLE!"), ("badge", "EARNED A BADGE!"),
        ("faint", "the whole team fainted")]
for k, t in news:
    pv.record_news(k, t, diary, events, chat, True)
check("both histories are the same length", len(diary.entries) == len(events.since(0)))
check("in the same order",
      [e[2] for e in diary.entries] == [r["text"] for r in events.since(0)])
check("chat heard all of them", len(chat.said) == len(news))

# -- the feed renames only what was agreed with the Discord side ---------
kinds = [r["kind"] for r in events.since(0)]
check("caught is served as catch", "catch" in kinds)
check("and 'caught' is not", "caught" not in kinds)
for k in ("level", "evolve", "badge", "faint"):
    check(f"{k} passes through unchanged", k in kinds)

# -- ids are what let Discord resume; they must be unique and rising -----
ids = [r["id"] for r in events.since(0)]
check("ids are unique", len(set(ids)) == len(ids))
check("ids rise", ids == sorted(ids))

# -- announcements off: still recorded, just not said --------------------
diary, events, chat = once("badge", "EARNED A BADGE!", announce=False)
check("still in the diary when quiet", len(diary.entries) == 1)
check("still in the feed when quiet", len(events.since(0)) == 1)
check("but nothing said", chat.said == [])

# -- it survives having no Twitch at all ---------------------------------
fresh.n += 1
diary, events, _ = fresh()
pv.record_news("caught", "CAUGHT PIDGEY!", diary, events, None, False)
check("no chat, still recorded", len(events.since(0)) == 1)

# -- THE REAL PATH: a genuine milestone, from two real save snapshots ----
# This is what the game itself computes; if the wiring is right, a party that
# gains a Pokemon must arrive in the Discord feed as a catch.
before = {"badges": 1, "dex_owned": 3, "dex_seen": 19}
after = {"badges": 1, "dex_owned": 4, "dex_seen": 20}
t0 = [{"name": "WARTORTLE", "level": 28, "hp": 76, "max_hp": 76, "egg": False}]
t1 = [{"name": "WARTORTLE", "level": 28, "hp": 76, "max_hp": 76, "egg": False},
      {"name": "NIDORAN", "level": 6, "hp": 22, "max_hp": 22, "egg": False}]
found = milestones(before, after, t0, t1)
check("the game calls that a catch", found and found[0][0] == "caught")

fresh.n += 1
diary, events, chat = fresh()
for k, t in found:
    pv.record_news(k, t, diary, events, chat, True)
rows = events.since(0)
check("a real catch reaches the Discord feed", rows and rows[0]["kind"] == "catch")
check("with the game's own wording", "NIDORAN" in rows[0]["text"])
check("and chat said the same thing", chat.said and "NIDORAN" in chat.said[0])

# a level-up, which Discord holds back for the digest rather than posting
found = milestones(before, before, t0,
                   [{"name": "WARTORTLE", "level": 29, "hp": 76, "max_hp": 76, "egg": False}])
check("the game calls that a level", found and found[0][0] == "level")
fresh.n += 1
diary, events, chat = fresh()
for k, t in found:
    pv.record_news(k, t, diary, events, chat, True)
check("a real level-up reaches the feed as 'level'",
      events.since(0)[0]["kind"] == "level")

# -- and it all survives a restart, which is the point of the ids --------
path = os.path.join(tmp, "persist.json")
log = EventLog(path=path)
d2 = Diary(path=os.path.join(tmp, "persist_d.json"))
pv.record_news("badge", "EARNED A BADGE!", d2, log, None, False)
last = log.since(0)[-1]["id"]
again = EventLog(path=path)
check("the feed survives a restart", len(again.since(0)) == 1)
check("and a poller at the last id is told nothing new", again.since(last) == [])
pv.record_news("caught", "CAUGHT ODDISH!", d2, again, None, False)
check("new news after a restart gets a higher id", again.since(last)[0]["id"] == last + 1)

print(f"{ok} checks pass")
