"""Start a new run: archive the old one, then clear exactly what belongs to it.

Ian's plan (2026-10-03): reset the run when YouTube goes live, so viewers
arriving there can take part from the beginning.

HOW IT IS TRIGGERED. Write RESET_FLAG (a small JSON of choices), then deploy
as usual (play/restart.sh). The game checks for the flag at startup -- after
the old process has saved and exited, before the new one looks for a save --
so nothing can race it: there is no moment when the old game is still
writing the snapshot this is deleting. The flag is consumed on success, so a
later restart resumes the new run instead of resetting again.

WHAT A RUN IS, enumerated from the code rather than from what the stream shows:
  cleared (archived first)
    play/<game>.sav, .resume.ss, .resume.ss.mb.npz   the save, snapshot, learned synapses
    ~/.config/flybrain/places.json                    visited areas + furthest marker. NOT
        deleted: a missing file is re-seeded with run 1's route (game._load_places), which
        would put "@ Mt. Moon" over Pallet Town. An explicit empty run is written instead.
    ~/.config/flybrain/diary.json                     the title's "Caught a X" and the diary panel
  reset by themselves, because they are read from the save's RAM
    badges ("Beat Brock!"), team, Pokedex counts
  KEPT, deliberately
    events.json            ids must keep counting: the Discord poster (last id 11) and the
                           YouTube promoter wait for the NEXT id, so restarting at 1 would
                           silence milestones for weeks without an error anywhere
    discord_state.json, youtube_state.json   same reason
    title.json             the title memo; the updater re-baselines it itself
    rom.json               the overworld callback belongs to the ROM, not the run
  Ian's choices, made in the flag:
    keep_leaderboard       leaderboard.json -- the people who trained the fly in run 1
    keep_learning          the learned KC->MBON synapses (otherwise the new fly starts naive)
  Added 2026-10-08 (run 3, Ian: "start fresh ... reset all data"):
    clear_science          archive then clear the science record of the run: the input
                           journals, progress, learning bursts, thought audit
                           (data/inputs), the 10-minute samples, thought counts, daily
                           learning snapshots, and the restart-proof totals
                           (stats_log.json); the Threads milestone list of places seen.
                           data/learning/pre-regime-*.npz is COPIED but never removed:
                           pre-registered experiments load it by path.
    announce               text of a "reset" news event (Discord, YouTube chat)
  The snapshot's .prev (load_with_fallback's second choice) is archived and
  cleared too, so a bad first snapshot can never fall back into the old run.
"""
import json
import os
import shutil
import time

CFG = os.path.expanduser("~/.config/flybrain")
RESET_FLAG = os.path.join(CFG, "reset_run.json")
RUNS_DIR = os.path.expanduser("~/flybrain/runs")
CARRY = os.path.join(CFG, "carry_learning.npz")    # learned synapses taken into a new run
DATA = os.path.expanduser("~/flybrain/data")
SCIENCE = (("inputs", "*.jsonl"), ("samples", "*.jsonl"), ("thoughts", "*.json"),
           ("learning", "*.npz"))
KEEP_IN_PLACE = ("pre-regime-",)                  # experiment inputs, loaded by path


def pending(flag=RESET_FLAG):
    """The flag's choices, or None. Both choices must be stated: a reset that
    guessed whether to wipe the leaderboard would be guessing Ian's call."""
    try:
        with open(flag) as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(d, dict):
        return None
    if not isinstance(d.get("keep_leaderboard"), bool) or not isinstance(d.get("keep_learning"), bool):
        raise SystemExit(f"{flag}: keep_leaderboard and keep_learning must both be true or false")
    return d


def run_files(sav, cfg=CFG):
    from .game import resume_path_for
    res = resume_path_for(sav)
    return {"save": sav, "snapshot": res, "snapshot_prev": res + ".prev",
            "learning": res + ".mb.npz",
            "places": os.path.join(cfg, "places.json"),
            "diary": os.path.join(cfg, "diary.json"),
            "leaderboard": os.path.join(cfg, "leaderboard.json"),
            "events": os.path.join(cfg, "events.json")}


def archive(files, label, runs_dir=RUNS_DIR, now=None):
    """Copy everything about the run somewhere dated. Copies, never moves, and
    every copy is checked before anything is cleared."""
    stamp = time.strftime("%Y%m%d-%H%M%S", time.localtime(now))
    out = os.path.join(runs_dir, f"{label}-{stamp}")
    os.makedirs(out, exist_ok=False)
    kept = []
    for name, path in files.items():
        if os.path.exists(path):
            dst = os.path.join(out, os.path.basename(path))
            shutil.copy2(path, dst)
            if os.path.getsize(dst) != os.path.getsize(path):
                raise OSError(f"archive copy of {path} is incomplete")
            kept.append(name)
    return out, kept


