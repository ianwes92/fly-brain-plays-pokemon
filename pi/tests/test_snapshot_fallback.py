"""A damaged snapshot must not crash-loop the stream: load falls back to the
previous one (.prev, kept by hard link on every save), then the daily backup."""
import os, shutil, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import game as gm

ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1

rom, sav, state = gm.default_paths()
d = tempfile.mkdtemp()
real = gm.GBA(os.path.join(gm.PLAY_DIR, "firered.gba"), None, None) if os.path.exists(os.path.join(gm.PLAY_DIR, "firered.gba")) else None
assert real is not None, "needs the ROM"
for _ in range(30):
    real.frame(0)
snap = os.path.join(d, "t.resume.ss")
real.save_state(snap)
check("a save writes the snapshot", os.path.getsize(snap) > 1000)
check("no .prev on the first save", not os.path.exists(snap + ".prev"))
first = open(snap, "rb").read()
for _ in range(30):
    real.frame(0)
real.save_state(snap)
check("the second save keeps the first as .prev", open(snap + ".prev", "rb").read() == first)
check("and the snapshot path itself always exists", os.path.exists(snap))
check("a good snapshot loads as itself", real.load_with_fallback(snap) == snap)
open(snap, "wb").write(b"garbage" * 100)
check("a corrupt snapshot falls back to .prev", real.load_with_fallback(snap) == snap + ".prev")
open(snap + ".prev", "wb").write(b"garbage")
bk = tempfile.mkdtemp(); was = gm.GBA.BACKUP_DIR; gm.GBA.BACKUP_DIR = bk
shutil.copyfile(os.path.join(gm.PLAY_DIR, "firered.resume.ss"), os.path.join(bk, "t.resume.ss"))
check("both corrupt: falls back to the daily backup", real.load_with_fallback(snap) == os.path.join(bk, "t.resume.ss"))
os.remove(os.path.join(bk, "t.resume.ss"))
try:
    real.load_with_fallback(snap); raised = False
except OSError:
    raised = True
check("nothing loadable: it raises (the poster's game-down alert covers it)", raised)
gm.GBA.BACKUP_DIR = was
print(f"{ok} checks pass")
