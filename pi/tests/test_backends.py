"""The C kernel must agree with the numpy path it replaces.

numpy is the reference: it is what the engine tests validate. This checks the
fused C step reproduces it spike for spike on the real connectome.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import ports
from flybrain.connectome import Connectome
from flybrain.engine import Brain, Params

ok = True


def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}")


cx = Connectome()
eye = cx.select(**ports.SENSORY["eye"])

bc = Brain(cx, Params(use_c=True), seed=7)
bn = Brain(cx, Params(use_c=False), seed=7)
check("C kernel is actually loaded", bc.backend == "C", f"backend={bc.backend}")
check("fallback really is numpy", bn.backend == "numpy")

print("\n1. identical spike trains under identical drive (same seed)")
mismatch = None
for i in range(1200):
    bc.drive(eye, 120.0)
    bn.drive(eye, 120.0)
    sc = np.asarray(bc.step(), dtype=np.int64)
    sn = np.asarray(bn.step(), dtype=np.int64)
    if sc.size != sn.size or not np.array_equal(np.sort(sc), np.sort(sn)):
        mismatch = (i, sc.size, sn.size)
        break
check("600 ms of spikes match exactly", mismatch is None,
      f"first divergence at step {mismatch}" if mismatch else "1200 steps")

print("\n2. state agrees at the end")
check("total spike counts equal",
      int(bc.spikes_total.sum()) == int(bn.spikes_total.sum()),
      f"C {int(bc.spikes_total.sum()):,} vs numpy {int(bn.spikes_total.sum()):,}")
for name, a, b in (("v", bc.v, bn.v), ("g", bc.g, bn.g), ("adapt", bc.adapt, bn.adapt)):
    d = float(np.abs(a - b).max())
    check(f"{name} within float tolerance", d < 1e-3, f"max|diff|={d:.2e}")
check("refrac identical", np.array_equal(bc.refrac, bn.refrac))
check("scratch flags left clean", int(bc._flag.sum()) == 0)

print("\n3. rates agree")
d = float(np.abs(bc.rate - bn.rate).max())
check("firing rates within tolerance", d < 1e-2, f"max|diff|={d:.2e} Hz")
for g in ("descending", "dn_left"):
    idx = cx.select(**ports.MOTOR[g])
    rc, rn = bc.population_rate(idx), bn.population_rate(idx)
    check(f"{g} rate matches", abs(rc - rn) < 1e-2, f"{rc:.4f} vs {rn:.4f} Hz")

print("\n4. step() returns a reused buffer on the C path")
# Documented contract, not a defect: the C kernel writes spikes into a
# preallocated array and hands back a view for speed. Anything that keeps
# spikes past the next step has to copy them. viz.BrainView.add_spikes does.
b = Brain(cx, Params(use_c=True), seed=3)
for _ in range(50):
    b.drive(eye, 150.0); b.step()
a1 = b.step(); b.drive(eye, 150.0); a2 = b.step()
check("consecutive results share the buffer", np.shares_memory(a1, a2))
from flybrain import viz
v = viz.BrainView.__new__(viz.BrainView)
v._pending = []
b2 = Brain(cx, Params(use_c=True), seed=3)
for _ in range(50):
    b2.drive(eye, 150.0); b2.step()
for _ in range(4):
    b2.drive(eye, 150.0)
    viz.BrainView.add_spikes(v, b2.step())
check("BrainView.add_spikes copies out of it",
      not np.shares_memory(v._pending[0], v._pending[-1]),
      f"{len(v._pending)} frames queued")

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
