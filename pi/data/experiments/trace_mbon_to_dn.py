"""Why doesn't mushroom body memory reach the descending neurons?

Walks outward from the punishment-taught MBONs through the connectome, hop by
hop, scoring influence as the fraction of each downstream neuron's input it
supplies (product along paths). Compares against random central-brain neurons
as a baseline, and reports where the influence lands at each hop.
"""
import os, sys, json
sys.path.insert(0, "~/flybrain")
import numpy as np
from flybrain.connectome import Connectome
from flybrain import ports

HERE = os.path.dirname(os.path.abspath(__file__))
out = open(os.path.join(HERE, "trace_mbon_to_dn.log"), "w")
def log(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); out.write(s + "\n"); out.flush()

cx = Connectome()
L = cx.labels
ct, hb, cl, sc = L["cell_type"], L["hemibrain_type"], L["class"], L["super_class"]
name = np.where(ct != "", ct, hb)
n = cx.n
absw = np.abs(cx.weights).astype(np.float64)
in_tot = np.bincount(cx.indices, weights=absw, minlength=n)
src_of_edge = np.repeat(np.arange(n), np.diff(cx.indptr))

def step(x):
    """One hop: influence x on sources -> fraction-of-input influence on targets."""
    nz = np.flatnonzero(x)
    if not len(nz):
        return np.zeros(n)
    starts, ends = cx.indptr[nz], cx.indptr[nz + 1]
    counts = ends - starts
    k = np.repeat(starts, counts) + (np.arange(counts.sum()) - np.repeat(np.cumsum(counts) - counts, counts))
    contrib = x[src_of_edge[k]] * absw[k] / np.maximum(in_tot[cx.indices[k]], 1)
    return np.bincount(cx.indices[k], weights=contrib, minlength=n)

z = np.load("data/learning_raw.npz")
mbon = np.flatnonzero(cl == "MBON"); dan = np.flatnonzero(cl == "DAN")
ppl1 = np.flatnonzero((cl == "DAN") & (np.char.startswith(ct, "PPL1") | np.char.startswith(hb, "PPL1")))
D = np.zeros((len(mbon), len(dan)))
pos_m = {int(i): j for j, i in enumerate(mbon)}; pos_d = {int(i): j for j, i in enumerate(dan)}
for a, b, s in zip(z["da_pre"], z["da_post"], z["da_syn"]):
    if int(b) in pos_m:
        D[pos_m[int(b)], pos_d[int(a)]] += s
share = D[:, np.isin(dan, ppl1)].sum(1) / np.maximum(D.sum(1), 1)
punished = mbon[share > 0.5]

dn = cx.select(**ports.MOTOR["descending"])
readout = {
    "DNp15/DNg41 (turn)": np.concatenate([cx.select(super_class="descending", hemibrain_type="DNp15*"),
                                          cx.select(super_class="descending", cell_type="DNg41")]),
    "MDN (backward)": cx.select(super_class="descending", hemibrain_type="MDN*"),
}

# --- 1. where do punishment-taught MBONs send their synapses? --------------------
log(f"punishment-taught MBONs: {len(punished)} of {len(mbon)}")
k = np.concatenate([np.arange(cx.indptr[i], cx.indptr[i + 1]) for i in punished])
tgt = cx.indices[k]
by = {}
for c, w in zip(np.where(cl[tgt] != "", cl[tgt], sc[tgt]), absw[k]):
    by[c] = by.get(c, 0) + w
tot = sum(by.values())
log("\n1. output synapses of punishment-taught MBONs, by target class:")
for c, w in sorted(by.items(), key=lambda t: -t[1])[:10]:
    log(f"   {c:22s} {w:8.0f}  ({100*w/tot:4.1f}%)")
direct_dn = absw[k][np.isin(tgt, dn)].sum()
log(f"   -> directly onto descending neurons: {direct_dn:.0f} synapses ({100*direct_dn/tot:.1f}%)")

m11 = [i for i in punished if name[i] == "MBON11"]
k11 = np.concatenate([np.arange(cx.indptr[i], cx.indptr[i + 1]) for i in m11])
t11 = {}
for t, w in zip(cx.indices[k11], absw[k11]):
    key = f"{cl[t] or sc[t]}:{name[t] or '?'}"
    t11[key] = t11.get(key, 0) + w
s11 = sum(t11.values())
log(f"\n   MBON11 (both sides) top targets, of {s11:.0f} output synapses:")
for key, w in sorted(t11.items(), key=lambda t: -t[1])[:8]:
    log(f"     {key:34s} {w:6.0f} ({100*w/s11:4.1f}%)")

# --- 2. hop-by-hop influence on descending neurons vs a random baseline -----------
rng = np.random.default_rng(0)
central = np.flatnonzero((sc == "central") & (cl != "MBON") & (cl != "Kenyon_Cell") & (np.diff(cx.indptr) > 0))
def walk(sources, hops=5):
    x = np.zeros(n); x[sources] = 1.0 / len(sources)
    rows = []
    for h in range(1, hops + 1):
        x = step(x)
        rows.append({"hop": h, "dn": x[dn].sum(), **{lab: x[idx].sum() for lab, idx in readout.items()},
                     "top_dn": [(name[i] or "?", float(x[i])) for i in dn[np.argsort(x[dn])[::-1][:4]]]})
    return rows

log("\n2. influence reaching descending neurons, hop by hop "
    "(per unit of source activity; fraction-of-input products)")
p_rows = walk(punished)
base = [walk(rng.choice(central, size=len(punished), replace=False)) for _ in range(20)]
log(f"   {'hop':>3} {'punished MBONs->all DNs':>24} {'random central->all DNs':>26} {'ratio':>6}   "
    f"{'->turn DNs':>10} {'->MDN':>9}")
for h in range(5):
    b = np.array([r[h]["dn"] for r in base])
    log(f"   {h+1:>3} {p_rows[h]['dn']:24.2e} {b.mean():15.2e} (p90 {np.percentile(b,90):.1e}) {p_rows[h]['dn']/b.mean():6.2f}   "
        f"{p_rows[h]['DNp15/DNg41 (turn)']:10.2e} {p_rows[h]['MDN (backward)']:9.2e}")
for h in (1, 2, 3):
    log(f"   hop {h} strongest DN targets: " + ", ".join(f"{a} {v:.1e}" for a, v in p_rows[h - 1]["top_dn"]))

# --- 3. how far is each DN, in hops, from any punished MBON? ----------------------
reach = np.full(n, 99, dtype=int); frontier = np.zeros(n, bool); frontier[punished] = True; reach[punished] = 0
for h in range(1, 6):
    x = step(frontier.astype(float)) > 0
    new = x & (reach == 99)
    reach[new] = h; frontier = new
    if not new.any(): break
u, c = np.unique(reach[dn], return_counts=True)
log("\n3. shortest path (hops) from a punishment-taught MBON to each descending neuron:")
log("   " + ", ".join(f"{h} hops: {k}" for h, k in zip(u, c)))
for lab, idx in readout.items():
    log(f"   {lab}: {sorted(set(reach[idx].tolist()))} hops")
