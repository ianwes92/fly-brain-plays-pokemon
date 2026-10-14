"""Learning ledger: depression booked to the source of the burst; recovery
shrinks every source in proportion; persisted with the weights; old saves get
'before_ledger'; the game passes the source through."""
import os, sys, tempfile
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.learning import MushroomBodyLearning

ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1

L = MushroomBodyLearning.__new__(MushroomBodyLearning)      # just the bookkeeping
L.m = np.ones(10, dtype=np.float32)
L.credit = "human"
L.ledger = {"outstanding": {}, "depressed": {}, "recovered": 0.0}
L._book(2.0, 0.0)
L.credit = "rule"
L._book(1.0, 0.0)
check("booked by source", L.ledger["depressed"] == {"human": 2.0, "rule": 1.0})
L._book(0.0, 1.5)                                   # half of the 3.0 recovers
check("recovery in proportion", abs(L.ledger["outstanding"]["human"] - 1.0) < 1e-9
      and abs(L.ledger["outstanding"]["rule"] - 0.5) < 1e-9)
check("totals keep history", L.ledger["depressed"]["human"] == 2.0 and L.ledger["recovered"] == 1.5)
L._book(0.0, 99.0)
check("never negative", all(v >= 0 for v in L.ledger["outstanding"].values()))

class Z:
    files = ["m"]
L.m[:3] = 0.5
L._ledger_from(Z())
check("old save -> before_ledger", abs(L.ledger["outstanding"]["before_ledger"] - 1.5) < 1e-6)
L.m[:] = 1.0
L._ledger_from(Z())
check("fresh fly -> empty ledger", L.ledger["outstanding"] == {})
L.ledger = {"outstanding": {"human": 0.7}, "depressed": {"human": 0.9}, "recovered": 0.2}
path = os.path.join(tempfile.mkdtemp(), "x.npz")
np.savez_compressed(path, m=L.m, ledger=L._ledger_json())
z = np.load(path)
L.ledger = None
L._ledger_from(z)
check("round trip", L.ledger == {"outstanding": {"human": 0.7}, "depressed": {"human": 0.9}, "recovered": 0.2})
f1 = L.fingerprint(); L.m[0] = 0.9; f2 = L.fingerprint(); L.m[0] = 1.00001
check("fingerprint tracks the state, ignores noise", f1 != f2 and L.fingerprint() == f1)

from flybrain.game import Game
g = Game.__new__(Game)
g._stim = None
g.stimulate("pain", "rule")
check("game queues the source", g._stim == ("pain", "rule"))
g.stimulate("pleasure")
check("default source is human", g._stim == ("pleasure", "human"))
print(f"test_learning_ledger: {ok} checks passed")
