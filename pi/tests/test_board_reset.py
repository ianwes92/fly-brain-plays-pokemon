"""Leaderboard reset: only with the flag; old board archived, then cleared; flag consumed."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.playview import _reset_board_if_asked
ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1
d = tempfile.mkdtemp()
board, flag, runs = os.path.join(d, "b.json"), os.path.join(d, "flag"), os.path.join(d, "runs")
json.dump({"trainers": {"a": 1}}, open(board, "w"))
check("no flag, no reset", not _reset_board_if_asked(board, flag, runs, log=lambda m: None) and os.path.exists(board))
open(flag, "w").close()
check("flag resets", _reset_board_if_asked(board, flag, runs, log=lambda m: None))
check("board cleared", not os.path.exists(board))
arch = os.listdir(runs)
check("archived", len(arch) == 1 and json.load(open(os.path.join(runs, arch[0]))) == {"trainers": {"a": 1}})
check("flag consumed", not os.path.exists(flag))
print(f"test_board_reset: {ok} checks passed")
