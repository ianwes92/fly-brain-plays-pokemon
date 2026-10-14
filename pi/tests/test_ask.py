"""Chat can ask the fly what it is doing, and it answers for itself.

Questions are free, so the limits matter more than the answers: a bot that
can be made to talk on demand is a bot that floods the channel. Refusals are
SILENT on purpose -- "too soon" said out loud is worse than saying nothing.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import twitch                                     # noqa: E402
from flybrain.twitch import PaidActions, interpret              # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


class Clock:
    t = 1000.0

    def __call__(self):
        return self.t


def said(text, user="viewer"):
    return interpret({"user": user, "name": user.title(), "text": text, "bits": 0})


print("1. the questions chat would actually type")
for text, topic in (("!fly", "fly"), ("!team", "team"), ("!story", "story"),
                    ("!help", "help"), ("!brain", "fly"), ("!mood", "fly"),
                    ("!what", "help")):
    ev = said(text)
    check(f"{text:7s} -> ask {topic}", ev == ("ask", topic, "viewer", "Viewer"), str(ev))
check("a question is not a steering command", said("!fly")[0] == "ask")
check("and steering is still steering", said("!left")[0] == "control")
check("plain chat is still ignored", said("what is this") is None)

print("2. it cannot be made to flood the channel")
p, c = PaidActions(clock=Clock()), None
c = p.clock
check("the first question is answered", p.may_answer("alice") is True)
check("the same viewer again is refused", p.may_answer("alice") is False)
check("and so is anyone else, right away", p.may_answer("bob") is False)
c.t += twitch.ASK_GLOBAL_S + 0.1
check("someone else may ask once the short gap passes", p.may_answer("bob") is True)
c.t += twitch.ASK_GLOBAL_S + 0.1
check("but the first viewer must still wait their own gap", p.may_answer("alice") is False)
c.t += twitch.ASK_USER_S
check("and is answered again after it", p.may_answer("alice") is True)

print("3. the limits are the ones intended")
check("a viewer waits the best part of a minute", twitch.ASK_USER_S >= 30,
      f"{twitch.ASK_USER_S}s")
check("the channel gets at most one answer every few seconds",
      twitch.ASK_GLOBAL_S >= 3, f"{twitch.ASK_GLOBAL_S}s")

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
