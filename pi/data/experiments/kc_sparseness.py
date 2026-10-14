"""Calibrate APL's inhibition to real Kenyon cell sparseness -- and nothing else.

Why: each scent recruits ~20% of Kenyon cells here and two scents share 85% of
them, so pain near a person teaches the fly to fear doors just as much (in-game
check: opinions people -0.635, exits -0.631). In real flies an odour drives
only ~5-10% of Kenyon cells (Turner et al. 2008; Honegger et al. 2011), kept
that sparse by APL, and blocking APL makes responses denser and odours harder
to discriminate (Lin et al. 2014). APL is the one known mechanism, so it is the
one knob: Params.apl_gain scales its inhibition onto the Kenyon cells.

RULE, fixed before running: on discovery seeds 300-302, the chosen gain is the
SMALLEST in the sweep whose mean responder fraction (over both scents and all
seeds) falls within 5-10%. Chosen on sparseness alone -- the specificity of
learning is then tested separately, on fresh seeds, and the calibration does
not get to peek at it.

RESPONDER  a Kenyon cell firing at least 2 more spikes in the 1 s the scent is
  on than in the 1 s of no scent before it (>= 2 Hz above its own baseline).
  1- and 3-spike criteria are reported too, so the choice is visibly not an
  artefact of the cut-off.
ALSO REPORTED  background activity (KCs firing at all with no scent), the
  overlap of the two scents' responder sets (Jaccard), and total KC output,
  so a gain that 'achieves' sparseness by silencing the mushroom body shows up.
"""
import os
import sys

sys.path.insert(0, "~/flybrain")
import numpy as np                                            # noqa: E402
from flybrain.connectome import Connectome                    # noqa: E402
from flybrain.engine import Brain, Params                     # noqa: E402
from flybrain.nose import Nose, PEAK_HZ                       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
out = open(os.path.join(HERE, "kc_sparseness.log"), "w")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    out.write(s + "\n"); out.flush(); os.fsync(out.fileno())


cx = Connectome()
nose = Nose(cx)
kc = np.flatnonzero(np.asarray(cx.labels["class"]).astype(str) == "Kenyon_Cell")
pos = np.full(cx.n, -1, dtype=np.int64); pos[kc] = np.arange(len(kc))
GAINS = (1, 2, 3, 5, 8, 12)
SEEDS = (300, 301, 302)


def measure(gain, seed):
    b = Brain(cx, Params(apl_gain=float(gain)), seed=seed)
    for _ in range(300):
        b.step()

    def count(idx):
        c = np.zeros(len(kc))
        for _ in range(1000):
            if idx is not None:
                b.drive(idx, PEAK_HZ)
            sp = b.step()
            if len(sp):
                k = pos[np.asarray(sp)]
                np.add.at(c, k[k >= 0], 1.0)
        return c

    res = {}
    for kind in Nose.KINDS:
        base = count(None)
        odour = count(nose.pn[kind])
        res[kind] = (base, odour)
    return res


log(f"{len(kc)} Kenyon cells; scents {nose.glomeruli}; target 5-10% responders")
log(f"{'gain':>5s} {'resp>=1':>8s} {'resp>=2':>8s} {'resp>=3':>8s} {'backgr':>7s} "
    f"{'overlap':>8s} {'KC Hz/cell':>11s}")
summary = {}
for gain in GAINS:
    r1, r2, r3, bg, jac, out_hz = [], [], [], [], [], []
    for seed in SEEDS:
        res = measure(gain, seed)
        sets = {}
        for kind, (base, odour) in res.items():
            d = odour - base
            r1.append((d >= 1).mean()); r2.append((d >= 2).mean()); r3.append((d >= 3).mean())
            bg.append((base > 0).mean())
            out_hz.append(odour.mean())
            sets[kind] = d >= 2
        a, c = sets["people"], sets["exits"]
        jac.append((a & c).sum() / max((a | c).sum(), 1))
    summary[gain] = float(np.mean(r2))
    log(f"{gain:5.0f} {100*np.mean(r1):7.1f}% {100*np.mean(r2):7.1f}% {100*np.mean(r3):7.1f}% "
        f"{100*np.mean(bg):6.1f}% {np.mean(jac):8.2f} {np.mean(out_hz):11.2f}")

inside = [g for g in GAINS if 0.05 <= summary[g] <= 0.10]
chosen = min(inside) if inside else None
log(f"\nCHOSEN apl_gain: {chosen}" + ("" if chosen else
    "  -- no gain in the sweep reaches 5-10%; widen the sweep before using any"))
out.close()
