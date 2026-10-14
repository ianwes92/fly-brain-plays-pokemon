"""The leaderboard outlives a restart.

The stream restarts on its own (play/launch.sh brings it back up), and what
chat put into the fly -- presses taught, seconds driven -- should still be
there afterwards. A damaged or missing file must never stop the stream.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import twitch                                     # noqa: E402
from flybrain.twitch import PaidActions                         # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "sub", "leaderboard.json")     # a directory that does not exist yet

print("1. it survives a restart")
p = PaidActions()
p.feed(("free", "pain", "alice", "Alice"))
p.feed(("steer", 5.0, "bob", "Bob"))
p.save_board(path)
q = PaidActions()                                        # a fresh process would look like this
check("loaded", q.load_board(path) is True)
check("trainers restored", q.top("trainers") == [("Alice", 1)], str(q.top("trainers")))
check("drivers restored", q.top("drivers") == [("Bob", 5.0)], str(q.top("drivers")))

print("2. totals carry on adding up")
q.feed(("steer", 3.0, "bob", "Bob"))
check("the restart did not reset Bob", q.drivers["Bob"] == 8.0, str(q.drivers))

print("3. a bad file costs nothing")
r = PaidActions()
check("missing file -> False, not an exception", r.load_board(path + ".nope") is False)
check("and the boards are simply empty", r.trainers == {} and r.drivers == {})
bad = os.path.join(tmp, "bad.json")
open(bad, "w").write("{not json")
check("damaged file -> False", PaidActions().load_board(bad) is False)
open(bad, "w").write('{"trainers": "nonsense", "drivers": [1, 2]}')
check("wrong shapes -> False, no crash", PaidActions().load_board(bad) is False)
open(bad, "w").write('{"trainers": {"A": 3, "B": "x", "C": -2, "D": null}}')
s = PaidActions()
s.load_board(bad)
check("only sane scores are taken", s.trainers == {"A": 3}, str(s.trainers))

print("4. the file stays small and whole")
big = PaidActions()
for i in range(twitch.BOARD_KEEP + 25):
    big.trainers[f"v{i}"] = i + 1
big.save_board(path)
saved = json.load(open(path))
check(f"kept to {twitch.BOARD_KEEP} names", len(saved["trainers"]) == twitch.BOARD_KEEP,
      str(len(saved["trainers"])))
check("and they are the top ones", max(saved["trainers"].values()) == twitch.BOARD_KEEP + 25
      and min(saved["trainers"].values()) == 26, str(min(saved["trainers"].values())))
check("no half-written file left behind", not os.path.exists(path + ".tmp"))

print("5. an unwritable path is reported, not hidden")
try:
    PaidActions().save_board("/proc/definitely/not/here/board.json")
    raised = None
except OSError as exc:
    raised = exc
check("save raises OSError for the caller to log", isinstance(raised, OSError), str(raised))

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
