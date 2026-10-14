"""The stream's front door: always up, so OBS never loses its source.

The game serves its window and sound on an internal port (--serve 8766). This
relay holds the public one OBS uses (8765) and forwards both. When the game is
restarting -- new code, a crash, the Pi's own screen being closed -- the relay
keeps answering: a "back in a moment" card instead of a dead picture, silence
instead of a dropped audio connection, and the moment the game is back it
carries on. OBS never has to be touched.

  http://<pi>:8765/             the page for an OBS Browser Source (800 x 450)
  http://<pi>:8765/stream.mjpg  the picture
  http://<pi>:8765/audio.wav    the game's sound, 48 kHz stereo, paced to real
                                time -- for an OBS Media Source
  http://<pi>:8765/health       game/audio status, for checking

WHY THE SOUND IS RESAMPLED, AND WHY SLOWLY. The game runs slower than real
time (the brain sets the pace), so it makes sound more slowly than a listener
plays it; played as-is that is constant gaps. So the relay plays it back at
the rate the game is producing it -- slow-motion music. But that rate must be
STEADY: pitch follows playback rate, and ears hear a wobbling rate as warble.
Measured live, the game's speed jumps 0.43-0.65x second to second (sd 0.055)
yet holds 0.53-0.59x over five seconds. A first version followed each second
and nudged +/-10% to hold a 0.25 s buffer, and on stream it warbled. Now:
  - the rate follows only the slow average (time constant RATE_TAU_S),
  - a larger cushion (TARGET_BUF_S) soaks up the second-to-second jitter,
  - trims to hold that cushion are capped at +/-TRIM and may change by at
    most SLEW per second -- a drift far below what an ear notices,
  - and the picture is delayed by the same cushion, so sound and picture
    stay in sync without anyone touching OBS.

POST /test-cheer is forwarded to the game, from the Pi itself only.
"""
import io
import json
import os
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

RATE = 48000
CHUNK = 960                    # 20 ms of output
TARGET_BUF_S = 2.0             # sound kept in hand to absorb the game's jitter.
                               # Every second of cushion is a second between a
                               # viewer typing and seeing it happen, and the
                               # picture is delayed to match, so this is the
                               # stream's own latency. It was 0.8 while the game
                               # ran at 0.5-0.7x and jittered. At 0.83x it was
                               # tried at 0.5 and the buffer dipped to 0.17 s,
                               # which is where gaps come from.
                               #
                               # Raised 0.7 -> 1.0 on measurement, not taste.
                               # At 0.7 the cushion could only be held by the
                               # emergency trim below, and the peer session
                               # timed the resulting pitch slides at 4.84 %/s
                               # worst case -- against 0.4 %/s known inaudible
                               # and 12 %/s already called horrible in this
                               # project. A bigger cushion absorbs the game's
                               # ~20% production swing inside the gentle 3%
                               # trim, so the emergency band stops firing.
                               # Costs ~0.3 s of delay, which nobody perceives,
                               # instead of paying in audible pitch AND
                               # sub-0.1 s dips at the same time.
MAX_BUF_S = 3.5                # beyond this, skip ahead (rare: a long speed-up).
                               # A ceiling on how far behind the stream can
                               # drift. It was 1.5 s, which turned out to be a
                               # trap: with the target at 0.7 s, every time the
                               # game ran ahead the cushion was chopped back to
                               # target, so it lived at the BOTTOM of its range
                               # and the next slow patch emptied it. The game
                               # delivers between 0.45 s and 0.94 s of sound
                               # per second (measured at its own /audio.raw),
                               # so the cushion has to be free to ride up and
                               # absorb that. A skip is one small
                               # discontinuity; the picture follows it exactly,
                               # because frames are released against the sound
                               # position and skipped samples count as played.
VIDEO_LEAD_S = 0.0             # only used for frames with no X-Audio-Pos stamp
                               # (an older game, or the placeholder). Stamped
                               # frames are matched exactly, so no correction
                               # is needed. A constant was tried first and
                               # failed: measured -420 ms, corrected, then read
                               # +160 and +240 ms on repeat runs -- the cushion
                               # swings by more than a second, so no fixed
                               # number can hold. Kept at 0 rather than a
                               # figure that was never right twice. Originally,
                               # not derived: pulling /stream.mjpg and /audio.wav
                               # together and cross-correlating picture changes
                               # against sound onsets put the best alignment at
                               # -420 ms, i.e. the sound of a step arrived before
                               # the step was visible. Subtracted from the delay
                               # below; re-measure with scratch/avsync.py after
                               # touching anything in the audio path.
RATE_TAU_S = 20.0              # how slowly the playback rate follows the game.
                               # Was 20 s, and that was the real fault: the
                               # game's production rate swings ~20%, and a 20%
                               # deficit drains a 0.7 s cushion in 3.5 s and a
                               # 1.0 s cushion in 5 s -- long before a 20 s
                               # follower notices. No cushion size fixes that
                               # (raising it to 1.0 s bought 0.35 s of delay
                               # and left the floor at 0.02 s and the worst
                               # pitch slide unchanged at 4.8 %/s). Following
                               # the game's OWN rate quickly is feed-forward
                               # and is not what warbled before: that was a
                               # fast loop chasing the BUFFER by +/-10% every
                               # second. The buffer trim below stays gentle.
