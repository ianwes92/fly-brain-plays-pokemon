"""Which visual channels can carry 'person' and 'exit' separately from 'wall'?

For each candidate lobula columnar type: drive left eye vs right eye and record
all descending neurons. A usable channel needs (1) a strong, consistent
left/right difference and (2) a pattern unlike LPLC1's (walls), so the brain's
output can tell them apart. Screen on seeds 500-503; confirm the two picks on
fresh seeds 600-607 with composite t >= 2.365 (df = 7), neurons chosen on the
screening seeds only.
"""
import os, sys, json
sys.path.insert(0, "~/flybrain")
import numpy as np
from flybrain.connectome import Connectome
from flybrain.engine import Brain, Params
HERE = os.path.dirname(os.path.abspath(__file__))
out = open(os.path.join(HERE, "channel_screen.log"), "w")
def log(*a):
    s = " ".join(str(x) for x in a); print(s, flush=True); out.write(s + "\n"); out.flush(); os.fsync(out.fileno())

cx = Connectome(); L = cx.labels
name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"])
side = L["side"]; dn = np.flatnonzero(L["super_class"] == "descending")
dn_names = [f"{name[i] or '?'}_{side[i][:1]}" for i in dn]
def eye(t, s):
    m = np.char.startswith(name, t) if t.endswith("*") is False and t in ("LC10",) else (name == t)
    if t == "LC10": m = np.char.startswith(name, "LC10")
    return np.flatnonzero(m & (side == s))
CANDIDATES = ["LPLC1", "LC10", "LC11", "LC15", "LC9", "LC12", "LC17", "LC18", "LC21", "LC6", "LC16", "LPLC4", "LC4", "LPLC2"]

def diff(t, seed, ms=400, hz=60.0):
    rows = []
    for s in ("left", "right"):
        b = Brain(cx, Params(), seed=seed)
        for _ in range(150): b.step()
        before = b.spikes_total.copy()
        idx = eye(t, s)
        for _ in range(ms):
            b.drive(idx, hz); b.step()
        rows.append((b.spikes_total - before)[dn] / (ms / 1000))
    return rows[0] - rows[1]

screen = {}
for t in CANDIDATES:
    D = np.array([diff(t, s) for s in range(500, 504)])
    m, sd = D.mean(0), D.std(0, ddof=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        tt = np.where(sd > 0, m / (sd / np.sqrt(len(D))), 0.0)
    ok = np.abs(m) >= 1.0
    order = np.argsort(-np.abs(np.where(ok, tt, 0.0)))[:10]
    screen[t] = dict(mean=m, order=order, signs=np.sign(m[order]),
                     strength=float(np.abs(m[order]).mean()), n_strong=int((np.abs(tt) >= 4).sum()),
                     n=len(eye(t, "left")) + len(eye(t, "right")))
ref = screen["LPLC1"]["mean"]
log(f"{'type':7s} {'neurons':>7} {'DNs |t|>=4':>10} {'top-10 |L-R| Hz':>15} {'similarity to walls':>20}")
for t, r in screen.items():
    sim = float(np.corrcoef(r["mean"], ref)[0, 1]) if t != "LPLC1" else 1.0
    r["sim"] = sim
    log(f"{t:7s} {r['n']:7d} {r['n_strong']:10d} {r['strength']:15.1f} {sim:+20.2f}")

# pick two: strongest channels whose pattern is least like walls (|similarity| < 0.3)
pool = [t for t in CANDIDATES if t != "LPLC1" and abs(screen[t]["sim"]) < 0.3 and screen[t]["n_strong"] >= 5]
pool.sort(key=lambda t: -screen[t]["strength"])
picks = pool[:2]
log(f"\ncandidates unlike walls and clearly lateral: {pool}")
log(f"picked (strongest two): {picks}")
if len(picks) >= 2:
    sim_pair = float(np.corrcoef(screen[picks[0]]["mean"], screen[picks[1]]["mean"])[0, 1])
    log(f"similarity between the two picks: {sim_pair:+.2f} (want low, so person and exit are distinguishable too)")

log("\nCONFIRMATION on fresh seeds 600-607")
result = {}
for t in picks:
    r = screen[t]
    comps = np.array([(diff(t, s)[r["order"]] * r["signs"]).mean() for s in range(600, 608)])
    ct = comps.mean() / (comps.std(ddof=1) / np.sqrt(len(comps)))
    v = "VALIDATED" if ct >= 2.365 and comps.mean() > 0 else "NOT VALIDATED"
    log(f"  {t}: composite {comps.mean():+.2f} ± {comps.std(ddof=1):.2f} Hz, t={ct:+.2f}, {int((comps>0).sum())}/8 -> {v}")
    log(f"     neurons: " + ", ".join(f"{dn_names[j]}{'+' if s > 0 else '-'}" for j, s in zip(r["order"], r["signs"])))
    result[t] = dict(verdict=v, neurons=[int(dn[j]) for j in r["order"]], signs=[int(s) for s in r["signs"]])
json.dump(result, open(os.path.join(HERE, "channel_screen.json"), "w"), indent=1)
