"""Live visual of the brain: a pygame window showing every neuron firing.

Each of the 139,255 neurons is a dot, placed inside its home neuropil on the
schematic in layout.py and coloured by which functional system it belongs to.
Spikes are drawn into a phosphor buffer that fades over a few frames, so you
see activity sweep through the brain rather than a strobe of single frames.

Rendering is done in numpy, not by pygame's draw calls: a per-neuron pixel
index is precomputed once, and each frame scatters the spiking neurons' colours
into three float buffers, fades them, and blits the result. Drawing 139k
individual circles per frame would not come close to real time.
"""
import math
import time
import os
import sys
import threading
import time

import numpy as np

import ctypes

# Tells SDL not to use XInput2 (nothing here needs touch or pointer input).
# This was added to stop an X11 BadWindow crash when closing the window with the
# touchpad. It did NOT stop it: the crash recurred with this set. The actual fix
# is xguard.py, which catches the error so the game still saves. Kept only
# because it is harmless.
os.environ.setdefault("SDL_VIDEO_X11_XINPUT2", "0")


from . import _ext, layout, ports
from .effectors import BuzzerEffector, ServoBank
from .engine import Brain, Params

MAP_W, MAP_H = 780, 780
# The phosphor is accumulated at half resolution and scaled up 2x to draw.
# Quarter the pixels to fade and tone-map each frame, and a lone neuron
# becomes a 2x2 block instead of a single pixel you cannot see.
RES_W, RES_H = MAP_W // 2, MAP_H // 2
PANEL_W = 400
WIN_W, WIN_H = MAP_W + PANEL_W, MAP_H

BG = (8, 10, 14)
PANEL_BG = (13, 16, 22)
OUTLINE = (42, 48, 60)
LABEL = (96, 108, 126)
TEXT = (176, 188, 204)
BRIGHT = (236, 242, 250)

# Colour per functional system. Chosen to stay distinguishable on black.
SYSTEM_COLOR = {
    "optic":           (70, 150, 255),
    "mushroom":        (255, 190, 70),
    "central_complex": (120, 255, 170),
    "olfactory":       (255, 120, 190),
    "mechanosensory":  (255, 110, 90),
    "superior":        (180, 150, 255),
    "other":           (150, 170, 195),
}
# What a viewer is actually looking at, in words they already have. Each is a
# group of neuropils (layout.GROUPS) or a class of neuron, named for what the
# part DOES rather than what it is called: nobody watching a stream knows what
# a lobula plate is, and "EYES" is true. `side` splits the paired ones so the
# label sits on the half it belongs to instead of floating at the midline.
BRAIN_LABELS = (
    ("EYES",    {"group": "optic"},           "left"),
    ("EYES",    {"group": "optic"},           "right"),
    ("SMELL",   {"group": "olfactory"},       None),
    ("MEMORY",  {"group": "mushroom"},        None),
    ("COMPASS", {"group": "central_complex"}, None),
    ("TOUCH",   {"group": "mechanosensory"},  None),
    ("MOTOR",   {"super_class": "descending"}, None),
)
LABEL_WINDOW = 600   # frames of its own history a label is judged against (~40 s)
LABEL_SAMPLE = 3000  # neurons sampled per label; the eyes alone are 45,000 and
                     # a brightness does not need all of them
LABEL_EVERY_S = 0.2  # how often the levels are recomputed, not how often drawn
FADE = 0.80          # phosphor decay per frame
# Brightness per spike. Kept low deliberately: a driven photoreceptor fires
# several times per frame, and at a high gain every channel saturates and the
# whole map goes white, throwing away the colour coding. Low gain plus a
# square-root tone map keeps one spike visible and a thousand still coloured.
GAIN = 22.0


