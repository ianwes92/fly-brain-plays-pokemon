"""What triggers avoidance turns? For each one, record what was actually around the player."""
import sys; sys.path.insert(0, "~/flybrain")
import numpy as np
from flybrain.connectome import Connectome
from flybrain import game
seed = int(sys.argv[1]) if len(sys.argv) > 1 else 0
cx = Connectome()
g = game.Game(cx, *game.default_paths(), seed=seed)
g.calibrate(verbose=True)
h, e = g.hands, g.eyes
rows, last = [], h.stats.get("avoid", 0)
end = g.brain.t_ms + 25000
while g.brain.t_ms < end:
    hb, grid = h.heading, e.grid
    g.step()
    if h.stats.get("avoid", 0) > last:
        last = h.stats["avoid"]
        if grid is not None:
            r = grid.shape[0] // 2
            _, hx, hy, _ = game.HEADINGS[hb]; lx, ly = hy, -hx
            at = lambda f, l: bool(grid[r + hy * f + ly * l, r + hx * f + lx * l])
            rows.append(dict(ahead=at(1, 0), diag=at(1, 1) or at(1, -1), beside=at(0, 1) or at(0, -1),
                             right=(h.heading - hb) % 4 == 1, seen=e.seen, walking=h.walking))
n = len(rows)
print(f"\n{n} avoidance turns in 25 s (threshold ±{h.obstacle_thresh:.1f} Hz)")
if n:
    near = sum(r["ahead"] or r["diag"] or r["beside"] for r in rows)
    print(f"  something near: {near} ({100*near/n:.0f}%)   way ahead blocked: {sum(r['ahead'] for r in rows)}   "
          f"nothing near: {n-near} ({100*(n-near)/n:.0f}%)   while standing: {sum(not r['walking'] for r in rows)}")
    lop = [r for r in rows if r["seen"][0] != r["seen"][1]]
    print(f"  turned away from the more-obstructed eye: {sum(r['right'] == (r['seen'][0] > r['seen'][1]) for r in lop)}/{len(lop)}")
print(f"tiles moved {g.steps_moved}, distinct {len(g.visited)}, all turns {h.stats['turns']}")
g.close()
