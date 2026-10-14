"""The wiring diagram: FlyWire FAFB v783 turned into flat arrays.

The CSVs from Zenodo are convenient for humans and useless at 200 Hz, so
`build()` converts them once into a compressed sparse row layout the engine
can walk without parsing anything:

    indptr[i]:indptr[i+1]  ->  slice of indices/weights for neuron i's outputs
    indices[k]             ->  postsynaptic neuron index
    weights[k]             ->  signed synapse count (+ excitatory, - inhibitory)

Weights stay as raw signed synapse counts rather than millivolts, so the
per-synapse PSP size remains a runtime knob instead of something baked into
the file.

Sign comes from the presynaptic transmitter prediction carried on each edge:
acetylcholine excites, GABA and glutamate inhibit (Drosophila glutamate is
largely inhibitory, via GluCl-alpha). The amines are slow modulators rather
than fast ionotropic drive, so they are dropped from the graph by default;
build(include_modulatory=True) folds them in as excitatory instead.
"""
import array
import csv
import gzip
import os
import sys
import time

import numpy as np

DATA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
CACHE = os.path.join(DATA, "connectome.npz")

# Fast ionotropic sign by predicted presynaptic transmitter.
SIGN = {"ACH": 1, "GABA": -1, "GLUT": -1, "DA": 0, "SER": 0, "OCT": 0, "": 0}
MODULATORY = ("DA", "SER", "OCT")

LABELS = ("super_class", "class", "sub_class", "cell_type", "hemibrain_type",
          "side", "nerve")

# Derived at build time from where a neuron's synapses actually sit, not
# present in classification.csv. Used to place neurons on the brain map.
DERIVED = ("neuropil",)


def _open(name):
    path = os.path.join(DATA, name)
    if not os.path.exists(path):
        raise SystemExit(f"missing {path}\nRun ./fetch_data.py first.")
    return gzip.open(path, "rt", newline="")


LEARNING_RAW = os.path.join(DATA, "learning_raw.npz")


