"""Which run this is, for everywhere that shows it (Ian, 2026-10-08: "advertise
the run number so people coming back are not confused").

~/.config/flybrain/run.json is written by reset.py on every reset:
{"id": N, "started": "YYYY-MM-DDTHH:MM:SS", "announced": bool}. Every reader
copes with it missing: no run number is shown rather than a wrong one.
"""
import json
import os
import time

RUN_F = os.path.expanduser("~/.config/flybrain/run.json")
# Ian, 2026-10-08: runs 1-3 were TEST RUNS; run 4 is LAB RUN 1, the first whose
# data counts. The run id keeps counting; the public name is derived from it.
LAB_FROM = 4


def name(run_id):
    """'Lab Run 1' for run 4, 'Test Run 2' for run 2."""
    run_id = int(run_id)
    return f"Lab Run {run_id - LAB_FROM + 1}" if run_id >= LAB_FROM else f"Test Run {run_id}"


def current(path=None):
    """{"id": int, "started": str, ...} or None."""
    try:
        with open(path or RUN_F) as f:
            d = json.load(f)
        d["id"] = int(d["id"])
        return d
    except (OSError, ValueError, KeyError, TypeError):
        return None


def prefix(path=None):
    """'Lab Run 1 · ' for titles and posts, or ''."""
    r = current(path)
    return f"{name(r['id'])} · " if r else ""


RUN_DAYS = 14           # play/run_clock.py: a lab run resets itself after this (Ian, 2026-10-13: was 30)


def day(r, now=None):
    """Day N of the run (1 on the day it started), or None."""
    try:
        t = time.strptime(r.get("started", ""), "%Y-%m-%dT%H:%M:%S")
    except ValueError:
        return None
    import datetime
    now = now or time.time()
    return (datetime.date.fromtimestamp(now) - datetime.date(t.tm_year, t.tm_mon, t.tm_mday)).days + 1


def tag(path=None, now=None):
    """'LAB RUN 1 · DAY 1 OF 14' for the overlay (test runs: 'TEST RUN 3 · DAY 2'), or ''."""
    r = current(path)
    if not r:
        return ""
    d = day(r, now)
    if d is None:
        return name(r["id"]).upper()
    of = f" OF {RUN_DAYS}" if r["id"] >= LAB_FROM else ""
    return f"{name(r['id']).upper()} · DAY {d}{of}"


def mark_announced(path=None):
    p = path or RUN_F
    r = current(p)
    if r is None:
        return
    r["announced"] = True
    tmp = p + ".tmp"
    with open(tmp, "w") as f:
        json.dump(r, f)
    os.replace(tmp, p)
