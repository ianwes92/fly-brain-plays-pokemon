"""Chat has the controls, all the time, unless somebody has bought them.

Nothing here talks to Twitch; PaidActions is driven with a fake clock. What
matters on stream: chat's keys always work, paying still means something, and
one viewer cannot flood the input now that no window limits anybody.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import twitch                                     # noqa: E402
from flybrain.twitch import PaidActions                         # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def fresh():
    c = Clock()
    return PaidActions(clock=c), c


print("1. everyone drives, straight away and at once")
p, c = fresh()
check("chat may drive from the very first moment", p.chat_status()[0] is True)
p.feed(("control", "left", "alice"))
got = p.tick()
check("alice's key reaches the game as a NUDGE, not a takeover",
      got == [("nudge", "left", "alice", twitch.PRESS_MS)], str(got))
c.t += 0.2
p.feed(("control", "up", "bob"))
check("so does bob's, with no queue and no turns",
      p.tick() == [("nudge", "up", "bob", twitch.PRESS_MS)])
check("the caption names the last press", p.chat_status()[1] == ("up", "bob"))
check("presses are counted", p.chat_status()[2] == 2, str(p.chat_status()[2]))
check("and tallied for the last minute", p.chat_status()[3] == {"left": 1, "up": 1},
      str(p.chat_status()[3]))

print("2. there is no window to wait for")
p, c = fresh()
for minute in range(4):
    c.t += 60.0
    p.tick()
    p.feed(("control", "right", f"viewer{minute}"))
    got = p.tick()
    check(f"still driving {minute + 1} minute(s) in",
          got == [("nudge", "right", f"viewer{minute}", twitch.PRESS_MS)], str(got))

print("3. spamming is allowed (Ian, 2026-10-08)")
p, c = fresh()
p.feed(("control", "left", "spammer"))
p.tick()
p.feed(("control", "left", "spammer"))
check("a second key from the same viewer right away goes through",
      p.tick() == [("nudge", "left", "spammer", twitch.PRESS_MS)])
p.feed(("control", "right", "someone_else"))
check("but another viewer gets through at once",
      p.tick() == [("nudge", "right", "someone_else", twitch.PRESS_MS)])

print("4. paying still means something")
p, c = fresh()
p.feed(("control", "left", "alice"))
p.tick()
p.feed(("steer", 5.0, "payer", "Payer"))
p.tick()
check("a payer takes the controls", p.chat_status()[0] is False)
note = p.feed(("control", "left", "alice"))
check("chat's keys stop working", p.tick() == [])
check("and the viewer is told why, not ignored", "bought the controls" in (note or ""), str(note))
p.feed(("control", "right", "payer"))
check("only the payer drives", p.tick() == [("control", "right", "Payer")])
c.t += 6.0
p.tick()
check("when their time runs out chat has it back", p.chat_status()[0] is True)
p.feed(("control", "down", "alice"))
check("proved by a key going through",
      p.tick() == [("nudge", "down", "alice", twitch.PRESS_MS)])

print("5. a nudge is not a takeover")
check("the press is long enough to see, short enough not to be control",
      400 <= twitch.PRESS_MS <= 1000, f"{twitch.PRESS_MS} ms")
q, qc = fresh()
q.feed(("steer", 5.0, "payer", "Payer"))
q.tick()
q.feed(("control", "down", "payer"))
paid_act = q.tick()
check("what the paid driver gets is still a takeover, not a nudge",
      paid_act == [("control", "down", "Payer")], str(paid_act))

print("6. the tally is a rolling minute, not a running total")
p, c = fresh()
p.feed(("control", "a", "alice"))
p.tick()
check("counted now", p.chat_status()[3] == {"a": 1})
c.t += twitch.TALLY_S + 1
check("gone a minute later", p.chat_status()[3] == {}, str(p.chat_status()[3]))
check("but the lifetime count remains", p.chat_status()[2] == 1)

print("7. driving costs nobody anything")
p, c = fresh()
p.feed(("control", "left", "alice"))
p.tick()
check("no presses queued", len(p.line) == 0 and not p.stims)
check("the leaderboards are untouched", p.drivers == {} and p.trainers == {})

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
