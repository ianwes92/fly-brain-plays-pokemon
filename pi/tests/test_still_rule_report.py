"""still_rule_report: like-for-like pause savings and before/after rates."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))
import still_rule_report as R

d = tempfile.mkdtemp()
R.INPUTS, R.SAMPLES = os.path.join(d, "inputs"), os.path.join(d, "samples")
R.ARCHIVES = os.path.join(d, "none")
os.makedirs(R.INPUTS); os.makedirs(R.SAMPLES)
P = R.PAUSE_ON
with open(os.path.join(R.INPUTS, "progress.jsonl"), "w") as f:
    for row in ({"kind": "still", "t": P - 100, "dur_s": 900, "rule_pains": 150},
                {"kind": "still", "t": P + 100, "dur_s": 120, "rule_pains": 2, "rule_pains_without_pause": 6,
                 "dialogue_share": 0.5},
                {"kind": "area", "t": P + 200}):
        f.write(json.dumps(row) + "\n")
with open(os.path.join(R.INPUTS, "2026-10-08.jsonl"), "w") as f:
    for t in [P - 3000 + i for i in range(160)] + [P + 50, P + 60]:
        f.write(json.dumps({"kind": "rule_stim", "t": t}) + "\n")
with open(os.path.join(R.SAMPLES, "2026-10-08.jsonl"), "w") as f:
    for t in range(int(P - 7200), int(P + 7201), 600):
        f.write(json.dumps({"t": t}) + "\n")
r = R.report(now=P + 7200)
l, b = r["like_for_like"], r["before_vs_after"]
assert (l["stretches"], l["pains_delivered"], l["pains_without_pause"], l["saved_by_pause"]) == (1, 2, 6, 4), l
assert l["dialogue_s"] == 60, l
assert b["before"]["pains"] == 160 and b["after"]["pains"] == 2, b
assert b["before"]["per_h"] == 80.0 and b["before"]["per_h_without_longest"] == 5.0, b
assert b["after"]["per_h"] == 1.0, b
print("test_still_rule_report: 7 checks passed")
