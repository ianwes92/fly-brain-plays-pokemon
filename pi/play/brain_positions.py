#!/usr/bin/env python3
"""Real positions for the brain layer's 4000-neuron sample (Ian, 2026-10-09:
real positions only). Run with the isolated venv that has pyarrow:

  ~/flybrain-data/venv/bin/python -I play/brain_positions.py SYNAPSES.feather

Reads FlyWire's public synapse table (Zenodo 10676866, flywire_synapses_783)
batch by batch and, for every sampled neuron, averages the coordinates of all
its synapses -- as the presynaptic and as the postsynaptic partner. That is
the neuron's centre of synaptic mass in FAFB14 space (nm): real anatomy, not
a soma position. Writes data/brain_layout.json in obsfly's shape from the
fixed sample in data/brain_layout_schematic.json (same i, same neurons).
"""
import json
import os
import sys

import numpy as np
import pyarrow as pa
import pyarrow.ipc as ipc

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(os.path.dirname(HERE), "data")


def pick(names, *cands):
    for c in cands:
        if c in names:
            return c
    raise SystemExit(f"none of {cands} in columns {names}")


def main(path):
    sample = json.load(open(os.path.join(DATA, "brain_layout_schematic.json")))["neurons"]
    roots = np.array([int(r["root_id"]) for r in sample], dtype=np.int64)
    order = np.argsort(roots)
    sorted_roots = roots[order]
    n = len(roots)
    sums, cnt = np.zeros((n, 3)), np.zeros(n)

    src = pa.memory_map(path, "r")
    reader = ipc.open_file(src)
    names = reader.schema.names
    print("columns:", names, flush=True)
    pre = pick(names, "pre_pt_root_id", "pre_root_id", "pre")
    post = pick(names, "post_pt_root_id", "post_root_id", "post")
    xyz = None
    for trio in (("x", "y", "z"), ("pre_x", "pre_y", "pre_z"), ("post_x", "post_y", "post_z"),
                 ("pre_pt_position_x", "pre_pt_position_y", "pre_pt_position_z")):
        if all(c in names for c in trio):
            xyz = trio
            break
    if xyz is None:
        raise SystemExit(f"no coordinate columns in {names}")
    print(f"using {pre}, {post}, {xyz}; {reader.num_record_batches} batches", flush=True)

    def add(ids, pts):
        j = np.searchsorted(sorted_roots, ids)
        j[j >= n] = n - 1
        hit = sorted_roots[j] == ids
        if not hit.any():
            return
        k = order[j[hit]]
        np.add.at(cnt, k, 1)
        for d in range(3):
            np.add.at(sums[:, d], k, pts[d][hit])

    for b in range(reader.num_record_batches):
        rb = reader.get_batch(b).select([pre, post, *xyz])
        pts = [rb.column(i + 2).to_numpy(zero_copy_only=False).astype(np.float64) for i in range(3)]
        add(rb.column(0).to_numpy(zero_copy_only=False).astype(np.int64), pts)
        add(rb.column(1).to_numpy(zero_copy_only=False).astype(np.int64), pts)
        if b % 50 == 0:
            print(f"batch {b}/{reader.num_record_batches}, {int((cnt > 0).sum())} neurons placed", flush=True)

    pos = sums / np.maximum(cnt, 1)[:, None]
    out = []
    for r, (x, y, z), c in zip(sample, pos, cnt):
        side = r.get("side") or "center"
        out.append({"i": r["i"], "root_id": r["root_id"],
                    "x": round(float(x), 1) if c else None, "y": round(float(y), 1) if c else None,
                    "z": round(float(z), 1) if c else None, "group": r["group"],
                    "side": side if side in ("left", "right") else "center",
                    "cell_type": r.get("cell_type"), "synapses": int(c), "_n": r["_n"]})
    xs = np.array([o["x"] for o in out if o["x"] is not None])
    ys = np.array([o["y"] for o in out if o["y"] is not None])
    print(f"placed {len(xs)}/{n}; x {xs.min():.0f}..{xs.max():.0f} nm, y {ys.min():.0f}..{ys.max():.0f} nm")
    doc = {"neurons": out,
           "axes": {"horizontal": "x", "vertical": "y", "flip_vertical": True, "flip_horizontal": False,
                    "units": "nm"},
           "meta": {"positions": "centroid of each neuron's synapses (pre and post), FlyWire v783 "
                                 "synapse table, FAFB14 space",
                    "source": "Zenodo 10676866 flywire_synapses_783.feather",
                    "note": "FAFB y grows ventrally, hence flip_vertical for dorsal at the top. "
                            "x is medio-lateral; FAFB +x is the fly's LEFT (the image's right in a "
                            "frontal view) -- check against side before trusting a mirror.",
                    "count": n, "spk": "base64 bitset over i, LSB-first (packbits little)"}}
    tmp = os.path.join(DATA, "brain_layout.json.tmp")
    json.dump(doc, open(tmp, "w"), separators=(",", ":"))
    os.replace(tmp, os.path.join(DATA, "brain_layout.json"))


if __name__ == "__main__":
    main(sys.argv[1])
