#!/usr/bin/env python3
"""Who did what to the fly: a per-viewer report from the input journal.

Reads data/inputs/YYYY-MM-DD.jsonl (written by the game, see flybrain/attrib.py)
and data/inputs/progress.jsonl. For each viewer: every command by kind, how
many got through and why the rest did not, pain vs pleasure, whether their
directions pulled WITH the fly or AGAINST it, and how fast they were going.
Flags are descriptive, never automatic punishments:
  pain-only   5+ stim requests, 80%+ of them pain, and no steering at all
  spammy      20+ inputs and half or more refused for being too fast
Then the fly: how much of the progress it made on its own.

Usage:
  inputs_report.py                 the last 24 hours
  inputs_report.py --hours 168     the last week
  inputs_report.py --user NAME     one viewer's inputs, line by line
  inputs_report.py --json          machine-readable (for obsfly's panel)
"""
import glob
import json
import os
import sys
import time
from collections import Counter, defaultdict

FOLDER = os.path.expanduser("~/flybrain/data/inputs")
REFUSED_FAST = {"paced", "cooldown_user", "cooldown_global"}
SYSTEM = {"the game", "stillness rule"}     # automatic, not viewers: counted in totals


def load(folder=FOLDER, since=0.0):
    recs = []
    for path in sorted(glob.glob(os.path.join(folder, "????-??-??.jsonl"))):
        day = os.path.basename(path)[:10]
        try:
            if time.mktime(time.strptime(day, "%Y-%m-%d")) + 2 * 86400 < since:
                continue
        except ValueError:
            continue
        with open(path) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("t", 0) >= since:
                    recs.append(r)
    return recs


def load_progress(folder=FOLDER, since=0.0):
    out = []
    try:
        with open(os.path.join(folder, "progress.jsonl")) as f:
            for line in f:
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if r.get("t", 0) >= since:
                    out.append(r)
    except OSError:
        pass
    return out


def per_user(recs):
    """{login: summary}. Delivered stims are logged under the display name;
    they are folded into the login that requested under that name."""
    names = {}
    for r in recs:
        if r.get("name"):
            names[str(r["name"]).lower()] = str(r["user"]).lower()
    users = defaultdict(lambda: {"inputs": 0, "accepted": 0, "refused": Counter(),
                                 "controls": Counter(), "vs_fly": Counter(),
                                 "pain": 0, "pleasure": 0, "delivered": Counter(),
                                 "steer_s": 0.0, "bits": 0, "asks": 0,
                                 "first": None, "last": None, "kinds": Counter()})
    for r in recs:
        user = str(r.get("user", "?")).lower()
        kind = str(r.get("kind"))
        if kind == "stim_delivered":
            if r.get("outcome") == "game" or user in SYSTEM:
                continue
            u = users[names.get(user, user)]
            u["delivered"][r.get("key")] += 1
            continue
        if user in SYSTEM or kind == "rule_stim":
            continue
        u = users[user]
        t = r.get("t", 0)
        u["first"] = t if u["first"] is None else min(u["first"], t)
        u["last"] = t if u["last"] is None else max(u["last"], t)
        u["inputs"] += 1
        u["kinds"][kind] += 1
        out = r.get("outcome")
        if out in ("ok", "ok_driver", "queued", "admin", None):
            u["accepted"] += 1
        else:
            u["refused"][out] += 1
        if kind in ("control", "panel_chat", "panel_nudge", "panel_press") and r.get("key"):
            # the panel's chat/nudge/press buttons are directions too
            u["controls"][r.get("key")] += 1
            if r.get("vs_fly"):
                u["vs_fly"][r["vs_fly"]] += 1
        elif kind in ("free_stim", "paid_stim", "panel_stim"):
            n = int(r.get("n", 1) or 1)
            if r.get("key") in ("pain", "pleasure"):
                u[r["key"]] += n
        elif kind == "steer":
            u["steer_s"] += float(r.get("seconds", 0) or 0)
        elif kind == "cheer":
            u["bits"] += int(r.get("bits", 0) or 0)
        elif kind == "ask":
            u["asks"] += 1
    for name, u in users.items():
        span_min = max(1.0, ((u["last"] or 0) - (u["first"] or 0)) / 60)
        u["per_min"] = round(u["inputs"] / span_min, 2)
        stims = u["pain"] + u["pleasure"]
        fast = sum(v for k, v in u["refused"].items() if k in REFUSED_FAST)
        flags = []
        if stims >= 5 and u["pain"] / stims >= 0.8 and not u["controls"] and not u["steer_s"]:
            flags.append("pain-only")
        if u["inputs"] >= 20 and fast / u["inputs"] >= 0.5:
            flags.append("spammy")
        u["flags"] = flags
    return dict(users)


