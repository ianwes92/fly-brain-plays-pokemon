"""!menu: the streamer takes the controls to teach HMs, and nothing else gets in.

The failures guarded: anyone but the broadcaster opening the menu; the fly or
chat pressing underneath the streamer; a forgotten !menu freezing the stream
for good; paid steering time eaten by an admin action; START leaking to the
fly outside !menu; and menu fast-forward making the streamer's taps scroll.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import twitch as tw
from flybrain import game as gm

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


IAN = "123456789"


def msg(text, user="examplechannel", uid=IAN, bits=0):
    return {"user": user, "name": user, "text": text, "bits": bits, "uid": uid}


# -- parsing ----------------------------------------------------------------
line = ("@badge-info=;badges=broadcaster/1;display-name=ExampleChannel;user-id=123456789 "
        ":examplechannel!examplechannel@examplechannel.tmi.twitch.tv PRIVMSG #examplechannel :!menu")
m = tw.parse(line)
check("the user id is parsed from the tags", m["uid"] == IAN)
check("!menu is an admin request", tw.interpret(m) == ("admin", True, "examplechannel", "LeetZerzz", IAN))
check("!menu off releases", tw.interpret(msg("!menu off"))[1] is False)
check("!menu done releases too", tw.interpret(msg("!menu done"))[1] is False)

# -- access: by id, not name -------------------------------------------------
now = [0.0]
p = tw.PaidActions(clock=lambda: now[0])
check("a viewer cannot take it", p.feed(tw.interpret(msg("!menu", user="viewer", uid="999"))) is None)
check("not even by renaming themselves", p.feed(tw.interpret(msg("!menu", user="examplechannel", uid="999"))) is None
      and p.admin is None)
check("and nothing happens", p.tick() == [])
check("START is never a viewer's", p.feed(tw.interpret(msg("!start", user="viewer", uid="1"))) is None)
check("(and is not passed on to the game)", not any(a[0] in ("tap", "control") for a in p.tick()))

# -- taking the controls -----------------------------------------------------
note = p.feed(tw.interpret(msg("!menu")))
check("the streamer takes it, with the banner text", note == tw.ADMIN_BANNER)
acts = p.tick()
check("the game is told admin is on, then START is tapped",
      acts[:2] == [("admin", True, "taken"), ("tap", "start")])
check("chat may no longer drive", p.chat_status()[0] is False and p.admin is not None)

# -- inside: only the streamer's keys ---------------------------------------
check("a viewer's press does nothing", p.feed(("control", "a", "viewer")) is None)
check("and reaches nothing", p.tick() == [])
check("the streamer's press is a tap", p.feed(("control", "down", "examplechannel")) == "streamer: DOWN"
      and p.tick() == [("tap", "down")])
check("!start reopens the menu for the streamer", p.feed(tw.interpret(msg("!start"))) == "streamer: START"
      and p.tick() == [("tap", "start")])
check("!menu again just taps START", p.feed(tw.interpret(msg("!menu"))) == "streamer: START"
      and p.tick() == [("tap", "start")])
check("!pain still works for viewers", p.feed(("free", "pain", "viewer", "viewer")) == "viewer: PAIN")

# -- paid steering waits, is not eaten ----------------------------------------
p.feed(("steer", 5.0, "payer", "Payer"))
now[0] += 1
p.tick()
check("bought time queues while the streamer works", p.driver is None and len(p.line) == 1)

# -- release ------------------------------------------------------------------
check("!menu off hands back", p.feed(tw.interpret(msg("!menu off"))) is not None)
acts = p.tick()
check("the game is told admin is off", ("admin", False, "released") in acts)
check("and the paid turn then starts in full", p.driver is not None and p.driver[1] == "Payer"
      and abs(p.driver[2] - now[0] - 5.0) < 1e-6)

# -- refused while someone has paid --------------------------------------------
note = p.feed(tw.interpret(msg("!menu")))
check("!menu is refused while a paid turn runs", p.admin is None and "Payer" in note)

# -- the timeout ------------------------------------------------------------
now[0] += 10
p.tick()                                   # the paid turn is over
p.feed(tw.interpret(msg("!menu")))
p.tick()
now[0] += tw.ADMIN_IDLE_S - 1
p.feed(("control", "a", "examplechannel"))      # each command refreshes it
p.tick()
now[0] += tw.ADMIN_IDLE_S - 1
check("a command refreshes the timeout", p.tick() == [] and p.admin is not None)
now[0] += 2
check("idle past the timeout releases by itself", p.tick() == [("admin", False, "timeout")]
      and p.admin is None)
check("the timeout is finite and generous", 60 <= tw.ADMIN_IDLE_S <= 900)

# -- the hard ceiling: busy or not, it ends --------------------------------------
p.feed(tw.interpret(msg("!menu")))
p.tick()
start = now[0]
fired = None
while now[0] - start < tw.ADMIN_MAX_S + 120:
    now[0] += 30
    p.feed(("control", "down", "examplechannel"))     # never idle: refreshes the idle timer
    acts = p.tick()
    if ("admin", False, "ceiling") in acts:
        fired = now[0] - start
        break
check("a never-idle session still ends at the ceiling", fired is not None)
check("at 15 minutes, observed", fired is not None and tw.ADMIN_MAX_S <= fired < tw.ADMIN_MAX_S + 30)
check("and control is really back", p.admin is None and p.chat_status()[0] is True)
check("Ian can take it again straight away", p.feed(tw.interpret(msg("!menu"))) == tw.ADMIN_BANNER)

# -- the game: nothing but the streamer's taps; START only then ----------------
seen = []


class FakeGBA:
    def frame(self, keys, allow=0):
        seen.append(keys & ~((gm.START | gm.SELECT) & ~allow))


class T:
    TAP_MS = gm.Game.TAP_MS
    TAKEOVER_KEYS = gm.Game.TAKEOVER_KEYS
    tap = gm.Game.tap


t = T()
t.admin, t._tap_req, t._tap_keys, t._tap_until = True, None, 0, -1.0


def frame_with_admin(fly_keys, t_ms):
    """The admin block of Game._game_frame, run on its own."""
    keys, allow = fly_keys, 0
    if t.admin:
        req, t._tap_req = t._tap_req, None
        if req is not None:
            t._tap_keys, t._tap_until = req, t_ms + t.TAP_MS
        keys = t._tap_keys if t_ms < t._tap_until else 0
        allow = gm.START
    FakeGBA().frame(keys, allow)


src = open(gm.__file__).read()
check("the game loop has exactly this admin block", "keys = self._tap_keys if b.t_ms < self._tap_until else 0" in src
      and "allow = START" in src)
frame_with_admin(gm.UP | gm.A, 0)
check("the fly's keys are dropped in admin mode", seen[-1] == 0)
t.tap("start")
frame_with_admin(gm.UP, 10)
check("the streamer's START gets through", seen[-1] == gm.START)
frame_with_admin(gm.UP, 10 + t.TAP_MS + 1)
check("a tap is short, then nothing", seen[-1] == 0)
FakeGBA().frame(gm.START | gm.UP)
check("outside admin, START is stripped as always", seen[-1] == gm.UP)
check("menu fast-forward is off while admin holds", "not self.in_overworld() and not self.admin" in src)
check("a tap is too short to auto-repeat a direction", t.TAP_MS < 250)

print(f"{ok} checks pass")