def build(min_syn=5, include_modulatory=False, out=CACHE, mb_min_syn=2):
    """Convert the FlyWire CSVs into the flat CSR cache. Takes a few minutes."""
    t0 = time.time()

    # --- neurons + annotations -------------------------------------------
    print("reading classification.csv.gz ...")
    root_ids = array.array("q")
    cols = {k: [] for k in LABELS}
    with _open("classification.csv.gz") as f:
        reader = csv.reader(f)
        header = next(reader)
        pos = {name: header.index(name) for name in LABELS}
        for row in reader:
            root_ids.append(int(row[0]))
            for name in LABELS:
                cols[name].append(row[pos[name]])

    root_ids = np.frombuffer(root_ids, dtype=np.int64).copy()
    n = len(root_ids)
    labels = {k: np.array(v) for k, v in cols.items()}
    del cols
    print(f"  {n:,} neurons")

    # Root ids are 64-bit and sparse. Sort once here and resolve every edge
    # with one vectorised searchsorted after the parse, rather than paying
    # numpy call overhead per row.
    order = np.argsort(root_ids)
    sorted_ids = root_ids[order]

    # --- per-neuron transmitter (fallback when an edge carries none) ------
    nt_of = {}
    with _open("neurons.csv.gz") as f:
        reader = csv.reader(f)
        header = next(reader)
        i_id, i_nt = header.index("root_id"), header.index("nt_type")
        for row in reader:
            nt_of[int(row[i_id])] = row[i_nt]

    # --- edges ------------------------------------------------------------
    sign = dict(SIGN)
    if include_modulatory:
        for nt in MODULATORY:
            sign[nt] = 1

    # This file has no quoted fields, so the fast split stays.
    print(f"reading connections_no_threshold.csv.gz (min_syn={min_syn}) ...")
    pre_raw = array.array("q")
    post_raw = array.array("q")
    wt = array.array("f")
    npil = array.array("h")          # index into npil_names
    npil_names, npil_code = [], {}

    seen = kept = dropped_weak = dropped_sign = 0
    with _open("connections_no_threshold.csv.gz") as f:
        header = next(f).rstrip("\r\n").split(",")
        i_pre = header.index("pre_root_id")
        i_post = header.index("post_root_id")
        i_syn = header.index("syn_count")
        i_nt = header.index("nt_type")
        i_npil = header.index("neuropil")

        for line in f:
            seen += 1
            if not seen % 2_000_000:
                sys.stdout.write(f"\r  {seen:,} rows, {kept:,} kept   ")
                sys.stdout.flush()

            row = line.rstrip("\r\n").split(",")
            count = int(row[i_syn])
            if count < min_syn:
                dropped_weak += 1
                continue

            a = int(row[i_pre])
            nt = row[i_nt]
            s = sign.get(nt if nt else nt_of.get(a, ""), 0)
            if s == 0:
                dropped_sign += 1
                continue

            region = row[i_npil]
            rc = npil_code.get(region)
            if rc is None:
                rc = npil_code[region] = len(npil_names)
                npil_names.append(region)

            pre_raw.append(a)
            post_raw.append(int(row[i_post]))
            wt.append(s * count)
            npil.append(rc)
            kept += 1
    print(f"\r  {seen:,} rows scanned, {kept:,} edges kept          ")

    pre_raw = np.frombuffer(pre_raw, dtype=np.int64)
    post_raw = np.frombuffer(post_raw, dtype=np.int64)
    wt = np.frombuffer(wt, dtype=np.float32)
    npil = np.frombuffer(npil, dtype=np.int16)

    # Resolve root ids -> dense indices, dropping edges touching a neuron
    # that is not in the classification table.
    print("resolving root ids ...")
    ia = np.searchsorted(sorted_ids, pre_raw).clip(0, n - 1)
    ib = np.searchsorted(sorted_ids, post_raw).clip(0, n - 1)
    ok = (sorted_ids[ia] == pre_raw) & (sorted_ids[ib] == post_raw)
    dropped_unknown = int((~ok).sum())

    pre = order[ia[ok]].astype(np.int32)
    post = order[ib[ok]].astype(np.int32)
    wt = wt[ok]
    npil = npil[ok]

    # Each neuron's home region: whichever neuropil holds most of its
    # synapses, counting both the ones it makes and the ones it receives.
    print("assigning neurons to neuropils ...")
    nn = len(npil_names)
    syn = np.abs(wt).astype(np.float64)
    tally = np.bincount((pre.astype(np.int64) * nn + npil), weights=syn,
                        minlength=n * nn)
    tally += np.bincount((post.astype(np.int64) * nn + npil), weights=syn,
                         minlength=n * nn)
    tally = tally.reshape(n, nn)
    home = tally.argmax(axis=1)
    home_names = np.array(npil_names, dtype=object)[home]
    home_names[tally.max(axis=1) == 0] = ""      # isolated neurons
    labels["neuropil"] = np.array(list(home_names))
    del tally

    print(f"  dropped: {dropped_weak:,} below threshold, "
          f"{dropped_sign:,} modulatory/unknown transmitter, "
          f"{dropped_unknown:,} unmatched root id")

    # --- mushroom body learning synapses ---------------------------------
    # Kenyon cell -> MBON connections are the synapses that learn, and they
    # are numerous but individually small: each KC contacts each MBON of its
    # lobe through a few synapses. The global min_syn=5 filter, meant to drop
    # unreliable connections, removes 82% of these edges and 54% of their
    # synapses -- more than half of the learning circuit. So they get their
    # own, lower threshold. >=2 keeps 86% of the synapses while still dropping
    # single-synapse contacts, the likeliest detection errors. All are
    # predicted cholinergic, so excitatory.
    mb_added = 0
    if mb_min_syn is not None and mb_min_syn < min_syn and os.path.exists(LEARNING_RAW):
        z = np.load(LEARNING_RAW)
        keep = (z["kcm_syn"] >= mb_min_syn) & (z["kcm_syn"] < min_syn)
        pre = np.concatenate([pre, z["kcm_pre"][keep].astype(np.int32)])
        post = np.concatenate([post, z["kcm_post"][keep].astype(np.int32)])
        wt = np.concatenate([wt, z["kcm_syn"][keep].astype(np.float32)])
        mb_added = int(keep.sum())
        print(f"  added {mb_added:,} Kenyon cell -> MBON edges between "
              f"{mb_min_syn} and {min_syn - 1} synapses")
    elif mb_min_syn is not None and mb_min_syn < min_syn:
        print("  (no learning_raw.npz yet -- run `flybrain learn-extract`; "
              "KC->MBON edges below min_syn left out)")

    # --- CSR by presynaptic neuron ---------------------------------------
    print("sorting into CSR ...")
    order_e = np.argsort(pre, kind="stable")
    indices = post[order_e]
    weights = wt[order_e]
    indptr = np.zeros(n + 1, dtype=np.int64)
    np.cumsum(np.bincount(pre, minlength=n), out=indptr[1:])

    np.savez_compressed(
        out,
        root_id=root_ids,
        indptr=indptr,
        indices=indices,
        weights=weights,
        min_syn=np.int32(min_syn),
        mb_min_syn=np.int32(mb_min_syn if mb_added else min_syn),
        include_modulatory=np.bool_(include_modulatory),
        **{f"label_{k}": v for k, v in labels.items()},
    )
    print(f"wrote {out} ({os.path.getsize(out)/1e6:.0f} MB) in {time.time()-t0:.0f}s")


