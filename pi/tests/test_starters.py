"""The starter scoreboard (just for fun, 2026-10-08)."""
import os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import starters as S

p = os.path.join(tempfile.mkdtemp(), "starters.json")
ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1
check("empty board", S.scoreboard(S.load(p)) == "Squirtle 0 · Charmander 0 · Bulbasaur 0")
for run, sp, by in ((1, "SQUIRTLE", "?"), (2, "SQUIRTLE", "?"), (3, "squirtle", "fly"), (4, "CHARMANDER", "fly")):
    S.record(run, sp, by, p)
check("a run is recorded once", S.record(4, "BULBASAUR", "chat", p) is False)
d = S.load(p)
check("tally", S.tally(d) == {"SQUIRTLE": 3, "CHARMANDER": 1, "BULBASAUR": 0})
check("scoreboard, biggest first", S.scoreboard(d) == "Squirtle 3 · Charmander 1 · Bulbasaur 0")
check("health line", S.health_line(d) == "starters: 1=SQUIRTLE:? 2=SQUIRTLE:? 3=SQUIRTLE:fly 4=CHARMANDER:fly")
S.record(5, "BULBASAUR", "chat", p)
check("ties keep Squirtle/Charmander/Bulbasaur order", S.scoreboard(S.load(p)) == "Squirtle 3 · Charmander 1 · Bulbasaur 1")
check("missing file", S.health_line(S.load(p + "x")) == "starters: ")
print(f"test_starters: {ok} checks passed")

# !starter (Ian said yes, 2026-10-08): a free question, rate-limited like !team
from flybrain import twitch as _tw, playview as _pv
assert _tw.interpret({"user": "bob", "name": "Bob", "text": "!starter", "bits": 0}) == ("ask", "starter", "bob", "Bob")
S.PATH = p
assert _pv._answer("starter", None, None, None).endswith("Squirtle 3 · Charmander 1 · Bulbasaur 1")
print("test_starters: !starter checks passed")
