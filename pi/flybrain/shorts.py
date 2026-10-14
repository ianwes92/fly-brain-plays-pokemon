"""Vertical short videos of the fly's best moments, cut automatically.

Discovery is this stream's problem, not retention: when people arrive they
engage (852 chat presses in one run) but the peak audience was eight. Short
vertical video -- YouTube Shorts, TikTok, Reels -- is what brings a small
channel new viewers, and the moments worth posting (a catch, a badge, an
evolution, the whole team fainting) are already detected by game.milestones.
They used to happen at 3am and vanish. Now each one leaves a ready-to-post
9:16 clip behind.

THE FRAMES ARE CLEAN. The stream's composited window carries Twitch-only
clutter -- "cheer50 steer", the chat-drives banner, the leaderboard -- which is
noise on YouTube or TikTok. So frames are taken inside the game loop at the one
moment the screen holds the raw game picture and the labelled brain map
(EYES, MEMORY, COMPASS, which genuinely help a stranger) and nothing else.

COST IS KEPT OFF THE BRAIN. Every frame is a memcpy into a RAM ring buffer, not
a JPEG encode: about 0.1 ms. Encoding only happens on a milestone, a few times
a day, as a separate `nice 19` process that always yields to the brain, staged
through /dev/shm so a burst of video never touches the SSD (which sits behind a
power supply known to be marginal). Measured on this Pi with every core busy:
33-45 s of one low-priority core per 30 s clip.
"""
import collections
import os
import shutil
import subprocess
import threading
import time

import numpy as np

OUT_DIR = os.path.expanduser("~/flybrain/shorts")
STAGE = "/dev/shm" if os.path.isdir("/dev/shm") else "/tmp"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

FPS = 20                      # the window's own rate, so nothing is resampled
SECONDS = 30                  # a Short's natural length
POST_S = 5.0                  # keep this much AFTER the moment: the payoff, not just the build-up
GAP_S = 300.0                 # at most one short per five minutes; moments cluster
SHORT_OF = ("badge", "caught", "evolve", "faint")   # levels are too frequent to be news
HOOK = "A REAL FLY BRAIN"
from .site import TWITCH_URL as _TWITCH_URL                    # noqa: E402
FOOTER = f"LIVE 24/7  {_TWITCH_URL}"

W, H = 720, 1280              # 9:16
TOP = 200                     # caption band
GAME_H = 480                  # 240x160 at 3x
BRAIN = 600                   # the 320x320 map, scaled


def caption(kind, text):
    """The milestone, rewritten for a phone screen: short, shouty, one line."""
    t = " ".join(str(text).split())
    if kind == "caught":
        name = t.upper().replace("CAUGHT", "").strip(" !")
        return f"CAUGHT {name}!" if name else "CAUGHT ONE!"
    if kind == "badge":
        return "EARNED A BADGE!"
    if kind == "evolve":
        # "SQUIRTLE EVOLVED INTO WARTORTLE!" -> keep it, the names ARE the news
        return t.upper().rstrip("!") + "!"
    if kind == "faint":
        return "THE WHOLE TEAM FAINTED"
    return t.upper()[:40]


def font_size(text, largest=58, width=W - 40):
    """Shrink to fit one line. DejaVu Sans Bold runs ~0.62 em per capital."""
    est = 0.62 * max(len(text), 1)
    return int(max(28, min(largest, width / est)))


