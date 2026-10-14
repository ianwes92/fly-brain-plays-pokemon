"""/panel/trainers data: rows per (who, source) by booked change, totals per
source, window by days, junk lines skipped."""
import json, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain.attrib import trainers
ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1
p = os.path.join(tempfile.mkdtemp(), "l.jsonl")
now = 1_800_000_000.0
with open(p, "w") as f:
    for who, src, b, t, ov in (("ann", "human", 0.5, now - 10, False), ("ann", "human", 1.0, now - 9, True),
                               ("bob", "human", 2.0, now - 8, False), ("local", "rule", 3.0, now - 7, False),
                               ("old", "human", 9.0, now - 9 * 86400, False)):
        f.write(json.dumps({"t": t, "kind": "learning", "who": who, "source": src,
                            "booked_to_source": b, "overlap": ov}) + "\n")
    f.write("junk\n")
r = trainers(7, p, now)
# ann's second burst (overlap) and bob's 1 s later are one cluster worth ann's
# second row (1.0), shared: 0.5 each. The rule's "local" rows are named.
check("sorted by booked", [x["who"] for x in r["trainers"]] == ["stillness rule", "ann", "bob"])
ann = r["trainers"][1]
check("summed per who", ann == {"who": "ann", "source": "human", "stims": 2, "booked": 1.0, "overlapped": 1})
check("cluster shared", r["trainers"][2] == {"who": "bob", "source": "human", "stims": 1, "booked": 0.5, "overlapped": 1})
check("totals", r["by_source"] == {"human": {"stims": 3, "booked": 1.5}, "rule": {"stims": 1, "booked": 3.0}})
# a different source after an overlapping burst is not folded in
q = p + ".2"
with open(q, "w") as f:
    for src, b, t, ov in (("game", 5.0, now - 3, True), ("rule", 2.0, now - 2, False)):
        f.write(json.dumps({"t": t, "kind": "learning", "who": "w", "source": src,
                            "booked_to_source": b, "overlap": ov}) + "\n")
check("other source kept", trainers(7, q, now)["by_source"] == {"game": {"stims": 1, "booked": 5.0},
                                                              "rule": {"stims": 1, "booked": 2.0}})
check("window", all(x["who"] != "old" for x in r["trainers"]))
check("missing file", trainers(7, p + "x", now)["trainers"] == [])
json.dumps(r)
print(f"test_trainers: {ok} checks passed")
