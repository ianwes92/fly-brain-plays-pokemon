"""Pi problems reach #fly-log with what to do (2026-10-13)."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))
import discord_poster as dp
run = lambda cmd: ("flybrain-backup.service loaded failed failed backup\n" if "--failed" in cmd else "throttled=0x50005")
p = dp.system_problems(run=run, disk=10e9, temp=85.0, throttled=None)
assert set(p) == {"failed:flybrain-backup.service", "disk", "temp", "volts"}, p
assert dp.system_problems(run=lambda c: "" if "--failed" in c else "throttled=0x0", disk=500e9, temp=50.0) == {}
alerts = []
st = {"x": p}
w = lambda t: dp.watch_system(now=t, alert=lambda *a: alerts.append(a), problems=lambda: st["x"])
w(1e9); assert len(alerts) == 4
w(1e9 + 700); assert len(alerts) == 4                 # once per problem
st["x"] = {}
w(1e9 + 1400); assert len(alerts) == 8 and all(a[0].startswith("Resolved") for a in alerts[4:])
print("test_watch_system: 5 checks passed")
