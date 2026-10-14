#!/usr/bin/env python3
"""Everything final about a run, for obsfly's run reports (Ian, 2026-10-08:
"on every reset publish the run's data as a PDF in Discord").

  runs()          the registry: [{id, kind, started, ended, reason, archive, data}]
  export(run_id)  one JSON with what the Pi knows about that run

Served by the relay as /panel/runs and /panel/run_export?id=N. What a run did
not record is null with a note, never a guess: runs 1-2 predate attribution
(Oct 7) and the learning ledger, and run 2 steered reversed from Oct 5 to
Oct 7 15:45. Ended runs are read from their archive (reset.py); the current
run from the live files. Time series are thinned to keep a run under ~2 MB.

STABILITY: obsfly's run reports read these fields. Add fields freely; never rename or
remove one (agreed 2026-10-08).

Usage: run_export.py runs | run_export.py export N [--out FILE]
"""
import ast
import glob
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

CFG = os.path.expanduser("~/.config/flybrain")
REGISTRY = os.path.join(CFG, "runs.json")
DATA = os.path.join(ROOT, "data")
MAX_POINTS = 1500
CACHE_S = 300.0
_cache = {}


def _json(path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def _jsonl(paths):
    out = []
    for p in paths:
        try:
            with open(p) as f:
                for line in f:
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        pass
        except OSError:
            pass
    return out


def _ts(iso):
    return None if not iso else time.mktime(time.strptime(iso, "%Y-%m-%dT%H:%M:%S"))


def registry():
    return _json(REGISTRY, {"runs": [], "experiments": []})


def _where(run):
    """(science dir or None, archive dir or None, live?)"""
    if run.get("archive"):
        a = os.path.join(ROOT, run["archive"])
        sci = os.path.join(a, "science")
        return (sci if os.path.isdir(sci) else None), a, False
    return DATA, None, True


def runs():
    out = []
    for r in registry().get("runs", []):
        sci, arch, live = _where(r)
        have = {"science": bool(sci) and bool(glob.glob(os.path.join(sci, "inputs", "*.jsonl"))),
                "ledger": r["id"] >= 2, "attribution": r["id"] >= 2,
                "archive": bool(arch and os.path.isdir(arch)) or live}
        out.append(dict(r, live=live, data=have))
    return out


def _thin(rows, n=MAX_POINTS):
    if len(rows) <= n:
        return rows
    step = len(rows) / n
    return [rows[int(i * step)] for i in range(n)] + [rows[-1]]


def _events(run, all_runs):
    """This run's news: event ids between the previous run's archive and this one's."""
    prev = [r for r in all_runs if r["id"] < run["id"] and r.get("archive")]
    lo = 0
    if prev:
        lo = (_json(os.path.join(ROOT, prev[-1]["archive"], "events.json"), {}) or {}).get("next_id", 1) - 1
    src = (os.path.join(ROOT, run["archive"], "events.json") if run.get("archive")
           else os.path.join(CFG, "events.json"))
    evs = (_json(src, {}) or {}).get("events", [])
    return [e for e in evs if e.get("id", 0) > lo and e.get("kind") != "reset"]


def _restarts(t0, t1):
    """Deploys, crashes and watchdog restarts between t0 and t1, from the logs."""
    out = {"deploys": 0, "crashes": 0, "watchdog": 0, "crash_times": []}
    pat = re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) (.*)$")
    for name in ("launcher.log", "watchdog.log"):
        try:
            with open(os.path.join(HERE, name), errors="replace") as f:
                for line in f:
                    m = pat.match(line)
                    if not m:
                        continue
                    t = time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
                    if not (t0 <= t < (t1 or 1e18)):
                        continue
                    msg = m.group(2)
                    if name == "launcher.log" and msg.startswith("restarting for new code"):
                        out["deploys"] += 1
                    elif name == "launcher.log" and msg.startswith("game exited with status"):
                        out["crashes"] += 1
                        out["crash_times"].append(m.group(1))
                    elif name == "watchdog.log" and "asking" in msg and "restart" in msg:
                        out["watchdog"] += 1
        except OSError:
            pass
    return out