class BrainView:
    def __init__(self, cx, brain, seed=0, size=None):
        self.cx = cx
        self.brain = brain
        # Square map, drawn at `size` pixels; the phosphor is half that.
        self.map_w = self.map_h = int(size or MAP_W)
        self.res_w = self.res_h = self.map_w // 2
        rng = np.random.default_rng(seed)

        places = layout.build()
        npil = cx.labels["neuropil"]

        # Scatter each neuron inside its region's disc. sqrt on the radius
        # keeps the dots evenly spread instead of crowding the centre.
        xs = np.zeros(cx.n, dtype=np.float32)
        ys = np.zeros(cx.n, dtype=np.float32)
        col = np.zeros((cx.n, 3), dtype=np.float32)
        for name in np.unique(npil):
            m = np.flatnonzero(npil == name)
            x0, y0, r = places.get(name, places["UNASGD"])
            if name in ("", "UNASGD"):
                x0, y0, r = 3.0, 3.0, 0.01     # off the map entirely
            ang = rng.random(len(m)) * 2 * math.pi
            rad = np.sqrt(rng.random(len(m))) * r
            xs[m] = x0 + rad * np.cos(ang)
            ys[m] = y0 + rad * np.sin(ang)
            col[m] = SYSTEM_COLOR[layout.group_of(name)]
        self.color = col / 255.0

        # World (-1..1) -> pixels. y flips: dorsal is up on screen.
        pad = 8
        sx = (self.res_w - 2 * pad) / 2.05
        sy = (self.res_h - 2 * pad) / 2.05
        px = np.clip((xs * sx + self.res_w / 2), 0, self.res_w - 1).astype(np.int32)
        py = np.clip((self.res_h / 2 - ys * sy), 0, self.res_h - 1).astype(np.int32)
        # Interleaved RGB so the buffer reshapes straight to (W, H, 3) with no
        # transpose on the render path.
        self.pix3 = ((px * self.res_h + py) * 3).astype(np.int32)
        self._chan = np.arange(3, dtype=np.int64)
        self._color3 = np.ascontiguousarray(self.color, dtype=np.float32)

        self.places = places
        self._pending = []
        self.phos = np.zeros(self.res_w * self.res_h * 3, dtype=np.float32)
        self._tone = np.zeros(self.res_w * self.res_h * 3, dtype=np.float32)
        self.frame = np.zeros((self.res_w, self.res_h, 3), dtype=np.uint8)

        # C helpers for the two hot drawing steps. They allocate nothing and
        # release the GIL, which matters a lot when the simulation is running
        # on another thread. numpy fallback below if the library is missing.
        self._lib = _ext.load_lib()
        if self._lib is not None:
            self._p_phos = _ext.ptr(self.phos, ctypes.c_float)
            self._p_pix3 = _ext.ptr(self.pix3, ctypes.c_int32)
            self._p_col3 = _ext.ptr(self._color3, ctypes.c_float)
            self._p_frame = _ext.ptr(self.frame, ctypes.c_uint8)

        # Region outlines, drawn once into a static overlay.
        # Where each label sits, and which neurons it speaks for.
        self.labels = []
        self._labels_at, self._labels_cached = 0.0, []
        npil_s = np.asarray(npil).astype(str)
        side_s = np.asarray(cx.labels["side"]).astype(str)
        sup_s = np.asarray(cx.labels["super_class"]).astype(str)
        for text, what, side in BRAIN_LABELS:
            if "group" in what:
                m = np.array([layout.group_of(n) == what["group"] for n in npil_s])
            else:
                m = sup_s == what["super_class"]
            if side is not None:
                m = m & (side_s == side)
            idx = np.flatnonzero(m)
            if len(idx) < 50:
                continue
            if len(idx) > LABEL_SAMPLE:      # an even sample, not the first N
                idx = idx[np.linspace(0, len(idx) - 1, LABEL_SAMPLE).astype(np.int64)]
            # Just OUTSIDE its dots (Ian, 2026-10-09: names sat on their own
            # clusters and hid them): above the cluster, or below it when the
            # cluster already touches the top of the map.
            # Lower-half clusters get their name below, upper-half above, so a
            # name never drifts across the map to the far side of its part.
            top, bot = np.percentile(py[idx], 8) * 2, np.percentile(py[idx], 92) * 2
            lower = np.median(py[idx]) > self.res_h / 2
            ly = int(bot + 12) if lower or top - 12 <= 16 else int(top - 12)
            self.labels.append({
                "text": text, "idx": idx,
                "x": int(np.median(px[idx]) * 2), "y": ly,
                "color": SYSTEM_COLOR[layout.group_of(npil_s[idx[0]])
                                      if "group" in what else "other"],
                "hist": np.zeros(LABEL_WINDOW, dtype=np.float32), "n": 0,
            })
        # Descending neurons sit in the same place as the mechanosensory ones,
        # so two names landed on top of each other. Nudge any that collide.
        # (spacing in render pixels; the window draws the map at ~0.7x, so 26
        # here is ~18 on screen -- TOUCH and MOTOR were overlapping at 18)
        for i, lab in enumerate(self.labels):
            for other in self.labels[:i]:
                if abs(lab["x"] - other["x"]) < 70 and abs(lab["y"] - other["y"]) < 26:
                    lab["y"] = other["y"] + 26

        self.region_px = {}
        for name, (x0, y0, r) in places.items():
            if not name or name == "UNASGD":
                continue
            self.region_px[name] = (
                int((x0 * sx + self.res_w / 2) * 2), int((self.res_h / 2 - y0 * sy) * 2),
                max(int(r * sx * 2), 4))

    def label_activity(self):
        """[(text, x, y, colour, 0..1 how lit up it is)] for the window to draw.

        Each part is measured against ITS OWN recent activity, because the
        rates are nowhere near comparable: the retina fires constantly while
        the mushroom body is deliberately sparse, and on a shared scale the
        eyes would sit at full brightness for ever and everything else at zero.
        """
        now = time.monotonic()
        if self._labels_at and now - self._labels_at < LABEL_EVERY_S:
            return self._labels_cached
        self._labels_at = now
        rate = self.brain.rate
        out = []
        for lab in self.labels:
            r = float(rate[lab["idx"]].mean())
            lab["hist"][lab["n"] % LABEL_WINDOW] = r
            lab["n"] += 1
            h = lab["hist"][:min(lab["n"], LABEL_WINDOW)]
            # Where this part sits in its OWN recent range. Two earlier tries
            # failed here: dividing by a running peak pinned every label at
            # full brightness for ever after its first rise, and dividing by a
            # running standard deviation made every label saturate the moment
            # the brain left a quiet patch, because the spread of a quiet
            # patch is almost zero. A range is steady under both.
            lo, hi = np.percentile(h, 20), np.percentile(h, 95)
            level = (r - lo) / (hi - lo) if hi - lo > 1e-6 else 0.0
            out.append((lab["text"], lab["x"], lab["y"], lab["color"],
                        float(min(max(level, 0.0), 1.0))))
        self._labels_cached = out
        return out

    def add_spikes(self, spiked):
        """Queue a step's spikes; they are drawn once per frame by flush().

        This copies, and must. The C backend returns a view into a spike
        buffer it overwrites on the very next step, so queuing the view would
        leave every entry in the queue showing the most recent step -- the
        frame would draw one step fifty times over instead of fifty steps.
        """
        if len(spiked):
            self._pending.append(np.array(spiked, dtype=np.int32))

    def flush(self):
        """Scatter a whole frame of spikes into the phosphor in one pass.

        np.add.at is the obvious call here and was costing 18 ms a frame:
        it is a slow scatter, and it was being run once per simulation step.
        One bincount over the frame's accumulated spikes does the same
        accumulation, duplicates included, an order of magnitude faster.
        """
        if not self._pending:
            return
        idx = (self._pending[0] if len(self._pending) == 1
               else np.concatenate(self._pending))
        self._pending.clear()
        if self._lib is not None:
            self._lib.fly_scatter(
                self._p_phos, self._p_pix3, self._p_col3,
                _ext.ptr(idx, ctypes.c_int32), len(idx), GAIN)
            return
        i64 = idx.astype(np.int64)
        flat = (self.pix3[i64].astype(np.int64)[:, None] + self._chan).ravel()
        vals = (self.color[i64] * GAIN).ravel()
        self.phos += np.bincount(flat, weights=vals, minlength=self.phos.size)

    def fade(self):
        self.phos *= FADE

    def render_map(self):
        # sqrt tone map: compresses the bright end so saturated regions keep
        # their hue instead of clipping to white.
        np.clip(self.phos, 0.0, 1.0e6, out=self.phos)
        np.sqrt(self.phos, out=self._tone)
        self._tone *= 16.0
        np.clip(self._tone, 0, 255, out=self._tone)
        self.frame[:] = self._tone.reshape(self.res_w, self.res_h, 3)
        return self.frame


