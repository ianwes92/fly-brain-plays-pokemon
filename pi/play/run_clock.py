#!/usr/bin/env python3
"""The run clock: a lab run lasts RUN_DAYS (14) days, then resets itself (Ian,
2026-10-08: "each run lasts at most 1 month", auto-reset approved by Ian in
this session; shortened to 14 days by Ian on 2026-10-13, with a weekly report).

A run ends at RESET_HOUR (13:00 ET) on the day RUN_DAYS after it began -- clear
of obsfly's maintenance (07:40/19:40), the 20:00 Instagram Story and the 00:30
experiment nights. A daily timer at 13:00 runs `run_clock.py tick`:
  * the day before: a warning in Twitch chat, YouTube chat and Discord, and
    "RESET TOMORROW" in the stream title (title_notice.json, which words
    itself "RESET TODAY" on the day and vanishes after);
  * on the day: the reset itself, through reset.py's flag and restart.sh, the
    same path as every manual reset (archive, new game, naive brain, empty
    leaderboard, NEW RUN announcements, runs.json closed with "14 days").
It refuses to reset while an experiment or a deploy is running, and tries
again the next day; that is logged and said in Discord.
Test runs (ids 1-3) never auto-reset; they are all over anyway.

Usage: run_clock.py status | tick [--dry-run]
"""
import datetime
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)
CFG = os.path.expanduser("~/.config/flybrain")
RUN_F = os.path.join(CFG, "run.json")
FLAG = os.path.join(CFG, "reset_run.json")
NOTICE_F = os.path.join(CFG, "title_notice.json")
LOG = os.path.join(HERE, "run_clock.log")
from flybrain.runinfo import RUN_DAYS                      # noqa: E402 -- one number, everywhere
RESET_HOUR = 13
BUSY_UNITS = ("flybrain-naive-trained.service", "flybrain-mirror-rerun.service",
              "flybrain-turn-sides-deploy.service", "flybrain-battery-run.service")


def log(msg):
    line = f"{time.strftime('%F %T')} run_clock: {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def ends(started_iso):
    """RESET_HOUR on the day RUN_DAYS after the start."""
    s = datetime.datetime.strptime(started_iso, "%Y-%m-%dT%H:%M:%S")
    d = (s + datetime.timedelta(days=RUN_DAYS)).date()
    return datetime.datetime(d.year, d.month, d.day, RESET_HOUR, 0, 0)


def status(now=None, path=None):
    """{"id", "day", "of", "ends"} for the current run, or None."""
    from flybrain import runinfo
    r = runinfo.current(path or RUN_F)
    if not r or not r.get("started"):
        return None
    now = now or datetime.datetime.now()
    s = datetime.datetime.strptime(r["started"], "%Y-%m-%dT%H:%M:%S")
    e = ends(r["started"])
    return {"id": r["id"], "day": (now.date() - s.date()).days + 1, "of": RUN_DAYS,
            "ends": e.strftime("%Y-%m-%dT%H:%M:%S"), "auto": r["id"] >= runinfo.LAB_FROM}


def busy():
    """A unit whose work a reset would spoil, or None."""
    for u in BUSY_UNITS:
        r = subprocess.run(["systemctl", "--user", "is-active", "--quiet", u])
        if r.returncode == 0:
            return u
    return None


def _say_everywhere(title, text):
    import experiment_notice as en
    for name, fn in (("twitch", en.twitch_say), ("youtube", en.youtube_say),
                     ("discord", lambda t: en.discord_post(title, t))):
        try:
            fn(text)
        except Exception as e:
            log(f"{name} failed: {type(e).__name__}: {e}")


def tick(now=None, dry=False, act=None):
    """What the daily 13:00 check does. Returns 'warn', 'reset', 'busy' or None."""
    from flybrain import runinfo
    now = now or datetime.datetime.now()
    st = status(now)
    if not st or not st["auto"]:
        return None
    e = datetime.datetime.strptime(st["ends"], "%Y-%m-%dT%H:%M:%S")
    name = runinfo.name(st["id"])
    act = act or {}
    if now.date() == (e - datetime.timedelta(days=1)).date():
        if dry:
            return "warn"
        with open(NOTICE_F, "w") as f:
            json.dump({"what": "RESET", "on": e.date().isoformat()}, f)
        (act.get("say") or _say_everywhere)(
            f"{name} ends tomorrow",
            f"⏳ {name} ends tomorrow at 1 PM ET after {RUN_DAYS} days: then a brand-new game and an "
            f"untrained brain start the next lab run. The run's full report goes to Discord.")
        log(f"{name}: warned (ends {st['ends']})")
        return "warn"
    if now >= e - datetime.timedelta(minutes=5):
        b = (act.get("busy") or busy)()
        if b:
            log(f"{name}: due but {b} is running; trying again tomorrow")
            if not dry:
                (act.get("say") or _say_everywhere)(
                    f"{name}: reset postponed", f"{name} is due to end but {b} is running; "
                    f"the reset moves to tomorrow 1 PM ET.")
            return "busy"
        if dry:
            return "reset"
        nxt = runinfo.name(st["id"] + 1)
        with open(FLAG, "w") as f:
            json.dump({"keep_leaderboard": False, "keep_learning": False, "clear_science": True,
                       "label": f"run{st['id']}", "reason": f"{RUN_DAYS} days",
                       "chat": f"{name} is complete after {RUN_DAYS} days! Starting {nxt}: brand-new game, "
                               f"untrained brain, fresh data, empty leaderboard. Back in about 20 seconds.",
                       "announce": f"{name} is complete after {RUN_DAYS} days. {nxt} starts: a brand-new game "
                                   f"with an untrained brain."}, f)
        r = (act.get("restart") or (lambda: subprocess.run(["bash", os.path.join(HERE, "restart.sh")])
                                    .returncode))()
        log(f"{name}: {RUN_DAYS} days reached; reset to {nxt} (restart exit {r})")
        return "reset"
    return None


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "status":
        print(json.dumps(status()))
    elif cmd == "tick":
        print(tick(dry="--dry-run" in sys.argv))
    else:
        sys.exit(__doc__)