def _const(path, names):
    """Module-level constants read from source (no import: game.py loads the
    emulator library, which the relay has no business doing)."""
    out = {}
    try:
        tree = ast.parse(open(path).read())
    except (OSError, SyntaxError):
        return out
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for tg in node.targets:
                tgts = tg.elts if isinstance(tg, ast.Tuple) else [tg]
                vals = node.value.elts if isinstance(node.value, ast.Tuple) and isinstance(tg, ast.Tuple) \
                    else [node.value]
                for t, v in zip(tgts, vals):
                    if isinstance(t, ast.Name) and t.id in names:
                        try:
                            out[t.id] = ast.literal_eval(v)
                        except ValueError:
                            out[t.id] = ast.unparse(v)
    return out


def settings():
    """The fly-behaviour settings in force now, for report-to-report diffs."""
    fb = os.path.join(ROOT, "flybrain")
    s = {"twitch": _const(os.path.join(fb, "twitch.py"),
                          {"CONTROL_PACE_S", "FREE_GLOBAL_S", "FREE_USER_S", "STIM_GAP_S", "PRESS_MS",
                           "BITS_PER_PRESS", "BITS_PER_SECOND", "PANEL_STIM_S"}),
         "stillness": _const(os.path.join(fb, "playview.py"),
                             {"FIRST_S", "REPEAT_S", "STUCK_S", "TEXTBOX_WHITE"}),
         "game": _const(os.path.join(fb, "game.py"),
                        {"GAME_SPEED", "MENU_SPEED", "AVOID_OPINION", "AUTOSAVE_MS"}),
         "attribution": _const(os.path.join(fb, "attrib.py"), {"CLEAN_MS"}),
         "learning": _json(os.path.join(CFG, "learning.json"), {}),
         "fixed_rules": {"a_press_cap": "1 per game-second", "b_press_cap": "1 per 2 game-seconds",
                         "stillness_rule": "overworld only; pauses in dialogue; resets after battles",
                         "avoid_side": os.environ.get("FLYBRAIN_AVOID_SIDE", "median"),
                         "wall_side": os.environ.get("FLYBRAIN_WALL_SIDE", "zero")}}
    s["game"]["GAME_SPEED"] = s["game"].get("GAME_SPEED", 1.0)
    return s


