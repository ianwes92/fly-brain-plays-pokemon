#!/usr/bin/env python3
"""Are the fly's on-screen thoughts true? Scored against the tile map.

Rules written 2026-10-07 BEFORE looking at any audit rows (Ian: "they don't
seem to reflect anything accurate"). Ground truth is the same tile grid the
fly's eyes are fed from (obstacle_view / target_view, player's own left/right).
Rows with a human hand on the keys or in a battle are excluded.

  walked into something - stopping   TRUE if the faced tile is solid
  wall on the S - turning away        WALL: any obstacle in view (max(obstacle_lr) > 0)
                                      SIDE: the named side S holds more obstacle than the other
                                      TURN: it turned, and AWAY from the heavier side
  wall ahead - turning                TRUE if the faced tile is solid
  a door on the S - ...               THERE: a door in range; SIDE: S holds more door than the other
  someone on the S - ...              THERE / SIDE as for doors, with people
  a way out, straight ahead           THERE: a door in range
  wandering                           not scored (claims nothing about the world)
A tie (both sides equal, e.g. a wall dead ahead only) counts as SIDE wrong:
the thought named a side the map does not single out.
"""
import json
import os
import sys
from collections import defaultdict

PATH = os.path.expanduser("~/flybrain/data/inputs/thought_audit.jsonl")


def side_of(lr):
    return "left" if lr[0] > lr[1] else "right" if lr[1] > lr[0] else None


def named_side(text):
    return "left" if " left" in text else "right" if " right" in text else None


def score(rows):
    res = defaultdict(lambda: defaultdict(lambda: [0, 0]))      # kind -> test -> [true, n]
    def mark(kind, test, ok):
        res[kind][test][0] += bool(ok)
        res[kind][test][1] += 1
    for r in rows:
        if r.get("human") or r.get("battle") or not r.get("overworld", True):
            continue
        t = r["thought"]
        s = named_side(t)
        if t.startswith("walked into something"):
            mark("walked into something", "faced tile solid", r["facing_solid"])
        elif t.startswith("wall on the"):
            k = "wall on the S - turning away"
            ob = r["obstacle_lr"]
            mark(k, "any obstacle in view", max(ob) > 0)
            mark(k, "named side is the heavier", side_of(ob) == s)
            heavy = side_of(ob)
            if heavy and r.get("turned"):
                mark(k, "turned away from the heavier side", r["turned"] != heavy)
        elif t.startswith("something close"):
            # 2026-10-07 wording: no side claimed for the obstacle; S is the turn
            k = "something close - turning S"
            ob = r["obstacle_lr"]
            mark(k, "any obstacle in view", max(ob) > 0)
            heavy = side_of(ob)
            if heavy and s:
                mark(k, "turned away from the heavier side", s != heavy)
            if r.get("applied") and s:
                mark(k, "named turn is the applied turn", r["applied"] == s)
        elif t.startswith("thinks someone"):
            k = "thinks someone's on the S (belief)"
            mark(k, "person in range", max(r["people_lr"]) > 0)
            mark(k, "named side is the heavier", side_of(r["people_lr"]) == s)
        elif t.startswith("wall ahead"):
            mark("wall ahead - turning", "faced tile solid", r["facing_solid"])
        elif t.startswith("a door on the"):
            k = "a door on the S"
            mark(k, "door in range", max(r["exits_lr"]) > 0)
            mark(k, "named side is the heavier", side_of(r["exits_lr"]) == s)
        elif t.startswith("someone on the"):
            k = "someone on the S"
            mark(k, "person in range", max(r["people_lr"]) > 0)
            mark(k, "named side is the heavier", side_of(r["people_lr"]) == s)
        elif t.startswith("a way out"):
            mark("a way out, straight ahead", "door in range", max(r["exits_lr"]) > 0)
    return res


def main(path=PATH):
    rows = []
    with open(path) as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                pass
    used = [r for r in rows if not (r.get("human") or r.get("battle") or not r.get("overworld", True))]
    print(f"{len(rows)} thoughts logged, {len(used)} scored (fly alone, overworld)")
    for kind, tests in score(rows).items():
        print(f"\n{kind}")
        for test, (ok, n) in tests.items():
            print(f"  {test:40} {ok}/{n} = {100 * ok / n:.0f}%")


if __name__ == "__main__":
    main(*sys.argv[1:])
