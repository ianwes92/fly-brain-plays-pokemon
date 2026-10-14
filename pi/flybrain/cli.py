"""Command line front end: build, info, bench, probe, run."""
import argparse
import os
import sys
import time

import numpy as np

from . import ports
from .connectome import Connectome, build
from .effectors import BuzzerEffector, ServoBank
from .engine import Brain, Params


def _params(a):
    p = Params()
    for k in ("dt", "tau_m", "tau_syn", "mv_per_synapse", "noise_mv",
              "v_threshold", "refractory"):
        v = getattr(a, k, None)
        if v is not None:
            setattr(p, k, v)
    return p


def _bar(x, width=10):
    filled = int(round(np.clip(x, 0, 1) * width))
    return "█" * filled + "·" * (width - filled)


# --- commands -------------------------------------------------------------
def cmd_build(a):
    build(min_syn=a.min_syn, include_modulatory=a.include_modulatory)


def cmd_info(a):
    cx = Connectome()
    print(cx.summary())
    deg = cx.out_degree()
    print(f"out-degree: mean {deg.mean():.1f}  median {np.median(deg):.0f}  "
          f"max {deg.max():,}  isolated {int((deg == 0).sum()):,}")

    for title, groups in (("sensory", ports.SENSORY),
                          ("motor", ports.MOTOR),
                          ("central", ports.CENTRAL)):
        print(f"\n{title}")
        for name, sel in groups.items():
            idx = cx.select(**sel)
            print(f"  {name:16s} {len(idx):7,d} neurons")


def cmd_bench(a):
    cx = Connectome()
    brain = Brain(cx, _params(a))
    eye = cx.select(**ports.SENSORY["eye"])

    print(cx.summary())
    print(f"dt={brain.p.dt} ms, backend={brain.backend}, "
          f"driving {len(eye):,} photoreceptors at {a.hz} Hz\n")

    brain.run(50)  # let transients settle before timing
    steps = int(round(a.ms / brain.p.dt))

    t0 = time.perf_counter()
    spikes = 0
    for _ in range(steps):
        brain.drive(eye, a.hz)
        spikes += len(brain.step())
    wall = time.perf_counter() - t0

    sim_s = a.ms / 1000.0
    print(f"{steps:,} steps of {brain.p.dt} ms in {wall:.2f}s wall")
    print(f"  {steps/wall:,.0f} steps/s")
    print(f"  {sim_s/wall:.2f}x real time"
          f"  ({'faster than' if wall < sim_s else 'slower than'} the fly)")
    print(f"  {spikes:,} spikes  ({spikes/sim_s:,.0f} spikes/simulated second)")
    active = int((brain.spikes_total > 0).sum())
    print(f"  {active:,} of {cx.n:,} neurons fired ({100*active/cx.n:.1f}%)")


def cmd_probe(a):
    """Stimulate one sensory population and see what downstream lights up."""
    cx = Connectome()
    brain = Brain(cx, _params(a))

    if a.group not in ports.SENSORY:
        raise SystemExit(f"unknown group {a.group!r}; "
                         f"choose from {', '.join(ports.SENSORY)}")
    idx = cx.select(**ports.SENSORY[a.group])
    if not len(idx):
        raise SystemExit(f"{a.group}: no neurons matched")

    print(f"driving {a.group} ({len(idx):,} neurons) at {a.hz} Hz "
          f"for {a.ms:.0f} ms ...")
    steps = int(round(a.ms / brain.p.dt))
    for _ in range(steps):
        brain.drive(idx, a.hz)
        brain.step()

    fired = brain.spikes_total.copy()
    fired[idx] = 0          # ignore the neurons we drove by hand
    order = np.argsort(fired)[::-1]

    print(f"\ntop responding cell types (excluding the driven population):")
    seen, shown = {}, 0
    for i in order:
        if fired[i] == 0 or shown >= a.top:
            break
        ct = cx.labels["cell_type"][i] or cx.labels["class"][i] or "?"
        key = (ct, cx.labels["super_class"][i])
        if key in seen:
            continue
        seen[key] = True
        shown += 1
        hz = fired[i] / (a.ms / 1000.0)
        print(f"  {ct:22s} {cx.labels['super_class'][i]:18s} {hz:7.1f} Hz")

    print("\nregional mean rates:")
    for name, sel in ports.CENTRAL.items():
        g = cx.select(**sel)
        print(f"  {name:18s} {brain.population_rate(g):6.2f} Hz  ({len(g):,})")
    for name in ("descending", "dn_left", "dn_right"):
        g = cx.select(**ports.MOTOR[name])
        print(f"  {name:18s} {brain.population_rate(g):6.2f} Hz  ({len(g):,})")