def _register(cfg, out, old_id, new_id, now, reason, log):
    """runs.json (play/run_export.py): close the old run, open the new one, and
    keep the behaviour settings the old run ran under in its archive. Never
    fails the reset: the archive and the clear have already happened."""
    try:
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))
        import run_export
        with open(os.path.join(out, "settings.json"), "w") as f:
            json.dump(run_export.settings(), f, indent=1)
        path = os.path.join(cfg, "runs.json")
        try:
            with open(path) as f:
                reg = json.load(f)
        except (OSError, ValueError):
            reg = {"runs": [], "experiments": []}
        stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
        rel = os.path.relpath(out, os.path.dirname(RUNS_DIR))
        for r in reg["runs"]:
            if r.get("id") == old_id and not r.get("ended"):
                r.update(ended=stamp, reason=f"manual: {reason}" if reason else "manual", archive=rel)
        reg["runs"].append({"id": new_id, "kind": "lab" if new_id >= 4 else "test", "started": stamp,
                            "ended": None, "reason": None, "archive": None})
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(reg, f, indent=1)
        os.replace(tmp, path)
    except Exception as e:                       # noqa: BLE001 -- never fail a reset here
        log(f"reset: run registry not updated ({type(e).__name__}: {e})")


def clear_science(out, data=DATA, cfg=CFG, log=print):
    """Copy the run's science record into the archive, check it, then clear it."""
    import glob
    moved = 0
    for sub, pattern in SCIENCE:
        dst_dir = os.path.join(out, "science", sub)
        for path in sorted(glob.glob(os.path.join(data, sub, pattern))):
            os.makedirs(dst_dir, exist_ok=True)
            dst = os.path.join(dst_dir, os.path.basename(path))
            shutil.copy2(path, dst)
            if os.path.getsize(dst) != os.path.getsize(path):
                raise OSError(f"archive copy of {path} is incomplete")
            if not os.path.basename(path).startswith(KEEP_IN_PLACE):
                os.remove(path)
                moved += 1
    stats = os.path.join(cfg, "stats_log.json")
    if os.path.exists(stats):
        shutil.copy2(stats, os.path.join(out, "stats_log.json"))
        os.remove(stats)
    th = os.path.join(cfg, "threads_state.json")
    try:
        with open(th) as f:
            d = json.load(f)
        shutil.copy2(th, os.path.join(out, "threads_state.json"))
        d["places_seen"] = []
        tmp = th + ".tmp"
        with open(tmp, "w") as f:
            json.dump(d, f)
        os.replace(tmp, th)
    except (OSError, ValueError):
        pass
    log(f"reset: science record archived to {out}/science; {moved} file(s) cleared")


def reset(sav, choices, cfg=CFG, runs_dir=RUNS_DIR, flag=RESET_FLAG, log=print, now=None,
          data=DATA):
    files = run_files(sav, cfg)
    label = str(choices.get("label") or "run")
    out, kept = archive(files, label, runs_dir, now)
    log(f"reset: archived {', '.join(kept)} to {out}")
    if choices.get("clear_science"):
        clear_science(out, data, cfg, log)
    clear = ["save", "snapshot", "snapshot_prev", "diary"]
    if not choices["keep_learning"]:
        clear.append("learning")
    if not choices["keep_leaderboard"]:
        clear.append("leaderboard")
    learned = None
    if choices["keep_learning"] and os.path.exists(files["learning"]):
        # The snapshot is what loads the learning (playview loads it only
        # when resuming), so keeping it means carrying it to where a new run
        # will look: playview loads CARRY on a start with no snapshot.
        learned = os.path.join(cfg, os.path.basename(CARRY))
        shutil.copy2(files["learning"], learned)
        clear.append("learning")
    for name in clear:
        try:
            os.remove(files[name])
        except FileNotFoundError:
            pass
    # An explicit empty run, never a missing file (see the module docstring).
    tmp = files["places"] + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"been": [], "furthest": None, "run": label}, f)
    os.replace(tmp, files["places"])
    # The run's number, for /health "run:" (obsfly splits the archive by it).
    # Run 3 began 2026-10-08 09:16:35; run.json was first written by hand then.
    rf = os.path.join(cfg, "run.json")
    try:
        with open(rf) as f:
            prev_id = int(json.load(f).get("id", 0))
    except (OSError, ValueError, TypeError):
        prev_id = 0
    tmp = rf + ".tmp"
    with open(tmp, "w") as f:
        # announced=False: the new game shows a NEW RUN banner and tells chat once
        json.dump({"id": prev_id + 1, "started": time.strftime("%Y-%m-%dT%H:%M:%S",
                                                               time.localtime(now)),
                   "announced": False}, f)
    os.replace(tmp, rf)
    _register(cfg, out, prev_id, prev_id + 1, now, choices.get("reason"), log)
    # the Twitch science panel's line, so it never names the old run (2026-10-13)
    try:
        from .runinfo import name as run_name
        ext = choices.get("ext") or (f"{run_name(prev_id + 1)} started "
                                     f"{time.strftime('%b %-d %H:%M', time.localtime(now))}: "
                                     f"brand-new game, untrained brain.")
        with open(os.path.join(cfg, "ext_experiment.txt"), "w") as f:
            f.write(ext[:160])
    except OSError:
        pass
    if choices.get("announce"):
        from .events import EventLog
        EventLog(os.path.join(cfg, "events.json")).add("reset", choices["announce"])
    os.remove(flag)
    # The "RESET TOMORROW/TODAY" title segment is over once the reset is done.
    try:
        os.remove(os.path.join(cfg, "title_notice.json"))
    except FileNotFoundError:
        pass
    log(f"reset: cleared {', '.join(clear)}; places emptied"
        + ("; learned synapses carried over" if learned else "")
        + ("; leaderboard kept" if choices["keep_leaderboard"] else "; leaderboard cleared"))
    return out
