"""obstacle_view geometry: left and right must be the player's own, whichever way it faces."""
import os, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.game import obstacle_view, HEADINGS

ok = True
def check(name, cond, extra=""):
    global ok; ok &= bool(cond); print(f"  {'PASS' if cond else 'FAIL'}  {name} {extra}")

R = 3
def grid_with(*cells):
    g = np.zeros((2 * R + 1, 2 * R + 1), dtype=bool)
    for x, y in cells:                    # screen offsets from the player
        g[R + y, R + x] = True
    return g

names = {0: "up", 1: "right", 2: "down", 3: "left"}
print("1. open ground: nothing seen")
for h in range(4):
    l, r = obstacle_view(grid_with(), h)
    check(f"facing {names[h]}", l == 0 and r == 0, f"({l:.2f}, {r:.2f})")

print("2. a wall dead ahead is seen equally by both eyes")
for h in range(4):
    _, hx, hy, _ = HEADINGS[h]
    l, r = obstacle_view(grid_with((hx, hy)), h)
    check(f"facing {names[h]}", l > 0 and abs(l - r) < 1e-9, f"({l:.2f}, {r:.2f})")

print("3. an obstacle ahead-left is on the LEFT eye, in the player's own frame")
# screen positions of 'ahead-left' for each heading, worked out by hand:
#   facing up: up-left (-1,-1); right: up-right (1,-1); down: down-right (1,1); left: down-left (-1,1)
ahead_left = {0: (-1, -1), 1: (1, -1), 2: (1, 1), 3: (-1, 1)}
for h, cell in ahead_left.items():
    l, r = obstacle_view(grid_with(cell), h)
    check(f"facing {names[h]}: {cell}", l > 0 and r == 0, f"({l:.2f}, {r:.2f})")

print("4. and beside-right is on the RIGHT eye")
beside_right = {0: (1, 0), 1: (0, 1), 2: (-1, 0), 3: (0, -1)}
for h, cell in beside_right.items():
    l, r = obstacle_view(grid_with(cell), h)
    check(f"facing {names[h]}: {cell}", r > 0 and l == 0, f"({l:.2f}, {r:.2f})")

print("5. nearer counts more than farther; fully boxed in reads 1.0")
near = obstacle_view(grid_with((0, -1)), 0)[0]
far = obstacle_view(grid_with((0, -2)), 0)[0]
check("ahead 1 > ahead 2", near > far, f"{near:.2f} > {far:.2f}")
boxed = obstacle_view(np.ones((7, 7), dtype=bool), 0)
check("everything solid -> (1, 1)", np.allclose(boxed, (1, 1)), f"{boxed}")

print("6. doors far away are seen, on the correct eye, nearer ones stronger")
from flybrain.game import target_view
R7 = 7
def far(*cells):
    g = np.zeros((2 * R7 + 1, 2 * R7 + 1), dtype=bool)
    for x, y in cells: g[R7 + y, R7 + x] = True
    return g
l, r = target_view(far(), 0)
check("nothing -> (0, 0)", l == 0 and r == 0)
l, r = target_view(far((0, -6)), 0)
check("door 6 tiles straight ahead: both eyes, equally", l > 0 and abs(l - r) < 1e-9, f"({l:.2f}, {r:.2f})")
l, r = target_view(far((-5, -2)), 0)
check("door ahead-left 5,2 away (facing up): left eye only", l > 0 and r == 0, f"({l:.2f}, {r:.2f})")
for h, cell in {1: (3, -4), 2: (4, 3), 3: (-3, 4)}.items():   # ahead-left for right/down/left
    l, r = target_view(far(cell), h)
    check(f"ahead-left while facing {names[h]}: left eye", l > 0 and r == 0, f"({l:.2f}, {r:.2f})")
near = target_view(far((-2, 0)), 0)[0]; farther = target_view(far((-6, 0)), 0)[0]
check("nearer door pulls harder", near > farther, f"{near:.2f} > {farther:.2f}")
front = target_view(far((-3, -2)), 0)[0]; behind = target_view(far((-3, 2)), 0)[0]
check("same distance behind counts less", behind < front, f"{behind:.2f} < {front:.2f}")
beyond = target_view(far((0, -7), (7, 1)), 0)
check("beyond reach is ignored", target_view(far((7, 1)), 0) == (0.0, 0.0))

print("7. ahead_view: how much lies straight ahead (ground truth for the lock)")
from flybrain.game import ahead_view
check("nothing -> 0", ahead_view(far(), 0) == 0.0)
check("door straight ahead is seen", ahead_view(far((0, -4)), 0) > 0)
check("door directly behind is not", ahead_view(far((0, 4)), 0) == 0.0)
check("door directly beside is not", ahead_view(far((4, 0)), 0) == 0.0)
straight = ahead_view(far((0, -4)), 0); diag = ahead_view(far((3, -3)), 0)
check("lined up beats off to the side", straight > diag, f"{straight:.2f} > {diag:.2f}")
close = ahead_view(far((0, -1)), 0); away = ahead_view(far((0, -6)), 0)
check("nearer counts more", close > away, f"{close:.2f} > {away:.2f}")
for h, cell in {0: (0, -4), 1: (4, 0), 2: (0, 4), 3: (-4, 0)}.items():
    check(f"straight ahead while facing {names[h]}", ahead_view(far(cell), h) > 0)
check("beyond reach is ignored", ahead_view(far((0, -9)), 0) == 0.0)
two = ahead_view(far((0, -3), (0, -5)), 0); one = ahead_view(far((0, -3)), 0)
check("two doors ahead beat one", two > one, f"{two:.2f} > {one:.2f}")
check("stays within 0..1", 0.0 <= ahead_view(far(*[(0, -d) for d in range(1, 8)]), 0) <= 1.0)

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