class SimThread(threading.Thread):
    """Runs the simulation continuously so drawing never stalls it.

    Worth doing because the C step kernel is reached through ctypes, which
    releases the GIL for the duration of the call. Roughly two thirds of a
    step is spent inside that call, so the simulation and the rendering
    genuinely overlap on separate cores instead of taking turns.

    Only the spike queue crosses the boundary, and only under the lock: the
    worker fills a private list and hands the whole batch over once per
    frame, so the lock is taken a dozen times a second rather than a
    thousand. Everything the side panel reads -- firing rates, gait state,
    beep count -- is read without a lock and is allowed to be a frame stale,
    which is invisible at 12 fps and costs nothing.
    """

    def __init__(self, brain, sensors, effectors, enabled, fps):
        super().__init__(daemon=True)
        self.brain = brain
        self.sensors = sensors
        self.effectors = effectors
        self.enabled = enabled
        self.lock = threading.Lock()
        self._outbox = []
        self._local = []
        self.running = True
        self.paused = False
        self._reset = False
        self.steps = 0
        # Hand spikes over, and run the effectors, once per displayed frame.
        self.batch_steps = max(int(1000.0 / fps / brain.p.dt), 1)

    def run(self):
        b = self.brain
        # OpenMP's team size is a per-thread setting, so this has to be done
        # from inside the worker. Leave one core for the drawing thread: with
        # all four inside the kernel the renderer has to preempt one and both
        # sides end up slower.
        lib = _ext.load_lib()
        if lib is not None and hasattr(lib, "fly_set_threads"):
            lib.fly_set_threads(max(1, (os.cpu_count() or 2) - 1))
        while self.running:
            if self._reset:
                b.reset()
                self._local.clear()
                with self.lock:
                    self._outbox = []
                self._reset = False
            if self.paused:
                time.sleep(0.004)
                continue

            for s in self.sensors:
                if self.enabled.get(s.role, True):
                    s.apply(b)
                else:
                    s.value = 0.0
            sp = b.step()
            if len(sp):
                self._local.append(np.array(sp, dtype=np.int64))
            self.steps += 1

            if self.steps % self.batch_steps == 0:
                for e in self.effectors:
                    e.update(b, self.batch_steps * b.p.dt)
                with self.lock:
                    self._outbox.extend(self._local)
                self._local.clear()

    def drain(self):
        with self.lock:
            out = self._outbox
            self._outbox = []
        return out

    def request_reset(self):
        self._reset = True

    def stop(self):
        self.running = False


