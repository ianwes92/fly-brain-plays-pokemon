"""Thought data for the science tab (Ian, 2026-10-07).

Every on-screen thought is counted, by kind, by hour, split into the fly's
OWN (no human key for attrib.CLEAN_MS) and ASSISTED, and checked against the
tile map at the moment it decided -- the same checks as
data/experiments/thought_audit_score.py, done inline so nothing per-thought
has to be stored. One small JSON file per local day in data/thoughts/,
rewritten atomically about once a minute and reloaded on start, so the
numbers survive restarts. The relay serves them at /panel/thoughts.

Only checks that can come out either way are kept. "walked into something"
is said only when the faced tile is solid and "thinks it's blocked" only when
it is not, so checking those would score the wording, not the fly; what IS
reported for them is how often each happens -- the share of stops with
nothing actually in the way.
"""
import json
import os
import time

FOLDER = os.path.expanduser("~/flybrain/data/thoughts")
FLUSH_S = 60.0


def kind(text):
    """'wall on the left - x' and '... right - x' are one kind."""
    return text.replace(" left", " X").replace(" right", " X").replace(" - ", " ").replace(" ", "_")


def _side(lr):
    return "left" if lr[0] > lr[1] else "right" if lr[1] > lr[0] else None


def checks(text, view):
    """[(test, passed)] for one thought against the decision-time view
    (game.decision_view: obstacle_lr, people_lr, exits_lr, heading_before/after)."""
    if not view:
        return []
    named = "left" if " left" in text else "right" if " right" in text else None
    out = []
    if text.startswith("something close"):
        ob = view["obstacle_lr"]
        out.append(("obstacle_in_view", max(ob) > 0))
        heavy = _side(ob)
        if heavy and named:
            out.append(("turned_away_from_obstacle", named != heavy))
    elif text.startswith("a door on the"):
        out.append(("door_in_range", max(view["exits_lr"]) > 0))
        out.append(("door_on_named_side", _side(view["exits_lr"]) == named))
    elif text.startswith("thinks someone"):
        out.append(("person_in_range", max(view["people_lr"]) > 0))
        out.append(("person_on_named_side", _side(view["people_lr"]) == named))
    elif text.startswith("a way out"):
        out.append(("door_in_range", max(view["exits_lr"]) > 0))
    return out


class ThoughtLog:
    def __init__(self, folder=FOLDER, clock=time.time):
        self.folder, self.clock = folder, clock
        self.day = None
        self.d = None
        self._flushed = 0.0
        self._last_tick = None

    def _path(self, day):
        return os.path.join(self.folder, day + ".json")

    def _roll(self, now):
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        if day == self.day:
            return
        if self.d is not None:
            self.flush(now, force=True)
        self.day = day
        try:
            with open(self._path(day)) as f:
                self.d = json.load(f)
        except (OSError, ValueError):
            self.d = {"date": day, "hours": {}, "kinds": {}}

    def tick(self, now, playing):
        """Called every window frame: time spent playing is the denominator
        for 'thoughts per minute'."""
        self._roll(now)
        if self._last_tick is not None and playing:
            dt = min(now - self._last_tick, 5.0)
            h = self.d["hours"].setdefault(str(time.localtime(now).tm_hour),
                                           {"thoughts": 0, "clean": 0, "playing_s": 0.0})
            h["playing_s"] = round(h["playing_s"] + dt, 2)
        self._last_tick = now
        if now - self._flushed >= FLUSH_S:
            self.flush(now)

    def add(self, text, clean, view, now=None, battle=False):
        now = self.clock() if now is None else now
        self._roll(now)
        h = self.d["hours"].setdefault(str(time.localtime(now).tm_hour),
                                       {"thoughts": 0, "clean": 0, "playing_s": 0.0})
        h["thoughts"] += 1
        h["clean"] += bool(clean)
        k = self.d["kinds"].setdefault(kind(text), {"n": 0, "clean": 0, "tests": {}})
        k["n"] += 1
        k["clean"] += bool(clean)
        if clean and not battle:           # accuracy from the fly's own thoughts only
            for test, ok in checks(text, view):
                t = k["tests"].setdefault(test, [0, 0])
                t[0] += bool(ok)
                t[1] += 1

    def flush(self, now=None, force=False):
        if self.d is None:
            return
        now = self.clock() if now is None else now
        try:
            os.makedirs(self.folder, exist_ok=True)
            tmp = self._path(self.day) + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.d, f, separators=(",", ":"))
            os.replace(tmp, self._path(self.day))
        except OSError:
            pass
        self._flushed = now


def summary(days=7, folder=FOLDER, now=None):
    """What /panel/thoughts serves: per day and overall, per kind, with rates
    and accuracy percentages worked out."""
    now = time.time() if now is None else now
    out = {"days": [], "kinds": {}, "definitions": {
        "clean": "no human key reached the game for 5 s of brain time (attrib.CLEAN_MS)",
        "per_min": "thoughts per minute of play (time the game was in phase=playing)",
        "accuracy": "checked against the tile map at the moment the fly decided; fly's own thoughts only"}}
    tot = {}
    for i in range(days - 1, -1, -1):
        day = time.strftime("%Y-%m-%d", time.localtime(now - i * 86400))
        try:
            with open(os.path.join(folder, day + ".json")) as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        n = sum(h["thoughts"] for h in d["hours"].values())
        c = sum(h["clean"] for h in d["hours"].values())
        s = sum(h["playing_s"] for h in d["hours"].values())
        out["days"].append({"date": day, "thoughts": n, "clean": c,
                            "per_min": round(n / (s / 60), 1) if s >= 60 else None,
                            "hours": d["hours"]})
        for k, v in d["kinds"].items():
            t = tot.setdefault(k, {"n": 0, "clean": 0, "tests": {}})
            t["n"] += v["n"]
            t["clean"] += v["clean"]
            for test, (ok, m) in v["tests"].items():
                x = t["tests"].setdefault(test, [0, 0])
                x[0] += ok
                x[1] += m
    alln = sum(t["n"] for t in tot.values()) or 1
    for k, t in sorted(tot.items(), key=lambda kv: -kv[1]["n"]):
        out["kinds"][k] = {"n": t["n"], "share": round(t["n"] / alln, 3), "clean": t["clean"],
                           "accuracy": {test: {"ok": ok, "n": m, "pct": round(100 * ok / m, 1)}
                                        for test, (ok, m) in t["tests"].items() if m}}
    stops = tot.get("walked_into_something_stopping", {}).get("n", 0)
    phantom = tot.get("thinks_it's_blocked_stopping", {}).get("n", 0)
    out["stops"] = {"real": stops, "nothing_in_the_way": phantom,
                    "phantom_pct": round(100 * phantom / (stops + phantom), 1) if stops + phantom else None}
    return out
