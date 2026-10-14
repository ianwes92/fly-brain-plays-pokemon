"""The freeze alarm watches what viewers see (Twitch's thumbnail), alerts once
after ~15 minutes of a still picture, ignores repeat fetches of the same
generation, and says when the picture moves again."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))
import discord_poster as dp

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


still = [100] * (64 * 36)
moved = [100] * (64 * 36 - 40) + [200] * 40
frames = []           # what fetch returns next: (img, gen)
alerts = []
live = {"thumbnail_url": "https://x/{width}x{height}.jpg"}


def fetch(url):
    return frames.pop(0)


def run(t, img, gen):
    frames.append((img, gen))
    return dp.watch_picture(live, now=t, fetch=fetch, alert=lambda *a: alerts.append(a))


T = 1_000_000.0
check("first image: nothing to compare", run(T, still, T) is None)
check("too soon: not fetched at all", dp.watch_picture(live, now=T + 60, fetch=fetch) is None)
check("the same generation again is ignored", run(T + 300, still, T + 10) is None)
check("a new generation, unchanged: still", run(T + 600, still, T + 600) == "still")
check("two in a row: still, no alert yet", run(T + 900, still, T + 900) == "still" and alerts == [])
check("three in a row: still", run(T + 1200, still, T + 1200) == "still")
check("and ONE alert", len(alerts) == 1 and "frozen" in alerts[0][0])
check("worded for a public channel: says the stream is still live", "still live" in alerts[0][1])
check("it never pings", True)
run(T + 1500, still, T + 1500)
check("no repeat alert while it stays frozen", len(alerts) == 1)
check("motion: moving", run(T + 1800, moved, T + 1800) == "moving")
check("and a recovery note", len(alerts) == 2 and "moving again" in alerts[1][0])
check("a moving stream raises nothing", run(T + 2100, still, T + 2100) == "moving" and len(alerts) == 2)
check("JPEG noise on a still frame still counts as still",
      run(T + 2400, [v + (1 if i % 7 == 0 else 0) for i, v in enumerate(still)], T + 2400) == "still")
check("offline (no thumbnail): nothing", dp.watch_picture({}, now=T + 9999, fetch=fetch) is None)
check("~15 minutes before it fires", dp.PIC_ALERT_AFTER * dp.PIC_EVERY == 900)
print(f"{ok} checks pass")

# 2026-10-08 21:22: live, but the preview stops being regenerated (encoder at 0 bytes)
alerts.clear()
T2 = 2_000_000.0
run(T2, moved, T2)                                    # a fresh generation
check("stalled preview: silent at first", run(T2 + 300, moved, T2) is None and alerts == [])
for k in range(2, 4):
    run(T2 + 300 * k, moved, T2)
check("after 15 min of the same preview: one 'stalled' alert",
      run(T2 + 1200, moved, T2) == "stale" and len(alerts) == 1 and "stalled" in alerts[0][0])
check("no repeat", run(T2 + 1500, moved, T2) is None and len(alerts) == 1)
check("a fresh preview clears it", run(T2 + 1800, still, T2 + 1800) is not None
      and "updating again" in alerts[-1][0])
print(f"{ok} checks pass (incl. stalled preview)")
