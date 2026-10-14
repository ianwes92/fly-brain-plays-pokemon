"""A wall readout that answers the same either way round.

The one in use is lopsided. Measured in play over 8,823 samples: with an
obstacle really on the left it reads +162 Hz, with one really on the right it
reads +22 Hz -- the wrong sign -- and with nothing, 0. So it is a "something on
the left" detector, the fly turns right about twice as often as left, and no
threshold fixes that (per-side thresholds set from ground truth still caught
only 14% of real right-hand obstacles, wrong 74% of the times they fired).

The cause is how it was chosen: ten descending neurons ranked by their
composite response, with no requirement that they come in MIRROR PAIRS. A
left-minus-right readout is only even-handed if each neuron on one side is
matched by its twin on the other with the opposite sign; then a mirrored input
gives a mirrored answer by construction, whatever the neurons do.

So: drive one eye's LPLC1, then the other, and keep only cell types present on
BOTH sides, weighting left +1 and right -1.

  DISCOVERY seeds 300-307 rank each pair by its WEAKER side: a pair only
  counts if it answers both cases with OPPOSITE signs, and is scored by the
  smaller of the two answers. Ranking by how far apart the cases are instead
  (a first attempt) chose pairs like DNp03, +80 for one eye and 0 for the
  other -- a wide gap that is itself lopsided, which is the bug being fixed.
  Each pair's weights are then flipped so every one answers an obstacle on
  the left positively; otherwise they cancel each other.
  VALIDATION seeds 400-407 must show, on data never used to choose them:
    - a strong answer to an obstacle on the left,
    - an equal and OPPOSITE answer to one on the right (the ratio of the two
      is the whole point: |ratio - (-1)| small),
    - and both far from what nothing produces.
"""
import os
import sys

sys.path.insert(0, "~/flybrain")
import numpy as np                                            # noqa: E402
from flybrain.connectome import Connectome                    # noqa: E402
from flybrain.engine import Brain, Params                     # noqa: E402
from flybrain.game import WALL_READOUT, _by_root              # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
out = open(os.path.join(HERE, "wall_symmetry.log"), "w")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    out.write(s + "\n"); out.flush(); os.fsync(out.fileno())


cx = Connectome()
L = cx.labels
name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"]).astype(str)
side = np.asarray(L["side"]).astype(str)
desc = np.asarray(L["super_class"]).astype(str) == "descending"
lplc1 = {s: np.flatnonzero((name == "LPLC1") & (side == s)) for s in ("left", "right")}
dn = np.flatnonzero(desc)
HZ = 60.0                      # one eye, the strength the game's channel delivers


def trial(seed, driven, ms=500):
    b = Brain(cx, Params(al_ln_inhibitory=True), seed=seed)
    for _ in range(200):
        b.step()
    before = b.spikes_total.copy()
    for _ in range(ms):
        if driven is not None:
            b.drive(driven, HZ)
        b.step()
    return (b.spikes_total - before) / (ms / 1000)


def responses(seeds):
    """Per seed: each descending neuron's rate change for left and right."""
    outL, outR = [], []
    for s in seeds:
        base = trial(s, None)
        outL.append(trial(s, lplc1["left"])[dn] - base[dn])
        outR.append(trial(s, lplc1["right"])[dn] - base[dn])
    return np.array(outL), np.array(outR)


# Mirror pairs: cell types with a neuron on each side.
by_type = {}
for k, i in enumerate(dn):
    by_type.setdefault((name[i], side[i]), []).append(k)
pairs = [(t, by_type[(t, "left")], by_type[(t, "right")])
         for t in {t for t, _ in by_type} if (t, "left") in by_type and (t, "right") in by_type]
log(f"{len(dn)} descending neurons, {len(pairs)} cell types present on both sides")

dL, dR = responses(range(300, 308))
score, detail = [], {}
for t, li, ri in pairs:
    # pair reading = (left members) - (right members), under each eye's drive
    a = (dL[:, li].sum(1) - dL[:, ri].sum(1)).mean()
    b = (dR[:, li].sum(1) - dR[:, ri].sum(1)).mean()
    if a * b >= 0:                      # must answer the two cases oppositely
        continue
    score.append((min(abs(a), abs(b)), t, a, b))
    detail[t] = (a, b)
score.sort(reverse=True)
chosen = [(t, 1 if a > 0 else -1) for _, t, a, b in score[:10]]     # +1 = left-eye positive
log(f"\n{len(score)} pairs answer the two cases with opposite signs; the ten strongest "
    f"(scored by their WEAKER side):")
for w, t, a, b in score[:10]:
    log(f"    {t:14s} left-eye {a:+8.1f}   right-eye {b:+8.1f}   weaker side {w:7.1f}")

vL, vR = responses(range(400, 408))
compL = np.zeros(len(vL)); compR = np.zeros(len(vR))
for t, w in chosen:                      # each pair aligned: left-eye positive
    li, ri = by_type[(t, "left")], by_type[(t, "right")]
    compL += w * (vL[:, li].sum(1) - vL[:, ri].sum(1))
    compR += w * (vR[:, li].sum(1) - vR[:, ri].sum(1))
ratio = compR.mean() / compL.mean() if compL.mean() else float("nan")
log(f"\nVALIDATION (fresh seeds 400-407):")
log(f"    obstacle on the left  -> {compL.mean():+8.1f} Hz  (sd {compL.std(ddof=1):.1f})")
log(f"    obstacle on the right -> {compR.mean():+8.1f} Hz  (sd {compR.std(ddof=1):.1f})")
log(f"    ratio {ratio:+.2f} (want about -1: equal and opposite)")

old_idx, old_sign = _by_root(cx, WALL_READOUT)
pos = {int(v): k for k, v in enumerate(dn)}
oi = [pos[int(i)] for i in old_idx if int(i) in pos]
os_ = np.array([s for i, s in zip(old_idx, old_sign) if int(i) in pos])
oldL = (vL[:, oi] * os_).sum(1)
oldR = (vR[:, oi] * os_).sum(1)
log(f"\n  the readout in use, on the same fresh seeds, for comparison:")
log(f"    left {oldL.mean():+8.1f} Hz   right {oldR.mean():+8.1f} Hz   "
    f"ratio {oldR.mean() / oldL.mean() if oldL.mean() else float('nan'):+.2f}")

good = abs(ratio + 1) < 0.25 and compL.mean() > 20
log(f"\nVERDICT: {'USABLE -- even-handed' if good else 'NOT USABLE'} "
    f"(needs ratio within 0.25 of -1 and a clear response)")
if good:
    log("\nWALL_READOUT = (")
    for t, w in chosen:
        for k in by_type[(t, "left")]:
            log(f"    ({int(cx.root_id[dn[k]])}, {w:+d}),   # {t} left")
        for k in by_type[(t, "right")]:
            log(f"    ({int(cx.root_id[dn[k]])}, {-w:+d}),   # {t} right")
    log(")")
out.close()
