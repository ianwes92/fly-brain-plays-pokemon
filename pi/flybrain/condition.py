"""Does the brain learn? A simulated odour-conditioning experiment.

The in-silico version of the experiment fly labs run to show aversive memory:
pair odour A with punishment, present odour B without it, then test both.

  odours       two non-overlapping sets of glomeruli, driven through their
               uniglomerular projection neurons
  punishment   the PPL1 dopamine cluster, driven during the second half of A
  test         both odours with learning frozen, before and after training
  control      an identical brain whose learning rule never updates

For every readout the learning score is (change to A) - (change to B): the
paired odour should change and the unpaired one should not. The control
should score about zero; any gap between the two is what learning did.

The question that matters for playing games is the last one: whether learning
reaches the descending neurons that drive the buttons. An MBON-only effect
would be real but invisible in play.
"""
import os
import re

import numpy as np

from . import ports
from .engine import Brain, Params
from .connectome import LEARNING_RAW
from .learning import LearningParams, MushroomBodyLearning

ODOUR_MS, ODOUR_HZ = 1000, 60.0
SHOCK_FROM_MS, SHOCK_HZ = 300, 80.0
ITI_MS = 2000
TRAIN_BLOCKS = 6
TEST_REPEATS = 3        # presentations of each odour per test; one is far too noisy
GLOMERULI_PER_ODOUR = 6


def odours(cx, seed=0):
    """Two odours as disjoint sets of glomeruli.

    Chosen by glomerulus, not by PN type: several glomeruli have more than
    one PN lineage (VL2p has both _vPN and _adPN), and an odour that activates
    a glomerulus drives all of its projection neurons. Picking types instead
    once put the same glomerulus into both odours.
    """
    ct, hb, cl = cx.labels["cell_type"], cx.labels["hemibrain_type"], cx.labels["class"]
    name = np.where(ct != "", ct, hb)
    pat = re.compile(r"^([A-Z]{1,3}\d?[a-z]{0,2})_[a-z]*PN$")
    glom_of = {}
    for n in set(name[cl == "ALPN"]):
        m = pat.match(n)
        if m:
            glom_of.setdefault(m.group(1), []).append(n)
    glomeruli = sorted(glom_of)
    rng = np.random.default_rng(seed)
    pick = rng.choice(glomeruli, size=2 * GLOMERULI_PER_ODOUR, replace=False)
    ga, gb = sorted(pick[:GLOMERULI_PER_ODOUR]), sorted(pick[GLOMERULI_PER_ODOUR:])
    types = lambda gs: [t for g in gs for t in glom_of[g]]
    sel = lambda gs: np.flatnonzero(np.isin(name, types(gs)) & (cl == "ALPN"))
    return (ga, sel(ga)), (gb, sel(gb)), len(glomeruli)


