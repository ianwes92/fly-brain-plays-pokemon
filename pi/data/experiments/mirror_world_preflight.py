"""Gate for the mirror-world rerun (obsfly's request, 2026-10-07): before any
row is logged, the normal arm must apply the turn the brain decided and the
mirror arm the opposite one, whatever the DNp35/DNp18 readout says. Exit 1
otherwise. Checks the arms' turn application only; changes nothing."""
import os, sys
sys.path.insert(0, os.path.expanduser("~/flybrain"))
from flybrain.connectome import Connectome
from flybrain.game import Hands

cx = Connectome()
bad = 0
for arm, env in (("normal", ""), ("mirror", "1")):
    for step in (1, -1):
        for readout in (0.0, 41.0, -9.0):
            os.environ["FLYBRAIN_MIRROR"] = env
            h = Hands(cx)
            h.obstacle, h.exits, h.exit_ahead, h.obstacle_thresh = 0.0, 0.0, -1e9, 1e9
            h._attraction = lambda s=step: s
            h.mirror = readout
            h.heading, h.last_turn, h.commit_until = 0, -1e9, -1e9
            h.walk_base, h.walk, h.mdn_rate, h.mdn_base = 1.0, 0.0, 0.0, 0.0
            h.decide(10_000.0)
            applied = {1: "right", 3: "left"}.get(h.heading)
            decided = "right" if step > 0 else "left"
            good = applied == decided if arm == "normal" else applied not in (decided, None)
            bad += not good
            print(f"preflight {arm}: decided {decided}, applied {applied}, readout {readout}: "
                  f"{'OK' if good else 'FAIL'}")
os.environ.pop("FLYBRAIN_MIRROR", None)
sys.exit(1 if bad else 0)
