"""Experiment intentions are posted once per stage to every channel (2026-10-08)."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))
import experiment_notice as N
d = tempfile.mkdtemp()
p = os.path.join(d, "notice.json")
N.EXT_F, N.LOG = os.path.join(d, "ext.txt"), os.path.join(d, "log")
json.dump({"name": "x", "lines": {"t-1h": "soon", "start": "now"}, "ext": "E" * 200, "posted": {}}, open(p, "w"))
got = []
def ok(t): got.append(t)
def bad(t): raise RuntimeError("down")
assert N.post("t-1h", p, {"twitch": ok, "youtube": bad, "discord": ok}) is True
assert got == ["soon", "soon"], got                       # a failing channel does not stop the rest
assert open(N.EXT_F).read() == "E" * 160                  # panel line, clipped to 160
assert N.post("t-1h", p, {"twitch": ok}) is False and len(got) == 2   # never twice
assert "t-1h" in json.load(open(p))["posted"]
print("test_experiment_notice: 5 checks passed")
