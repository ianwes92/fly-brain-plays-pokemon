#!/usr/bin/env python3
"""Is the stillness rule firing less since the dialogue pause? (Ian, 2026-10-08)

Two comparisons, from the journals (restart-proof):

1. LIKE FOR LIKE, since the pause went in: every stretch of standing still
   logged since then carries the pains the rule actually delivered AND the
   pains the old rule (no pause) would have delivered on the same frames
   (attrib.StillLog: rule_pains vs rule_pains_without_pause). The difference
   is exactly what the pause saved. Only stretches of 25 s or more are logged;
   any pain needs 60 s, so none is missed.
2. BEFORE vs AFTER, per hour of play: rule pains per hour before the pause
   and after. Confounded by where the fly happened to be (one 2 h 20 min
   stretch at Route 3 on 10-07 gave 377 of the 467 pains before), so it is
   shown with and without that stretch, and comparison 1 is the one to trust.

Usage: still_rule_report.py [--since "YYYY-MM-DD HH:MM"]   (default: the pause's
fixed detector, 2026-10-08 08:16:49)
"""
import glob
import json
import os
import sys
import time

INPUTS = os.path.expanduser("~/flybrain/data/inputs")
SAMPLES = os.path.expanduser("~/flybrain/data/samples")
PAUSE_ON = time.mktime(time.strptime("2026-10-08 08:16:49", "%Y-%m-%d %H:%M:%S"))
RULE_ON = time.mktime(time.strptime("2026-10-07 14:30:00", "%Y-%m-%d %H:%M:%S"))
SAMPLE_GAP_S = 900        # a longer gap between 10-minute samples is downtime, not play


# Run 2's record was archived by the run-3 reset (2026-10-08 09:16); the
# "before" baseline lives there now.
ARCHIVES = os.path.expanduser("~/flybrain/runs/run*-*/science")


def _rows(pattern):
    out = []
    rel = os.path.relpath(pattern, os.path.dirname(INPUTS))      # e.g. inputs/progress.jsonl
    old = sorted(glob.glob(os.path.join(ARCHIVES, rel)))
    for path in old + sorted(glob.glob(pattern)):
        with open(path) as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except ValueError:
                    pass
    return out


def play_hours(t0, t1):
    """Hours the game was up between t0 and t1, from the 10-minute samples."""
    ts = sorted(r["t"] for r in _rows(os.path.join(SAMPLES, "*.jsonl")) if t0 <= r.get("t", 0) <= t1)
    return sum(b - a for a, b in zip(ts, ts[1:]) if b - a <= SAMPLE_GAP_S) / 3600.0


def report(since=PAUSE_ON, now=None):
    now = time.time() if now is None else now
    prog = [p for p in _rows(os.path.join(INPUTS, "progress.jsonl")) if p.get("kind") == "still"]
    pains = [r for r in _rows(os.path.join(INPUTS, "????-??-??.jsonl")) if r.get("kind") == "rule_stim"]
    after = [p for p in prog if p.get("t", 0) >= since and "rule_pains_without_pause" in p]
    got = sum(p["rule_pains"] for p in after)
    would = sum(p["rule_pains_without_pause"] for p in after)
    dl = sum(p["dur_s"] * p.get("dialogue_share", 0) for p in after)
    big = [p for p in prog if RULE_ON <= p.get("t", 0) < since and p.get("rule_pains", 0) >= 100]
    big_pains = sum(p["rule_pains"] for p in big)
    before_n = sum(1 for r in pains if RULE_ON <= r["t"] < since)
    after_n = sum(1 for r in pains if r["t"] >= since)
    hb, ha = play_hours(RULE_ON, since), play_hours(since, now)
    rate = lambda n, h: round(n / h, 1) if h >= 0.5 else None
    return {
        "since": time.strftime("%Y-%m-%d %H:%M", time.localtime(since)),
        "like_for_like": {"stretches": len(after), "pains_delivered": got,
                          "pains_without_pause": would, "saved_by_pause": would - got,
                          "dialogue_s": round(dl), "still_s": round(sum(p["dur_s"] for p in after))},
        "before_vs_after": {
            "before": {"pains": before_n, "play_h": round(hb, 1), "per_h": rate(before_n, hb),
                       "per_h_without_longest": rate(before_n - big_pains, hb),
                       "longest_stretches": [{"t": time.strftime("%m-%d %H:%M", time.localtime(p["t"])),
                                              "pains": p["rule_pains"], "place": p.get("place")}
                                             for p in big]},
            "after": {"pains": after_n, "play_h": round(ha, 1), "per_h": rate(after_n, ha)}},
    }


def main(argv):
    since = PAUSE_ON
    if "--since" in argv:
        since = time.mktime(time.strptime(argv[argv.index("--since") + 1], "%Y-%m-%d %H:%M"))
    r = report(since)
    if "--json" in argv:
        print(json.dumps(r, indent=1))
        return
    l, b = r["like_for_like"], r["before_vs_after"]
    print(f"Since {r['since']} (dialogue pause on):")
    print(f"  {l['stretches']} still stretches, {l['still_s']} brain-s still, {l['dialogue_s']} s of it in dialogue")
    print(f"  rule pains delivered {l['pains_delivered']}, without the pause {l['pains_without_pause']}"
          f" -> pause saved {l['saved_by_pause']}")
    bb, aa = b["before"], b["after"]
    print(f"Before: {bb['pains']} pains in {bb['play_h']} h of play = {bb['per_h']}/h"
          f" ({bb['per_h_without_longest']}/h without the {len(bb['longest_stretches'])} stretch(es) of 100+ pains)")
    print(f"After:  {aa['pains']} pains in {aa['play_h']} h of play = {aa['per_h']}/h")


if __name__ == "__main__":
    main(sys.argv)
