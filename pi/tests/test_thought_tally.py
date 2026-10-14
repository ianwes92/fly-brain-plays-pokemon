"""ThoughtTally: kinds ignore side, rates per minute overall and last 10 min."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.playview import ThoughtTally
ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1
K = ThoughtTally.kind
check("side ignored", K("wall on the left - turning away") == K("wall on the right - turning away")
      == "wall_on_the_X_turning_away")
check("door vs person keep apart", K("a door on the left - keeping away") != K("someone on the left - keeping away"))
check("plain", K("wandering") == "wandering")
t = ThoughtTally()
check("empty line", t.line(0) == "thoughts: total=0 per_min=- per_min_10m=-")
for i in range(120):
    t.add("wandering" if i % 2 else "wall ahead - turning", i * 10.0)    # 6/min for 20 min
line = t.line(1200.0)
check("total", "total=120" in line)
check("rate overall", "per_min=6.0" in line)
check("rate 10 min", "per_min_10m=6.0" in line)
check("kinds", "wandering=60" in line and "wall_ahead_turning=60" in line)
print(f"test_thought_tally: {ok} checks passed")