class Session:
    def __init__(self, cx, seed, plastic, lp=None):
        self.cx = cx
        self.b = Brain(cx, Params(), seed=seed)
        self.mb = MushroomBodyLearning(self.b, lp).attach()
        self.mb.frozen = not plastic
        cl, ct, hb = cx.labels["class"], cx.labels["cell_type"], cx.labels["hemibrain_type"]
        self.ppl1 = np.flatnonzero((cl == "DAN") & (np.char.startswith(ct, "PPL1")
                                                    | np.char.startswith(hb, "PPL1")))
        sel = lambda **kw: cx.select(super_class="descending", **kw)
        self.dn = cx.select(**ports.MOTOR["descending"])
        self.turn_l = np.concatenate([sel(hemibrain_type="DNp15*", side="left"),
                                      sel(cell_type="DNg41", side="left")])
        self.turn_r = np.concatenate([sel(hemibrain_type="DNp15*", side="right"),
                                      sel(cell_type="DNg41", side="right")])
        self.mdn = sel(hemibrain_type="MDN*")

    def wait(self, ms):
        for _ in range(int(ms / self.b.p.dt)):
            self.b.step()

    def present(self, pn, shock=False):
        """One odour presentation; returns per-neuron spike counts in the window."""
        b = self.b
        before = b.spikes_total.copy()
        for i in range(int(ODOUR_MS / b.p.dt)):
            b.drive(pn, ODOUR_HZ)
            if shock and i * b.p.dt >= SHOCK_FROM_MS:
                b.drive(self.ppl1, SHOCK_HZ)
            b.step()
        return (b.spikes_total - before).astype(np.float64)

    def readout(self, counts):
        s = ODOUR_MS / 1000.0
        rl, rr = counts[self.turn_l].sum(), counts[self.turn_r].sum()
        return {
            "mbon": counts[self.mb.mbon] / s,
            "kc_active": counts[self.mb.kc] > 0,
            "kc_count": counts[self.mb.kc],
            "dn_vec": counts[self.dn] / s,
            "dn": counts[self.dn].mean() / s,
            "turn": (rl - rr) / (rl + rr + 1.0),
            "mdn": counts[self.mdn].mean() / s,
        }

    def test(self, a, b, a_first):
        """Each odour TEST_REPEATS times, interleaved, learning frozen.

        Single presentations proved far too noisy to compare: in a smoke run
        the unpaired odour's moonwalker response went from 8.5 to 3.75 Hz on
        its own. Counts are summed across repeats before reading out.
        """
        frozen, self.mb.frozen = self.mb.frozen, True
        sums = {"A": 0.0, "B": 0.0}
        order = (("A", a), ("B", b)) if a_first else (("B", b), ("A", a))
        for _ in range(TEST_REPEATS):
            for key, pn in order:
                sums[key] = sums[key] + self.present(pn)
                self.wait(ITI_MS)
        self.mb.frozen = frozen
        return {k: self.readout(v / TEST_REPEATS) for k, v in sums.items()}

    def run(self, a, b, a_first=True):
        self.wait(500)
        pre = self.test(a, b, a_first)
        for _ in range(TRAIN_BLOCKS):
            self.present(a, shock=True); self.wait(ITI_MS)
            self.present(b);             self.wait(ITI_MS)
        post = self.test(a, b, a_first)
        return pre, post


def learned(pre, post, key):
    return (post["A"][key] - pre["A"][key]) - (post["B"][key] - pre["B"][key])


def experiment(cx, seeds=(0, 1, 2), lp=None, log=print):
    (a_types, a), (b_types, b), n_types = odours(cx, seed=7)
    log(f"odour A: {', '.join(a_types)}  ({len(a)} PNs)")
    log(f"odour B: {', '.join(b_types)}  ({len(b)} PNs)   [glomeruli from {n_types} available]")
    results = {True: [], False: []}
    mbon_names = None
    for seed in seeds:
        for plastic in (True, False):
            s = Session(cx, seed, plastic, lp)
            pre, post = s.run(a, b, a_first=(seed % 2 == 0))
            results[plastic].append((pre, post, s.mb.mbon_strength()))
            if mbon_names is None:
                ct, hb = cx.labels["cell_type"], cx.labels["hemibrain_type"]
                mbon_names = [ct[i] or hb[i] for i in s.mb.mbon]
                teachers = s.mb.D
                ppl1_frac = teachers[:, np.isin(s.mb.dan, s.ppl1)].sum(axis=1)
            log(f"  seed {seed} {'learning' if plastic else 'control '} done")
    return results, mbon_names, ppl1_frac, (a_types, b_types)


