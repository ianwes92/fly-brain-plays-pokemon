"""Who did it: the fly on its own, or a human?

Ian, 2026-10-07: "There are times where the fly makes progress but not on its
own ... quantify as much of the fly as we can while not having human input
taint its data." Two parts:

ATTRIBUTION (Attribution). Every game frame is classed by who had a hand on
the keys:
  fly     no human key reaching the game
  nudge   chat's press OR'd with the fly's (the default when nobody paid)
  driver  paid steering: the human's key REPLACES the fly's
  admin   !menu / the panel's take: the fly is switched off entirely
A human key leaves effects behind it -- the character turned, a dialogue
opened, the fly's heading was reset -- so a frame only counts as the fly's
OWN ("clean") once CLEAN_MS of brain time has passed with no human key. Tiles,
wall bumps and progress are split the same way: CLEAN (fly alone) or
ASSISTED. The window is a fixed rule written here before any numbers were
looked at; it is not tuned. Every progress event also records how long it had
been since the last human key and the human share of the last 5 minutes, so a
stricter rule can be applied afterwards from the raw log.

Learning is the other route by which humans shape the fly: a pain or pleasure
burst changes its synapses. Those are counted by SOURCE (human vs the game's
own rewards), so "what the fly thinks of people" can always be reported next
to how much of that opinion humans paid for.

THE INPUT JOURNAL (Journal). Every chat/panel input, accepted or refused, one
JSON line per input in data/inputs/YYYY-MM-DD.jsonl, with the game context at
that moment (where, battle or not, what the FLY was pressing). The per-user
report is play/inputs_report.py.
"""
import json
import os
import threading
import time
from collections import deque

CLEAN_MS = 5000.0                  # brain-ms after the last human key before frames are the fly's own
SHARE_WINDOW_S = 300               # "human share of the last 5 minutes", for progress events
STATES = ("fly", "nudge", "driver", "admin")
JOURNAL_DIR = os.path.expanduser("~/flybrain/data/inputs")
PROGRESS_LOG = os.path.expanduser("~/flybrain/data/inputs/progress.jsonl")


