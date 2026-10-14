"""When the fly turns toward a door or a person, is the thing where it says?

thing_reading.py measured the READOUT against truth and found it good: with a
door off to one side, the sign of Hands.exits is right 96% of the time. But
measured at the moment the DECISION fires, the side looked right only ~15-30%.
Those two cannot both be true of the same signal, so one of the measurements
is wrong -- most likely the second, which sampled truth AFTER the step, by
which time Eyes.update had replaced it with the next frame's view.

This settles it by looking inside the decision: Hands.decide is wrapped, and
the truth and the signal are read at the instant it runs, before anything
moves on. Reported per kind: how often the claimed side matches the real one,
how strong the signal was, and what the alternative (post-step) sampling says
about the very same events -- so the disagreement is measured, not argued.
"""
import os
import shutil
import sys
import tempfile

import numpy as np

sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain import game as gm                              # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402
from flybrain.engine import Params                           # noqa: E402
from flybrain.mood import Mood                               # noqa: E402
from flybrain.nose import Nose, kc_signatures                # noqa: E402

PLAY_MS = float(os.environ.get("PLAY_MS", 150_000))
STATS = ("toward_person", "away_from_person", "toward_exit", "away_from_exit")


def main():
    rom, sav, state = gm.default_paths()
    tmp = tempfile.mkdtemp()
    work = os.path.join(tmp, "t.sav")
    shutil.copyfile(sav, work)
    cx = Connectome()
    P = Params(al_ln_inhibitory=True)
    g = gm.Game(cx, rom, work, state, params=P, seed=0, learn=True)
    g.calibrate(verbose=False)
    resume = gm.resume_path_for(sav)
    if os.path.exists(resume):
        g.gba.load_state(resume)
        for _ in range(30):
            g.gba.frame(0)
    g.nose = Nose(cx)
    g.mood = Mood(cx, signatures=kc_signatures(cx, params=P, log=lambda *a: None))
    g.auto_rewards = False

    h, e = g.hands, g.eyes
    events = []
    real_decide = h.decide

    def watched(t_ms):
        before = {k: h.stats.get(k, 0) for k in STATS}
        # Truth AS THE DECISION SEES IT: eyes still hold the view the brain has
        # been responding to; Eyes.update only replaces it later this frame.
        snap = {"people": tuple(e.seen_people), "exits": tuple(e.seen_exits),
                "p_sig": float(h.people), "x_sig": float(h.exits)}
        keys = real_decide(t_ms)
        for k in STATS:
            if h.stats.get(k, 0) > before[k]:
                kind = "people" if "person" in k else "exits"
                L, R = snap[kind]
                sig = snap["p_sig"] if kind == "people" else snap["x_sig"]
                events.append({"stat": k, "kind": kind, "L": L, "R": R, "sig": sig,
                               "after_L": None, "after_R": None})
        return keys

    h.decide = watched
    end = g.brain.t_ms + PLAY_MS
    pending = []
    while g.brain.t_ms < end:
        n_before = len(events)
        g.step()
        for ev in events[n_before:]:          # the same event, sampled the OLD way
            ev["after_L"], ev["after_R"] = (e.seen_people if ev["kind"] == "people"
                                            else e.seen_exits)
    g.close()

    print(f"{len(events)} attraction decisions in {PLAY_MS/1000:.0f}s of game time")
    for key, (thr, prec, tpr, base) in sorted(h.retuned.items()):
        print(f"  {key:7s} threshold re-set to {thr:6.1f} Hz here "
              f"(thing present {100*base:4.1f}% of the time; expected to be right "
              f"{100*prec:4.0f}% of the times it fires, catching {100*tpr:4.0f}%)")
    print()
    for kind in ("people", "exits"):
        evs = [x for x in events if x["kind"] == kind]
        print(f"{kind.upper()}  ({len(evs)} decisions)")
        if not evs:
            print("  none\n")
            continue
        present = [x for x in evs if (x["L"] + x["R"]) > 0]
        print(f"  something really in view at that moment: {100*len(present)/len(evs):5.1f}%")
        if present:
            ok = [x for x in present if np.sign(x["sig"]) == np.sign(x["L"] - x["R"])]
            wrong = [x for x in present if np.sign(x["sig"]) != np.sign(x["L"] - x["R"])]
            if wrong:
                print(f"  the {len(wrong)} it got wrong, in detail:")
                for x in wrong[:8]:
                    lop = abs(x["L"] - x["R"])
                    both = min(x["L"], x["R"]) > 0
                    print(f"    truth L={x['L']:.2f} R={x['R']:.2f} (gap {lop:.2f}"
                          f"{', on BOTH sides' if both else ''}), "
                          f"readout {x['sig']:+7.1f} Hz")
            print(f"  ...and on the side the fly said:         {100*len(ok)/len(present):5.1f}%")
            strong = [x for x in present if abs(x["L"] - x["R"]) >= 0.1]
            if strong:
                ok2 = [x for x in strong if np.sign(x["sig"]) == np.sign(x["L"] - x["R"])]
                print(f"  ...of those clearly to one side:         {100*len(ok2)/len(strong):5.1f}% "
                      f"({len(strong)} decisions)")
            print(f"  signal strength at the decision: median |{np.median([abs(x['sig']) for x in present]):.0f}| Hz")
        after = [x for x in evs if x["after_L"] is not None and (x["after_L"] + x["after_R"]) > 0]
        if after:
            ok3 = [x for x in after if np.sign(x["sig"]) == np.sign(x["after_L"] - x["after_R"])]
            print(f"  the OLD way (truth read after the step): {100*len(ok3)/len(after):5.1f}% "
                  f"on {len(after)} decisions -- the number to compare")
        print()


if __name__ == "__main__":
    main()
