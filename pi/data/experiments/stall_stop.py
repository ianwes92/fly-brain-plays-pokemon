"""Is there a stop signal when the fly walks into something?

A walking bout has nothing that ends it when the fly stops making progress, so
it holds the direction key against a wall. Giving the bout hysteresis made that
worse: wall bumps went 191 -> 560 (t=+2.99), more than double per tile moved
(bout_hysteresis.jsonl).

A hard rule -- "stop after N ms of not moving" -- would be a mechanical
override, and in this fly the buttons come from descending neurons. The input
is already there: walking without moving drives the bristle mechanosensors at
150 Hz (Eyes.touch). Nothing reads them. So screen for descending neurons that
respond to that input, exactly as WALL_AHEAD and EXIT_AHEAD were screened, and
report DNp09 separately -- it is the known 'stop' descending neuron in flies,
so it is the one result here that can be checked against the literature rather
than only against itself.

Neurons chosen on discovery seeds 300-307, verdict on fresh seeds 400-407:
each kept only if it holds its direction, composite |t| >= 2.365.
"""
import os
import sys

sys.path.insert(0, "~/flybrain")
import numpy as np                                           # noqa: E402
from flybrain import ports                                   # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402
from flybrain.engine import Brain, Params                    # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
out = open(os.path.join(HERE, "stall_stop.log"), "w")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    out.write(s + "\n")
    out.flush()
    os.fsync(out.fileno())


cx = Connectome()
L = cx.labels
name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"])
desc = L["super_class"] == "descending"
touch = cx.select(**ports.SENSORY["touch"])
dn = np.flatnonzero(desc)
names = [f"{name[i] or '?'}_{L['side'][i][:1]}" for i in dn]
root = cx.root_id
dnp09 = np.flatnonzero(desc & np.char.startswith(name, "DNp09"))
HZ = 150.0                      # exactly what Eyes delivers while stalling


def trial(seed, hz, ms=500):
    b = Brain(cx, Params(), seed=seed)
    for _ in range(200):
        b.step()
    before = b.spikes_total.copy()
    for _ in range(ms):
        if hz:
            b.drive(touch, hz)
        b.step()
    return (b.spikes_total - before) / (ms / 1000)


def run(seeds):
    D, stop = [], []
    for s in seeds:
        on, off = trial(s, HZ), trial(s, 0.0)
        D.append(on[dn] - off[dn])
        stop.append(float(on[dnp09].mean() - off[dnp09].mean()))
    return np.array(D), np.array(stop)


log(f"touch input: {len(touch)} bristle neurons at {HZ:.0f} Hz; "
    f"{len(dn)} descending screened; DNp09 n={len(dnp09)}")

Dd, stop_d = run(range(300, 308))
m, sd = Dd.mean(0), Dd.std(0, ddof=1)
with np.errstate(divide="ignore", invalid="ignore"):
    t = np.where(sd > 0, m / (sd / np.sqrt(len(Dd))), 0.0)
ok = np.abs(m) >= 1.0
order = np.argsort(-np.abs(np.where(ok, t, 0.0)))[:10]
signs = np.sign(m[order])
log(f"\nDISCOVERY (seeds 300-307): {int((np.abs(t) >= 4).sum())} DNs with |t| >= 4; chosen 10:")
for j, sg in zip(order, signs):
    log(f"    {names[j]:16s} {m[j]:+7.2f} Hz  t={t[j]:+6.1f}  sign {int(sg):+d}")

Dv, stop_v = run(range(400, 408))
mv = Dv.mean(0)
held = [(j, sg) for j, sg in zip(order, signs) if np.sign(mv[j]) == sg]
log(f"\nVALIDATION (fresh seeds 400-407): {len(held)} of 10 held their direction")
for j, sg in zip(order, signs):
    log(f"    {names[j]:16s} discovery {m[j]:+7.2f}  fresh {mv[j]:+7.2f}  "
        f"{'kept' if np.sign(mv[j]) == sg else 'DROPPED'}")

if held:
    cols = [j for j, _ in held]
    sgn = np.array([sg for _, sg in held])
    comp = (Dv[:, cols] * sgn).sum(1)
    tc = comp.mean() / (comp.std(ddof=1) / np.sqrt(len(comp)))
    log(f"\nCOMPOSITE on fresh seeds: {comp.mean():+.1f} Hz, t={tc:+.2f}, "
        f"{int((comp > 0).sum())}/{len(comp)} seeds positive")
    log(f"VERDICT: {'USABLE' if abs(tc) >= 2.365 else 'NOT USABLE'} (needs |t| >= 2.365)")
    log("\nSTALL_READOUT = (")
    for j, sg in held:
        i = dn[j]
        log(f"    ({int(root[i])}, {int(sg):+d}),   # {name[i] or '?'} {L['side'][i]}")
    log(")")

log(f"\nDNp09, the known stop neuron, on touch input:")
log(f"    discovery {stop_d.mean():+.2f} +/- {stop_d.std(ddof=1):.2f} Hz, "
    f"fresh {stop_v.mean():+.2f} +/- {stop_v.std(ddof=1):.2f} Hz")
tp = stop_v.mean() / (stop_v.std(ddof=1) / np.sqrt(len(stop_v))) if stop_v.std(ddof=1) else float("nan")
log(f"    t on fresh seeds = {tp:+.2f}  "
    f"({'responds' if abs(tp) >= 2.365 else 'no clear response'})")
out.close()
