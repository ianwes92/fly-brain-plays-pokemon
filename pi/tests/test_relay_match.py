"""The audio-match switch: with a listener, the picture waits for the sound
only when matching is on. Off, frames go straight out even with a listener --
the video latency Ian will not give up -- and a missing or damaged switch file
means the old, matched behaviour."""
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import relay

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


d = tempfile.mkdtemp()
relay.MATCH_F = os.path.join(d, "relay.json")


def setting(value):
    if value is None:
        if os.path.exists(relay.MATCH_F):
            os.remove(relay.MATCH_F)
    else:
        with open(relay.MATCH_F, "w") as f:
            f.write(value)
    relay._match["at"] = 0.0           # skip the 2 s cache


def frames_out(listeners):
    v = relay.Video("http://127.0.0.1:1", delay=lambda: 5.0,
                    audio_pos=lambda: 0, audio_listeners=lambda: listeners)
    before = v.seq
    with v._plock:
        for i in range(5):              # stamped far AHEAD of the sound
            v._pending.append((time.monotonic(), b"\xff\xd8x\xff\xd9", 10_000_000 + i))
    time.sleep(0.3)
    return v.seq - before, v


setting(None)
check("no file means matching on", relay.match_audio())
n, _ = frames_out(1)
check("matched + a listener: frames wait for the sound", n == 0)
n, _ = frames_out(0)
check("no listener: frames go straight out", n == 5)

setting(json.dumps({"match_audio": False}))
check("the file turns it off", not relay.match_audio())
n, v = frames_out(1)
check("unmatched + a listener: every frame goes straight out", n == 5)
check("and is barely held", v.held_ms < 100)

setting("{not json")
check("a damaged file falls back to matching", relay.match_audio())
setting(json.dumps([1, 2]))
check("a wrong-shaped file falls back to matching", relay.match_audio())
setting(json.dumps({"match_audio": True}))
check("and it turns back on", relay.match_audio())

print(f"{ok} checks pass")
