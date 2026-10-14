"""The fly plays a Game Boy Advance game.

The emulator runs inside this process (see _gba.c), in lockstep with the
brain: one GBA frame per 16.74 ms of simulated fly time. So if the brain runs
slower than real time the game does too, and the two never drift apart.

Wiring, and why each piece is wired the way it is:

EYES   The rendered frame, nothing else. Overall brightness and frame-to-frame
       change drive the photoreceptors. Scrolling of the scene is measured and
       fed into the T4/T5 direction-selective motion detectors, expressed in
       the player's own frame of reference: moving forward gives front-to-back
       motion on both eyes (T4a/T5a), sliding sideways gives front-to-back on
       one eye and back-to-front on the other. Motion goes in at T4/T5 rather
       than at the photoreceptors because the connectome has no per-cell
       retinal positions, so there is no way to paint a moving pattern across
       the retina; and uniform flashes on one eye were measured to carry no
       steering information at all, while T4/T5 input steers cleanly.

EARS   Onsets in the game audio -- loudness above its own recent average --
       drive Johnston's organ. Steady music fades into the background; a bump
       or a menu blip does not.

TOUCH  Walking without moving: the direction is held but the player's tile
       position, read from game RAM, does not change. That drives the bristle
       mechanosensors. It is the one thing read from memory instead of pixels,
       and it stands in for leg proprioception, not vision.

HANDS  Turning away from obstacles comes from the DNp35/DNp18 mirror pairs, which
       report which eye's LPLC1 is driven. Occasional spontaneous turns come
       from the left/right imbalance of DNp15 and DNg41 -- pairs that steer
       cleanly under yaw motion, but that in this grid game fire almost
       entirely without it, so only their rarest swings turn the fly. Walking comes
       in bouts started and sustained by overall descending drive. A is a
       surge of descending activity, beyond ordinary walking or well above
       its own recent level; B is a burst in the moonwalker neurons
       (MDN) above their own baseline, since their raw rate is high under any
       motion. Start and Select are never pressed: nothing the fly does can
       reach the save menu.

Every threshold is calibrated from the brain's own output during a short
warm-up (see Game.calibrate), not tuned by hand toward good play. The fly is
not trained and has no goal. Expect wandering, wall-following, talking to
whoever it bumps into, and mashing through menus -- not progress.
"""
import ctypes
import json
import os

import numpy as np

from . import _ext, ports
from .engine import Brain, Params
from .motivation import Motivation
from .attrib import Attribution

A, B, SELECT, START, RIGHT, LEFT, UP, DOWN, R, L = (1 << i for i in range(10))

# Headings clockwise from up. dx, dy in screen pixels (y grows downward).
HEADINGS = ((UP, 0, -1, "up"), (RIGHT, 1, 0, "right"),
            (DOWN, 0, 1, "down"), (LEFT, -1, 0, "left"))

GBA_FRAME_MS = 1000.0 / 59.7275
REPRESS_FRAMES = 2.5           # game frames a repeated chat key is let go for (a fresh key-down)


def nudge_window(t, held_keys, held_until, mask, ms, pace):
    """(keys, until, gap_until or None) for a chat press of `mask` at brain time t.

    The same key again while chat still holds it (Ian, 2026-10-13: "I can't
    press the same button sometimes"): extending the hold gave the game no new
    key-down, so two !a were one A. Now the key is let go for REPRESS_FRAMES
    game frames, then pressed afresh for the full time."""
    if t < held_until and held_keys & mask:
        gap = REPRESS_FRAMES * GBA_FRAME_MS / max(pace, 1e-3)
        return mask, t + gap + ms, t + gap
    return mask, t + ms, None


def mix_keys(t, fly, nudge_keys, nudge_until, gap_mask, gap_until, override_keys, override_until):
    """What reaches the game: the fly's keys, plus chat's (or with a repeated
    key released for its gap), or a paid driver's alone."""
    keys = fly
    if t < gap_until:
        keys &= ~gap_mask                 # the release before a repeated press
    elif t < nudge_until:
        keys |= nudge_keys                # added to the fly, not instead of it
    if t < override_until:
        keys = override_keys              # bought control: exclusive
    return keys
EYE_REST = 8.0                 # photoreceptor rate in a scene it has adapted to
EYE_CAP = 80.0                 # and the most it may reach. Adapting alone only cut
                               # the drive 31 -> 24 Hz, because an animated room
                               # never settles: the gains themselves were the
                               # problem. This channel is one global number about
                               # the whole screen -- it exists to keep the visual
                               # system alive, not to carry information -- and at
                               # the old gains it WAS the fly's entire afferent
                               # load. What the fly steers by (walls, people,
                               # doors) comes in on its own channels and is
                               # untouched by any of this.
ADAPT_TAU = 0.02               # how fast the eye adapts (~50 frames, under a second)
RETUNE_SAMPLES = 5400          # frames of history the thresholds are re-set from (~90 s)
RETUNE_EVERY = 450             # frames between re-settings (~7.5 s)
RETUNE_MIN = 40                # lopsided views needed before trusting a re-setting
# What counts as "really off to one side" when the thresholds are being set.
# Doors are worth walking toward from across a room, so a faint one still
# counts. People are not: measured in play, every wrong "someone on the left"
# had the person 4-5 tiles away (truth gap 0.08-0.22, so a drive of only
# 12-33 Hz) while the readout sat at +190 to +432 Hz -- which is crosstalk
# from the far stronger wall and door channels, not the person. Asking for a
# clear view of somebody keeps the fly from answering the leak.
RETUNE_CLEAR = {"people": 0.25, "exits": 0.1}
RETUNE_BASE = 900              # frames the BASE RATE is estimated from (~15 s). How
                               # often a thing is in view is one number and needs far
                               # less evidence than a distribution does, so it is
                               # measured over a much shorter window: taken over the
                               # full 90 s it lagged a room change badly, and the fly
                               # walking from a corridor full of doors into a sparse
                               # room kept a threshold set for the corridor and spoke
                               # mostly nonsense until the window rolled over.

# How much of the fly's own time the game costs it. The brain is the slow
# part: one millisecond of 139,255 neurons takes about two milliseconds of
# wall clock on this machine, so a game frame's worth of thinking (16.7 ms)
# takes ~35 ms and the game runs at roughly a third of normal speed.
#
# `game_speed` buys normal speed with the only currency there is: the fly's
# thinking time per frame. At 1.0 the fly gets the full 16.7 ms of brain time
# between frames and the game crawls; at 4.0 it gets a quarter of that and the
# game runs at about normal speed, with the fly reacting proportionally more
# slowly IN GAME TIME. Its neurons are unchanged either way -- rates,
# thresholds and every calibrated readout are in brain time, which still runs
# at true physiological speed. What changes is how much world goes past
# between its decisions.
GAME_SPEED = float(os.environ.get("FLYBRAIN_SPEED", "1.0"))
# ...but thinking time is only worth buying where the fly is actually using it.
# Out of the overworld -- in a battle, a menu, a shop -- it cannot navigate and
# its vision drives nothing; all it does is press A to get through. Measured on
# the live stream in tall grass on Route 3: 87.7% of twelve minutes was spent
# out of the overworld, nearly all of it in wild battles, and 77.7% of samples
# were more than twenty seconds into one. That is most of the stream, and it is
# the dead air chat complains about as well as the slowness.
#
# So the game runs faster exactly there. Nothing is taken from the fly while it
# is finding its way; what changes is how long a battle takes to sit through.
MENU_SPEED = float(os.environ.get("FLYBRAIN_MENU_SPEED", "3.0"))

PLAY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "play")


