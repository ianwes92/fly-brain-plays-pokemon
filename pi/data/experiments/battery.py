#!/usr/bin/env python3
"""The standing per-run experiment, fully automatic (Ian, 2026-10-13: the
project must run with nobody -- no Claude session -- watching).

After every LAB run ends, the night after: "untrained vs this run's trained
brain", exactly the 2026-10-09 design (naive_trained.py, unchanged), with the
end-of-run learned weights from the run's archive as the trained arm, and
scored by the frozen naive_trained_score.py. Pre-registered in
STANDING_BATTERY_PREREG.md. There is NO independent blind recompute any more;
every public line says "scored by the pre-registered script, not independently
checked".

  battery.py plan     daily 13:30: a lab run ended and has no battery yet ->
                      schedule it for 00:30 tonight, post the notice, ext line
  battery.py notice   23:30: the one-hour notice (if tonight's is planned)
  battery.py run      00:30: preflight, 24 runs, score, post the result

State: data/experiments/battery/run<N>/ (plan.json, rows.jsonl, score.txt, log)
and an entry in ~/.config/flybrain/runs.json "experiments".
"""
import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "play"))
DIR = os.path.join(HERE, "battery")
REG = os.path.expanduser("~/.config/flybrain/runs.json")
NOTICE = os.path.join(HERE, "notice.json")
EXT = os.path.expanduser("~/.config/flybrain/ext_experiment.txt")
LABEL = "scored by the pre-registered script, not independently checked"


def log(msg, d=None):
    line = f"{time.strftime('%F %T')} battery: {msg}"
    print(line, flush=True)
    if d:
        with open(os.path.join(d, "log"), "a") as f:
            f.write(line + "\n")


def _reg():
    try:
        return json.load(open(REG))
    except (OSError, ValueError):
        return {"runs": [], "experiments": []}


def _save_reg(r):
    tmp = REG + ".tmp"
    json.dump(r, open(tmp, "w"), indent=1)
    os.replace(tmp, REG)


def _fingerprint(m):
    import numpy as np
    q = np.round(m.astype(np.float64), 4).astype(np.float32)
    return hashlib.sha1(q.tobytes()).hexdigest()[:12]


def plan(now=None, root=ROOT):
    """Schedule tonight's battery for the newest ended lab run without one."""
    import numpy as np
    from flybrain import runinfo
    now = now or dt.datetime.now()
    reg = _reg()
    done = {e.get("battery_run") for e in reg.get("experiments", [])}
    ended = [r for r in reg.get("runs", []) if r.get("ended") and r.get("archive")
             and r["id"] >= runinfo.LAB_FROM and r["id"] not in done]
    if not ended:
        return None
    run = ended[-1]
    name = runinfo.name(run["id"])
    trained = os.path.join(root, run["archive"], "firered.resume.ss.mb.npz")
    d = os.path.join(DIR, f"run{run['id']}")
    os.makedirs(d, exist_ok=True)
    try:
        m = np.load(trained)["m"]
    except (OSError, KeyError, ValueError):
        reg.setdefault("experiments", []).append({"name": f"untrained vs trained ({name})", "run": run["id"],
                                                  "battery_run": run["id"], "status": "skipped: no learned state archived"})
        _save_reg(reg)
        log(f"{name}: no learned state in the archive; skipped", d)
        return "skipped"
    depressed = float((1.0 - m).sum())
    start = dt.datetime.combine((now + dt.timedelta(days=1)).date(), dt.time(0, 30))
    p = {"run": run["id"], "name": name, "trained": trained, "fingerprint": _fingerprint(m),
         "depressed": round(depressed, 1), "start": start.strftime("%Y-%m-%dT%H:%M:%S")}
    json.dump(p, open(os.path.join(d, "plan.json"), "w"), indent=1)
    title = f"Untrained vs trained ({name})"
    q = f"Does {name}'s training change how often the fly turns toward people and doors?"
    json.dump({"name": title, "start": p["start"], "posted": {},
               "lines": {"today": f"🧪 Tonight 00:30 ET: untrained vs {name}'s trained fly brain. {q} "
                                  "Method locked in advance; results on Discord.",
                         "t-1h": f"🧪 In one hour (00:30 ET): untrained vs {name}'s trained fly brain. {q}",
                         "start": f"🧪 Starting now: untrained vs {name}'s trained brain (24 offline runs, "
                                  "same seeds). Method locked in advance."},
               "ext": f"Tonight 00:30: untrained vs {name}'s trained brain. Does training change turns "
                      "to people and doors?"},
              open(NOTICE, "w"), indent=1)
    reg.setdefault("experiments", []).append({"name": title, "run": run["id"], "battery_run": run["id"],
                                              "night": start.date().isoformat(), "status": "scheduled",
                                              "files": f"data/experiments/battery/run{run['id']}/"})
    _save_reg(reg)
    log(f"{name}: scheduled for {p['start']} (depressed {p['depressed']}, fingerprint {p['fingerprint']})", d)
    if depressed < 1.0:
        log(f"{name}: almost nothing learned; the precondition will likely fail (reported as such)", d)
    _notice("today")
    return "planned"