TRIM = 0.03                    # nudge while the cushion is comfortable (inaudible)
TRIM_MAX = 0.05                # nudge allowed when it is not, to avoid a gap
SLEW = 0.002                   # most the rate may change, per second, normally
SLEW_FAST = 0.008              # ...and when the cushion is in danger. Pitch
                               # moves at roughly SLEW/ratio per second, so at
                               # 0.03 and a 0.68x rate this was 4.4 %/s, which
                               # Ian heard as "a bit wobbly" and which matched
                               # the peer session's 4.84 %/s measurement. 0.008
                               # is ~1.2 %/s: three times the level that went
                               # unnoticed, a quarter of the level that was
                               # heard. Affordable now that running dry costs
                               # REPRIME_S of silence instead of two seconds.
SAFE = 0.5                     # |error| within this counts as comfortable
REPRIME_S = 0.25               # sound in hand needed to resume after a dry patch
# Whether a listener on the sound makes the picture wait for it. True is exact
# sync, bought with ~2 s of video delay (the cushion). False serves the sound
# but releases every frame the moment it arrives, so the picture runs AHEAD of
# the sound by the cushion -- for a stream whose sound is music, where nobody
# can see a 2 s offset but everybody notices 2 s of extra latency. Read from a
# file every couple of seconds so it can be flipped on the live stream
# without restarting the relay (which drops OBS's video source).
# 2026-10-03: obsfly's request, for Ian to judge the game's own soundtrack.
BOOT = f"{os.getpid()}-{time.time():.0f}"   # changes on every restart; see /boot
MATCH_F = os.path.expanduser("~/.config/flybrain/relay.json")
_match = {"at": 0.0, "on": True}


_pi = {"at": 0.0, "line": "pi: unknown"}


def pi_line(now=None):
    """Power and heat, for the open /health (obsfly's panel; the under-voltage
    history and the SSD-corruption risk it carries are the project's top
    unresolved hardware risk). Cached 10 s: vcgencmd is a subprocess.
    get_throttled bits: 0 under-voltage now, 16 under-voltage since boot,
    2 throttled now, 18 throttled since boot, 3/19 soft temperature limit."""
    import re
    import subprocess
    now = time.monotonic() if now is None else now
    if now - _pi["at"] < 10.0:
        return _pi["line"]
    _pi["at"] = now
    parts = []
    try:
        t = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True,
                           timeout=3).stdout
        v = int(t.strip().split("=")[1], 16)
        parts += [f"throttled={hex(v)}", f"under_volt_now={v & 1}",
                  f"under_volt_ever={(v >> 16) & 1}", f"throttled_now={(v >> 2) & 1}",
                  f"throttled_ever={(v >> 18) & 1}"]
    except Exception:
        parts.append("throttled=?")
    try:
        e = subprocess.run(["vcgencmd", "pmic_read_adc", "EXT5V_V"], capture_output=True,
                           text=True, timeout=3).stdout
        parts.append("ext5v=" + re.search(r"=([\d.]+)V", e).group(1)[:4])
    except Exception:
        parts.append("ext5v=?")
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            parts.append(f"temp={int(f.read()) / 1000:.1f}")
    except (OSError, ValueError):
        parts.append("temp=?")
    _pi["line"] = "pi: " + " ".join(parts)
    return _pi["line"]


def match_audio(now=None):
    now = time.monotonic() if now is None else now
    if now - _match["at"] > 2.0:
        _match["at"] = now
        try:
            import json
            with open(MATCH_F) as f:
                _match["on"] = bool(json.load(f).get("match_audio", True))
        except (OSError, ValueError, AttributeError):
            _match["on"] = True          # missing or damaged: the old behaviour
    return _match["on"]
def _game_log(msg):
    """Viewers and upstream reconnects of /game.mjpg, to play/relay.log, so a
    frozen OBS Game layer can be traced afterwards (2026-10-13)."""
    try:
        with open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "play", "relay.log"), "a") as f:
            f.write(f"{time.strftime('%F %T')} game.mjpg: {msg}\n")
    except OSError:
        pass


