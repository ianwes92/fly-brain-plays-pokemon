"""Run registry and per-run export for the run reports (2026-10-08)."""
import json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "play"))
import run_export as X

d = tempfile.mkdtemp()
X.ROOT, X.CFG = d, os.path.join(d, "cfg")
X.REGISTRY = os.path.join(X.CFG, "runs.json")
a2 = os.path.join(d, "runs", "run2-x")
for sub in ("inputs", "samples", "thoughts"):
    os.makedirs(os.path.join(a2, "science", sub))
os.makedirs(X.CFG)
json.dump({"runs": [{"id": 2, "kind": "test", "started": "2026-10-04T08:00:00", "ended": "2026-10-04T12:00:00",
                     "reason": "manual: x", "archive": "runs/run2-x"}],
           "experiments": [{"name": "e", "run": 2}]}, open(X.REGISTRY, "w"))
json.dump({"been": [88, 101], "furthest": 101}, open(os.path.join(a2, "places.json"), "w"))
json.dump({"next_id": 4, "events": [{"id": 1, "kind": "catch", "text": "CAUGHT PIDGEY!", "by": "fly"},
                                    {"id": 2, "kind": "badge", "text": "EARNED A BADGE!  (1 in total)"},
                                    {"id": 3, "kind": "reset", "text": "x"}]}, open(os.path.join(a2, "events.json"), "w"))
t0 = X._ts("2026-10-04T08:00:00")
with open(os.path.join(a2, "science", "samples", "2026-10-04.jsonl"), "w") as f:
    for i, (tiles, clean) in enumerate(((100, 90), (160, 140), (400, 380))):
        f.write(json.dumps({"t": t0 + 600 * (i + 1), "tiles": tiles, "tiles_clean": clean}) + "\n")
    f.write(json.dumps({"t": t0 + 99_999, "tiles": 9999, "tiles_clean": 9999}) + "\n")   # after the run
r = X.runs()
assert r[0]["id"] == 2 and r[0]["live"] is False and r[0]["data"]["archive"], r
e = X.export(2, now=t0 + 10 ** 6)
assert e["progress"]["badges"] == 1 and e["progress"]["caught"] == ["CAUGHT PIDGEY!"], e["progress"]
assert [m["kind"] for m in e["progress"]["milestones"]] == ["catch", "badge"]          # no reset event
assert e["attrib"]["tiles"] == 300 and e["attrib"]["tiles_clean"] == 290, e["attrib"]  # within the run only
assert e["learning"] is None and e["experiments"] == [{"name": "e", "run": 2}]
assert X.export(9) is None
assert len(json.dumps(e)) < 2_000_000
print("test_run_export: 7 checks passed")
