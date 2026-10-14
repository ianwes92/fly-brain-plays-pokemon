"""What triggers turns from the older (DNp15/DNg41) steering signal?"""
import sys; sys.path.insert(0, "~/flybrain")
import numpy as np
from flybrain.connectome import Connectome
from flybrain import game
cx = Connectome()
g = game.Game(cx, *game.default_paths(), seed=int(sys.argv[1]) if len(sys.argv) > 1 else 0)
g.calibrate(verbose=True)
h, e = g.hands, g.eyes
turns, samples = [], []
last_turns, last_avoid = h.stats["turns"], h.stats.get("avoid", 0)
end = g.brain.t_ms + 25000
while g.brain.t_ms < end:
    lat_recent = abs(e.lat)
    g.step()
    if g.brain.steps % 17 == 0:
        L = g.brain.rate[h.turn_l].sum(); R = g.brain.rate[h.turn_r].sum()
        samples.append((L, R, abs(e.lat) > 0, h.walking))
    if h.stats["turns"] > last_turns:
        avoid = h.stats.get("avoid", 0) > last_avoid
        last_turns, last_avoid = h.stats["turns"], h.stats.get("avoid", 0)
        if not avoid:
            L = g.brain.rate[h.turn_l].sum(); R = g.brain.rate[h.turn_r].sum()
            turns.append(dict(lat=lat_recent > 0, walking=h.walking, L=L, R=R, ratio=h.turn))
n = len(turns)
print(f"\n{n} turns from the older steering signal in 25 s")
if n:
    print(f"  with any sideways motion: {sum(t['lat'] for t in turns)}   while standing: {sum(not t['walking'] for t in turns)}")
    LR = np.array([(t['L'], t['R']) for t in turns])
    print(f"  firing at the moment of the turn: left {np.median(LR[:,0]):.1f} Hz, right {np.median(LR[:,1]):.1f} Hz (median)")
S = np.array([(s[0], s[1]) for s in samples]); lat = np.array([s[2] for s in samples])
print(f"all frames: left+right median {np.median(S.sum(1)):.1f} Hz; frames with sideways motion: {int(lat.sum())}/{len(lat)}")
d = np.abs(S[:, 0] - S[:, 1])
print(f"|left-right| Hz: median {np.median(d):.1f}, p95 {np.percentile(d,95):.1f}, p99 {np.percentile(d,99):.1f}, max {d.max():.1f}")
g.close()