GAME_PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>flybrain game</title>
<style>html,body{margin:0;background:#000;overflow:hidden;width:100%;height:100%}
canvas{display:block;width:100%;height:100%;object-fit:contain;image-rendering:pixelated}</style></head>
<body><canvas id="c" width="240" height="160"></canvas>
<script>
// Reads /game.mjpg itself (2026-10-09): an <img> on a multipart stream froze on
// its last frame when the stream ended, with no error to react to. Here every
// PNG part is drawn to the canvas, and 2 s without a new frame means abort
// and reconnect -- whatever the cause.
const c = document.getElementById('c'), x = c.getContext('2d');
let last = Date.now(), ctrl = null;
const enc = new TextEncoder(), CL = enc.encode('Content-Length:'), EOH = enc.encode('\\r\\n\\r\\n');
function find(a, pat, from) {
  outer: for (let i = from; i <= a.length - pat.length; i++) {
    for (let k = 0; k < pat.length; k++) if (a[i + k] !== pat[k]) continue outer;
    return i;
  }
  return -1;
}
async function draw(bytes) {
  try {
    const bmp = await createImageBitmap(new Blob([bytes], {type: 'image/png'}));
    if (c.width !== bmp.width || c.height !== bmp.height) { c.width = bmp.width; c.height = bmp.height; }
    x.drawImage(bmp, 0, 0);
    last = Date.now();
  } catch (e) {}
}
async function run() {
  ctrl = new AbortController();
  try {
    const r = await fetch('game.mjpg?t=' + Date.now(), {signal: ctrl.signal, cache: 'no-store'});
    const rd = r.body.getReader();
    let buf = new Uint8Array(0);
    for (;;) {
      const {value, done} = await rd.read();
      if (done) break;
      const nb = new Uint8Array(buf.length + value.length); nb.set(buf); nb.set(value, buf.length); buf = nb;
      for (;;) {
        const h = find(buf, CL, 0); if (h < 0) break;
        const e = find(buf, EOH, h); if (e < 0) break;
        const len = parseInt(new TextDecoder().decode(buf.slice(h + CL.length, e)).trim(), 10);
        const start = e + 4;
        if (!(len > 0) || buf.length < start + len) break;
        draw(buf.slice(start, start + len));
        buf = buf.slice(start + len);
      }
    }
  } catch (e) {}
  setTimeout(run, 500);
}
setInterval(() => { if (Date.now() - last > 2000 && ctrl) { ctrl.abort(); last = Date.now(); } }, 500);
run();
</script></body></html>"""

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>flybrain</title>
<style>html,body{margin:0;background:#000;overflow:hidden}
img{display:block;width:800px;height:450px}</style></head>
<body><img id="v" src="stream.mjpg" alt="flybrain">
<script>
// If the picture ever drops, reconnect by itself -- no OBS refresh needed.
const v = document.getElementById('v');
const again = () => { v.src = 'stream.mjpg?t=' + Date.now(); };
v.onerror = () => setTimeout(again, 1000);
// onerror is NOT enough: when an MJPEG stream simply ends (the relay
// restarting), Chromium -- which OBS's browser source is -- keeps the last
// frame on screen and fires nothing. OBS then streams a frozen picture with
// perfect stats. So ask the relay who it is every few seconds; if it has
// restarted, or was unreachable and is back, reconnect the picture.
let boot = null, lost = false;
setInterval(() => {
  fetch('boot?t=' + Date.now(), {cache: 'no-store'}).then(r => r.text()).then(b => {
    if ((boot !== null && b !== boot) || lost) again();
    boot = b; lost = false;
  }).catch(() => { lost = true; });
}, 3000);
</script></body></html>"""


def placeholder_jpeg(text="the fly is waking up — back in a moment"):
    from PIL import Image, ImageDraw, ImageFont
    img = Image.new("RGB", (800, 500), (14, 16, 22))
    d = ImageDraw.Draw(img)

    def font(size):
        for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
            if os.path.exists(p):
                return ImageFont.truetype(p, size)
        return ImageFont.load_default()
    big, small = font(34), font(18)
    for txt, f, y, col in (("flybrain", big, 200, (240, 240, 240)),
                           (text, small, 258, (255, 200, 90))):
        w = d.textlength(txt, font=f)
        d.text(((800 - w) / 2, y), txt, font=f, fill=col)
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=85)
    return buf.getvalue()