class GBA:
    def __init__(self, rom, sav=None, state=None):
        self.lib = _ext.load_gba()
        if self.lib is None:
            raise SystemExit("GBA support unavailable (install libmgba-dev)")
        enc = lambda p: os.fsencode(p) if p else None
        self.rom_path = rom
        rc = self.lib.gba_open(enc(rom), enc(sav), enc(state))
        if rc != 0:
            raise SystemExit(f"could not start {rom} (gba_open returned {rc})")
        self.w, self.h = self.lib.gba_width(), self.lib.gba_height()
        self.bpp = self.lib.gba_bytes_per_pixel()
        self.frames = 0

    def frame(self, keys, allow=0):
        """Run one frame; returns an (h, w, 3) uint8 view of the framebuffer.

        The view aliases the emulator's own buffer and is overwritten by the
        next frame -- copy it to keep it. START and SELECT are stripped: the
        fly never opens the menu. `allow` lets one through on purpose -- only
        the streamer's !menu taps pass it.
        """
        ptr = self.lib.gba_frame(keys & ~((START | SELECT) & ~allow))
        self.frames += 1
        raw = (ctypes.c_uint8 * (self.w * self.h * self.bpp)).from_address(ptr)
        return np.ctypeslib.as_array(raw).reshape(self.h, self.w, self.bpp)[:, :, :3]

    def audio_rms(self):
        return self.lib.gba_audio_rms()

    def audio_samples(self):
        """The last frame's sound: an (n, 2) int16 array, left/right, at the
        emulator's own rate (see AUDIO_RATE). Empty if the shim predates it."""
        if not hasattr(self.lib, "gba_audio_samples"):
            return np.zeros((0, 2), dtype=np.int16)
        buf = np.empty((4096, 2), dtype=np.int16)
        n = self.lib.gba_audio_samples(buf.ctypes.data, 4096)
        return buf[:n].copy()

    def player(self):
        """(map_group, map_num, x, y) from FireRed-family RAM, or None."""
        sb1 = self.lib.gba_read32(0x03005008)
        if not 0x02000000 <= sb1 < 0x02040000:
            return None
        pos = (self.lib.gba_read8(sb1 + 4), self.lib.gba_read8(sb1 + 5),
               self.lib.gba_read16(sb1), self.lib.gba_read16(sb1 + 2))
        # All zeros before the save block is populated (boot, intro, naming):
        # there is no player on a map yet, so no position and no bumps.
        return None if pos == (0, 0, 0, 0) else pos

    def save_state(self, path):
        """Snapshot the whole machine to `path`, atomically.

        Written to a temporary file, flushed to disk, then renamed over the
        old snapshot and the directory flushed too. The Pi may be switched off at
        any moment, and a cut in the middle of a save must leave the previous
        snapshot intact rather than a half-written file.
        """
        tmp = path + ".tmp"
        rc = self.lib.gba_save_state(os.fsencode(tmp))
        if rc != 0:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise OSError(f"savestate failed ({rc}) writing {tmp}")
        with open(tmp, "rb") as f:
            os.fsync(f.fileno())
        # Keep the previous snapshot as .prev, by HARD LINK, so `path` itself
        # is never missing even for an instant (a missing snapshot would be
        # read as "new run"). If the newest one is ever unreadable -- SSD
        # damage, the risk this Pi's power supply carries -- the game falls
        # back to it instead of crash-looping (see load_with_fallback).
        if os.path.exists(path):
            try:
                link = path + ".prev.tmp"
                if os.path.exists(link):
                    os.remove(link)
                os.link(path, link)
                os.replace(link, path + ".prev")
            except OSError:
                pass
        os.replace(tmp, path)
        dfd = os.open(os.path.dirname(os.path.abspath(path)), os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)

    def load_state(self, path):
        rc = self.lib.gba_load_state(os.fsencode(path))
        if rc != 0:
            raise OSError(f"could not load savestate {path} ({rc})")

    BACKUP_DIR = os.path.expanduser("~/flybrain-backup/play")

    def load_with_fallback(self, path):
        """Load the newest snapshot that works: the current one, then the one
        before it, then the daily off-device backup. Returns the path used.
        Raises only if none loads -- and then the launcher's restarts plus the
        Discord poster's game-down alert are what tell a person."""
        tried = []
        for cand in (path, path + ".prev",
                     os.path.join(self.BACKUP_DIR, os.path.basename(path))):
            if not os.path.exists(cand):
                continue
            try:
                self.load_state(cand)
                if tried:
                    print(f"WARNING: snapshot {tried[0]} would not load; resumed from {cand}",
                          flush=True)
                return cand
            except OSError as exc:
                tried.append(cand)
                print(f"snapshot {cand} would not load: {exc}", flush=True)
        raise OSError(f"no snapshot would load: {tried}")

    def map_section(self):
        """Which named place the fly is in, as a number. Names live in
        game.PLACES, which is filled in only for places actually seen."""
        return self.lib.gba_read32(self.MAP_SECTION) & 0xFF

    def callback2(self):
        """gMain.callback2: which top-level routine is driving the game.

        Constant while walking the overworld (0x080565C9 in this hack), and
        something else in battles and their transitions (0x080565BD, then
        0x08011115). Dialogue does not change it. The overworld value is
        learned at runtime, not assumed -- see Game.overworld_cb2."""
        return self.lib.gba_read32(0x030030F4)

    # --- what is walkable -------------------------------------------------------
    LAYOUT = 0x03005040       # FireRed gBackupMapLayout: s32 width, s32 height, u16 *map
    MAP_OFFSET = 7            # that layout has a 7-tile border around the map proper
    OBJECTS, OBJECT_SIZE, OBJECT_COUNT = 0x02036E38, 0x24, 16   # gObjectEvents

    FREE, WALL, PERSON, EXIT = 0, 1, 2, 3
    MAP_HEADER = 0x02036DFC   # gMapHeader, a copy of the current map's header
    # gMapHeader.regionMapSectionId: which named place the current map belongs
    # to, as the game itself groups them for the town map. Every room of a
    # cave or a town shares one id, which is exactly the granularity a stream
    # title wants -- "Mt. Moon", not "Mt. Moon B1F room 3".
    MAP_SECTION = MAP_HEADER + 0x14

    def _map_extras(self, pos):
        """Warp tiles and edge connections for the current map, cached per map.

        Warps are verified: walking onto each of the three doors in the new
        game's town, and onto the ladder in Mt. Moon, landed on exactly the map
        the warp entry names. Edge connections are read the same way but not
        yet verified by walking (the one attempt was stopped by a story script).
        """
        key = pos[:2]
        if getattr(self, "_extras_key", None) == key:
            return self._extras
        lib = self.lib
        s16 = lambda v: v - 0x10000 if v & 0x8000 else v
        rom = lambda p: 0x08000000 <= p < 0x0A000000
        warps, conns = set(), set()
        events = lib.gba_read32(self.MAP_HEADER + 4)
        if rom(events):
            n, wp = lib.gba_read8(events + 1), lib.gba_read32(events + 8)
            if rom(wp):
                warps = {(s16(lib.gba_read16(wp + 8 * i)), s16(lib.gba_read16(wp + 8 * i + 2)))
                         for i in range(min(n, 64))}
        conn = lib.gba_read32(self.MAP_HEADER + 12)
        if rom(conn):
            count, lst = lib.gba_read32(conn), lib.gba_read32(conn + 4)
            if rom(lst):
                conns = {lib.gba_read8(lst + 12 * i) for i in range(min(count, 8))}
        self._extras_key, self._extras = key, (warps, conns)
        return self._extras

    def scene_grid(self, pos, radius=3):
        """(2r+1)x(2r+1) int grid of FREE / WALL / PERSON / EXIT, centred on the player."""
        lib = self.lib
        w = lib.gba_read32(self.LAYOUT); h = lib.gba_read32(self.LAYOUT + 4)
        ptr = lib.gba_read32(self.LAYOUT + 8)
        n = 2 * radius + 1
        grid = np.full((n, n), self.WALL, dtype=np.int8)
        if not (0 < w < 1024 and 0 < h < 1024 and 0x02000000 <= ptr < 0x02040000):
            return grid
        mw, mh = w - 2 * self.MAP_OFFSET - 1, h - 2 * self.MAP_OFFSET - 1   # the map proper
        warps, conns = self._map_extras(pos)
        px, py = pos[2], pos[3]
        for gy in range(n):
            y = py + gy - radius
            for gx in range(n):
                x = px + gx - radius
                if (x, y) in warps:
                    grid[gy, gx] = self.EXIT
                    continue
                tx, ty = x + self.MAP_OFFSET, y + self.MAP_OFFSET
                if not (0 <= tx < w and 0 <= ty < h):
                    continue                                   # beyond what is loaded: wall
                solid = (lib.gba_read16(ptr + 2 * (ty * w + tx)) >> 10) & 3
                outside = x < 0 or x >= mw or y < 0 or y >= mh
                if outside:
                    # Past the map edge. With no connection on that side it is a
                    # wall. With one, the loaded border holds the neighbouring
                    # map's real tiles, so only its walkable ones are a way out --
                    # not everything behind the tree line.
                    # (connection directions: 1 down, 2 up, 3 left, 4 right)
                    # Only the first row just past an edge, within the map's
                    # span along that edge, is the doorway; corners and deeper
                    # rows of the neighbouring map are not a way out from here.
                    in_x, in_y = 0 <= x < mw, 0 <= y < mh
                    doorway = ((y == mh and in_x and 1 in conns) or (y == -1 and in_x and 2 in conns) or
                               (x == -1 and in_y and 3 in conns) or (x == mw and in_y and 4 in conns))
                    grid[gy, gx] = self.EXIT if (doorway and not solid) else self.WALL
                else:
                    grid[gy, gx] = self.WALL if solid else self.FREE
        s16 = lambda v: v - 0x10000 if v & 0x8000 else v
        for i in range(self.OBJECT_COUNT):
            base = self.OBJECTS + i * self.OBJECT_SIZE
            if lib.gba_read8(base) & 1:
                ox = s16(lib.gba_read16(base + 0x10)) - self.MAP_OFFSET - px + radius
                oy = s16(lib.gba_read16(base + 0x12)) - self.MAP_OFFSET - py + radius
                if (ox, oy) != (radius, radius) and 0 <= ox < n and 0 <= oy < n:
                    grid[oy, ox] = self.PERSON
        return grid

    # --- how far the fly has actually got ---------------------------------------
    SAVEBLOCK2 = 0x0300500C
    PARTY, PARTY_COUNT, PARTY_SIZE = 0x02024284, 0x02024029, 100
    FLAGS_OFF, FLAGS_LEN = 0x0EE0, 0x12C     # within SaveBlock1
    BADGE1_FLAG = 0x820
    MONEY_OFF, MONEY_KEY_OFF = 0x0290, 0x0F20
    DEX_OFF, DEX_OWNED, DEX_SEEN, DEX_BYTES = 0x18, 0x10, 0x44, 52

    # --- the team -----------------------------------------------------------------
    # Gen 3 keeps each party Pokemon's species in an encrypted 48-byte block:
    # four 12-byte substructures (Growth, Attacks, EVs, Misc) in one of 24
    # orders picked by personality % 24, each word XORed with personality ^ OT
    # id. Species is the first half-word of Growth. Level and HP sit in the
    # clear after the block.
    _ORDERS = ("GAEM", "GAME", "GEAM", "GEMA", "GMAE", "GMEA", "AGEM", "AGME",
               "AEGM", "AEMG", "AMGE", "AMEG", "EGAM", "EGMA", "EAGM", "EAMG",
               "EMGA", "EMAG", "MGAE", "MGEA", "MAGE", "MAEG", "MEGA", "MEAG")

    @staticmethod
    def decode_text(raw):
        """Gen 3 in-game text -> str (letters, digits, common punctuation)."""
        out = []
        for b in raw:
            if b == 0xFF:
                break
            if 0xBB <= b <= 0xD4: out.append(chr(65 + b - 0xBB))
            elif 0xD5 <= b <= 0xEE: out.append(chr(97 + b - 0xD5))
            elif 0xA1 <= b <= 0xAA: out.append(chr(48 + b - 0xA1))
            else: out.append({0x00: " ", 0xAB: "!", 0xAC: "?", 0xAD: ".", 0xAE: "-",
                              0xB4: "'", 0xB5: "(M)", 0xB6: "(F)", 0xB8: ",", 0x1B: "e"}.get(b, ""))
        return "".join(out)

    def _species_table(self):
        """ROM address of the species name table, found by looking for
        BULBASAUR (entry 1) in the ROM itself, so it holds for any
        FireRed-family ROM without trusting a hard-coded address."""
        if getattr(self, "_names_at", None) is None:
            self._names_at = 0
            try:
                data = open(self.rom_path, "rb").read()
                needle = bytes([0xBC, 0xCF, 0xC6, 0xBC, 0xBB, 0xCD, 0xBB, 0xCF, 0xCC, 0xFF])
                i = data.find(needle)
                if i >= 11:
                    self._names_at = 0x08000000 + i - 11
            except OSError:
                pass
        return self._names_at

    def species_name(self, species):
        base = self._species_table()
        if not base or not 0 < species < 450:
            return f"#{species}"
        raw = bytes(self.lib.gba_read8(base + 11 * species + k) for k in range(11))
        return self.decode_text(raw) or f"#{species}"

    def team(self):
        """The party: list of dicts (name, nickname, species, level, hp, max_hp),
        or None if no save block is loaded yet."""
        lib = self.lib
        if not 0x02000000 <= lib.gba_read32(0x03005008) < 0x02040000:
            return None
        count = lib.gba_read8(self.PARTY_COUNT)
        if not 0 <= count <= 6:
            return None
        out = []
        for i in range(count):
            base = self.PARTY + self.PARTY_SIZE * i
            pid, otid = lib.gba_read32(base), lib.gba_read32(base + 4)
            g = self._ORDERS[pid % 24].index("G")
            species = (lib.gba_read32(base + 32 + 12 * g) ^ pid ^ otid) & 0xFFFF
            if species == 0:
                continue
            nick = self.decode_text(bytes(lib.gba_read8(base + 8 + k) for k in range(10)))
            name = self.species_name(species)
            out.append({"name": name, "nickname": nick, "species": species,
                        "level": lib.gba_read8(base + 84), "egg": species == 412,
                        "hp": lib.gba_read16(base + 86), "max_hp": lib.gba_read16(base + 88)})
        return out

    def map_shape(self):
        """The whole current map's walkability, as (solid, walkable_count,
        wall_adjacent_count), or None if the layout is not loaded.

        The null model for wall-following: a fly wandering with no preference
        for walls would be found beside one at the rate the map itself
        provides. Without that baseline a high 'time spent by a wall' means
        nothing -- corridors are mostly wall-adjacent whatever the fly does.
        """
        lib = self.lib
        w = lib.gba_read32(self.LAYOUT)
        h = lib.gba_read32(self.LAYOUT + 4)
        ptr = lib.gba_read32(self.LAYOUT + 8)
        if not (0 < w < 1024 and 0 < h < 1024 and 0x02000000 <= ptr < 0x02040000):
            return None
        solid = np.ones((h, w), dtype=bool)
        for ty in range(h):
            base = ptr + 2 * ty * w
            for tx in range(w):
                solid[ty, tx] = bool((lib.gba_read16(base + 2 * tx) >> 10) & 3)
        free = ~solid
        # A walkable tile counts as wall-adjacent if any of its four
        # neighbours is solid; tiles on the array edge are skipped rather
        # than guessed at.
        inner = free[1:-1, 1:-1]
        touch = (solid[:-2, 1:-1] | solid[2:, 1:-1] |
                 solid[1:-1, :-2] | solid[1:-1, 2:])
        return solid, int(inner.sum()), int((inner & touch).sum())

    def progress(self):
        """What the fly has achieved, from save RAM, or None if not in a game.

        Addresses are the FireRed ones this hack inherits, checked against a
        known save: money decodes to exactly 3000, the canonical starting
        amount, which only works if both SaveBlock pointers and the security
        key offset are right. The rest read 0 on a save with nothing done yet,
        and play time matches the session that produced it.

        No single score: the components mean different things, and a weighted
        total would only invite tuning toward whichever one is cheapest to
        move. Pick a primary per experiment and say so in advance.
        """
        lib = self.lib
        sb1 = lib.gba_read32(0x03005008)
        sb2 = lib.gba_read32(self.SAVEBLOCK2)
        if not (0x02000000 <= sb1 < 0x02040000 and 0x02000000 <= sb2 < 0x02040000):
            return None
        pop = lambda v: bin(v).count("1")
        count = lib.gba_read8(self.PARTY_COUNT)
        levels = []
        if 0 < count <= 6:
            for i in range(count):
                base = self.PARTY + self.PARTY_SIZE * i
                levels.append(lib.gba_read8(base + 84))
        flags = sb1 + self.FLAGS_OFF
        badges = sum((lib.gba_read8(flags + (self.BADGE1_FLAG + b) // 8)
                      >> ((self.BADGE1_FLAG + b) % 8)) & 1 for b in range(8))
        dex = sb2 + self.DEX_OFF
        return {
            "badges": badges,
            "party": len(levels),
            "levels": levels,
            "level_sum": sum(levels),
            "money": lib.gba_read32(sb1 + self.MONEY_OFF) ^ lib.gba_read32(sb2 + self.MONEY_KEY_OFF),
            "dex_owned": sum(pop(lib.gba_read8(dex + self.DEX_OWNED + i)) for i in range(self.DEX_BYTES)),
            "dex_seen": sum(pop(lib.gba_read8(dex + self.DEX_SEEN + i)) for i in range(self.DEX_BYTES)),
            "flags_set": sum(pop(lib.gba_read8(flags + i)) for i in range(self.FLAGS_LEN)),
            "play_s": (lib.gba_read16(sb2 + 0x0E) * 3600 + lib.gba_read8(sb2 + 0x10) * 60
                       + lib.gba_read8(sb2 + 0x11)),
        }

    def solid_grid(self, pos, radius=3):
        """(2r+1)x(2r+1) bool array, True where the player cannot walk.

        Centred on the player, rows = y (down), cols = x (right). A tile is
        solid if its collision bits are set or a character stands on it.
        Checked against actually walking: of tiles marked solid, 24/24 were
        blocked; of tiles marked walkable, 75/80 were walkable (the misses
        look like elevation changes, which this does not model).
        """
        lib = self.lib
        w = lib.gba_read32(self.LAYOUT)
        h = lib.gba_read32(self.LAYOUT + 4)
        ptr = lib.gba_read32(self.LAYOUT + 8)
        n = 2 * radius + 1
        grid = np.ones((n, n), dtype=bool)
        if not (0 < w < 1024 and 0 < h < 1024 and 0x02000000 <= ptr < 0x02040000):
            return grid
        px, py = pos[2], pos[3]
        for gy in range(n):
            ty = py + gy - radius + self.MAP_OFFSET
            for gx in range(n):
                tx = px + gx - radius + self.MAP_OFFSET
                if 0 <= tx < w and 0 <= ty < h:
                    grid[gy, gx] = bool((lib.gba_read16(ptr + 2 * (ty * w + tx)) >> 10) & 3)
        s16 = lambda v: v - 0x10000 if v & 0x8000 else v
        for i in range(self.OBJECT_COUNT):
            base = self.OBJECTS + i * self.OBJECT_SIZE
            if lib.gba_read8(base) & 1:
                ox = s16(lib.gba_read16(base + 0x10)) - self.MAP_OFFSET - px + radius
                oy = s16(lib.gba_read16(base + 0x12)) - self.MAP_OFFSET - py + radius
                if (ox, oy) != (radius, radius) and 0 <= ox < n and 0 <= oy < n:
                    grid[oy, ox] = True
        return grid

    def close(self):
        self.lib.gba_close()


def obstacle_view(grid, heading):
    """How strongly each eye sees obstacles, given the solid grid and heading.

    Returns (left, right) in 0..1, in the player's own frame: 'left' is the
    player's left whichever way it faces. Nearer obstacles count more, the way
    an approaching wall looms larger. A wall dead ahead is seen by both eyes.

      tiles considered, relative to the player facing 'ahead':
        ahead 1 (w 1.0, both eyes)     ahead 2 (w 0.5, both eyes)
        ahead-left/right 1 (w 0.7)     beside left/right (w 0.4)
    """
    r = grid.shape[0] // 2
    _, hx, hy, _ = HEADINGS[heading]
    lx, ly = hy, -hx                 # the player's left, in screen coordinates
    def solid(fwd, left):
        x = r + hx * fwd + lx * left
        y = r + hy * fwd + ly * left
        return float(grid[y, x]) if 0 <= x < grid.shape[1] and 0 <= y < grid.shape[0] else 1.0
    both = 1.0 * solid(1, 0) + 0.5 * solid(2, 0)
    left = both + 0.7 * solid(1, 1) + 0.4 * solid(0, 1)
    right = both + 0.7 * solid(1, -1) + 0.4 * solid(0, -1)
    top = 1.0 + 0.5 + 0.7 + 0.4
    return left / top, right / top


def target_view(mask, heading, reach=7):
    """How strongly each eye sees targets (exits) anywhere within `reach` tiles.

    Unlike obstacle_view, which only looks at the tiles touching the player,
    this is for things worth walking toward from across a room or a town --
    a door ten steps away should still pull. Each target tile adds
    1 / (1 + distance): straight ahead to both eyes, off to one side to that
    eye, and behind at half weight (a fly sees nearly all the way round).
    The total per eye is squashed into 0..1 with 1 - exp(-x).

    mask: square bool grid centred on the player (rows = y down, cols = x right).
    """
    r = mask.shape[0] // 2
    _, hx, hy, _ = HEADINGS[heading]
    lx, ly = hy, -hx                      # the player's left, in screen coordinates
    left = right = 0.0
    ys, xs = np.nonzero(mask)
    for y, x in zip(ys, xs):
        dx, dy = x - r, y - r
        if (dx, dy) == (0, 0):
            continue
        dist = abs(dx) + abs(dy)
        if dist > reach:
            continue
        fwd = dx * hx + dy * hy
        lat = dx * lx + dy * ly           # + = on the player's left
        w = 1.0 / (1.0 + dist)
        if fwd < 0:
            w *= 0.5
        if lat > 0:
            left += w
        elif lat < 0:
            right += w
        else:
            left += w
            right += w
    return 1.0 - np.exp(-left), 1.0 - np.exp(-right)


def ahead_view(mask, heading, reach=7):
    """How much of what's in `mask` lies straight ahead, 0..1.

    Ground truth only -- the brain never sees this. It plays the same part for
    exits that facing_solid() plays for walls: something to calibrate the
    head-on readout against and to check its sign. Each target counts
    1/(1+distance), scaled by how well it lines up with the way the fly faces
    (the cosine of the bearing), and nothing behind counts at all.
    """
    r = mask.shape[0] // 2
    _, hx, hy, _ = HEADINGS[heading]
    total = 0.0
    ys, xs = np.nonzero(mask)
    for y, x in zip(ys, xs):
        dx, dy = x - r, y - r
        if (dx, dy) == (0, 0):
            continue
        dist = abs(dx) + abs(dy)
        if dist > reach:
            continue
        fwd = dx * hx + dy * hy
        if fwd <= 0:
            continue
        total += (fwd / dist) / (1.0 + dist)
    return float(1.0 - np.exp(-total))


def _profile_shift(cur, prev, max_shift=6):
    """Best 1-D shift between two intensity profiles.

    Returns s such that cur[i] ~ prev[i + s]. Every candidate shift is scored
    at once over the central stretch both profiles share.
    """
    n = len(cur)
    windows = np.lib.stride_tricks.sliding_window_view(prev, n - 2 * max_shift)
    err = np.abs(windows - cur[max_shift:n - max_shift]).mean(axis=1)
    return int(np.argmin(err)) - max_shift


class Eyes:
    """Everything the fly senses from the game, turned into firing rates."""

    NAMES = ("eye", "forward", "backward", "slip_right", "slip_left", "ear", "touch",
             "obstacle_left", "obstacle_right", "people_left", "people_right",
             "exits_left", "exits_right", "pleasure", "pain")

    def __init__(self, cx):
        sel = lambda **kw: cx.select(**kw)
        self.eye = sel(**ports.SENSORY["eye"])
        self.ear = sel(**ports.SENSORY["ear"])
        self.touch = sel(**ports.SENSORY["touch"])
        prog = ["T4a", "T5a"]      # front-to-back
        regr = ["T4b", "T5b"]      # back-to-front
        self.forward = sel(cell_type=prog)
        self.backward = sel(cell_type=regr)
        # Sideways slip: front-to-back on one eye, back-to-front on the other.
        self.slip_right = np.concatenate([sel(cell_type=prog, side="left"),
                                          sel(cell_type=regr, side="right")])
        self.slip_left = np.concatenate([sel(cell_type=regr, side="left"),
                                         sel(cell_type=prog, side="right")])
        # Obstacle vision: LPLC1, the lobula plate/lobula columnar neurons flies
        # use for collision avoidance while walking. Driving one eye's LPLC1
        # moves the descending neurons strongly and reproducibly (validated on
        # fresh seeds: composite +35.6 Hz, t=+67.6, 8/8).
        name = np.where(cx.labels["cell_type"] != "", cx.labels["cell_type"],
                        cx.labels["hemibrain_type"])
        self.obstacle_left = np.flatnonzero((name == "LPLC1") & (cx.labels["side"] == "left"))
        self.obstacle_right = np.flatnonzero((name == "LPLC1") & (cx.labels["side"] == "right"))
        self.seen = (0.0, 0.0)
        self.grid = None
        # People -> LC10 (the channel flies use to track and pursue another
        # fly); exits -> LPLC4 (a looming/escape channel). Chosen because each
        # steers the descending neurons strongly left vs right on fresh seeds
        # (t=+55 both) with patterns unlike walls and unlike each other.
        side = cx.labels["side"]
        lc10 = np.char.startswith(name, "LC10")
        self.people_left = np.flatnonzero(lc10 & (side == "left"))
        self.people_right = np.flatnonzero(lc10 & (side == "right"))
        self.exits_left = np.flatnonzero((name == "LPLC4") & (side == "left"))
        self.exits_right = np.flatnonzero((name == "LPLC4") & (side == "right"))
        # Pleasure and pain: the reward (PAM) and punishment (PPL1) dopamine
        # clusters, driven in bursts by motivation.py.
        dan = cx.labels["class"] == "DAN"
        self.pleasure = np.flatnonzero(dan & np.char.startswith(name, "PAM"))
        self.pain = np.flatnonzero(dan & np.char.startswith(name, "PPL1"))
        self.scene = None
        self.seen_people = self.seen_exits = (0.0, 0.0)
        self.seen_exits_ahead = 0.0
        self.gain_people = self.gain_exits = 1.0     # set from the urges
        self.pleasure_hz = self.pain_hz = 0.0

        self.prev_gray = None
        self.prev_cols = self.prev_rows = None
        self.bright_slow = self.change_slow = None
        self.audio_slow = None
        self.touch_until = -1.0
        self.last_pos = None
        self.stuck_frames = 0

        # Latest readings, for the display.
        self.brightness = self.change = 0.0
        self.vx = self.vy = 0
        self.fwd = self.lat = 0.0
        self.sound = 0.0
        self.touching = False
        self.rates = {}
        self._sched = []

    def update(self, frame, audio_rms, pos, heading, walking, t_ms, grid=None):
        gray = frame.mean(axis=2, dtype=np.float32)
        cols, rows = gray.mean(axis=0), gray.mean(axis=1)

        if self.prev_gray is None:
            self.change, self.vx, self.vy = 0.0, 0, 0
        else:
            self.change = float(np.abs(gray - self.prev_gray).mean()) / 255.0
            # The camera follows the player. Walking right slides the scenery
            # left, so cur[i] = prev[i + 1] and the shift is +1: its sign is
            # already the player's direction. (Verified in-game: holding
            # right reads +1 px/frame.)
            self.vx = _profile_shift(cols, self.prev_cols)
            self.vy = _profile_shift(rows, self.prev_rows)
        self.prev_gray, self.prev_cols, self.prev_rows = gray, cols, rows
        self.brightness = float(gray.mean()) / 255.0
        # Photoreceptors ADAPT. A real one answers contrast against the light
        # level it has been sitting in, not the absolute level, which is why a
        # fly is not blinded walking from shade into sun. Ours did not, and it
        # cost more than everything else put together: 11,119 photoreceptors
        # held at a median 31 Hz is 345,065 spikes a second, measured as 100% of
        # the fly's entire afferent load, to carry one number about how bright
        # the room is. Adapted, a still scene falls to the resting rate and the
        # drive is spent on what actually changed.
        if self.bright_slow is None:
            self.bright_slow, self.change_slow = self.brightness, self.change
        self.bright_slow += ADAPT_TAU * (self.brightness - self.bright_slow)
        self.change_slow += ADAPT_TAU * (self.change - self.change_slow)

        _, hx, hy, _ = HEADINGS[heading]
        self.fwd = float(self.vx * hx + self.vy * hy)
        self.lat = float(self.vx * -hy + self.vy * hx)   # + is to the right

        if self.audio_slow is None:
            self.audio_slow = audio_rms
        self.audio_slow += 0.02 * (audio_rms - self.audio_slow)
        self.sound = max(0.0, audio_rms - self.audio_slow) / max(self.audio_slow, 200.0)

        # Proprioception: pushing a direction and going nowhere.
        if walking and pos is not None and pos == self.last_pos:
            self.stuck_frames += 1
        else:
            self.stuck_frames = 0
        self.last_pos = pos
        if self.stuck_frames >= 20:          # ~1/3 s: a turn-in-place is shorter
            self.touch_until = t_ms + 250.0
            self.stuck_frames = 0
        self.touching = t_ms < self.touch_until

        # What is around. The game's own map stands in for depth: a flat
        # top-down game gives none of the parallax or looming a fly uses to
        # judge obstacles, so pixels alone cannot tell a tree from grass. The
        # grid holds GBA.FREE / WALL / PERSON / EXIT per tile.
        self.scene = grid
        if grid is not None:
            # Walls and people matter where they touch the player: the central
            # 7x7. Exits are worth walking toward from across the room, so they
            # are looked for over the whole grid (see target_view).
            r = grid.shape[0] // 2
            near = grid[r - 3:r + 4, r - 3:r + 4]
            walls, people = near == GBA.WALL, near == GBA.PERSON
            self.grid = walls | people                  # solid, for bumps and facing checks
            self.seen = obstacle_view(walls, heading)
            # People are worth walking toward from a few tiles away, like exits
            # -- with the short-range view a person four tiles off was invisible
            # (0 lopsided frames in 45 s inside a house). Shorter reach than
            # exits: greeting someone is a local affair.
            self.seen_people = target_view(grid == GBA.PERSON, heading, reach=5)
            self.seen_exits = target_view(grid == GBA.EXIT, heading)
            self.seen_exits_ahead = ahead_view(grid == GBA.EXIT, heading)
        else:
            self.grid = None
            self.seen = self.seen_people = self.seen_exits = (0.0, 0.0)
            self.seen_exits_ahead = 0.0

        clip = lambda v: float(min(max(v, 0.0), 150.0))
        self.rates = {
            # Resting rate, plus what is NEW: a brightness step the eye has
            # not adapted to yet, and movement above the scene's own bustle.
            "eye": min(clip(EYE_REST
                            + 100.0 * abs(self.brightness - self.bright_slow)
                            + 250.0 * max(self.change - self.change_slow, 0.0)), EYE_CAP),
            "forward": clip(60.0 * max(self.fwd, 0.0)),
            "backward": clip(60.0 * max(-self.fwd, 0.0)),
            "slip_right": clip(60.0 * max(self.lat, 0.0)),
            "slip_left": clip(60.0 * max(-self.lat, 0.0)),
            "ear": clip(300.0 * self.sound),
            "touch": 150.0 if self.touching else 0.0,
            "obstacle_left": clip(60.0 * self.seen[0]),
            "obstacle_right": clip(60.0 * self.seen[1]),
            "people_left": clip(150.0 * self.seen_people[0] * self.gain_people),
            "people_right": clip(150.0 * self.seen_people[1] * self.gain_people),
            # 150 rather than 60: a single door several tiles off scores only
            # ~0.1-0.3, which at 60 Hz would be too faint to steer by.
            "exits_left": clip(150.0 * self.seen_exits[0] * self.gain_exits),
            "exits_right": clip(150.0 * self.seen_exits[1] * self.gain_exits),
            "pleasure": clip(self.pleasure_hz),
            "pain": clip(self.pain_hz),
        }
        if os.environ.get("FLYBRAIN_MIRROR", "") == "1":
            # The mirrored world: the left eye gets what the right eye would
            # have seen, and vice versa, for every lateral channel.
            r = self.rates
            for a, b in (("slip_left", "slip_right"), ("obstacle_left", "obstacle_right"),
                         ("people_left", "people_right"), ("exits_left", "exits_right")):
                r[a], r[b] = r[b], r[a]

    def schedule(self, brain, nsteps):
        """Draw the afferent spikes for the next nsteps steps in one go.

        Rates only change once per game frame, so there is no need to draw
        seven Poisson populations separately every millisecond; that cost more
        per fly-second than the emulator did. Refractoriness is still enforced
        by the step kernel on each forced spike.
        """
        rng, dt = brain.rng, brain.p.dt
        idx_parts, step_parts = [], []
        for name in self.NAMES:
            hz = self.rates.get(name, 0.0)
            if hz <= 0.0:
                continue
            pop = getattr(self, name)
            counts = rng.binomial(len(pop), min(hz * dt / 1000.0, 1.0), size=nsteps)
            total = int(counts.sum())
            if total:
                idx_parts.append(pop[rng.integers(0, len(pop), total)])
                step_parts.append(np.repeat(np.arange(nsteps), counts))
        if not idx_parts:
            self._sched = [None] * nsteps
            return
        idx = np.concatenate(idx_parts)
        steps = np.concatenate(step_parts)
        order = np.argsort(steps, kind="stable")
        idx, steps = idx[order], steps[order]
        bounds = np.searchsorted(steps, np.arange(nsteps + 1))
        self._sched = [idx[bounds[i]:bounds[i + 1]] for i in range(nsteps)]

    def apply(self, brain, i):
        if i < len(self._sched):
            f = self._sched[i]
            if f is not None and len(f):
                brain.force(f)


# Readout neurons: exactly the ten descending neurons validated for each
# channel (chosen on discovery seeds, confirmed on fresh ones), as FlyWire root
# IDs so they survive rebuilding the connectome. Sign +1 means the neuron fires
# more when the thing is on the fly's left. Source experiments:
#   WALL        data/experiments/obstacle_steer.log   (LPLC1 left vs right)
#   WALL_AHEAD  data/experiments/headon_obstacle.log  (LPLC1 both eyes vs none)
#   PEOPLE      data/experiments/channel_screen.log   (LC10 left vs right)
#   EXITS       data/experiments/channel_screen.log   (LPLC4 left vs right)
WALL_READOUT = (
    (720575940627645514, +1),   # DNp03 left
    (720575940620054694, +1),   # DNp11 left
    (720575940623276168, -1),   # DNp35 right
    (720575940612804862, +1),   # DNg01_a right
    (720575940618440000, -1),   # DNp18 left
    (720575940628437291, +1),   # DNp35 left
    (720575940627949589, +1),   # DNp18 right
    (720575940632903737, +1),   # DNp34 right
    (720575940622673860, +1),   # DNp06 left
    (720575940643347616, +1),   # DNp55 left
)
# A mirror-paired replacement was built and REJECTED: it reads the two sides
# even-handedly (fresh seeds: left +340, right -278, ratio -0.82, against this
# one's -0.17; in play +104 / -64 instead of +162 / +22) but in a pre-registered
# paired test over 5 seeds it did NOT even out the turning (|left share - 50%|
# +0.012, t=+0.21) and nearly doubled wall bumps per tile, 0.205 vs 0.108,
# because its weaker responses cross the threshold later and less often.
# data/experiments/wall_symmetry.log, turn_balance.jsonl. The turn bias has
# another cause: obstacles genuinely sit on the fly's left more often than its
# right (11.1% vs 7.1% of frames), and what is left of it survives a readout
# that answers both sides correctly.

WALL_AHEAD_READOUT = (
    (720575940634617952, +1),   # DNg57 left
    (720575940618574749, +1),   # DNg35 left
    (720575940629428215, +1),   # DNpe002 left
    (720575940630808591, +1),   # DNg52_a right
    (720575940622673860, +1),   # DNp06 left
    (720575940620642940, +1),   # DNpe002 right
    (720575940629461442, +1),   # DNb05 right
    (720575940608716809, +1),   # DNge133 right
    (720575940629805950, +1),   # DNg35 right
    (720575940644467360, +1),   # DNge133 left
)

PEOPLE_READOUT = (
    (720575940606222014, -1),   # DNge033 right
    (720575940644438551, -1),   # DNa01 right
    (720575940620380449, -1),   # DNae002 right
    (720575940624816115, +1),   # DNge033 left
    (720575940640325429, -1),   # DNa06 right
    (720575940616286175, +1),   # DNa13_a left
    (720575940629327659, +1),   # DNa02 left
    (720575940613408552, +1),   # DNa13_b left
    (720575940616604817, +1),   # DNpe023 left
    (720575940624986919, +1),   # DNa06 left
)

EXITS_READOUT = (
    (720575940629461442, -1),   # DNb05 right
    (720575940625182341, +1),   # DNge083 left
    (720575940632189900, +1),   # DNp31 left
    (720575940620621190, +1),   # DNg99 left
    (720575940619332944, -1),   # DNg99 right
    (720575940627732361, -1),   # DNp26 right
    (720575940622550089, -1),   # DNp07 right
    (720575940628132745, -1),   # DNp31 right
    (720575940627210568, +1),   # DNg79 left
    (720575940619432261, +1),   # DNp26 left
)


# Crosstalk between the three readouts, measured by driving each channel alone
# on one eye (6 seeds, data/experiments/channel_crosstalk.log). Rows are
# readouts (walls, people, exits); columns the channel driven (LPLC1, LC10,
# LPLC4); Hz of left-minus-right readout per full one-sided drive. The big
# leak is exits into walls: 133 Hz against the walls' own 363.
CROSSTALK = np.array([[362.9, 24.6, 133.3],
                      [-121.2, 1652.5, -135.8],
                      [-4.2, 70.4, 1390.4]])
# Unmixing: invert the mixing and re-express each channel in its own readout's
# Hz, so thresholds calibrate the same way as before. Well conditioned
# (condition number ~4.6). It removes leakage between these three channels only
# -- activity from motion, sound and touch reaching the same neurons is not in
# the table and stays in the signal.
UNMIX = np.diag(np.diag(CROSSTALK)) @ np.linalg.inv(CROSSTALK)


def _by_root(cx, table):
    if not hasattr(cx, "_root_index"):
        cx._root_index = {int(r): i for i, r in enumerate(cx.root_id)}
    idx = np.array([cx._root_index[r] for r, _ in table], dtype=int)
    return idx, np.array([sg for _, sg in table], dtype=np.float32)


# The approach lock -- hold the heading and keep walking while a door reads as
# straight ahead -- is OFF. It was measured twice, pre-registered, paired by
# seed, and failed both times: time to leave the starting room -3.5 s, t=-0.28
# (door_finding.jsonl), and seconds per room episode +10.2 s the WRONG way on
# the three pairs that survived the exclusion rule, with 21 room changes
# against 38 and 634 wall bumps against 350 (door_episodes.jsonl). It holds
# the fly's nose against walls beside doorways. The machinery below stays
# because the head-on readout it is built on is sound and independently
# validated -- it is the arbitration that is wrong -- but it does not drive
# the fly until something better is measured.
APPROACH_LOCK = False

# Head-on exits: descending neurons that respond to BOTH eyes' LPLC4 at the
# strength a door three tiles straight ahead produces. Chosen on discovery
# seeds 300-307, all ten held their direction on fresh seeds 400-407
# (composite +836 Hz, t=+66.9, 8/8). data/experiments/headon_exit.log
EXIT_AHEAD_READOUT = (
    (720575940628132745, +1),   # DNp31 right
    (720575940620038779, +1),   # DNp47 right
    (720575940617034565, +1),   # DNp07 left
    (720575940620621190, +1),   # DNg99 left
    (720575940628352850, +1),   # DNb05 left
    (720575940629461442, +1),   # DNb05 right
    (720575940619432261, +1),   # DNp26 left
    (720575940622550089, +1),   # DNp07 right
    (720575940604783136, +1),   # DNg56 left
    (720575940629421159, +1),   # DNp12 right
)


# Walking into something: descending neurons that respond to the bristle
# mechanosensors, which Eyes drives at 150 Hz whenever the fly holds a
# direction and its tile does not change. Screened like the others -- chosen on
# seeds 300-307, all ten held on fresh seeds 400-407, composite +3857 Hz,
# t=+939 (data/experiments/stall_stop.log). They saturate, which is what is
# wanted here: this is a yes/no 'I am stuck', not a graded quantity. DNp09, the
# stop neuron known from real flies, also responds (t=+4.33) but far more
# weakly, so it corroborates the pathway rather than carrying the signal.
STALL_READOUT = (
    (720575940618574749, +1),   # DNg35 left
    (720575940625870640, +1),   # DNg15 right
    (720575940644473760, +1),   # DNg84 left
    (720575940621628582, +1),   # DNg85 left
    (720575940629805950, +1),   # DNg35 right
    (720575940639152361, +1),   # DNg84 right
    (720575940626295396, +1),   # DNg85 right
    (720575940620169607, +1),   # DNg59 right
    (720575940641833869, +1),   # DNg73 right
    (720575940631982952, +1),   # DNge132 left
)


class Hands:
    """Reads descending neurons and decides which buttons are down."""

    def __init__(self, cx):
        sel = lambda **kw: cx.select(super_class="descending", **kw)
        self.turn_l = np.concatenate([sel(hemibrain_type="DNp15*", side="left"),
                                      sel(cell_type="DNg41", side="left")])
        self.turn_r = np.concatenate([sel(hemibrain_type="DNp15*", side="right"),
                                      sel(cell_type="DNg41", side="right")])
        self.dn = cx.select(**ports.MOTOR["descending"])
        self.mdn = sel(hemibrain_type="MDN*")
        # Obstacle side, from the two mirror pairs that tracked one-sided LPLC1
        # input most cleanly on validation seeds: an obstacle on the left raises
        # left DNp35 and right DNp18, and lowers their partners.
        name = np.where(cx.labels["cell_type"] != "", cx.labels["cell_type"],
                        cx.labels["hemibrain_type"])
        side = cx.labels["side"]; desc = cx.labels["super_class"] == "descending"
        pick = lambda t, s_: np.flatnonzero(desc & (name == t) & (side == s_))
        # Left-minus-right readouts (Hz; + = on the left). Walls used to be read
        # from the DNp35/DNp18 pairs alone; crosstalk showed that was too weak --
        # people and exits leaked 22-39% into it -- so all three now use their
        # full validated sets.
        # The SIDE of an obstacle, from the DNp35/DNp18 mirror pairs: left
        # DNp35 + right DNp18 rise for an obstacle on the left, their partners
        # for one on the right. Measured in play against the tile map
        # (data/experiments/mirror_sides.py, 4 seeds, 3000+ samples a side):
        # truly-left +22 to +25 Hz, truly-right -26 to -34 Hz, corr +0.57-0.62.
        # WALL_READOUT, used to NOTICE obstacles, gets that sign wrong (a right
        # obstacle reads +44 Hz); these get it right. Downstream descending
        # neurons, not LPLC1 -- reading LPLC1 would be reading back the input.
        self.mir_up = np.concatenate([pick("DNp35", "left"), pick("DNp18", "right")])
        self.mir_down = np.concatenate([pick("DNp35", "right"), pick("DNp18", "left")])
        self.mirror = 0.0
        self.turned_side = None      # the last turn applied to the game, for the caption
        self._mir_hist = np.zeros(1200, dtype=np.float32)
        self._mir_n = 0
        self.mir_mid = 0.0
        self.wall_idx, self.wall_sign = _by_root(cx, WALL_READOUT)
        self.people_idx, self.people_sign = _by_root(cx, PEOPLE_READOUT)
        self.exit_idx, self.exit_sign = _by_root(cx, EXITS_READOUT)
        self.people = self.exits = 0.0
        self.raw_obstacle = self.raw_people = self.raw_exits = 0.0
        self.unmix = True
        self.people_thresh = self.exit_thresh = 1e9   # set by Game.calibrate
        self.urge_leave = self.urge_talk = 0.0         # set by Game from Motivation
        # + = obstacle on the left, - = on the right, in Hz. A difference, not
        # a ratio: these neurons idle near 0-2 Hz in play, where a ratio like
        # (L-R)/(L+R+1) swings past any threshold on a single spike -- an
        # earlier version turned 29 times in 25 s, 86% of them with nothing
        # anywhere near the player. A real obstacle moves them by tens of Hz.
        self.obstacle = 0.0
        self.obstacle_thresh = 10.0  # Hz; set by Game.calibrate
        # A wall straight ahead looks the same to both eyes, so the left/right
        # readout above cannot pick a side (validated: it stays balanced,
        # +1.5 +/- 9.3 Hz). Head-on input has its own signal instead: these ten
        # descending neurons, chosen on discovery seeds and validated on fresh
        # ones (composite +30.2 Hz, t=+11.4, 8/8), mostly in left/right pairs.
        self.wall_ahead_idx, _ = _by_root(cx, WALL_AHEAD_READOUT)
        self.wall_ahead = 0.0
        self.wall_thresh = 1e9       # Hz; set by Game.calibrate
        # A door dead ahead has the same problem, and needs no new neurons to
        # solve: it drives BOTH exit channels, so the validated exit readout is
        # loud as a population while its left-minus-right difference stays
        # quiet. That pair of facts is 'a way out, straight in front'. Without
        # it the exit channel says nothing at all once the fly is lined up --
        # it could turn toward a door but never had a reason to walk into one.
        self.exit_ahead_idx, self.exit_ahead_sign = _by_root(cx, EXIT_AHEAD_READOUT)
        self.exit_ahead = 0.0
        self.exit_ahead_thresh = 1e9  # Hz; set by Game.calibrate, 1e9 = no lock
        # The left-minus-right exit readout is NOT balanced: driving both eyes
        # equally -- a door dead ahead -- moves it -280 Hz, reading as 'door on
        # the right' when nothing is on the right (headon_exit.log). Left
        # uncorrected that is a phantom sideways pull on every head-on door.
        # Calibration fits that offset against the head-on readout and read()
        # subtracts it, so what is left is the genuine left/right difference.
        self.exit_bias_a = self.exit_bias_b = 0.0
        # All three lateral readouts carry a constant offset that depends on
        # the map, so a warm-up on one map mis-centres them on another: in play
        # the obstacle signal sat at +137 Hz with the world dead symmetric, and
        # passed the negative threshold on 0.0% of 5294 frames -- the fly could
        # not turn left away from a wall at all. The width of the band still
        # comes from calibration against ground truth (so the fly does not turn
        # for nothing), but its centre tracks the brain's own recent history,
        # the same way the spontaneous-turn thresholds do.
        # Pressing into a wall. A bout used to end only when descending drive
        # fell away, so the fly held the key against a wall for as long as the
        # drive lasted -- giving the bout hysteresis doubled wall bumps per
        # tile moved. Now the brain's own answer to 'am I getting anywhere'
        # ends it.
        self.stall_idx, self.stall_sign = _by_root(cx, STALL_READOUT)
        self.stall = 0.0
        self.stall_thresh = 1e9      # Hz; set by Game.calibrate
        # Rolling record of "how loud was the readout" against "was the thing
        # really off to one side", kept so the thresholds can be re-set for the
        # room the fly is actually in. A threshold fixed at calibration is set
        # for the room calibration happened in: someone is clearly to one side
        # on ~33% of warm-up samples out in town, and on 1.5% of frames inside
        # a house, and at that rarity a threshold tuned for the town fires
        # almost only on noise (measured: 5% of "someone on the left" had
        # anyone in view, against 76% for doors).
        self._att_hist = {"people": np.zeros((2, RETUNE_SAMPLES), dtype=np.float32),
                          "exits": np.zeros((2, RETUNE_SAMPLES), dtype=np.float32)}
        self._att_n = 0
        self.retuned = {}
        self._lat_hist = np.zeros((3, 1200), dtype=np.float32)
        self._lat_n = 0
        self.lat_center = np.zeros(3)
        self.locked = False
        # Approach takes longer than one decision window, so a turn toward
        # something holds the heading for a while against the brain's own
        # spontaneous turning, which otherwise undoes it a second later.
        self.commit_until = -1e9
        self.commit_ms = 1500.0

        # Set by Game.calibrate(); these are only placeholders.
        # Spontaneous turns, from DNp15/DNg41 left minus right, in Hz. These
        # pairs steer cleanly under yaw motion, but in this game the player
        # almost never slips sideways: of 26 turns they caused in 25 s, 1 had
        # any sideways motion and 18 came while standing still. So in play they
        # act as the brain's own spontaneous turning, and only its rarest
        # swings (calibrated) are allowed to turn the fly.
        self.turn_left = 150.0       # Hz above this -> turn left
        self.turn_right = -150.0     # ...below this -> turn right
        # Those thresholds track the brain's own recent history (the extreme
        # 0.5% of the last ~20 s, per side) rather than a fixed warm-up value:
        # the warm-up walks the fly with heavy motion input, and thresholds set
        # there (+188 / -321 Hz) were out of reach in play, where the signal
        # never passed 122 Hz -- the fly stopped turning and stood still.
        self._turn_hist = np.zeros(1200, dtype=np.float32)
        self._turn_n = 0
        self.turn_mid = 0.0          # the signal's running median; set once 300 samples exist
        # Mood's hand on spontaneous turning (mood.py): below 1 turns come
        # more easily, above 1 they need a bigger swing. 1.0 leaves it alone.
        self.turn_scale = 1.0
        # Learned opinion of each kind of thing, -1 fears .. +1 likes, set by
        # mood.py from the mushroom body. Empty = no opinion = old behaviour.
        self.opinion = {}
        self.start_thresh = 1.0      # descending drive that starts a walking bout
        self.stop_thresh = 0.5       # ...and below which a bout ends
        self.min_bout_ms = 300.0     # about one tile, so a bout can build its own flow
        # ...and, symmetrically, how long it waits before trying again after
        # giving a bout up as fruitless. Without this the stall readout fires
        # and changes nothing: the bout ends on one frame and the very next
        # frame restarts it, because the drive that started it is still there.
        # Measured on the live stream: in a battle the stall signal is over
        # threshold on 100% of samples (median 3676 Hz against a threshold of
        # 86) while the fly is in a walking bout 99% of the time. A fly that
        # has just found it is getting nowhere does not resume 16 ms later.
        self.stall_rest_ms = 300.0
        # Which signal chooses the SIDE of an avoidance turn. "readout" is the
        # original: the obstacle readout's sign. "turn" uses the readout only
        # to decide that a turn is due and the brain's own turn drive for the
        # side, as wall-ahead already does. See the avoidance branch in decide.
        # "median" ADOPTED 2026-10-02 on a pre-registered test
        # (data/experiments/avoid_median.py, 6 paired seeds): left-turn share
        # 37.1% -> 44.7%, |share - 50%| 0.129 -> 0.058, t = -2.84; every guard
        # moved the right way (distinct tiles 56 -> 76, bumps unchanged).
        # "turn" (split at zero) was REJECTED first: the signal is not centred.
        self.avoid_side = os.environ.get("FLYBRAIN_AVOID_SIDE", "median")
        # Wall-ahead turns: "zero" splits the turn signal at 0, which it sits
        # below ~2/3 of the time, so 2/3 go right; "median" splits at its own
        # running median, as obstacle turns already do. A/B: wall_median.py.
        self.wall_side = os.environ.get("FLYBRAIN_WALL_SIDE", "zero")
        # The mirrored world (mirror_world_PREREG.md): the chosen turn is
        # applied to the game reversed; Eyes swaps every lateral channel. Stats
        # still count the turn the BRAIN chose. Experiments only.
        # NOT self.mirror: that name is the DNp35/DNp18 side readout, rewritten
        # every read(). Sharing it (2026-10-05..07) reversed nearly every turn
        # in live play, because the readout is almost never exactly zero.
        self.mirror_world = os.environ.get("FLYBRAIN_MIRROR", "") == "1"
        self.rest_until = -1e9       # no new bout before this
        self.a_thresh = 10.0
        self.a_surge = 1.8           # ...or drive this many times its own recent level
        self.b_ratio = 2.0           # only the pre-calibration fallback now
        self.b_margin = 1e9          # set by Game.calibrate; no B until then
        self.force_walk = False      # calibration only

        self.heading = 0
        self.bout = False
        self.bout_since = -1e9
        self.turn = self.walk = self.mdn_rate = 0.0
        self.mdn_base = None
        self.walk_base = None
        self.last_turn = self.last_a = self.last_b = -1e9
        # How fast the game is running against brain time (see Game.step and
        # MENU_SPEED). 1.0 means one game millisecond per brain millisecond.
        self.pace = 1.0
        self.a_until = self.b_until = -1.0
        self.keys = 0
        self.stats = {"turns": 0, "a": 0, "b": 0, "bouts": 0}
        self.sides = {}              # turns by the rule that chose them x side (log only)
        self._attr_src = None

    def read(self, brain):
        self.turn = float(brain.rate[self.turn_l].sum() - brain.rate[self.turn_r].sum())
        self._turn_hist[self._turn_n % len(self._turn_hist)] = self.turn
        self._turn_n += 1
        if self._turn_n >= 300 and self._turn_n % 30 == 0 and not self.force_walk:
            window = self._turn_hist[:min(self._turn_n, len(self._turn_hist))]
            self.turn_left = float(np.percentile(window, 99.5))
            self.turn_right = float(np.percentile(window, 0.5))
            # Where the signal actually sits. Measured in play
            # (data/experiments/turn_centre.py, 4 seeds): median -6 to -24 Hz,
            # above zero only 24-38% of the time, the negative tail 2-3x the
            # positive. So `turn > 0` answers "right" about two thirds of the
            # time. Spontaneous turns never noticed -- they fire only at the
            # extremes above -- but any SIDE CHOICE made by sign does.
            self.turn_mid = float(np.median(window))
        r = brain.rate
        raw = np.array([(r[self.wall_idx] * self.wall_sign).sum(),
                        (r[self.people_idx] * self.people_sign).sum(),
                        (r[self.exit_idx] * self.exit_sign).sum()])
        self.raw_obstacle, self.raw_people, self.raw_exits = (float(v) for v in raw)
        if self.unmix:
            raw = UNMIX @ raw
        self.obstacle, self.people, self.exits = (float(v) for v in raw)
        self.wall_ahead = float(brain.rate[self.wall_ahead_idx].mean())
        self.exit_ahead = float((r[self.exit_ahead_idx] * self.exit_ahead_sign).sum())
        self.stall = float((r[self.stall_idx] * self.stall_sign).sum())
        m = float(r[self.mir_up].sum() - r[self.mir_down].sum())
        if not self.force_walk:
            self._mir_hist[self._mir_n % len(self._mir_hist)] = m
            self._mir_n += 1
            if self._mir_n >= 300 and self._mir_n % 30 == 0:
                # Obstacles are in view ~18% of frames, so the median is
                # close to the no-obstacle baseline -- the centre to split at.
                self.mir_mid = float(np.median(
                    self._mir_hist[:min(self._mir_n, len(self._mir_hist))]))
        self.mirror = m - self.mir_mid
        self.exits -= self.exit_bias_a + self.exit_bias_b * self.exit_ahead
        if not self.force_walk:
            self._lat_hist[:, self._lat_n % self._lat_hist.shape[1]] = (
                self.obstacle, self.people, self.exits)
            self._lat_n += 1
            if self._lat_n >= 300 and self._lat_n % 30 == 0:
                w = self._lat_hist[:, :min(self._lat_n, self._lat_hist.shape[1])]
                self.lat_center = np.median(w, axis=1)
        self.obstacle -= self.lat_center[0]
        self.people -= self.lat_center[1]
        self.exits -= self.lat_center[2]
        self.walk = float(brain.rate[self.dn].mean())
        self.mdn_rate = float(brain.rate[self.mdn].mean())
        if self.mdn_base is None:
            self.mdn_base = self.mdn_rate
        self.mdn_base += 0.01 * (self.mdn_rate - self.mdn_base)
        if self.walk_base is None:
            self.walk_base = self.walk
        self.walk_base += 0.01 * (self.walk - self.walk_base)

    def observe_things(self, seen_people, seen_exits):
        """One frame of evidence for the thresholds: what was in view, and how
        loudly the readout said so. Called by the game once per frame."""
        i = self._att_n % RETUNE_SAMPLES
        for key, (l, r), sig in (("people", seen_people, self.people),
                                 ("exits", seen_exits, self.exits)):
            self._att_hist[key][0, i] = abs(sig)
            self._att_hist[key][1, i] = l - r
        self._att_n += 1
        if self._att_n % RETUNE_EVERY or self._att_n < RETUNE_MIN:
            return
        n = min(self._att_n, RETUNE_SAMPLES)
        # The most recent frames, newest first, for the base rate. The ring
        # buffer is not in time order, so they have to be picked out.
        recent = (self._att_n - 1 - np.arange(min(RETUNE_BASE, n))) % RETUNE_SAMPLES
        for key in ("people", "exits"):
            mag, side = self._att_hist[key][0, :n], self._att_hist[key][1, :n]
            clear = RETUNE_CLEAR[key]
            lop, sym = np.abs(side) >= clear, side == 0.0
            if lop.sum() < RETUNE_MIN or sym.sum() < RETUNE_MIN:
                continue
            cand = np.unique(np.percentile(mag, np.linspace(5, 99.5, 60)))
            tpr = np.array([(mag[lop] > t).mean() for t in cand])
            fpr = np.array([(mag[sym] > t).mean() for t in cand])
            # How common the thing is HERE AND NOW -- from the recent window,
            # while the shapes of the two distributions above still come from
            # the long one, which is what they need.
            here = np.abs(self._att_hist[key][1, recent]) >= clear
            base = float(here.mean())
            hit = base * tpr
            prec = hit / np.maximum(hit + (1.0 - base) * fpr, 1e-9)
            # Right more often than not, AND actually answering: a threshold
            # can be perfectly precise by almost never firing, which reads as
            # the fly ignoring the room (one re-setting here picked a
            # threshold catching 1% of the doors it could see).
            # No evidence here is not evidence for firing. With nothing
            # clearly to one side in the recent window the base rate is zero,
            # every candidate scores zero precision, and argmax over a flat
            # zero picks the FIRST -- the most permissive threshold there is.
            # Measured: the people threshold collapsed to 13 Hz and the fly
            # answered crosstalk twice as often, 76% correct down to 55%.
            # Keep what we have until the room shows us something.
            if not here.any():
                continue
            good = (prec >= 0.5) & (tpr >= 0.25)
            if good.any():
                pick = int(np.argmax(np.where(good, tpr - fpr, -np.inf)))
            elif (prec >= 0.5).any():
                pick = int(np.argmax(np.where(prec >= 0.5, tpr, -np.inf)))
            else:
                pick = int(np.argmax(prec))
            want = float(cand[pick])
            attr = "people_thresh" if key == "people" else "exit_thresh"
            now = getattr(self, attr)
            # Move a third of the way: the room can change again, and a
            # threshold that chases every corridor is its own kind of noise.
            new = float(now + 0.33 * (want - now))
            setattr(self, attr, max(new, 1.0))
            self.retuned[key] = (new, float(prec[pick]), float(tpr[pick]), base)

    def decide(self, t_ms):
        # A way out, straight ahead: the exit population loud while its
        # left-minus-right readout stays quiet. Both come from the same
        # validated neurons, and the fly holds its line while it lasts.
        self.locked = (self.exit_ahead > self.exit_ahead_thresh
                       and abs(self.exits) < self.exit_thresh)
        if t_ms - self.last_turn > 600.0:
            committed = t_ms < self.commit_until
            src = None
            if self.locked or committed:
                # An approach already under way. Nothing turns the fly except
                # an obstacle well past the usual threshold -- a door sits in a
                # wall, so the ordinary avoid signal is high exactly where the
                # fly needs to go, and letting it win here is what kept the
                # fly circling rooms it could see the door of.
                hard = 2.0 * self.obstacle_thresh
                step = 1 if self.obstacle > hard else -1 if self.obstacle < -hard else 0
                if step:
                    self.stats["avoid"] = self.stats.get("avoid", 0) + 1
                    src = "avoid"
                    self.commit_until = -1e9        # the approach is off
                elif self.locked:
                    self.stats["approach"] = self.stats.get("approach", 0) + 1
            # Obstacle avoidance next: turn AWAY from the side the obstacle
            # neurons report. That direction is a design choice following
            # LPLC1's known role; what is measured is only which side the
            # brain says the obstacle is on. (The older turn signal below
            # also responds to obstacles, but would turn toward them.)
            elif self.avoid_side == "mirror" and abs(self.obstacle) > self.obstacle_thresh:
                # NOTICE with the strong readout (keeps reaction timing -- the
                # wholesale mirror readout doubled bumps by noticing late);
                # choose the SIDE with the pairs that actually know it.
                # + = obstacle on the left -> turn right (step 1).
                step = 1 if self.mirror > 0 else -1
                self.stats["avoid"] = self.stats.get("avoid", 0) + 1
                src = "avoid"
            elif self.avoid_side == "median" and abs(self.obstacle) > self.obstacle_thresh:
                # As "turn" below, but split at the signal's own running
                # median rather than at zero, so the side choice is genuinely
                # even-handed instead of two-thirds "right".
                step = -1 if self.turn > self.turn_mid else 1
                self.stats["avoid"] = self.stats.get("avoid", 0) + 1
                src = "avoid"
            elif self.avoid_side == "turn" and abs(self.obstacle) > self.obstacle_thresh:
                # The readout says THAT something is near; it cannot say where.
                # Measured against the tile map's own left/right asymmetry
                # (data/experiments/obstacle_sides.py), a truly-RIGHT obstacle
                # reads POSITIVE (+44 Hz), not negative -- the readout rises for
                # an obstacle on either side, a little more for the left (+66).
                # Reading its sign as the side meant every avoidance turn went
                # right (turn_balance: 0L/35R, 3L/63R), which is the fly's whole
                # right bias. So, exactly as wall-ahead does below: the readout
                # decides to turn, the brain's own turn drive picks the side.
                step = -1 if self.turn > 0 else 1
                self.stats["avoid"] = self.stats.get("avoid", 0) + 1
                src = "avoid"
            elif self.avoid_side not in ("turn", "median", "mirror") and self.obstacle > self.obstacle_thresh:
                step = 1                       # obstacle left -> turn right
                self.stats["avoid"] = self.stats.get("avoid", 0) + 1
                src = "avoid"
            elif self.avoid_side not in ("turn", "median", "mirror") and self.obstacle < -self.obstacle_thresh:
                step = -1                      # obstacle right -> turn left
                self.stats["avoid"] = self.stats.get("avoid", 0) + 1
                src = "avoid"
            else:
                # Turn TOWARD what the fly currently wants more (design
                # choice); the readout says which side it is on. One call,
                # not two -- asking twice counted every turn toward a door
                # twice over in the stats.
                step = self._attraction()
                if step:
                    src = self._attr_src
                    self.commit_until = t_ms + self.commit_ms
                elif self.wall_ahead > self.wall_thresh:
                    # Wall ahead: turn, and let the brain's own left/right turn
                    # signal at this moment choose the side.
                    mid = self.turn_mid if self.wall_side == "median" else 0.0
                    step = -1 if self.turn > mid else 1
                    self.stats["wall"] = self.stats.get("wall", 0) + 1
                    src = "wall"
                    self.stats["wall_left" if step < 0 else "wall_right"] = \
                        self.stats.get("wall_left" if step < 0 else "wall_right", 0) + 1
                else:
                    # Spontaneous turn: left pair far ahead of right -> turn left.
                    step = (-1 if self.turn > self.turn_left * self.turn_scale
                            else 1 if self.turn < self.turn_right * self.turn_scale else 0)
                    src = "spontaneous"
            if step:
                applied = -step if self.mirror_world else step
                self.heading = (self.heading + applied) % 4
                # the way the character actually turned (+1 clockwise = right)
                self.turned_side = "right" if applied > 0 else "left"
                self.last_turn = t_ms
                self.stats["turns"] += 1
                side = "left" if step < 0 else "right"
                self.stats[side] = self.stats.get(side, 0) + 1
                # Log only (2026-10-08, Ian via obsfly): which rule chose the
                # turn and which side, in the fly's own frame (the side
                # DECIDED, as stats left/right). Sums to stats left/right.
                k = (src or "other") + "_" + side       # never raises
                self.sides[k] = self.sides.get(k, 0) + 1

        # A is a surge of descending drive. Two ways to get one: drive beyond
        # anything ordinary walking produces (pushing into an NPC, a frozen
        # battle screen), or drive jumping well above its own recent level.
        # The second is what works on screens with no walking at all -- a
        # title screen holds drive around 2 Hz, nowhere near the walking-
        # calibrated threshold, but it still surges as the screen changes.
        surge = self.walk > self.a_surge * self.walk_base + 1.0
        # These two gaps are "no more than one press a second", a hand-set
        # limit to stop button-mashing -- not a calibrated physiological
        # quantity, which is what a_thresh is. They are therefore held in GAME
        # time: dividing by the pace keeps the fly pressing at the same rate
        # in the world whatever the pacing dial is doing. Without this,
        # speeding the game up in battles silently made the fly press A three
        # times LESS often per game second, which cancelled most of the
        # speed-up -- menu-stuck barely moved (77.7% -> 72.1%) on the first
        # measurement for exactly this reason.
        if (self.walk > self.a_thresh or surge) and t_ms - self.last_a > 1000.0 / self.pace:
            self.a_until, self.last_a = t_ms + 60.0, t_ms
            self.stats["a"] += 1
        if (self.mdn_rate > self.mdn_base + self.b_margin
                and t_ms - self.last_b > 2000.0 / self.pace):
            self.b_until, self.last_b = t_ms + 60.0, t_ms
            self.stats["b"] += 1

        # Walking comes in bouts. Starting one takes a burst of descending
        # drive; once going, the forward flow it produces keeps the drive up,
        # so a bout only ends when that flow stops -- usually at a wall.
        if self.force_walk:
            self.bout = True
        elif self.locked:
            # Walking toward a door it can see: start a bout if there is none,
            # and do not end one on a dip in drive. Turning toward a door was
            # never the hard part -- staying pointed at it and still moving was.
            if not self.bout:
                self.bout, self.bout_since = True, t_ms
                self.stats["bouts"] += 1
        elif not self.bout:
            if self.walk > self.start_thresh and t_ms >= self.rest_until:
                self.bout, self.bout_since = True, t_ms
                self.stats["bouts"] += 1
        elif (t_ms - self.bout_since > self.min_bout_ms
              and (self.walk < self.stop_thresh or self.stall > self.stall_thresh)):
            # Either the drive ran out, or the fly is pushing against something
            # and getting nowhere. Both end the bout.
            if self.stall > self.stall_thresh and self.walk >= self.stop_thresh:
                self.stats["stalled"] = self.stats.get("stalled", 0) + 1
                # Give the stall its effect. Only this case rests: a bout that
                # simply ran out of drive has nothing to wait for.
                self.rest_until = t_ms + self.stall_rest_ms
            self.bout = False

        keys = 0
        if self.bout:
            keys |= HEADINGS[self.heading][0]
        if t_ms < self.a_until:
            keys |= A
        if t_ms < self.b_until:
            keys |= B
        self.keys = keys
        return keys

    # Below this a kind of thing is turned away from: about a third of the way
    # to a fully punished scent (opinion -0.5 = raw -0.19 at OPINION_SCALE
    # 0.35), so a thing merely present at a shock is not fled like the thing
    # the shocks were about.
    AVOID_OPINION = -0.5

    def _attraction(self):
        """-1 turn left / +1 turn right, toward or away from an exit or person, or 0.

        Toward by default. What the fly has LEARNED about each kind of thing
        (self.opinion, read from its mushroom body by mood.py) can flip that:
        a feared kind is turned away from when seen, and a strong opinion
        either way makes the fly react on a weaker signal. With no opinion
        (every experiment) this is exactly the old toward-only rule.
        """
        chans = [("exits", self.exits, self.exit_thresh, "exit"),
                 ("people", self.people, self.people_thresh, "person")]
        if self.urge_talk > self.urge_leave:
            chans = chans[::-1]
        if self.opinion:
            chans.sort(key=lambda c: -abs(self.opinion.get(c[0], 0.0)))
        for kind, signal, thresh, noun in chans:
            o = self.opinion.get(kind, 0.0)
            if abs(signal) > thresh * (1.0 - 0.5 * abs(o)):
                away = o <= self.AVOID_OPINION
                stat = ("away_from_" if away else "toward_") + noun
                self._attr_src = stat
                if not getattr(self, "_counting", False):
                    self.stats[stat] = self.stats.get(stat, 0) + 1
                toward = -1 if signal > 0 else 1        # + signal = on the left
                return -toward if away else toward
        return 0

    @property
    def walking(self):
        return bool(self.keys & (UP | DOWN | LEFT | RIGHT))


def cap_threads(n=2):
    """Two OpenMP threads measured best on this Pi with a browser open: 0.77x
    real time against 0.62x for four, because a four-thread team keeps getting
    preempted and then waits at every barrier. The setting is per thread, so
    call this from the thread that steps the brain."""
    lib = _ext.load_lib()
    if lib is not None and hasattr(lib, "fly_set_threads"):
        lib.fly_set_threads(n)


class Game:
    """Brain and emulator stepped together on simulated fly time."""

    def __init__(self, cx, rom, sav=None, state=None, params=None, seed=0, learn=True):
        self.cx = cx
        self.brain = Brain(cx, params or Params(), seed=seed)
        self.motivation = Motivation()
        # Optional: how the fly feels, and what that does (mood.py). None in
        # every experiment, so nothing measured changes because it exists.
        self.mood = None
        # Optional: smell (nose.py) -- things give off scents the mushroom body
        # can tell apart, which vision could not. None in every experiment.
        self.nose = None
        # A pleasure or pain button pressed in the window, waiting for the game
        # thread to deliver it at the right point in simulated time.
        self._stim = None
        # When False (the play window), the urges neither steer nor reward:
        # whoever holds the buttons is the fly's only teacher. True everywhere
        # else, so experiments keep the behaviour they were measured with.
        self.auto_rewards = True
        # Where the game's sound goes each frame, if anywhere (the stream).
        self.on_audio = None
        # The team and progress, refreshed about once a second by the game
        # thread so the window can show them without touching the emulator.
        self.team_now, self.progress_now = None, None
        self.team_calm, self.progress_calm = None, None   # only ever read in the overworld
        self._team_frames = 0
        # Someone else's hand on the controls for a moment (Twitch chat): a
        # button mask that replaces the fly's own choice until a deadline.
        self._takeover_req = None
        self._override_keys, self._override_until = 0, -1.0
        # !menu (twitch.ADMIN_IDS): while True, NOTHING but the streamer's taps
        # reaches the game -- not the fly, not chat. The fly keeps thinking.
        self.admin = False
        self._tap_req = None
        self._tap_keys, self._tap_until = 0, -1.0
        self._nudge_req = None
        self._nudge_keys, self._nudge_until = 0, -1.0
        self._nudge_gap_mask, self._nudge_gap_until = 0, -1.0
        self.sent_keys = 0
        # Who had a hand on the keys, frame by frame (see attrib.py), and a
        # hook the window sets to log progress with that credit attached.
        self.attrib = Attribution()
        self.on_progress = None
        # The tug of war: what the fly chose, what chat pressed, and how the
        # two have been getting on lately.
        self.fly_keys = self.chat_keys = 0
        self.pull, self.pull_until = None, -1.0
        self.pull_counts = {}
        # The mushroom body learning rule, fed by the pleasure and pain bursts.
        # Frozen during calibration; its learned state is saved with the game.
        self.learning = None
        if learn:
            from .learning import LEARNING_RAW, MushroomBodyLearning
            if os.path.exists(LEARNING_RAW):
                self.learning = MushroomBodyLearning(self.brain).attach()
                # Only delivered pleasure and pain teach (plus 0.5 s for the
                # dopamine to arrive); ordinary game visuals do not.
                mo_tail = 500.0
                self.learning.gate = lambda: (self.brain.t_ms < self.motivation.pleasure_until + mo_tail or
                                              self.brain.t_ms < self.motivation.pain_until + mo_tail)
        self.gba = GBA(rom, sav, state)
        self.eyes = Eyes(cx)
        self.hands = Hands(cx)
        self.frame = np.zeros((self.gba.h, self.gba.w, 3), dtype=np.uint8)
        self._next_frame_ms = 0.0
        # How much world goes past between the fly's thoughts (see GAME_SPEED).
        self.game_speed = GAME_SPEED
        self.menu_speed = MENU_SPEED
        self._frame_step = 0
        self.visited = set()
        self.maps = set()
        self.steps_moved = 0
        self._last_tile = None
        # Learned: the callback2 value seen whenever the player actually moves
        # a tile -- by definition the free-walking overworld.
        self._cb2_counts = {}
        self.overworld_cb2 = None
        self.wall_bumps = 0
        self._was_touching = False
        # When the fly stopped getting anywhere, and why (see _game_frame):
        # menu_since while it is in a menu or battle it cannot read,
        # still_since while it is in the overworld going nowhere.
        self.menu_since = self.still_since = None
        # What the game is running right now, published by the worker thread.
        self.cb2 = 0
        self.battle = False
        self.place = -1              # gMapHeader.regionMapSectionId, see PLACES
        self.places_been = set()     # every named area the fly has ever stood in
        self.furthest = None         # the last of those it reached for the FIRST time
        self._place_run = (None, 0)  # (id, consecutive frames), see note_place
        # Nothing is recorded until the REAL save is loaded. Calibration runs
        # on a different cartridge state and steps the game like play does, so
        # without this its map readings are recorded as places the run has
        # reached -- and because the set is written as soon as it changes, that
        # overwrote the seeded history with a single-element set. The marker
        # then had no memory of Route 3 or Pewter, so walking back into one
        # would have counted as NEW and moved the run's progress BACKWARDS,
        # which is the one thing this design exists to prevent.
        self._places_ready = False

    # The game's own callback pointer while a battle is running, read off the
    # live game and confirmed against the screen: 134287617 showed a wild
    # PIDGEY fight, while the neighbouring values were the party screen
    # (135392161), the bag (135298785) and a fade (135392209). It is a ROM
    # address, so it is specific to this cartridge -- hence in_battle() is only
    # ever used to make a message MORE accurate, never to decide anything, and
    # a wrong value costs nothing but a vaguer caption.
    BATTLE_CB2 = 134287617

    def in_battle(self):
        """Is a battle on screen? (The party and bag screens opened from
        inside one are their own callbacks and read as False here.)

        Reads a flag the WORKER publishes, not the emulator. The drawing
        thread and the /health handler both ask this, and /health is answered
        on a web server thread -- so reading the core directly would mean any
        HTTP request poking the emulator while the worker steps it.
        """
        return self.battle

    def in_overworld(self):
        return self.overworld_cb2 is not None and self.gba.callback2() == self.overworld_cb2

    def facing_solid(self):
        """Is the tile the player faces solid (wall, rock, person)?"""
        return self.facing_solid_at(self.hands.heading)

    def facing_solid_at(self, heading):
        grid = self.eyes.grid
        if grid is None:
            return False
        r = grid.shape[0] // 2
        _, hx, hy, _ = HEADINGS[heading]
        return bool(grid[r + hy, r + hx])

    def _motivate(self, pos):
        """Urges -> channel gains; game events -> pleasure and pain."""
        t, mo, e, h = self.brain.t_ms, self.motivation, self.eyes, self.hands
        if pos is not None and self.in_overworld() and self.auto_rewards:
            mo.on_map(t, pos[:2])
            if (h.keys & A) and e.scene is not None:
                r = e.scene.shape[0] // 2
                _, hx, hy, _ = HEADINGS[h.heading]
                if e.scene[r + hy, r + hx] == GBA.PERSON:
                    mo.on_talk(t, (pos[:2], pos[2] + hx, pos[3] + hy))
        if self.auto_rewards:
            leave, talk = mo.leave_urge(t), mo.talk_urge(t)
        else:
            leave = talk = 0.5            # fixed pull, the midpoint of the urge range
        h.urge_leave, h.urge_talk = leave, talk
        e.gain_exits = 0.25 + 0.75 * leave
        e.gain_people = 0.25 + 0.75 * talk
        stim, self._stim = self._stim, None
        if stim is not None:
            kind, source = stim if isinstance(stim, tuple) else (stim, "human")
            if self.learning is not None:
                self.learning.credit = source      # the ledger books this burst to it
            mo.manual(t, kind)
        e.pleasure_hz = 80.0 * max(mo.pleasure_level, 0.3) if mo.pleasure(t) else 0.0
        e.pain_hz = 80.0 if mo.pain(t) else 0.0
        if self.mood is not None:
            self.mood.update(self.brain, t, self.learning)
            self.mood.apply(self)

    TAKEOVER_KEYS = {"up": UP, "down": DOWN, "left": LEFT, "right": RIGHT, "a": A, "b": B}

    def takeover(self, key, ms=1000.0):
        """Press `key` ('up', 'down', 'left', 'right', 'a', 'b') for the fly
        for `ms` of game time. Safe to call from another thread; the game
        thread applies it on its next frame."""
        if key not in self.TAKEOVER_KEYS:
            raise ValueError(key)
        self._takeover_req = (key, ms)

    TAP_MS = 120.0          # long enough for the game to see, short enough
                            # that a direction never auto-repeats in a menu

    def tap(self, key):
        """One short press by the streamer during !menu. 'start' is allowed."""
        mask = START if key == "start" else self.TAKEOVER_KEYS[key]
        self._tap_req = mask

    def nudge(self, key, ms=300.0):
        """Press `key` ALONGSIDE the fly, rather than instead of it.

        This is what chat gets, which is any time nobody has bought control.
        Unlike takeover(), the fly keeps deciding and its own keys keep
        reaching the game the whole time: the two are OR'd together, so
        pressing against the fly is a tug of war and pressing with it helps.
        Working with the animal is the point; presses that replaced it would
        just switch the animal off."""
        if key not in self.TAKEOVER_KEYS:
            raise ValueError(key)
        self._nudge_req = (key, ms)

    @property
    def nudged(self):
        return self.brain.t_ms < self._nudge_until

    @property
    def taken_over(self):
        return self.brain.t_ms < self._override_until

    def stimulate(self, kind, source="human"):
        """Queue a hand-delivered 'pleasure' or 'pain' burst. Safe to call from
        the window's thread: the game thread picks it up on its next frame.
        source ('human', 'game', 'rule') is who the learning ledger credits."""
        if kind not in ("pleasure", "pain"):
            raise ValueError(kind)
        self._stim = (kind, source)

    def _game_frame(self):
        b = self.brain
        self.hands.read(b)
        # What the fly could see when it decided (the eyes are updated AFTER
        # deciding, for the new heading), kept for the thought audit.
        e, h = self.eyes, self.hands
        before = (e.seen, e.seen_people, e.seen_exits, h.heading, h.stats.get("turns", 0),
                  h.stats.get("stalled", 0), h.stats.get("approach", 0))
        keys = self.hands.decide(b.t_ms)
        if (h.stats.get("turns", 0), h.stats.get("stalled", 0),
                h.stats.get("approach", 0)) != before[4:]:
            self.decision_view = {"obstacle_lr": before[0], "people_lr": before[1],
                                  "exits_lr": before[2], "heading_before": before[3],
                                  "heading_after": h.heading, "facing_solid": self.facing_solid_at(before[3]),
                                  "t_ms": b.t_ms}
        req, self._takeover_req = self._takeover_req, None
        if req is not None:
            key, ms = req
            self._override_keys = self.TAKEOVER_KEYS[key]
            self._override_until = b.t_ms + ms
            # A direction also turns the fly that way, so when the controls
            # come back it is facing where it was steered.
            for i, (mask, _, _, _) in enumerate(HEADINGS):
                if mask == self._override_keys:
                    self.hands.heading = i
        req, self._nudge_req = self._nudge_req, None
        if req is not None:
            key, ms = req
            mask = self.TAKEOVER_KEYS[key]
            (self._nudge_keys, self._nudge_until, gap) = nudge_window(
                b.t_ms, self._nudge_keys, self._nudge_until, mask, ms, self.hands.pace)
            if gap is not None:
                self._nudge_gap_mask, self._nudge_gap_until = mask, gap
            # The character really does turn that way, so the fly's idea of
            # which way it faces has to follow the world, or its vision is
            # transformed the wrong way round for as long as it is out.
            for i, (mask, _, _, _) in enumerate(HEADINGS):
                if mask == self._nudge_keys:
                    self.hands.heading = i
        # Who is pulling which way, for the window to show. The whole point of
        # the stream is that a viewer's key is ADDED to a live animal's own
        # choice, and until now nothing said whether the two were pulling
        # together or against each other.
        self.fly_keys = keys
        self.chat_keys = self._nudge_keys if b.t_ms < self._nudge_until else 0
        if self.chat_keys:
            fly_dir = keys & (UP | DOWN | LEFT | RIGHT)
            chat_dir = self.chat_keys & (UP | DOWN | LEFT | RIGHT)
            if not fly_dir or not chat_dir:
                self.pull = "chat"            # the fly wanted nothing; chat moved it
            elif fly_dir == chat_dir:
                self.pull = "together"
            elif ((fly_dir | chat_dir) == (LEFT | RIGHT)
                  or (fly_dir | chat_dir) == (UP | DOWN)):
                self.pull = "against"         # dead opposite: a real tug of war
            else:
                self.pull = "apart"           # different axes, pulling crosswise
            self.pull_counts[self.pull] = self.pull_counts.get(self.pull, 0) + 1
            self.pull_until = b.t_ms + 1500.0
        elif b.t_ms > self.pull_until:
            self.pull = None
        keys = mix_keys(b.t_ms, keys, self._nudge_keys, self._nudge_until,
                        self._nudge_gap_mask, self._nudge_gap_until,
                        self._override_keys, self._override_until)
        self.sent_keys = keys                 # what the game gets this tick (/panel/live)
        self.attrib.frame(b.t_ms, "admin" if self.admin
                          else "driver" if b.t_ms < self._override_until
                          else "nudge" if b.t_ms < self._nudge_until else "fly")
        allow = 0
        if self.admin:
            req, self._tap_req = self._tap_req, None
            if req is not None:
                self._tap_keys, self._tap_until = req, b.t_ms + self.TAP_MS
            keys = self._tap_keys if b.t_ms < self._tap_until else 0
            allow = START
            self.chat_keys, self.pull = 0, None
        else:
            self._tap_req = None
        self.frame[:] = self.gba.frame(keys, allow) if allow else self.gba.frame(keys)
        self._team_frames += 1
        if self._team_frames % 60 == 1:
            self.team_now = self.gba.team()
            self.progress_now = self.gba.progress()
            # A second copy, taken ONLY in the overworld. During a battle the
            # party memory is shuffled and briefly holds the opponent's
            # Pokemon: read then, the fly announced catching a Charmeleon it
            # does not own, put it in the stream title, and wrote species it
            # could not even name ("#22502") into its diary. The panel keeps
            # the live reading, because watching HP fall during a fight is the
            # point of it; anything that makes a CLAIM uses this one.
            if self.in_overworld():
                self.team_calm, self.progress_calm = self.team_now, self.progress_now
        if self.on_audio is not None:
            self.on_audio(self.gba.audio_samples())
        pos = self.gba.player()
        self._motivate(pos)
        grid = self.gba.scene_grid(pos, radius=7) if pos is not None else None
        if self.nose is not None:
            if grid is not None and self.in_overworld():
                self.nose.sniff(grid, {"people": GBA.PERSON, "exits": GBA.EXIT})
            else:
                self.nose.level = {k: 0.0 for k in self.nose.level}
        self.eyes.update(self.frame, self.gba.audio_rms(), pos,
                         self.hands.heading, self.hands.walking, b.t_ms, grid)
        # Keep the attraction thresholds honest for wherever the fly now is.
        self.hands.observe_things(self.eyes.seen_people, self.eyes.seen_exits)
        # A wall bump, for measuring navigation: pushing without moving, while
        # walking the overworld (not in a battle), against a tile the collision
        # map says is solid. Talking to someone counts -- you can only start a
        # conversation by walking into them, and people are obstacles too.
        touching = self.eyes.touching
        if touching and not self._was_touching and self.in_overworld() and self.facing_solid():
            self.wall_bumps += 1
            self.attrib.bumped()
        self._was_touching = touching
        # How long the fly has been getting nowhere, and why. Out of the
        # overworld it is in a menu or a battle it cannot read; in the
        # overworld but not moving it is blocked or idling. Both look like a
        # frozen stream to somebody who just arrived.
        # Published once per frame, by this thread, for everyone else to read
        # (see in_battle). Also the only place the core is touched for it.
        self.cb2 = self.gba.callback2()
        self.battle = self.cb2 == self.BATTLE_CB2
        if not self.battle:
            # Only in the world: the header is not meaningful mid-battle.
            self.place = self.gba.map_section()
            self.note_place(self.place)
        if self.in_overworld():
            self.menu_since = None
            if self._last_tile is not None and pos is not None and pos != self._last_tile:
                self.still_since = None
            elif self.still_since is None:
                self.still_since = b.t_ms
        elif self.menu_since is None:
            self.menu_since = b.t_ms
        if pos is not None:
            if self._last_tile is not None and pos != self._last_tile:
                self.steps_moved += 1
                self.attrib.moved()
                cb2 = self.gba.callback2()
                self._cb2_counts[cb2] = self._cb2_counts.get(cb2, 0) + 1
                was = self.overworld_cb2
                self.overworld_cb2 = max(self._cb2_counts, key=self._cb2_counts.get)
                if self.overworld_cb2 != was:
                    self._remember_cb2(self.overworld_cb2)
            self._last_tile = pos
            self.visited.add(pos)
            self.maps.add(pos[:2])

    def step(self):
        """One brain step, plus a game frame whenever one is due."""
        b = self.brain
        if b.t_ms >= self._next_frame_ms:
            self._game_frame()
            # Full thinking time while navigating; hurry through what the fly
            # cannot navigate anyway (see MENU_SPEED).
            # Hurry only where we KNOW the fly is not navigating. Before the
            # overworld's own callback has been learned -- which includes the
            # whole of calibration -- "not in the overworld" is ignorance, not
            # a menu, and treating it as one ran calibration at menu speed.
            # That is not cosmetic: it moved a calibrated quantity. The
            # walking median fell 11.65 -> 6.70 Hz and the A threshold with it
            # (13.96 -> 13.18 Hz), because the fly was measured against a
            # world going past three times too fast.
            known = self.overworld_cb2 is not None
            # Never while the streamer is in the menu: their taps are timed
            # for a game running at its normal pace.
            hurry = known and not self.in_overworld() and not self.admin
            pace = self.game_speed * (self.menu_speed if hurry else 1.0)
            self.hands.pace = pace      # the fly presses at a steady GAME rate
            self._next_frame_ms += GBA_FRAME_MS / pace
            n = max(int(np.ceil((self._next_frame_ms - b.t_ms) / b.p.dt)), 1)
            self.eyes.schedule(b, n)
            self._frame_step = 0
        self.eyes.apply(b, self._frame_step)
        if self.nose is not None:
            self.nose.drive(b)
        self._frame_step += 1
        return b.step()

    def calibrate(self, idle_s=3.0, walk_s=6.0, verbose=True):
        """Set the output thresholds from the brain's own activity.

        Two regimes. First the fly just watches and listens (no buttons),
        which gives the resting distribution of descending drive. Then it is
        walked under a babbling policy -- a random heading every half second
        -- which gives the distribution with real forward flow, bumps and
        sound. Thresholds are percentiles of those, so they describe this
        brain on this game rather than being tuned to make the play look good.
        """
        b, h = self.brain, self.hands
        rng = np.random.default_rng(1)
        if self.learning is not None:
            self.learning.frozen = True          # warm-up is not experience
        h.turn_left, h.turn_right, h.a_thresh = 9.0, -9.0, 1e9   # none during warm-up
        h.stats["left"] = h.stats["right"] = 0
        h.sides = {}

        idle = []
        mdn = []          # MDN rate, for B's threshold (see below)
        h.force_walk, h.start_thresh = False, 1e9
        end = b.t_ms + idle_s * 1000.0
        ob, asym, turn, wall, blocked = [], [], [], [], []
        ex_ahead, ex_ahead_true = [], []
        stall, stall_true = [], []
        att = {"people": ([], []), "exits": ([], [])}
        while b.t_ms < end:
            self.step()
            if b.steps % 17 == 0:
                idle.append(h.walk)
                mdn.append(h.mdn_rate)
                turn.append(h.turn)
                att["people"][0].append(h.people)
                att["people"][1].append(self.eyes.seen_people[0] - self.eyes.seen_people[1])
                att["exits"][0].append(h.exits)
                att["exits"][1].append(self.eyes.seen_exits[0] - self.eyes.seen_exits[1])
                wall.append(h.wall_ahead)
                blocked.append(self.facing_solid())
                ex_ahead.append(h.exit_ahead)
                ex_ahead_true.append(self.eyes.seen_exits_ahead >= 0.3)
                stall.append(h.stall)
                stall_true.append(self.eyes.touching)
                ob.append(h.obstacle)
                asym.append(self.eyes.seen[0] - self.eyes.seen[1])

        walk = []
        h.force_walk = True
        heading, change = 0, 0.0
        end = b.t_ms + walk_s * 1000.0
        while b.t_ms < end:
            if b.t_ms >= change:
                heading, change = int(rng.integers(4)), b.t_ms + 500.0
            h.heading = heading
            self.step()
            if b.steps % 17 == 0:
                walk.append(h.walk)
                mdn.append(h.mdn_rate)
                turn.append(h.turn)
                att["people"][0].append(h.people)
                att["people"][1].append(self.eyes.seen_people[0] - self.eyes.seen_people[1])
                att["exits"][0].append(h.exits)
                att["exits"][1].append(self.eyes.seen_exits[0] - self.eyes.seen_exits[1])
                wall.append(h.wall_ahead)
                blocked.append(self.facing_solid())
                ex_ahead.append(h.exit_ahead)
                ex_ahead_true.append(self.eyes.seen_exits_ahead >= 0.3)
                stall.append(h.stall)
                stall_true.append(self.eyes.touching)
                ob.append(h.obstacle)
                asym.append(self.eyes.seen[0] - self.eyes.seen[1])
        h.force_walk, h.bout = False, False
        h._turn_n = 0                 # warm-up history is not play history
        h._lat_n = 0
        h.lat_center = np.zeros(3)

        idle, walk, turn = np.array(idle), np.array(walk), np.array(turn)
        self._cal_turn = turn.copy()        # kept for inspection, like _cal_ob
        # One threshold per side (the distribution is skewed; a single band
        # once put one side out of reach), sampled standing and walking, at the
        # extreme 0.5% tails: at 5% of walking frames an earlier version turned
        # more than once a second, almost never for any visible reason.
        h.turn_left = float(np.percentile(turn, 99.5))
        h.turn_right = float(np.percentile(turn, 0.5))
        # A resting brain occasionally flickers above its usual level; the top
        # tenth of those flickers is what starts a bout.
        h.start_thresh = max(float(np.percentile(idle, 90)), 0.05)
        # A bout persists while drive stays in the range walking produces --
        # but the old rule, min(p15 of walking, start_thresh), collapsed to
        # exactly start_thresh whenever walking drive sat above resting drive,
        # which it always does. Starting and stopping on ONE value gives no
        # hysteresis: the bout chattered on and off as the drive jittered
        # across it, 144 bouts in 90 s, and the fly stood still 66% of the
        # time (heading_code.jsonl). A bout now ends only when drive falls
        # back to an ordinarily resting level, which by construction is below
        # the flicker that starts one.
        h.stop_thresh = min(float(np.percentile(walk, 15)), float(np.percentile(idle, 50)))
        # A is arousal beyond ordinary walking. Pressing into a wall, an NPC,
        # or a frozen battle screen all push descending drive above that, so
        # the same rule talks, advances text and fights -- with no special
        # case for any of them.
        h.a_thresh = max(float(np.percentile(walk, 90)), h.start_thresh + 0.5)
        h.walk_p90 = float(np.percentile(walk, 90))
        # B was the ONLY motor threshold never calibrated: a bare
        # `mdn_rate > 2.0 * mdn_base + 3.0`, with 2.0 and 3.0 chosen by hand
        # and never checked against anything. Measured on the live stream it
        # fired at 71.8% of its own refractory ceiling -- the fly backing out
        # of menus almost as often as it was allowed to, undoing what chat had
        # just set up. Everything else here comes from a percentile of real
        # data, so this does too.
        #
        # The adaptive baseline is KEPT -- these rates drift, and a fixed
        # threshold on a drifting signal is the mistake that made the lateral
        # readouts unusable. What is calibrated is the MARGIN above it: how far
        # beyond its own ordinary level this population has to go, taken as the
        # same top-tenth rule A uses.
        mdn = np.array(mdn) if len(mdn) else np.array([0.0])
        h.b_margin = max(float(np.percentile(mdn, 90) - np.median(mdn)), 1.0)
        # Obstacle threshold, sampled standing still and walking: halfway
        # between the readout's noise when both eyes see the same (usually
        # nothing) and its typical response when the view is lopsided, and
        # never below the noise's 99th percentile.
        ob, asym = np.array(ob), np.array(asym)
        # Kept for inspection: the obstacle readout against the TRUE left/right
        # asymmetry of the view. Diagnostics read these rather than re-sampling.
        self._cal_ob, self._cal_asym = ob.copy(), asym.copy()
        even, lop = np.abs(asym) < 0.05, np.abs(asym) >= 0.1
        if even.sum() >= 5:
            ob = ob - np.median(ob[even])      # width, measured about the centre
        if lop.sum() >= 5 and even.sum() >= 5:
            noise = np.abs(ob[even])
            h.obstacle_thresh = float(max(0.5 * np.percentile(noise, 95)
                                          + 0.5 * np.median(np.abs(ob[lop])),
                                          np.percentile(noise, 99)))
            self.obstacle_sign_check = float(np.corrcoef(ob[lop], asym[lop])[0, 1])
        else:
            self.obstacle_sign_check = float("nan")
        # Wall-ahead threshold: halfway between the readout with the way ahead
        # clear (95th percentile) and blocked (median), never below the clear
        # 99th percentile; with a live sign check.
        wall, blocked = np.array(wall), np.array(blocked, dtype=bool)
        if blocked.sum() >= 5 and (~blocked).sum() >= 5:
            clear = wall[~blocked]
            h.wall_thresh = float(max(0.5 * np.percentile(clear, 95) + 0.5 * np.median(wall[blocked]),
                                      np.percentile(clear, 99)))
            self.wall_check = (float(np.median(wall[blocked])), float(np.median(clear)))
        else:
            self.wall_check = (float("nan"), float("nan"))
        # Exit-ahead threshold: the population rate of the exit readout with a
        # door lined up ahead versus without. Same recipe as the wall above.
        # If the warm-up map never showed the fly a door ahead there is nothing
        # to calibrate against, and the lock stays off (1e9) rather than firing
        # on a guessed number.
        ex_ahead = np.array(ex_ahead)
        ex_true = np.array(ex_ahead_true, dtype=bool)
        if ex_true.sum() >= 5 and (~ex_true).sum() >= 5:
            away = ex_ahead[~ex_true]
            # Where to put the line. The wall and obstacle thresholds sit above
            # the 99th percentile of the quiet distribution, because crossing
            # them TURNS the fly and a false turn is expensive. This one only
            # makes the fly hold the heading it already has, and that tail rule
            # put it above both medians -- the lock armed on 1.6% of frames and
            # its A/B was null. So: the operating point that best separates a
            # door ahead from no door ahead (max true-positive minus
            # false-positive rate) on the warm-up's own ground truth.
            cand = np.unique(np.percentile(ex_ahead, np.arange(1, 100)))
            tpr = np.array([(ex_ahead[ex_true] > c).mean() for c in cand])
            fpr = np.array([(away > c).mean() for c in cand])
            j = tpr - fpr
            k = int(np.argmax(j))
            self.exit_ahead_check = (float(np.median(ex_ahead[ex_true])), float(np.median(away)),
                                     float(tpr[k]), float(fpr[k]))
                # A lock that cannot tell the two apart is worse than none -- and
            # for now neither can one that can (see APPROACH_LOCK).
            if j[k] >= 0.2 and APPROACH_LOCK:
                h.exit_ahead_thresh = float(cand[k])
            else:
                h.exit_ahead_thresh = 1e9
        else:
            h.exit_ahead_thresh = 1e9
            self.exit_ahead_check = (float("nan"), float("nan"), float("nan"), float("nan"))
        # Stall threshold: the readout while pushing into something versus while
        # not. The warm-up walks a random heading every half second, so it runs
        # into plenty of walls. With no stalling seen there is nothing to
        # calibrate and the bout keeps its old ending rule.
        stall = np.array(stall)
        st_true = np.array(stall_true, dtype=bool)
        if st_true.sum() >= 5 and (~st_true).sum() >= 5:
            clear = stall[~st_true]
            # The operating point that best separates stuck from moving, not
            # the 99th percentile of the quiet side. Crossing this ENDS a bout,
            # and the two errors are not equal: ending one early costs a step,
            # failing to end one means holding the key against a wall, which is
            # the whole problem. The same rule the approach lock uses.
            cand = np.unique(np.percentile(stall, np.arange(1, 100)))
            tpr = np.array([(stall[st_true] > c).mean() for c in cand])
            fpr = np.array([(clear > c).mean() for c in cand])
            k = int(np.argmax(tpr - fpr))
            h.stall_thresh = float(cand[k])
            # Kept so an experiment can rebuild the stricter rule from the same
            # warm-up samples, and the two differ only in where the line falls.
            self._stall_clear_p95 = float(np.percentile(clear, 95))
            self._stall_clear_p99 = float(np.percentile(clear, 99))
            self.stall_check = (float(np.median(stall[st_true])), float(np.median(clear)),
                                float(tpr[k]), float(fpr[k]))
        else:
            h.stall_thresh = 1e9
            self.stall_check = (float("nan"),) * 4
            self._stall_clear_p95 = self._stall_clear_p99 = float("inf")
        # Fit the head-on offset out of the lateral exit readout, then set the
        # toward-exit threshold from the corrected signal. Frames where the
        # view is symmetric carry no real left/right information, so whatever
        # the readout does there as a function of the head-on signal is the
        # offset, not a door to one side.
        ex_raw = np.array(att["exits"][0])
        ex_asym = np.array(att["exits"][1])
        sym = np.abs(ex_asym) < 0.05
        if sym.sum() >= 10 and ex_ahead[sym].std() > 1e-6:
            b_, a_ = np.polyfit(ex_ahead[sym], ex_raw[sym], 1)
            h.exit_bias_b, h.exit_bias_a = float(b_), float(a_)
            corrected = ex_raw - (h.exit_bias_a + h.exit_bias_b * ex_ahead)
            self.exit_bias_check = (float(np.median(np.abs(ex_raw[sym]))),
                                    float(np.median(np.abs(corrected[sym]))))
            att["exits"] = (list(corrected), att["exits"][1])
        else:
            h.exit_bias_a = h.exit_bias_b = 0.0
            self.exit_bias_check = (float("nan"), float("nan"))
        # Toward-person and toward-exit thresholds, the same way as walls: above
        # the readout's noise when nothing lopsided is in view, halfway to its
        # typical response when something is. If the warm-up saw too few
        # lopsided views, fall back to 1.5x the noise's 99th percentile.
        # Toward-person and toward-exit thresholds, chosen by how well they
        # SEPARATE "a thing off to one side" from "nothing lopsided in view"
        # -- the Youden rule already used for the stall readout.
        #
        # The old rule took whichever was larger of "halfway to the typical
        # response" and "the 99th percentile of the noise", and the noise
        # floor won every time. Measured in play afterwards (thing_reading):
        # people fired on 14.5% of frames with someone off to one side, doors
        # on 5.2%, and over 7,168 frames the fly announced a person ten times
        # and a door five. It was not misreading them -- the readout picks the
        # right side 97% of the time -- it simply never reached its own
        # threshold, so people and doors played almost no part in what it did.
        # At the separation-optimal threshold the same data gives 77% of
        # lopsided views acted on, 3-15% false, side still 96-97% right.
        self.attraction_check = {}
        for key in ("people", "exits"):
            sig, asy = np.array(att[key][0]), np.array(att[key][1])
            sym = np.abs(asy) < 0.05
            if sym.sum() >= 5:
                sig = sig - np.median(sig[sym])
            noise = np.abs(sig[sym]) if sym.sum() >= 5 else np.abs(sig)
            lop = np.abs(asy) >= 0.1
            if lop.sum() >= 5 and sym.sum() >= 5:
                hits, false_alarms = np.abs(sig[lop]), np.abs(sig[sym])
                cand = np.unique(np.percentile(np.abs(sig), np.linspace(1, 99.5, 80)))
                tpr = np.array([(hits > t).mean() for t in cand])
                fpr = np.array([(false_alarms > t).mean() for t in cand])
                j = tpr - fpr
                # Separation alone is not enough, because it ignores how RARE
                # the thing is. Someone is clearly off to one side on about 1.5%
                # of frames and a door on 15%, so at the separation-optimal
                # threshold nearly every "someone on the left" was a false
                # alarm (measured: 5% of person decisions had anyone in view at
                # all, against 76% for doors). So: of the thresholds that would
                # be right more often than not, take the one that separates
                # best; if none manages that, take the most precise on offer.
                base = float(lop.mean())
                hit_rate = base * tpr
                precision = hit_rate / np.maximum(hit_rate + (1.0 - base) * fpr, 1e-9)
                good = precision >= 0.5
                if good.any():
                    pick = int(np.argmax(np.where(good, j, -np.inf)))
                else:
                    pick = int(np.argmax(precision))
                best = float(cand[pick])
                # If the readout cannot separate the two cases at all, keep the
                # cautious old threshold rather than firing on noise.
                thr = best if j.max() >= 0.15 else max(
                    0.5 * np.percentile(noise, 95) + 0.5 * np.median(np.abs(sig[lop])),
                    np.percentile(noise, 99))
                self.attraction_check[key] = (int(lop.sum()),
                                              float(np.corrcoef(sig[lop], asy[lop])[0, 1]),
                                              float(j[pick]), float(precision[pick]))
            else:
                thr = 1.5 * np.percentile(noise, 99)
                self.attraction_check[key] = (int(lop.sum()), float("nan"),
                                              float("nan"), float("nan"))
            setattr(h, "people_thresh" if key == "people" else "exit_thresh", float(max(thr, 1.0)))
        if self.learning is not None:
            self.learning.frozen = False
        self.motivation = Motivation()
        h.stats = {"turns": 0, "a": 0, "b": 0, "bouts": 0}
        h.sides = {}
        self.visited.clear(); self.maps.clear(); self.steps_moved = 0
        self.wall_bumps = 0
        if verbose:
            for key, thr in (("people", h.people_thresh), ("exits", h.exit_thresh)):
                n_lop, corr, sep, prec = self.attraction_check[key]
                print(f"  toward {key} beyond +/-{thr:.0f} Hz ({n_lop} lopsided views; "
                      f"readout vs view correlation {corr:+.2f}, expect positive; "
                      f"separation {sep:+.2f}, right {100*prec:.0f}% of the times it fires)")
            lock = ("off (measured twice, no benefit -- see APPROACH_LOCK)"
                    if not APPROACH_LOCK else
                    "off (warm-up could not tell a door ahead from none)"
                    if h.exit_ahead_thresh > 1e8
                    else f"above {h.exit_ahead_thresh:.1f} Hz (readout median: door ahead "
                         f"{self.exit_ahead_check[0]:.1f} Hz vs not {self.exit_ahead_check[1]:.1f} Hz; "
                         f"catches {100*self.exit_ahead_check[2]:.0f}% of doors ahead, "
                         f"fires on {100*self.exit_ahead_check[3]:.0f}% of the rest)")
            stall_txt = ("off (warm-up never pushed into anything)" if h.stall_thresh > 1e8
                         else f"above {h.stall_thresh:.0f} Hz (stuck {self.stall_check[0]:.0f} Hz "
                              f"vs moving {self.stall_check[1]:.0f} Hz; catches "
                              f"{100*self.stall_check[2]:.0f}% of stalls, fires on "
                              f"{100*self.stall_check[3]:.0f}% of the rest)")
            print(f"  bout ends on stall {stall_txt}")
            print(f"  approach lock {lock}")
            before, after = self.exit_bias_check
            print(f"  head-on offset removed from the exit readout: |signal| on "
                  f"symmetric views {before:.1f} -> {after:.1f} Hz")
            print(f"  wall-ahead turn above {h.wall_thresh:.1f} Hz (readout median: way blocked "
                  f"{self.wall_check[0]:.1f} Hz vs clear {self.wall_check[1]:.1f} Hz, expect blocked > clear)")
            print(f"  obstacle turn beyond +/-{h.obstacle_thresh:.1f} Hz "
                  f"({int(lop.sum())} lopsided views seen; readout vs view "
                  f"correlation {self.obstacle_sign_check:+.2f}, expect positive)")
            print(f"calibrated ({idle_s:.0f}s watching, {walk_s:.0f}s walking): "
                  f"rest drive median {np.median(idle):.2f} Hz, walking median "
                  f"{np.median(walk):.2f} Hz")
            print(f"  start a bout above {h.start_thresh:.2f} Hz, end below "
                  f"{h.stop_thresh:.2f} Hz, A above {h.a_thresh:.2f} Hz, "
                  f"B above baseline+{h.b_margin:.2f} Hz, "
                  f"spontaneous turn left above {h.turn_left:+.0f} Hz, right below {h.turn_right:+.0f} Hz")

    # --- resuming across sessions ----------------------------------------------
    AUTOSAVE_MS = 30000.0         # of game time; switching off loses at most this

    def enable_resume(self, path):
        """Snapshot to `path` every AUTOSAVE_MS of game time (see save()).

        Also seeds the overworld callback from last run, so a restart that
        lands inside a battle is not stuck at unknown pacing until it escapes.
        """
        self._load_places()
        self._places_ready = True              # the real save is in: start tracking
        if self.overworld_cb2 is None:
            seed = self._recall_cb2()
            if seed is not None:
                self.overworld_cb2 = seed
                print(f"overworld callback recalled: {seed}", flush=True)
        self.resume_path = path
        self._next_autosave = self.brain.t_ms + self.AUTOSAVE_MS
        self.last_saved_ms = None

    def save(self):
        """Snapshot now, if resuming is enabled. Returns True if written."""
        path = getattr(self, "resume_path", None)
        if not path:
            return False
        self.gba.save_state(path)
        if self.learning is not None:
            # What it has learned travels with the game. Same atomic pattern.
            tmp, final = path + ".mb.tmp.npz", path + ".mb.npz"
            self.learning.save(tmp)
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
            os.replace(tmp, final)
        self.last_saved_ms = self.brain.t_ms
        return True

    PLACES_MEMO = os.path.expanduser("~/.config/flybrain/places.json")
    # How many consecutive frames a NEW place must hold before it counts. The
    # visited set is permanent, so a bad write is permanent: one misread during
    # a warp or a screen transition would advance the run's progress marker to
    # somewhere the fly has never stood, for ever. ~1 s at 60 game frames.
    NEW_PLACE_FRAMES = 60
    # Real map sections that must never COUNT as reaching a new area:
    #   99, 100  the Route 4 and Route 10 Pokemon Centers -- buildings, and
    #            advancing the marker to one would read as a place, not progress
    # Anything below 88 is Hoenn, inherited from Emerald, and should never
    # appear in a Kanto game at all.
    NOT_PROGRESS = frozenset({99, 100})

    def note_place(self, pid):
        """Record where the fly is, and move the progress marker only when it
        stands somewhere it has never stood before.

        Furthest-reached, not current location: the fly wanders, so a live
        location flaps between neighbours several times an hour and regularly
        shows REGRESSION -- it read Route 3 while the run was at Mt. Moon.
        A progress marker should only ever go forward.
        """
        from .twitch import PLACES             # local: keeps the import one-way
        if not self._places_ready:
            return                             # still calibrating: not this run
        if pid < 88 or pid in self.NOT_PROGRESS or pid not in PLACES:
            self._place_run = (None, 0)        # unnameable: cannot be progress
            return
        last, n = self._place_run
        n = n + 1 if pid == last else 1
        self._place_run = (pid, n)
        if n < self.NEW_PLACE_FRAMES or pid in self.places_been:
            return
        self.places_been.add(pid)
        self.furthest = pid
        print(f"first time at {PLACES[pid]} (id {pid})", flush=True)
        hook = getattr(self, "on_progress", None)
        if hook is not None:
            try:
                hook("area", PLACES[pid], self.attrib.credit())
            except Exception:
                pass                           # a log line is not worth a crash
        try:
            self._save_places()
        except OSError:
            pass                               # the title is not worth a crash

    def _save_places(self):
        os.makedirs(os.path.dirname(self.PLACES_MEMO) or ".", exist_ok=True)
        tmp = self.PLACES_MEMO + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"been": sorted(self.places_been), "furthest": self.furthest}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.PLACES_MEMO)

    def _load_places(self):
        """Pick the run's progress back up.

        If this is lost the marker silently regresses to wherever the fly
        wanders next, which is the exact failure the design exists to prevent,
        so a missing file is seeded from the route already played rather than
        left empty.
        """
        try:
            with open(self.PLACES_MEMO) as f:
                d = json.load(f) or {}
            been = {int(x) for x in d.get("been", []) if isinstance(x, int)}
            # An explicit list is the truth, EMPTY INCLUDED: that is what a new
            # run looks like (flybrain/reset.py writes it). Only a missing or
            # unreadable file falls through to the run-1 seed below.
            if been or isinstance(d.get("been"), list):
                self.places_been = been
                f_ = d.get("furthest")
                self.furthest = int(f_) if isinstance(f_, int) else None
                return
        except (OSError, ValueError, TypeError):
            pass
        # Seed: the route this run has already played, per Ian, 2026-09-27.
        # Pallet, Viridian, Route 1, Route 2, Viridian Forest, Pewter, Route 3,
        # Mt. Moon, and the Route 4 Pokemon Center at Mt. Moon's east exit.
        # The marker starts at Mt. Moon because that is where he says the run
        # is; it only affects which place the FIRST advance moves on from.
        self.places_been = {88, 89, 101, 102, 126, 90, 103, 127, 104}
        self.furthest = 127

    CB2_MEMO = os.path.expanduser("~/.config/flybrain/rom.json")

    def _remember_cb2(self, value):
        """Keep the overworld's callback across restarts.

        It is a ROM address, so it is the same every run -- but it is only
        LEARNED once the fly walks a tile, and a restart that resumes inside a
        battle may not get to walk one for a long time. Until then the game
        cannot tell "in a menu" from "no idea", so MENU_SPEED stays off
        exactly when it would help most. Seeding it from last time fixes that;
        the first real tile move recomputes it from counts either way, so a
        stale or wrong value costs at most one step of ordinary pacing.
        """
        try:
            os.makedirs(os.path.dirname(self.CB2_MEMO) or ".", exist_ok=True)
            tmp = self.CB2_MEMO + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"overworld_cb2": int(value)}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.CB2_MEMO)
        except (OSError, ValueError, TypeError):
            pass          # a hint is not worth failing a frame for

    def _recall_cb2(self):
        try:
            with open(self.CB2_MEMO) as f:
                v = (json.load(f) or {}).get("overworld_cb2")
            return int(v) if isinstance(v, int) else None
        except (OSError, ValueError, TypeError):
            return None

    def load_learning(self, path):
        """Restore learned synapse strengths saved next to a snapshot, if any.
        A damaged file falls back to the daily backup, then to starting
        naive -- never to a crash loop."""
        if self.learning is None:
            return False
        name = os.path.basename(self.resume_root(path)) + ".mb.npz"
        for mb in (self.resume_root(path) + ".mb.npz", os.path.join(GBA.BACKUP_DIR, name)):
            if not os.path.exists(mb):
                continue
            try:
                self.learning.load(mb)
                return True
            except Exception as exc:
                print(f"learned synapses {mb} would not load: {type(exc).__name__}: {exc}",
                      flush=True)
        return False

    @staticmethod
    def resume_root(path):
        """The snapshot a fallback (.prev / backup copy) stands in for."""
        return path[:-5] if path.endswith(".prev") else path

    def maybe_autosave(self):
        if getattr(self, "resume_path", None) and self.brain.t_ms >= self._next_autosave:
            self._next_autosave = self.brain.t_ms + self.AUTOSAVE_MS
            try:
                self.save()
            except OSError as exc:
                print(f"autosave failed: {exc}", flush=True)

    def load(self, rom, sav=None, state=None):
        """Swap in a different cartridge (or the same one from scratch),
        keeping the brain and its calibrated thresholds.

        Used to calibrate on a save where the player can walk, then play a
        brand-new game whose first minute and a half is intro text.
        """
        self.gba.close()
        self.gba = GBA(rom, sav, state)
        # The team and progress on record belong to the save being left. Kept,
        # they are compared against the new save's and every party member reads
        # as freshly caught -- which is what the fly's own diary caught this
        # doing on every restart, and what chat was being told.
        self.team_now, self.progress_now = None, None
        self.team_calm, self.progress_calm = None, None
        self._team_frames = 0
        self.eyes = Eyes(self.cx)
        h = self.hands
        self.motivation = Motivation()
        h.heading, h.bout, h.keys = 0, False, 0
        h._turn_n = 0
        h.mdn_base = h.walk_base = None
        h.stats = {"turns": 0, "a": 0, "b": 0, "bouts": 0, "left": 0, "right": 0}
        h.sides = {}
        self.frame[:] = 0
        self.visited.clear(); self.maps.clear()
        self.steps_moved, self._last_tile = 0, None
        self.wall_bumps, self._was_touching = 0, False
        self.menu_since = self.still_since = None
        self._next_frame_ms = self.brain.t_ms
        self._frame_step = 0

    def close(self):
        self.gba.close()