def cmd_learn_extract(a):
    from . import learning
    learning.extract()


def cmd_condition(a):
    from . import condition
    cx = Connectome()
    print(cx.summary())
    seeds = tuple(range(a.seeds))
    res, names, ppl1, _ = condition.experiment(cx, seeds=seeds)
    condition.report(res, names, ppl1)


def game_names():
    from .game import GAMES
    return GAMES.keys()


def cmd_play(a):
    from . import game, playview
    if a.speed:
        game.GAME_SPEED = a.speed
    rom, sav, state = game.default_paths()
    rom = a.rom or rom
    if a.rom:                      # a ROM given explicitly brings its own companions
        base = os.path.splitext(a.rom)[0]
        sav = a.sav or (base + ".sav" if os.path.exists(base + ".sav") else None)
        state = a.state
    else:
        sav, state = a.sav or sav, a.state or state
    if not os.path.exists(rom):
        raise SystemExit(f"no ROM at {rom}; pass --rom")
    play_paths = None
    if a.game:
        game.set_current_game(a.game)       # remembered: the shortcut plays it from now on
    if a.new or a.cont:
        # A run reset asked for by flag (flybrain/reset.py) happens HERE: the
        # old process has saved and gone, and nothing has looked for the
        # save yet, so nothing can race the archive or the clearing.
        from . import reset as reset_mod
        choices = reset_mod.pending()
        if choices is not None:
            rom_name, sav_name = game.GAMES[a.game or game.current_game()]
            reset_mod.reset(os.path.join(game.PLAY_DIR, sav_name), choices,
                            log=lambda m: print(m, flush=True))
        # Calibrate where the player can walk, then play the new-game line:
        # --new wipes it and starts over, --continue picks it back up.
        play_paths = game.new_game_paths(fresh=a.new, game=a.game)
        resume = game.resume_path_for(play_paths[1])
    else:
        resume = game.resume_path_for(sav or rom)
    cx = Connectome()
    print(cx.summary())
    if a.cont and os.path.exists(resume):
        what = "the saved game"
    else:
        what = "a brand-new game" if (a.new or a.cont) else os.path.basename(rom)
    print(f"playing {what}; q or esc to quit, space to pause")
    g = playview.run(cx, rom, sav, state, fps=a.fps, play_paths=play_paths, resume=resume,
                     twitch=a.twitch, serve=a.serve)
    h = g.hands
    print(f"visited {len(g.visited)} tiles on {len(g.maps)} map(s); "
          f"{h.stats['bouts']} walks, {h.stats['turns']} turns, "
          f"{h.stats['a']} A, {h.stats['b']} B")
    if getattr(g, "restart_wanted", False):
        # SIGUSR1: saved and stopped so newer code can be loaded. The status
        # is what play/launch.sh watches for; see RESTART_STATUS there.
        print("stopped for new code; the launcher brings it back", flush=True)
        raise SystemExit(75)


def cmd_title_auto(a):
    """Hand the stream title back to the fly after editing it by hand.

    The updater yields the moment it finds a title it did not write, so the
    broadcaster is never argued with. The cost is that it stays yielded: one
    hand-edit turns the feature off until somebody deliberately turns it back
    on. That is what this is. Without it the only route was to guess the exact
    string the fly last wrote, or restart the game and hope.

    It works by accepting whatever is live NOW as the fly's own baseline, so
    the next update compares against that and proceeds.
    """
    from . import twitch
    cfg = twitch.load_config()
    if not cfg:
        raise SystemExit("no Twitch login saved -- run `flybrain twitch-login` first")
    live = twitch.current_title(cfg)
    if live is None:
        raise SystemExit("could not read the current title from Twitch")
    twitch.remember_title(live)
    print(f"title handed back to the fly.\n"
          f"  it will treat this as its own and rewrite it on the next update:\n"
          f"    {live}\n"
          f"  (the running game keeps its own yield until it restarts -- "
          f"`bash play/restart.sh` to apply it now)")