HUMAN_STILL_SHARE = 0.1


def _with_human(p):
    """A stretch is the human's if a human key was in for 10%+ of it. Stretches
    logged before 2026-10-08 have only the old flag (any human frame at all),
    which put a 2-hour stretch that began 1 s after a key on the human side."""
    if "human_share" in p:
        return p["human_share"] >= HUMAN_STILL_SHARE
    return bool(p.get("human"))


def still_summary(progress, human=False):
    """Stretches of standing still (attrib.StillLog), the fly's own unless
    human=True. Shares are weighted by how long each stretch lasted."""
    eps = [p for p in progress if p.get("kind") == "still" and _with_human(p) == human]
    total = sum(p["dur_s"] for p in eps)
    if not eps:
        return {"stretches": 0}
    w = lambda k: round(sum(p[k] * p["dur_s"] for p in eps) / total, 3)
    durs = sorted(p["dur_s"] for p in eps)
    dl = [q for q in eps if "dialogue_share" in q and q["dur_s"] > 0]
    return {"stretches": len(eps), "total_s": round(total), "median_s": durs[len(durs) // 2],
            "longest_s": durs[-1], "content_share": w("content_share"),
            "near_any_share": w("near_any_share"), "near_people_share": w("near_people_share"),
            "near_exits_share": w("near_exits_share"), "mean_valence": w("mean_valence"),
            "content_and_nothing_near": round(sum(
                p["dur_s"] for p in eps if p["content_share"] >= 0.5 and p["near_any_share"] < 0.5)
                / total, 3),
            # time-weighted; only stretches logged since 2026-10-08 have it
            "dialogue_share": (round(sum(q["dialogue_share"] * q["dur_s"] for q in dl)
                                     / sum(q["dur_s"] for q in dl), 3) if dl else None),
            "pained": sum(1 for p in eps if p.get("rule_pains")),
            "rule_pains": sum(p.get("rule_pains", 0) for p in eps),
            # since 2026-10-08: the same stretches' pains had there been no dialogue pause
            "rule_pains_without_pause": (sum(p["rule_pains_without_pause"] for p in eps
                                             if "rule_pains_without_pause" in p)
                                         if any("rule_pains_without_pause" in p for p in eps) else None)}


def fly_summary(progress):
    progress = [p for p in progress if p.get("kind") != "still"]
    by = Counter(p.get("by", "?") for p in progress)
    return {"events": len(progress), "by_fly": by.get("fly", 0), "assisted": by.get("assisted", 0),
            "list": [(p.get("kind"), p.get("text"), p.get("by"), p.get("since_human_s"))
                     for p in progress]}


def fmt_counter(c):
    return " ".join(f"{k}:{v}" for k, v in c.most_common()) or "-"


def text_report(users, fly, hours):
    out = [f"Inputs in the last {hours:g}h: {sum(u['inputs'] for u in users.values())} from "
           f"{len(users)} user(s)"]
    for name, u in sorted(users.items(), key=lambda kv: -kv[1]["inputs"]):
        out.append("")
        out.append(f"{name}  {'[' + ', '.join(u['flags']) + ']' if u['flags'] else ''}".rstrip())
        out.append(f"  {u['inputs']} inputs ({u['accepted']} got through), {u['per_min']}/min, "
                   f"{time.strftime('%m-%d %H:%M', time.localtime(u['first']))} -> "
                   f"{time.strftime('%m-%d %H:%M', time.localtime(u['last']))}")
        if u["refused"]:
            out.append(f"  refused: {fmt_counter(u['refused'])}")
        if u["controls"]:
            out.append(f"  directions: {fmt_counter(u['controls'])} | vs the fly: {fmt_counter(u['vs_fly'])}")
        if u["pain"] or u["pleasure"]:
            out.append(f"  asked pain {u['pain']}, pleasure {u['pleasure']} | delivered: "
                       f"{fmt_counter(u['delivered'])}")
        extra = [f"steering {u['steer_s']:.0f}s" if u["steer_s"] else "",
                 f"{u['bits']} bits" if u["bits"] else "", f"{u['asks']} questions" if u["asks"] else ""]
        if any(extra):
            out.append("  " + ", ".join(x for x in extra if x))
    out.append("")
    out.append(f"The fly: {fly['events']} progress event(s): {fly['by_fly']} on its own, "
               f"{fly['assisted']} with a human hand on the keys")
    for kind, text, by, since in fly["list"]:
        s = "" if since is None else f" ({since:.0f}s after a human key)"
        out.append(f"  {kind:7} {text} -- {by}{s if by == 'assisted' else ''}")
    st = fly.get("still") or {}
    out.append("")
    if st.get("stretches"):
        out.append(f"Standing still 25s+ (the fly alone): {st['stretches']} stretches, "
                   f"{st['total_s']}s in all, median {st['median_s']:.0f}s, longest {st['longest_s']:.0f}s")
        out.append(f"  of that time: content/happy {st['content_share']:.0%}, person or door in "
                   f"range {st['near_any_share']:.0%} (people {st['near_people_share']:.0%}, doors "
                   f"{st['near_exits_share']:.0%}), mean mood {st['mean_valence']:+.2f}")
        out.append(f"  mostly content AND nothing in range: {st['content_and_nothing_near']:.0%} of the time")
        out.append(f"  stillness-rule pains: {st['rule_pains']} across {st['pained']} stretches")
    else:
        out.append("Standing still 25s+ (the fly alone): none logged yet")
    return "\n".join(out)


def report(hours=24.0, folder=FOLDER, now=None):
    """What --json prints and the relay's /panel/inputs serves."""
    since = (time.time() if now is None else now) - hours * 3600
    prog = load_progress(folder, since=since)
    fly = fly_summary(prog)
    fly["still"] = still_summary(prog)
    fly["still_with_human"] = still_summary(prog, human=True)
    recs = load(folder, since=since)
    users = per_user(recs)
    viewer = [r for r in recs if str(r.get("user", "")).lower() not in SYSTEM
              and r.get("kind") not in ("stim_delivered", "rule_stim")]
    totals = {"inputs": len(viewer), "users": len(users),
              "by_kind": dict(Counter(str(r.get("kind")) for r in viewer)),
              "by_outcome": dict(Counter(str(r.get("outcome")) for r in viewer)),
              "delivered_stims": dict(Counter(f"{r.get('outcome')}_{r.get('key')}" for r in recs
                                              if r.get("kind") == "stim_delivered")),
              "rule_pains": sum(1 for r in recs if r.get("kind") == "rule_stim")}
    return json.loads(json.dumps({"hours": hours, "totals": totals, "users": users,
                                  "fly": fly}, default=dict))


def main(argv):
    hours = 24.0
    if "--hours" in argv:
        hours = float(argv[argv.index("--hours") + 1])
    since = time.time() - hours * 3600
    recs = load(since=since)
    if "--user" in argv:
        who = argv[argv.index("--user") + 1].lower()
        for r in recs:
            if str(r.get("user", "")).lower() == who or str(r.get("name", "")).lower() == who:
                print(time.strftime("%m-%d %H:%M:%S", time.localtime(r["t"])),
                      r.get("kind"), r.get("key") or "", r.get("outcome") or "",
                      r.get("vs_fly") or "", f"place={r.get('place')}")
        return
    users = per_user(recs)
    prog = load_progress(since=since)
    fly = fly_summary(prog)
    fly["still"] = still_summary(prog)
    fly["still_with_human"] = still_summary(prog, human=True)
    if "--json" in argv:
        print(json.dumps(report(hours), indent=1))
    else:
        print(text_report(users, fly, hours))


if __name__ == "__main__":
    main(sys.argv)
