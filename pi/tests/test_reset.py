"""A run reset archives everything, clears exactly the run, and keeps the
things whose loss would fail silently (event ids, poster state)."""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import reset as R
from flybrain import game

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


def world():
    root = tempfile.mkdtemp()
    play, cfg, runs = (os.path.join(root, d) for d in ("play", "cfg", "runs"))
    os.makedirs(play); os.makedirs(cfg)
    sav = os.path.join(play, "firered.sav")
    files = {sav: b"SAVE", sav[:-4] + ".resume.ss": b"SNAP", sav[:-4] + ".resume.ss.mb.npz": b"LEARN"}
    for name, body in (("places.json", {"been": [88, 125], "furthest": 125}),
                       ("diary.json", {"entries": [[1, "caught", "CAUGHT SPEAROW!"]]}),
                       ("leaderboard.json", {"trainers": {"examplechannel": 68}}),
                       ("events.json", {"next_id": 12, "events": [{"id": 11}]}),
                       ("discord_state.json", {"last_event_id": 11}),
                       ("youtube_state.json", {"last_event": 11}),
                       ("title.json", {"title": "x"}), ("rom.json", {"cb2": 1})):
        files[os.path.join(cfg, name)] = json.dumps(body).encode()
    for p, b in files.items():
        open(p, "wb").write(b)
    flag = os.path.join(cfg, "reset_run.json")
    return sav, cfg, runs, flag, files


def flagged(flag, **kw):
    json.dump(kw, open(flag, "w"))


# -- the choices are Ian's and must be explicit ----------------------------
sav, cfg, runs, flag, files = world()
check("no flag, no reset", R.pending(flag) is None)
flagged(flag, keep_leaderboard=True)
try:
    R.pending(flag); refused = False
except SystemExit:
    refused = True
check("a flag that does not say what to do with learning is refused", refused)

# -- reset keeping the leaderboard, new naive fly --------------------------
flagged(flag, keep_leaderboard=True, keep_learning=False, label="run1")
R.CARRY = os.path.join(cfg, "carry_learning.npz")
out = R.reset(sav, R.pending(flag), cfg=cfg, runs_dir=runs, flag=flag, log=lambda m: None)
arch = set(os.listdir(out))
check("the archive is dated and labelled", os.path.basename(out).startswith("run1-"))
check("the archive holds the save, snapshot and learning",
      {"firered.sav", "firered.resume.ss", "firered.resume.ss.mb.npz"} <= arch)
check("and the places, diary, leaderboard and events",
      {"places.json", "diary.json", "leaderboard.json", "events.json"} <= arch)
check("archived bytes are the originals", open(os.path.join(out, "firered.sav"), "rb").read() == b"SAVE")
check("the save is gone", not os.path.exists(sav))
check("the snapshot is gone", not os.path.exists(sav[:-4] + ".resume.ss"))
check("the learning is gone (naive fly)", not os.path.exists(sav[:-4] + ".resume.ss.mb.npz"))
check("nothing is carried", not os.path.exists(R.CARRY))
check("the diary is gone, so the title cannot claim an old catch", not os.path.exists(os.path.join(cfg, "diary.json")))
pl = json.load(open(os.path.join(cfg, "places.json")))
check("places is an explicit EMPTY run, not a missing file", pl["been"] == [] and pl["furthest"] is None)
check("the leaderboard is kept", json.load(open(os.path.join(cfg, "leaderboard.json")))["trainers"] == {"examplechannel": 68})
check("event ids keep counting (the poster trap)", json.load(open(os.path.join(cfg, "events.json")))["next_id"] == 12)
for n in ("discord_state.json", "youtube_state.json", "title.json", "rom.json"):
    check(f"{n} is untouched", os.path.exists(os.path.join(cfg, n)))
check("the flag is consumed, so the next restart resumes", not os.path.exists(flag))

# -- the empty places file is honoured, not re-seeded ----------------------
class G:
    PLACES_MEMO = os.path.join(cfg, "places.json")
g = G()
game.Game._load_places(g)
check("an empty places file means no places, not run 1's route", g.places_been == set() and g.furthest is None)
os.remove(G.PLACES_MEMO)
game.Game._load_places(g)
check("(a MISSING file still seeds, which is why reset never deletes it)", 127 == g.furthest)

# -- reset wiping the leaderboard, carrying the learning -------------------
sav, cfg, runs, flag, files = world()
R.CARRY = os.path.join(cfg, "carry_learning.npz")
flagged(flag, keep_leaderboard=False, keep_learning=True)
R.reset(sav, R.pending(flag), cfg=cfg, runs_dir=runs, flag=flag, log=lambda m: None)
check("the leaderboard can be cleared when Ian says so", not os.path.exists(os.path.join(cfg, "leaderboard.json")))
check("kept learning is carried to where a new run looks", open(R.CARRY, "rb").read() == b"LEARN")
check("and not left beside a snapshot that no longer exists", not os.path.exists(sav[:-4] + ".resume.ss.mb.npz"))

# -- an archive that cannot be made stops the reset before anything is lost -
sav, cfg, runs, flag, files = world()
flagged(flag, keep_leaderboard=True, keep_learning=False)
open(runs, "w").write("not a directory")
try:
    R.reset(sav, R.pending(flag), cfg=cfg, runs_dir=runs, flag=flag, log=lambda m: None); failed = False
except OSError:
    failed = True
check("a failed archive raises", failed)
check("and nothing was cleared", os.path.exists(sav) and os.path.exists(os.path.join(cfg, "diary.json")))
check("and the flag stays, so it can be retried", os.path.exists(flag))

