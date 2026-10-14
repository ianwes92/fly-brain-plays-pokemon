"""Stage 1 of giving the fly real vision: where does each photoreceptor look?

FlyWire ships no coordinates, so the eye's layout has to come out of the
wiring. It can: the columns form a lattice (measured -- columns reachable in
d steps go 6.5/20.3/39.9/65.8 against a hexagon's 6/18/36/60), and laying the
column graph out in 2D gives a disc of ~790 columns per eye, which is what a
fly has.

This builds the map and then CHECKS it, because a layout that looks like an
eye is not necessarily the right eye:
  - neighbours in the layout should be neighbours in the wiring,
  - the two eyes should come out the same shape,
  - and each photoreceptor should land in exactly one column.

It writes nothing into the game. Stage 2 would sample the screen through this
map and drive the retina with it; that only becomes worth doing if the checks
here hold.
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain.connectome import Connectome                   # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "eye_map.npz")


def column_graph(cx, side, k=6):
    """Columns (one Mi1 each) linked to the columns they share targets with."""
    name = np.where(cx.labels["cell_type"] != "", cx.labels["cell_type"],
                    cx.labels["hemibrain_type"]).astype(str)
    sd = np.asarray(cx.labels["side"]).astype(str)
    sel = np.flatnonzero((name == "Mi1") & (sd == side))
    T = np.zeros((len(sel), cx.n), dtype=np.float32)
    for r, i in enumerate(sel):
        T[r, cx.indices[cx.indptr[i]:cx.indptr[i + 1]]] = 1.0
    S = T @ T.T
    np.fill_diagonal(S, 0)
    W = np.zeros_like(S)
    for i, row in enumerate(np.argsort(-S, axis=1)[:, :k]):
        for j in row:
            if S[i, j] > 0:
                W[i, j] = W[j, i] = 1.0
    return sel, S, W


def embed(W):
    """A 2D layout from the graph's own connectivity (spectral)."""
    d = W.sum(1)
    keep = d > 0
    W = W[keep][:, keep]
    d = W.sum(1)
    L = np.diag(d) - W
    norm = 1.0 / np.sqrt(np.maximum(d, 1e-9))
    vals, vecs = np.linalg.eigh(L * norm[:, None] * norm[None, :])
    xy = vecs[:, 1:3] * norm[:, None]
    xy = (xy - xy.mean(0)) / (xy.std(0) + 1e-12)
    return keep, xy, vals


