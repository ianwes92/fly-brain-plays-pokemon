"""Naive vs trained fly. Design, measures and rules are fixed in
naive_trained_PREREG.md (written before any data). This file only runs it.

ethogram_now.run() unchanged, with the mushroom body's weights set right after
calibration: reset (naive) or loaded from the pre-regime snapshot (trained).
Also logged per run: the opinions at the start of play (manipulation check)
and the mean mood valence over sampled overworld frames. Resumable."""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.expanduser("~/flybrain"))
# NT_OUT / NT_TRAINED: the standing per-run battery (battery.py) reuses this
# harness unchanged on each run's end-of-run brain; the defaults are the
# 2026-10-09 run's, as preregistered.
OUT = os.environ.get("NT_OUT") or os.path.join(HERE, "naive_trained.jsonl")
TRAINED = os.environ.get("NT_TRAINED") or os.path.expanduser(
    "~/flybrain/data/learning/pre-regime-20261007-164515.npz")
SEEDS = range(12)
ARMS = ("naive", "trained")


def done():
    if not os.path.exists(OUT):
        return set()
    return {(r["seed"], r["arm"]) for r in map(json.loads, filter(str.strip, open(OUT)))}


def run_one(seed, arm):
    import numpy as np
    from flybrain import game
    import ethogram_now as eth
    eth.SCRATCH = f"/tmp/claude-1000/naive_trained_{arm}"
    orig_cal, orig_frame = game.Game.calibrate, game.Game._game_frame
    probe = {"valence": [], "start": None, "opp_any": 0, "opp_people": 0, "opp_exits": 0}

    def calibrate(self, *a, **k):
        out = orig_cal(self, *a, **k)
        L = self.learning
        L.reset()
        if arm == "trained":
            z = np.load(TRAINED)
            L.m[:] = z["m"].astype(np.float32)
            L.brain.w_mv[L.edges] = L.w0 * L.m
        L.version += 1
        probe["depressed"] = float((1.0 - L.m).sum())
        return out

    def frame(self):
        orig_frame(self)
        m = self.mood
        if m is None or self.learning is None:
            return
        if probe["start"] is None and m.signatures is not None:
            probe["start"] = dict(m.opinions(self.learning, m.signatures))
        if self.in_overworld():
            probe["valence"].append(m.valence)
            lv = self.nose.level if self.nose is not None else {}
            p_, e_ = lv.get("people", 0.0) >= 0.5, lv.get("exits", 0.0) >= 0.5
            probe["opp_people"] += p_
            probe["opp_exits"] += e_
            probe["opp_any"] += p_ or e_

    game.Game.calibrate, game.Game._game_frame = calibrate, frame
    try:
        row = eth.run(seed, "fly")
    finally:
        game.Game.calibrate, game.Game._game_frame = orig_cal, orig_frame
    v = probe["valence"]
    s = row["stats"]
    row.update({k: s.get(k, 0) for k in ("toward_exit", "toward_person",
                                         "away_from_exit", "away_from_person")})
    row.update(opportunities=probe["opp_any"], opp_people=probe["opp_people"],
               opp_exits=probe["opp_exits"])
    row.update(arm=arm, depressed=round(probe.get("depressed", 0.0), 3),
               opinions_start=probe["start"],
               mean_valence=float(np.mean(v)) if v else None, valence_samples=len(v),
               attraction_turns=sum(s.get(k, 0) for k in ("toward_exit", "toward_person",
                                                         "away_from_exit", "away_from_person")))
    return row


def main():
    have = done()
    for seed in SEEDS:
        for arm in ARMS:
            if (seed, arm) in have:
                continue
            row = run_one(seed, arm)
            with open(OUT, "a") as f:
                f.write(json.dumps(row) + "\n")
            op = row["opinions_start"] or {}
            print(f"seed {seed} {arm}: attraction {row['attraction_turns']}, moves {row['moves']}, "
                  f"valence {row['mean_valence']}, opinions {op}", flush=True)


if __name__ == "__main__":
    main()
