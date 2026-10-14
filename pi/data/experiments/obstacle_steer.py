"""Does an obstacle on one side steer the fly?

Drive LPLC1 (collision-avoidance visual projection neurons) on the left eye
versus the right eye, and compare all descending neurons. Neurons are chosen
on discovery seeds; the verdict comes only from fresh validation seeds:
composite t >= 2.365 (two-sided p < 0.05, df = 7) in the discovered direction.
Also reports the turn index the game currently reads (DNp15 + DNg41).
"""
import os, sys
sys.path.insert(0, "~/flybrain")
import numpy as np
from flybrain.connectome import Connectome
from flybrain.engine import Brain, Params
HERE = os.path.dirname(os.path.abspath(__file__))
out = open(os.path.join(HERE, "obstacle_steer.log"), "w")
def log(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); out.write(s + "\n"); out.flush(); os.fsync(out.fileno())

cx = Connectome()
L = cx.labels; name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"])
lplc1 = {s: np.flatnonzero((name == "LPLC1") & (L["side"] == s)) for s in ("left", "right")}
dn = cx.select(super_class="descending")
sel = lambda **kw: cx.select(super_class="descending", **kw)
tl = np.concatenate([sel(hemibrain_type="DNp15*", side="left"), sel(cell_type="DNg41", side="left")])
tr = np.concatenate([sel(hemibrain_type="DNp15*", side="right"), sel(cell_type="DNg41", side="right")])
dn_names = [f"{name[i] or '?'}_{L['side'][i][:1]}" for i in dn]

def trial(seed, eye, ms=500, hz=60.0):
    b = Brain(cx, Params(), seed=seed)
    for _ in range(200): b.step()
    before = b.spikes_total.copy()
    for _ in range(ms):
        b.drive(lplc1[eye], hz); b.step()
    c = (b.spikes_total - before) / (ms / 1000)
    turn = (c[tl].sum() - c[tr].sum()) / (c[tl].sum() + c[tr].sum() + 1.0)
    return c[dn], turn

def run(seeds):
    D, T = [], []
    for s in seeds:
        l, tl_ = trial(s, "left"); r, tr_ = trial(s, "right")
        D.append(l - r); T.append(tl_ - tr_)
    return np.array(D), np.array(T)

log(f"LPLC1: {len(lplc1['left'])} left, {len(lplc1['right'])} right")
Dd, Td = run(range(100, 108))
m, sd = Dd.mean(0), Dd.std(0, ddof=1)
t = np.where(sd > 0, m / (sd / np.sqrt(len(Dd))), 0.0)
ok = np.abs(m) >= 1.0
K = 10
order = np.argsort(-np.abs(np.where(ok, t, 0.0)))[:K]
signs = np.sign(m[order])
log(f"\nDISCOVERY (seeds 100-107): left-eye minus right-eye LPLC1, per descending neuron")
log(f"  {int((np.abs(t) >= 4).sum())} DNs with |t| >= 4; chosen {K}:")
for j in order:
    log(f"    {dn_names[j]:14s} {m[j]:+7.2f} Hz  t={t[j]:+6.1f}")
log(f"  current game turn index (DNp15+DNg41), left minus right: {Td.mean():+.3f} ± {Td.std(ddof=1):.3f}")

Dv, Tv = run(range(200, 208))
comp = (Dv[:, order] * signs).mean(1)
ct = comp.mean() / (comp.std(ddof=1) / np.sqrt(len(comp)))
log(f"\nVALIDATION (fresh seeds 200-207)")
for j, v in zip(order, Dv[:, order].mean(0)):
    log(f"    {dn_names[j]:14s} discovery {m[j]:+7.2f}  validation {v:+7.2f}  {'same sign' if np.sign(v) == np.sign(m[j]) else 'FLIPPED'}")
verdict = "VALIDATED" if (ct >= 2.365 and comp.mean() > 0) else "NOT VALIDATED"
log(f"  composite {comp.mean():+.2f} ± {comp.std(ddof=1):.2f} Hz, t={ct:+.2f}, {int((comp>0).sum())}/8 positive -> {verdict}")
tt = Tv.mean() / (Tv.std(ddof=1) / np.sqrt(len(Tv))) if Tv.std(ddof=1) > 0 else 0
log(f"  current game turn index on validation: {Tv.mean():+.3f} ± {Tv.std(ddof=1):.3f} (t={tt:+.2f})")
np.save(os.path.join(HERE, "obstacle_steer_chosen.npy"), np.stack([dn[order], signs]))