def _tonight(now=None):
    """(dir, plan) for a battery planned for the coming/current 00:30, or None."""
    now = now or dt.datetime.now()
    if not os.path.isdir(DIR):
        return None
    for name in sorted(os.listdir(DIR)):
        d = os.path.join(DIR, name)
        try:
            p = json.load(open(os.path.join(d, "plan.json")))
        except (OSError, ValueError):
            continue
        start = dt.datetime.strptime(p["start"], "%Y-%m-%dT%H:%M:%S")
        if abs((start - now).total_seconds()) < 3 * 3600 and not os.path.exists(os.path.join(d, "score.txt")):
            return d, p
    return None


def _notice(stage, line=None):
    import experiment_notice as en
    if line is not None:
        n = json.load(open(NOTICE))
        n["lines"][stage] = line
        json.dump(n, open(NOTICE, "w"), indent=1)
    try:
        en.post(stage)
    except Exception as e:                       # a notice must never stop the experiment
        log(f"notice {stage} failed: {type(e).__name__}: {e}")


def _set_status(run_id, status):
    reg = _reg()
    for e in reg.get("experiments", []):
        if e.get("battery_run") == run_id:
            e["status"] = status
    _save_reg(reg)


def result_line(score_text, name):
    """The public sentence, from the frozen scorer's own verdict lines."""
    lab = LABEL[0].upper() + LABEL[1:]
    if "PRECONDITION FAILED" in score_text:
        return (f"🧪 Result: {name}'s training left too little in the brain to test "
                f"(precondition not met). {lab}.")
    if "-> SIGNIFICANT" in score_text:
        more = "MORE" if "more with training" in score_text else "FEWER"
        return (f"🧪 Result: {name}'s training made the fly turn toward people and doors {more} often. "
                f"{lab}; details on Discord.")
    if "NULL FINDING" in score_text:
        return (f"🧪 Result: {name}'s training did NOT change how often the fly turns toward people or "
                f"doors. {lab}; details on Discord.")
    return f"🧪 Result: {name}'s experiment was inconclusive (not 'no effect'). {lab}; details on Discord."


def run(now=None, harness=None):
    t = _tonight(now)
    if not t:
        return None
    d, p = t
    _notice("start")
    _set_status(p["run"], "running")
    env = dict(os.environ, NT_TRAINED=p["trained"], NT_OUT=os.path.join(d, "rows.jsonl"),
               OMP_NUM_THREADS="1")
    env.pop("FLYBRAIN_MIRROR", None)
    import numpy as np
    if _fingerprint(np.load(p["trained"])["m"]) != p["fingerprint"]:
        log("preflight FAILED: the trained state changed since planning; not run", d)
        _set_status(p["run"], "not run: preflight failed")
        return "preflight-failed"
    log("preflight OK; starting 24 runs", d)
    rc = (harness or (lambda: subprocess.run(["nice", "-n", "19", sys.executable,
                                              os.path.join(HERE, "naive_trained.py")],
                                             env=env, cwd=HERE).returncode))()
    import io
    import contextlib
    import naive_trained_score as S
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        S.main(os.path.join(d, "rows.jsonl"))
    score = buf.getvalue()
    open(os.path.join(d, "score.txt"), "w").write(score)
    line = result_line(score, p["name"])
    log(f"harness exit {rc}; {line}", d)
    _notice("result", line)
    try:
        open(EXT, "w").write(line.replace("🧪 ", "")[:160])
    except OSError:
        pass
    _set_status(p["run"], "scored (" + LABEL + "): " + line.replace("🧪 Result: ", "")[:160])
    return "scored"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "plan":
        print(plan())
    elif cmd == "notice":
        print(_notice("t-1h") if _tonight() else None)
    elif cmd == "run":
        print(run())
    else:
        sys.exit(__doc__)
