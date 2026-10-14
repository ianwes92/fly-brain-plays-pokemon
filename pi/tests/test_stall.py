"""A walking bout must end when the fly is getting nowhere.

Before this, a bout ended only when descending drive fell away, so the fly held
the direction key against a wall for as long as the drive lasted. Giving the
bout hysteresis made it worse: wall bumps per tile moved went 0.117 -> 0.265
(t=+2.99, bout_hysteresis.jsonl). The bristle mechanosensors already report the
stall; STALL_READOUT reads it out.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.connectome import Connectome                    # noqa: E402
from flybrain.game import Hands, STALL_READOUT                # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


class StubBrain:
    def __init__(self, n):
        self.rate = np.zeros(n, dtype=np.float32)


cx = Connectome()
n = len(cx.root_id)
h = Hands(cx)
b = StubBrain(n)

print("1. the readout resolves and is read")
check("ten neurons pinned", len(STALL_READOUT) == 10, f"{len(STALL_READOUT)}")
check("all resolve in the connectome", len(h.stall_idx) == 10, f"{len(h.stall_idx)}")
b.rate[h.stall_idx] = 100.0
h.read(b)
expected = float((np.full(10, 100.0) * h.stall_sign).sum())
check("stall is the signed sum", abs(h.stall - expected) < 1e-3, f"{h.stall:.0f} Hz")

print("2. a stall ends the bout, but not before the minimum bout length")
h.stall_thresh = 500.0
h.walk = 10.0                      # drive still high: only the stall can end it
h.start_thresh, h.stop_thresh = 1.0, 0.5
h.bout, h.bout_since = True, 0.0
h.force_walk = False
h.decide(h.min_bout_ms / 2)        # too early
check("bout survives inside the minimum length", h.bout)
h.decide(h.min_bout_ms + 100.0)
check("bout ends once stuck", not h.bout)
check("counted as a stall", h.stats.get("stalled", 0) == 1, f"{h.stats.get('stalled', 0)}")

print("3. with nothing stuck the bout continues")
h.bout, h.bout_since = True, 0.0
b.rate[h.stall_idx] = 0.0
h.read(b)
h.walk = 10.0
h.decide(h.min_bout_ms + 200.0)
check("bout continues while moving", h.bout, f"stall {h.stall:.0f} Hz")

print("4. an uncalibrated threshold leaves the old behaviour alone")
h2 = Hands(cx)
check("starts disabled", h2.stall_thresh > 1e8, f"{h2.stall_thresh:.0g}")
b.rate[h2.stall_idx] = 1000.0
h2.read(b)
h2.walk, h2.start_thresh, h2.stop_thresh = 10.0, 1.0, 0.5
h2.bout, h2.bout_since = True, 0.0
h2.decide(h2.min_bout_ms + 100.0)
check("no stall stop when uncalibrated", h2.bout, f"stall {h2.stall:.0f} Hz")

print("5. drive running out still ends a bout, and is not called a stall")
h3 = Hands(cx)
h3.stall_thresh = 500.0
b.rate[h3.stall_idx] = 0.0
h3.read(b)
h3.walk, h3.start_thresh, h3.stop_thresh = 0.1, 1.0, 0.5
h3.bout, h3.bout_since = True, 0.0
h3.decide(h3.min_bout_ms + 100.0)
check("bout ends on low drive", not h3.bout)
check("not counted as a stall", h3.stats.get("stalled", 0) == 0, f"{h3.stats.get('stalled', 0)}")

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