def export(run_id, now=None):
    now = time.time() if now is None else now
    hit = _cache.get(run_id)
    if hit and (hit[0] is None or now - hit[0] < CACHE_S):
        return hit[1]
    reg = registry()
    all_runs = reg.get("runs", [])
    run = next((r for r in all_runs if r["id"] == run_id), None)
    if run is None:
        return None
    sci, arch, live = _where(run)
    t0, t1 = _ts(run["started"]), _ts(run.get("ended"))
    notes = []
    if run_id == 2:
        notes.append("Turns were applied reversed from Oct 5 to Oct 7 15:45 (Hands.mirror name clash).")
    if run_id == 1:
        notes.append("No attribution, learning ledger, input journal or thought data existed yet. "
                     "The news log began Oct 2, so milestones before then (incl. badges) are missing; "
                     "badges is null. Crash counts include development crash loops before Sep 21.")
    if run_id == 2:
        notes.append("Attribution, input journal and thoughts start Oct 7 ~16:24; before that, null.")
    if run_id <= 3:
        notes.append("Until Oct 8 13:03 the stillness rule also fired during battles.")

    places = _json(os.path.join(arch, "places.json") if arch else os.path.join(CFG, "places.json"), {}) or {}
    events = _events(run, all_runs)
    from flybrain.twitch import PLACES
    badges = max([int(m.group(1)) for e in events if e.get("kind") == "badge"
                  for m in [re.search(r"\((\d+) in total\)", e.get("text", ""))] if m] or [0])
    caught = [e["text"] for e in events if e.get("kind") == "catch"]
    from flybrain import starters as st
    starter = st.load().get(str(run_id))

    out = {"id": run_id, "kind": run.get("kind"), "started": run["started"], "ended": run.get("ended"),
           "reason": run.get("reason"), "notes": notes, "exported": int(now),
           # weekly reports (Ian, 2026-10-13): day N of the run (1-based) and
           # week N it falls in; for an ended run, its last day and week
           "days": (int(((t1 or now) - t0) // 86400) + 1) if t0 else None,
           "weeks": (int(((t1 or now) - t0) // (7 * 86400)) + 1) if t0 else None,
           "progress": {"furthest": PLACES.get(places.get("furthest"), places.get("furthest")),
                        "areas": [PLACES.get(p, p) for p in places.get("been") or []],
                        "badges": None if run_id == 1 else badges, "caught": caught, "starter": starter,
                        "milestones": [{k: e.get(k) for k in ("id", "kind", "text", "by")} for e in events]},
           "restarts": _restarts(t0, t1)}

    # learning ledger: the archived learned state carries it (runs 2+)
    ledger = None
    npz = (os.path.join(arch, "firered.resume.ss.mb.npz") if arch
           else os.path.join(HERE, "firered.resume.ss.mb.npz"))
    try:
        import numpy as np
        z = np.load(npz)
        if "ledger" in z:
            ledger = json.loads(bytes(z["ledger"]).decode())
    except (OSError, ValueError, KeyError):
        pass
    out["learning"] = ledger

    if sci is None:
        out.update(attrib=None, viewers=None, trainers=None, thoughts=None, stillness=None, series=None)
    else:
        samples = sorted(_jsonl(glob.glob(os.path.join(sci, "samples", "*.jsonl"))), key=lambda r: r.get("t", 0))
        samples = [r for r in samples if t0 <= r.get("t", 0) < (t1 or 1e18)]
        att = None
        if len(samples) >= 2:
            a, b = samples[0], samples[-1]
            att = {k: b.get(k, 0) - a.get(k, 0) for k in b if k != "t" and isinstance(b.get(k), (int, float))
                   and k in a}
            att["span_h"] = round((b["t"] - a["t"]) / 3600, 2)
        out["attrib"] = att
        recs = _jsonl(glob.glob(os.path.join(sci, "inputs", "????-??-??.jsonl")))
        recs = [r for r in recs if t0 <= r.get("t", 0) < (t1 or 1e18)]
        import inputs_report as ir
        users = ir.per_user(recs)
        out["viewers"] = [{"login": u, "inputs": v["inputs"], "accepted": v["accepted"],
                           "vs_fly": dict(v["vs_fly"]), "pain": v["pain"], "pleasure": v["pleasure"],
                           "delivered": dict(v["delivered"]), "flags": v["flags"]}
                          for u, v in sorted(users.items(), key=lambda kv: -kv[1]["inputs"])]
        prog = [p for p in _jsonl([os.path.join(sci, "inputs", "progress.jsonl")])
                if t0 <= p.get("t", 0) < (t1 or 1e18)]
        out["stillness"] = {"fly": ir.still_summary(prog), "with_human": ir.still_summary(prog, human=True)}
        # Every catch, from the party log (attrib.party_changes, since 10-08 12:25):
        # the news only says CAUGHT for a species new to the party, so a second
        # Rattata never made it there. The starter is the first join.
        joins = [p for p in prog if p.get("kind") == "party" and p.get("change") == "joined"]
        out["progress"]["party_joins"] = [{k: p.get(k) for k in ("t", "species", "level", "by", "since_human_s")}
                                          for p in joins]
        out["progress"]["attributed"] = [{k: p.get(k) for k in ("t", "kind", "text", "by", "since_human_s",
                                                                 "human_share_5min", "change", "species")}
                                         for p in prog if p.get("kind") != "still"]
        from flybrain import attrib
        tr = attrib.trainers(days=10_000, path=os.path.join(sci, "inputs", "learning.jsonl"), now=now)
        out["trainers"] = tr
        # thoughts: every day file in this run's record, summed
        kinds, tot, secs = {}, 0, 0.0
        for path in glob.glob(os.path.join(sci, "thoughts", "*.json")):
            d = _json(path, {}) or {}
            tot += sum(h["thoughts"] for h in d.get("hours", {}).values())
            secs += sum(h["playing_s"] for h in d.get("hours", {}).values())
            for k, v in d.get("kinds", {}).items():
                x = kinds.setdefault(k, {"n": 0, "clean": 0, "tests": {}})
                x["n"] += v["n"]
                x["clean"] += v["clean"]
                for test, (ok, m) in v["tests"].items():
                    y = x["tests"].setdefault(test, [0, 0])
                    y[0] += ok
                    y[1] += m
        stops = kinds.get("walked_into_something_stopping", {}).get("n", 0)
        phantom = kinds.get("thinks_it's_blocked_stopping", {}).get("n", 0)
        out["thoughts"] = {"total": tot, "per_min": round(tot / (secs / 60), 1) if secs >= 60 else None,
                           "kinds": {k: {"n": v["n"], "clean": v["clean"],
                                         "accuracy": {t: round(100 * a / m, 1) for t, (a, m) in v["tests"].items() if m}}
                                     for k, v in sorted(kinds.items(), key=lambda kv: -kv[1]["n"])},
                           "phantom_stop_pct": round(100 * phantom / (stops + phantom), 1) if stops + phantom else None}
        learn = _jsonl([os.path.join(sci, "inputs", "learning.jsonl")])
        # Which stretch of the run each block covers (obsfly, 2026-10-08: run 2's
        # blocks began at different times, so their totals differ honestly).
        iso = lambda t: None if t is None else time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
        out["windows"] = {
            "viewers_journal_from": iso(min((r["t"] for r in recs), default=None)),
            "attrib_samples_from": iso(samples[0]["t"] if samples else None),
            "ledger_from": iso(min([g.get("t") for g in (ledger or {}).get("regimes", []) if g.get("t")]
                                   or [None]) if ledger and ledger.get("regimes") else None),
            "learning_bursts_from": iso(min((r["t"] for r in learn), default=None)),
            "note": "Each block counts only from its own start. Learning booked before the "
                    "ledger existed sits under source 'before_ledger', not under any viewer."}
        out["series"] = {
            "fields": "samples every 10 min: cumulative counters (restart-proof totals); "
                      "opinions: after each delivered burst",
            "samples": _thin([{k: r.get(k) for k in ("t", "tiles", "bumps", "spikes", "tiles_clean",
                                                      "tiles_assisted", "bumps_clean", "human_pain",
                                                      "human_pleasure", "game_pleasure", "rule_pain",
                                                      "battle", "people", "exits", "furthest", "areas",
                                                      "human_share") if k in r} for r in samples]),
            "opinions": _thin([{"t": r.get("t"), "source": r.get("source"), "people": r.get("people_after"),
                                "exits": r.get("exits_after")} for r in learn
                               if t0 <= r.get("t", 0) < (t1 or 1e18)])}
    out["settings"] = (_json(os.path.join(arch, "settings.json")) if arch else settings())
    if out["settings"] is None:
        notes.append("Behaviour settings were not snapshotted for this run (snapshots start with run 4).")
    out["experiments"] = [e for e in reg.get("experiments", []) if e.get("run") == run_id]
    if live:
        h = None
        try:
            import urllib.request
            h = urllib.request.urlopen("http://127.0.0.1:8766/health", timeout=3).read().decode()
        except OSError:
            pass
        lines = {l.split(":", 1)[0]: l.split(":", 1)[1].strip() for l in (h or "").splitlines() if ":" in l}
        out["now"] = {k: lines.get(k) for k in ("team", "progress", "turns", "turn_sides", "learning", "attrib")}
    _cache[run_id] = (None if not live else now, out)
    return out


def main(argv):
    if len(argv) > 1 and argv[1] == "runs":
        print(json.dumps(runs(), indent=1))
    elif len(argv) > 2 and argv[1] == "export":
        s = json.dumps(export(int(argv[2])), indent=1)
        if "--out" in argv:
            open(argv[argv.index("--out") + 1], "w").write(s)
        else:
            print(s)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv)