def report(results, mbon_names, ppl1_frac, log=print):
    P, C = results[True], results[False]
    pre0 = P[0][0]
    ka, kb = pre0["A"]["kc_active"], pre0["B"]["kc_active"]
    union = (ka | kb).sum()
    log(f"\nKenyon cells active: odour A {ka.mean()*100:.1f}%, odour B {kb.mean()*100:.1f}%, "
        f"overlap {100*(ka & kb).sum()/max(union,1):.0f}% of the union")

    def stat(runs, key):
        v = np.array([learned(pre, post, key) for pre, post, _ in runs])
        return v

    log("\nMBONs -- learned change in response to A relative to B (Hz), mean over seeds")
    lm = np.mean([learned(pre, post, "mbon") for pre, post, _ in P], axis=0)
    lc = np.mean([learned(pre, post, "mbon") for pre, post, _ in C], axis=0)
    strength = np.mean([st for *_, st in P], axis=0)
    order = np.argsort(lm)
    log(f"  {'MBON':14s} {'PPL1 share':>10s} {'learning':>9s} {'control':>8s} {'KC input left':>14s}")
    for j in list(order[:8]):
        log(f"  {mbon_names[j]:14s} {ppl1_frac[j]:10.2f} {lm[j]:+9.2f} {lc[j]:+8.2f} {100*strength[j]:13.0f}%")
    hi = ppl1_frac > 0.5
    log(f"  PPL1-taught MBONs (n={int(hi.sum())}): learning {lm[hi].mean():+.2f} Hz, control {lc[hi].mean():+.2f} Hz")
    log(f"  other MBONs       (n={int((~hi).sum())}): learning {lm[~hi].mean():+.2f} Hz, control {lc[~hi].mean():+.2f} Hz")

    log("\nMotor readouts the game uses -- learned change A relative to B")
    verdicts = {}
    for key, label in (("dn", "descending drive (Hz)"), ("turn", "turn index"), ("mdn", "moonwalker MDN (Hz)")):
        p, c = stat(P, key), stat(C, key)
        diff = p.mean() - c.mean()
        spread = np.sqrt(p.var(ddof=1) / len(p) + c.var(ddof=1) / len(c)) if len(p) > 1 else np.inf
        z = diff / spread if spread > 0 else 0.0
        verdicts[key] = abs(z) > 2.5
        log(f"  {label:24s} learning {p.mean():+7.3f} ± {p.std(ddof=1):.3f}   "
            f"control {c.mean():+7.3f} ± {c.std(ddof=1):.3f}   difference {diff:+.3f} (z={z:+.1f})")
    return lm, lc, ppl1_frac, verdicts


# --- ceiling test with a matched control -------------------------------------
def ceiling_experiment(cx, seeds=range(5), log=print):
    """Largest motor effect this memory could have, against a matched control.

    For each seed, two runs of the same brain: 'ceiling' floors every synapse
    from the Kenyon cells odour A activates onto punishment-taught MBONs (a
    stronger memory than training can produce); 'control' does nothing
    between the two tests. Same seed, so both start identical and the
    pre-tests match exactly; the per-seed difference cancels drift from
    repeated odour presentations, which on its own moved the moonwalker
    readout by +1.9 Hz in the first experiment.
    """
    (ga, a), (gb, b), _ = odours(cx, seed=7)
    keys = ("dn", "turn", "mdn")
    out = {arm: {k: [] for k in keys} for arm in ("control", "ceiling")}
    for seed in seeds:
        for arm in ("control", "ceiling"):
            s = Session(cx, seed, plastic=False)
            s.wait(500)
            pre = s.test(a, b, a_first=(seed % 2 == 0))
            if arm == "ceiling":
                share = s.mb.D[:, np.isin(s.mb.dan, s.ppl1)].sum(axis=1)
                hit = pre["A"]["kc_active"][s.mb.edge_kc] & (share[s.mb.edge_mbon] > 0.5)
                s.mb.m[hit] = s.mb.p.floor
                s.b.w_mv[s.mb.edges] = s.mb.w0 * s.mb.m
            post = s.test(a, b, a_first=(seed % 2 == 0))
            for k in keys:
                out[arm][k].append(learned(pre, post, k))
            log(f"  seed {seed} {arm:8s}: " + ", ".join(
                f"{k} {out[arm][k][-1]:+.3f}" for k in keys))
    log("\nCEILING vs MATCHED CONTROL (A relative to B; paired by seed)")
    for k, label in (("dn", "descending drive (Hz)"), ("turn", "turn index"),
                     ("mdn", "moonwalker MDN (Hz)")):
        c, x = np.array(out["control"][k]), np.array(out["ceiling"][k])
        d = x - c
        z = d.mean() / (d.std(ddof=1) / np.sqrt(len(d))) if d.std(ddof=1) > 0 else 0.0
        log(f"  {label:24s} ceiling {x.mean():+7.3f}  control {c.mean():+7.3f}  "
            f"paired difference {d.mean():+.3f} ± {d.std(ddof=1):.3f}  (t={z:+.1f}, "
            f"{int((d > 0).sum())}/{len(d)} seeds positive)")
    return out


