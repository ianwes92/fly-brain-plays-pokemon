"""Window for watching the fly play -- laid out for stream viewers.

800x444, so it fits the Pi's 800x480 display under the taskbar and is the
same picture the stream shows:

    game (2x)                          | the fly's brain, live
    ---------------------------------- + ------------------------------
    TEAM: the party, levels and HP,    | MOOD: scared .. happy, what it
    badges / caught / seen             | thinks of people and doors, the
                                       | pleasure and pain buttons, and
                                       | which buttons the fly is pressing

Everything shown is something a viewer can follow. The engineering readouts
(firing rates, motor thresholds, turn counters, the collision minimap) were
removed; they live in the experiments, not on the stream.

Brain and emulator run together on a worker thread (they share one clock, see
game.Game); this thread only draws.
"""
import json
import os
import sys
import threading
import time
from collections import deque

import numpy as np

from . import game as gm
from .game import HEADINGS
from .attrib import Journal, StillLog, party_changes, relation
from .thoughts import ThoughtLog
from .viz import (BG, BRIGHT, LABEL, OUTLINE, PANEL_BG, SYSTEM_COLOR, TEXT,
                  BrainView)

# Tells SDL not to use XInput2 (nothing here needs touch or pointer input).
# This was added to stop an X11 BadWindow crash when closing the window with the
# touchpad. It did NOT stop it: the crash recurred with this set. The actual fix
# is xguard.py, which catches the error so the game still saves. Kept only
# because it is harmless.
os.environ.setdefault("SDL_VIDEO_X11_XINPUT2", "0")


WIN_W, WIN_H = 800, 450      # 16:9 (2026-10-09, obsfly: OBS scales 2.4x with no black bars)
GAME_W, GAME_H = 480, 320
MAP = 320
# The brain map is drawn smaller than its 320 x 320 render, in its own box
# under a header band (title + chat line) and above the story lines, so
# nothing sits on top of it (Ian, 2026-10-09: "not so overlapped").
MAP_TOP = 36
MAP_S = 228
MAP_X = GAME_W + (MAP - MAP_S) // 2
PANEL_Y = 320                 # the team (left) and mood (right) panels start here
MOOD_X = GAME_W
PLEASURE_RECT = (MOOD_X + 10, PANEL_Y + 84, 96, 32)
PAIN_RECT = (MOOD_X + 114, PANEL_Y + 84, 96, 32)
# What the game's own news is worth to the fly, in presses.
GAME_REWARDS = {"caught": ("pleasure", 2), "level": ("pleasure", 1),
                "badge": ("pleasure", 3), "faint": ("pain", 2),
                "evolve": ("pleasure", 2)}
PANEL = (22, 25, 31)
CARD = (32, 36, 45)
DIM = (120, 126, 138)


class GameThread(threading.Thread):
    def __init__(self, g, calibrate=True, play_paths=None, resume=None):
        super().__init__(daemon=True)
        self.g = g
        self.do_calibrate = calibrate
        self.play_paths = play_paths
        self.resume = resume
        self.lock = threading.Lock()
        self.shot = np.zeros_like(g.frame)
        self._outbox, self._local = [], []
        self.running, self.paused = True, False
        self.phase = "calibrating" if calibrate else "playing"

    def run(self):
        gm.cap_threads(2)
        g = self.g
        if self.do_calibrate:
            g.calibrate(verbose=True)
        if self.play_paths:
            with self.lock:
                g.load(*self.play_paths)
                self.shot[:] = 0
        if self.resume:
            with self.lock:
                if os.path.exists(self.resume):
                    used = g.gba.load_with_fallback(self.resume) if hasattr(
                        g.gba, "load_with_fallback") else (g.gba.load_state(self.resume) or self.resume)
                    learned = g.load_learning(self.resume)
                    print(f"resumed from {used}"
                          f"{' (with learned memories)' if learned else ''}", flush=True)
                else:
                    # A new run that was told to keep what the fly learned
                    # (flybrain/reset.py). Loaded once, then removed, so the
                    # run's own snapshot is the source from here on.
                    from .reset import CARRY
                    if g.learning is not None and os.path.exists(CARRY):
                        g.learning.load(CARRY)
                        os.remove(CARRY)
                        print("new run, carrying over learned memories", flush=True)
                g.enable_resume(self.resume)
        from .mood import Mood
        from .nose import Nose, kc_signatures
        g.nose = Nose(g.cx)
        g.mood = Mood(g.cx, signatures=kc_signatures(g.cx, params=g.brain.p))
        # The buttons are the only teacher here: no automatic rewards for
        # leaving rooms or talking, which would teach things nobody chose.
        g.auto_rewards = False
        # The live learning regime (learning.json), applied once the saved
        # learning is loaded so the pre-regime snapshot is of the real fly.
        apply_learning_settings(g.learning, learning_settings(),
                                os.path.expanduser("~/flybrain/data/learning"),
                                log=lambda m: print(m, flush=True))
        self.phase = "playing"
        while self.running:
            if self.paused:
                time.sleep(0.01)
                continue
            sp = g.step()
            if len(sp):
                self._local.append(np.array(sp, dtype=np.int32))
            if g.brain.steps % 17 == 0:           # about once per game frame
                with self.lock:
                    self._outbox.extend(self._local)
                    np.copyto(self.shot, g.frame)
                self._local.clear()
                g.maybe_autosave()

    def drain(self):
        with self.lock:
            out, self._outbox = self._outbox, []
            shot = self.shot.copy()
        return out, shot

    def stop(self):
        self.running = False


def _mood_line(g):
    """mood: valence (-1..+1), its acute (dopamine bursts) and learned (this
    context's history) parts, the label, and the smell of people and doors
    (>= 0.5 is 'in range', as the on-screen labels show it)."""
    m, n = g.mood, g.nose
    sm = n.level if n is not None else {}
    if m is None:
        head = "mood: valence=- acute=- learned=- label=-"
    else:
        head = (f"mood: valence={m.valence:+.3f} acute={m.acute:+.3f} "
                f"learned={m.learned:+.3f} label={m.label}")
    return (head + f" smell_people={sm.get('people', 0.0):.2f} smell_exits={sm.get('exits', 0.0):.2f}"
            f" near={'yes' if max(sm.values() or [0.0]) >= 0.5 else 'no'}")


def _team_fields(team, progress):
    """The /health team line: strongest non-egg Pokemon, badges, caught."""
    live = [m for m in (team or []) if not m.get("egg")]
    star = max(live, key=lambda m: m["level"]) if live else None
    p = progress or {}
    return {"tl": star["level"] if star else "-", "tn": star["name"] if star else "-",
            "tb": int(p.get("badges", 0) or 0), "tc": int(p.get("dex_owned", 0) or 0)}


