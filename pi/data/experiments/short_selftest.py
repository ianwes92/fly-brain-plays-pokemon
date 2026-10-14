"""Render one Short from the REAL game loop, on a COPY of the save.

The game autosaves every 30 s to its resume file, so pointing a test run at the
real save would overwrite the fly's progress and its learned synapses. This
copies the save, the snapshot and the learning file into a scratch directory
and runs the play window headless against those copies only.

What it checks that the unit tests cannot: that frames taken at the capture
point inside playview.run are clean (no chat banner, prices or leaderboard),
labelled, and the right way round -- pygame hands screen pixels back as
width x height, so a missing transpose would mirror the brain map diagonally.
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.expanduser("~/flybrain"))
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ["FLYBRAIN_SHORT_SELFTEST"] = "1"

from flybrain import game, playview                         # noqa: E402
from flybrain.connectome import Connectome                   # noqa: E402

PLAY = os.path.expanduser("~/flybrain/play")


def main():
    work = tempfile.mkdtemp(prefix="short_selftest_")
    real_sav = os.path.join(PLAY, "firered.sav")
    real_ss = os.path.join(PLAY, "firered.resume.ss")
    sav = os.path.join(work, "firered.sav")
    ss = os.path.join(work, "firered.resume.ss")
    shutil.copyfile(real_sav, sav)
    shutil.copyfile(real_ss, ss)
    if os.path.exists(real_ss + ".mb.npz"):
        shutil.copyfile(real_ss + ".mb.npz", ss + ".mb.npz")
    before = os.path.getmtime(real_ss)
    print("working on copies in", work, flush=True)

    rom, cal_sav, cal_state = game.default_paths()
    fr_rom = rom.replace("ultraviolet", "firered")
    cx = Connectome()
    playview.run(cx, rom, cal_sav, cal_state, fps=20, max_frames=20 * 60 * 12,
                 play_paths=(fr_rom, sav, None), resume=ss, twitch=False)

    after = os.path.getmtime(real_ss)
    print("REAL SAVE UNTOUCHED:", before == after, flush=True)
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