# --- what does training actually deliver? -------------------------------------
def training_diagnostics(cx, seed=0, lp=None, log=print):
    """One training run, instrumented: teaching signal and baseline per MBON,
    per trial, and the synapse strength it leaves on odour A's Kenyon cells."""
    (ga, a), (gb, b), _ = odours(cx, seed=7)
    s = Session(cx, seed, plastic=True, lp=lp)
    mb = s.mb
    share = mb.D[:, np.isin(mb.dan, s.ppl1)].sum(axis=1)
    bins = [("share > 0.8", share > 0.8), ("0.5 - 0.8", (share > 0.5) & (share <= 0.8)),
            ("0.2 - 0.5", (share > 0.2) & (share <= 0.5)), ("< 0.2", share <= 0.2)]
    rec = {"teach": np.zeros(len(mb.mbon)), "base_end": None}
    orig = mb._update

    def spy():
        orig()
        rec["teach"] += mb.teach
    mb._update = spy

    s.wait(500)
    pre = s.test(a, b, True)
    kcA = pre["A"]["kc_active"]
    log(f"odour A activates {kcA.mean()*100:.0f}% of Kenyon cells\n")
    log(f"{'trial':>5} " + "".join(f"{n:>22s}" for n, _ in bins))
    log(f"{'':>5} " + "".join(f"{'peak teach / baseline':>22s}" for _ in bins))
    for t in range(TRAIN_BLOCKS):
        peak = np.zeros(len(mb.mbon))
        orig_spy = mb._update

        def spy2():
            orig_spy()
            np.maximum(peak, mb.teach, out=peak)
        mb._update = spy2
        s.present(a, shock=True)
        mb._update = orig_spy
        base = mb.base.copy()
        log(f"{t+1:>5} " + "".join(f"{peak[m].mean():>12.2f} / {base[m].mean():>6.1f} Hz" for _, m in bins))
        s.wait(ITI_MS); s.present(b); s.wait(ITI_MS)
    onA = kcA[mb.edge_kc]
    log(f"\nsynapse strength left on odour A's Kenyon cells, by how much of an MBON's "
        f"dopamine comes from PPL1:")
    for name, m in bins:
        e = onA & m[mb.edge_mbon]
        log(f"  {name:12s} ({int(m.sum()):2d} MBONs, {int(e.sum()):6,} synapses): "
            f"mean strength {mb.m[e].mean() if e.any() else float('nan'):.2f}")
    return mb, share, kcA


