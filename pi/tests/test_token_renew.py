"""A connected bot must keep its token alive, not just get one to connect with.

Found live on 2026-09-26: chat had been connected and talking for hours while
the token in the config was DEAD. Twitch keeps an established IRC session
working long after the token that opened it expires, so renewing only on
connect meant the file went stale and stayed stale. Everything else that reads
that file then failed quietly -- the stream title had logged "the login could
not be checked" thousands of times, and nobody noticed because chat looked
fine.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flybrain import twitch as tw

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


class Bridge(tw.ChatBridge):
    """A bridge that never touches the network; counts its own renewals."""

    def __init__(self):
        self.renewals = 0
        self._say = __import__("collections").deque()
        self._say_at = 0.0
        self._token_at = 0.0

    def _ensure_token(self):
        self.renewals += 1


b = Bridge()

# -- it checks once straight away, then not again for a while --------------
b._maybe_renew(0.0)
check("checks on the first beat", b.renewals == 1)
b._maybe_renew(1.0)
b._maybe_renew(60.0)
b._maybe_renew(tw.TOKEN_CHECK_S - 1)
check("does not check again straight away", b.renewals == 1)

# -- ...and then does, on its own beat, for ever ---------------------------
b._maybe_renew(tw.TOKEN_CHECK_S)
check("checks again after the interval", b.renewals == 2)
b._maybe_renew(tw.TOKEN_CHECK_S * 2)
check("and keeps checking", b.renewals == 3)

# -- the interval must beat the margin, or the gap swallows the token ------
# This one caught a real mistake when it was first written: checking every 15
# minutes while only renewing under 10 minutes remaining leaves a token that
# reads "fine" at one check and is dead by the next, for ever.
check("a check always comes round inside the margin",
      tw.TOKEN_CHECK_S < tw.TOKEN_MARGIN_S)
check("with room to spare, not by a hair",
      tw.TOKEN_CHECK_S <= tw.TOKEN_MARGIN_S / 2)
check("interval is not so short it hammers Twitch", tw.TOKEN_CHECK_S >= 300)
check("margin is well inside a Twitch token's ~4h life", tw.TOKEN_MARGIN_S < 4 * 3600)

# -- a bridge starts ready to check, not already satisfied -----------------
fresh = Bridge()
fresh._maybe_renew(1000.0)
check("a fresh bridge checks at once whatever the clock says", fresh.renewals == 1)

# -- the loop really calls it, on both paths through the socket read -------
import inspect

src = inspect.getsource(tw.ChatBridge.run)
check("renewal is wired into the idle path", src.count("_maybe_renew") >= 2)
check("it is driven by the same clock as the say-drain",
      "_drain_say(send, now)" in src and "_maybe_renew(now)" in src)

print(f"{ok} checks pass")
