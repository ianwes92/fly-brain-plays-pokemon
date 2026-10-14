#!/usr/bin/env python3
"""Mend a dead Twitch login from a phone, with nobody at the Pi (Ian,
2026-10-13: the project must run without a Claude session).

Every 15 minutes (flybrain-twitch-relogin.timer): if the bridge's token does
not validate on two checks in a row, start Twitch's device login and post the
link and code to Discord #fly-log, with what to do. Ian opens the link on his
phone (signed in to Twitch as the stream account) and taps Authorize; this
script saves the new login and restarts the game so the bridge picks it up.
At most MAX_A_DAY codes a day, so a long outage cannot flood the channel.
It never refreshes the token itself: the bridge is that token's only
refresher (refresh tokens rotate; a second refresher locks it out).
"""
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)
STATE_F = os.path.expanduser("~/.config/flybrain/twitch_relogin.json")
MAX_A_DAY = 6
BAD_CHECKS = 2


def log(msg):
    print(f"{time.strftime('%F %T')} twitch_relogin: {msg}", flush=True)


def _state():
    try:
        with open(STATE_F) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save(s):
    tmp = STATE_F + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE_F)


def _post(title, text, colour):
    import discord_poster as dp
    dp.post("fly-log", {"title": title, "description": text, "color": colour})


def check(validate=None, login=None, post=_post, restart=None, now=None):
    """One pass. Returns 'ok', 'watching', 'relogged', 'gave-up' or 'limit'."""
    from flybrain import twitch as tw
    now = time.time() if now is None else now
    s = _state()
    cfg = tw.load_config() or {}
    access = (cfg.get("oauth") or "").split(":", 1)[-1]
    try:
        who = (validate or tw.validate)(access)
    except OSError:
        return "watching"                       # no network: say nothing, look again later
    if who:
        if s.get("bad"):
            log("token valid again")
        _save({"bad": 0, "codes": s.get("codes", [])})
        return "ok"
    s["bad"] = s.get("bad", 0) + 1
    if s["bad"] < BAD_CHECKS:
        _save(s)
        return "watching"
    codes = [t for t in s.get("codes", []) if now - t < 86400]
    if len(codes) >= MAX_A_DAY:
        _save(dict(s, codes=codes))
        return "limit"
    codes.append(now)
    _save(dict(s, codes=codes))

    def show(text):
        # device_login prints the URL and the code; turn them into a phone-friendly post
        import re
        uri = re.search(r"open:\s+(\S+)", text)
        code = re.search(r"code:\s+(\S+)", text)
        if uri and code:
            link = f"{uri.group(1)}?device-code={code.group(1)}"
            post("Twitch login needed (tap to fix)",
                 f"The stream's Twitch login stopped working, so chat commands, channel points "
                 f"and the title are down. To fix it from your phone:\n"
                 f"1. Open {link}\n"
                 f"2. Make sure you are signed in to Twitch as **{tw.broadcaster_login()}**.\n"
                 f"3. Tap **Authorize**.\n"
                 f"The Pi picks it up by itself within a minute and restarts the game "
                 f"(~20 s freeze). The code ({code.group(1)}) works for 30 minutes; if you miss "
                 f"it, a new one comes in about 15 minutes.", 0xED4245)
    try:
        (login or (lambda: tw.device_login(cfg["client_id"], cfg.get("channel") or tw.broadcaster_login(),
                                           show=show)))()
    except (SystemExit, KeyError, OSError) as e:
        log(f"device login did not complete: {e}")
        return "gave-up"
    log("new login saved; restarting the game")
    (restart or (lambda: subprocess.run(["bash", os.path.join(HERE, "restart.sh")])))()
    post("Twitch login fixed", "Thanks -- the new Twitch login is saved and the game restarted. "
         "Chat commands, channel points and the title work again.", 0x3BA55D)
    _save({"bad": 0, "codes": codes})
    return "relogged"


if __name__ == "__main__":
    print(check())