# --- how much depression does behaviour need? ----------------------------------
def dose_response(cx, etas=(0.35, 1.0, 3.0), seeds=range(5), log=print, checkpoint=None):
    """Train at several learning rates against one frozen control per seed.

    The learning rate is a modelling choice, not a measurement. Rather than
    turning it up until the numbers look good, map it: for each rate, how far
    the synapses from odour A's Kenyon cells onto punishment-taught MBONs are
    weakened, and how large the resulting motor effect is compared with a
    brain that went through the identical protocol with learning frozen.
    """
    (ga, a), (gb, b), _ = odours(cx, seed=7)
    keys = ("dn", "turn", "mdn")
    arms = ["control"] + [f"eta {e:g}" for e in etas]
    res = {arm: {k: [] for k in keys + ("strength",)} for arm in arms}
    # Resumable: every finished session is appended to `checkpoint` (JSON
    # lines, fsync'd) and skipped on a rerun, so a power cut costs one session
    # instead of the whole run.
    import json
    done = {}
    if checkpoint and os.path.exists(checkpoint):
        with open(checkpoint) as f:
            for line in f:
                line = line.strip().strip("\x00")
                if line:
                    r = json.loads(line)
                    done[(r["seed"], r["arm"])] = r
        log(f"  resuming: {len(done)} session(s) already on disk")
    for seed in seeds:
        for arm, eta in zip(arms, (None,) + tuple(etas)):
            if (seed, arm) in done:
                r = done[(seed, arm)]
                for k in keys + ("strength",):
                    res[arm][k].append(r[k])
                log(f"  seed {seed} {arm:9s}: (from checkpoint) strength {r['strength']:.2f}, "
                    + ", ".join(f"{k} {r[k]:+.3f}" for k in keys))
                continue
            lp = LearningParams(eta=eta) if eta is not None else None
            s = Session(cx, seed, plastic=eta is not None, lp=lp)
            pre, post = s.run(a, b, a_first=(seed % 2 == 0))
            share = s.mb.D[:, np.isin(s.mb.dan, s.ppl1)].sum(axis=1)
            onA = pre["A"]["kc_active"][s.mb.edge_kc] & (share[s.mb.edge_mbon] > 0.5)
            res[arm]["strength"].append(float(s.mb.m[onA].mean()))
            for k in keys:
                res[arm][k].append(learned(pre, post, k))
            if checkpoint:
                rec = {"seed": seed, "arm": arm, **{k: float(res[arm][k][-1]) for k in keys + ("strength",)}}
                with open(checkpoint, "a") as f:
                    f.write(json.dumps(rec) + "\n"); f.flush(); os.fsync(f.fileno())
            log(f"  seed {seed} {arm:9s}: strength {res[arm]['strength'][-1]:.2f}, "
                + ", ".join(f"{k} {res[arm][k][-1]:+.3f}" for k in keys))
    log("\nDOSE-RESPONSE (A relative to B, paired against the frozen control by seed)")
    log(f"  {'arm':9s} {'strength':>8s}   {'moonwalker MDN (Hz)':>30s}   {'drive (Hz)':>24s}   {'turn':>22s}")
    for arm in arms[1:]:
        cells = []
        for k in ("mdn", "dn", "turn"):
            d = np.array(res[arm][k]) - np.array(res["control"][k])
            sd = d.std(ddof=1)
            t = d.mean() / (sd / np.sqrt(len(d))) if sd > 0 else 0.0
            cells.append(f"{d.mean():+6.2f} ± {sd:4.2f} t={t:+4.1f} {int((d > 0).sum())}/{len(d)}")
        log(f"  {arm:9s} {np.mean(res[arm]['strength']):8.2f}   {cells[0]:>30s}   {cells[1]:>24s}   {cells[2]:>22s}")
    return res