class Video:
    def __init__(self, upstream, delay=lambda: 0.0, audio_pos=lambda: None,
                 audio_listeners=lambda: 0):
        self.upstream = upstream
        self.audio_pos = audio_pos      # which sample the sound is on, or None
        self.audio_listeners = audio_listeners
        self.held_ms = 0.0              # how long the last frame was held back
        self.jpeg = placeholder_jpeg()
        self.seq = 0
        self.live = False
        self.cond = threading.Condition()
        self._placeholder = self.jpeg
        # Frames wait here until they are as old as the sound's cushion.
        self.delay = delay
        self._pending = deque()
        self.sync_err_ms = None       # picture vs sound at the moment of release
        self._plock = threading.Lock()
        threading.Thread(target=self._release, daemon=True).start()

    def _release(self):
        """Let each frame go when the sound it belongs to is being sent.

        The frame carries the game's own audio position (X-Audio-Pos) and the
        relay knows which sample it is playing, so the two are matched exactly
        rather than estimated. Estimating was the bug: the cushion swings by
        more than a second, so any fixed correction was wrong within a minute.
        The old timing rule is kept for frames that arrive without a stamp --
        an older game, or the placeholder.
        """
        while True:
            now = time.monotonic()
            out = None
            with self._plock:
                # With nobody listening to the sound there is nothing to line
                # the picture up WITH, and every millisecond the frame waits is
                # latency spent for no one. Straight out it goes. The stamps
                # and the matching below are untouched and take over again the
                # moment a listener attaches -- the picture then pauses once,
                # for as long as the cushion, while the sound catches up to it.
                if self.audio_listeners() == 0 or not match_audio(now):
                    ready = list(self._pending)
                    self._pending.clear()
                    if ready:
                        self.held_ms = max(0.0, (now - ready[-1][0]) * 1000.0)
                else:
                    ready = None
            if ready is not None:
                # EVERY frame that has arrived, not just the newest. Keeping
                # only the last one silently threw frames away whenever this
                # thread was slow to wake -- which it is, with the brain's
                # threads spinning on three of the four cores -- and the
                # delivered rate fell to about two thirds without anything
                # looking wrong from inside.
                for _, jpeg, _ in ready:
                    self._publish(jpeg)
                time.sleep(0.002)
                continue
            with self._plock:
                playing = self.audio_pos()
                d = max(0.0, self.delay() - VIDEO_LEAD_S)
                while self._pending:
                    when, jpeg, apos = self._pending[0]
                    if apos is not None and playing is not None:
                        if apos > playing:
                            break
                    elif now - when < d:
                        break
                    out = self._pending.popleft()[1]
                    self.held_ms = max(0.0, (now - when) * 1000.0)
                    if apos is not None and playing is not None:
                        # How far off the match was, in ms of sound. Exact sync
                        # keeps this inside a frame; a large value means the
                        # stamps stopped arriving or a clock jumped.
                        self.sync_err_ms = (playing - apos) / 48.0
                while len(self._pending) > 150:         # never hold more than ~10 s
                    out = self._pending.popleft()[1]
            if out is not None:
                self._publish(out)
            time.sleep(0.01)

    def _publish(self, jpeg):
        with self.cond:
            self.jpeg = jpeg
            self.seq += 1
            self.cond.notify_all()

    def run(self):
        while True:
            try:
                r = urllib.request.urlopen(self.upstream + "/stream.mjpg", timeout=5)
                self.live = True
                while True:
                    length, apos = None, None
                    while True:                         # part headers
                        line = r.readline()
                        if not line:
                            raise ConnectionError("upstream closed")
                        line = line.strip()
                        if line.lower().startswith(b"content-length:"):
                            length = int(line.split(b":", 1)[1])
                        elif line.lower().startswith(b"x-audio-pos:"):
                            apos = int(line.split(b":", 1)[1])
                        elif not line and length is not None:
                            break
                    jpeg = r.read(length)
                    with self._plock:
                        self._pending.append((time.monotonic(), jpeg, apos))
                    r.readline()                        # the CRLF after the part
            except (OSError, ConnectionError, ValueError):
                self.live = False
                with self._plock:
                    self._pending.clear()
                # Keep the page alive with the card while the game is away.
                for _ in range(2):
                    self._publish(self._placeholder)
                    time.sleep(0.5)


