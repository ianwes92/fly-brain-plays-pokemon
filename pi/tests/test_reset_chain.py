"""A whole reset, offline (obsfly, 2026-10-13): Lab Run 1 -> Lab Run 2 through
the run clock's flag, and everything that names the run follows."""
import datetime as dt, json, os, sys, tempfile
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT); sys.path.insert(0, os.path.join(ROOT, "play"))
from flybrain import reset as R, runinfo, twitch
import run_clock as C, threads_post as TP, youtube_promo as YP

d = tempfile.mkdtemp()
cfg, runs, play = (os.path.join(d, x) for x in ("cfg", "runs", "play"))
for x in (cfg, runs, play):
    os.makedirs(x)
sav = os.path.join(play, "firered.sav")
for p, body in ((sav, b"S"), (sav[:-4] + ".resume.ss", b"X"), (sav[:-4] + ".resume.ss.mb.npz", b"L")):
    open(p, "wb").write(body)
for n, body in (("places.json", {"been": [88], "furthest": 88}), ("events.json", {"next_id": 60, "events": []}),
                ("leaderboard.json", {"trainers": {"a": 1}}), ("diary.json", {"entries": []}),
                ("run.json", {"id": 4, "started": "2026-10-08T13:14:39"}),
                ("title_notice.json", {"what": "RESET", "on": "2026-10-22"}),
                ("runs.json", {"runs": [{"id": 4, "kind": "lab", "started": "2026-10-08T13:14:39",
                                         "ended": None, "reason": None, "archive": None}], "experiments": []})):
    json.dump(body, open(os.path.join(cfg, n), "w"))
# 1. the run clock writes the flag on the day (restart stubbed out)
C.RUN_F, C.FLAG, C.NOTICE_F, C.LOG = (os.path.join(cfg, "run.json"), os.path.join(cfg, "reset_run.json"),
                                      os.path.join(cfg, "title_notice.json"), os.path.join(d, "log"))
assert C.tick(dt.datetime(2026, 10, 22, 13, 0), act={"busy": lambda: None, "restart": lambda: 0,
                                                     "say": lambda *a: None}) == "reset"
flag = json.load(open(C.FLAG))
assert flag["reason"] == "14 days" and "Lab Run 1 is complete" in flag["chat"] and "Lab Run 2" in flag["announce"]
# 2. what the game does at startup with that flag
out = R.reset(sav, R.pending(C.FLAG), cfg=cfg, runs_dir=runs, flag=C.FLAG, log=lambda m: None,
              now=dt.datetime(2026, 10, 22, 13, 0, 30).timestamp())
run = json.load(open(os.path.join(cfg, "run.json")))
reg = json.load(open(os.path.join(cfg, "runs.json")))["runs"]
assert run["id"] == 5 and run["announced"] is False                       # NEW RUN banner + chat pending
assert reg[0]["ended"] and reg[0]["reason"] == "manual: 14 days" and reg[0]["archive"]   # report poster fires
assert reg[1]["id"] == 5 and reg[1]["ended"] is None
assert not os.path.exists(os.path.join(cfg, "title_notice.json"))         # RESET TOMORROW gone
assert not os.path.exists(os.path.join(cfg, "leaderboard.json"))
assert open(os.path.join(cfg, "ext_experiment.txt")).read().startswith("Lab Run 2 started Oct 22")
ev = json.load(open(os.path.join(cfg, "events.json")))["events"][-1]
assert ev["kind"] == "reset" and "Lab Run 2" in ev["text"]
# 3. everything that names the run
rf = os.path.join(cfg, "run.json")
assert runinfo.name(5) == "Lab Run 2" and runinfo.tag(rf).startswith("LAB RUN 2 · DAY ")
assert runinfo.prefix(rf) == "Lab Run 2 · "
YP.RUN_F, TP.RUN_F = rf, rf
assert YP.title().startswith("Lab Run 2 · ") and TP.run_prefix() == "Lab Run 2 · "
runinfo.RUN_F = rf
assert twitch.stream_title({"badges": 0}, [], None).startswith("Lab Run 2 · ")
assert os.path.exists(os.path.join(out, "settings.json"))                # the old run's settings kept
print("test_reset_chain: 16 checks passed")
