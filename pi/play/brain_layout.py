#!/usr/bin/env python3
"""A fixed sample of ~4000 neurons for obsfly's GPU brain layer (2026-10-09).

Stratified so small, important populations are visible: every PAM and PPL1
dopamine neuron if there are under ~150, at least 200 central-complex, 400
Kenyon cells, every MBON, 200 descending; everything else in proportion.
Seeded, so the sample never changes (the live spike bitset indexes it).

POSITIONS ARE SCHEMATIC. FlyWire's public tables give each neuron's home
neuropil (where most of its synapses are), not coordinates. Each neuron is
placed inside its home neuropil's disc on flybrain/layout.py's frontal
schematic, the same map the stream draws: x -1 (fly's left) .. +1 (right),
y -1 (ventral) .. +1 (dorsal), z none. Arrangement is right (lateral is
lateral, dorsal is dorsal, midline is midline); distances are legible, not
measured. "neuropil" is included so real coordinates can be swapped in later.

Writes data/brain_layout.json; the relay serves it at /panel/brain_layout.
Usage: brain_layout.py build
"""
import hashlib
import json
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
OUT = os.path.join(ROOT, "data", "brain_layout.json")
TARGET = 4000
FLOORS = {"pam": 150, "ppl1": 150, "mbon": 10 ** 6, "cx": 200, "kc": 400, "descending": 200}


def groups(cx):
    """Display group per neuron, by the same rules as the panel's brain_hz."""
    from flybrain import layout
    L = cx.labels
    sc, cl = L["super_class"].astype(str), L["class"].astype(str)
    name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"]).astype(str)
    npil = L["neuropil"].astype(str)
    g = np.array(["other"] * len(sc), dtype=object)
    for want, key in (("optic", "optic"), ("visual_projection", "visual_projection"),
                      ("sensory", "sensory"), ("central", "central"),
                      ("descending", "descending"), ("motor", "motor")):
        g[sc == key] = want
    g[np.array([layout.group_of(n) == "central_complex" for n in npil])] = "cx"
    g[cl == "Kenyon_Cell"] = "kc"
    g[cl == "MBON"] = "mbon"
    g[(cl == "DAN") & np.char.startswith(name, "PAM")] = "pam"
    g[(cl == "DAN") & np.char.startswith(name, "PPL1")] = "ppl1"
    return g, npil, name


def build(seed=20261009):
    from flybrain.connectome import Connectome
    from flybrain import layout
    cx = Connectome()
    g, npil, name = groups(cx)
    rng = np.random.default_rng(seed)
    kinds, counts = np.unique(g, return_counts=True)
    take = {}
    for k, n in zip(kinds, counts):
        if k in FLOORS:
            take[k] = min(n, FLOORS[k])
    rest = TARGET - sum(take.values())
    free = {k: n for k, n in zip(kinds, counts) if k not in take}
    tot = sum(free.values())
    for k, n in free.items():
        take[k] = max(1, int(round(rest * n / tot)))
    idx = []
    for k, n in take.items():
        pool = np.flatnonzero(g == k)
        idx.extend(sorted(rng.choice(pool, size=min(n, len(pool)), replace=False).tolist()))
    idx = sorted(idx)
    places = layout.build()
    side = cx.labels["side"].astype(str)
    out = []
    for i, n in enumerate(idx):
        x0, y0, r = places.get(npil[n], places[""])
        # a fixed spot inside the disc, from the root id (so it never moves)
        h = hashlib.sha1(str(int(cx.root_id[n])).encode()).digest()
        a, d = h[0] / 255 * 2 * np.pi, np.sqrt(h[1] / 255) * r
        out.append({"i": i, "root_id": str(int(cx.root_id[n])), "x": round(float(x0 + d * np.cos(a)), 4),
                    "y": round(float(y0 + d * np.sin(a)), 4), "z": None, "group": str(g[n]),
                    "side": side[n] or None, "cell_type": name[n] or None, "neuropil": npil[n] or None,
                    "_n": int(n)})
    meta = {"version": 1, "seed": seed, "count": len(out),
            "positions": "schematic frontal layout by home neuropil (flybrain/layout.py); NOT measured coordinates",
            "axes": {"x": "-1 fly's left .. +1 fly's right", "y": "-1 ventral .. +1 dorsal", "z": None},
            "per_group": {k: int(v) for k, v in sorted(take.items())},
            "spk": "/panel/live 'spk' is base64 of a little-endian bitset over i (numpy packbits, bitorder='little'), spikes since the previous /live call"}
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        json.dump({"meta": meta, "neurons": out}, f, separators=(",", ":"))
    return meta


def load(path=OUT):
    with open(path) as f:
        return json.load(f)


if __name__ == "__main__":
    if sys.argv[1:] == ["build"]:
        print(json.dumps(build(), indent=1))
    else:
        sys.exit(__doc__)
