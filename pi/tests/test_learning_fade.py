"""Continuous forgetting: off by default (experiments unchanged); when on,
depression recovers between bursts at tau_recover, booked to every source
in proportion; the regime is logged, snapshotted first, and applied once."""
import json, os, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.learning import MushroomBodyLearning, LearningParams
from flybrain import playview as pv

ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1

class B:
    def __init__(self, n): self.w_mv = np.zeros(n, dtype=np.float32)

def make():
    L = MushroomBodyLearning.__new__(MushroomBodyLearning)
    L.p = LearningParams()
    L.m = np.full(4, 0.5, dtype=np.float32)
    L.edges = np.arange(4); L.w0 = np.ones(4, dtype=np.float32)
    L.brain = B(4); L.version = 0; L._idle_ms = 0.0
    L.recover_always = False
    L.ledger = {"outstanding": {"human": 1.0, "before_ledger": 1.0}, "depressed": {}, "recovered": 0.0}
    L.credit = "human"
    return L

L = make()
L._recover_idle(5000.0)
check("helper alone does not check the switch", True)
L = make()
L.frozen, L.gate = False, (lambda: False)
for _ in range(100):
    if L.gate is not None and not L.gate() and L.recover_always:
        L._recover_idle(20.0)
check("off by default: nothing moves", np.allclose(L.m, 0.5) and L.ledger["recovered"] == 0.0)

L = make()
L.p.tau_recover = 7_200_000.0              # 2 h
L.recover_always = True
for _ in range(3600):                       # one brain-hour in 1 s steps
    L._recover_idle(1000.0)
expect = 1 - 0.5 * np.exp(-3600 / 7200)
check("exponential at tau", np.allclose(L.m, expect, atol=1e-4))
out = L.ledger["outstanding"]
check("both sources faded in proportion", abs(out["human"] - out["before_ledger"]) < 1e-9
      and abs(out["human"] - (1 - 4 * (expect - 0.5) / 2)) < 1e-3)
check("weights written", np.allclose(L.brain.w_mv, L.m))
check("sub-second steps accumulate", (lambda M: (M._recover_idle(400.0), M._recover_idle(400.0),
                                                np.allclose(M.m, 0.5))[-1])(make()))

L = make()
d = tempfile.mkdtemp()
msgs = []
cfg = {"recover_always": True, "tau_recover_s": 7200.0}
check("first apply snapshots + logs", pv.apply_learning_settings(L, cfg, d, log=msgs.append, now=1e9))
snaps = os.listdir(d)
check("snapshot is the state before", len(snaps) == 1 and np.allclose(np.load(os.path.join(d, snaps[0]))["m"], 0.5))
check("regime in ledger", L.ledger["regimes"][-1]["change"] == "recover_always=True tau_recover=7200s")
check("settings applied", L.recover_always and L.p.tau_recover == 7_200_000.0)
check("second apply is a no-op", not pv.apply_learning_settings(L, cfg, d, log=msgs.append) and len(os.listdir(d)) == 1)
check("no config, no change", not pv.apply_learning_settings(L, None, d))
f = os.path.join(d, "l.json"); json.dump(cfg, open(f, "w"))
check("settings file read", pv.learning_settings(f) == cfg)
check("missing file", pv.learning_settings(os.path.join(d, "nope")) is None)
print(f"test_learning_fade: {ok} checks passed")