class ShortsRecorder:
    """A rolling buffer of clean frames, and the means to turn one into a Short."""

    def __init__(self, seconds=SECONDS, fps=FPS, clock=time.time):
        self.fps = fps
        self.frames = collections.deque(maxlen=int(seconds * fps))
        self.clock = clock
        self.last_cut = -1e18
        self.made = []                 # paths of shorts written this run
        self.failed = 0
        self.last_thread = None

    # -- capture: must be cheap and must never take the stream down ------------
    def add(self, game, brain):
        """game: HxWx3 uint8 GBA frame. brain: 320x320x3 uint8 labelled map."""
        try:
            self.frames.append((np.ascontiguousarray(game, dtype=np.uint8).copy(),
                                np.ascontiguousarray(brain, dtype=np.uint8).copy()))
        except Exception:
            pass

    # -- cutting ---------------------------------------------------------------
    def wanted(self, kind):
        return kind in SHORT_OF and self.clock() - self.last_cut >= GAP_S

    def cut_later(self, kind, text, on_done=None, post_s=POST_S):
        """Wait for the payoff, then encode the last SECONDS in the background.

        Returns the thread, or None if this moment is not wanted (wrong kind,
        or too soon after the last short).
        """
        if not self.wanted(kind):
            return None
        self.last_cut = self.clock()
        cap = caption(kind, text)
        # The path is decided NOW, at the milestone, and returned, so it can
        # travel in the event to the Discord poster -- which then knows exactly
        # which file to wait for instead of guessing by timestamp.
        out = self.planned_path(kind, cap)
        th = threading.Thread(target=self._cut, args=(kind, cap, on_done, post_s, out),
                              daemon=True)
        self.last_thread = th
        th.start()
        return out

    def planned_path(self, kind, cap):
        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe = "".join(c if c.isalnum() else "-" for c in cap.lower()).strip("-")[:40]
        return os.path.join(OUT_DIR, f"{stamp}-{kind}-{safe}.mp4")

    def _cut(self, kind, cap, on_done, post_s, out=None):
        time.sleep(post_s)
        snap = list(self.frames)             # references; the arrays never change
        if len(snap) < self.fps * 5:
            self.failed += 1
            self._mark_failed(out)
            return                           # under 5 s of footage: not worth posting
        path = self.render(snap, kind, cap, out=out)
        if path:
            self.made.append(path)
            if on_done:
                try:
                    on_done(path, cap)
                except Exception:
                    pass
        else:
            self.failed += 1
            self._mark_failed(out)

    @staticmethod
    def _mark_failed(out):
        """Tell anyone waiting for this file that it is not coming. Without
        it the Discord poster would hold the milestone for its full timeout
        before giving up and posting the text alone."""
        if out:
            try:
                os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
                with open(out + ".failed", "w") as f:
                    f.write("render failed\n")
            except OSError:
                pass

    def render(self, snap, kind, cap, out=None):
        """Write the frames out through ffmpeg. Returns the .mp4 path or None."""
        if not shutil.which("ffmpeg"):
            return None
        os.makedirs(OUT_DIR, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        if out is None:
            out = self.planned_path(kind, cap)
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        # Written under a temporary name and renamed only when ffmpeg succeeds,
        # so the file EXISTING means the file is COMPLETE. A poster watching for
        # it must never upload a half-written mp4.
        part = out + ".part"
        tag = f"short-{os.getpid()}-{stamp}"
        g_raw = os.path.join(STAGE, tag + "-game.raw")
        b_raw = os.path.join(STAGE, tag + "-brain.raw")
        txt_hook = os.path.join(STAGE, tag + "-hook.txt")
        txt_cap = os.path.join(STAGE, tag + "-cap.txt")
        txt_foot = os.path.join(STAGE, tag + "-foot.txt")
        gh, gw = snap[0][0].shape[:2]
        bh, bw = snap[0][1].shape[:2]
        try:
            with open(g_raw, "wb") as fg, open(b_raw, "wb") as fb:
                for game, brain in snap:
                    if game.shape[:2] == (gh, gw) and brain.shape[:2] == (bh, bw):
                        fg.write(game.tobytes())
                        fb.write(brain.tobytes())
            # Text through files, never through the command line: drawtext
            # treats : ' and % as syntax, and a Pokemon name or a future caption
            # containing one would otherwise break the render silently.
            for p, t in ((txt_hook, HOOK), (txt_cap, cap), (txt_foot, FOOTER)):
                with open(p, "w") as f:
                    f.write(t)
            fs_hook, fs_cap = font_size(HOOK, 52), font_size(cap, 58)
            graph = (
                f"[0:v]scale={W}:{GAME_H}:flags=neighbor[g];"
                f"[1:v]scale={BRAIN}:{BRAIN}:flags=neighbor,"
                f"pad={W}:{BRAIN}:{(W - BRAIN) // 2}:0:black[b];"
                f"[g][b]vstack=inputs=2,pad={W}:{H}:0:{TOP}:black,"
                f"drawtext=fontfile={FONT}:textfile={txt_hook}:fontcolor=white:"
                f"fontsize={fs_hook}:x=(w-text_w)/2:y=38,"
                f"drawtext=fontfile={FONT}:textfile={txt_cap}:fontcolor=0xFFD778:"
                f"fontsize={fs_cap}:x=(w-text_w)/2:y=112,"
                f"drawtext=fontfile={FONT}:textfile={txt_foot}:fontcolor=white@0.85:"
                f"fontsize=30:box=1:boxcolor=black@0.55:boxborderw=10:"
                f"x=(w-text_w)/2:y=h-70[out]"
            )
            cmd = ["nice", "-n", "19", "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{gw}x{gh}", "-r", str(self.fps),
                   "-i", g_raw,
                   "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{bw}x{bh}", "-r", str(self.fps),
                   "-i", b_raw,
                   "-filter_complex", graph, "-map", "[out]",
                   "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                   "-pix_fmt", "yuv420p", "-r", str(self.fps),
                   "-movflags", "+faststart", "-f", "mp4", part]
            t0 = time.monotonic()
            # All cores, not the game's. launch.sh's OMP_PROC_BIND pins the
            # game's main thread to core 0 and children inherit that mask, so
            # a nice-19 ffmpeg shared one busy core and got ~1.4% of it: the
            # 2026-10-02 600 s timeout, reproduced 2026-10-05 (8.5 min at
            # 1.4% CPU). Standalone, the same encode takes ~10 s.
            def _all_cores():
                try:
                    os.sched_setaffinity(0, range(os.cpu_count() or 1))
                except OSError:
                    pass
            # ...and WITHOUT the game's OpenMP settings: this ffmpeg links
            # libgomp, which reads OMP_PROC_BIND/OMP_PLACES at startup and
            # pinned itself straight back to core 0 (found 2026-10-05, after
            # the affinity reset above alone changed nothing).
            env = {k: v for k, v in os.environ.items() if not k.startswith(("OMP_", "GOMP_"))}
            r = subprocess.run(cmd, capture_output=True, timeout=600, preexec_fn=_all_cores, env=env)
            if r.returncode != 0 or not os.path.exists(part) or os.path.getsize(part) < 1000:
                print(f"short failed: {r.stderr.decode(errors='replace')[-300:]}", flush=True)
                return None
            os.replace(part, out)            # atomic: from here on it is whole
            # Normally ~10 s under the live brain; once it took over 600 s and
            # the log could not say when or why. The duration makes the next
            # slow one visible before it becomes a timeout.
            print(f"short made in {time.monotonic() - t0:.0f}s: {os.path.basename(out)}", flush=True)
            return out
        except Exception as exc:
            print(f"short failed: {type(exc).__name__}: {exc}", flush=True)
            return None
        finally:
            for p in (g_raw, b_raw, txt_hook, txt_cap, txt_foot, part):
                try:
                    os.remove(p)
                except OSError:
                    pass
