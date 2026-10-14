"""What does a wall straight ahead do? Both eyes' LPLC1 at head-on strength vs none.

Head-on view strength = (1.0 + 0.5) / 2.6 = 0.58 per eye -> 35 Hz each.
Neurons chosen on discovery seeds 300-307; verdict on fresh seeds 400-407:
composite t >= 2.365 (two-sided p < 0.05, df = 7) in the discovered direction.
Also reports MDN (backward), DNp09 (stop) and the left/right obstacle readout.
"""
import os, sys
sys.path.insert(0, "~/flybrain")
import numpy as np
from flybrain.connectome import Connectome
from flybrain.engine import Brain, Params
HERE = os.path.dirname(os.path.abspath(__file__))
out = open(os.path.join(HERE, "headon_obstacle.log"), "w")
def log(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); out.write(s + "\n"); out.flush(); os.fsync(out.fileno())

cx = Connectome(); L = cx.labels
name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"])
side = L["side"]; desc = L["super_class"] == "descending"
lplc1 = np.flatnonzero(name == "LPLC1")
dn = np.flatnonzero(desc)
names = [f"{name[i] or '?'}_{side[i][:1]}" for i in dn]
pick = lambda t, s=None: np.flatnonzero(desc & np.char.startswith(name, t) & ((side == s) if s else True))
mdn, dnp09 = pick("MDN"), pick("DNp09")
ob_plus = np.concatenate([pick("DNp35", "left"), pick("DNp18", "right")])
ob_minus = np.concatenate([pick("DNp35", "right"), pick("DNp18", "left")])
HZ = 60.0 * (1.0 + 0.5) / 2.6

def trial(seed, hz, ms=500):
    b = Brain(cx, Params(), seed=seed)
    for _ in range(200): b.step()
    before = b.spikes_total.copy()
    for _ in range(ms):
        if hz: b.drive(lplc1, hz)
        b.step()
    return (b.spikes_total - before) / (ms / 1000)

def run(seeds):
    D, extra = [], []
    for s in seeds:
        on, off = trial(s, HZ), trial(s, 0.0)
        D.append(on[dn] - off[dn])
        extra.append((on[mdn].mean() - off[mdn].mean(), on[dnp09].mean() - off[dnp09].mean(),
                      on[ob_plus].sum() - on[ob_minus].sum(), on[dn].mean() - off[dn].mean()))
    return np.array(D), np.array(extra)

log(f"LPLC1 {len(lplc1)} neurons, both eyes at {HZ:.1f} Hz; MDN {len(mdn)}, DNp09 {len(dnp09)}")
Dd, Ed = run(range(300, 308))
m, sd = Dd.mean(0), Dd.std(0, ddof=1)
with np.errstate(divide="ignore", invalid="ignore"):
    t = np.where(sd > 0, m / (sd / np.sqrt(len(Dd))), 0.0)
ok = np.abs(m) >= 1.0
order = np.argsort(-np.abs(np.where(ok, t, 0.0)))[:10]
signs = np.sign(m[order])
log(f"\nDISCOVERY (seeds 300-307): {int((np.abs(t) >= 4).sum())} DNs with |t| >= 4; chosen 10:")
for j in order:
    log(f"    {names[j]:14s} {m[j]:+7.2f} Hz  t={t[j]:+6.1f}")
def summarize(E, label):
    for k, lab in enumerate(("MDN (backward) change", "DNp09 (stop) change", "left/right obstacle readout", "mean DN change")):
        v = E[:, k]; tt = v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if v.std(ddof=1) > 0 else 0
        log(f"    {label} {lab:28s} {v.mean():+8.2f} ± {v.std(ddof=1):6.2f} Hz  t={tt:+5.1f}")
summarize(Ed, "discovery ")

Dv, Ev = run(range(400, 408))
comp = (Dv[:, order] * signs).mean(1)
ct = comp.mean() / (comp.std(ddof=1) / np.sqrt(len(comp)))
log(f"\nVALIDATION (fresh seeds 400-407)")
for j, v in zip(order, Dv[:, order].mean(0)):
    log(f"    {names[j]:14s} discovery {m[j]:+7.2f}  validation {v:+7.2f}  {'same sign' if np.sign(v) == np.sign(m[j]) else 'FLIPPED'}")
verdict = "VALIDATED" if (ct >= 2.365 and comp.mean() > 0) else "NOT VALIDATED"
log(f"  composite {comp.mean():+.2f} ± {comp.std(ddof=1):.2f} Hz, t={ct:+.2f}, {int((comp>0).sum())}/8 -> {verdict}")
summarize(Ev, "validation")
np.save(os.path.join(HERE, "headon_chosen.npy"), np.stack([dn[order], signs]))