class Audio:
    """Raw game sound in, real-time 48 kHz out, silence when the game is away."""

    def __init__(self, upstream):
        self.upstream = upstream
        self.buf = np.zeros((0, 2), dtype=np.float32)
        self.pos = 0.0                     # fractional read position in buf
        self.lock = threading.Lock()
        self.live = False
        self.in_rate = None                # frames/s the game is producing; learned slowly
        self._arrived, self._since = 0, time.monotonic()
        self.ratio = 0.75
        # Where in the game's own sound the relay currently is: what it was
        # told at connection time, plus everything played or skipped since.
        # The picture is matched against this (Video._release).
        self.base_pos, self.consumed = None, 0
        self._first_prime = True
        self.listeners = []                # per-client deques of bytes chunks
        self.llock = threading.Lock()

    def run_input(self):
        while True:
            try:
                r = urllib.request.urlopen(self.upstream + "/audio.raw", timeout=5)
                start = r.headers.get("X-Audio-Pos")
                # Measure the game's rate from THIS connection's start: timing
                # from the relay's own start counted the wait for the game as
                # slowness, and the (deliberately slow) follower then spent
                # minutes climbing from a speed that was never real.
                with self.lock:
                    self.in_rate = None
                    self._arrived, self._since = 0, time.monotonic()
                    self.base_pos = int(start) if start is not None else None
                    self.consumed = 0
                self.live = True
                while True:
                    data = r.read(4 * 1600)
                    if not data:
                        raise ConnectionError("upstream closed")
                    data = data[:len(data) // 4 * 4]
                    pcm = np.frombuffer(data, dtype=np.int16).reshape(-1, 2).astype(np.float32)
                    with self.lock:
                        self.buf = np.concatenate([self.buf, pcm])
                        if len(self.buf) > RATE * 3:          # never hoard more than 3 s
                            drop = len(self.buf) - RATE
                            self.buf = self.buf[drop:]
                            self.pos = max(0.0, self.pos - drop)
                            self.consumed += drop
                        self._arrived += len(pcm)
                        now = time.monotonic()
                        if now - self._since >= 1.0:
                            rate = self._arrived / (now - self._since)
                            if self.in_rate is None:
                                self.in_rate = rate
                            else:
                                k = 1.0 - np.exp(-(now - self._since) / RATE_TAU_S)
                                self.in_rate += k * (rate - self.in_rate)
                            self._arrived, self._since = 0, now
            except (OSError, ConnectionError, ValueError):
                self.live = False
                with self.lock:
                    self.buf = np.zeros((0, 2), dtype=np.float32)
                    self.pos = 0.0
                    self.base_pos = None
                time.sleep(1.0)

    def _next_chunk(self):
        with self.lock:
            level = (len(self.buf) - self.pos) / RATE
            if not self.live or self.in_rate is None:
                self._primed = False
                return np.zeros((CHUNK, 2), dtype=np.int16)
            # Start with a full cushion so playback does not begin by
            # stuttering -- but come BACK from a dry patch as soon as there is
            # anything to play. Waiting for the full cushion again meant
            # roughly two seconds of silence every time it ran dry (the game
            # only makes ~0.68 s of sound per second, so refilling 1.2 s takes
            # that long), and then a jump in playback rate on resuming, which
            # is what the 11 %/s pitch spikes actually were. Neither is
            # something a listener should be given for a momentary shortfall.
            if not getattr(self, "_primed", False):
                want_level = TARGET_BUF_S if self._first_prime else REPRIME_S
                if level < want_level:
                    return np.zeros((CHUNK, 2), dtype=np.int16)
                self._primed = True
                if self._first_prime:
                    # Nothing has been heard yet, so a rate jump cannot be
                    # heard either: start at the game's own rate.
                    self.ratio = float(np.clip(self.in_rate / RATE, 0.3, 1.25))
                    self._first_prime = False
            if level < 0.02:
                self._primed = False
                return np.zeros((CHUNK, 2), dtype=np.int16)
            if level > MAX_BUF_S:                     # a long speed-up: skip ahead once
                skip = int((level - TARGET_BUF_S) * RATE)
                self.buf = self.buf[skip:]
                self.consumed += skip
                level = (len(self.buf) - self.pos) / RATE
            # Steady rate: the game's slow average, trimmed to hold the cushion.
            #
            # Gently while the cushion is comfortable, because pitch follows
            # playback rate and an ear hears a wobbling rate as warble. But
            # gently ALL the time does not work: the game's own rate swings
            # about 20% as scenes change (measured 0.625-0.778x within 100 s)
            # and a 3% trim that may only move 0.002/s cannot answer that. It
            # did not: the cushion sat at 0.41 s against a 0.7 s target, spent
            # a third of its time under 0.3 s, and touched 0.02 s, which is a
            # silent gap. So past SAFE the trim and the slew both open up: a
            # brief pitch slide is far less objectionable than the sound
            # cutting out, and it only happens when the alternative is that.
            base = self.in_rate / RATE
            err = float(np.clip((level - TARGET_BUF_S) / TARGET_BUF_S, -1.0, 2.0))
            if abs(err) <= SAFE:
                trim = TRIM * err / SAFE
                step = SLEW * CHUNK / RATE
            else:
                over = (abs(err) - SAFE) / SAFE
                mag = min(TRIM_MAX, TRIM + over * (TRIM_MAX - TRIM))
                trim = mag if err > 0 else -mag
                step = SLEW_FAST * CHUNK / RATE
            want = base * (1.0 + trim)
            self.ratio = float(np.clip(want, self.ratio - step, self.ratio + step))
            self.ratio = float(np.clip(self.ratio, 0.3, 1.25))
            idx = self.pos + np.arange(CHUNK) * self.ratio
            i0 = np.floor(idx).astype(np.int64)
            frac = (idx - i0)[:, None]
            last = len(self.buf) - 2
            ok = i0 <= last
            out = np.zeros((CHUNK, 2), dtype=np.float32)
            if ok.any():
                a, b = self.buf[i0[ok]], self.buf[i0[ok] + 1]
                out[ok] = a + (b - a) * frac[ok]
            self.pos += CHUNK * self.ratio
            used = int(min(np.floor(self.pos), len(self.buf)))
            self.buf = self.buf[used:]
            self.pos -= used
            self.consumed += used
        return np.clip(out, -32768, 32767).astype(np.int16)

    def run_output(self):
        t = time.monotonic()
        while True:
            chunk = self._next_chunk().tobytes()
            with self.llock:
                for q in self.listeners:
                    q.append(chunk)
            t += CHUNK / RATE
            time.sleep(max(0.0, t - time.monotonic()))
            if time.monotonic() - t > 1.0:          # fell far behind: resync
                t = time.monotonic()

    def latency_s(self):
        """How far behind real time the sound plays: the cushion, in seconds
        of listening. The picture is held back by the same amount."""
        if not self.live or not getattr(self, "_primed", False):
            return 0.0
        return min(self.buffered_s() / max(self.ratio, 0.3), 6.0)

    def pos_now(self):
        """The game's own sound position being sent right now, or None when
        there is no sound to match the picture against."""
        with self.lock:
            if not self.live or self.base_pos is None or not getattr(self, "_primed", False):
                return None
            return self.base_pos + self.consumed

    def buffered_s(self):
        with self.lock:
            return (len(self.buf) - self.pos) / RATE


def wav_header():
    """A WAV header for a stream that never ends (sizes left at maximum)."""
    return (b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE" +
            b"fmt " + struct.pack("<IHHIIHH", 16, 1, 2, RATE, RATE * 4, 4, 16) +
            b"data" + struct.pack("<I", 0xFFFFFFFF))


def serve(port=8765, upstream="http://127.0.0.1:8766", host="0.0.0.0"):
    audio = Audio(upstream)
    video = Video(upstream, delay=audio.latency_s, audio_pos=audio.pos_now,
                  audio_listeners=lambda: len(audio.listeners))
    # Game audio is not used on the stream (Ian, 2026-10-06), so the relay no
    # longer pulls or paces it unless FLYBRAIN_AUDIO=1. Video never waited for
    # it anyway (no listeners); this just stops the work.
    fns = [video.run]
    if os.environ.get("FLYBRAIN_AUDIO") == "1":
        fns += [audio.run_input, audio.run_output]
    for fn in fns:
        threading.Thread(target=fn, daemon=True).start()

    _emu = deque()                # (when, emulator frame count) samples for game_avg

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            if path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", PAGE.encode())
            elif path == "/boot":
                # Who this relay is: changes on every restart. The viewer page
                # polls it to notice a restart its <img> cannot (see PAGE).
                self._send(200, "text/plain", BOOT.encode())
            elif path == "/panel/youtube":
                from . import panel
                why = panel.check(self.client_address[0], self.headers.get("Authorization"),
                                  panel.panel_token())
                if why:
                    return self._send(why[0], "application/json",
                                      json.dumps({"ok": False, "why": why[1]}).encode())
                return self._send(200, "application/json",
                                  json.dumps(panel.youtube_probe()).encode())
            elif path == "/panel/brain_layout":
                # obsfly's GPU brain layer: the fixed 4000-neuron sample with
                # schematic positions (play/brain_layout.py). Bearer, read-only.
                from . import panel
                why = panel.check(self.client_address[0], self.headers.get("Authorization"),
                                  panel.panel_token())
                if why:
                    return self._send(why[0], "application/json",
                                      json.dumps({"ok": False, "why": why[1]}).encode())
                p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                 "data", "brain_layout.json")
                try:
                    with open(p, "rb") as f:
                        return self._send(200, "application/json", f.read())
                except OSError:
                    return self._send(404, "application/json", b'{"ok": false, "why": "not built"}')
            elif path == "/panel/experiment_file":
                # Raw experiment files for obsfly's blind recompute (2026-10-09:
                # hand-copied rows are not good enough). Read-only, bearer,
                # one directory, plain names with an allowed extension only.
                from . import panel
                why = panel.check(self.client_address[0], self.headers.get("Authorization"),
                                  panel.panel_token())
                if why:
                    return self._send(why[0], "application/json",
                                      json.dumps({"ok": False, "why": why[1]}).encode())
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                name = (q.get("name") or [""])[0]
                exp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                   "data", "experiments")
                if (not name or name != os.path.basename(name) or name.startswith(".")
                        or os.path.splitext(name)[1] not in (".jsonl", ".txt", ".md", ".py", ".log", ".csv")
                        or not os.path.isfile(os.path.join(exp, name))):
                    return self._send(404, "application/json", b'{"ok": false, "why": "no such file"}')
                with open(os.path.join(exp, name), "rb") as f:
                    return self._send(200, "text/plain; charset=utf-8", f.read())
            elif path in ("/panel/runs", "/panel/run_export"):
                # Run registry and per-run export for obsfly's run reports
                # (play/run_export.py). Ended runs are cached for good.
                from . import panel
                why = panel.check(self.client_address[0], self.headers.get("Authorization"),
                                  panel.panel_token())
                if why:
                    return self._send(why[0], "application/json",
                                      json.dumps({"ok": False, "why": why[1]}).encode())
                import sys as _sys
                _sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
                    os.path.abspath(__file__))), "play"))
                import run_export
                if path == "/panel/runs":
                    return self._send(200, "application/json", json.dumps(run_export.runs()).encode())
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                try:
                    out = run_export.export(int((q.get("id") or ["0"])[0]))
                except ValueError:
                    out = None
                if out is None:
                    return self._send(404, "application/json", b'{"ok": false, "why": "no such run"}')
                return self._send(200, "application/json", json.dumps(out).encode())
            elif path == "/panel/live":
                # The fly right now, for the panel's live view (~2 Hz): the
                # game's /live, passed through. In-memory there; no API calls.
                from . import panel
                why = panel.check(self.client_address[0], self.headers.get("Authorization"),
                                  panel.panel_token())
                if why:
                    return self._send(why[0], "application/json",
                                      json.dumps({"ok": False, "why": why[1]}).encode())
                try:
                    with urllib.request.urlopen(upstream + "/live", timeout=2) as r:
                        return self._send(200, "application/json", r.read())
                except urllib.error.HTTPError as e:
                    return self._send(e.code, "application/json", e.read())
                except OSError:
                    return self._send(503, "application/json", b'{"why": "the game is not running"}')
            elif path == "/panel/inputs":
                # The per-viewer input report (play/inputs_report.py --json).
                from . import panel
                why = panel.check(self.client_address[0], self.headers.get("Authorization"),
                                  panel.panel_token())
                if why:
                    return self._send(why[0], "application/json",
                                      json.dumps({"ok": False, "why": why[1]}).encode())
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                try:
                    hours = max(1, min(168, int(q.get("hours", ["24"])[0])))
                except ValueError:
                    hours = 24
                play = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play")
                if play not in sys.path:
                    sys.path.insert(0, play)
                import inputs_report
                return self._send(200, "application/json",
                                  json.dumps(inputs_report.report(float(hours))).encode())
            elif path == "/panel/trainers":
                # Who trained the fly, by synaptic change booked (attrib.trainers).
                from . import panel, attrib
                why = panel.check(self.client_address[0], self.headers.get("Authorization"),
                                  panel.panel_token())
                if why:
                    return self._send(why[0], "application/json",
                                      json.dumps({"ok": False, "why": why[1]}).encode())
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                try:
                    days = max(1, min(60, int(q.get("days", ["7"])[0])))
                except ValueError:
                    days = 7
                return self._send(200, "application/json", json.dumps(attrib.trainers(days)).encode())
            elif path == "/panel/thoughts":
                # The science tab: thought counts, rates and map-checked
                # accuracy per day (flybrain/thoughts.py). ?days=N, max 60.
                from . import panel, thoughts
                why = panel.check(self.client_address[0], self.headers.get("Authorization"),
                                  panel.panel_token())
                if why:
                    return self._send(why[0], "application/json",
                                      json.dumps({"ok": False, "why": why[1]}).encode())
                q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
                try:
                    days = max(1, min(60, int(q.get("days", ["7"])[0])))
                except ValueError:
                    days = 7
                return self._send(200, "application/json",
                                  json.dumps(thoughts.summary(days)).encode())
            elif path == "/panel/status":
                from . import panel
                why = panel.check(self.client_address[0], self.headers.get("Authorization"),
                                  panel.panel_token())
                if why:
                    return self._send(why[0], "application/json",
                                      json.dumps({"ok": False, "why": why[1]}).encode())
                rel = {"game_live": video.live, "frames": video.seq, "audio_live": audio.live,
                       "listeners": len(audio.listeners), "video_held_ms": round(video.held_ms)}
                return self._send(200, "application/json",
                                  json.dumps(panel.status(upstream, rel)).encode())
            elif path == "/game":
                # The bare game for the OBS-side HUD (2026-10-09): pixel-perfect
                # scaling, and back by itself after a game restart.
                self._send(200, "text/html; charset=utf-8", GAME_PAGE.encode())
            elif path == "/game.mjpg":
                # Passed straight through from the game: no sync, no buffer --
                # chat control wants the least latency there is. And it never
                # ENDS: 2026-10-09 13:33 a game restart closed the upstream, the
                # relay ended the response cleanly, and OBS's <img> sat on its
                # last frame with no error. Now the relay reconnects upstream
                # itself and the viewer's stream just carries on.
                who = self.client_address[0]
                _game_log(f"viewer {who} connected")
                ups = 0
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=flyframe")   # stream.BOUNDARY
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    while True:
                        try:
                            up = urllib.request.urlopen(upstream + path, timeout=5)
                        except (urllib.error.URLError, OSError):
                            time.sleep(1.0)              # the game is restarting
                            continue
                        ups += 1
                        if ups > 1:
                            _game_log(f"viewer {who}: upstream reconnected (#{ups})")
                        try:
                            while True:
                                chunk = up.read1(65536) if hasattr(up, "read1") else up.read(65536)
                                if not chunk:
                                    break
                                self.wfile.write(chunk)
                                self.wfile.flush()
                        except (urllib.error.URLError, OSError) as e:
                            if isinstance(e, (BrokenPipeError, ConnectionResetError)):
                                raise
                        finally:
                            up.close()
                        time.sleep(0.5)
                except (BrokenPipeError, ConnectionResetError, OSError) as e:
                    _game_log(f"viewer {who} gone ({type(e).__name__})")
                return
            elif path == "/game.png":
                try:
                    up = urllib.request.urlopen(upstream + path, timeout=5)
                except (urllib.error.URLError, OSError):
                    return self._send(503, "text/plain", b"the game is not running")
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", up.headers.get("Content-Type", "image/png"))
                    self.send_header("Cache-Control", "no-store")
                    self.end_headers()
                    while True:
                        chunk = up.read1(65536) if hasattr(up, "read1") else up.read(65536)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    up.close()
                return
            elif path == "/panel/overlay":
                # Everything the window overlay draws, as data (playview.overlay_snapshot).
                from . import panel
                why = panel.check(self.client_address[0], self.headers.get("Authorization"),
                                  panel.panel_token())
                if why:
                    return self._send(why[0], "application/json",
                                      json.dumps({"ok": False, "why": why[1]}).encode())
                try:
                    with urllib.request.urlopen(upstream + "/overlay", timeout=2) as r:
                        return self._send(200, "application/json", r.read())
                except urllib.error.HTTPError as e:
                    return self._send(e.code, "application/json", e.read())
                except OSError:
                    return self._send(503, "application/json", b'{"why": "the game is not running"}')
            elif path == "/frame.jpg":
                with video.cond:
                    self._send(200, "image/jpeg", video.jpeg)
            elif path == "/health":
                # The relay's own numbers, then whatever the game reports about
                # itself (chat state). Without this second part the game's
                # health is invisible from outside the Pi, since the relay owns
                # the public port -- which is how another machine came to be
                # told to poll for a line it could never see.
                # With no listener the picture is not waiting for anything,
                # so the audio delay says nothing about what a viewer sees.
                sync = ("n/a" if video.sync_err_ms is None or not audio.listeners
                        else f"{video.sync_err_ms:+.0f}ms")
                if not match_audio():
                    sync = "off"         # by choice: picture leads sound by av_delay
                try:
                    up = urllib.request.urlopen(upstream + "/health", timeout=2).read().decode()
                except Exception:
                    up = None
                audio_on = os.environ.get("FLYBRAIN_AUDIO") == "1"
                if audio_on:
                    gavg = (audio.in_rate or 0) / RATE
                else:
                    # Game speed from the EMULATOR's own frame counter (emu=)
                    # now that there is no audio stream to time it by: real
                    # GBA frames per second over the last ~10 s, / 59.7275.
                    import re as _re
                    m = _re.search(r"emu=(\d+)", up or "")
                    now_m = time.monotonic()
                    if m:
                        _emu.append((now_m, int(m.group(1))))
                    while len(_emu) > 2 and now_m - _emu[1][0] > 10.0:
                        _emu.popleft()
                    gavg = 0.0
                    if len(_emu) >= 2 and _emu[-1][1] >= _emu[0][1] and _emu[-1][0] > _emu[0][0]:
                        gavg = (_emu[-1][1] - _emu[0][1]) / (_emu[-1][0] - _emu[0][0]) / 59.7275
                body = (f"ok game={'live' if video.live else 'down'} "
                        f"audio={('live' if audio.live else 'down') if audio_on else 'off'} "
                        f"frames={video.seq} buffer={audio.buffered_s():.2f}s "
                        f"speed={audio.ratio:.3f}x game_avg="
                        f"{gavg:.3f}x av_delay={audio.latency_s():.2f}s "
                        f"listeners={len(audio.listeners)} sync={sync} "
                        f"video_held={video.held_ms:.0f}ms\n")
                if up is not None:
                    body += "".join(f"game: {ln}\n" for ln in up.split("\n")[1:] if ln.strip())
                else:                                       # the game may be down
                    body += "game: unreachable\n"
                body += pi_line() + "\n"
                self._send(200, "text/plain", body.encode())
            elif path == "/stream.mjpg":
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=flyframe")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                seen = -1
                try:
                    while True:
                        with video.cond:
                            video.cond.wait_for(lambda: video.seq != seen, timeout=2.0)
                            jpeg, seen = video.jpeg, video.seq
                        self.wfile.write(b"--flyframe\r\nContent-Type: image/jpeg\r\n"
                                         + f"Content-Length: {len(jpeg)}\r\n\r\n".encode())
                        self.wfile.write(jpeg + b"\r\n")
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
            elif path == "/audio.wav":
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                q = deque(maxlen=50)                  # a slow listener drops, never stalls
                with audio.llock:
                    audio.listeners.append(q)
                try:
                    self.wfile.write(wav_header())
                    while True:
                        if q:
                            self.wfile.write(q.popleft())
                        else:
                            time.sleep(0.005)
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass
                finally:
                    with audio.llock:
                        audio.listeners.remove(q)
            else:
                self._send(404, "text/plain", b"not found")

        def do_POST(self):
            if self.path.split("?")[0] == "/panel/control":
                from . import panel
                why = panel.check(self.client_address[0], self.headers.get("Authorization"),
                                  panel.panel_token())
                if why:
                    return self._send(why[0], "application/json",
                                      json.dumps({"ok": False, "why": why[1]}).encode())
                n = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(min(n, 4096))
                try:
                    req = json.loads(body or b"{}")
                    req = {"action": str(req.get("action", "")),
                           "key": req.get("key") or req.get("kind")}
                    if req["action"] == "yt_end":
                        # Pi-side, not the game's: end the YouTube broadcast
                        # before maintenance stops OBS (panel.youtube_end).
                        out = panel.youtube_end()
                        panel.log_action(self.client_address[0], req, out)
                        return self._send(200, "application/json", json.dumps(out).encode())
                    r = urllib.request.Request(upstream + "/panel/control",
                                               data=json.dumps(req).encode(), method="POST",
                                               headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(r, timeout=5) as resp:
                        out = json.loads(resp.read() or b"{}")
                except urllib.error.HTTPError as e:
                    out = {"ok": False, "why": e.read().decode(errors="replace")[:200]}
                except OSError:
                    out = {"ok": False, "why": "the game is not running"}
                except ValueError:
                    req, out = {"raw": body[:100].decode(errors="replace")}, {"ok": False, "why": "bad JSON"}
                panel.log_action(self.client_address[0], req, out)
                return self._send(200, "application/json", json.dumps(out).encode())
            if self.path.split("?")[0] != "/test-cheer":
                return self._send(404, "text/plain", b"not found")
            if self.client_address[0] not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
                return self._send(403, "text/plain", b"rehearsals only from the Pi itself")
            n = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(n)
            req = urllib.request.Request(upstream + "/test-cheer", data=body, method="POST",
                                         headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    self._send(r.status, "application/json", r.read())
            except urllib.error.HTTPError as e:
                self._send(e.code, "text/plain", e.read())
            except OSError:
                self._send(503, "text/plain", b"the game is not running right now")

    httpd = ThreadingHTTPServer((host, port), H)
    httpd.daemon_threads = True
    print(f"flybrain relay on :{port}, forwarding {upstream}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    import sys
    serve(int(sys.argv[1]) if len(sys.argv) > 1 else 8765,
          sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8766")