class Attribution:
    """Counters, cumulative per process (the Threads sampler makes them
    restart-proof, as it does for tiles and spikes). Written by the game
    thread; read by others. Plain ints, so a torn read is impossible."""

    def __init__(self, clean_ms=CLEAN_MS):
        self.clean_ms = clean_ms
        self.frames = {s: 0 for s in STATES}
        self.frames["clean"] = 0
        self.tiles = {"clean": 0, "assisted": 0}
        self.bumps = {"clean": 0, "assisted": 0}
        # rule_pain: the stillness rule (playview.StillPain) -- automatic, not human
        self.stims = {"human_pain": 0, "human_pleasure": 0, "game_pain": 0, "game_pleasure": 0,
                      "rule_pain": 0}
        self.last_human_ms = None        # brain time of the last frame with a human key
        self.state = "fly"
        self._t = 0.0
        self._secs = deque()             # [second, human frames, all frames], last SHARE_WINDOW_S

    def frame(self, t_ms, state):
        if state not in STATES:
            raise ValueError(state)
        self._t, self.state = t_ms, state
        self.frames[state] += 1
        human = state != "fly"
        if human:
            self.last_human_ms = t_ms
        if self.clean(t_ms):
            self.frames["clean"] += 1
        sec = int(t_ms // 1000)
        if not self._secs or self._secs[-1][0] != sec:
            self._secs.append([sec, 0, 0])
            while self._secs and self._secs[0][0] <= sec - SHARE_WINDOW_S:
                self._secs.popleft()
        self._secs[-1][1] += human
        self._secs[-1][2] += 1

    def clean(self, t_ms=None):
        t = self._t if t_ms is None else t_ms
        return self.state == "fly" and (self.last_human_ms is None
                                        or t - self.last_human_ms >= self.clean_ms)

    def moved(self):
        self.tiles["clean" if self.clean() else "assisted"] += 1

    def bumped(self):
        self.bumps["clean" if self.clean() else "assisted"] += 1

    def stim(self, kind, human, source=None):
        k = f"{source or ('human' if human else 'game')}_{kind}"
        if k in self.stims:
            self.stims[k] += 1

    def since_human_s(self):
        """Brain-seconds since a human last had a key in, or None if never."""
        return None if self.last_human_ms is None else (self._t - self.last_human_ms) / 1000.0

    def human_share(self):
        """Fraction of frames in the last 5 minutes with a human key in."""
        h = sum(x[1] for x in self._secs)
        n = sum(x[2] for x in self._secs)
        return h / n if n else 0.0

    def credit(self):
        """What a progress event records about who made it happen."""
        s = self.since_human_s()
        return {"by": "fly" if self.clean() else "assisted",
                "since_human_s": None if s is None else round(s, 1),
                "human_share_5min": round(self.human_share(), 3),
                "control": self.state}

    def health(self):
        f, t, b, s = self.frames, self.tiles, self.bumps, self.stims
        since = self.since_human_s()
        return ("attrib: " + " ".join(f"{k}_frames={f[k]}" for k in STATES + ("clean",))
                + f" tiles_clean={t['clean']} tiles_assisted={t['assisted']}"
                + f" bumps_clean={b['clean']} bumps_assisted={b['assisted']}"
                + " " + " ".join(f"{k}={v}" for k, v in s.items())
                + f" since_human_s={'-' if since is None else f'{since:.0f}'}"
                + f" human_share_5min={self.human_share():.3f}")


class Journal:
    """Append-only JSON lines, one file per local day. Never raises into the
    caller: losing a log line must not cost the stream a frame."""

    def __init__(self, folder=JOURNAL_DIR, clock=time.time):
        self.folder, self.clock = folder, clock
        self._lock = threading.Lock()
        self.errors = 0

    def path(self, now=None):
        return os.path.join(self.folder, time.strftime("%Y-%m-%d", time.localtime(now)) + ".jsonl")

    def write(self, rec, path=None):
        rec = dict(rec)
        rec.setdefault("t", round(self.clock(), 3))
        try:
            with self._lock:
                os.makedirs(self.folder, exist_ok=True)
                with open(path or self.path(rec["t"]), "a") as f:
                    f.write(json.dumps(rec, separators=(",", ":")) + "\n")
        except (OSError, TypeError, ValueError):
            self.errors += 1

    def progress(self, rec):
        self.write(rec, path=os.path.join(self.folder, os.path.basename(PROGRESS_LOG)))


def party_changes(prev, team):
    """[(change, mon)] between two party snapshots, slot by slot (obsfly,
    2026-10-08: every run's starter was SQUIRTLE and none of the picks was
    logged, so whose pick it was took archaeology). 'joined' for a new slot (a
    starter, a catch, a gift), 'left' for a slot gone, 'changed' for a slot
    whose species changed (an evolution, or two slots swapped). None on either
    side means no comparable reading -- e.g. the first after a restart -- and
    gives nothing, so a restart never logs the whole party as joining."""
    if prev is None or team is None:
        return []
    out = []
    for i in range(max(len(prev), len(team))):
        a = prev[i] if i < len(prev) else None
        b = team[i] if i < len(team) else None
        if a is None and b is not None:
            out.append(("joined", b))
        elif b is None and a is not None:
            out.append(("left", a))
        elif a is not None and a.get("species") != b.get("species"):
            out.append(("changed", b))
    return out


def relation(key, fly_keys):
    """How a viewer's direction relates to what the fly was pressing at that
    moment: together / against / apart / fly_idle, or None for a non-direction."""
    from .game import UP, DOWN, LEFT, RIGHT
    m = {"up": UP, "down": DOWN, "left": LEFT, "right": RIGHT}.get(key)
    if m is None:
        return None
    fly = (fly_keys or 0) & (UP | DOWN | LEFT | RIGHT)
    if not fly:
        return "fly_idle"
    if fly & m:
        return "together"
    opposite = {UP: DOWN, DOWN: UP, LEFT: RIGHT, RIGHT: LEFT}[m]
    return "against" if fly == opposite else "apart"


NEAR = 0.5          # smell level the on-screen People/Doors labels light up at


class StillLog:
    """One record per stretch of standing still (Ian, 2026-10-07: "most of its
    stuck time is when it's not detecting something and it's in the pleasure
    range"). Measured, so that can be checked rather than assumed.

    update() is called once per window frame with the stretch's length in
    brain seconds (None while moving), the mood, and what is in range. When a
    stretch of at least MIN_S ends, a summary is returned for the journal:
    how long, the share of it spent content/happy (valence >= 0.15, the
    'content' label), the share with a person or door in smelling range, the
    mood when it began and at its longest, how many stillness-rule pains it
    took, and whether a human had a key in during it.

    2026-10-08 audit: a 3832 s stretch was marked "human" because it began
    within CLEAN_MS of a key, though nobody touched it for two hours after.
    `human_share` (frames under human influence / all frames) says how much
    of a stretch that really was; `dialogue_share` how much a dialogue box
    was open (when the stillness rule pauses, Ian 2026-10-08)."""

    MIN_S = 25.0                    # the on-screen "GOING NOWHERE" threshold

    def __init__(self, min_s=MIN_S):
        self.min_s = min_s
        self._ep = None
        self._prev = None

    def update(self, still_s, valence, label, near, human, pain=False, place=None,
               dialogue=False, shadow_pain=False):
        out = None
        if self._ep is not None and (still_s is None or (self._prev is not None
                                                         and still_s < self._prev)):
            out = self._close()
        self._prev = still_s
        if still_s is None:
            return out
        e = self._ep
        if e is None:
            e = self._ep = {"start_valence": valence, "start_label": label, "n": 0,
                            "content": 0, "near_any": 0, "near_people": 0, "near_exits": 0,
                            "valence_sum": 0.0, "pains": 0, "human": False, "place": place,
                            "human_n": 0, "dialogue_n": 0, "shadow": 0}
        e["n"] += 1
        e["dur"] = still_s
        if valence is not None:
            e["valence_sum"] += valence
            e["content"] += valence >= 0.15
            e["end_valence"], e["end_label"] = valence, label
        p, x = near.get("people", 0.0) >= NEAR, near.get("exits", 0.0) >= NEAR
        e["near_people"] += p
        e["near_exits"] += x
        e["near_any"] += p or x
        e["pains"] += bool(pain)
        e["human"] = e["human"] or bool(human)
        e["human_n"] += bool(human)
        e["dialogue_n"] += bool(dialogue)
        e["shadow"] += bool(shadow_pain)
        return out

    def _close(self):
        e, self._ep = self._ep, None
        if e is None or e.get("dur", 0) < self.min_s or not e["n"]:
            return None
        n = e["n"]
        r = lambda v: None if v is None else round(v, 3)
        return {"kind": "still", "dur_s": round(e["dur"], 1), "place": e["place"],
                "content_share": round(e["content"] / n, 3),
                "near_any_share": round(e["near_any"] / n, 3),
                "near_people_share": round(e["near_people"] / n, 3),
                "near_exits_share": round(e["near_exits"] / n, 3),
                "mean_valence": round(e["valence_sum"] / n, 3),
                "start_valence": r(e["start_valence"]), "start_label": e["start_label"],
                "end_valence": r(e.get("end_valence")), "end_label": e.get("end_label"),
                "rule_pains": e["pains"], "human": e["human"],
                "human_share": round(e["human_n"] / n, 3),
                "dialogue_share": round(e["dialogue_n"] / n, 3),
                # what the rule would have fired without the dialogue pause (counted only)
                "rule_pains_without_pause": e["shadow"]}


LEARNING_LOG = os.path.expanduser("~/flybrain/data/inputs/learning.jsonl")


def trainers(days=7, path=LEARNING_LOG, now=None):
    """Who changed the fly's learned synapses, for /panel/trainers: one row
    per (who, source) with bursts delivered and synaptic change booked to
    that source in their windows (learning.py ledger units), biggest first,
    plus per-source totals.

    2026-10-08 audit: overlapping windows double-counted. Bursts 0.7 s apart
    each booked the whole cluster's change (the game's three-pleasure bursts:
    1383 units in these rows vs 634 in learning.py's own ledger). A window
    flagged 'overlap' already holds the change of the same-source bursts
    after it, so a run of same-source bursts chained by 'overlap' is one
    cluster worth its FIRST row's booked (checked: 633.57 vs the ledger's
    633.566), shared equally among its bursts. 'overlapped' counts the bursts
    that were in such a cluster. The stillness rule's rows (logged as
    who='local') are named."""
    now = time.time() if now is None else now
    since = now - days * 86400
    picked = []
    try:
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("t", 0) < since or r.get("kind") != "learning":
                    continue
                picked.append(r)
    except OSError:
        pass
    picked.sort(key=lambda r: r.get("t", 0))
    clusters, prev = [], None
    for r in picked:
        src = r.get("source") or "unknown"
        if prev is not None and prev.get("overlap") and (prev.get("source") or "unknown") == src:
            clusters[-1].append(r)
        else:
            clusters.append([r])
        prev = r
    rows, totals = {}, {}
    for c in clusters:
        share = max(0.0, float(c[0].get("booked_to_source") or 0.0)) / len(c)
        for r in c:
            src, who = r.get("source") or "unknown", str(r.get("who") or "?")
            if src == "rule" and who == "local":
                who = "stillness rule"
            x = rows.setdefault((who, src), {"who": who, "source": src, "stims": 0,
                                             "booked": 0.0, "overlapped": 0})
            x["stims"] += 1
            x["booked"] += share
            x["overlapped"] += len(c) > 1
            t = totals.setdefault(src, {"stims": 0, "booked": 0.0})
            t["stims"] += 1
            t["booked"] += share
    out = sorted(rows.values(), key=lambda x: -x["booked"])
    for x in out:
        x["booked"] = round(x["booked"], 4)
    for t in totals.values():
        t["booked"] = round(t["booked"], 4)
    return {"days": days, "trainers": out, "by_source": totals,
            "units": "summed lost fraction of KC->MBON synaptic strength (learning.py ledger)"}
