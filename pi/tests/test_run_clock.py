"""The run clock (14 days since 2026-10-13; was 30) (Ian approved auto-reset, 2026-10-08)."""
import datetime as dt, json, os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))
import run_clock as C
d = tempfile.mkdtemp()
C.RUN_F, C.FLAG, C.NOTICE_F, C.LOG = (os.path.join(d, n) for n in ("run.json", "flag.json", "notice.json", "log"))
json.dump({"id": 4, "started": "2026-10-08T13:14:39"}, open(C.RUN_F, "w"))
ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1
st = C.status(dt.datetime(2026, 10, 8, 20, 0))
check("day 1 of 14, ends Oct 22 13:00", st == {"id": 4, "day": 1, "of": 14, "ends": "2026-10-22T13:00:00", "auto": True})
check("day 12", C.status(dt.datetime(2026, 10, 19, 9, 0))["day"] == 12)
said, restarted = [], []
act = {"say": lambda t, x: said.append(t), "busy": lambda: None, "restart": lambda: restarted.append(1) or 0}
check("nothing mid-run", C.tick(dt.datetime(2026, 10, 15, 13, 0), act=act) is None and not said)
check("the day before: warn + title notice", C.tick(dt.datetime(2026, 10, 21, 13, 0), act=act) == "warn"
      and json.load(open(C.NOTICE_F)) == {"what": "RESET", "on": "2026-10-22"} and said)
check("on the day: reset through the flag", C.tick(dt.datetime(2026, 10, 22, 13, 0), act=act) == "reset"
      and restarted and json.load(open(C.FLAG))["reason"] == "14 days")
act["busy"] = lambda: "flybrain-naive-trained.service"
os.remove(C.FLAG); restarted.clear()
check("busy: postponed, no flag", C.tick(dt.datetime(2026, 10, 22, 13, 0), act=act) == "busy"
      and not restarted and not os.path.exists(C.FLAG))
json.dump({"id": 3, "started": "2026-10-08T09:16:35"}, open(C.RUN_F, "w"))
check("test runs never auto-reset", C.tick(dt.datetime(2026, 10, 22, 13, 0), act=act) is None)
print(f"test_run_clock: {ok} checks passed")