def _draw_team(screen, font, small, big, team, progress, run_tag="", fav=""):
    """TEAM: up to six Pokemon with level and HP, and the trainer's progress.
    run_tag ("RUN 4 · since Oct 8 13:20", Ian 2026-10-08) sits beside the
    heading so a returning viewer can see this is a new run."""
    import pygame
    pygame.draw.rect(screen, PANEL, (0, PANEL_Y, GAME_W, WIN_H - PANEL_Y))
    head = big.render("TEAM", True, (240, 240, 240))
    screen.blit(head, (10, PANEL_Y + 5))
    # Every third 8 s slot the header line shows the starter scoreboard
    # (fav, "just for fun") in place of the run tag and the progress counts.
    if fav and int(time.time() // 8) % 3 == 2:
        screen.blit(font.render(fav, True, (255, 200, 90)), (10 + head.get_width() + 10, PANEL_Y + 7))
        progress, run_tag = None, ""
    if run_tag:
        screen.blit(font.render(run_tag, True, (255, 200, 90)),
                    (10 + head.get_width() + 10, PANEL_Y + 7))
    if progress:
        info = (f"Badges {progress['badges']}    Caught {progress['dex_owned']}    "
                f"Seen {progress['dex_seen']}")
        t = font.render(info, True, (200, 205, 215))
        screen.blit(t, (GAME_W - 10 - t.get_width(), PANEL_Y + 7))
    team = team or []
    for i in range(6):
        c, r = i % 3, i // 3
        x, y = 8 + c * 157, PANEL_Y + 28 + r * 47
        pygame.draw.rect(screen, CARD, (x, y, 150, 42), border_radius=5)
        if i >= len(team):
            msg = "no Pokemon yet" if (i == 0 and not team) else "-"
            t = small.render(msg, True, (90, 96, 108))
            screen.blit(t, (x + 75 - t.get_width() // 2, y + 14))
            continue
        m = team[i]
        fainted = m["hp"] == 0 and not m["egg"]
        name = "EGG" if m["egg"] else m["name"]
        col = (110, 114, 124) if fainted else (240, 240, 240)
        screen.blit(font.render(name[:11], True, col), (x + 7, y + 4))
        if not m["egg"]:
            lv = font.render(f"Lv {m['level']}", True, (255, 215, 120))
            screen.blit(lv, (x + 143 - lv.get_width(), y + 4))
            frac = m["hp"] / m["max_hp"] if m["max_hp"] else 0.0
            hc = (90, 210, 110) if frac > 0.5 else (240, 200, 70) if frac > 0.2 else (230, 80, 70)
            pygame.draw.rect(screen, (55, 60, 70), (x + 7, y + 25, 136, 6), border_radius=3)
            pygame.draw.rect(screen, hc, (x + 7, y + 25, int(136 * frac), 6), border_radius=3)
            hp = small.render("fainted" if fainted else f"{m['hp']}/{m['max_hp']}", True, DIM)
            screen.blit(hp, (x + 143 - hp.get_width(), y + 30))


SCIENCE_EVERY_S, SCIENCE_FOR_S = 45.0, 15.0     # the SCIENCE card takes TEAM's place this often


BOARD_RESET_FLAG = os.path.expanduser("~/.config/flybrain/reset_leaderboard")


def _reset_board_if_asked(board, flag=BOARD_RESET_FLAG, runs=os.path.expanduser("~/flybrain/runs"),
                          log=print):
    """Start with an empty leaderboard when the flag exists (Ian, 2026-10-07).
    Checked at startup, after the old process has saved its board on the way
    out, so nothing can write the old names back. The old board is archived,
    never just deleted; the flag is consumed so the next restart keeps the
    new board."""
    if not os.path.exists(flag):
        return False
    if os.path.exists(board):
        os.makedirs(runs, exist_ok=True)
        dst = os.path.join(runs, "leaderboard-" + time.strftime("%Y%m%d-%H%M%S") + ".json")
        import shutil
        shutil.copy2(board, dst)
        os.remove(board)
        log(f"leaderboard reset: old board archived to {dst}")
    os.remove(flag)
    return True


class ScienceCard:
    """The numbers behind the SCIENCE card (Ian, 2026-10-07), refreshed every
    30 s from files the game already writes -- nothing here is computed on the
    hot path.
      own     today's steps, and the share the fly made on its own
              (data/samples: restart-proof totals of attrib's clean/assisted)
      taught  the learning ledger's current shares by source (learning.py),
              plus today's top human trainers by synaptic change actually
              booked to them (data/inputs/learning.jsonl)"""

    REFRESH_S = 30.0
    SAMPLES = os.path.expanduser("~/flybrain/data/samples")
    LEARN = os.path.expanduser("~/flybrain/data/inputs/learning.jsonl")

    def __init__(self):
        self._t = -1e9
        self.own = None          # (steps today, fly-alone share) or None
        self.trainers = []       # [(who, change)] today, biggest first

    def refresh(self, now=None):
        now = time.time() if now is None else now
        if now - self._t < self.REFRESH_S:
            return
        self._t = now
        day = time.strftime("%Y-%m-%d", time.localtime(now))
        rows = []
        try:
            with open(os.path.join(self.SAMPLES, day + ".jsonl")) as f:
                rows = [json.loads(x) for x in f if x.strip()]
        except (OSError, ValueError):
            pass
        rows = [r for r in rows if "tiles_clean" in r and "tiles_assisted" in r]
        if len(rows) >= 2:
            a, b = rows[0], rows[-1]
            c = b["tiles_clean"] - a["tiles_clean"]
            h = b["tiles_assisted"] - a["tiles_assisted"]
            self.own = (c + h, c / (c + h)) if c + h > 0 else None
        start = time.mktime(time.strptime(day, "%Y-%m-%d"))
        who = {}
        try:
            with open(self.LEARN) as f:
                for x in f:
                    try:
                        r = json.loads(x)
                    except ValueError:
                        continue
                    if r.get("t", 0) >= start and r.get("source") == "human":
                        w = r.get("who") or "?"
                        who[w] = who.get(w, 0.0) + max(0.0, r.get("booked_to_source") or 0.0)
        except OSError:
            pass
        self.trainers = sorted(((k, v) for k, v in who.items() if v > 0), key=lambda kv: -kv[1])[:3]


TAUGHT_COLORS = (("human", "chat", (160, 120, 255)), ("game", "game", (240, 200, 80)),
                 ("rule", "stillness rule", (230, 95, 80)),
                 ("before_ledger", "before today", (110, 116, 128)))


def _draw_science(screen, font, small, big, card, learning, brain_rates):
    """SCIENCE: what the fly did on its own, who taught it, and its brain now.
    Shares only, never invented: a part with no data says so."""
    import pygame
    pygame.draw.rect(screen, PANEL, (0, PANEL_Y, GAME_W, WIN_H - PANEL_Y))
    screen.blit(big.render("SCIENCE", True, (240, 240, 240)), (10, PANEL_Y + 5))
    t = small.render("measured, not scripted", True, DIM)
    screen.blit(t, (GAME_W - 10 - t.get_width(), PANEL_Y + 10))

    def bar(y, parts, label):
        screen.blit(font.render(label, True, (215, 220, 230)), (10, y))
        bx, bw = 120, GAME_W - 130
        pygame.draw.rect(screen, CARD, (bx, y + 3, bw, 10), border_radius=4)
        x = bx
        for frac, col in parts:
            w = int(round(bw * max(0.0, frac)))
            if w:
                pygame.draw.rect(screen, col, (x, y + 3, w, 10))
                x += w

    # 1. on its own
    y = PANEL_Y + 30
    if card.own:
        n, own = card.own
        bar(y, [(own, (90, 210, 120)), (1 - own, (160, 120, 255))], "Steps today")
        t = small.render(f"{n:,} steps: {own:.0%} fly alone, {1 - own:.0%} chat helped",
                         True, (200, 205, 215))
    else:
        bar(y, [], "Steps today")
        t = small.render("counting... (needs 10 minutes)", True, DIM)
    screen.blit(t, (120, y + 15))

    # 2. who taught it
    y = PANEL_Y + 62
    out = (learning.ledger.get("outstanding") if learning is not None else None) or {}
    tot = sum(out.values())
    parts = [(out.get(k, 0.0) / tot, col) for k, _, col in TAUGHT_COLORS] if tot else []
    bar(y, parts, "Who taught it")
    if tot:
        bits = [f"{name} {out.get(k, 0.0) / tot:.0%}" for k, name, _ in TAUGHT_COLORS
                if out.get(k, 0.0) / tot >= 0.005]
        line = "  ".join(bits)
        if card.trainers:
            line += "   top today: " + ", ".join(w for w, _ in card.trainers)
    else:
        line = "nothing learned yet"
    while line and small.size(line)[0] > GAME_W - 130:
        line = line[:-1]
    screen.blit(small.render(line, True, (200, 205, 215)), (120, y + 15))

    # 3. brain now: mean firing per region, log-scaled bars
    y = PANEL_Y + 94
    screen.blit(font.render("Brain now", True, (215, 220, 230)), (10, y))
    regions = (("eyes", "sc_optic"), ("centre", "sc_central"), ("memory", "kc"),
               ("reward", "pam"), ("punish", "ppl1"), ("motor", "sc_descending"))
    cw = (GAME_W - 130) // len(regions)
    for i, (lab, key) in enumerate(regions):
        hz = brain_rates.get(key)
        x = 120 + i * cw
        screen.blit(small.render(lab, True, DIM), (x, y + 13))
        pygame.draw.rect(screen, CARD, (x, y + 2, cw - 8, 9), border_radius=3)
        if hz is not None:
            import math
            f = min(1.0, math.log10(1.0 + hz) / math.log10(1.0 + 20.0))
            pygame.draw.rect(screen, (120, 190, 240), (x, y + 2, int((cw - 8) * f), 9), border_radius=3)


def _draw_feelings(screen, font, small, big, mood, hands, nose, flash):
    """MOOD: how the fly feels, what it has learned about people and doors,
    the pleasure/pain buttons, and the buttons the fly itself is pressing."""
    import pygame
    x0, w = MOOD_X, WIN_W - MOOD_X
    pygame.draw.rect(screen, PANEL, (x0, PANEL_Y, w, WIN_H - PANEL_Y))
    pygame.draw.line(screen, (45, 50, 60), (x0, PANEL_Y), (x0, WIN_H))
    screen.blit(big.render("MOOD", True, (240, 240, 240)), (x0 + 10, PANEL_Y + 5))
    v = max(-1.0, min(1.0, mood.valence)) if mood is not None else 0.0
    label = mood.label if mood is not None else "waking up"
    col = (230, 100, 90) if v < -0.15 else (100, 220, 130) if v > 0.15 else (220, 220, 225)
    t = big.render(label, True, col)
    screen.blit(t, (WIN_W - 10 - t.get_width(), PANEL_Y + 5))

    # scared .. happy
    bx, by, bw, bh = x0 + 10, PANEL_Y + 30, w - 20, 9
    for i in range(bw):
        f = i / (bw - 1) * 2 - 1
        base = (205, 65, 60) if f < 0 else (60, 190, 95)
        pygame.draw.line(screen, tuple(int(70 + (cc - 70) * abs(f)) for cc in base),
                         (bx + i, by), (bx + i, by + bh))
    mx = int(bx + (v + 1) / 2 * (bw - 1))
    pygame.draw.polygon(screen, (250, 250, 250), [(mx, by - 1), (mx - 5, by - 7), (mx + 5, by - 7)])
    pygame.draw.rect(screen, (250, 250, 250), (mx - 1, by, 3, bh))
    screen.blit(small.render("scared", True, (200, 110, 100)), (bx, by + bh + 1))
    t = small.render("happy", True, (110, 200, 130))
    screen.blit(t, (bx + bw - t.get_width(), by + bh + 1))

    # what it has learned: fear (red, left) .. like (green, right)
    y = PANEL_Y + 60
    smells = nose.level if nose is not None else {}
    for j, (lab, kind) in enumerate((("People", "people"), ("Doors", "exits"))):
        x = x0 + 10 + j * 155
        near = smells.get(kind, 0.0) >= 0.5
        screen.blit(font.render(lab, True, (255, 235, 150) if near else (200, 205, 215)), (x, y))
        cx, cw = x + 56, 90
        pygame.draw.rect(screen, (55, 60, 70), (cx, y + 5, cw, 6), border_radius=3)
        o = max(-1.0, min(1.0, hands.opinion.get(kind, 0.0)))
        span = int(cw / 2 * abs(o))
        if o < 0:
            pygame.draw.rect(screen, (230, 85, 75), (cx + cw // 2 - span, y + 5, span, 6))
        elif o > 0:
            pygame.draw.rect(screen, (90, 215, 120), (cx + cw // 2, y + 5, span, 6))
        pygame.draw.line(screen, (220, 220, 225), (cx + cw // 2, y + 2), (cx + cw // 2, y + 13))

    # pleasure / pain (click, or keys g / b on the Pi)
    for rect, text, base, lit, kind in ((PLEASURE_RECT, "PLEASURE", (40, 105, 58), (90, 230, 120), "pleasure"),
                                        (PAIN_RECT, "PAIN", (115, 40, 36), (250, 90, 70), "pain")):
        pygame.draw.rect(screen, lit if flash.get(kind, 0) > 0 else base, rect, border_radius=5)
        t = font.render(text, True, (240, 240, 240))
        screen.blit(t, (rect[0] + rect[2] // 2 - t.get_width() // 2, rect[1] + rect[3] // 2 - 7))

    # the fly's own buttons
    keys = hands.keys
    cxp, cyp, s_ = x0 + 250, PANEL_Y + 106, 10
    for mask, dx, dy, _ in gm.HEADINGS:
        pygame.draw.rect(screen, (255, 200, 90) if keys & mask else (55, 60, 70),
                         (cxp + dx * s_ - s_ // 2, cyp + dy * s_ - s_ // 2, s_, s_))
    pygame.draw.rect(screen, (55, 60, 70), (cxp - s_ // 2, cyp - s_ // 2, s_, s_))
    for lab, mask, bxx in (("A", gm.A, x0 + 280), ("B", gm.B, x0 + 302)):
        pygame.draw.circle(screen, (255, 110, 90) if keys & mask else (55, 60, 70), (bxx, cyp), 8)
        t = small.render(lab, True, (240, 240, 240))
        screen.blit(t, (bxx - t.get_width() // 2, cyp - 7))
    t = small.render("fly presses", True, DIM)
    screen.blit(t, (x0 + 276 - t.get_width() // 2, PANEL_Y + 111))  # under the buttons, clear of all


def _draw_banner(screen, big, text):
    """The news, across the game: a catch, a level, a badge, a wipe."""
    import pygame
    t = big.render(text, True, (255, 225, 130))
    bg = pygame.Surface((t.get_width() + 24, t.get_height() + 12), pygame.SRCALPHA)
    bg.fill((0, 0, 0, 205))
    pygame.draw.rect(bg, (255, 215, 120), bg.get_rect(), 2, border_radius=6)
    x, y = GAME_W // 2 - bg.get_width() // 2, GAME_H // 2 - 40
    screen.blit(bg, (x, y))
    screen.blit(t, (x + 12, y + 6))


def story_lines(progress, places_been, furthest, paid):
    """The run as a story a newcomer can read in one glance.

    The fly's slow progress IS the content, but nothing on screen said so: a
    stranger saw a sprite in a field with no idea whether this was minute
    three or week three. Every line here comes from something that survives a
    restart -- the persisted visited-areas set, the persisted leaderboard, the
    save's own progress -- so a counter never resets to zero in front of the
    people it is meant to impress. No date is guessed: there is no trustworthy
    record of when this run began, so there is no "Day N".
    """
    from .twitch import PLACES
    lines = []
    n = len(places_been or ())
    where = PLACES.get(furthest) if furthest is not None else None
    if n and where:
        lines.append(f"{n} AREAS EXPLORED  -  FURTHEST: {where.upper()}")
    elif n:
        lines.append(f"{n} AREAS EXPLORED")
    if paid is not None:
        board = dict(getattr(paid, "trainers", None) or {})
        people, times = len(board), int(sum(board.values())) if board else 0
        if times:
            lines.append(f"{people} {'PERSON HAS' if people == 1 else 'PEOPLE HAVE'} "
                         f"TRAINED IT  -  {times} TIMES")
    p = progress or {}
    badges, caught = int(p.get("badges", 0) or 0), int(p.get("dex_owned", 0) or 0)
    if badges or caught:
        lines.append(f"{badges} BADGE{'' if badges == 1 else 'S'}  -  "
                     f"{caught} POKEMON CAUGHT")
    return lines


def _draw_story(screen, small, lines):
    """The run's story, in the shared space under the brain."""
    import pygame
    for i, row in enumerate(lines[:3]):
        t = small.render(row[:58], True, (255, 225, 150))
        bg = pygame.Surface((t.get_width() + 10, t.get_height() + 2), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 170))
        y = PANEL_Y - 6 - (min(len(lines), 3) - i) * 16
        screen.blit(bg, (GAME_W + 6, y))
        screen.blit(t, (GAME_W + 11, y + 1))


def _draw_under_brain(screen, small, paid, diary, story=None):
    """The space under the brain, shared three ways: who is playing along,
    what has happened to the fly, and how far the run has got. They take turns
    rather than fighting for the room -- a newcomer needs the story and the
    history, a regular wants the leaderboard."""
    import time as _time
    turn = int(_time.monotonic() / 9.0) % 3
    if turn == 0 and story:
        _draw_story(screen, small, story)
    elif turn == 1 and diary is not None and diary.entries:
        _draw_diary(screen, small, diary)
    else:
        _draw_leaderboard(screen, small, paid)


def _draw_diary(screen, small, diary):
    """What the fly has been up to, so the stream has a past."""
    import pygame
    rows = [f"{ago}   {text}" for ago, _, text in diary.recent(2)]
    for i, row in enumerate(rows):
        t = small.render(row[:58], True, (215, 220, 230))
        bg = pygame.Surface((t.get_width() + 10, t.get_height() + 2), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 160))
        y = PANEL_Y - 6 - (len(rows) - i) * 16
        screen.blit(bg, (GAME_W + 6, y))
        screen.blit(t, (GAME_W + 11, y + 1))


def _draw_leaderboard(screen, small, paid):
    """Who is playing along, in the space under the brain."""
    trainers, drivers = paid.top("trainers"), paid.top("drivers")
    if not trainers and not drivers:
        return
    import pygame
    rows = []
    if trainers:
        rows.append("trained: " + "  ".join(f"{n} {c}" for n, c in trainers))
    if drivers:
        rows.append("drove: " + "  ".join(f"{n} {int(sec)}s" for n, sec in drivers))
    for i, row in enumerate(rows):
        t = small.render(row, True, (215, 220, 230))
        bg = pygame.Surface((t.get_width() + 10, t.get_height() + 2), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 160))
        y = PANEL_Y - 6 - (len(rows) - i) * 16
        screen.blit(bg, (GAME_W + 6, y))
        screen.blit(t, (GAME_W + 11, y + 1))


# What the fly just decided, named by the branch of decide() that actually
# ran. Nothing here is invented: each line corresponds to a counter the
# decision itself incremented, in the order the fly weighs them.
THOUGHT_STATS = ("stalled", "approach", "avoid", "wall", "toward_exit", "away_from_exit",
                 "toward_person", "away_from_person", "turns")


def _thought(h, seen, solid=None):
    """A short line of what the fly just did, or None if nothing new.

    Rewritten 2026-10-07 after an audit against the tile map
    (data/experiments/thought_audit_score.py, 500 thoughts after the turn fix):
      doors       a door in range 98%, on the named side 83% -> stated as fact
      people      someone in range 47%, whatever the signal strength -> said
                  as what the fly THINKS, not as a fact
      obstacles   the readout cannot tell left from right (it said "left" ~90%
                  of the time) and the avoid side is not chosen from the
                  wall's side either -> no side is claimed for the obstacle;
                  the line says which way the character actually turned
      stalls      something solid ahead only 64% -> "walked into something"
                  only when the map confirms it (`solid`), else what the fly
                  believes ("thinks it's blocked")
    `solid` is whether the faced tile was solid when it decided; None (no
    map, e.g. tests) keeps the belief wording."""
    d = {}
    for k in THOUGHT_STATS:
        n = h.stats.get(k, 0)
        d[k] = n - seen.get(k, n)
        seen[k] = n

    def side(signal):                      # + reads as "on the left"
        return "left" if signal > 0 else "right"

    turned = getattr(h, "turned_side", None)
    turning = f"turning {turned}" if turned else "turning"
    if d["stalled"]:
        return "walked into something - stopping" if solid else "thinks it's blocked - stopping"
    if d["approach"]:
        return "a way out, straight ahead"
    if d["avoid"]:
        return f"something close - {turning}"
    if d["toward_exit"]:
        return f"a door on the {side(h.exits)} - going for it"
    if d["away_from_exit"]:
        return f"a door on the {side(h.exits)} - keeping away"
    if d["toward_person"]:
        return f"thinks someone's on the {side(h.people)} - going over"
    if d["away_from_person"]:
        return f"thinks someone's on the {side(h.people)} - keeping away"
    if d["wall"]:
        return f"wall ahead - {turning}" if solid else f"thinks there's a wall ahead - {turning}"
    if d["turns"]:
        return f"wandering - {turning}" if turned else "wandering"
    return None


def _build_id():
    """Hash of the running code (flybrain/*.py) and the behaviour switches,
    taken at start: rows in the raw archive can be split by which code made
    them -- the turn reversal of 2026-10-05..07 is why this matters."""
    import glob, hashlib
    h = hashlib.sha1()
    for f in sorted(glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), "*.py"))):
        with open(f, "rb") as fh:
            h.update(fh.read())
    env = ",".join(f"{k}={os.environ[k]}" for k in sorted(os.environ) if k.startswith("FLYBRAIN_"))
    return f"code={h.hexdigest()[:10]} started={time.strftime('%Y-%m-%dT%H:%M:%S')} env=[{env}]"


LEARN_CFG = os.path.expanduser("~/.config/flybrain/learning.json")


def learning_settings(path=LEARN_CFG):
    """{"recover_always": bool, "tau_recover_s": float} -- the live learning
    regime (Ian, 2026-10-07: learning fades, tau 2 h of brain time). Read by
    the play window only; experiments never read it."""
    try:
        with open(path) as f:
            d = json.load(f)
        return {"recover_always": bool(d.get("recover_always")),
                "tau_recover_s": float(d.get("tau_recover_s", 600.0))}
    except (OSError, ValueError, TypeError):
        return None


def apply_learning_settings(learning, cfg, snap_dir, log=print, now=None):
    """Switch the live fly to the configured regime. The first time a regime
    is applied, the learned state is snapshotted BEFORE anything fades, and
    the change is written into the ledger and the log."""
    if learning is None or not cfg:
        return False
    learning.recover_always = cfg["recover_always"]
    learning.p.tau_recover = cfg["tau_recover_s"] * 1000.0
    change = (f"recover_always={cfg['recover_always']} tau_recover={cfg['tau_recover_s']:.0f}s")
    regs = learning.ledger.get("regimes") or []
    if regs and regs[-1].get("change") == change:
        return False
    now = time.time() if now is None else now
    os.makedirs(snap_dir, exist_ok=True)
    path = os.path.join(snap_dir, "pre-regime-" + time.strftime("%Y%m%d-%H%M%S", time.localtime(now)) + ".npz")
    np.savez_compressed(path, m=learning.m.astype(np.float32), ledger=learning._ledger_json())
    learning.note_regime(change, now)
    log(f"learning regime: {change} (state before it saved to {path})")
    return True


BUILD = _build_id() + f" learning={json.dumps(learning_settings(), sort_keys=True)}"


def run_line(path=os.path.expanduser("~/.config/flybrain/run.json")):
    """run: id=N started=... -- which run this is (reset.py numbers them), so
    the raw /health archive can be split by run without timestamps."""
    try:
        with open(path) as f:
            d = json.load(f)
        from . import runinfo
        ends = "-"
        if int(d["id"]) >= runinfo.LAB_FROM:
            import datetime
            s = datetime.datetime.strptime(d["started"], "%Y-%m-%dT%H:%M:%S")
            e = (s + datetime.timedelta(days=runinfo.RUN_DAYS)).date()
            ends = f"{e.isoformat()}T13:00:00"          # play/run_clock.RESET_HOUR
        return (f"run: id={int(d['id'])} started={d.get('started', '-')} ends={ends} "
                f"day={runinfo.day(d)} name={runinfo.name(d['id']).replace(' ', '_')}")
    except (OSError, ValueError, KeyError, TypeError):
        return "run: id=- started=-"


class BrainSummary:
    """Cheap, already-computed numbers for /health (obsfly's raw archive):
    mean firing (Hz) per FlyWire super_class and for the learning circuit
    (Kenyon cells, MBONs, reward PAM and punishment PPL1 dopamine neurons,
    descending neurons), the retina's input channels, and the motor side.
    Group indices are built once; a summary is cached for a second, so a
    burst of /health requests costs one pass over the 139k rates."""

    def __init__(self, cx):
        L = cx.labels
        sc, cl = L["super_class"], L["class"]
        name = np.where(L["cell_type"] != "", L["cell_type"], L["hemibrain_type"])
        self.groups = {f"sc_{k}": np.flatnonzero(sc == k) for k in sorted(set(sc.tolist())) if k}
        self.groups.update(kc=np.flatnonzero(cl == "Kenyon_Cell"), mbon=np.flatnonzero(cl == "MBON"),
                           pam=np.flatnonzero((cl == "DAN") & np.char.startswith(name, "PAM")),
                           ppl1=np.flatnonzero((cl == "DAN") & np.char.startswith(name, "PPL1")),
                           cx_central_complex=np.flatnonzero(cl == "CX"))
        self._cache = ("", -1e9)
        self.rates = {}

    def lines(self, g):
        now = time.monotonic()
        if now - self._cache[1] < 1.0:
            return self._cache[0]
        r = g.brain.rate
        self.rates = {k: float(r[ix].mean()) for k, ix in self.groups.items() if len(ix)}
        brain = " ".join(f"{k}={v:.2f}" for k, v in self.rates.items())
        e, h = g.eyes, g.hands
        eyes = " ".join(f"{k}={float(v):.1f}" for k, v in sorted(e.rates.items()))
        keys = int(getattr(g, "fly_keys", 0) or 0)
        names = [n for n, m in (("up", gm.UP), ("down", gm.DOWN), ("left", gm.LEFT),
                                ("right", gm.RIGHT), ("a", gm.A), ("b", gm.B)) if keys & m]
        motor = (f"motor: fly_keys={'+'.join(names) or '-'} chat_keys={int(getattr(g, 'chat_keys', 0) or 0)} "
                 f"heading={HEADINGS[h.heading][3]} bout={h.bout} walk={h.walk:.1f} "
                 f"turn={h.turn:.1f} turn_mid={getattr(h, 'turn_mid', 0.0):.1f} "
                 f"obstacle={h.obstacle:.1f} people={h.people:.1f} exits={h.exits:.1f} "
                 f"wall_ahead={h.wall_ahead:.1f} exit_ahead={h.exit_ahead:.1f} stall={h.stall:.1f} "
                 f"turn_scale={getattr(h, 'turn_scale', 1.0):.2f}")
        out = f"brain_hz: {brain}\neyes_in: {eyes}\n{motor}\n{turn_sides(h)}"
        self._cache = (out, now)
        return out


TURN_SOURCES = ("avoid", "spontaneous", "wall", "toward_exit", "away_from_exit",
                "toward_person", "away_from_person")


def turn_sides(h):
    """turn_sides: each rule that chose a turn, as left/right, since the game's last
    calibration. The side the fly DECIDED (its own frame), whoever held the keys."""
    s = getattr(h, "sides", {}) or {}
    keys = TURN_SOURCES + (("other",) if s.get("other_left") or s.get("other_right") else ())
    return "turn_sides: " + " ".join(f"{k}={s.get(k + '_left', 0)}/{s.get(k + '_right', 0)}"
                                     for k in keys)


LIVE_REGIONS = (("eyes", "sc_optic"), ("cx", "cx_central_complex"), ("kc", "kc"),
                ("mbon", "mbon"), ("pam", "pam"), ("ppl1", "ppl1"),
                ("descending", "sc_descending"), ("motor", "sc_motor"))
HEADING_DEG = {"up": 0, "right": 90, "down": 180, "left": 270}
_live_cache = {"at": -1.0, "out": None}


_overlay_cache = {"at": -1.0, "out": None}


def _experiment_now(path=None, now=None):
    """{name, start, status pending|running|done} from data/experiments/notice.json
    (play/experiment_notice.py), or None. 'done' once the result line is out;
    dropped a day after that so the chip does not linger."""
    import time as _t
    p = path or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "data", "experiments", "notice.json")
    try:
        with open(p) as f:
            n = json.load(f)
        start = _t.mktime(_t.strptime(n["start"], "%Y-%m-%dT%H:%M:%S"))
    except (OSError, ValueError, KeyError):
        return None
    now = _t.time() if now is None else now
    posted = n.get("posted") or {}
    if "result" in posted:
        if now - posted["result"] > 86400:
            return None
        status = "done"
    else:
        status = "pending" if now < start else "running"
    return {"name": n.get("name"), "start": n["start"], "status": status}


def _press_vs(log, text, t, window=2.0):
    """together|against|apart|fly_idle|full for a chat-feed line, from the
    presses the window loop recorded as it applied them (None for stims)."""
    for when, who, key, vs in reversed(log):
        if abs(when - t) <= window and text.startswith(f"{who}  {key.upper()}"):
            return vs
    return None


def _starters_now():
    from . import starters
    d = starters.load()
    return {"board": starters.scoreboard(d), "tally": starters.tally(d),
            "runs": {k: v.get("species") for k, v in d.items()}}


def overlay_snapshot(g, ui, now=None):
    """GET /overlay (obsfly's OBS-side HUD, 2026-10-09): everything the window
    overlay draws, as data. Field names are a contract (add, never rename).
    `ui` holds what only the window loop knows: banner, call, ticker, paid,
    diary, science, fav, run, stim, thought, still_s, learning."""
    now = time.monotonic() if now is None else now
    if _overlay_cache["out"] is not None and now - _overlay_cache["at"] < 0.5:
        return _overlay_cache["out"]
    import time as _t
    from . import runinfo
    from .twitch import EXPLAIN_S, EXPLAINERS, PRICES
    paid, diary, sci = ui.get("paid"), ui.get("diary"), ui.get("science")
    team = [{"species": m.get("name"), "nickname": m.get("nickname"), "lv": m.get("level"),
             "hp": m.get("hp"), "hp_max": m.get("max_hp"), "egg": bool(m.get("egg"))}
            for m in (g.team_now or g.team_calm or [])]
    prog = g.progress_now or g.progress_calm or {}
    r = runinfo.current()
    chat_state = paid.chat_status() if paid is not None else (False, None, 0, {})
    may_drive, last, presses, tally = chat_state
    counts = "   ".join(f"{ARROWS.get(k, k.upper())} {n}" for k, n in
                        sorted(tally.items(), key=lambda kv: -kv[1])[:4])
    slot = int(_t.monotonic() / EXPLAIN_S)
    rot = list(EXPLAINERS) + list(PRICES) + ([f"last minute:   {counts}"] if counts else [])
    banner = ui.get("banner")
    call = ui.get("call")
    learning = ui.get("learning")
    out_l = (learning.ledger.get("outstanding") if learning is not None else None) or {}
    tot = sum(out_l.values()) or 0.0
    seeing = [{"label": lab, "where": None if w is None else round(float(w), 3), "lit": bool(lit)}
              for lab, w, _at, lit in seeing_rows(g.hands)]
    feed = list(getattr(paid, "chat_log", []) or [])[-12:] if paid is not None else []
    clock_now = getattr(paid, "clock", _t.monotonic)()
    out = {
        "t": round(_t.time(), 2),
        "team": team,
        "progress": {"badges": int(prog.get("badges", 0) or 0), "caught": int(prog.get("dex_owned", 0) or 0),
                     "seen": int(prog.get("dex_seen", 0) or 0)},
        "run": ({"id": r["id"], "name": runinfo.name(r["id"]), "day": runinfo.day(r),
                 "of": runinfo.RUN_DAYS if r["id"] >= runinfo.LAB_FROM else None,
                 "tag": runinfo.tag()} if r else None),
        "starters": _starters_now(),
        "banner": ({"text": banner[0]} if banner else None),
        "call": ({"title": call[0], "ask": call[1]} if call else None),
        "ticker": [t for t, _n in (ui.get("ticker") or [])],
        "bar": {"headline": (f"{last[1]}: {last[0].upper()}" if last
                             else "CHAT DRIVES THE FLY -- !left !right !up !down !a !b"),
                "rotating": rot, "now": rot[slot % len(rot)] if rot else None,
                "every_s": EXPLAIN_S, "chat_may_drive": bool(may_drive), "presses": presses,
                "last_minute": dict(tally)},
        "seeing": seeing,
        "thinking": ui.get("thought") or None,
        "still_s": ui.get("still_s"),
        "science": {"steps_today": sci.own[0] if sci and sci.own else None,
                    "fly_share": round(sci.own[1], 3) if sci and sci.own else None,
                    "taught": ({k: round(v / tot, 3) for k, v in out_l.items()} if tot else {}),
                    "trainers_today": [[w, round(c, 1)] for w, c in (sci.trainers if sci else [])[:5]]},
        "story": ui.get("story") or [],
        "milestones": [{"t": e[0], "kind": e[1], "text": e[2]} for e in (diary.entries if diary else [])[-6:]],
        # t is the controller's own clock (monotonic); ts is epoch (added 2026-10-09)
        "chat_feed": [{"t": round(t_, 2), "ts": round(_t.time() - (clock_now - t_), 2), "text": txt,
                       "vs": _press_vs(ui.get("press_vs") or (), txt, t_)}
                      for t_, txt in feed],
        "leaderboard": sorted(((k, v) for k, v in (getattr(paid, "trainers", {}) or {}).items()),
                              key=lambda kv: -kv[1])[:5] if paid is not None else [],
        "stim": dict(ui["stim"]) if ui.get("stim") else None,
        "pull": getattr(g, "pull", None),
        # added 2026-10-09 (obsfly): the learned opinions the MOOD needles show,
        # and the experiment the public notices are about
        "opinions": ({k: round(float(v), 3) for k, v in g.mood.opinion.items() if k in ("people", "exits")}
                     if getattr(g, "mood", None) is not None and g.mood.opinion else None),
        "experiment": _experiment_now(),
    }
    _overlay_cache.update(at=now, out=out)
    return out


class SpikeBits:
    """Which of obsfly's 4000 sampled neurons (data/brain_layout.json) spiked
    since the last /live call, as a bitset (2026-10-09, the GPU brain layer).
    add() runs on the window thread every frame; take() on the HTTP thread."""

    def __init__(self, n_neurons, path=None):
        import base64, threading
        self._b64, self._lock = base64.b64encode, threading.Lock()
        self.map = np.full(n_neurons, -1, dtype=np.int32)
        self.bits = None
        try:
            data = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
            p = path or os.path.join(data, "brain_layout.json")
            if not path and not os.path.exists(p):     # real positions not built yet: same sample
                p = os.path.join(data, "brain_layout_schematic.json")
            with open(p) as f:
                rows = json.load(f)["neurons"]
            for r in rows:
                self.map[r["_n"]] = r["i"]
            self.bits = np.zeros(len(rows), dtype=bool)
        except (OSError, ValueError, KeyError, IndexError):
            pass

    def add(self, spikes):
        if self.bits is None or not spikes:
            return
        idx = self.map[np.concatenate([np.asarray(s, dtype=np.int64).ravel() for s in spikes])]
        with self._lock:
            self.bits[idx[idx >= 0]] = True

    def take(self):
        if self.bits is None:
            return None
        with self._lock:
            packed = np.packbits(self.bits, bitorder="little")
            self.bits[:] = False
        return self._b64(packed.tobytes()).decode()


def live_snapshot(g, groups, thought="", still_s=None, now=None, spk=None, stim=None):
    """GET /live (obsfly's panel, 2026-10-08, ~2 Hz): what the fly is doing NOW,
    from memory only. Brain rates are read fresh (not /health's 1 s cache), but
    one answer is shared for 0.25 s so a busy panel costs nothing."""
    now = time.monotonic() if now is None else now
    if _live_cache["out"] is not None and now - _live_cache["at"] < 0.25:
        return _live_cache["out"]
    from .twitch import PLACES
    h = g.hands
    r = g.brain.rate
    regions = {k: round(float(r[groups[src]].mean()), 2) + 0.0     # + 0.0: no "-0.0"
               for k, src in LIVE_REGIONS if src in groups and len(groups[src])}
    driver = bool(getattr(g, "admin", False)) or bool(getattr(g, "taken_over", False))
    chat = int(getattr(g, "chat_keys", 0) or 0)
    mask = (getattr(g, "_override_keys", 0) if driver else chat or int(getattr(g, "fly_keys", 0) or 0))
    key = next((n for n, m in (("a", gm.A), ("b", gm.B), ("up", gm.UP), ("down", gm.DOWN),
                               ("left", gm.LEFT), ("right", gm.RIGHT)) if mask & m), "none")
    md = g.mood
    lv = dict(g.nose.level) if getattr(g, "nose", None) is not None else {}
    tf = _team_fields(g.team_calm, g.progress_calm)
    # what actually reached the game this tick, and whose it was (2026-10-13)
    sent = int(getattr(g, "sent_keys", 0) or 0)
    fly_k = int(getattr(g, "fly_keys", 0) or 0) & sent
    chat_k = chat & sent
    pressed = next((n for n, m in (("a", gm.A), ("b", gm.B), ("up", gm.UP), ("down", gm.DOWN),
                                   ("left", gm.LEFT), ("right", gm.RIGHT)) if sent & m), "none")
    pressed_by = (None if not sent else "driver" if driver
                  else "both" if fly_k and chat_k else "chat" if chat_k else "fly")
    st = h.stats
    menu_s = None if g.menu_since is None else round((g.brain.t_ms - g.menu_since) / 1000.0, 1)
    out = {"t": round(time.time(), 2), "regions": regions,
           "eyes_in": {k: round(float(v), 1) + 0.0 for k, v in sorted(g.eyes.rates.items())},
           "keys": key, "by": "driver" if driver else "chat" if chat else "fly",
           # menu_since is the worker's published flag; asking the emulator from
           # this (HTTP) thread would race the worker stepping it
           "heading": HEADING_DEG.get(gm.HEADINGS[h.heading][3]) if g.menu_since is None else None,
           "walking": bool(h.bout),
           "mood": ({"valence": round(float(md.valence), 3), "label": md.label,
                     "smell_people": round(float(lv.get("people", 0.0)), 2),
                     "smell_exits": round(float(lv.get("exits", 0.0)), 2)} if md else None),
           "thinking": thought or None, "battle": bool(g.battle), "menu_s": menu_s,
           "still_s": None if still_s is None else round(still_s, 1),
           "turns": {k: int(st.get(k, 0)) for k in ("left", "right", "a", "b")},
           "place": PLACES.get(g.place, g.place),
           "lead": f"{tf['tn']} Lv {tf['tl']}" if tf["tn"] != "-" else None,
           # the GPU brain layer: sampled neurons that spiked since the last
           # call, and the last pain/pleasure delivered (for its wave)
           "spk": spk() if spk else None, "stim": dict(stim) if stim else None,
           "pressed": pressed, "pressed_by": pressed_by}
    _live_cache.update(at=now, out=out)
    return out


class LearningWatch:
    """Per-stim and running accounts of who changed the fly's learned
    synapses (learning.py's ledger). For each delivered burst: the opinions
    and depression before, and LAG_MS of brain time later, written to
    data/inputs/learning.jsonl with its source. 'overlap' marks a burst whose
    window held another one, so its delta is shared. Also a daily snapshot of
    the learned state (data/learning/YYYY-MM-DD.npz) so innate and trained
    behaviour can be compared offline in the experiment harness."""

    LAG_MS = 3000.0
    SNAP_DIR = os.path.expanduser("~/flybrain/data/learning")

    def __init__(self, journal):
        self.journal = journal
        self.pending = []
        self._fp = (None, -1e9)
        self._snap_day = None

    @staticmethod
    def _state(g):
        L, m = g.learning, g.mood
        op = dict(m.opinion) if m is not None and m.opinion else {}
        return {"depressed": round(L.depressed_now(), 3) if L is not None else None,
                "people": round(op.get("people", 0.0), 4), "exits": round(op.get("exits", 0.0), 4),
                "booked": dict(L.ledger["depressed"]) if L is not None else {}}

    def start(self, g, kind, source, who):
        for p in self.pending:
            p["overlap"] = True
        self.pending.append({"t_ms": g.brain.t_ms, "kind": kind, "source": source, "who": str(who),
                             "before": self._state(g), "overlap": False})

    def tick(self, g, playing=True):
        now = g.brain.t_ms
        while self.pending and now - self.pending[0]["t_ms"] >= self.LAG_MS:
            p = self.pending.pop(0)
            a, b = p["before"], self._state(g)
            src = p["source"]
            self.journal.write({
                "kind": "learning", "stim": p["kind"], "source": src, "who": p["who"],
                "overlap": p["overlap"] or bool(self.pending),
                "d_depressed": None if a["depressed"] is None else round(b["depressed"] - a["depressed"], 4),
                "booked_to_source": round(b["booked"].get(src, 0.0) - a["booked"].get(src, 0.0), 4),
                "d_people": round(b["people"] - a["people"], 4),
                "d_exits": round(b["exits"] - a["exits"], 4),
                "people_after": b["people"], "exits_after": b["exits"]},
                path=os.path.join(self.journal.folder, "learning.jsonl"))
        day = time.strftime("%Y-%m-%d")
        # Only while playing: before that the saved learning is not loaded yet
        # and the snapshot would be of a naive fly.
        if (playing and g.learning is not None and day != self._snap_day
                and time.localtime().tm_hour >= 3):
            self._snap_day = day
            path = os.path.join(self.SNAP_DIR, day + ".npz")
            if not os.path.exists(path):
                try:
                    os.makedirs(self.SNAP_DIR, exist_ok=True)
                    np.savez_compressed(path, m=g.learning.m.astype(np.float16),
                                        ledger=g.learning._ledger_json())
                except OSError:
                    pass

    def line(self, g):
        L = g.learning
        if L is None:
            return "learning: off"
        now = time.monotonic()
        if now - self._fp[1] > 60:
            self._fp = (L.fingerprint(), now)
        led = L.ledger
        out = led["outstanding"]
        tot = sum(out.values()) or 0.0
        fmt = lambda d: " ".join(f"{k}={v:.3f}" for k, v in sorted(d.items()))
        share = (out.get("human", 0.0) / tot) if tot else 0.0
        return (f"learning: depressed_now={L.depressed_now():.3f} human_share={share:.3f} "
                f"fingerprint={self._fp[0]} recovered={led['recovered']:.3f} "
                f"outstanding[{fmt(out)}] depressed_total[{fmt(led['depressed'])}]")


class ThoughtTally:
    """How often the fly thinks each kind of thought (Ian, 2026-10-07).
    Kinds ignore the side ("wall on the left/right" are one kind). Counts are
    per process, plus a per-minute rate over the process and over the last
    10 minutes, for /health."""

    WINDOW_S = 600.0

    def __init__(self):
        self.counts = {}
        self.t0 = None
        self.recent = deque()

    @staticmethod
    def kind(text):
        k = text.replace(" left", " X").replace(" right", " X").replace(" - ", " ")
        return k.replace(" ", "_")

    def add(self, text, now):
        if self.t0 is None:
            self.t0 = now
        k = self.kind(text)
        self.counts[k] = self.counts.get(k, 0) + 1
        self.recent.append(now)
        while self.recent and now - self.recent[0] > self.WINDOW_S:
            self.recent.popleft()

    def line(self, now):
        n = sum(self.counts.values())
        mins = max((now - self.t0) / 60.0, 1e-9) if self.t0 is not None else 0
        rate = f"{n / mins:.1f}" if mins else "-"
        rec = len([t for t in self.recent if now - t <= self.WINDOW_S])
        span = min(self.WINDOW_S, now - self.t0) / 60.0 if self.t0 is not None else 0
        rate10 = f"{rec / span:.1f}" if span > 0.5 else "-"
        kinds = " ".join(f"{k}={v}" for k, v in sorted(self.counts.items(), key=lambda kv: -kv[1]))
        return f"thoughts: total={n} per_min={rate} per_min_10m={rate10} {kinds}".rstrip()


THOUGHT_AUDIT_FLAG = os.path.expanduser("~/.config/flybrain/thought_audit")


def _thought_truth(g, said, turned):
    """The thought next to what the map actually held at that moment (the
    same tile grid the fly's eyes are fed from), for checking the thoughts
    are true. left/right are the player's own, as in obstacle_view."""
    h, e = g.hands, g.eyes
    rnd = lambda t: [round(float(t[0]), 3), round(float(t[1]), 3)]
    # The view at the moment of the decision when there is one (game.py
    # decision_view), else the current view. heading_before -> heading_after
    # is the turn actually applied to the game: +1 clockwise = right.
    dv = getattr(g, "decision_view", None) or {}
    applied = None
    if dv:
        d = (dv["heading_after"] - dv["heading_before"]) % 4
        applied = "right" if d == 1 else "left" if d == 3 else None
    return {"thought": said, "turned": turned, "applied": applied, "battle": bool(g.battle),
            "overworld": g.menu_since is None,
            "facing_solid": dv.get("facing_solid", g.facing_solid()),
            "obstacle_lr": rnd(dv.get("obstacle_lr", e.seen)),
            "people_lr": rnd(dv.get("people_lr", e.seen_people)),
            "exits_lr": rnd(dv.get("exits_lr", e.seen_exits)),
            "view_age_ms": round(g.brain.t_ms - dv["t_ms"], 1) if dv else None, "readout": {"obstacle": round(h.obstacle, 1),
                                                       "people": round(h.people, 1),
                                                       "exits": round(h.exits, 1)},
            "human": not g.attrib.clean(), "heading": h.heading}


def _draw_thought(screen, small, text):
    """Top of the game view: what the fly is making of what it sees."""
    import pygame
    t = small.render(text, True, (225, 232, 245))
    bg = pygame.Surface((t.get_width() + 12, t.get_height() + 4), pygame.SRCALPHA)
    bg.fill((0, 0, 0, 175))
    screen.blit(bg, (6, 6))
    screen.blit(t, (12, 8))


ARROWS = {"up": "UP", "down": "DOWN", "left": "LEFT", "right": "RIGHT", "a": "A", "b": "B"}


def _answer(topic, g, diary, paid):
    """What the fly says when chat asks. Everything here is read from what it
    is actually doing -- no canned lines except the help."""
    if topic == "help":
        return ("!left !right !up !down !a !b drive it, free, any time -- your press is "
                "ADDED to what it already wants. !pain / !pleasure teach it. "
                "cheer50 steer = 5s of exclusive control.")
    if topic == "starter":
        from . import starters
        return f"the fly's favourite starter (just for fun): {starters.scoreboard()}"
    if topic == "team":
        team = [m for m in (g.team_calm or g.team_now or []) if not m["egg"]]
        who = ", ".join(f"{m['name']} Lv{m['level']}" for m in team[:6]) or "nothing yet"
        p = g.progress_calm or g.progress_now or {}
        b = p.get("badges", 0)
        return (f"team: {who}. {b} badge{'s' if b != 1 else ''}, "
                f"{p.get('dex_owned', 0)} caught, {p.get('dex_seen', 0)} seen.")
    if topic == "story":
        return diary.summary() or "nothing has happened to it yet today."
    # "fly": what it is doing and feeling right now
    mood = g.mood.label if g.mood is not None else "?"
    thought = _thought_now[0] or "wandering"
    opin = []
    if g.mood is not None and g.mood.opinion:
        for kind, noun in (("people", "people"), ("exits", "doors")):
            o = g.mood.opinion.get(kind, 0.0)
            if o <= -0.35:
                opin.append(f"afraid of {noun}")
            elif o >= 0.35:
                opin.append(f"drawn to {noun}")
    taught = f" chat has taught it to be {' and '.join(opin)}." if opin else ""
    return (f"right now: {thought}. it feels {mood}. it has walked {g.steps_moved} tiles "
            f"and bumped into {g.wall_bumps} walls this session.{taught}")


_thought_now = [""]          # the latest caption, shared with the answer above


def _draw_brain_labels(screen, font, view):
    """Name the parts of the brain, lit by how busy each one is.

    Each part is judged against its own recent range (BrainView.label_activity),
    because the raw rates are not comparable -- the retina never stops and the
    mushroom body is deliberately sparse.
    """
    import pygame
    for text, x, y, col, level in view.label_activity():
        shade = 0.58 + 0.42 * level     # readable when quiet, obvious when busy
        c = tuple(min(255, int(v * shade)) for v in col)
        t = font.render(text, True, c)
        k = MAP_S / MAP                       # the map is drawn scaled into its box
        px = int(MAP_X + x * k) - t.get_width() // 2
        py = int(MAP_TOP + y * k) - t.get_height() // 2
        px = max(GAME_W + 2, min(px, WIN_W - t.get_width() - 2))
        py = max(MAP_TOP, min(py, MAP_TOP + MAP_S - t.get_height()))
        # A dark plate under the name. Without it the letters sit on top of
        # the activity itself and the bright parts -- the ones worth reading --
        # are exactly the ones that swallow their own label: "EYES" over a lit
        # retina came out as "EYE".
        plate = pygame.Surface((t.get_width() + 8, t.get_height() + 4), pygame.SRCALPHA)
        plate.fill((0, 0, 0, 170 + int(50 * level)))
        screen.blit(plate, (px - 4, py - 2))
        if level > 0.5:                      # a halo, so a busy part reads at a glance
            glow = pygame.Surface((t.get_width() + 12, t.get_height() + 8), pygame.SRCALPHA)
            glow.fill((*col, int(55 * (level - 0.5) * 2)))
            screen.blit(glow, (px - 6, py - 4))
        screen.blit(t, (px, py))


PULL_SAYS = {
    "together": ("PULLING TOGETHER", (140, 235, 160)),
    "against":  ("FIGHTING THE FLY", (255, 130, 110)),
    "apart":    ("PULLING CROSSWISE", (255, 215, 120)),
    "chat":     ("CHAT IS DRIVING",  (150, 200, 255)),
}


def _draw_pull(screen, small, big, g):
    """Chat's key against the fly's own, while both are pressing.

    This is the thing the stream is actually about -- a viewer and a live
    animal with their hands on the same controls -- and nothing said whether
    they were helping each other or fighting.
    """
    import pygame
    if not g.pull:
        return
    text, col = PULL_SAYS[g.pull]
    fly = ARROWS.get(_key_name(g.fly_keys), "-")
    chat = ARROWS.get(_key_name(g.chat_keys), "-")
    lines = [(f"fly {fly}   chat {chat}", (235, 238, 248), small),
             (text, col, big)]
    c = g.pull_counts
    tally = "   ".join(f"{lbl} {c[k]}" for k, lbl in
                       (("together", "together"), ("against", "fighting"),
                        ("apart", "crosswise")) if c.get(k))
    if tally:                       # how the two have been getting on, all session
        lines.append((tally, (200, 205, 215), small))
    w = max(f.size(t)[0] for t, _, f in lines) + 16
    h = sum(f.size(t)[1] for t, _, f in lines) + 12
    x, y = GAME_W // 2 - w // 2, GAME_H - 190   # clear of the press flash below
    bg = pygame.Surface((w, h), pygame.SRCALPHA)
    bg.fill((0, 0, 0, 185))
    screen.blit(bg, (x, y))
    yy = y + 4
    for t, c, f in lines:
        surf = f.render(t, True, c)
        screen.blit(surf, (GAME_W // 2 - surf.get_width() // 2, yy))
        yy += surf.get_height() + 4


def _key_name(mask):
    from .game import UP, DOWN, LEFT, RIGHT, A, B
    for nm, bit in (("up", UP), ("down", DOWN), ("left", LEFT), ("right", RIGHT),
                    ("a", A), ("b", B)):
        if mask & bit:
            return nm
    return ""


CLIP_OF = ("badge", "caught", "evolve")   # news worth keeping after the stream


def clip_later(chat, diary, text, announce, gap=None, now=None, sleep=None):
    """Cut a clip of the moment that just passed, off the drawing thread.

    Two reasons this cannot happen inline: the Twitch call takes up to 15 s,
    and the stream is this thread's only job -- a blocked draw is a frozen
    picture for everybody watching. And the link does not play for a few
    seconds after it is made, so saying it immediately would hand chat a dead
    link.

    Returns the thread it started, or None if it declined (no chat, not the
    kind of news worth a clip, or too soon after the last one).
    """
    import threading
    from . import twitch as twitch_mod
    clock = now or time.time
    if chat is None or not getattr(chat, "cfg", None):
        return None
    t = clock()
    if t - clip_later.last < (twitch_mod.CLIP_GAP_S if gap is None else gap):
        return None
    clip_later.last = t

    def work():
        url, why = twitch_mod.make_clip(chat.cfg)
        if not url:
            if why != "not live":      # not broadcasting is not a fault
                print(f"clip failed: {why}", flush=True)
            clip_later.last = 0.0      # it did not happen; do not serve the wait
            return
        diary.add("clip", f"{text} -- {url}")
        (sleep or time.sleep)(twitch_mod.CLIP_READY_S)
        if announce:
            chat.say(f"{text} -- clip: {url}")

    th = threading.Thread(target=work, daemon=True)
    th.start()
    return th


clip_later.last = 0.0


def _input_record(g, user, kind, key=None, outcome=None, **extra):
    """One line for the input journal: who, what, what became of it, and the
    game at that moment -- including what the FLY was pressing, so a viewer's
    direction can be scored as with or against it."""
    from .attrib import relation
    rec = {"user": str(user), "kind": kind, "key": key, "outcome": outcome,
           "place": getattr(g, "place", None), "battle": bool(getattr(g, "battle", False)),
           "fly_keys": int(getattr(g, "fly_keys", 0) or 0),
           "since_human_s": g.attrib.since_human_s()}
    rel = relation(key, rec["fly_keys"]) if isinstance(key, str) else None
    if rel:
        rec["vs_fly"] = rel
    rec.update(extra)
    return rec


def _event_record(g, ev, outcome):
    """A chat event (twitch.interpret's tuples) as a journal line."""
    kind = ev[0]
    if kind == "control":
        return _input_record(g, ev[2], "control", ev[1], outcome)
    if kind == "free":
        return _input_record(g, ev[2], "free_stim", ev[1], outcome, name=ev[3])
    if kind == "stim":
        via = ev[4] if len(ev) > 4 else "bits"
        return _input_record(g, ev[3], "paid_stim", ev[1], outcome, n=ev[2], via=via)
    if kind == "steer":
        return _input_record(g, ev[2], "steer", None, outcome, seconds=round(ev[1], 1), name=ev[3])
    if kind == "ask":
        return _input_record(g, ev[2], "ask", ev[1], outcome, name=ev[3])
    if kind == "admin":
        return _input_record(g, ev[2], "admin", "on" if ev[1] else "off", outcome)
    if kind == "cheer":
        return _input_record(g, ev[2], "cheer", None, outcome, bits=ev[1])
    return _input_record(g, "?", str(kind), None, outcome, raw=[str(x) for x in ev])


def record_news(kind, text, diary, events, chat, announce, shorts=None, credit=None,
                journal=None):
    """Everything that must happen when the fly achieves something.

    Pulled out of the milestone loop so it can be tested. It could not be
    before: the loop lives inside run(), needs a window, an emulator and a
    connectome, and milestones are hours apart on a real game -- so the one
    line joining the milestone to the Discord feed went unverified all day
    while everything around it had tests.

    The order matters in one place only: the diary and the event log must both
    get every piece of news, because they are what the stream's history and
    the Discord channel are built from, and a difference between them shows up
    as the two disagreeing in public.
    """
    diary.add(kind, text)               # the fly's own history
    # A vertical Short of it, for discovery -- asked for FIRST, because the
    # event below carries the Short's path. The Discord poster sees that path,
    # holds the news, and sends the text and the video as ONE message (obsfly's
    # rule: two posts for one catch reads as a bug). No path -- a level, or a
    # moment rate-limited out of a Short -- means the text goes straight out.
    short = None
    if shorts is not None:
        short = shorts.cut_later(kind, text, on_done=lambda path, cap:
                                 print(f"short ready: {path} ({cap})", flush=True))
    events.add(kind, text, short=short, credit=credit)  # ...numbered, for Discord
    if journal is not None:             # ...and who made it happen (attrib.py)
        journal.progress({"kind": kind, "text": text, **(credit or {})})
    if announce:                        # the fly tells chat what just happened
        chat.say(text)
    if kind in CLIP_OF:                 # ...and keeps the moment, if it is live
        clip_later(chat, diary, text, announce)


class StillPain:
    """Ian, 2026-10-07: standing still for 60 s earns a pain burst, and
    another every 10 s after that until it moves.

    Seconds are the fly's (brain) seconds -- the same clock as the on-screen
    "GOING NOWHERE (Ns)" counter, so the pain lands when that reads 60. Only
    in the overworld (stillness is measured nowhere else), and never while a
    human holds the controls (!menu / the panel, or paid steering): standing
    still then is not the fly's doing. The 60 s restart from when the human
    lets go.

    Ian, 2026-10-08: the clock PAUSES while a dialogue box is open (the fly
    cannot walk while the game is talking), and runs on from where it was
    when the box closes. On 10-07 one stretch took 377 pains in 2 h 20 min."""

    FIRST_S = 60.0
    REPEAT_S = 10.0

    def __init__(self, first_s=FIRST_S, repeat_s=REPEAT_S):
        self.first_s, self.repeat_s = first_s, repeat_s
        self.next_at = first_s
        self._prev = None
        self.fired = 0
        self.paused_s = 0.0                      # total time held off by dialogue
        self.dialogue = False

    def line(self, shadow=None):
        out = (f"still_rule: fired={self.fired} paused_dialogue_s={self.paused_s:.0f} "
               f"dialogue_now={self.dialogue}")
        if shadow is not None:           # the same rule without the pause, never delivered
            out += f" without_pause={shadow.fired}"
        return out

    def check(self, still_s, blocked=False, paused=False):
        """True when a pain burst is due now."""
        prev = self._prev
        if still_s is None or (prev is not None and still_s < prev):
            self.next_at = self.first_s          # moved: a new stretch starts
            prev = None
        self._prev = still_s
        if still_s is None:
            return False
        self.dialogue = paused
        if paused:
            if prev is not None:                 # push the deadline on by the time passed
                self.next_at += still_s - prev
                self.paused_s += still_s - prev
            return False
        if blocked:
            self.next_at = max(self.next_at, still_s + self.first_s)
            return False
        if still_s >= self.next_at:
            self.next_at = still_s + self.repeat_s
            self.fired += 1
            return True
        return False


STUCK_S = 25.0          # nothing happening for this long is worth saying
STUCK_SAY_S = 180.0     # ...but say it in chat at most this often


class OverworldStill:
    """Seconds the fly has stood still IN THE OVERWORLD, or None.

    Ian, 2026-10-08: the stillness rule must not run in battles. game.py only
    advances still_since in the overworld but never clears it on leaving, so
    a battle that began right after the fly stopped counted as standing
    still: 283 of run 2's 479 rule pains, and 13 of run 3's first 25, landed
    in battles. Here the clock reads None out of the overworld and restarts
    from zero on coming back, so only an unbroken stretch of standing still
    in the world counts -- for the rule, the GOING NOWHERE call and /health.
    (Kept out of game.py so the experiment harness's code is untouched.)"""

    def __init__(self):
        self.back_at = None
        self._out = False

    def update(self, g, now_ms):
        out = g.menu_since is not None
        if self._out and not out:
            self.back_at = now_ms                 # back in the world: a fresh clock
        self._out = out
        if out or g.still_since is None:
            return None
        start = g.still_since if self.back_at is None else max(g.still_since, self.back_at)
        return max(0.0, (now_ms - start) / 1000.0)


def stuck_call(g, now_ms, still=None):
    """What to ask chat for, when the fly is getting nowhere. None if it is
    playing normally.

    Measured before building this: the fly moves every second or so most of
    the time (median 1.0 s between tiles), but 11% of the stream is a stretch
    with no movement at all, some of them over a minute and a half. Those
    stretches are dead air, and they are also exactly when a viewer pressing a
    button is worth more than the fly's own next decision.
    """
    menu = None if g.menu_since is None else (now_ms - g.menu_since) / 1000.0
    if still is None and g.menu_since is None and g.still_since is not None:
        still = (now_ms - g.still_since) / 1000.0      # callers without an OverworldStill
    if menu is not None and menu >= STUCK_S:
        if g.in_battle():
            # A battle is not a menu, and "back out" is wrong advice in one:
            # B does not leave a battle, and RUN does not work against a
            # trainer at all. The fly drifts onto RUN -- bottom right of the
            # four -- and hammers it against trainers that cannot be run from,
            # which is where the longest stretches of dead air come from. So
            # the ask points at FIGHT, which is the opposite corner.
            return (f"THE FLY IS STUCK IN A BATTLE ({menu:.0f}s)",
                    "press !up !left to reach FIGHT, then !a")
        return (f"THE FLY IS LOST IN A MENU ({menu:.0f}s)", "press !b to back out, !a to go on")
    if still is not None and still >= STUCK_S:
        return (f"THE FLY IS GOING NOWHERE ({still:.0f}s)",
                "press !up !down !left !right to move it")
    return None


def _draw_stuck(screen, small, big, call, low=False):
    """Say it where the game is, since that is where the viewer is looking.

    `low` puts it in the one clear band of a battle screen. The enemy's box
    is top-left (down to about y=80) and the fly's own is on the right from
    about y=150, with the text box below that -- so it goes between them.
    Measured off a real battle frame after the first attempt at y=132 landed
    on top of the fly's own health and paralysis status.
    """
    import pygame
    head, how = call
    lines = [(head, (255, 150, 120), big), (how, (235, 238, 248), small)]
    w = max(f.size(t)[0] for t, _, f in lines) + 18
    h = sum(f.size(t)[1] for t, _, f in lines) + 14
    x, y = GAME_W // 2 - w // 2, (88 if low else 40)
    bg = pygame.Surface((w, h), pygame.SRCALPHA)
    bg.fill((0, 0, 0, 200))
    screen.blit(bg, (x, y))
    yy = y + 5
    for t, c, f in lines:
        surf = f.render(t, True, c)
        screen.blit(surf, (GAME_W // 2 - surf.get_width() // 2, yy))
        yy += surf.get_height() + 4


def _draw_chat_feed(screen, small, big, paid):
    """Down the right-hand side: what chat has just done, by name.

    Without this the stream gives no sign that a press arrived, so viewers
    cannot tell a working bot from a broken one -- and neither could the
    broadcaster, which is how this came to be built.
    """
    # 2026-10-09: one line in its own band between the brain title and the
    # map (y 21-33), newest first, as many as fit -- it used to sit on the map.
    import pygame
    recent = paid.recent()
    x, y, right = GAME_W + 8, 21, WIN_W - 6
    head = small.render("CHAT", True, (255, 215, 120))
    screen.blit(head, (x, y))
    x += head.get_width() + 8
    items = recent[:4] or ["say !left !right !up !down"]
    col = (235, 238, 248) if recent else (170, 176, 190)
    for i, text in enumerate(items):
        t = small.render(("· " if i else "") + text, True, col)
        if x + t.get_width() > right:
            break
        screen.blit(t, (x, y))
        x += t.get_width() + 6


TEXTBOX_WHITE = 0.18    # share of margin pixels that are box-white (see below)


def textbox_open(shot):
    """Is the game showing a dialogue box along the bottom?

    The standing hints (who may drive, what the cheers cost) live in the
    bottom third of the game view, which is exactly where the game puts its
    own text -- so on 24 frames sampled off the live stream the sign in the
    Wireless Club read "This is the POKeMON WIRELESS CLUB ONL". Measured on
    the two strips either side of those hints, which nothing overlays: pale
    0.34-0.41 with a box open, 0.09-0.12 without.

    2026-10-08: "pale" (all channels > 195) also fired on Pewter City's grey
    cobblestones and house walls -- 0.22-0.47, as high as a box -- so the
    stillness rule's dialogue pause was pausing while the fly walked round
    town. The box is WHITE (all channels >= 235); cobbles and walls are not.
    White share: boxes 0.28-0.36 (9 frames), maps 0.00-0.01 (15), Pewter
    false alarms 0.00-0.08 (10). Threshold between them: 0.18.
    """
    h, w = shot.shape[0], shot.shape[1]
    strip = shot[int(h * 0.675):int(h * 0.94)]
    edge = int(w * 0.017), int(w * 0.117), int(w * 0.883), int(w * 0.983)
    margins = np.concatenate([strip[:, edge[0]:edge[1]], strip[:, edge[2]:edge[3]]], axis=1)
    return float((margins.min(axis=2) >= 235).mean()) > TEXTBOX_WHITE


SEEING = (("WALLS", "obstacle", "obstacle_thresh"),
          ("PEOPLE", "people", "people_thresh"),
          ("DOORS", "exits", "exit_thresh"))
SEE_SPAN = 1.7          # how far past the threshold the bar's end sits


def seeing_rows(hands):
    """What the fly's three lateral readouts say, right now, as fractions.

    Returns (label, where, at, lit) per channel: `where` is the signal on a
    -1..+1 scale with + meaning left, `at` is where the threshold sits on
    that same scale, and `lit` is whether the signal has crossed it -- the
    moment the fly will actually turn on it.

    The stream asserts that the fly sees; this is the number it sees WITH.
    Each channel gets its own scale because the thresholds are re-set for the
    room the fly is in (371 Hz where people are rare, 108 Hz where doors are
    common), so a shared axis would flatten one channel to nothing.
    """
    rows = []
    for label, sig_name, thr_name in SEEING:
        sig = float(getattr(hands, sig_name, 0.0))
        thr = float(getattr(hands, thr_name, 0.0))
        if not thr or thr > 1e8:          # not calibrated yet: nothing to show
            rows.append((label, 0.0, None, False))
            continue
        span = thr * SEE_SPAN
        where = max(-1.0, min(1.0, sig / span))
        rows.append((label, where, 1.0 / SEE_SPAN, abs(sig) >= thr))
    return rows


def _draw_seeing(screen, small, hands):
    """Top-left of the game: the fly's own view of what is beside it."""
    import pygame
    rows = seeing_rows(hands)
    x0, y0, bw, rh = 6, 30, 78, 15
    lw = max(small.size(lab)[0] for lab, _, _, _ in rows) + 6
    w, h = lw + bw + 12, 14 + rh * len(rows)
    plate = pygame.Surface((w, h), pygame.SRCALPHA)
    plate.fill((0, 0, 0, 200))     # a bright room swallows anything lighter
    screen.blit(plate, (x0, y0))
    screen.blit(small.render("WHAT IT SEES", True, (205, 212, 225)), (x0 + 6, y0 + 2))
    t = small.render("L|R", True, (150, 160, 175))
    screen.blit(t, (x0 + w - 6 - t.get_width(), y0 + 2))
    for i, (label, where, at, lit) in enumerate(rows):
        y = y0 + 15 + i * rh
        col = (255, 215, 120) if lit else (185, 192, 205)
        screen.blit(small.render(label, True, col), (x0 + 6, y))
        bx, cx = x0 + 6 + lw, x0 + 6 + lw + bw // 2
        pygame.draw.rect(screen, (62, 68, 80), (bx, y + 5, bw, 5))
        if at is not None:
            for s in (-1, 1):              # where it starts to matter
                mx = int(cx + s * at * bw / 2)
                pygame.draw.line(screen, (105, 115, 130), (mx, y + 2), (mx, y + 12))
        # + is the left, so the bar grows leftward out of the middle
        span = int(abs(where) * bw / 2)
        if span:
            fx = cx - span if where > 0 else cx
            pygame.draw.rect(screen, col if lit else (130, 175, 235), (fx, y + 5, span, 5))
        pygame.draw.line(screen, (225, 228, 235), (cx, y + 3), (cx, y + 11))


def _draw_press(screen, big, flash):
    """A press just landed: say so in letters big enough to see on a phone."""
    import pygame
    who, key, frames = flash
    t = big.render(f"{who}  {ARROWS.get(key, key.upper())}", True, (20, 20, 24))
    pad = 14
    bg = pygame.Surface((t.get_width() + pad * 2, t.get_height() + 10), pygame.SRCALPHA)
    bg.fill((255, 215, 120, 235))
    x = GAME_W // 2 - bg.get_width() // 2
    screen.blit(bg, (x, GAME_H - 108))
    screen.blit(t, (x + pad, GAME_H - 103))


def _draw_admin(screen, big, small):
    """While the streamer holds the controls (!menu): unmistakable, because
    chat's presses doing nothing must never look like a broken stream."""
    import pygame
    from .twitch import ADMIN_BANNER
    head, _, tail = ADMIN_BANNER.partition(" - ")
    rows = [(head, (255, 120, 90), big), (tail + "  -  chat is paused, !pain/!pleasure still work",
                                          (255, 225, 190), small)]
    y = 6
    for text, col, f in rows:
        t = f.render(text, True, col)
        bg = pygame.Surface((t.get_width() + 14, t.get_height() + 6), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 200))
        screen.blit(bg, (GAME_W // 2 - bg.get_width() // 2, y))
        screen.blit(t, (GAME_W // 2 - t.get_width() // 2, y + 3))
        y += bg.get_height() + 2


def _draw_chat_control(screen, small, big, state, talking=False):
    """Chat has the controls whenever nobody has bought them, which is nearly
    always -- so this says so quietly and once, rather than announcing a
    window that no longer exists."""
    import pygame
    may_drive, last, presses, tally = state
    if not may_drive or talking:
        return
    import time as _time
    from .twitch import EXPLAIN_S, EXPLAINERS, PRICES
    counts = "   ".join(f"{ARROWS.get(k, k.upper())} {n}" for k, n in
                        sorted(tally.items(), key=lambda kv: -kv[1])[:4])
    head = (f"{last[1]}: {last[0].upper()}" if last
            else "CHAT DRIVES THE FLY -- !left !right !up !down !a !b")
    # 2026-10-09 (Ian: "not so overlapped"): ONE full-width bar along the
    # bottom of the game with one solid backing -- the headline, and under it
    # a single line that rotates through the explainers, the prices and the
    # last minute's presses. A line too wide for the game view is skipped,
    # never drawn half off the edge.
    slot = int(_time.monotonic() / EXPLAIN_S)
    pool = list(EXPLAINERS) + list(PRICES) + ([f"last minute:   {counts}"] if counts else [])
    second = None
    for k in range(len(pool)):
        cand = pool[(slot + k) % len(pool)]
        if small.size(cand)[0] <= GAME_W - 16:
            second = cand
            break
    bar_h = 36
    bg = pygame.Surface((GAME_W, bar_h), pygame.SRCALPHA)
    bg.fill((0, 0, 0, 200))
    screen.blit(bg, (0, GAME_H - bar_h))
    t = big.render(head, True, (255, 215, 120))
    if t.get_width() > GAME_W - 12:
        t = small.render(head, True, (255, 215, 120))
    screen.blit(t, (GAME_W // 2 - t.get_width() // 2, GAME_H - bar_h + 2))
    if second:
        s2 = small.render(second, True, (200, 205, 215))
        screen.blit(s2, (GAME_W // 2 - s2.get_width() // 2, GAME_H - 14))


def _draw_chat(screen, small, big, ticker, status, connected, messages=0, talking=False):
    """Top-right of the game view: the prices, who in chat just paid for
    what, and who is steering right now."""
    import pygame
    import time as _time
    from .twitch import EXPLAIN_S, EXPLAINERS, PRICES, STEER_HELP
    driver, left, waiting, queued = status
    x = GAME_W - 8
    # One of the two lines rotates through the explainers, so the invitation
    # is always on screen for whoever just arrived.
    slot = int(_time.monotonic() / EXPLAIN_S)
    line = EXPLAINERS[slot % len(EXPLAINERS)]
    if small.size(line)[0] > GAME_W - 12:      # never clip: a line too wide is
        line = PRICES[0]                       # worse than no line at all
    # (the explainer and price lines now rotate in the bottom bar,
    # _draw_chat_control -- 2026-10-09)
    dot = (90, 220, 120) if connected else (200, 80, 70)
    label = small.render("twitch" if connected else "twitch (offline)", True, (220, 220, 230))
    lbg = pygame.Surface((label.get_width() + 20, label.get_height() + 4), pygame.SRCALPHA)
    lbg.fill((0, 0, 0, 160))
    screen.blit(lbg, (x - label.get_width() - 15, 4))
    screen.blit(label, (x - label.get_width(), 6))
    pygame.draw.circle(screen, dot, (x - label.get_width() - 8, 13), 4)
    for i, (text, _) in enumerate(reversed(ticker)):
        t = small.render(text, True, (255, 255, 255))
        bg = pygame.Surface((t.get_width() + 8, t.get_height() + 2), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 160))
        y = 24 + i * 16
        screen.blit(bg, (x - bg.get_width(), y))
        screen.blit(t, (x - t.get_width() - 4, y + 1))
    lines = []
    if driver:
        lines.append((f"{driver} is steering ({left:.0f}s)", (255, 200, 90)))
        lines.append((f"{driver}: {STEER_HELP}", (255, 235, 170)))
    if waiting:
        lines.append((f"{waiting} waiting to steer", (220, 220, 230)))
    if queued:
        lines.append((f"{queued} press{'es' if queued != 1 else ''} queued", (220, 220, 230)))
    for i, (text, col) in enumerate(lines):
        t = (big if i == 0 and driver else small).render(text, True, col)
        bg = pygame.Surface((t.get_width() + 12, t.get_height() + 4), pygame.SRCALPHA)
        bg.fill((0, 0, 0, 170))
        y = GAME_H - 56 - (len(lines) - 1 - i) * 20
        screen.blit(bg, (GAME_W // 2 - bg.get_width() // 2, y))
        screen.blit(t, (GAME_W // 2 - t.get_width() // 2, y + 2))


def run(cx, rom, sav=None, state=None, fps=15, calibrate=True, max_frames=None,
        screenshot=None, play_paths=None, resume=None, twitch=False, chat=None,
        serve=None):
    """rom/sav/state is what calibration runs on; play_paths, if given, is the
    game actually played afterwards. resume, if given, is a snapshot path:
    loaded at start if it exists, autosaved during play, and saved again on
    every clean exit -- window close, q/esc, or SIGTERM/SIGHUP."""
    import pygame

    # The play window runs the antennal lobe with its local neurons inhibitory
    # (engine.Params.al_ln_inhibitory): pre-registered, it raised the share of
    # learning that is specific to the punished scent from 11% to 61%
    # (t=+21.4, 8/8 seeds, thing_learning_scent_alln.jsonl). Experiments keep
    # the default so everything measured before stays comparable.
    from .engine import Params
    g = gm.Game(cx, rom, sav, state, params=Params(al_ln_inhibitory=True))
    view = BrainView(cx, g.brain, size=MAP)
    worker = GameThread(g, calibrate=calibrate, play_paths=play_paths, resume=resume)

    # A shutdown or kill should save too, not just closing the window.
    import signal
    stop_requested = {"flag": False, "restart": False}
    def _on_term(signum, frame):
        stop_requested["flag"] = True
    def _on_restart(signum, frame):
        # SIGUSR1 means "stop for new code", not "stop". Everything is saved
        # exactly as on a clean exit; the difference is the status, which
        # tells play/launch.sh to bring the game straight back instead of
        # treating the exit as the end of the stream. Without this, deploying
        # a change cost a minute of dead stream while it was relaunched by
        # hand -- SIGTERM exits 0, and 0 is what stops the launcher's loop.
        stop_requested["flag"] = True
        stop_requested["restart"] = True
    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGHUP, _on_term)
    signal.signal(signal.SIGUSR1, _on_restart)

    pygame.init()
    pygame.display.set_caption("flybrain plays")
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    # Closing the window with the touchpad used to kill the process with an
    # X11 BadWindow error before the save on exit could run (see xguard.py).
    guard = None
    wm = pygame.display.get_wm_info()
    if "window" in wm:
        from .xguard import XGuard
        guard = XGuard()
        if not guard.install(wm["window"]):
            guard = None
    font = pygame.font.SysFont("dejavusansmono", 12)
    small = pygame.font.SysFont("dejavusansmono", 11)
    big = pygame.font.SysFont("dejavusansmono", 15, bold=True)
    clock = pygame.time.Clock()

    game_surf = pygame.Surface((g.gba.w, g.gba.h))
    game_big = pygame.Surface((GAME_W, GAME_H))
    brain_small = pygame.Surface((view.res_w, view.res_h))
    brain_big = pygame.Surface((MAP_S, MAP_S))
    overlay = pygame.Surface((MAP_S, MAP_S), pygame.SRCALPHA)
    for _, (cxp, cyp, r) in view.region_px.items():
        k = MAP_S / MAP
        pygame.draw.circle(overlay, OUTLINE, (int(cxp * k), int(cyp * k)), max(2, int(r * k)), 1)

    old_switch = sys.getswitchinterval()
    sys.setswitchinterval(2e-5)      # see viz.run: lets drawing and simulating interleave
    worker.start()
    t_wall0 = time.perf_counter()
    t_fly0 = None
    frames = 0
    running = True
    window_lost = False
    flash = {}                 # button -> frames left to show it lit
    from .effectors import Chirper
    voice = Chirper()          # the piezo: pain, pleasure, and learned turns
    seen_turns = {}            # stat -> count already voiced
    learned_toward = 0

    stim_count = {"pain": 0, "pleasure": 0}    # delivered since this process started
    journal = Journal()                          # every human input + who made progress
    from . import runinfo
    run_tag = [0.0, ""]                     # [when read, text]: re-read each minute (the day ticks)
    run_info = runinfo.current()
    from . import starters as starters_mod
    fav = [f"FLY'S FAVORITE: {starters_mod.scoreboard()}"]
    still_pain = StillPain()
    ow_still = OverworldStill()             # stillness counted in the overworld only
    still_now = [None]                      # its latest reading, for /health
    ui = {}                                 # what /overlay reads (overlay_snapshot)
    spike_bits = SpikeBits(len(g.brain.rate))   # obsfly's brain layer (/live "spk")
    last_stim = {}                              # ...and its pain/pleasure wave
    # Ian, 2026-10-08: does the dialogue pause make the rule fire less? The old
    # rule (no pause) runs alongside on the same frames, COUNTED ONLY -- its
    # pains are never delivered -- so the two counts compare like for like.
    still_shadow = StillPain()
    still_log = StillLog()
    audit_turns = {}
    thought_tally = ThoughtTally()
    thought_log = ThoughtLog()             # per-day thought data for the science tab
    g.on_progress = lambda kind, text, credit: journal.progress(
        {"kind": kind, "text": text, **credit})

    learn_watch = LearningWatch(journal)
    brain_summary = BrainSummary(g.cx)
    science = ScienceCard()

    def stimulate(kind, source="human", who="local"):
        """source: 'human' (chat, panel, the keyboard), 'game' (its own
        rewards) or 'rule' (the stillness rule). Only human ones count as
        people shaping what it learns."""
        last_stim.clear()
        last_stim.update(kind=kind, source=source, t=round(time.time(), 2))
        g.stimulate(kind, source)
        g.attrib.stim(kind, human=source == "human", source=source)
        learn_watch.start(g, kind, source, who)
        stim_count[kind] = stim_count.get(kind, 0) + 1
        flash[kind] = 6
        voice.say(kind)

    # Twitch chat, if asked for: a background reader handing over commands.
    # `chat` lets a test pass in its own bridge.
    paid = None
    announce = False
    if twitch or chat is not None:
        from .twitch import ChatBridge, PaidActions, load_config
        if chat is None:
            chat = ChatBridge(load_config())
            chat.start()
            # Channel points -> pain / pleasure, onto the SAME event queue, so a
            # redemption is paced, credited and shown exactly like a cheer. It
            # turns itself off (with a logged reason) if the login lacks the
            # scope, so an old token costs nothing but the feature.
            try:
                from .eventsub import EventSubBridge
                from .twitch import validate as _validate
                # No user id up front: the token may be stale at start-up and
                # is renewed by the chat bridge a moment later. The bridge
                # resolves the id itself once the login validates.
                points = EventSubBridge(chat.cfg, chat.events, None, validate=_validate)
                points.start()
            except Exception as exc:
                print(f"channel points not started: {type(exc).__name__}: {exc}", flush=True)
        from . import twitch as twitch_mod
        paid = PaidActions()       # Bits -> queued presses and steering time
        _reset_board_if_asked(twitch_mod.BOARD)
        paid.load_board()          # the stream restarts; the leaderboard should not
        # Speaking in chat is off unless the config asks for it: the bot holds
        # the broadcaster's token, so it posts publicly under their name.
        announce = bool(getattr(chat, "cfg", {}).get("announce"))
    from .diary import Diary
    from .events import EventLog
    diary = Diary()            # what has happened to this fly, across restarts
    # The same news, numbered, for anything outside the game that repeats it
    # (the Discord poster). Served on /events; see events.py for why the ids
    # have to outlive a restart.
    events = EventLog()
    # Vertical Shorts of the moments worth posting (see shorts.py).
    from .shorts import ShortsRecorder
    shorts = ShortsRecorder()
    _selftest_done = [False]
    taught = {}                # the last opinion seen, to catch it crossing
    stuck_said = [0.0]         # when chat was last asked for help
    title_at = 0.0                  # the stream title, kept current
    from . import twitch as _tw             # module-level import: no chat needed to read it
    title_was = _tw.recall_title()          # what we set last time, across restarts
    title_off = [False]             # ...unless a human takes it over
    places_seen = set()             # map-section ids met, logged once each
    ad_at = [time.time()]           # last Discord mention; not straight away
    ad_n = [0]                      # how many have gone out, for the rotation
    ad_seen = [0]                   # chat messages counted at the last one
    ticker = []                # (text, frames left) -- who in chat did what
    thought = None             # (text, frames left) -- what the fly just decided
    thought_seen = {}          # decision counters last looked at
    last_thought = [""]        # the latest one, for /health
    heard = [0, ""]            # chat commands understood, and the last one
    press_flash = ["", "", 0]  # [who, key, frames left] -- chat's latest press
    banner = None              # (text, frames left) -- the game's own news
    prev_prog, prev_team = None, None
    # The window as a network video feed for OBS on another machine.
    feed = None
    if serve:
        from .stream import FrameServer
        feed = FrameServer(port=int(serve), size=(WIN_W, WIN_H)).start()
        # The game's sound, for the stream -- OFF unless FLYBRAIN_AUDIO=1. Ian,
        # 2026-10-06: "We aren't using audio." The fly's own hearing reads the
        # emulator directly (gba.audio_rms) and is unaffected.
        if os.environ.get("FLYBRAIN_AUDIO") == "1":
            g.on_audio = feed.push_audio
        feed.events = events.since          # numbered news, for the poster
    if feed is not None and chat is not None:
        # `flybrain test-cheer` from the Pi itself: a real-format cheer line
        # through the same parser, queue and overlay a viewer's would take.
        from .twitch import interpret, parse, rehearsal_line

        def _rehearse(req):
            line = rehearsal_line(str(req.get("user", "tester")), int(req.get("bits", 0)),
                                  str(req.get("text", "")))
            ev = interpret(parse(line))
            if ev is not None:
                chat.events.put(ev)
                if getattr(chat, "wake", None) is not None:
                    chat.wake.set()
            return ev
        feed.inject = _rehearse

        # The control panel. PaidActions belongs to this (window) thread, so a
        # request is queued with its own reply slot and answered from the main
        # loop below, at most a frame later.
        import queue as _queue
        panel_q = _queue.Queue()

        def _panel(req):
            reply = _queue.Queue(maxsize=1)
            panel_q.put((str(req.get("action", "")), req.get("key") or req.get("kind"), reply))
            try:
                ok, note = reply.get(timeout=3.0)
            except _queue.Empty:
                return {"ok": False, "why": "the game did not answer (still loading?)"}
            return {"ok": bool(ok), "note": note}
        feed.control = _panel

    if feed is not None:
        def _clip():
            """The Story fallback: the last 30 s of CLEAN frames (game and
            brain map, none of the window's overlays) with a progress caption
            such as "SQUIRTLE LV 16 - ROUTE 1". Rendered in this HTTP thread;
            the ring buffer holds its own copies, so the game is untouched."""
            from .twitch import PLACES, _nice
            from . import shorts as shorts_mod
            snap = list(shorts.frames)
            if len(snap) < shorts.fps * 5:
                return {"ok": False, "why": f"only {len(snap)} frames in the buffer"}
            team = [m for m in (g.team_calm or []) if not m.get("egg")]
            star = max(team, key=lambda m: m["level"]) if team else None
            where = PLACES.get(g.place) or PLACES.get(g.furthest)
            cap = " - ".join(x for x in (
                f"{_nice(star['name'])} lv {star['level']}" if star else "",
                where or "") if x) or "LIVE RIGHT NOW"
            out = os.path.join(shorts_mod.OUT_DIR, time.strftime("%Y%m%d-%H%M%S") + "-now.mp4")
            path = shorts.render(snap, "now", cap.upper(), out=out)
            return {"ok": bool(path), "path": path, "caption": cap.upper(),
                    "frames": len(snap)}
        feed.clip = _clip
    else:
        panel_q = None
    loop_t0 = time.monotonic()
    while running:
        if stop_requested["flag"]:
            running = False
        if guard is not None and guard.window_lost:
            # The window is already gone: treat it exactly like the close
            # button, but draw nothing more on a window that does not exist.
            window_lost = True
            break
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False
                elif ev.key == pygame.K_SPACE:
                    worker.paused = not worker.paused
                elif ev.key == pygame.K_g and worker.phase == "playing":
                    stimulate("pleasure", who="keyboard")
                    journal.write(_input_record(g, "keyboard", "local_stim", "pleasure", "ok"))
                elif ev.key == pygame.K_b and worker.phase == "playing":
                    stimulate("pain", who="keyboard")
                    journal.write(_input_record(g, "keyboard", "local_stim", "pain", "ok"))
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1 \
                    and worker.phase == "playing":
                for rect, kind in ((PLEASURE_RECT, "pleasure"), (PAIN_RECT, "pain")):
                    if pygame.Rect(rect).collidepoint(ev.pos):
                        stimulate(kind, who="mouse")
                        journal.write(_input_record(g, "mouse", "local_stim", kind, "ok"))

        spikes, shot = worker.drain()
        view._pending.extend(spikes)
        spike_bits.add(spikes)
        view.flush()
        view.fade()

        screen.fill(BG)
        # game
        pygame.surfarray.blit_array(game_surf, shot.swapaxes(0, 1))
        pygame.transform.scale(game_surf, (GAME_W, GAME_H), game_big)
        screen.blit(game_big, (0, 0))
        # brain
        pygame.surfarray.blit_array(brain_small, view.render_map())
        pygame.transform.scale(brain_small, (MAP_S, MAP_S), brain_big)
        screen.blit(brain_big, (MAP_X, MAP_TOP))
        screen.blit(overlay, (MAP_X, MAP_TOP))
        _draw_brain_labels(screen, small, view)
        # Clean frames for vertical Shorts, taken HERE: the screen now holds the
        # raw game picture and the labelled brain map, and none of the
        # Twitch-only clutter drawn after this (chat banner, prices, leader-
        # board, the header). A memcpy into a RAM ring buffer, not an encode.
        if shorts is not None and worker.phase == "playing":
            try:
                brain_px = pygame.surfarray.array3d(
                    screen.subsurface((GAME_W, 0, MAP, MAP))).swapaxes(0, 1)
                shorts.add(shot, brain_px)   # shot is the raw GBA frame, H x W
            except Exception:
                pass                         # a short is never worth a frame
            # Self-test only (env var): render one short, synchronously, from
            # real loop frames, so the capture point and orientation can be
            # checked without waiting hours for a real milestone.
            if (os.environ.get("FLYBRAIN_SHORT_SELFTEST") and not _selftest_done[0]
                    and len(shorts.frames) >= shorts.fps * 8):
                _selftest_done[0] = True
                path = shorts.render(list(shorts.frames), "caught", "CAUGHT A SELFTEST!")
                print(f"SHORT SELFTEST: {path}", flush=True)
                stop_requested["flag"] = True   # done: end the run cleanly
        # Short, and ending well left of x 645: OBS puts the latest tipper in
        # the brain panel's top-right corner (x 650-800, y 9-20 here).
        cap = small.render("FLY BRAIN · 139,255 neurons", True, (200, 205, 215))
        screen.blit(cap, (GAME_W + 8, 5))

        h, e, b = g.hands, g.eyes, g.brain
        if worker.phase == "calibrating":
            msg = big.render("the fly is waking up...", True, (255, 200, 90))
            screen.blit(msg, (GAME_W // 2 - msg.get_width() // 2, GAME_H // 2 - 10))
        elif t_fly0 is None:
            t_fly0, t_wall0 = b.t_ms, time.perf_counter()

        # The game's own news: a catch, a level, a badge, a wipe. The game
        # thread hands over a fresh snapshot about once a second.
        # Only compare snapshots taken while actually playing: the ones from
        # calibration are of a different save.
        if worker.phase != "playing":
            prev_prog, prev_team = None, None
        if (worker.phase == "playing" and g.progress_calm is not None
                and g.progress_calm is not prev_prog):
            for _kind, text in gm.milestones(prev_prog, g.progress_calm, prev_team, g.team_calm):
                banner = (text, fps * 5)
                ticker.append((text, fps * 7))
                voice.say("milestone")
                record_news(_kind, text, diary, events, chat, announce, shorts=shorts,
                            credit=g.attrib.credit(), journal=journal)
                # The game teaches the fly too, not just chat: getting
                # somewhere feels good, losing the whole team does not.
                kind, n = GAME_REWARDS.get(_kind, (None, 0))
                if kind:
                    if paid is not None:
                        paid.enqueue(kind, n)
                    else:
                        stimulate(kind, source="game")
            # Who was at the keys when the party changed (a starter pick, a catch).
            for change, mon in party_changes(prev_team, g.team_calm):
                # The run's first party member is its starter: the scoreboard
                # (just for fun) gets it once, and chat hears about it.
                if change == "joined" and not prev_team and run_info is not None:
                    cr = g.attrib.credit()
                    by = ("fly" if cr["by"] == "fly" else
                          "driver" if cr.get("control") in ("driver", "admin") else "chat")
                    if starters_mod.record(run_info["id"], mon.get("name"), by):
                        board = starters_mod.scoreboard()
                        fav[0] = f"FLY'S FAVORITE: {board}"
                        name = str(mon.get("name", "")).upper()
                        rn = runinfo.name(run_info["id"])
                        banner = (f"{rn.upper()}: THE FLY PICKED {name}!", fps * 8)
                        if chat is not None and announce:
                            chat.say(f"{rn}: the fly picked {name}! Favourite "
                                     f"starter so far (just for fun): {board}")
                journal.progress({"kind": "party", "change": change, "species": mon.get("name"),
                                  "text": f"{mon.get('name')} {change} (Lv {mon.get('level')})",
                                  "level": mon.get("level"), "place": g.place,
                                  "party": [m.get("name") for m in g.team_calm or []],
                                  **g.attrib.credit()})
            prev_prog, prev_team = g.progress_calm, g.team_calm

        # What chat has actually taught it, once the lesson has stuck. The
        # opinions come from the mushroom body, so this is the fly's own
        # verdict on a kind of thing, not a count of button presses.
        if g.mood is not None and frames % 60 == 0:
            for kind, noun in (("people", "people"), ("exits", "doors")):
                o = g.mood.opinion.get(kind, 0.0) if g.mood.opinion else 0.0
                was = taught.get(kind, 0.0)
                if o <= -0.5 < was:
                    diary.add("learned", f"chat taught it to avoid {noun}")
                elif o >= 0.5 > was:
                    diary.add("learned", f"chat taught it to seek out {noun}")
                taught[kind] = o
        # The browse page is where most people meet this stream, so it says
        # what the fly has actually done. Only on a change, and rarely.
        # A brand-new run (no place visited yet) gets its title straight away,
        # before the first overworld reading: otherwise the reset left run 1's
        # "Blastoise lv 36, Beat Brock!" over Oak's intro until the fly found
        # the overworld (2026-10-04). Anything else still waits for a calm
        # reading, so an ordinary restart does not flap the title.
        fresh_run = not g.places_been and g.progress_calm is None
        if (paid is not None and chat is not None
                and (g.progress_calm is not None or fresh_run)
                and time.time() - title_at > twitch_mod.TITLE_EVERY_S):
            want = twitch_mod.stream_title(g.progress_calm, g.team_calm, diary, place=g.furthest,
                                           notice=twitch_mod.title_notice())
            title_at = time.time()
            if want != title_was and not title_off[0]:
                # Never argue with the broadcaster. If the title is not what
                # we last set, a human has edited it by hand, and this stops
                # rather than overwriting them every five minutes.
                live_now = twitch_mod.current_title(chat.cfg)
                if title_was and live_now is not None and live_now.strip() != title_was.strip():
                    title_off[0] = True
                    print(f"stream title left alone: it was edited by hand "
                          f"({live_now!r}); automatic updates off for this run",
                          flush=True)
                    # SAY SO. Yielding in silence is what caused the loop this
                    # guard was blamed for: the broadcaster saw the title stop
                    # updating, could not tell "yielded to you" from "broken",
                    # edited it by hand to compensate, and that edit re-tripped
                    # the guard. He kept the title by hand for hours believing
                    # the automation had failed. A guard nobody can see is
                    # indistinguishable from a fault.
                    if announce:
                        chat.say("the stream title is yours now -- somebody set it "
                                 "by hand, so the fly has stopped rewriting it. "
                                 "run `flybrain title-auto` on the pi to hand it back.")
                else:
                    okay, why = twitch_mod.set_title(chat.cfg, want)
                    if okay:
                        title_was = want
                        twitch_mod.remember_title(want)
                    if not okay:
                        print(f"stream title not set: {why}", flush=True)

        # A place we have no name for is worth recording ONCE, so it can be
        # matched to the screen later and added to twitch.PLACES. Guessing the
        # name from a number is how a title ends up confidently wrong.
        if (worker.phase == "playing" and g.place >= 0 and g.place not in places_seen):
            places_seen.add(g.place)
            from .twitch import PLACES as _PLACES
            if g.place not in _PLACES:
                print(f"unnamed place id {g.place} (add to twitch.PLACES once "
                      f"someone has checked the screen)", flush=True)

        # The stream's Discord, once an hour, only while live and only if
        # anybody has spoken. Live state is read from the Discord poster's own
        # file rather than by polling Twitch again here: it already asks, once
        # a minute, and a second poller on the same token is waste. If that
        # file is missing or stale the answer is "not live" -- failing closed
        # means the worst case is no advert, not an advert to an empty room.
        if announce and frames % (fps * 30) == 0:
            now_s = time.time()
            live_now = False
            try:
                st = os.stat(twitch_mod.POSTER_STATE)
                if now_s - st.st_mtime < 300:
                    with open(twitch_mod.POSTER_STATE) as f:
                        live_now = bool(json.load(f).get("live"))
            except (OSError, ValueError):
                live_now = False
            said_n = getattr(chat, "messages", 0)
            if twitch_mod.discord_due(now_s, ad_at[0], live_now, said_n - ad_seen[0]):
                chat.say(twitch_mod.discord_line(ad_n[0]))
                ad_at[0], ad_seen[0] = now_s, said_n
                ad_n[0] += 1

        if announce and diary.due_to_post():
            line = diary.summary()
            if line:
                chat.say(line)

        # A new run says so once (Ian, 2026-10-08), when chat is up and the
        # picture is back: a banner, the ticker, and a chat line.
        if (run_info is not None and not run_info.get("announced", True)
                and worker.phase == "playing" and frames > fps * 20):
            n = runinfo.name(run_info["id"])
            banner = (f"NEW RUN: {n.upper()} - FRESH BRAIN + NEW GAME", fps * 10)
            ticker.append((f"{n} started: fresh brain, new game, leaderboard reset", fps * 12))
            if chat is not None and announce:
                chat.say(f"NEW RUN: {n} has started -- brand-new game, untrained brain, "
                         f"fresh science data and an empty leaderboard. Everyone starts equal.")
            runinfo.mark_announced()
            run_info["announced"] = True

        # The stillness rule (Ian): 60 s without moving -> pain, then every 10 s.
        if worker.phase == "playing":
            still_s = ow_still.update(g, b.t_ms)
            still_now[0] = still_s
            dialogue = still_s is not None and textbox_open(shot)
            held = bool(getattr(g, "admin", False)) or g.taken_over
            fire = still_pain.check(still_s, blocked=held, paused=dialogue)
            shadow_fire = still_shadow.check(still_s, blocked=held)
            md = g.mood
            smell = dict(g.nose.level) if g.nose is not None else {}
            rec = still_log.update(still_s, md.valence if md else None, md.label if md else None,
                                   smell, human=not g.attrib.clean(), pain=fire, place=g.place,
                                   dialogue=dialogue, shadow_pain=shadow_fire)
            if rec is not None:
                journal.progress(rec)
            if fire:
                stimulate("pain", source="rule", who="stillness rule")
                journal.write(_input_record(g, "stillness rule", "rule_stim", "pain", "rule",
                                            still_s=round(still_s, 1)))
                ticker.append((f"STOOD STILL {still_s:.0f}s: PAIN", fps * 4))
        # Dead air is the stream's worst moment and chat's best one.
        call = (stuck_call(g, b.t_ms, still_now[0]) if worker.phase == "playing"
                and not getattr(g, "admin", False) else None)
        if getattr(g, "admin", False):
            _draw_admin(screen, big, small)
        if call is not None:
            _draw_stuck(screen, small, big, call, low=g.menu_since is not None)
            if announce and time.time() - stuck_said[0] > STUCK_SAY_S:
                stuck_said[0] = time.time()
                chat.say(f"{call[0].lower()} -- {call[1]}")
                # Not the ticker too: the banner below it says the same words
                # twice as large, and the pair read as a rendering fault.
        elif worker.phase == "playing" and g.menu_since is None:
            # The readouts share the top-left with that banner, and when the
            # banner is up it is the more urgent of the two. They also go away
            # out of the overworld: in a battle they drive nothing, and the
            # panel sits exactly where the enemy's health bar is.
            _draw_seeing(screen, small, h)

        # Which way it turned since the last window frame -- the same span the
        # thought's counters cover, so the two line up.
        lr = (h.stats.get("left", 0), h.stats.get("right", 0))
        turned = ("left" if lr[0] > audit_turns.get("lr", lr)[0]
                  else "right" if lr[1] > audit_turns.get("lr", lr)[1] else None)
        audit_turns["lr"] = lr
        dv = getattr(g, "decision_view", None)
        if dv and b.t_ms - dv["t_ms"] >= 1000.0:
            dv = None                       # not this thought's decision
        solid = dv["facing_solid"] if dv else g.facing_solid()
        thought_log.tick(time.time(), worker.phase == "playing")
        learn_watch.tick(g, playing=worker.phase == "playing")
        said = _thought(h, thought_seen, solid=solid)
        if said is not None and os.path.exists(THOUGHT_AUDIT_FLAG):
            journal.write(_thought_truth(g, said, turned),
                          path=os.path.join(journal.folder, "thought_audit.jsonl"))
        if said is not None and g.battle:
            # "wall on the left - turning away" is true of the fly's readouts
            # and meaningless on a battle screen, where those readouts drive
            # nothing. Say what it is actually doing instead.
            said = "in a battle - pushing buttons"
        if said is not None:
            thought_tally.add(said, time.monotonic())
            thought_log.add(said, g.attrib.clean(), dv, battle=bool(g.battle))
            thought = (said, fps * 3)
            last_thought[0] = said
            _thought_now[0] = said
        if thought is not None:
            _draw_thought(screen, small, thought[0])
            thought = (thought[0], thought[1] - 1) if thought[1] > 1 else None

        if time.time() % SCIENCE_EVERY_S < SCIENCE_FOR_S and worker.phase == "playing":
            science.refresh()
            brain_summary.lines(g)              # keeps .rates fresh (cached 1 s)
            _draw_science(screen, font, small, big, science, g.learning, brain_summary.rates)
        else:
            if time.time() - run_tag[0] > 60:
                run_tag[:] = [time.time(), runinfo.tag()]
            _draw_team(screen, font, small, big, g.team_now, g.progress_now, run_tag[1], fav[0])
        _draw_feelings(screen, font, small, big, g.mood, g.hands, g.nose, flash)

        # A tick for every turn made because of something learned: away from
        # a feared kind, or toward a liked one (toward alone is the default
        # attraction, so it only counts when an opinion says 'like').
        for stat, kind in (("away_from_person", None), ("away_from_exit", None),
                           ("toward_person", "people"), ("toward_exit", "exits")):
            n_now = h.stats.get(stat, 0)
            new = n_now - seen_turns.get(stat, n_now)
            seen_turns[stat] = n_now
            if new > 0 and (kind is None or h.opinion.get(kind, 0.0) >= 0.25):
                voice.say("learned")
                if kind is not None:
                    learned_toward += new
        if panel_q is not None:
            while True:
                try:
                    action, key, reply = panel_q.get_nowait()
                except Exception:
                    break
                if paid is None or worker.phase != "playing":
                    reply.put((False, "the game is not playing yet"))
                    continue
                ok, note = paid.panel(action, key)
                journal.write(_input_record(g, paid.PANEL_USER, "panel_" + action, key,
                                            "ok" if ok else "refused"))
                print(f"panel: {action} {key or ''} -> {'ok' if ok else 'refused'}: {note}", flush=True)
                reply.put((ok, note))
        if chat is not None and worker.phase == "playing":
            while True:
                try:
                    ev = chat.events.get_nowait()
                except Exception:
                    break
                note = paid.feed(ev)
                journal.write(_event_record(g, ev, paid.last_outcome))
                heard[0] += 1
                heard[1] = " ".join(str(x) for x in ev)
                if note:                      # tell the viewer what happened
                    ticker.append((note, fps * 5))
                if ev[0] == "stim":
                    ticker.append((f"{ev[3]}: {ev[1].upper()} x{ev[2]}", fps * 6))
                elif ev[0] == "free":
                    ticker.append((f"{ev[3]}: {ev[1].upper()}", fps * 5))
                elif ev[0] == "steer":
                    ticker.append((f"{ev[3]} bought {ev[1]:.0f}s of steering", fps * 6))
                elif ev[0] == "cheer":
                    ticker.append((f"thanks {ev[2]} ({ev[1]} bits)", fps * 5))
                elif ev[0] == "ask" and announce and paid.may_answer(ev[2]):
                    # The fly answers for itself, from what it is doing.
                    try:
                        chat.say(_answer(ev[1], g, diary, paid))
                        ticker.append((f"{ev[3]} asked: {ev[1]}", fps * 5))
                    except Exception as exc:          # never let a question crash play
                        print(f"answer failed: {type(exc).__name__}: {exc}", flush=True)
            for act in paid.tick():
                if act[0] == "stim":
                    src = "game" if act[2] == "the game" else "human"
                    stimulate(act[1], source=src, who=act[2])
                    journal.write(_input_record(g, act[2], "stim_delivered", act[1], src))
                elif act[0] == "nudge":
                    # how this press relates to what the fly was doing (the
                    # journal's vs_fly), for the HUD's coloured chat arrows
                    ui.setdefault("press_vs", deque(maxlen=40)).append(
                        (paid.clock(), act[2], act[1], relation(act[1], g.fly_keys)))
                    g.nudge(act[1], act[3])     # alongside the fly, not instead of it
                    press_flash[:] = [act[2], act[1], fps * 2]
                    voice.say("learned")        # a tick, so chat is audible too
                elif act[0] == "admin":
                    # !menu. Said in chat on the way in and out: on a stream
                    # whose premise is chat control, presses that silently do
                    # nothing read as a broken stream, and people leave.
                    g.admin = act[1]
                    print(f"admin control {'TAKEN' if act[1] else 'released'} ({act[2]})", flush=True)
                    if announce:
                        if act[1]:
                            chat.say("the streamer has taken the controls for a moment to teach "
                                     "the fly a new move -- chat and the fly are paused until "
                                     "they hand back. !pain and !pleasure still work.")
                        elif act[2] in ("timeout", "ceiling"):
                            chat.say("the controls are back with the fly and chat "
                                     "(the streamer's turn timed out).")
                        else:
                            chat.say("the controls are back with the fly and chat -- go!")
                elif act[0] == "tap":
                    g.tap(act[1])
                    press_flash[:] = ["streamer", act[1], fps * 2]
                else:
                    # a paid driver: chat fully in control ("full")
                    ui.setdefault("press_vs", deque(maxlen=40)).append(
                        (paid.clock(), act[2] if len(act) > 2 else "?", act[1], "full"))
                    g.takeover(act[1])
            ticker[:] = [(t, n - 1) for t, n in ticker[-5:] if n > 1]
            # While the game is talking, the standing hints get out of its way.
            # Two ways to be talking. A dialogue box in the overworld is a
            # pixel test; a battle or a menu is not, because those boxes are
            # dark blue and no brightness test finds them -- but out there the
            # game has told us exactly, so use the fact rather than a guess.
            # (g.menu_since is set by the worker whenever the fly is out of the
            # overworld, so this needs no cross-thread peek at the emulator.)
            talking = g.menu_since is not None or textbox_open(shot)
            _draw_chat(screen, small, big, ticker, paid.status(), chat.connected,
                       getattr(chat, "messages", 0), talking=talking)
            _draw_chat_control(screen, small, big, paid.chat_status(),
                               talking=talking or getattr(g, "admin", False))
            _draw_pull(screen, small, big, g)
            _draw_chat_feed(screen, small, big, paid)
            if press_flash[2] > 0:
                _draw_press(screen, big, press_flash)
                press_flash[2] -= 1
            if feed is not None and getattr(feed, "overlay", 0) is None:
                feed.overlay = lambda: overlay_snapshot(g, ui)
            if feed is not None and getattr(feed, "live", 0) is None:
                feed.live = lambda: live_snapshot(g, brain_summary.groups, last_thought[0],
                                                  still_now[0], spk=spike_bits.take,
                                                  stim=last_stim or None)
            if feed is not None and feed.status is None:
                feed.status = lambda: (
                    # phase first: a deploy needs to know when the game is
                    # really back, and /health answers within seconds of the
                    # process starting -- long before it is past calibration
                    # and on screen. See play/restart.sh.
                    # emu= is the EMULATOR's frame count, from the worker
                    # thread. frames= on the line above is the window's
                    # publish count, which keeps rising if the worker hangs
                    # -- the watchdog watched that and was blind to the one
                    # hang it exists for. See play/watchdog.sh.
                    "phase={14} title={30} emu={emu}\n"
                    "chat connected={0} messages={1} heard={2} last_cmd={3} said={10}\n"
                    "chat may drive={4} last={5} presses={6} lastminute={7} "
                    "driver={8}\n"
                    "pull={11}\n"
                    "stuck: menu={12} still={13}\n"
                    "cb2: now={15} overworld={16} battle={17} place={31}\n"
                    "progress: furthest={34} areas={35}\n"
                    # the strongest Pokemon (as in the title), for the
                    # Threads post; lead= last because a name can hold spaces
                    "team: lv={tl} badges={tb} caught={tc} lead={tn}\n"
                    # fly-alone vs human-assisted (attrib.py), for the science
                    "{attrib}\n"
                    # what it feels and what is in range, for the stillness question
                    "{moodline}\n"
                    # the stillness rule (Ian): pains so far, time held off by dialogue
                    "{stillrule}\n"
                    "{thoughtline}\n"
                    "{learnline}\n"
                    "{brainlines}\n"
                    "build: {build}\n"
                    "{runline}\n"
                    "{starterline}\n"
                    "turns: left={18} right={19} a={20} b={21}\n"
                    "drive: walk={22} stall={23} thr={24} bout={25} stalled={26}\n"
                    "moved: tiles={27} bumps={28} bouts={29}\n"
                    "senses: eye={32} spikes={33}\n"
                    # The learning curve (for obsfly's panel): the mushroom
                    # body's opinions, the training that drives them, and the
                    # approach/avoid turns they steer. Cumulative per process.
                    # Who holds exclusive control (!menu or the panel's take),
                    # so a reloaded panel can tell. secs_left = whichever of
                    # the idle timeout and the hard ceiling comes first.
                    "control: held={ch} by={cb} secs_left={cs}\n"
                    "learned: people={lp:+.3f} exits={le:+.3f} pain={sp} pleasure={sq} "
                    "toward_person={tp} toward_exit={te} away_from_person={ap} away_from_exit={ae}\n"
                    "thinking: {9}".format(
                        chat.connected, getattr(chat, "messages", 0),
                        heard[0], heard[1] or "(none yet)",
                        *paid.chat_status(), paid.status()[0],
                        last_thought[0] or "(nothing yet)", getattr(chat, "said", 0),
                        dict(g.pull_counts),
                        "-" if g.menu_since is None else f"{(g.brain.t_ms-g.menu_since)/1000:.0f}s",
                        "-" if still_now[0] is None else f"{still_now[0]:.0f}s",
                        worker.phase,
                        g.cb2, g.overworld_cb2, g.battle,
                        h.stats.get("left", 0), h.stats.get("right", 0),
                        h.stats.get("a", 0), h.stats.get("b", 0),
                        f"{h.walk:.1f}", f"{h.stall:.0f}",
                        "off" if h.stall_thresh > 1e8 else f"{h.stall_thresh:.0f}",
                        h.bout, h.stats.get("stalled", 0),
                        g.steps_moved, g.wall_bumps, h.stats.get("bouts", 0),
                        "yielded (edited by hand)" if title_off[0] else "auto",
                        g.place,
                        f"{e.rates.get('eye', 0.0):.0f}",
                        # cumulative, so a rate comes from the difference
                        # between two reads -- and a busy scene shows up as a
                        # steeper slope. The retina never stops, so its load is
                        # scene-dependent and is the first suspect whenever the
                        # brain slows down (see EYE_REST/EYE_CAP).
                        f"{int(b.spikes_total.sum())}",
                        g.furthest, len(g.places_been), emu=g.gba.frames,
                        ch=paid.admin is not None, cb=(paid.admin[0] if paid.admin else "-"),
                        cs=(max(0, round(min(paid.admin[2], paid.admin[3]) - paid.clock()))
                            if paid.admin else "-"),
                        lp=h.opinion.get("people", 0.0), le=h.opinion.get("exits", 0.0),
                        sp=stim_count["pain"], sq=stim_count["pleasure"],
                        tp=h.stats.get("toward_person", 0), te=h.stats.get("toward_exit", 0),
                        ap=h.stats.get("away_from_person", 0), ae=h.stats.get("away_from_exit", 0),
                        attrib=g.attrib.health(), moodline=_mood_line(g),
                        stillrule=still_pain.line(still_shadow),
                        thoughtline=thought_tally.line(time.monotonic()),
                        learnline=learn_watch.line(g), brainlines=brain_summary.lines(g),
                        build=BUILD, runline=run_line(), starterline=starters_mod.health_line(),
                        **_team_fields(g.team_calm, g.progress_calm)))
        if paid is not None:
            _draw_under_brain(screen, small, paid, diary,
                              story=story_lines(g.progress_calm, g.places_been,
                                                g.furthest, paid))
            if frames % (fps * 60) == 0:
                try:
                    paid.save_board()
                except OSError as exc:
                    print(f"leaderboard save failed: {exc}", flush=True)
        if banner is not None:
            _draw_banner(screen, big, banner[0])
            banner = (banner[0], banner[1] - 1) if banner[1] > 1 else None
        voice.tick()
        for k in list(flash):
            flash[k] -= 1
            if flash[k] <= 0:
                del flash[k]

        if worker.paused:
            screen.blit(big.render("PAUSED", True, (255, 200, 90)), (8, 6))

        pygame.display.flip()
        if feed is not None:
            feed.publish(screen)
            feed.publish_game(shot)              # the bare GBA frame, if anyone watches
        ui.update(banner=banner, call=call, ticker=ticker, paid=paid, diary=diary, science=science,
                  learning=g.learning, stim=last_stim or None, thought=last_thought[0],
                  still_s=still_now[0])
        if frames % 30 == 0:
            ui["story"] = story_lines(g.progress_calm, g.places_been, g.furthest, paid)
        frames += 1
        if screenshot and max_frames and frames == max_frames:
            pygame.image.save(screen, screenshot)
        if max_frames and frames >= max_frames:
            running = False
        # Wait out the frame -- but wake early for a chat command, so it is
        # acted on now rather than up to a frame (50 ms at 20 fps) later. Ian,
        # 2026-10-06: chat control should feel as immediate as possible. At
        # most one early wake per half-frame, so a flood of chat cannot turn
        # into a flood of redraws stealing the brain's CPU.
        wake = getattr(chat, "wake", None) if chat is not None else None
        if wake is None:
            clock.tick(fps)
        else:
            spent = time.monotonic() - loop_t0
            floor = 0.5 / fps
            if spent < floor:
                time.sleep(floor - spent)
            left = 1.0 / fps - (time.monotonic() - loop_t0)
            if left > 0:
                wake.wait(left)
            wake.clear()
            clock.tick()
        loop_t0 = time.monotonic()

    said_before = getattr(chat, "said", 0) if chat is not None else 0
    # Tell chat before the picture goes away. A deliberate restart takes the
    # stream down for about twenty seconds, and an unexplained freeze is
    # indistinguishable from a broken stream to somebody watching -- they
    # leave. Said BEFORE the teardown, while the chat thread is still running,
    # with a moment for it to reach the socket (it paces its own sending).
    if chat is not None and stop_requested["restart"] and announce:
        try:                                 # a run reset is armed: say THAT instead
            from .reset import pending as _reset_pending
            _r = _reset_pending()
        except (Exception, SystemExit):
            _r = None
        if _r is not None:
            chat.say(str(_r.get("chat") or _r.get("announce") or
                         "starting a new run: the fly starts over from scratch."))
        else:
            chat.say("brief maintenance on the fly's code -- the stream will "
                     "freeze for about 20 seconds and come straight back. "
                     "the fly keeps everything it has learned.")
        for _ in range(30):                 # up to 3 s, no longer than needed
            if getattr(chat, "said", 0) != said_before:
                break
            time.sleep(0.1)
        # Recorded, so a maintenance notice can be audited after the fact.
        # say() writes to Twitch, not here; without this line there is no way
        # to tell afterwards whether viewers were actually told.
        print("told chat about the restart"
              if getattr(chat, "said", 0) != said_before else
              "WARNING: could not tell chat about the restart", flush=True)

    worker.stop()
    worker.join(timeout=3.0)
    voice.close()
    if paid is not None:
        try:
            paid.save_board()
        except OSError as exc:
            print(f"leaderboard save failed: {exc}", flush=True)
    if chat is not None:
        chat.stop()
    if feed is not None:
        feed.stop()
    sys.setswitchinterval(old_switch)
    # Final snapshot, taken only once the worker has stopped touching the
    # emulator, and only if the game got past calibration.
    if resume and worker.phase == "playing" and not worker.is_alive():
        try:
            g.save()
            print(f"saved {resume}", flush=True)
        except OSError as exc:
            print(f"save on exit failed: {exc}", flush=True)
    if window_lost:
        # Everything that matters is on disk. Leave now: pygame's own teardown
        # talks to X about the destroyed window and was seen to hang.
        print("window closed; saved and exiting", flush=True)
        sys.stdout.flush()
        os._exit(0)
    g.close()
    pygame.quit()
    g.restart_wanted = stop_requested["restart"]
    return g
