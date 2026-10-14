"""StillLog: one summary per still stretch >= 25 s, with mood and what was in
range; the report weights shares by duration and keeps human stretches apart."""
import os, sys
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [ROOT, os.path.join(ROOT, "play")]
from flybrain.attrib import StillLog
import inputs_report as r

ok = 0
def check(name, cond):
    global ok
    assert cond, name
    ok += 1

L = StillLog()
out = []
def feed(seq, val, near, human=False, pain_at=()):
    for s in seq:
        x = L.update(s, val, "content" if val and val >= 0.15 else "calm", near, human,
                     pain=s in pain_at, place=90)
        if x: out.append(x)

feed([None] + [i * 1.0 for i in range(10)], 0.3, {})
feed([None], 0.3, {})
check("short stretch not logged", out == [])
feed([i * 1.0 for i in range(0, 40)], 0.3, {"people": 0.1})
feed([i * 1.0 for i in range(40, 80)], -0.2, {"people": 0.7}, pain_at=(60.0, 70.0))
feed([None], 0.0, {})
e = out[-1]
check("logged once", len(out) == 1)
check("duration", e["dur_s"] == 79.0)
check("content half", e["content_share"] == 0.5)
check("near half", e["near_any_share"] == 0.5 and e["near_people_share"] == 0.5
      and e["near_exits_share"] == 0.0)
check("start/end mood", e["start_label"] == "content" and e["end_label"] == "calm")
check("pains", e["rule_pains"] == 2 and e["human"] is False and e["place"] == 90)
feed([30.0, 31.0, 2.0], 0.5, {}, human=True)      # counter dropped -> new stretch
check("counter drop closes", len(out) == 2 and out[-1]["human"] is True)
check("None valence ok", StillLog().update(1.0, None, None, {}, False) is None)

eps = [{"kind": "still", "dur_s": 100, "content_share": 1.0, "near_any_share": 0.0,
        "near_people_share": 0, "near_exits_share": 0, "mean_valence": 0.4, "rule_pains": 0, "human": False},
       {"kind": "still", "dur_s": 300, "content_share": 0.0, "near_any_share": 1.0,
        "near_people_share": 1, "near_exits_share": 0, "mean_valence": -0.2, "rule_pains": 3, "human": False},
       {"kind": "still", "dur_s": 999, "content_share": 1, "near_any_share": 1, "near_people_share": 1,
        "near_exits_share": 1, "mean_valence": 1, "rule_pains": 0, "human": True},
       {"kind": "area", "text": "Route 2", "by": "fly"}]
s = r.still_summary(eps)
check("fly-only stretches", s["stretches"] == 2 and s["total_s"] == 400)
check("duration-weighted", s["content_share"] == 0.25 and s["near_any_share"] == 0.75)
check("content and nothing near", s["content_and_nothing_near"] == 0.25)
check("pains", s["rule_pains"] == 3 and s["pained"] == 1)
check("human kept apart", r.still_summary(eps, human=True)["stretches"] == 1)
check("progress excludes still", r.fly_summary(eps)["events"] == 1)
check("empty", r.still_summary([]) == {"stretches": 0})
fly = r.fly_summary(eps); fly["still"] = s
check("text", "content/happy 25%" in r.text_report({}, fly, 24))
print(f"test_still_log: {ok} checks passed")


# 2026-10-08 audit: how much of a stretch was human, and how much dialogue.
def _share_checks():
    from flybrain.attrib import StillLog as SL
    s = SL()
    for t in range(0, 100):
        s.update(float(t), 0.0, "calm", {}, human=t < 5, dialogue=50 <= t < 80)
    r = s.update(None, 0.0, "calm", {}, human=False)
    assert r["human"] is True and r["human_share"] == 0.05, r
    assert r["dialogue_share"] == 0.3, r
    import importlib.util
    spec = importlib.util.spec_from_file_location("ir", os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play", "inputs_report.py"))
    ir = importlib.util.module_from_spec(spec); spec.loader.exec_module(ir)
    eps = [dict(r, t=1), {"kind": "still", "dur_s": 30.0, "human": True, "content_share": 0,
                          "near_any_share": 0, "near_people_share": 0, "near_exits_share": 0,
                          "mean_valence": 0, "t": 2}]
    alone, helped = ir.still_summary(eps), ir.still_summary(eps, human=True)
    assert alone["stretches"] == 1 and alone["dialogue_share"] == 0.3, alone   # 5% human: the fly's
    assert helped["stretches"] == 1 and helped["dialogue_share"] is None, helped  # old row: old flag
    print("test_still_log: share checks passed")


_share_checks()


def _shadow_checks():
    from flybrain.attrib import StillLog as SL
    s = SL()
    for t in range(0, 100):
        s.update(float(t), 0.0, "calm", {}, human=False, pain=t == 90, shadow_pain=t in (60, 70, 80, 90))
    r = s.update(None, 0.0, "calm", {}, human=False)
    assert r["rule_pains"] == 1 and r["rule_pains_without_pause"] == 4, r
    print("test_still_log: shadow checks passed")


_shadow_checks()
