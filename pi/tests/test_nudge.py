"""Chat's presses must not switch the fly off.

The anarchy window hands the controls to everyone for 20 s. If those presses
were takeovers, the fly would stop driving for the whole window and chat would
be playing alone -- but working WITH the animal is the point. So an anarchy
press is OR'd into whatever the fly is pressing that frame, and only bought
control replaces it. This drives the real Game frame path and reads the key
mask actually handed to the emulator.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import game as gm                              # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402
from flybrain.engine import Params                           # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


rom, sav, state = gm.default_paths()
tmp = tempfile.mkdtemp()
work = os.path.join(tmp, "t.sav")
shutil.copyfile(sav, work)                      # never touch the live save
cx = Connectome()
g = gm.Game(cx, rom, work, state, params=Params(al_ln_inhibitory=True), seed=0)

# The fly "decides" to walk UP every frame, so what reaches the emulator says
# exactly whether its own decision survived.
FLY = gm.UP
g.hands.decide = lambda t_ms: FLY
seen = []
real_frame = g.gba.frame
g.gba.frame = lambda keys: (seen.append(keys), real_frame(keys))[1]

def frames(n):
    """Step until n emulator frames have been handed keys (they come about
    every 16 ms of brain time, so stepping 6 times sees none at all -- an
    empty list would make every all(...) below pass vacuously)."""
    seen.clear()
    for _ in range(4000):
        if len(seen) >= n:
            break
        g.step()
    return list(seen)


print("1. the fly alone")
got = frames(5)
check("its own key reaches the game", len(got) >= 5 and all(k == FLY for k in got), str(set(got)))

print("2. a chat nudge is ADDED to the fly, not instead of it")
g.nudge("left", 300.0)
during = frames(5)          # the press applies from the next frame on
check("frames were actually seen during the nudge", len(during) >= 5, f"{len(during)} frames")
check("chat's key is pressed", during and all(k & gm.LEFT for k in during), str(set(during)))
check("and the fly is STILL pressing its own key at the same time",
      during and all(k & FLY for k in during), str(set(during)))
check("so the game sees both at once", during and set(during) == {FLY | gm.LEFT},
      str(set(during)))
check("the fly is marked as nudged, not taken over", g.nudged and not g.taken_over)

print("3. it wears off")
while g.nudged:
    g.step()
after = frames(5)
check("the fly has the controls back to itself",
      len(after) >= 5 and all(k == FLY for k in after), str(set(after)))

print("4. bought control still means exclusive control")
g.takeover("right", 300.0)
paid = frames(5)
check("the payer's key replaces the fly's",
      len(paid) >= 5 and all(k == gm.RIGHT for k in paid), str(set(paid)))
check("and the fly is marked as taken over", g.taken_over)
g.close()

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
