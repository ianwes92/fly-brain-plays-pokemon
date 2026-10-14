"""The learning rule on a network small enough to reason about by hand.

Two Kenyon cells, two MBONs, two dopamine neurons, wired so that DAN 0 teaches
MBON 0 and DAN 1 teaches MBON 1. The rule should depress a KC->MBON synapse
only when that KC fired recently AND that MBON's own dopamine rose above
baseline -- not for either alone, not in another compartment, and never when
frozen.
"""
import os
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.engine import Brain, Params
from flybrain.learning import LearningParams, MushroomBodyLearning

ok = True


def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}")


KC0, KC1, MB0, MB1, DAN0, DAN1 = range(6)
N = 6


class Fake:
    def __init__(self):
        self.n = N
        # KC0 -> MB0, MB1 ; KC1 -> MB0, MB1. Weak, so MBONs never matter here.
        pre = [KC0, KC0, KC1, KC1]
        post = [MB0, MB1, MB0, MB1]
        self.indptr = np.zeros(N + 1, dtype=np.int64)
        np.cumsum(np.bincount(pre, minlength=N), out=self.indptr[1:])
        self.indices = np.array(post, dtype=np.int32)
        self.weights = np.full(4, 2.0, dtype=np.float32)
        self.n_edges = 4
        cls = ["Kenyon_Cell", "Kenyon_Cell", "MBON", "MBON", "DAN", "DAN"]
        self.labels = {"class": np.array(cls)}


def fresh_raw(path):
    np.savez(path, da_pre=np.array([DAN0, DAN1], np.int32),
             da_post=np.array([MB0, MB1], np.int32),
             da_syn=np.array([50, 50], np.int32))


def session(raw, kc=(), dan=(), frozen=False, ms=1500, gap_ms=0):
    """Drive the given KCs, optionally wait, then the given DANs, together."""
    b = Brain(Fake(), Params(noise_mv=0.0, adapt_mv=0.0), seed=0)
    mb = MushroomBodyLearning(b, LearningParams(eta=2.0), raw=raw).attach()
    mb.frozen = frozen
    for _ in range(300):
        b.step()                               # settle the dopamine baseline
    for i in range(ms):
        if kc and i < 700:
            b.drive(list(kc), 80.0)
        if dan and i >= 500 + gap_ms and i < 1000 + gap_ms:
            b.drive(list(dan), 100.0)
        b.step()
    return mb


tmp = tempfile.mkdtemp()
raw = os.path.join(tmp, "learning_raw.npz")
fresh_raw(raw)


def strength(mb, kc, mbon):
    e = [k for k in range(len(mb.edges))
         if mb.kc[mb.edge_kc[k]] == kc and mb.mbon[mb.edge_mbon[k]] == mbon]
    return float(mb.m[e[0]])


print("1. coincidence depresses the paired synapse")
mb = session(raw, kc=[KC0], dan=[DAN0])
s = strength(mb, KC0, MB0)
check("KC0->MBON0 weakened", s < 0.8, f"strength {s:.2f}")

print("2. only in the compartment the dopamine reaches")
check("KC0->MBON1 untouched (DAN0 does not teach MBON1)",
      strength(mb, KC0, MB1) > 0.99, f"{strength(mb, KC0, MB1):.3f}")

print("3. only for Kenyon cells that were active")
check("KC1->MBON0 untouched (KC1 silent)", strength(mb, KC1, MB0) > 0.99,
      f"{strength(mb, KC1, MB0):.3f}")

print("4. neither half alone is enough")
m = session(raw, kc=[KC0])
check("KC activity without dopamine: no change", m.m.min() > 0.99, f"min {m.m.min():.3f}")
m = session(raw, dan=[DAN0])
check("dopamine without KC activity: no change", m.m.min() > 0.99, f"min {m.m.min():.3f}")

print("5. timing: dopamine long after the KC fired teaches much less")
late = session(raw, kc=[KC0], dan=[DAN0], gap_ms=4000, ms=6000)
check("4 s gap depresses far less than overlap", strength(late, KC0, MB0) > s + 0.1,
      f"{strength(late, KC0, MB0):.2f} vs {s:.2f}")

print("6. frozen means frozen")
m = session(raw, kc=[KC0], dan=[DAN0], frozen=True)
check("no change while frozen", m.m.min() > 0.999, f"min {m.m.min():.4f}")

print("7. weights reach the simulation, and save/load round-trips")
check("brain.w_mv reflects depression", abs(float(mb.brain.w_mv[mb.edges][0]) - float(mb.w0[0] * mb.m[0])) < 1e-5)
path = os.path.join(tmp, "mem.npz")
mb.save(path)
other = session(raw)
other.load(path)
check("loaded strengths match", np.allclose(other.m, mb.m))

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
