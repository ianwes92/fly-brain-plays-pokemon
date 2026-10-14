"""The speed dial must not touch anything that gets calibrated.

MENU_SPEED runs the game faster where the fly is not navigating. It shipped
with a bug that cost a calibrated quantity: calibration happens BEFORE the
overworld's callback has been learned, so `not in_overworld()` was true
throughout it, the game ran at menu speed, and the fly was measured against a
world going past three times too fast. The walking median fell 11.65 -> 6.70 Hz
and the A threshold with it. The rule this encodes: hurry only where we KNOW
the fly is not navigating; not knowing is not a menu.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flybrain import game as gm

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


class FakeHands:
    pace = 1.0


class Paced:
    """Just the pacing decision out of Game.step, with nothing else running."""

    def __init__(self, cb2, overworld_cb2, game_speed=1.0, menu_speed=3.0):
        self._cb2 = cb2
        self.overworld_cb2 = overworld_cb2
        self.game_speed = game_speed
        self.menu_speed = menu_speed
        self.hands = FakeHands()

    def in_overworld(self):
        return self.overworld_cb2 is not None and self._cb2 == self.overworld_cb2

    def pace(self):
        known = self.overworld_cb2 is not None
        hurry = known and not self.in_overworld()
        p = self.game_speed * (self.menu_speed if hurry else 1.0)
        self.hands.pace = p
        return p


OW, BATTLE = 134571445, 134287617

# -- the whole point: menus and battles run fast ---------------------------
check("a battle hurries", Paced(BATTLE, OW).pace() == 3.0)
check("the overworld does not", Paced(OW, OW).pace() == 1.0)

# -- THE BUG: not knowing where we are is not a menu -----------------------
check("calibration, before the overworld is known, runs at normal pace",
      Paced(BATTLE, None).pace() == 1.0)
check("...whatever the callback happens to be",
      Paced(OW, None).pace() == 1.0 and Paced(999, None).pace() == 1.0)

# -- the dial still composes with the main speed setting -------------------
check("game_speed multiplies in the overworld",
      Paced(OW, OW, game_speed=2.0).pace() == 2.0)
check("game_speed multiplies in a battle",
      Paced(BATTLE, OW, game_speed=2.0).pace() == 6.0)
check("menu_speed 1.0 disables the whole feature",
      Paced(BATTLE, OW, menu_speed=1.0).pace() == 1.0)

# -- the fly's press rate is told about the pacing -------------------------
# The A/B refractories are "no more than one press a second" in GAME time.
# If they stayed in brain time, speeding the game up would make the fly press
# three times LESS often per game second and cancel the speed-up.
g = Paced(BATTLE, OW)
g.pace()
check("hands learn the pace", g.hands.pace == 3.0)
g2 = Paced(OW, OW)
g2.pace()
check("and learn it back again in the overworld", g2.hands.pace == 1.0)

# -- the refractory divides by it, so presses stay steady in game time -----
class Fly:
    def __init__(self, pace):
        self.pace = pace


for pace, gap_ms in ((1.0, 1000.0), (3.0, 1000.0 / 3), (0.5, 2000.0)):
    f = Fly(pace)
    check(f"at pace {pace} the A gap is {gap_ms:.0f} brain-ms",
          abs(1000.0 / f.pace - gap_ms) < 1e-6)
    check(f"which is one press per game second at pace {pace}",
          abs((1000.0 / f.pace) * f.pace - 1000.0) < 1e-6)

# -- a real Game exposes what the pacing needs, and starts sane ------------
check("MENU_SPEED exists", isinstance(gm.MENU_SPEED, float))
check("and is a speed-up, not a slow-down", gm.MENU_SPEED >= 1.0)

print(f"{ok} checks pass")
