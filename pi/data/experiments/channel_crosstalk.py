"""Crosstalk: does stimulating one channel move the other channels' readouts?

Readouts (left-right indices, Hz): walls = the 10 validated LPLC1 neurons (this
replaced the 4-neuron DNp35/DNp18 readout, which crosstalk showed was too weak); people = the 10 validated LC10 neurons; exits = the 10 validated LPLC4
neurons (signed sums). Each channel is driven on the left eye; a readout should
move strongly for its own channel and little for the others. Seeds 700-705.
"""
import os, sys, json
sys.path.insert(0, "~/flybrain")
import numpy as np
from flybrain.connectome import Connectome
from flybrain.engine import Brain, Params
HERE = os.path.dirname(os.path.abspath(__file__))
cx = Connectome(); L = cx.labels
name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"]); side = L["side"]
desc = L["super_class"] == "descending"
pick = lambda t, s: np.flatnonzero(desc & (name == t) & (side == s))
chosen = json.load(open(os.path.join(HERE, "channel_screen.json")))
walls10 = np.load(os.path.join(HERE, "obstacle_steer_chosen.npy"))
readouts = {
    "walls":  (walls10[0].astype(int), walls10[1].astype(int)),     # the 10 validated LPLC1 neurons
    "people": (np.array(chosen["LC10"]["neurons"]), np.array(chosen["LC10"]["signs"])),
    "exits":  (np.array(chosen["LPLC4"]["neurons"]), np.array(chosen["LPLC4"]["signs"])),
}
channels = {"walls": "LPLC1", "people": "LC10", "exits": "LPLC4"}
def eye(t, s):
    m = np.char.startswith(name, "LC10") if t == "LC10" else (name == t)
    return np.flatnonzero(m & (side == s))
def index(rates, key):
    idx, sg = readouts[key]; return float((rates[idx] * sg).sum())
res = {c: {r: [] for r in readouts} for c in channels}
# Resumable: one JSON line per (seed, channel), fsync'd, because this Pi keeps
# losing power and the first rerun died with nothing written.
CK = os.path.join(HERE, "channel_crosstalk.jsonl")
done = {}
if os.path.exists(CK):
    for line in open(CK):
        line = line.strip().strip("\x00")
        if line:
            r = json.loads(line); done[(r["seed"], r["channel"])] = r["values"]
for seed in range(700, 706):
    for c, t in channels.items():
        if (seed, c) not in done:
            vals = []
            for s_ in ("left", "right"):
                b = Brain(cx, Params(), seed=seed)
                for _ in range(150): b.step()
                before = b.spikes_total.copy()
                for _ in range(400):
                    b.drive(eye(t, s_), 60.0); b.step()
                vals.append((b.spikes_total - before) / 0.4)
            values = {r: index(vals[0], r) - index(vals[1], r) for r in readouts}
            with open(CK, "a") as f:
                f.write(json.dumps({"seed": seed, "channel": c, "values": values}) + "\n")
                f.flush(); os.fsync(f.fileno())
            done[(seed, c)] = values
            print(f"  seed {seed} {c}: " + ", ".join(f"{r} {v:+.0f}" for r, v in values.items()), flush=True)
        for r in readouts:
            res[c][r].append(done[(seed, c)][r])
print(f"{'driven channel':16s}" + "".join(f"{'-> ' + r + ' readout':>22s}" for r in readouts))
for c in channels:
    cells = []
    for r in readouts:
        v = np.array(res[c][r]); cells.append(f"{v.mean():+9.1f} ± {v.std(ddof=1):5.1f}")
    print(f"{c + ' (' + channels[c] + ')':16s}" + "".join(f"{x:>22s}" for x in cells))
print("\nselectivity (own readout / largest other readout, by magnitude):")
for c in channels:
    own = abs(np.mean(res[c][c])); other = max(abs(np.mean(res[c][r])) for r in readouts if r != c)
    print(f"  {c:7s} {own / max(other, 1e-9):6.1f}x")