class Connectome:
    """Loaded wiring diagram plus the label lookups used to pick populations."""

    def __init__(self, path=CACHE):
        if not os.path.exists(path):
            raise SystemExit(f"missing {path}\nRun: ./flybrain build")
        z = np.load(path, allow_pickle=False)
        self.root_id = z["root_id"]
        self.indptr = z["indptr"]
        self.indices = z["indices"]
        self.weights = z["weights"]
        self.min_syn = int(z["min_syn"])
        self.labels = {k: z[f"label_{k}"] for k in LABELS}
        for k in DERIVED:
            if f"label_{k}" in z:
                self.labels[k] = z[f"label_{k}"]
        self.n = len(self.root_id)
        self.n_edges = len(self.indices)

    def select(self, **kw):
        """Indices of neurons matching every label given.

        Values may be a string, a list of strings, or a string ending in '*'
        for a prefix match:  select(super_class='sensory', cell_type='JO-*')
        """
        mask = np.ones(self.n, dtype=bool)
        for key, want in kw.items():
            if key not in self.labels:
                raise KeyError(f"no such label {key!r}; have {list(self.labels)}")
            col = self.labels[key]
            if isinstance(want, str):
                want = [want]
            sub = np.zeros(self.n, dtype=bool)
            for w in want:
                sub |= (np.char.startswith(col, w[:-1]) if w.endswith("*")
                        else col == w)
            mask &= sub
        return np.flatnonzero(mask)

    def out_degree(self, idx=None):
        d = np.diff(self.indptr)
        return d if idx is None else d[idx]

    def describe(self, i):
        bits = [f"{k}={self.labels[k][i]}" for k in LABELS if self.labels[k][i]]
        return f"{self.root_id[i]} " + " ".join(bits)

    def summary(self):
        exc = int((self.weights > 0).sum())
        syn = float(np.abs(self.weights).sum())
        return (f"{self.n:,} neurons  {self.n_edges:,} edges  "
                f"{syn/1e6:.1f}M synapses  "
                f"{100*exc/max(self.n_edges,1):.0f}% excitatory  "
                f"(min_syn={self.min_syn})")
