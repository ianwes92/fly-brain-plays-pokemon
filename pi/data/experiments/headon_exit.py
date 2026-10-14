"""What does a door straight ahead do? Both eyes' LPLC4 at head-on strength vs none.

The approach lock needs a signal for 'a way out, straight in front'. The first
version reused the left-minus-right exit readout's population mean, which was a
shortcut and separated badly in play (door ahead 42 Hz vs not 31 Hz, tails
overlapping) -- the lock engaged 1-22% of frames and its A/B came out null
(door_finding.jsonl). This screens for neurons that actually respond to
bilateral LPLC4 drive, the same way WALL_AHEAD was found (headon_obstacle.py).

Head-on view strength: a door straight ahead adds 1/(1+d) to BOTH eyes, so at
three tiles each eye sees 1 - exp(-0.25) = 0.22, times the 150 Hz exit channel
and a mid-range urge gain of 0.625 -> about 21 Hz per eye. Screened at that.

Neurons chosen on discovery seeds 300-307; verdict on fresh seeds 400-407.
A neuron is kept only if the effect holds in the discovered direction on the
fresh seeds, and the composite needs |t| >= 2.365 (two-sided p < 0.05, df=7).
Also reports what the LATERAL exit readout does under this symmetric drive --
it should stay near zero, which is the other half of the lock's condition.
"""
import os
import sys

sys.path.insert(0, "~/flybrain")
import numpy as np                                           # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402
from flybrain.engine import Brain, Params                    # noqa: E402
from flybrain.game import EXITS_READOUT, _by_root            # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
out = open(os.path.join(HERE, "headon_exit.log"), "w")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    out.write(s + "\n")
    out.flush()
    os.fsync(out.fileno())


cx = Connectome()
L = cx.labels
name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"])
side = L["side"]
desc = L["super_class"] == "descending"
lplc4_l = np.flatnonzero((name == "LPLC4") & (side == "left"))
lplc4_r = np.flatnonzero((name == "LPLC4") & (side == "right"))
dn = np.flatnonzero(desc)
names = [f"{name[i] or '?'}_{side[i][:1]}" for i in dn]
root = cx.root_id

exit_idx, exit_sign = _by_root(cx, EXITS_READOUT)
HZ = 150.0 * (1.0 - np.exp(-0.25)) * 0.625


def trial(seed, hz, ms=500):
    b = Brain(cx, Params(), seed=seed)
    for _ in range(200):
        b.step()
    before = b.spikes_total.copy()
    for _ in range(ms):
        if hz:
            b.drive(lplc4_l, hz)
            b.drive(lplc4_r, hz)
        b.step()
    return (b.spikes_total - before) / (ms / 1000)


def run(seeds):
    D, lateral = [], []
    for s in seeds:
        on, off = trial(s, HZ), trial(s, 0.0)
        D.append(on[dn] - off[dn])
        lateral.append(float(((on[exit_idx] - off[exit_idx]) * exit_sign).sum()))
    return np.array(D), np.array(lateral)


log(f"LPLC4 {len(lplc4_l)} left + {len(lplc4_r)} right, both eyes at {HZ:.1f} Hz; "
    f"{len(dn)} descending neurons screened")

Dd, lat_d = run(range(300, 308))
m, sd = Dd.mean(0), Dd.std(0, ddof=1)
with np.errstate(divide="ignore", invalid="ignore"):
    t = np.where(sd > 0, m / (sd / np.sqrt(len(Dd))), 0.0)
ok = np.abs(m) >= 1.0                      # ignore neurons that barely move
order = np.argsort(-np.abs(np.where(ok, t, 0.0)))[:10]
signs = np.sign(m[order])
log(f"\nDISCOVERY (seeds 300-307): {int((np.abs(t) >= 4).sum())} DNs with |t| >= 4; chosen 10:")
for j, sg in zip(order, signs):
    log(f"    {names[j]:16s} {m[j]:+7.2f} Hz  t={t[j]:+6.1f}  sign {int(sg):+d}")

Dv, lat_v = run(range(400, 408))
mv = Dv.mean(0)
held = [(j, sg) for j, sg in zip(order, signs) if np.sign(mv[j]) == sg]
log(f"\nVALIDATION (fresh seeds 400-407): {len(held)} of 10 held their direction")
for j, sg in zip(order, signs):
    mark = "kept" if np.sign(mv[j]) == sg else "DROPPED"
    log(f"    {names[j]:16s} discovery {m[j]:+7.2f}  fresh {mv[j]:+7.2f}  {mark}")

if held:
    idx = np.array([dn[j] for j, _ in held])
    sgn = np.array([sg for _, sg in held])
    comp = (Dv[:, [j for j, _ in held]] * sgn).sum(1)
    tc = comp.mean() / (comp.std(ddof=1) / np.sqrt(len(comp)))
    log(f"\nCOMPOSITE on fresh seeds: {comp.mean():+.1f} Hz, t={tc:+.2f}, "
        f"{int((comp > 0).sum())}/{len(comp)} seeds positive")
    log(f"VERDICT: {'USABLE' if abs(tc) >= 2.365 else 'NOT USABLE'} "
        f"(needs |t| >= 2.365)")
    log("\nEXIT_AHEAD_READOUT = (")
    for i, sg in zip(idx, sgn):
        log(f"    ({int(root[i])}, {int(sg):+d}),   # {name[i] or '?'} {side[i]}")
    log(")")
    np.save(os.path.join(HERE, "headon_exit_chosen.npy"), np.array([idx, sgn]))

log(f"\nLATERAL exit readout under symmetric drive (should stay near zero):")
log(f"    discovery {lat_d.mean():+.1f} +/- {lat_d.std(ddof=1):.1f} Hz, "
    f"fresh {lat_v.mean():+.1f} +/- {lat_v.std(ddof=1):.1f} Hz")
out.close()