def cmd_twitch_login(a):
    """Log in, or log in again to widen the scopes (see twitch.SCOPES).

    The client id and channel are remembered, so a re-login after a scope
    change needs no arguments -- which matters, because a token minted with
    too few scopes fails silently and the fix is exactly this command.
    """
    from . import twitch
    saved = twitch.load_config() or {}
    client_id = a.client_id or saved.get("client_id")
    channel = a.channel or saved.get("channel")
    if not client_id or not channel:
        raise SystemExit("no saved login: pass --client-id and --channel")
    print(f"logging in for #{channel} with scopes: {' '.join(twitch.SCOPES)}")
    twitch.device_login(client_id, channel)


def cmd_test_cheer(a):
    import json
    import urllib.error
    import urllib.request
    if a.what.startswith("free-"):
        text, a.bits = a.text or ("!" + a.what.split("-", 1)[1]), 0
    else:
        text = a.text or f"cheer{a.bits} {a.what}"
    req = urllib.request.Request(
        f"http://127.0.0.1:{a.port}/test-cheer", method="POST",
        data=json.dumps({"user": a.user, "bits": a.bits, "text": text}).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            ev = json.loads(r.read())["event"]
    except urllib.error.HTTPError as e:
        raise SystemExit(f"the game refused the rehearsal: {e.read().decode().strip()}")
    except OSError:
        raise SystemExit("no game answering -- is it running with --serve --twitch?")
    what = {"free": lambda e: f"one free {e[1].upper()} press (subject to the chat cooldowns)",
            "stim": lambda e: f"{e[2]} x {e[1].upper()} press, queued 2 s apart",
            "steer": lambda e: f"{e[1]:.0f} s of steering for {e[2]} (type !left etc. as that user)",
            "cheer": lambda e: f"just a thank-you on screen ({e[1]} bits, nothing bought)"}
    print(f"rehearsed '{text}' with {a.bits} bits -> "
          + (what[ev[0]](ev) if ev else "nothing (not a cheer)"))


def cmd_view(a):
    from . import viz
    cx = Connectome()
    print(cx.summary())
    print("opening window; q or esc to quit")
    viz.run(cx, _params(a), senses=a.senses, period=a.period, hz=a.hz,
            buzzer=not a.no_buzzer, fps=a.fps, threaded=not a.no_threads)


def cmd_run(a):
    cx = Connectome()
    brain = Brain(cx, _params(a))

    # Sensors. Real drivers replace these by subclassing ports.Sensor.
    sensors = []
    for role, pattern, phase in (("eye", "sine", 0.0),
                                 ("ear", "pulse", 0.35),
                                 ("touch", "pulse", 0.7)):
        if role in a.senses:
            s = ports.SyntheticSensor(role, pattern=pattern, phase=phase,
                                      period=a.period, max_hz=a.hz).attach(cx)
            if len(s):
                sensors.append(s)

    effectors = []
    buzzer = None
    if not a.no_buzzer:
        buzzer = BuzzerEffector(threshold=a.buzz_threshold).attach(cx)
        effectors.append(buzzer)
        if not buzzer.enabled:
            print("note: lgpio unavailable, buzzer inert")
    gait = ServoBank(dry_run=True).attach(cx)
    effectors.append(gait)

    watch = {n: cx.select(**s) for n, s in ports.CENTRAL.items()}
    watch["descending"] = cx.select(**ports.MOTOR["descending"])

    print(cx.summary())
    print(f"dt={brain.p.dt} ms  backend={brain.backend}   sensors: "
          f"{', '.join(f'{s.role}({len(s):,})' for s in sensors) or 'none'}")
    print("ctrl-c to stop\n")

    step_ms = brain.p.dt
    ui_every = max(int(100.0 / step_ms), 1)    # redraw ~10x a second
    # Cache the population index arrays the dashboard reduces over; optic_lobe
    # alone is 77k indices and re-gathering it every frame is not free.
    t_wall0 = time.perf_counter()
    lines = 0
    spikes = 0
    spike_window = 0

    try:
        while a.duration <= 0 or brain.t_ms < a.duration * 1000.0:
            for s in sensors:
                s.apply(brain)
            n = len(brain.step())
            spikes += n
            spike_window += n

            if brain.steps % ui_every == 0:
                for e in effectors:
                    e.update(brain, step_ms * ui_every)

                if a.realtime:
                    ahead = brain.t_ms / 1000.0 - (time.perf_counter() - t_wall0)
                    if ahead > 0:
                        time.sleep(ahead)

                wall = time.perf_counter() - t_wall0
                out = _render(brain, cx, sensors, watch, gait, buzzer,
                              wall, spike_window, ui_every * step_ms)
                spike_window = 0
                if lines:
                    sys.stdout.write(f"\033[{lines}A")
                sys.stdout.write(out)
                sys.stdout.flush()
                lines = out.count("\n")
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        for e in effectors:
            e.close()
        for s in sensors:
            s.close()

    wall = time.perf_counter() - t_wall0
    print(f"\nsimulated {brain.t_ms/1000:.1f}s of fly time in {wall:.1f}s "
          f"({brain.t_ms/1000/max(wall,1e-9):.2f}x real time), {spikes:,} spikes")


def _render(brain, cx, sensors, watch, gait, buzzer, wall, win_spikes, win_ms):
    L = []
    rt = brain.t_ms / 1000.0 / max(wall, 1e-9)
    L.append(f"\033[Kt={brain.t_ms/1000:7.2f}s  {rt:5.2f}x real  "
             f"{win_spikes/(win_ms/1000.0):8,.0f} spikes/s")
    L.append("\033[K")

    if sensors:
        L.append("\033[K sensors")
        for s in sensors:
            L.append(f"\033[K  {s.role:8s} {_bar(s.value)} {s.value:4.2f}   "
                     f"{len(s):,} cells")
        L.append("\033[K")

    L.append("\033[K brain")
    for name, idx in watch.items():
        hz = brain.population_rate(idx)
        L.append(f"\033[K  {name:16s} {_bar(hz/20.0)} {hz:6.2f} Hz")
    L.append("\033[K")

    L.append("\033[K output")
    if buzzer:
        mark = "ON " if buzzer._on else "   "
        L.append(f"\033[K  buzzer  {mark} {buzzer.beeps:5d} beeps  "
                 f"(fires above {buzzer.threshold:.1f} Hz)")
    legs = "  ".join(f"{n}{int(v):4d}" for n, v in zip(gait.LEGS, gait.angles))
    L.append(f"\033[K  gait     speed {gait.speed:4.2f}  turn {gait.turn:+5.2f}")
    L.append(f"\033[K  legs     {legs}")
    return "\n".join(L) + "\n"


# --- entry point ----------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="flybrain", description="Whole-brain Drosophila simulation.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def model_args(p):
        p.add_argument("--dt", type=float, help="ms per step (default 1.0)")
        p.add_argument("--tau-m", type=float, dest="tau_m")
        p.add_argument("--tau-syn", type=float, dest="tau_syn")
        p.add_argument("--mv-per-synapse", type=float, dest="mv_per_synapse")
        p.add_argument("--noise-mv", type=float, dest="noise_mv")
        p.add_argument("--v-threshold", type=float, dest="v_threshold")
        p.add_argument("--refractory", type=float)

    p = sub.add_parser("build", help="convert the FlyWire CSVs into the cache")
    p.add_argument("--min-syn", type=int, default=5,
                   help="drop connections weaker than this (default 5)")
    p.add_argument("--include-modulatory", action="store_true",
                   help="treat dopamine/serotonin/octopamine edges as excitatory")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("info", help="connectome and population sizes")
    p.set_defaults(func=cmd_info)

    p = sub.add_parser("bench", help="how fast does it run on this Pi")
    p.add_argument("--ms", type=float, default=1000.0, help="simulated ms")
    p.add_argument("--hz", type=float, default=50.0, help="photoreceptor drive")
    model_args(p)
    p.set_defaults(func=cmd_bench)

    p = sub.add_parser("probe", help="stimulate one sense, see what responds")
    p.add_argument("group", nargs="?", default="eye",
                   help=f"one of: {', '.join(ports.SENSORY)}")
    p.add_argument("--hz", type=float, default=100.0)
    p.add_argument("--ms", type=float, default=500.0)
    p.add_argument("--top", type=int, default=20)
    model_args(p)
    p.set_defaults(func=cmd_probe)

    p = sub.add_parser("learn-extract",
                       help="pull the mushroom body learning circuit from the raw data")
    p.set_defaults(func=cmd_learn_extract)

    p = sub.add_parser("condition",
                       help="does it learn? odour + punishment conditioning, with a control")
    p.add_argument("--seeds", type=int, default=3)
    p.set_defaults(func=cmd_condition)

    p = sub.add_parser("play", help="the fly plays a GBA game (opens a window)")
    p.add_argument("--rom", help="GBA ROM (default: the sandboxed copy in play/)")
    p.add_argument("--sav", help="battery save to load")
    p.add_argument("--state", help="savestate to start from")
    p.add_argument("--fps", type=int, default=15)
    p.add_argument("--new", action="store_true",
                   help="start a brand-new game, discarding any saved one")
    p.add_argument("--continue", dest="cont", action="store_true",
                   help="resume the saved new game, or start one if there is none")
    p.add_argument("--twitch", action="store_true",
                   help="let Twitch chat press pain/pleasure and take the controls "
                        "(credentials in ~/.config/flybrain/twitch.json)")
    p.add_argument("--game", choices=sorted(game_names()),
                   help="which game to play (remembered for next time); each keeps its own save")
    p.add_argument("--speed", type=float, metavar="X",
                   help="how fast the game runs relative to the fly's thinking. "
                        "1.0 = the fly gets a full game frame (16.7 ms) of brain "
                        "time between frames, and the game runs at about 0.8x on "
                        "this machine; higher runs the game nearer normal speed "
                        "by giving the fly proportionally less time per frame")
    p.add_argument("--serve", nargs="?", const=8765, type=int, metavar="PORT",
                   help="serve the window on the network for OBS on another machine "
                        "(default port 8765; open http://<pi>:PORT/ as a Browser Source)")
    p.set_defaults(func=cmd_play)

    p = sub.add_parser("twitch-login",
                       help="log the chat bot in to Twitch (approve a code on any device)")
    p.add_argument("--client-id",
                   help="Client ID of your app at dev.twitch.tv/console (Client Type: Public); "
                        "defaults to the one already saved")
    p.add_argument("--channel", help="your channel name, e.g. mychannel; "
                                     "defaults to the one already saved")
    p.set_defaults(func=cmd_twitch_login)

    p = sub.add_parser("title-auto",
                       help="hand the stream title back to the fly after editing it by hand")
    p.set_defaults(func=cmd_title_auto)

    p = sub.add_parser("test-cheer",
                       help="rehearse a Twitch cheer in the running game (from the Pi only)")
    p.add_argument("what", nargs="?", default="pleasure",
                   choices=("pain", "pleasure", "steer", "none", "free-pain", "free-pleasure"),
                   help="what to rehearse: a cheer for pain/pleasure/steer, or a free "
                        "chat command (free-pain, free-pleasure); default pleasure")
    p.add_argument("--bits", type=int, default=100)
    p.add_argument("--user", default="tester")
    p.add_argument("--text", help="the exact cheer message, overriding 'what'")
    p.add_argument("--port", type=int, default=8765)
    p.set_defaults(func=cmd_test_cheer)

    p = sub.add_parser("view", help="live visual of the brain (opens a window)")
    p.add_argument("--senses", default=["eye", "ear", "touch"],
                   type=lambda s: [x.strip() for x in s.split(",") if x.strip()])
    p.add_argument("--hz", type=float, default=150.0)
    p.add_argument("--period", type=float, default=2000.0)
    p.add_argument("--fps", type=int, default=10,
                   help="redraws per second; lower leaves more time for the sim")
    p.add_argument("--no-buzzer", action="store_true")
    p.add_argument("--no-threads", action="store_true",
                   help="run the simulation on the drawing thread (slower)")
    model_args(p)
    p.set_defaults(func=cmd_view)

    p = sub.add_parser("run", help="live simulation with sensors and outputs")
    p.add_argument("--duration", type=float, default=0.0,
                   help="seconds of fly time; 0 runs until ctrl-c")
    p.add_argument("--senses", default=["eye", "ear", "touch"],
                   type=lambda s: [x.strip() for x in s.split(",") if x.strip()],
                   help="comma-separated afferent groups to drive")
    p.add_argument("--hz", type=float, default=120.0, help="peak afferent rate")
    p.add_argument("--period", type=float, default=3000.0,
                   help="ms period of the stand-in stimuli")
    p.add_argument("--buzz-threshold", type=float, default=0.5)
    p.add_argument("--no-buzzer", action="store_true")
    p.add_argument("--realtime", action="store_true",
                   help="throttle to wall clock instead of running flat out")
    model_args(p)
    p.set_defaults(func=cmd_run)

    a = ap.parse_args(argv)
    return a.func(a) or 0


if __name__ == "__main__":
    sys.exit(main())
