"""Threads post: real numbers only, restart-proof 24h totals, honest windows,
milestones only since the last post, <=500 chars, one tag."""
import os, sys, tempfile, time
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "play")); sys.path.insert(0, ROOT)
import threads_post as t
t.RUN_F = "/nonexistent/run.json"     # posts tested without a run number (see the end)

ok = 0
def check(name, cond):
    global ok
    assert cond, name
    ok += 1

H = """ok frames=500
attrib: fly_frames=9 nudge_frames=1 driver_frames=0 admin_frames=0 clean_frames=8 tiles_clean=70 tiles_assisted=30 bumps_clean=100 bumps_assisted=50 human_pain=3 human_pleasure=1 game_pain=0 game_pleasure=2 since_human_s=12 human_share_5min=0.100
phase=playing title=auto emu=1
cb2: now=1 overworld=1 battle=False place=89
progress: furthest=122 areas=4
team: lv=21 badges=0 caught=3 lead=MR. MIME
moved: tiles=100 bumps=150 bouts=9
senses: eye=8 spikes=1000
learned: people=-0.269 exits=+0.361 pain=0 pleasure=0 toward_person=1
"""
h = t.parse_health(H)
check("attrib parsed", h["tiles_clean"] == 70 and h["human_pain"] == 3 and "human_share_5min" not in h)
check("tiles", h["tiles"] == 100); check("bumps", h["bumps"] == 150)
check("spikes", h["spikes"] == 1000); check("frames", h["frames"] == 500)
check("place", h["place"] == 89); check("lead with spaces", h["lead"] == "MR. MIME")
check("lv", h["lv"] == 21); check("people signed", h["people"] == -0.269)
check("exits", h["exits"] == 0.361); check("playing", h["playing"])
check("no team -> no lead", "lead" not in t.parse_health(H.replace("lead=MR. MIME", "lead=-")))
check("missing counters absent", "tiles" not in t.parse_health("ok frames=1\n"))

# running totals across a restart
d = {}
def rd(fr, ti, bu, sp): return {"frames": fr, "tiles": ti, "bumps": bu, "spikes": sp}
t.add_sample(d, rd(100, 10, 5, 1000), 0)
t.add_sample(d, rd(200, 30, 9, 3000), 3600)
check("first delta", d["total"] == {"tiles": 30, "bumps": 9, "spikes": 3000})
t.add_sample(d, rd(50, 4, 1, 500), 7200)             # restarted: frames went back
check("restart adds new counts whole", d["total"] == {"tiles": 34, "bumps": 10, "spikes": 3500})
t.add_sample(d, None, 7300)
check("unreadable game adds nothing", len(d["samples"]) == 3)
t.add_sample(d, {"frames": 60}, 7300)
check("partial reading adds nothing", len(d["samples"]) == 3)
win, hrs = t.window(d, 7200)
check("window covers 2h", abs(hrs - 2) < 1e-9 and win["tiles"] == 34 - 10)
check("under 1h -> no window", t.window({"samples": [[0, 1, 1, 1], [1800, 2, 2, 2]]}, 1800) == (None, 0))
check("one sample -> no window", t.window({"samples": [[0, 1, 1, 1]]}, 0) == (None, 0))
old = {}
for i in range(30):
    t.add_sample(old, rd(100 + i, i, i, i), i * 3600)
w2, h2 = t.window(old, 29 * 3600)
check("24h window, not more", 23.9 < h2 <= 24.2)
t.add_sample(old, rd(1000, 1, 1, 1), 9 * 86400 + 30 * 3600)
check("old samples pruned", all(s["t"] >= 30 * 3600 for s in old["samples"]))
legacy = {"samples": [[0, 1, 2, 3], [7200, 11, 12, 13]]}
lw, lh = t.window(legacy, 7200)
check("legacy list rows still read", lw["tiles"] == 10 and lh == 2 and "tiles_clean" not in lw)
mixed = {}
t.add_sample(mixed, rd(100, 10, 5, 1000), 0)                 # an older game: no attrib line
t.add_sample(mixed, dict(rd(200, 20, 6, 2000), tiles_clean=8, tiles_assisted=2,
                         bumps_clean=1, bumps_assisted=0), 1800)
t.add_sample(mixed, dict(rd(300, 40, 9, 3000), tiles_clean=20, tiles_assisted=10,
                         bumps_clean=3, bumps_assisted=1), 9000)