def _bar(surf, font, x, y, w, label, frac, value, color):
    import pygame
    surf.blit(font.render(label, True, TEXT), (x, y))
    bx, bw = x + 122, w - 122
    pygame.draw.rect(surf, (28, 32, 40), (bx, y + 3, bw, 9))
    f = max(0.0, min(1.0, frac))
    if f > 0:
        pygame.draw.rect(surf, color, (bx, y + 3, int(bw * f), 9))
    surf.blit(font.render(value, True, BRIGHT), (bx + bw + 8, y))


def run(cx, params=None, senses=("eye", "ear", "touch"), period=2000.0,
        hz=150.0, buzzer=True, fps=10, seed=0, max_frames=None,
        screenshot=None, warmup_ms=0.0, threaded=True):
    import pygame

    brain = Brain(cx, params or Params(), seed=seed)
    view = BrainView(cx, brain, seed=seed)

    sensors = []
    for role, pattern, phase in (("eye", "sine", 0.0), ("ear", "pulse", 0.35),
                                 ("touch", "pulse", 0.7)):
        if role in senses:
            s = ports.SyntheticSensor(role, pattern=pattern, phase=phase,
                                      period=period, max_hz=hz).attach(cx)
            if len(s):
                sensors.append(s)

    buz = BuzzerEffector().attach(cx) if buzzer else None
    gait = ServoBank(dry_run=True).attach(cx)

    watch = [(n, cx.select(**s)) for n, s in ports.CENTRAL.items()]
    watch.append(("descending", cx.select(**ports.MOTOR["descending"])))

    pygame.init()
    pygame.display.set_caption("flybrain - Drosophila whole-brain simulation")
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    font = pygame.font.SysFont("dejavusansmono", 13)
    small = pygame.font.SysFont("dejavusansmono", 11)
    big = pygame.font.SysFont("dejavusansmono", 16, bold=True)
    clock = pygame.time.Clock()
    # Allocated once. make_surface() builds a fresh surface every call, which
    # at 20 fps was costing more than the tone mapping.
    small_surf = pygame.Surface((RES_W, RES_H))
    map_surf = pygame.Surface((MAP_W, MAP_H))

    def _overlay(with_labels):
        surf = pygame.Surface((MAP_W, MAP_H), pygame.SRCALPHA)
        for nm, (cxp, cyp, r) in view.region_px.items():
            pygame.draw.circle(surf, OUTLINE, (cxp, cyp), r, 1)
        if with_labels:
            for nm, (cxp, cyp, r) in view.region_px.items():
                if r >= 13:
                    lab = small.render(nm, True, LABEL)
                    surf.blit(lab, (cxp - lab.get_width() // 2, cyp + r + 1))
        return surf

    overlays = {True: _overlay(True), False: _overlay(False)}

    # Static panel strings, rendered once.
    static = {t: font.render(t, True, BRIGHT)
              for t in ("SENSORS", "REGIONS  (Hz)", "OUTPUT", "SYSTEMS")}
    legend = [(c, small.render(nm.replace("_", " "), True, TEXT))
              for nm, c in SYSTEM_COLOR.items()]
    hints = [small.render(t, True, LABEL) for t in
             ("space pause   1/2/3 senses   l labels", "r reset   q quit")]
    legs_hdr = small.render(" L1 L2 L3 R1 R2 R3", True, LABEL)
    title = big.render("flybrain", True, BRIGHT)
    size_line = font.render(f"{cx.n:,} neurons  {cx.n_edges:,} edges", True, TEXT)

    sim_per_frame = 1000.0 / fps        # ms of fly time per frame
    steps_per_frame = max(int(sim_per_frame / brain.p.dt), 1)

    if warmup_ms:      # settle before the first frame is drawn or captured
        for _ in range(int(warmup_ms / brain.p.dt)):
            for s in sensors:
                s.apply(brain)
            brain.step()

    frames = 0
    paused = False
    prof = {k: 0.0 for k in ("event", "sim", "effect", "flush", "tone",
                             "mapblit", "panel", "flip")} \
        if os.environ.get("FLYBRAIN_PROFILE") else None
    _t = time.perf_counter
    show_labels = True
    enabled = {s.role: True for s in sensors}

    effectors = ([buz] if buz else []) + [gait]
    worker = SimThread(brain, sensors, effectors, enabled, fps) if threaded else None
    old_switch = sys.getswitchinterval()
    if worker:
        # Python only reconsiders which thread runs every 5 ms by default.
        # That is coarse next to a 0.75 ms simulation step: the drawing thread
        # would hold the interpreter through a whole numpy call while the
        # simulation sat waiting for the GIL to come back. Dropping the
        # interval lets the two interleave around each other properly and is
        # worth ~0.1x here. Restored when the window closes.
        sys.setswitchinterval(2e-5)
    t_wall0 = time.perf_counter()
    if worker:
        worker.start()
    running = True

    while running:
        _m = _t()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_q, pygame.K_ESCAPE):
                    running = False
                elif ev.key == pygame.K_SPACE:
                    paused = not paused
                    if worker:
                        worker.paused = paused
                elif ev.key == pygame.K_l:
                    show_labels = not show_labels
                elif ev.key == pygame.K_r:
                    view.phos[:] = 0
                    view._pending.clear()
                    if worker:
                        worker.request_reset()
                    else:
                        brain.reset()
                elif ev.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                    i = ev.key - pygame.K_1
                    if i < len(sensors):
                        r = sensors[i].role
                        enabled[r] = not enabled[r]

        if prof is not None: prof["event"] += _t() - _m; _m = _t()
        if worker:
            # The simulation is already running on its own core; just collect
            # whatever it produced since the last frame.
            view._pending.extend(worker.drain())
        elif not paused:
            for _ in range(steps_per_frame):
                for s in sensors:
                    if enabled[s.role]:
                        s.apply(brain)
                    else:
                        s.value = 0.0
                view.add_spikes(brain.step())
            if prof is not None: prof["sim"] += _t() - _m; _m = _t()
            buz and buz.update(brain, sim_per_frame)
            gait.update(brain, sim_per_frame)
        if prof is not None: prof["effect"] += _t() - _m; _m = _t()
        view.flush()
        view.fade()
        if prof is not None: prof["flush"] += _t() - _m; _m = _t()

        # --- brain map ---
        screen.fill(BG)
        _img = view.render_map()
        if prof is not None: prof["tone"] += _t() - _m; _m = _t()
        pygame.surfarray.blit_array(small_surf, _img)
        pygame.transform.scale(small_surf, (MAP_W, MAP_H), map_surf)
        screen.blit(map_surf, (0, 0))
        screen.blit(overlays[show_labels], (0, 0))
        if prof is not None: prof["mapblit"] += _t() - _m; _m = _t()

        # --- side panel ---
        pygame.draw.rect(screen, PANEL_BG, (MAP_W, 0, PANEL_W, WIN_H))
        x, y = MAP_W + 18, 16
        wall = time.perf_counter() - t_wall0
        screen.blit(title, (x, y)); y += 24
        screen.blit(size_line, (x, y)); y += 17
        screen.blit(font.render(
            f"t {brain.t_ms/1000:7.1f}s   {brain.t_ms/1000/max(wall,1e-9):4.2f}x real",
            True, TEXT), (x, y)); y += 17
        screen.blit(font.render(f"{clock.get_fps():4.1f} fps   backend {brain.backend}",
                                True, TEXT), (x, y)); y += 26

        screen.blit(static["SENSORS"], (x, y)); y += 19
        for i, s in enumerate(sensors):
            on = enabled[s.role]
            _bar(screen, font, x, y, PANEL_W - 116,
                 f" {i+1} {s.role:<7}{'' if on else ' off'}",
                 s.value if on else 0.0, f"{s.value if on else 0:4.2f}",
                 (90, 200, 255) if on else (70, 78, 90))
            y += 18
        y += 10

        screen.blit(static["REGIONS  (Hz)"], (x, y)); y += 19
        for name, idx in watch:
            r = brain.population_rate(idx)
            _bar(screen, font, x, y, PANEL_W - 116, f" {name[:15]}",
                 r / 8.0, f"{r:5.2f}", (120, 235, 170))
            y += 18
        y += 10

        screen.blit(static["OUTPUT"], (x, y)); y += 19
        if buz:
            state = "BEEP" if buz._on else "  . "
            col = (255, 120, 90) if buz._on else (70, 78, 90)
            screen.blit(font.render(f" buzzer  {state}  {buz.beeps} beeps",
                                    True, col), (x, y)); y += 18
        _bar(screen, font, x, y, PANEL_W - 116, " gait speed", gait.speed,
             f"{gait.speed:4.2f}", (255, 190, 70)); y += 18
        _bar(screen, font, x, y, PANEL_W - 116, " turn", (gait.turn + 1) / 2,
             f"{gait.turn:+4.2f}", (255, 190, 70)); y += 22

        legs = " ".join(f"{int(a):3d}" for a in gait.angles)
        screen.blit(legs_hdr, (x, y)); y += 13
        screen.blit(small.render(f" {legs}", True, TEXT), (x, y)); y += 24

        screen.blit(static["SYSTEMS"], (x, y)); y += 19
        for c, surf in legend:
            pygame.draw.rect(screen, c, (x + 2, y + 3, 9, 9))
            screen.blit(surf, (x + 18, y)); y += 15

        y = WIN_H - 40
        screen.blit(hints[0], (x, y)); y += 14
        screen.blit(hints[1], (x, y))

        if paused:
            screen.blit(big.render("PAUSED", True, (255, 200, 90)), (18, 14))

        if prof is not None: prof["panel"] += _t() - _m; _m = _t()
        pygame.display.flip()
        if prof is not None: prof["flip"] += _t() - _m
        frames += 1
        if screenshot and frames == (max_frames or 1):
            pygame.image.save(screen, screenshot)
        if max_frames and frames >= max_frames:
            running = False
        clock.tick(fps)

    if worker:
        worker.stop()
        worker.join(timeout=2.0)
        sys.setswitchinterval(old_switch)

    if prof is not None and frames:
        print("\nframe breakdown (ms):")
        for k, val in prof.items():
            print(f"  {k:9s} {val/frames*1e3:7.2f}")
        print(f"  {'TOTAL':9s} {sum(prof.values())/frames*1e3:7.2f}")
    if buz:
        buz.close()
    pygame.quit()
    return brain
