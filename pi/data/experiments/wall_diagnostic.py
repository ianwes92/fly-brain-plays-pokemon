"""Do wall-ahead turns happen when the way ahead is actually blocked?"""
import sys; sys.path.insert(0, "~/flybrain")
from flybrain.connectome import Connectome
from flybrain import game
seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
g = game.Game(Connectome(), *game.default_paths(), seed=seed)
g.calibrate(verbose=True)
h = g.hands
rows, last = [], 0
end = g.brain.t_ms + 25000
while g.brain.t_ms < end:
    blocked_before = g.facing_solid()
    g.step()
    if h.stats.get("wall", 0) > last:
        last = h.stats["wall"]; rows.append(blocked_before)
n = len(rows)
print(f"\nseed {seed}: {n} wall-ahead turns in 25 s; way ahead actually blocked in {sum(rows)}"
      f"{f' ({100*sum(rows)/n:.0f}%)' if n else ''}")
print(f"  avoid turns {h.stats.get('avoid',0)}, all turns {h.stats['turns']}, tiles moved {g.steps_moved}, "
      f"wall bumps {g.wall_bumps}")
g.close()