mw, mh = t.window(mixed, 9000)
check("extras get their own span", mw["tiles_clean"] == 12 and abs(mw["tiles_clean_h"] - 2) < 1e-9
      and abs(mw["tiles_h"] - 2.5) < 1e-9)

# day N from runs/
rd_ = tempfile.mkdtemp(); os.mkdir(os.path.join(rd_, "run1-20261004-080001"))
now = time.mktime(time.strptime("20261007 18:00", "%Y%m%d %H:%M"))
check("day 4 on Oct 7", t.run_day(now, rd_) == 4)
check("no runs -> None", t.run_day(now, tempfile.mkdtemp()) is None)

# milestones
evs = [{"id": 5, "kind": "evolve", "text": "WARTORTLE EVOLVED INTO BLASTOISE!"},
       {"id": 6, "kind": "level", "text": "WARTORTLE grew to Lv 22"},
       {"id": 7, "kind": "catch", "text": "CAUGHT PIDGEY!", "short": "/nope.mp4"}]
line, short, newest = t.milestone({"last_event": 4, "places_seen": [88]}, evs, {"been": [88]})
check("evolve outranks catch", "Wartortle just evolved into Blastoise" in line)
check("newest id", newest == 7)
line, short, _ = t.milestone({"last_event": 5, "places_seen": [88]}, evs, {"been": [88]})
check("catch", line == "The fly just caught a Pidgey!")
check("missing short dropped", short is None)
line, _, _ = t.milestone({"last_event": 7, "places_seen": [88]}, evs, {"been": [88]})
check("nothing new", line is None)
lv = [{"id": 6, "kind": "level", "text": "WARTORTLE grew to Lv 22"}]
check("levels are not milestones", t.milestone({"last_event": 0, "places_seen": []}, lv, {})[0] is None)
line, _, _ = t.milestone({"last_event": 7, "places_seen": [88]}, evs, {"been": [88, 89]},
                         credit_of=lambda n: "fly")
check("new area", line and line.endswith("for the first time!"))
line, _, _ = t.milestone({"last_event": 7, "places_seen": [88]}, evs, {"been": [88, 89]},
                         credit_of=lambda n: "assisted")
check("assisted area says so", line.endswith(t.HELPED))
line, _, _ = t.milestone({"last_event": 0, "places_seen": []},
                         [{"id": 1, "kind": "catch", "text": "CAUGHT PIDGEY!", "by": "assisted"}], {})
check("assisted catch says so", line == "The fly just caught a Pidgey!" + t.HELPED)
pf = os.path.join(tempfile.mkdtemp(), "p.jsonl")
open(pf, "w").write('{"kind":"area","text":"Route 2","by":"fly"}\nbad\n{"kind":"area","text":"Route 2","by":"assisted"}\n')
check("area credit: latest wins", t.area_credit("Route 2", pf) == "assisted")
check("area credit: unknown", t.area_credit("Nowhere", pf) is None)
line, _, _ = t.milestone({"last_event": 0, "places_seen": []},
                         [{"id": 1, "kind": "badge", "text": "EARNED A BADGE!  (1 in total)"}], {})
check("badge names gym", line == "The fly just beat Brock and earned its first gym badge!")

# templates
places = {"furthest": 122, "been": [88, 89]}
win = {"tiles": 4321, "bumps": 5000, "spikes": 12_300_000_000}
win.update(tiles_h=24, spikes_h=24, bumps_h=24, tiles_clean=3000, tiles_assisted=1321,
           tiles_clean_h=24, bumps_clean=1000, bumps_assisted=4000, bumps_clean_h=24,
           human_pain=40, human_pleasure=2, game_pain=1, game_pleasure=5, human_pain_h=24)
for k in "ABC":
    txt = t.compose(k, 4, h, win, 24, places, "The fly just caught a Pidgey!")
    check(f"{k} <=500", len(txt) <= 500)
    check(f"{k} one tag", txt.count("#") == 1)
    check(f"{k} links", "Chat controls it on twitch.tv/examplechannel" in txt and "youtube.com/@examplechannel" in txt)
