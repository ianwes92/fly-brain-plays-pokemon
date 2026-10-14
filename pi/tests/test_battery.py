"""The automatic per-run experiment (2026-10-13), offline."""
import datetime as dt, json, os, sys, tempfile
import numpy as np
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "data", "experiments")); sys.path.insert(0, ROOT)
import battery as B
d = tempfile.mkdtemp()
B.DIR, B.REG, B.NOTICE, B.EXT = (os.path.join(d, x) for x in ("battery", "runs.json", "notice.json", "ext.txt"))
arch = os.path.join(d, "runs", "run4-x"); os.makedirs(arch)
m = np.ones(100, np.float32); m[:10] = 0.5
np.savez(os.path.join(arch, "firered.resume.ss.mb.npz"), m=m)
json.dump({"runs": [{"id": 4, "kind": "lab", "started": "2026-10-08T13:14:39", "ended": "2026-10-22T13:00:30",
                     "reason": "manual: 14 days", "archive": "runs/run4-x"}], "experiments": []}, open(B.REG, "w"))
posted = []
B._notice = lambda stage, line=None: posted.append((stage, line))
assert B.plan(now=dt.datetime(2026, 10, 22, 13, 30), root=d) == "planned"
p = json.load(open(os.path.join(B.DIR, "run4", "plan.json")))
assert p["start"] == "2026-10-23T00:30:00" and p["depressed"] == 5.0
assert json.load(open(B.NOTICE))["lines"]["today"].startswith("🧪 Tonight 00:30 ET: untrained vs Lab Run 1")
assert posted == [("today", None)]
assert B.plan(now=dt.datetime(2026, 10, 23, 13, 30), root=d) is None        # once per run
assert B._tonight(dt.datetime(2026, 10, 22, 13, 30)) is None                # not yet tonight
rows = os.path.join(B.DIR, "run4", "rows.jsonl")
def harness():
    for s in range(12):
        for arm, a in (("naive", 100), ("trained", 100 + (s % 3) - 1)):
            open(rows, "a").write(json.dumps({"seed": s, "arm": arm, "attraction_turns": a, "toward_exit": a, "toward_person": 0,
                "away_from_exit": 0, "away_from_person": 0, "opportunities": 1000, "opp_people": 1, "opp_exits": 1,
                "mean_valence": 0.0 if arm == "naive" else -0.3, "moves": 400, "bumps": 4, "bumps_per_tile": 0.01,
                "walking_frac": 0.5, "left": 100, "right": 110,
                "opinions_start": {"people": 0.0, "exits": 0.0 if arm == "naive" else -0.2}}) + "\n")
    return 0
assert B.run(now=dt.datetime(2026, 10, 23, 0, 30), harness=harness) == "scored"
line = posted[-1][1]
assert posted[-2][0] == "start" and posted[-1][0] == "result" and "not independently checked" in line, posted
assert "did NOT change" in line or "inconclusive" in line, line
reg = json.load(open(B.REG))["experiments"][0]
assert reg["status"].startswith("scored") and os.path.exists(os.path.join(B.DIR, "run4", "score.txt"))
assert B.result_line("PRECONDITION FAILED -- x", "Lab Run 1").startswith("🧪 Result: Lab Run 1's training left too little")
print("test_battery: 12 checks passed")
