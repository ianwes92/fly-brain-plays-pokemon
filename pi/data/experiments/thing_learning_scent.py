"""Can the buttons teach the fly to avoid one THING rather than fear everything?

The goal is to train the fly with a pleasure and a pain button to approach or
avoid particular things. In a fly that is the mushroom body's job: the Kenyon
cells active when punishment arrives lose their drive onto approach MBONs, so
that situation turns bad. But that only singles out a THING if different
things activate different Kenyon cells. If people, doors and walls all light
up the same ones, pain teaches the fly to fear everything, and no training
will make it avoid one thing in particular.

So, in the brain alone, with the game's own channels:
  people  LC10 (the fly's pursuit channel), both eyes
  exits   LPLC4, both eyes
  walls   LPLC1, both eyes
each at 60 Hz -- the same order as play.

PROTOCOL per seed
  1. KC patterns: each thing driven alone for 1 s, Kenyon cell spikes counted,
     learning frozen. Reported as the correlation between things' KC response
     vectors -- if near 1, nothing downstream can tell them apart.
  2. Training: 6 rounds of [people 1.5 s, pain burst in its last 0.5 s] and
     [exits 1.5 s, no pain], with rests between. Learning is gated to the pain
     burst plus 0.5 s, exactly as in play.
  3. Test, frozen: each thing for 1 s, then the learned valence of what the
     Kenyon cells encode at that moment (Mood._context_valence), in fractions
     of drive lost.

PRIMARY  specificity = valence(exits) - valence(people). Pain was paired only
  with people, so if learning is specific people end up more negative and this
  is positive. One-sample t across validation seeds 400-407, t >= 2.365. Seeds
  300-303 ran first as a check that the protocol produces any learning at all;
  they do not count toward the verdict.
SECONDARY  walls, never trained, as a second untrained reference; and the
  absolute size of the people valence.
"""
import json
import os
import sys

sys.path.insert(0, "~/flybrain")
import numpy as np                                            # noqa: E402
from flybrain.connectome import Connectome                    # noqa: E402
from flybrain.engine import Brain, Params                     # noqa: E402
from flybrain.game import Eyes                                # noqa: E402
from flybrain.learning import MushroomBodyLearning            # noqa: E402
from flybrain.mood import Mood                                # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "thing_learning_scent.jsonl")
HZ = 60.0
PAIN_HZ = 80.0

cx = Connectome()
eyes = Eyes(cx)

# SCENT VERSION. The vision version (thing_learning.jsonl) learned but could not
# tell things apart: KC patterns people-walls r=0.97, specificity t=-0.93. 77%
# of the Kenyon cells' input is olfactory and 2.9% visual, so here each thing
# gets its own scent: a disjoint pair of antennal-lobe glomeruli, driven through
# their uniglomerular projection neurons. Glomeruli with known innate valence
# are excluded, so any preference has to come from training, not wiring:
# geosmin DA2, CO2 V, pheromones DA1/VA1v/VA1d/VL2a/DL3?, vinegar and fruit
# DM1/DM2/DM4/DP1m/VA2/VM7d/VM7v, stress DL4/DL5, humidity/temperature V*/VP*.
_L = cx.labels
_name = np.where(_L["cell_type"] != "", _L["cell_type"], _L["hemibrain_type"])
_cl = np.asarray(_L["class"]).astype(str)
INNATE = {"DA2", "V", "DA1", "VA1v", "VA1d", "VL2a", "DL3", "DM1", "DM2", "DM4",
          "DP1m", "VA2", "VM7d", "VM7v", "DL4", "DL5", "DC1", "VA6", "DA4l", "DA4m"}
_by_glom = {}
for _i in np.flatnonzero(np.char.lower(_cl) == "alpn"):
    _t = str(_name[_i])
    if "_" not in _t or not _t.endswith("PN") or _t.startswith(("M_", "CB")):
        continue
    _g = _t.split("_")[0]
    if _g in INNATE or _g.startswith(("VP", "V_")) or "+" in _g:
        continue
    _by_glom.setdefault(_g, []).append(_i)