print(f"{ok} checks pass")


# -- run 3 (2026-10-08): science record, .prev, announcement ----------------
def _run3():
    global ok
    sav, cfg, runs, flag, files = world()
    res = sav[:-4] + ".resume.ss"
    open(res + ".prev", "wb").write(b"OLD")
    data = os.path.join(os.path.dirname(cfg), "data")
    for sub, name in (("inputs", "2026-10-08.jsonl"), ("inputs", "progress.jsonl"),
                      ("inputs", "learning.jsonl"), ("samples", "2026-10-08.jsonl"),
                      ("thoughts", "2026-10-08.json"), ("learning", "2026-10-08.npz"),
                      ("learning", "pre-regime-20261007-164515.npz")):
        os.makedirs(os.path.join(data, sub), exist_ok=True)
        open(os.path.join(data, sub, name), "w").write("x" * 10)
    open(os.path.join(data, "inputs", "notes.txt"), "w").write("keep")
    json.dump({"total": {"tiles": 5}}, open(os.path.join(cfg, "stats_log.json"), "w"))
    json.dump({"last_event": 11, "places_seen": [88, 103], "last_id": "9"},
              open(os.path.join(cfg, "threads_state.json"), "w"))
    flagged(flag, keep_leaderboard=False, keep_learning=False, clear_science=True,
            label="run2", announce="New run: the fly starts over.")
    out = R.reset(sav, R.pending(flag), cfg=cfg, runs_dir=runs, flag=flag, log=lambda m: None,
                  data=data)
    left = {s: sorted(os.listdir(os.path.join(data, s))) for s in ("inputs", "samples", "thoughts", "learning")}
    check("science record cleared", left == {"inputs": ["notes.txt"], "samples": [], "thoughts": [],
                                             "learning": ["pre-regime-20261007-164515.npz"]})
    check("the experiment's trained snapshot is archived AND left in place",
          os.path.exists(os.path.join(out, "science", "learning", "pre-regime-20261007-164515.npz")))
    check("every science file is in the archive",
          sorted(os.listdir(os.path.join(out, "science", "inputs"))) ==
          ["2026-10-08.jsonl", "learning.jsonl", "progress.jsonl"])
    check("restart-proof totals archived and cleared",
          os.path.exists(os.path.join(out, "stats_log.json"))
          and not os.path.exists(os.path.join(cfg, "stats_log.json")))
    th = json.load(open(os.path.join(cfg, "threads_state.json")))
    check("Threads places seen reset, the rest kept", th == {"last_event": 11, "places_seen": [], "last_id": "9"})
    check("the .prev snapshot is archived and cleared",
          not os.path.exists(res + ".prev") and os.path.exists(os.path.join(out, "firered.resume.ss.prev")))
    check("leaderboard cleared", not os.path.exists(os.path.join(cfg, "leaderboard.json")))
    ev = json.load(open(os.path.join(cfg, "events.json")))
    check("a reset news event, with the next id", ev["next_id"] == 13 and ev["events"][-1]["id"] == 12
          and ev["events"][-1]["kind"] == "reset" and ev["events"][-1]["text"].startswith("New run"))
    check("flag consumed", not os.path.exists(flag))


_run3()
print(f"test_reset (run 3): {ok} checks pass")


def _run_ids():
    sav, cfg, runs, flag, files = world()
    json.dump({"id": 3, "started": "x"}, open(os.path.join(cfg, "run.json"), "w"))
    flagged(flag, keep_leaderboard=True, keep_learning=False, label="run3")
    R.reset(sav, R.pending(flag), cfg=cfg, runs_dir=runs, flag=flag, log=lambda m: None, now=0)
    d = json.load(open(os.path.join(cfg, "run.json")))
    check("the next run is numbered", d["id"] == 4 and d["started"].startswith("19"))
    check("and not yet announced", d["announced"] is False)
    from flybrain import runinfo
    runinfo.mark_announced(os.path.join(cfg, "run.json"))
    check("announced once", runinfo.current(os.path.join(cfg, "run.json"))["announced"] is True)
    from flybrain.playview import run_line
    check("health line", run_line(os.path.join(cfg, "run.json")).startswith("run: id=4 started=19") and " ends=" in run_line(os.path.join(cfg, "run.json")))
    check("missing file", run_line(os.path.join(cfg, "nope.json")) == "run: id=- started=-")


_run_ids()
print(f"test_reset (run ids): {ok} checks pass")


def _registry():
    sav, cfg, runs_dir, flag, files = world()
    json.dump({"id": 4, "started": "x"}, open(os.path.join(cfg, "run.json"), "w"))
    json.dump({"runs": [{"id": 4, "kind": "lab", "started": "2026-10-08T13:14:39", "ended": None,
                         "reason": None, "archive": None}], "experiments": []},
              open(os.path.join(cfg, "runs.json"), "w"))
    flagged(flag, keep_leaderboard=False, keep_learning=False, label="run4", reason="30 days")
    out = R.reset(sav, R.pending(flag), cfg=cfg, runs_dir=runs_dir, flag=flag, log=lambda m: None, now=0)
    reg = json.load(open(os.path.join(cfg, "runs.json")))
    old, new = reg["runs"]
    check("the old run is closed with its reason and archive",
          old["ended"].startswith("19") and old["reason"] == "manual: 30 days" and old["archive"])
    check("the new run is open", new["id"] == 5 and new["kind"] == "lab" and new["ended"] is None)
    check("the old run's settings are kept with it", os.path.exists(os.path.join(out, "settings.json")))


_registry()
print(f"test_reset (registry): {ok} checks pass")
