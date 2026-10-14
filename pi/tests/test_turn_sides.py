"""The per-rule left/right turn log (2026-10-08): log only.

Fast: each rule's turns land under its own name and sum to stats left/right.
Slow (FLYBRAIN_SLOW=1, ~10 min): seed 0 of the ethogram, normal world, must
reproduce the mirror rerun's row (made before the log existed) exactly --
moves, turns, turn signal -- so the log changed nothing the fly does."""
import json
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from flybrain import game                                    # noqa: E402
from flybrain.playview import turn_sides                     # noqa: E402

ROWS = os.path.expanduser("~/flybrain/data/experiments/mirror_world.jsonl")


class Brainless(game.Hands):
    """Hands without a connectome: only decide() and its counters."""
    def __init__(self):
        self.stats = {"turns": 0, "a": 0, "b": 0, "bouts": 0}
        self.sides, self._attr_src = {}, None
        self.heading, self.last_turn, self.commit_until = 0, -1e9, -1e9
        self.mirror_world, self.avoid_side, self.wall_side = False, "median", "zero"
        self.obstacle = self.exits = self.people = self.exit_ahead = 0.0
        self.obstacle_thresh, self.exit_thresh, self.people_thresh = 10.0, 10.0, 10.0
        self.exit_ahead_thresh, self.wall_thresh, self.wall_ahead = 1e9, 10.0, 0.0
        self.turn, self.turn_mid, self.turn_left, self.turn_right, self.turn_scale = 0.0, 0.0, 5.0, -5.0, 1.0
        self.commit_ms, self.opinion, self.urge_talk, self.urge_leave = 0.0, {}, 0.0, 0.0


def _turn(h, t, **kw):
    for k, v in kw.items():
        setattr(h, k, v)
    h.last_turn = -1e9
    h.commit_until = -1e9
    before = dict(h.sides)
    try:
        h.decide(t)
    except Exception:
        pass
    return {k: v - before.get(k, 0) for k, v in h.sides.items() if v != before.get(k, 0)}


def test_each_rule_logged_under_its_name():
    h = Brainless()
    assert _turn(h, 1, obstacle=20.0, turn=1.0, turn_mid=0.0) == {"avoid_left": 1}
    assert _turn(h, 2, obstacle=20.0, turn=-1.0) == {"avoid_right": 1}
    assert _turn(h, 3, obstacle=0.0, exits=20.0, turn=0.0) == {"toward_exit_left": 1}
    assert _turn(h, 4, exits=0.0, people=-20.0) == {"toward_person_right": 1}
    h.opinion = {"people": -0.8}
    assert _turn(h, 5, people=-20.0) == {"away_from_person_left": 1}
    h.opinion = {}
    assert _turn(h, 6, people=0.0, wall_ahead=20.0, turn=-1.0) == {"wall_right": 1}
    assert _turn(h, 7, wall_ahead=0.0, turn=9.0) == {"spontaneous_left": 1}
    assert _turn(h, 8, turn=0.0) == {}                 # no turn, nothing logged
    left = sum(v for k, v in h.sides.items() if k.endswith("_left"))
    right = sum(v for k, v in h.sides.items() if k.endswith("_right"))
    assert (left, right) == (h.stats["left"], h.stats["right"]) == (4, 3)


def test_mirror_world_logs_the_decided_side():
    h = Brainless()
    h.mirror_world = True
    assert _turn(h, 1, obstacle=20.0, turn=1.0) == {"avoid_left": 1}
    assert h.turned_side == "right"


def test_unnamed_rule_still_counts():
    h = Brainless()
    h._attraction = lambda: 1            # a stub that names no rule
    assert _turn(h, 1, obstacle=0.0) == {"other_right": 1}


def test_health_line():
    h = Brainless()
    h.sides = {"avoid_left": 3, "avoid_right": 5, "wall_right": 1}
    line = turn_sides(h)
    assert line.startswith("turn_sides: avoid=3/5 spontaneous=0/0 wall=0/1 ")
    assert "toward_person=0/0" in line and "away_from_exit=0/0" in line
    assert "other" not in line
    h.sides["other_right"] = 2
    assert turn_sides(h).endswith(" other=0/2")


def test_reproduces_pre_log_row():
    if os.environ.get("FLYBRAIN_SLOW") != "1":
        print("  (slow repro skipped; FLYBRAIN_SLOW=1 to run)")
        return
    os.environ["FLYBRAIN_MIRROR"] = ""
    sys.path.insert(0, os.path.join(ROOT, "data", "experiments"))
    import ethogram_now as eth
    eth.SCRATCH = "/tmp/claude-1000/turn_sides_test"
    samples = []
    orig = game.Hands.decide

    def decide(self, t_ms):
        k = orig(self, t_ms)
        samples.append(self.turn)
        return k
    game.Hands.decide = decide
    try:
        row = eth.run(0, "fly")
    finally:
        game.Hands.decide = orig
    a = np.asarray(samples[len(samples) // 10:])
    ref = next(r for r in map(json.loads, open(ROWS)) if r["seed"] == 0 and r["arm"] == "normal")
    for k in ("moves", "distinct", "bumps", "left", "right", "n_bouts", "walking_frac"):
        assert row[k] == ref[k], (k, row[k], ref[k])
    assert row["stats"] == ref["stats"], (row["stats"], ref["stats"])
    assert float(np.mean(a < 0)) == ref["turn_below_zero"] and a.size == ref["turn_samples"]
    s = row["sides"]
    assert sum(v for k, v in s.items() if k.endswith("_left")) == row["left"]
    assert sum(v for k, v in s.items() if k.endswith("_right")) == row["right"]
    print("  repro OK:", s)


if __name__ == "__main__":
    for name, f in list(globals().items()):
        if name.startswith("test_"):
            f()
            print("ok", name)