# --- replication, decided before looking ------------------------------------------
def replication(cx, seeds=range(10, 20), log=print, checkpoint=None):
    """Replicate the maximal-memory result on fresh seeds.

    The original ceiling test gave moonwalker +3.78 Hz (t=+3.1) on seeds 0-4,
    but many comparisons had been run by then, and training that got within
    reach of the same synapse strength showed nothing. So this is a
    replication with the analysis fixed in advance:

      PRIMARY  arm 'all' vs 'control', moonwalker MDN, paired by seed, on seeds
               10-19 (fresh: the simulation is deterministic, so reusing 0-4
               would just count the original result twice). Replicates only if
               t >= 2.262 (two-sided p < 0.05, df = 9) and the effect is positive.
      SECONDARY  arms 'strong' and 'weak' split odour A's active Kenyon cells at
               their median spike count, to ask whether the weakly firing cells
               -- the ones training barely depresses -- carry any effect.
               Descending drive and turning are reported for all arms.
    """
    import json
    (ga, a), (gb, b), _ = odours(cx, seed=7)
    keys = ("dn", "turn", "mdn")
    arms = ("control", "all", "strong", "weak")
    res = {arm: {k: [] for k in keys + ("floored",)} for arm in arms}
    done = {}
    if checkpoint and os.path.exists(checkpoint):
        with open(checkpoint) as f:
            for line in f:
                line = line.strip().strip("\x00")
                if line:
                    r = json.loads(line)
                    done[(r["seed"], r["arm"])] = r
        log(f"  resuming: {len(done)} session(s) already on disk")
    for seed in seeds:
        for arm in arms:
            if (seed, arm) in done:
                r = done[(seed, arm)]
                for k in keys + ("floored",):
                    res[arm][k].append(r[k])
                log(f"  seed {seed} {arm:7s}: (from checkpoint) " + ", ".join(f"{k} {r[k]:+.3f}" for k in keys))
                continue
            s = Session(cx, seed, plastic=False)
            s.wait(500)
            pre = s.test(a, b, a_first=(seed % 2 == 0))
            n = 0
            if arm != "control":
                share = s.mb.D[:, np.isin(s.mb.dan, s.ppl1)].sum(axis=1)
                c = pre["A"]["kc_count"]
                active = c > 0
                med = np.median(c[active])
                kc_set = {"all": active, "strong": c >= med, "weak": active & (c < med)}[arm]
                hit = kc_set[s.mb.edge_kc] & (share[s.mb.edge_mbon] > 0.5)
                s.mb.m[hit] = s.mb.p.floor
                s.b.w_mv[s.mb.edges] = s.mb.w0 * s.mb.m
                n = int(hit.sum())
            post = s.test(a, b, a_first=(seed % 2 == 0))
            res[arm]["floored"].append(n)
            for k in keys:
                res[arm][k].append(float(learned(pre, post, k)))
            if checkpoint:
                rec = {"seed": seed, "arm": arm, "floored": n,
                       **{k: res[arm][k][-1] for k in keys}}
                with open(checkpoint, "a") as f:
                    f.write(json.dumps(rec) + "\n"); f.flush(); os.fsync(f.fileno())
            log(f"  seed {seed} {arm:7s}: floored {n:5d}, " + ", ".join(f"{k} {res[arm][k][-1]:+.3f}" for k in keys))

    log("\nREPLICATION (seeds %d-%d; paired against control by seed)" % (min(seeds), max(seeds)))
    stats = {}
    for arm in arms[1:]:
        for k, label in (("mdn", "moonwalker MDN (Hz)"), ("dn", "descending drive (Hz)"), ("turn", "turn index")):
            d = np.array(res[arm][k]) - np.array(res["control"][k])
            sd = d.std(ddof=1)
            t = d.mean() / (sd / np.sqrt(len(d))) if sd > 0 else 0.0
            stats[(arm, k)] = (d.mean(), sd, t, int((d > 0).sum()))
            tag = "PRIMARY  " if (arm, k) == ("all", "mdn") else "secondary"
            log(f"  {tag} {arm:7s} {label:24s} {d.mean():+7.3f} ± {sd:5.3f}  t={t:+5.2f}  "
                f"{int((d > 0).sum())}/{len(d)} positive  (mean floored {np.mean(res[arm]['floored']):.0f})")
    m, sd, t, _ = stats[("all", "mdn")]
    verdict = "REPLICATES" if (t >= 2.262 and m > 0) else "DOES NOT REPLICATE"
    log(f"\nPrimary verdict (t >= 2.262 and positive): {verdict}  (mean {m:+.3f} Hz, t={t:+.2f})")
    return res, verdict



# --- which descending neurons carry the memory? ---------------------------------------
DN_CHANNEL_K = 10


