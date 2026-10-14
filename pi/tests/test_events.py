"""Numbered news, for a poster that must repeat each item exactly once.

The two failures that matter are opposite and both bad: announcing something
twice (ids that repeat or reset) and announcing nothing ever again (ids that
jump backwards past what a poster has already seen). Everything here is aimed
at those.
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flybrain.events import EventLog

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


tmp = tempfile.mkdtemp()
path = os.path.join(tmp, "events.json")


def fresh():
    return EventLog(path=path)


# -- ids go up, one per item ----------------------------------------------
log = fresh()
a = log.add("caught", "CAUGHT RATTATA!")
b = log.add("badge", "EARNED A BADGE!  (2 in total)")
check("first id is 1", a == 1)
check("ids increase", b == a + 1)

# -- the game's word becomes the agreed word ------------------------------
check("caught is served as catch", log.since(0)[0]["kind"] == "catch")
check("badge is unchanged", log.since(0)[1]["kind"] == "badge")
for kind in ("badge", "evolve", "level", "faint"):
    log2 = EventLog(path=os.path.join(tmp, f"k_{kind}.json"))
    log2.add(kind, "x")
    check(f"{kind} passes through", log2.since(0)[0]["kind"] == kind)

# -- since() is exclusive, so a poster cannot re-post its last item -------
rows = log.since(a)
check("since skips what was seen", [r["id"] for r in rows] == [b])
check("since 0 gives everything", len(log.since(0)) == 2)
check("since the last gives nothing", log.since(b) == [])
check("a string since works (it comes off a URL)", len(log.since("1")) == 1)
check("nonsense since is treated as 0", len(log.since("banana")) == 2)
check("negative since is not a crash", len(log.since(-5)) == 2)

# -- the shape the poster parses ------------------------------------------
row = log.since(0)[0]
check("row has exactly id, kind, text", set(row) == {"id", "kind", "text"})
check("id is an int", isinstance(row["id"], int))
check("it survives json", json.loads(json.dumps({"events": log.since(0)}))["events"][0]["id"] == 1)

# -- a restart does not re-announce ---------------------------------------
again = fresh()
check("it reloaded the events", len(again.since(0)) == 2)
c = again.add("evolve", "SQUIRTLE EVOLVED INTO WARTORTLE!")
check("numbering continues past a restart", c == b + 1)
check("the poster at b sees only the new one", [r["id"] for r in again.since(b)] == [c])

# -- trimming must not rewind the count -----------------------------------
small = EventLog(path=os.path.join(tmp, "small.json"), keep=3)
for i in range(10):
    small.add("level", f"grew to Lv {i}")
check("only keep are held", len(small.since(0)) == 3)
check("but the ids are the late ones", [r["id"] for r in small.since(0)] == [8, 9, 10])
reloaded = EventLog(path=os.path.join(tmp, "small.json"), keep=3)
check("count survives trimming", reloaded.add("badge", "x") == 11)

# -- a poster further behind than KEEP gets what is left, not nothing -----
check("behind the window still gets news", len(small.since(1)) == 3)

# -- a damaged file must not restart the numbering ------------------------
broken = os.path.join(tmp, "broken.json")
with open(broken, "w") as f:
    f.write("{not json at all")
b1 = EventLog(path=broken)
check("damaged file starts empty", b1.since(0) == [])
check("damaged file does not replay old news", b1.since(0) == [])

# a file whose counter is missing, but whose events are readable: the count
# must come from the events, or ids repeat
half = os.path.join(tmp, "half.json")
with open(half, "w") as f:
    json.dump({"events": [{"id": 7, "kind": "catch", "text": "old"}]}, f)
h = EventLog(path=half)
check("count recovered from the events", h.add("badge", "new") == 8)

# a counter BEHIND its own events (a bad write) must not hand out a used id
behind = os.path.join(tmp, "behind.json")
with open(behind, "w") as f:
    json.dump({"next_id": 2, "events": [{"id": 9, "kind": "catch", "text": "old"}]}, f)
check("a stale counter cannot reuse an id", EventLog(path=behind).add("badge", "x") == 10)

# -- junk rows are dropped rather than served ------------------------------
junk = os.path.join(tmp, "junk.json")
with open(junk, "w") as f:
    json.dump({"next_id": 5, "events": [{"id": 1, "kind": "catch", "text": "fine"},
                                        {"kind": "catch", "text": "no id"},
                                        "not a row", None]}, f)
j = EventLog(path=junk)
check("only well-formed rows survive", [r["id"] for r in j.since(0)] == [1])

# -- empty news is not news ------------------------------------------------
e = EventLog(path=os.path.join(tmp, "empty.json"))
check("empty text is refused", e.add("catch", "   ") is None)
check("and uses no id", e.add("catch", "real") == 1)

# -- the file is private ---------------------------------------------------
check("saved 0600", oct(os.stat(path).st_mode & 0o777) == "0o600")

print(f"{ok} checks pass")
