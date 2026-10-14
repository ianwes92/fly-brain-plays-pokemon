"""Vertical Shorts: cut on the right moments, rendered correctly, never costly.

The failures worth guarding are the silent ones: a caption that runs off a
phone screen, a Pokemon name with a character ffmpeg treats as syntax, a frame
the wrong way round, a burst of milestones stacking up encodes, and the
recorder raising inside the live draw loop.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flybrain import shorts as sh

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


# -- captions are short, shouty, and fit a phone ---------------------------
check("a catch names the Pokemon", sh.caption("caught", "CAUGHT SPEAROW!") == "CAUGHT SPEAROW!")
check("lower case in still reads as a shout", sh.caption("caught", "caught pidgey!") == "CAUGHT PIDGEY!")
check("a badge", sh.caption("badge", "EARNED A BADGE!  (2 in total)") == "EARNED A BADGE!")
check("an evolution keeps both names",
      "WARTORTLE" in sh.caption("evolve", "SQUIRTLE EVOLVED INTO WARTORTLE!"))
check("a wipe", sh.caption("faint", "the whole team fainted") == "THE WHOLE TEAM FAINTED")
check("a catch with no name still says something", sh.caption("caught", "CAUGHT !") == "CAUGHT ONE!")

for text in ("CAUGHT MR. MIME!", "SQUIRTLE EVOLVED INTO WARTORTLE!", "THE WHOLE TEAM FAINTED",
             "CAUGHT FARFETCH'D!", "EARNED A BADGE!"):
    fs = sh.font_size(text)
    check(f"'{text}' fits the width", 0.62 * len(text) * fs <= sh.W - 40 + 1)
    check(f"'{text}' stays legible", fs >= 28)
check("short captions are not shrunk", sh.font_size("EARNED A BADGE!") == 58)

# -- only the moments worth posting, and not too often ---------------------
now = [1000.0]
r = sh.ShortsRecorder(clock=lambda: now[0])
for kind in ("badge", "caught", "evolve", "faint"):
    check(f"{kind} is worth a short", kind in sh.SHORT_OF)
check("a level-up is not", "level" not in sh.SHORT_OF)
check("wanted at first", r.wanted("caught"))
check("a level is never wanted", not r.wanted("level"))
r.last_cut = now[0]
check("not straight after another", not r.wanted("caught"))
now[0] += sh.GAP_S - 1
check("not just before the gap is up", not r.wanted("caught"))
now[0] += 2
check("wanted again after the gap", r.wanted("caught"))

r2 = sh.ShortsRecorder(clock=lambda: now[0])
check("an unwanted kind starts no thread", r2.cut_later("level", "x") is None)

# -- the buffer is a fixed window and holds its own copies -----------------
r3 = sh.ShortsRecorder(seconds=2, fps=10)
g = np.zeros((160, 240, 3), np.uint8)
b = np.zeros((320, 320, 3), np.uint8)
for i in range(50):
    g[:] = i
    r3.add(g, b)
check("the ring never grows past its window", len(r3.frames) == 20)
check("it keeps the newest frames", int(r3.frames[-1][0][0, 0, 0]) == 49)
check("and the oldest it kept are the right ones", int(r3.frames[0][0][0, 0, 0]) == 30)
g[:] = 255
check("a stored frame is a copy, not a view the caller can change",
      int(r3.frames[-1][0][0, 0, 0]) == 49)

# -- it never raises inside the draw loop ----------------------------------
r4 = sh.ShortsRecorder()
r4.add(None, None)                 # garbage in
r4.add("not an array", 5)
check("garbage frames are swallowed, not raised", True)

# -- too little footage is not posted --------------------------------------
r5 = sh.ShortsRecorder(clock=lambda: now[0])
for _ in range(10):                # half a second
    r5.add(np.zeros((160, 240, 3), np.uint8), np.zeros((320, 320, 3), np.uint8))
path = r5.cut_later("caught", "CAUGHT X!", post_s=0)
check("cut_later returns the planned path", isinstance(path, str) and path.endswith(".mp4"))
r5.last_thread.join(timeout=10)
check("a failed cut leaves a .failed marker for the poster", os.path.exists(path + ".failed"))
os.remove(path + ".failed")
check("under five seconds of footage makes nothing", r5.made == [] and r5.failed == 1)

# -- it actually renders a correct video -----------------------------------
if shutil.which("ffmpeg") and shutil.which("ffprobe"):
    out_dir = tempfile.mkdtemp()
    sh.OUT_DIR = out_dir
    snap = []
    for i in range(sh.FPS * 6):    # six seconds
        game = np.zeros((160, 240, 3), np.uint8)
        game[:, : (i * 2) % 240] = (40, 160, 80)       # something moving
        brain = np.zeros((320, 320, 3), np.uint8)
        brain[100:220, 100:220] = (200, 200, 255)
        snap.append((game, brain))
    # A caption full of drawtext syntax: colon, quote, percent, backslash.
    nasty = "CAUGHT FARFETCH'D: 100% \\REAL!"
    path = sh.ShortsRecorder().render(snap, "caught", nasty)
    check("a caption full of ffmpeg syntax still renders", path is not None)
    if path:
        p = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=width,height,codec_name,pix_fmt",
                            "-show_entries", "format=duration",
                            "-of", "default=nw=1", path], capture_output=True, text=True).stdout
        check("it is H.264", "codec_name=h264" in p)
        check("it is 9:16 at 720x1280", "width=720" in p and "height=1280" in p)
        check("it plays everywhere (yuv420p)", "pix_fmt=yuv420p" in p)
        dur = float([l for l in p.splitlines() if l.startswith("duration=")][0].split("=")[1])
        check("it is as long as the footage", abs(dur - 6.0) < 0.2)
        check("the staging files were cleaned up",
              not [f for f in os.listdir(sh.STAGE) if f.startswith(f"short-{os.getpid()}")])

print(f"{ok} checks pass")
