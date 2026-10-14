"""The fly's history has to survive a restart and read like a history.

A viewer arriving mid-stream should be able to tell whether this is minute
three or hour forty. What is checked: entries persist, the file surviving a
power cut leaves the old one whole, damage costs nothing, and the fly does not
fill its own channel with repeats.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.diary import KEEP, POST_EVERY_S, Diary                # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


class Clock:
    t = 1_000_000.0

    def __call__(self):
        return self.t


tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "sub", "diary.json")      # a directory that does not exist yet
c = Clock()

print("1. it remembers")
d = Diary(path, clock=c)
check("a fresh fly has no past", d.entries == [] and d.summary() is None)
d.add("caught", "caught a RATTATA")
c.t += 300
d.add("badge", "earned the BOULDER badge")
again = Diary(path, clock=c)
check("the history survives a restart", len(again.entries) == 2, str(len(again.entries)))
check("in the order it happened",
      [e[2] for e in again.entries] == ["caught a RATTATA", "earned the BOULDER badge"])

print("2. it reads like a history")
r = again.recent(2)
check("newest first", r[0][2] == "earned the BOULDER badge", str(r[0]))
check("with how long ago", r[1][0] == "5m ago", str(r[1][0]))
check("ago in words", (Diary.ago(10), Diary.ago(600), Diary.ago(7200), Diary.ago(200000))
      == ("just now", "10m ago", "2h ago", "2d ago"))
s = again.summary()
check("the catch-up line names what happened", "1 caught" in s and "1 badge" in s, s)

print("3. it does not repeat itself")
d2 = Diary(os.path.join(tmp, "b.json"), clock=c)
check("first time is news", d2.add("level", "WARTORTLE reached level 26") is True)
check("the same thing again is not", d2.add("level", "WARTORTLE reached level 26") is False)
check("something else is", d2.add("level", "RATTATA reached level 7") is True)
check("and blank is never news", d2.add("level", "   ") is False)

print("4. it tells chat rarely, and only with news")
d3 = Diary(os.path.join(tmp, "c.json"), clock=c)
check("nothing to say at first", d3.due_to_post() is False)
d3.add("caught", "caught a PIDGEY")
check("news, but not yet due", d3.due_to_post() is True)     # first post allowed
check("and not twice in a row", d3.due_to_post() is False)
c.t += POST_EVERY_S + 1
check("time passed but no new news, so still quiet", d3.due_to_post() is False)
d3.add("badge", "earned a badge")
check("new news after the gap: it speaks", d3.due_to_post() is True)

print("5. the file behaves")
big = Diary(os.path.join(tmp, "d.json"), clock=c)
for i in range(KEEP + 20):
    big.add("level", f"levelled to {i}")
check(f"kept to {KEEP} entries", len(big.entries) == KEEP, str(len(big.entries)))
check("keeping the newest", big.entries[-1][2] == f"levelled to {KEEP + 19}")
check("no half-written file left behind", not os.path.exists(big.path + ".tmp"))
check("saved private", oct(os.stat(big.path).st_mode & 0o777) == "0o600",
      oct(os.stat(big.path).st_mode & 0o777))
bad = os.path.join(tmp, "bad.json")
open(bad, "w").write("{not json")
check("a damaged diary just means no past", Diary(bad, clock=c).entries == [])
open(bad, "w").write('{"entries": [["x", 1, 2], [1.0, "caught", "a fly"], "junk"]}')
check("and junk rows are dropped, good ones kept",
      [e[2] for e in Diary(bad, clock=c).entries] == ["a fly"])

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