# The games the fly can play, each with its OWN battery save (and so its own
# resume snapshot and learned memories): switching games never touches another
# game's progress. All are FireRed-family, US 1.0 memory layout -- the RAM the
# eyes, nose and progress readouts use. Calibration always runs on
# ultraviolet.sav/.ss1 (default_paths), which only needs a place to walk.
GAMES = {
    "ultraviolet": ("ultraviolet.gba", "newgame.sav"),   # Ultra Violet 1.22, a FireRed hack
    "firered": ("firered.gba", "firered.sav"),          # FireRed (U) 1.0, BPRE rev 0
}
CURRENT_GAME = os.path.join(PLAY_DIR, "current_game")


def current_game():
    """Which game `play --continue` means: play/current_game, else ultraviolet."""
    try:
        name = open(CURRENT_GAME).read().strip()
        return name if name in GAMES else "ultraviolet"
    except OSError:
        return "ultraviolet"


def set_current_game(name):
    if name not in GAMES:
        raise ValueError(name)
    with open(CURRENT_GAME, "w") as f:
        f.write(name + "\n")


def milestones(before, now, team_before, team_now):
    """The things worth announcing between two snapshots of the save.

    Compared by name rather than by slot, because the party gets reordered,
    and a level is the best one seen for that name, so a Pokemon fainting,
    healing or being swapped about does not read as news.
    """
    out = []
    if not now:
        return out
    b, t0, t1 = before or {}, team_before or [], team_now or []
    if b:
        if now["badges"] > b.get("badges", 0):
            out.append(("badge", f"EARNED A BADGE!  ({now['badges']} in total)"))

    def best(team):
        lv = {}
        for m in team:
            # A name like "#22502" means the species did not resolve: the party
            # was read mid-write or mid-battle and the bytes are garbage. Such
            # a read is not evidence of anything, and treating it as one put
            # "CAUGHT #22502!" in the fly's diary.
            if not m["egg"] and not str(m["name"]).startswith("#"):
                lv[m["name"]] = max(lv.get(m["name"], 0), m["level"])
        return lv
    had, has = best(t0), best(t1)
    # Team news needs a team to compare WITH. Allowing `b` alone here meant
    # that any snapshot taken before the save was loaded -- during calibration
    # the party reads empty -- made the whole party look freshly caught on the
    # next comparison, which the diary caught it doing on every restart.
    if t0:
        gone = [n for n in had if n not in has]
        for name in has:
            if name not in had:
                # One vanished as this one appeared, and the party is the same
                # size: that is an evolution, not a catch. The starter turning
                # into Wartortle was being announced as having been caught.
                if gone and len(has) == len(had):
                    out.append(("evolve", f"{gone.pop(0)} EVOLVED INTO {name}!"))
                else:
                    out.append(("caught", f"CAUGHT {name}!"))
            elif has[name] > had[name]:
                out.append(("level", f"{name} grew to Lv {has[name]}"))
        if t1 and all(m["hp"] == 0 for m in t1 if not m["egg"]) \
                and not (t0 and all(m["hp"] == 0 for m in t0 if not m["egg"])):
            out.append(("faint", "the whole team fainted"))
    if b and not t0 and not t1 and now["dex_owned"] > b.get("dex_owned", 0):
        out.append(("caught", f"NEW POKEDEX ENTRY ({now['dex_owned']} caught)"))
    return out


def new_game_paths(fresh=True, game=None):
    """The sandboxed ROM with its own separate battery save and no state.

    fresh deletes that save and its resume snapshot first, so the cartridge
    boots as if never played. It never touches ultraviolet.sav, which holds
    the copied real progress, nor any other game's save.
    """
    rom_name, sav_name = GAMES[game or current_game()]
    rom = os.path.join(PLAY_DIR, rom_name)
    sav = os.path.join(PLAY_DIR, sav_name)
    if fresh:
        res = resume_path_for(sav)
        for p in (sav, res, res + ".mb.npz"):       # the learned memories go too
            if os.path.exists(p):
                os.remove(p)
    return rom, sav, None


def resume_path_for(sav):
    """Where a game's resume snapshot lives: next to its battery save."""
    return os.path.splitext(sav)[0] + ".resume.ss"


def default_paths():
    rom = os.path.join(PLAY_DIR, "ultraviolet.gba")
    sav = os.path.join(PLAY_DIR, "ultraviolet.sav")
    st = os.path.join(PLAY_DIR, "ultraviolet.ss1")
    return rom, (sav if os.path.exists(sav) else None), (st if os.path.exists(st) else None)