def dn_channel(cx, discovery=range(20, 30), validation=range(30, 40), eta=3.0,
               outdir=None, log=print):
    """Find the descending neurons that carry an aversive memory, then test them
    on seeds that played no part in choosing them.

    Wiring analysis showed the turn neurons the game reads (DNp15, DNg41) get
    ~0.1% of the memory's influence on descending neurons, which instead
    spreads thinly across ~1,100 of them. So look at all 1,303.

      DISCOVERY   seeds 20-29, train odour A + punishment (eta 3, which leaves
                  the punished synapses at ~0.16) vs the frozen control; for
                  every DN, the paired learned change in its rate. Choose the
                  10 with the largest |t| (and |mean| >= 0.5 Hz), with signs.
      VALIDATION  seeds 30-39, same protocol. For each seed, the composite
                  = mean over the 10 of sign * paired change. Counts only if
                  t >= 2.262 (two-sided p < 0.05, df = 9) and positive.

    With 1,303 candidates, discovery will find some by chance; that is why the
    decision is made on fresh seeds only. Sessions are saved one file each
    (atomic rename), so a power cut costs one session.
    """
    (ga, a), (gb, b), _ = odours(cx, seed=7)
    outdir = outdir or os.path.join(os.path.dirname(LEARNING_RAW), "experiments", "dn_channel")
    os.makedirs(outdir, exist_ok=True)

    def session(seed, arm):
        path = os.path.join(outdir, f"seed{seed}_{arm}.npy")
        if os.path.exists(path):
            return np.load(path)
        lp = LearningParams(eta=eta) if arm == "learn" else None
        s = Session(cx, seed, plastic=(arm == "learn"), lp=lp)
        pre, post = s.run(a, b, a_first=(seed % 2 == 0))
        vec = learned(pre, post, "dn_vec").astype(np.float32)
        tmp = path + ".tmp.npy"
        with open(tmp, "wb") as f:
            np.save(f, vec); f.flush(); os.fsync(f.fileno())
        os.replace(tmp, path)
        return vec

    def diffs(seeds, phase):
        rows = []
        for seed in seeds:
            d = session(seed, "learn") - session(seed, "control")
            rows.append(d)
            log(f"  {phase} seed {seed}: done ({len(rows)}/{len(seeds)})")
        return np.array(rows)

    dn = cx.select(super_class="descending")
    ct, hb, side = cx.labels["cell_type"], cx.labels["hemibrain_type"], cx.labels["side"]
    names = [f"{ct[i] or hb[i] or '?'}_{side[i][:1]}" for i in dn]

    log("DISCOVERY")
    Dd = diffs(discovery, "discovery")
    m, sd = Dd.mean(0), Dd.std(0, ddof=1)
    t = np.where(sd > 0, m / (sd / np.sqrt(len(Dd))), 0.0)
    ok = np.abs(m) >= 0.5
    order = np.argsort(-np.abs(np.where(ok, t, 0.0)))[:DN_CHANNEL_K]
    signs = np.sign(m[order])
    log(f"\n  chosen {DN_CHANNEL_K} (of {len(dn)}; {int((np.abs(t) >= 3).sum())} had |t| >= 3 in discovery):")
    for j in order:
        log(f"    {names[j]:14s} {m[j]:+6.2f} Hz  t={t[j]:+5.1f}")
    np.save(os.path.join(outdir, "chosen.npy"), np.stack([order, signs]))

    log("\nVALIDATION (fresh seeds; decision rule fixed above)")
    Dv = diffs(validation, "validation")
    comp = (Dv[:, order] * signs).mean(1)
    csd = comp.std(ddof=1)
    ct_ = comp.mean() / (csd / np.sqrt(len(comp))) if csd > 0 else 0.0
    log(f"\n  per-neuron in validation:")
    vm = Dv[:, order].mean(0)
    for j, v, sg in zip(order, vm, signs):
        log(f"    {names[j]:14s} discovery {m[j]:+6.2f}  validation {v:+6.2f}  {'same sign' if np.sign(v) == sg else 'FLIPPED'}")
    verdict = "VALIDATED" if (ct_ >= 2.262 and comp.mean() > 0) else "NOT VALIDATED"
    log(f"\n  composite: {comp.mean():+.3f} ± {csd:.3f} Hz, t={ct_:+.2f}, "
        f"{int((comp > 0).sum())}/{len(comp)} seeds positive  ->  {verdict}")
    return order, signs, verdict
