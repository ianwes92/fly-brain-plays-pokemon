"""Party changes for the progress journal (2026-10-08)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.attrib import party_changes as pc

M = lambda name, sp, lv=5: {"name": name, "species": sp, "level": lv}
sq, pid, wa = M("SQUIRTLE", 7), M("PIDGEY", 16, 3), M("WARTORTLE", 8, 16)
ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1
check("first reading after a restart logs nothing", pc(None, [sq, pid]) == [])
check("no reading now logs nothing", pc([sq], None) == [])
check("the starter pick", pc([], [sq]) == [("joined", sq)])
check("a catch", pc([sq], [sq, pid]) == [("joined", pid)])
check("an evolution", pc([sq, pid], [wa, pid]) == [("changed", wa)])
check("a release", pc([sq, pid], [sq]) == [("left", pid)])
check("unchanged", pc([sq, pid], [dict(sq, level=6), pid]) == [])
print(f"test_party_changes: {ok} checks passed")
