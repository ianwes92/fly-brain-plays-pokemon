"""SCIENCE card data: today's steps and fly-alone share from the sample
archive, top human trainers by booked synaptic change; nothing when no data."""
import json, os, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.playview import ScienceCard
ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1
d = tempfile.mkdtemp()
now = time.mktime(time.strptime("2026-10-07 18:00", "%Y-%m-%d %H:%M"))
c = ScienceCard(); c.SAMPLES = d; c.LEARN = os.path.join(d, "learning.jsonl")
c.refresh(now)
check("no data -> nothing", c.own is None and c.trainers == [])
with open(os.path.join(d, "2026-10-07.jsonl"), "w") as f:
    f.write(json.dumps({"t": now - 3600, "tiles_clean": 100, "tiles_assisted": 10}) + "\n")
    f.write(json.dumps({"t": now, "tiles_clean": 400, "tiles_assisted": 110}) + "\n")
with open(c.LEARN, "w") as f:
    for who, v, src, t in (("ann", 0.5, "human", now - 10), ("bob", 2.0, "human", now - 10),
                           ("ann", 1.0, "human", now - 5), ("the game", 9.0, "game", now - 5),
                           ("old", 99.0, "human", now - 2 * 86400)):
        f.write(json.dumps({"t": t, "who": who, "booked_to_source": v, "source": src}) + "\n")
c.refresh(now + 31)
check("steps and share", c.own == (400, 0.75))
check("top trainers: humans, today, by change", c.trainers == [("bob", 2.0), ("ann", 1.5)])
c2 = ScienceCard(); c2.SAMPLES = d; c2.LEARN = c.LEARN
c2.refresh(now); c.refresh(now + 32)
check("refresh is throttled", c.trainers == [("bob", 2.0), ("ann", 1.5)])
print(f"test_science_card: {ok} checks passed")
