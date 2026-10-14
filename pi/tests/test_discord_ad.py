"""The Discord line in chat: once an hour, and only when it is worth saying.

Three gates, each cheap and each guarding something real. An advert posted
into an offline channel reaches nobody and is all anyone sees in the VOD; one
posted when not a single person has typed is a bot talking to itself; and one
measured on the wall-clock hour shows twice in five minutes to whoever arrives
at :55.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flybrain.twitch import (DISCORD_EVERY_S, DISCORD_INVITE, DISCORD_LINES,
                             SAY_MAX, discord_due, discord_line)

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


HOUR = DISCORD_EVERY_S

# -- the gates ------------------------------------------------------------
check("due after an hour, live, with chat", discord_due(HOUR + 1, 0, True, 5))
check("not before the hour is up", not discord_due(HOUR - 1, 0, True, 5))
check("not exactly one second early", not discord_due(HOUR - 1, 0, True, 500))
check("due exactly on the hour", discord_due(HOUR, 0, True, 1))

check("never while offline", not discord_due(HOUR * 10, 0, False, 500))
check("never into a silent chat", not discord_due(HOUR * 10, 0, True, 0))
check("...and a negative count is still silence",
      not discord_due(HOUR * 10, 0, True, -3))
check("one person speaking is enough", discord_due(HOUR + 1, 0, True, 1))

# -- the hour runs from the LAST post, not the clock ----------------------
# Somebody arriving at :55 must not see it twice in five minutes.
posted_at = 3000.0
check("a viewer arriving late does not get a second one straight away",
      not discord_due(posted_at + 300, posted_at, True, 50))
check("but does an hour after that one", discord_due(posted_at + HOUR, posted_at, True, 50))

# -- the copy -------------------------------------------------------------
check("more than one phrasing", len(DISCORD_LINES) >= 2)
check("the rotation cycles", discord_line(0) == discord_line(len(DISCORD_LINES)))
check("every line differs", len(set(DISCORD_LINES)) == len(DISCORD_LINES))
for i, line in enumerate(DISCORD_LINES):
    check(f"line {i} carries the invite", DISCORD_INVITE in line)
    check(f"line {i} fits Twitch", len(line) <= SAY_MAX)
    check(f"line {i} is one line", "\n" not in line)
    # say() strips a leading / or . because the bot holds the broadcaster
    # token and those would run a mod command.
    check(f"line {i} cannot be read as a command", not line.lstrip().startswith(("/", ".")))

# -- the invite itself ----------------------------------------------------
# The whole point of pinning it here: Ian's older invites expire in 30 days,
# and a dead link posted hourly is worse than no link.
check("the permanent invite, not an expiring one", DISCORD_INVITE == "discord.gg/example")
check("no scheme, so Twitch sees a plain domain", not DISCORD_INVITE.startswith("http"))

print(f"{ok} checks pass")