def main():
    cx = Connectome()
    out = {}
    for side in ("left", "right"):
        sel, S, W = column_graph(cx, side)
        keep, xy, vals = embed(W)
        cols = sel[keep]
        print(f"{side} eye: {len(sel)} Mi1, {len(cols)} columns laid out")
        print(f"  eigenvalues 1-5: " + " ".join(f"{v:.4f}" for v in vals[1:6]))

        # CHECK 1: do neighbours in the layout share targets in the wiring?
        Sk = S[keep][:, keep]
        near = []
        for i in range(len(cols)):
            dist = np.sqrt(((xy - xy[i]) ** 2).sum(1))
            dist[i] = 1e9
            near.append(np.argsort(dist)[:6])
        shared_near = np.mean([Sk[i, j] for i, js in enumerate(near) for j in js])
        rng = np.random.default_rng(0)
        shared_rand = np.mean([Sk[i, rng.integers(len(cols))] for i in range(len(cols))
                               for _ in range(6)])
        print(f"  targets shared with the 6 NEAREST columns : {shared_near:7.2f}")
        print(f"  ...with 6 columns picked at random        : {shared_rand:7.2f}")
        print(f"  ratio: {shared_near / max(shared_rand, 1e-9):5.1f}x "
              f"(a real map should be far above 1)")

        # CHECK 2: is the layout a filled disc rather than a line or a blob?
        r = np.sqrt((xy ** 2).sum(1))
        print(f"  layout radius: median {np.median(r):.2f}, "
              f"5th-95th {np.percentile(r, 5):.2f}-{np.percentile(r, 95):.2f}")
        # ORIENT IT. The layout comes out of an eigenvector, so its rotation
        # and handedness are arbitrary -- "up" in the numbers is not up in the
        # eye. The dorsal rim (sub_class DRA, the polarised-light strip) runs
        # along the TOP edge of a fly's eye, so the columns its receptors sit
        # in say which way is dorsal, and the map can be turned to match.
        sub = np.asarray(cx.labels["sub_class"]).astype(str)
        sd_all = np.asarray(cx.labels["side"]).astype(str)
        dra = np.flatnonzero((sub == "DRA") & (sd_all == side))
        col_at = {int(c): k for k, c in enumerate(cols)}
        seen = []
        for i in dra:
            a, b = cx.indptr[i], cx.indptr[i + 1]
            for t in cx.indices[a:b]:
                if int(t) in col_at:
                    seen.append(col_at[int(t)])
        if len(seen) >= 5:
            centre = xy[seen].mean(0)
            ang = np.arctan2(centre[1], centre[0])
            rot = np.array([[np.cos(-ang + np.pi / 2), -np.sin(-ang + np.pi / 2)],
                            [np.sin(-ang + np.pi / 2), np.cos(-ang + np.pi / 2)]])
            xy = xy @ rot.T
            print(f"  dorsal rim found in {len(set(seen))} columns; map turned so "
                  f"they sit at the top (they are now at y = {xy[seen].mean(0)[1]:+.2f})")
        else:
            print(f"  dorsal rim reached only {len(set(seen))} columns -- "
                  f"NOT enough to orient the map; it stays in eigenvector axes")
        out[f"{side}_cols"] = cols
        out[f"{side}_xy"] = xy

    # CHECK 3: which column does each photoreceptor look through?
    # NOT directly: R1-6 synapse onto the LAMINA (L1/L2/L3) and the lamina
    # onto the medulla, so the assignment is two hops. Asking for a direct
    # R->Mi1 edge found nothing at all, which is the anatomy, not a bug.
    name = np.where(cx.labels["cell_type"] != "", cx.labels["cell_type"],
                    cx.labels["hemibrain_type"]).astype(str)
    r16 = np.flatnonzero(np.char.startswith(name, "R1-6"))
    lam = np.flatnonzero(np.char.startswith(name, "L1")
                         | np.char.startswith(name, "L2")
                         | np.char.startswith(name, "L3"))
    allcols = np.concatenate([out["left_cols"], out["right_cols"]])
    col_at = {int(c): k for k, c in enumerate(allcols)}

    def strongest(i, among):
        """The partner of i, within `among`, carrying the most synapses."""
        a, b = cx.indptr[i], cx.indptr[i + 1]
        tgt, w = cx.indices[a:b], np.abs(cx.weights[a:b])
        best, best_w = None, 0.0
        for t, ww in zip(tgt, w):
            if int(t) in among and ww > best_w:
                best, best_w = int(t), float(ww)
        return best

    lam_set = set(int(x) for x in lam)
    lam_col = {}
    for i in lam:
        c = strongest(int(i), col_at)
        if c is not None:
            lam_col[int(i)] = col_at[c]
    print(f"\nlamina neurons (L1-L3): {len(lam)}, "
          f"{100*len(lam_col)/len(lam):.0f}% sit under a column")
    assigned = 0
    pr_col = {}
    for i in r16:
        l = strongest(int(i), lam_set)
        if l is not None and l in lam_col:
            pr_col[int(i)] = lam_col[l]
            assigned += 1
    print(f"photoreceptors (R1-6): {len(r16)}")
    print(f"  placed in a column, R -> lamina -> medulla: {100*assigned/len(r16):5.1f}%")
    if assigned:
        per = np.bincount(list(pr_col.values()), minlength=len(allcols))
        used = per[per > 0]
        print(f"  columns with photoreceptors: {len(used)} of {len(allcols)}")
        print(f"  photoreceptors per column: median {np.median(used):.0f}, "
              f"5th-95th {np.percentile(used, 5):.0f}-{np.percentile(used, 95):.0f} "
              f"(a real ommatidium has 6 of R1-6)")
        out["pr_index"] = np.array(sorted(pr_col), dtype=np.int64)
        out["pr_column"] = np.array([pr_col[i] for i in sorted(pr_col)], dtype=np.int32)
    np.savez(OUT, **out)
    print(f"\nwrote {OUT}")


if __name__ == "__main__":
    main()