_ranked = sorted(_by_glom, key=lambda g: -len(_by_glom[g]))
_pick = _ranked[:6]                           # the six best-populated neutral glomeruli
THINGS = {
    "people": np.array(_by_glom[_pick[0]] + _by_glom[_pick[3]]),
    "exits": np.array(_by_glom[_pick[1]] + _by_glom[_pick[4]]),
    "walls": np.array(_by_glom[_pick[2]] + _by_glom[_pick[5]]),
}
SCENTS = {"people": (_pick[0], _pick[3]), "exits": (_pick[1], _pick[4]),
          "walls": (_pick[2], _pick[5])}
print("scents:", {k: f"{a}+{b} ({len(THINGS[k])} PNs)" for k, (a, b) in SCENTS.items()},
      flush=True)
PPL1 = eyes.pain
mood = Mood(cx)


def run_seed(seed):
    b = Brain(cx, Params(), seed=seed)
    lr = MushroomBodyLearning(b)
    lr.attach()
    pain_until = {"t": -1e9}
    lr.gate = lambda: b.t_ms < pain_until["t"] + 500.0
    kc = lr.kc

    def hold(idx, ms, pain_last_ms=0):
        """Drive `idx` for `ms`; deliver pain in the last `pain_last_ms`."""
        counts = np.zeros(len(kc))
        start = b.t_ms
        for _ in range(int(ms)):
            if idx is not None:
                b.drive(idx, HZ)
            if pain_last_ms and b.t_ms >= start + ms - pain_last_ms:
                b.drive(PPL1, PAIN_HZ)
                pain_until["t"] = b.t_ms
            sp = b.step()
            if len(sp):
                k = lr.kc_pos[np.asarray(sp)]
                np.add.at(counts, k[k >= 0], 1.0)
        return counts

    for _ in range(300):
        b.step()

    # 1. Kenyon cell patterns, nothing learned yet.
    lr.frozen = True
    base = hold(None, 1000)
    resp = {name: hold(idx, 1000) - base for name, idx in THINGS.items()}
    lr.frozen = False
    names = list(THINGS)
    corr = {}
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            a, c = resp[names[i]], resp[names[j]]
            ok = (a.std() > 0) and (c.std() > 0)
            corr[f"{names[i]}-{names[j]}"] = float(np.corrcoef(a, c)[0, 1]) if ok else None
    active = {name: int((r > 0).sum()) for name, r in resp.items()}

    # 2. Training: people paired with pain, exits never.
    for _ in range(6):
        hold(THINGS["people"], 1500, pain_last_ms=500)
        hold(None, 1000)
        hold(THINGS["exits"], 1500)
        hold(None, 1000)

    # 3. Test, frozen.
    lr.frozen = True
    val = {}
    for name, idx in THINGS.items():
        hold(None, 1500)                     # let the previous thing's trace fade
        hold(idx, 1000)
        val[name] = mood._context_valence(lr)

    return {"seed": seed, "kc_corr": corr, "kc_active": active,
            "valence": val, "specificity": val["exits"] - val["people"],
            "mbon_strength_min": float(lr.mbon_strength().min())}


def main():
    done = set()
    if os.path.exists(OUT):
        for line in open(OUT):
            if line.strip():
                done.add(json.loads(line)["seed"])
    for seed in (300, 301, 302, 303) + tuple(range(400, 408)):
        if seed in done:
            continue
        r = run_seed(seed)
        with open(OUT, "a") as f:
            f.write(json.dumps(r) + "\n"); f.flush(); os.fsync(f.fileno())
        v = r["valence"]
        print(f"seed {seed}: KC corr {r['kc_corr']}  active {r['kc_active']}", flush=True)
        print(f"          valence people {v['people']:+.4f}  exits {v['exits']:+.4f}  "
              f"walls {v['walls']:+.4f}  -> specificity {r['specificity']:+.4f}", flush=True)


if __name__ == "__main__":
    main()
