"""The poster alerts on what nobody watches: the game down under a live stream,
and Twitch unaskable. Once each, with an all-clear, and not for a short blip."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))
import discord_poster as dp
ok = 0
def check(n, c):
    global ok
    assert c, n
    ok += 1
A = []
up = {"game": "live", "phase": "playing"}
down = {"game": "down", "phase": None}
def run(live, health, t):
    dp.watch_health(live, health, now=t, alert=lambda *a: A.append(a[0]))
run(True, up, 0)
run(True, down, 60); run(True, down, 300)
check("a 4-minute outage (a deploy, a restart) does not alert", A == [])
run(True, down, 60 + dp.GAME_DOWN_ALERT_S)
check("10 minutes down under a live stream alerts", A == ["The fly's game is down"])
run(True, down, 2000)
check("once", len(A) == 1)
run(True, {"game": "live", "phase": "loading"}, 2100)
check("still loading is not back", len(A) == 1)
run(True, up, 2200)
check("back: one all-clear", A[-1] == "The fly's game is back" and len(A) == 2)
A.clear()
run(False, down, 5000); run(False, down, 9000)
check("stream off with the game down too: no stream alert (the Pi is the problem then)", A == [])
dp._watch.update(off=None, off_alerted=False)
run(True, up, 30000)
run(False, up, 30060); run(False, up, 30060 + 120)
check("a 2-min maintenance restart does not alert", A == [])
run(True, up, 30300)
run(False, up, 31000); run(False, up, 31000 + dp.STREAM_OFF_ALERT_S - 1)
check("offline just under 10 min: quiet", A == [])
run(False, up, 31000 + dp.STREAM_OFF_ALERT_S)
check("10 min offline with the game playing: the PIN alert", A == ["Stream offline while the fly plays"])
run(False, up, 40000)
check("once", len(A) == 1)
run(None, up, 40100)
check("Twitch unanswerable is not 'offline': no all-clear, no reset", len(A) == 1 and dp._watch["off_alerted"])
run(True, up, 40200)
check("back live: one all-clear", A[-1] == "Stream back online" and len(A) == 2)
A.clear()
run(None, up, 10000); run(None, up, 10000 + dp.TWITCH_BLIND_ALERT_S - 1)
check("Twitch unaskable for under 30 min: quiet", A == [])
run(None, up, 10000 + dp.TWITCH_BLIND_ALERT_S)
check("30 minutes blind alerts, with the fix", A and "twitch-login" not in A[0] and A[0].startswith("Twitch cannot"))
run(True, up, 20000)
check("and clears", A[-1] == "Twitch is reachable again")
print(f"{ok} checks pass")
