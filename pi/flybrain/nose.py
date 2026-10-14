"""Smell: each kind of thing in the game gives off its own scent.

Why a nose, in a game with no smell: the fly learns what is good or bad
through its mushroom body, and 77% of the Kenyon cells' input is olfactory
(2.9% visual). Driving people, doors and walls into the visual channels, the
mushroom body learned from pleasure and pain but could not tell the things
apart -- KC patterns people-walls r=0.97, and pain paired with people soured
exits and walls just as much (thing_learning.jsonl, specificity t=-0.93).
Flies learn about odours, so here things have odours.

Each thing type drives its own disjoint pair of antennal-lobe glomeruli
through their uniglomerular projection neurons. Glomeruli with known innate
valence (geosmin, CO2, pheromones, vinegar and fruit, stress, humidity and
temperature) are excluded, so the fly starts with no opinion about any scent
and whatever it comes to like or fear is learned. The same selection is used
in data/experiments/thing_learning_scent.py, which is where it was tested.

Walls have no scent. They did, and it backfired: indoors the wall scent sits
at full strength all the time while a person two tiles off smelled at 0.2-0.4,
so pain pressed beside a person was blamed on the walls -- the fly ended up
fearing walls and LIKING people (opinion walls -0.95, people +0.83). That is
overshadowing, and real flies do it too: the strongest cue present takes the
association. Walls are everywhere and already avoided by sight, so only the
things worth training on -- people and doors -- smell.

Concentration falls off with distance -- each source adds 1/(1+d) within
REACH tiles -- steeply enough that a person beside the fly smells strongly
(~0.8 at 1 tile, ~0.6 at 2) rather than faintly, and is not lateralised: fly antennae are too close together to smell a
direction in a grid this coarse, so a scent tells the fly what is near, not
where. Where comes from vision, or from walking and noticing it getting
stronger or weaker.
"""
import numpy as np

INNATE = {"DA2", "V", "DA1", "VA1v", "VA1d", "VL2a", "DL3", "DM1", "DM2", "DM4",
          "DP1m", "VA2", "VM7d", "VM7v", "DL4", "DL5", "DC1", "VA6", "DA4l", "DA4m"}
REACH = 5
PEAK_HZ = 60.0
STEEPNESS = 2.5


def neutral_glomeruli(cx):
    """Uniglomerular projection neurons of glomeruli with no known innate
    valence, grouped by glomerulus and ranked by how many PNs each has."""
    L = cx.labels
    name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"])
    cl = np.asarray(L["class"]).astype(str)
    by = {}
    for i in np.flatnonzero(np.char.lower(cl) == "alpn"):
        t = str(name[i])
        if "_" not in t or not t.endswith("PN") or t.startswith(("M_", "CB")):
            continue
        g = t.split("_")[0]
        if g in INNATE or g.startswith(("VP", "V_")) or "+" in g:
            continue
        by.setdefault(g, []).append(int(i))
    ranked = sorted(by, key=lambda g: -len(by[g]))
    return by, ranked


class Nose:
    KINDS = ("people", "exits")

    def __init__(self, cx):
        by, ranked = neutral_glomeruli(cx)
        # The same glomeruli the specificity test used for people and exits.
        pick = ranked[:6]
        self.glomeruli = {"people": (pick[0], pick[3]), "exits": (pick[1], pick[4])}
        self.pn = {k: np.array(by[a] + by[b], dtype=np.int64)
                   for k, (a, b) in self.glomeruli.items()}
        self.level = {k: 0.0 for k in self.KINDS}

    def sniff(self, grid, codes):
        """Concentration of each scent from a scene grid centred on the fly.
        codes: {kind: grid value} e.g. {"people": GBA.PERSON, ...}."""
        r = grid.shape[0] // 2
        ys, xs = np.mgrid[:grid.shape[0], :grid.shape[1]]
        dist = np.abs(ys - r) + np.abs(xs - r)
        near = (dist > 0) & (dist <= REACH)
        w = np.where(near, 1.0 / (1.0 + dist), 0.0)
        for kind, code in codes.items():
            self.level[kind] = float(1.0 - np.exp(-STEEPNESS * (w * (grid == code)).sum()))
        return self.level

    def drive(self, brain):
        """Poisson-drive each scent's projection neurons, applied next step."""
        for kind, idx in self.pn.items():
            hz = PEAK_HZ * self.level[kind]
            if hz > 0.5:
                brain.drive(idx, hz)


def kc_signatures(cx, path=None, seeds=(0, 1, 2), log=print, params=None):
    """Each scent's Kenyon cell response: extra KC spikes per second when that
    scent alone is smelled at full strength, averaged over a few seeds.

    This is what lets the brain be asked "how do you feel about people?" while
    no person is near: the learned valence of a scent is the valence of the
    Kenyon cells it drives. Measured in throwaway brains so the live one is not
    disturbed, and cached, since it depends only on the connectome.
    """
    import os
    from .connectome import DATA
    from .engine import Brain, Params
    params = params or Params()
    # Signatures depend on the model settings that shape Kenyon cell responses,
    # so each setting gets its own cache.
    tag = "_alln" if params.al_ln_inhibitory else ""
    path = path or os.path.join(DATA, f"scent_kc{tag}.npz")
    kc = np.flatnonzero(np.asarray(cx.labels["class"]) == "Kenyon_Cell")
    if os.path.exists(path):
        z = np.load(path)
        if len(z["kc"]) == len(kc) and "background" in z.files and \
                all(k in z.files for k in Nose.KINDS):
            return {k: z[k] for k in Nose.KINDS + ("background",)}
    nose = Nose(cx)
    pos = np.full(cx.n, -1, dtype=np.int64); pos[kc] = np.arange(len(kc))
    total = {k: np.zeros(len(kc)) for k in Nose.KINDS}
    background = np.zeros(len(kc))
    log(f"measuring scent signatures in the Kenyon cells ({len(seeds)} seeds, once) ...")
    for seed in seeds:
        b = Brain(cx, params, seed=seed)
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

        base = count(None)
        background += base
        for k in Nose.KINDS:
            total[k] += np.maximum(count(nose.pn[k]) - base, 0.0)
    sig = {k: v / len(seeds) for k, v in total.items()}
    # What the Kenyon cells do while smelling nothing: the reference each
    # scent's opinion is judged against (mood.opinions).
    sig["background"] = background / len(seeds)
    np.savez(path, kc=kc, **sig)
    return sig