a = t.compose("A", 4, h, win, 24, places)
check("A day", a.startswith("Day 4 of"))
check("A own share", "4,321 steps (69% on its own)" in a)
check("A numbers", "4,321 steps" in a and "12.3 billion neuron spikes" in a and "Mr. Mime at Lv 21" in a)
check("A 24h label", "Last 24h:" in a)
check("A short window named", "Last 9h:" in t.compose("A", 4, h, win, 9.2, places))
check("A no window -> no invented stats", "steps" not in t.compose("A", 4, h, None, 0, places))
check("A no health -> still valid", "twitch.tv" in t.compose("A", None, None, None, 0, {}))
b = t.compose("B", 4, h, win, 24, places)
check("B bump % from the fly's own moves", "25% of its own moves today" in b)   # 1000/4000
check("B shaped by", "by 42 chat !pain/!pleasure and 6 automatic ones" in b)
nob = t.compose("B", 4, h, {"tiles": 9, "bumps": 9, "spikes": 1, "tiles_h": 5}, 5, places)
check("B no fly-alone data -> no bump line", "bump" not in nob)
check("B opinions signed", "(-0.27)" in b and "(+0.36)" in b and "dislikes people" in b)
check("A split hidden when spans differ", "on its own" not in t.compose(
    "A", 4, h, dict(win, tiles_clean_h=3), 24, places))
check("B science tag", "#neuroscience" in b)
c = t.compose("C", 4, h, win, 24, places, "The fly just caught a Pidgey!")
check("C headline", c.startswith("🚨 The fly just caught a Pidgey!"))
long = t.compose("A", 4, dict(h, lead="X" * 600), win, 24, places)
check("over-long trimmed", len(long) <= 500 and long.endswith("#FruitFlyPlaysPokemon"))

# rotation
wed = time.mktime(time.strptime("20261007", "%Y%m%d"))
thu = time.mktime(time.strptime("20261008", "%Y%m%d"))
check("Wed is B", t.choose(wed, None) == "B"); check("Thu is A", t.choose(thu, None) == "A")
check("milestone wins", t.choose(wed, "x") == "C")

# token refresh + warning
saved, warned = [], []
cfg = {"token": "old", "refreshed_at": 0, "expires_at": 60 * 86400}
tok = t.token(cfg, now=8 * 86400, call=lambda *a, **k: {"access_token": "new", "expires_in": 5184000},
              save=saved.append, warn=warned.append)
check("refreshed weekly", tok == "new" and saved and not warned)
def boom(*a, **k): raise t.ThreadsError("x")
cfg = {"token": "old", "refreshed_at": 0, "expires_at": 60 * 86400}
t.token(cfg, now=54 * 86400, call=boom, save=saved.append, warn=warned.append)
check("warns under 7 days", len(warned) == 1)
cfg = {"token": "old", "refreshed_at": 0, "expires_at": 60 * 86400}
warned.clear(); t.token(cfg, now=20 * 86400, call=boom, save=saved.append, warn=warned.append)
check("no warning with time left", not warned)
try:
    t.token({}); check("no token raises", False)
except t.ThreadsError:
    check("no token raises", True)

print(f"test_threads_post: {ok} checks passed")


def _run_prefix():
    import json as _j
    rf = os.path.join(tempfile.mkdtemp(), "run.json")
    _j.dump({"id": 4, "started": "2026-10-08T13:20:00"}, open(rf, "w"))
    t.RUN_F = rf
    for kind in ("A", "B", "C"):
        txt = t.compose(kind, 5, {"lead": "SQUIRTLE", "lv": 12, "people": 0.0, "exits": 0.0}, None, 0, {},
                        ms_line="The fly just caught a Pidgey!")
        assert txt.startswith("Lab Run 1 · "), (kind, txt)
        assert len(txt) <= t.MAX_CHARS
    t.RUN_F = "/nonexistent/run.json"
    print("test_threads_post: run prefix checks passed")


_run_prefix()


def _run_boundary():
    evs = [{"id": 5, "kind": "badge", "text": "EARNED A BADGE!  (1 in total)"},
           {"id": 9, "kind": "reset", "text": "Run 4 starts"},
           {"id": 10, "kind": "level", "text": "SQUIRTLE grew to Lv 6"}]
    line, short, newest = t.milestone({"last_event": 3}, evs, {"been": []})
    assert line is None and newest == 10, line            # run 2's badge is not run 4's news
    evs.append({"id": 11, "kind": "catch", "text": "CAUGHT PIDGEY!"})
    line, _, _ = t.milestone({"last_event": 3}, evs, {"been": []})
    assert line.startswith("The fly just caught a Pidgey"), line
    print("test_threads_post: run boundary checks passed")


_run_boundary()
