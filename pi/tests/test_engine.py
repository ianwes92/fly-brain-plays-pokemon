"""Engine sanity checks against hand-computable cases."""
import os
import sys

import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.engine import Brain, Params

class Fake:
    """Minimal stand-in for Connectome."""
    def __init__(self, n, indptr, indices, weights):
        self.n, self.indptr, self.indices, self.weights = n, indptr, indices, weights
        self.n_edges = len(indices)

def chain(n=5, w=100.0):
    # 0 -> 1 -> 2 -> ... strong enough that one spike drives the next
    indptr = np.arange(n + 1, dtype=np.int64); indptr[-1] = n - 1
    indptr = np.array([min(i, n - 1) for i in range(n + 1)], dtype=np.int64)
    indices = np.arange(1, n, dtype=np.int32)
    weights = np.full(n - 1, w, dtype=np.float32)
    return Fake(n, indptr, indices, weights)

ok = True
def check(name, cond, extra=""):
    global ok
    ok &= bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}")

print("1. silent brain stays silent")
b = Brain(chain(), Params(noise_mv=0.0))
check("no spikes in 500ms", b.run(500) == 0)
check("voltage pinned at rest", np.allclose(b.v, b.p.v_rest), f"v={b.v[0]:.3f}")

print("2. leak returns an displaced voltage to rest")
b = Brain(chain(), Params(adapt_mv=0.0))   # adaptation would pull below rest
b.v[0] = 0.0
b.run(200)   # 10 tau
check("decayed to rest", abs(b.v[0] - b.p.v_rest) < 0.01, f"v={b.v[0]:.4f}")

print("3. forced spike propagates down the chain")
# max_psp_mv off: this test needs one connection to fire the next neuron.
b = Brain(chain(n=5, w=100.0), Params(max_psp_mv=0.0))
b.force([0])
seen = []
for _ in range(60):
    s = b.step()
    if len(s): seen.append((round(b.t_ms, 1), s.tolist()))
check("neuron 0 fired first", seen and seen[0][1] == [0], f"{seen[:4]}")
check("activity reached the end", any(4 in s for _, s in seen), f"{len(seen)} events")
first = {}
for t, sp in seen:
    for i in sp:
        first.setdefault(i, t)
check("first spikes ordered down the chain",
      [first[i] for i in sorted(first)] == sorted(first.values()), f"{first}")

print("4. total PSP from one spike matches mv_per_synapse * syn_count")
p = Params(v_threshold=1e9)   # never fire, just watch the voltage
b = Brain(chain(n=2, w=10.0), p)
b.force([0]); b.step()
peak = 0.0
for _ in range(2000):
    b.step()
    peak = max(peak, b.v[1] - p.v_rest)
delivered = peak
# leak eats some of it, so expect less than the ideal but the right order
ideal = 10.0 * p.mv_per_synapse
check("peak PSP in range", 0.3 * ideal < delivered < ideal,
      f"peak {delivered:.3f} mV, total charge {ideal:.3f}")

print("5. inhibition is actually negative")
b = Brain(chain(n=2, w=-50.0), Params(v_threshold=1e9, max_psp_mv=0.0))
b.force([0]); b.run(50)
check("target hyperpolarised", b.v[1] < b.p.v_rest - 0.5, f"v={b.v[1]:.3f}")

print("6. refractory period is honoured")
p = Params(dt=0.5, refractory=2.2)
b = Brain(chain(n=2), p)
fired = []
for i in range(40):
    b.force([0])          # try to make it spike every single step
    if 0 in b.step(): fired.append(i)
gaps = np.diff(fired)
check("min gap >= refractory steps", gaps.min() >= int(round(2.2/0.5)),
      f"gaps={set(gaps.tolist())}, expect >= {int(round(2.2/0.5))}")

print("7. drive() produces roughly the requested rate")
b = Brain(chain(n=1000), Params(v_threshold=1e9))
idx = np.arange(1000)
target, ms = 20.0, 4000.0
n = 0
for _ in range(int(ms / b.p.dt)):
    b.drive(idx, target)
    n += len(b.step())
got = n / (1000 * ms / 1000.0)
t_ref = b._ref_steps * b.p.dt / 1000.0
expect = target / (1 + target * t_ref)      # refractory losses
check("rate matches Poisson-with-deadtime", abs(got - expect) / expect < 0.05,
      f"{got:.1f} Hz, expected {expect:.1f}")

print("8. very hard drive saturates at 1/refractory, not infinity")
b = Brain(chain(n=200), Params(v_threshold=1e9))
idx = np.arange(200)
n = 0
for _ in range(int(2000 / b.p.dt)):
    b.drive(idx, 1e5)
    n += len(b.step())
got = n / (200 * 2.0)
ceiling = 1.0 / (b._ref_steps * b.p.dt / 1000.0)
check("saturates at the ceiling", abs(got - ceiling) / ceiling < 0.02,
      f"{got:.0f} Hz, ceiling {ceiling:.0f} Hz")

print("9. no single connection can fire a neuron by itself")
p = Params()          # cap on, as shipped
b = Brain(chain(n=2, w=1000.0), p)   # absurdly strong connection
b.force([0])
fired_downstream = any(1 in b.step() for _ in range(400))
check("1000-synapse connection does not fire its target", not fired_downstream,
      f"capped at {p.max_psp_mv} mV vs {p.v_threshold - p.v_rest} mV to threshold")

print("10. adaptation suppresses sustained firing")
# Driven by injected current, not force(): forced spikes bypass the membrane
# entirely, so adaptation has nothing to act on there -- which is right for a
# sensory afferent, but makes them useless for testing this.
def sustained(adapt_mv):
    # dt pinned: inject() is per-step, so the current per millisecond would
    # otherwise change with the timestep and this tests adaptation, not dt.
    p = Params(dt=0.5, adapt_mv=adapt_mv, tau_adapt=150.0)
    b = Brain(chain(n=500), p)
    idx = np.arange(500)
    early = late = 0
    for _ in range(int(1500 / p.dt)):
        b.inject(idx, 0.35)
        n = len(b.step())
        if b.t_ms <= 100: early += n
        if b.t_ms > 1400: late += n
    return early, late

early, late = sustained(4.0)
check("firing declines as adaptation builds", late < early * 0.9,
      f"first 100ms {early}, last 100ms {late}")
early2, late2 = sustained(0.0)
check("without adaptation it holds steady", late2 > early2 * 0.9,
      f"first 100ms {early2}, last 100ms {late2}")

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
