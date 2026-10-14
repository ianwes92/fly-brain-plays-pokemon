#!/usr/bin/env bash
# One-shot proof that everything comes back after a reboot (2026-10-06 test).
# Runs once ~6 min after boot, reports, posts a line to #fly-log, removes itself.
export PATH=$HOME/.local/bin:$PATH
OUT=$HOME/flybrain/play/reboot_check.log
sleep 360
{
echo "== reboot check $(date '+%F %T'), up $(uptime -p)"
for u in flybrain-relay flybrain-discord flybrain-youtube flybrain-watchdog; do
    echo "$u: $(systemctl --user is-active $u)"
done
for t in flybrain-igstory.timer flybrain-backup.timer; do
    echo "$t: $(systemctl --user is-active $t)"
done
echo "game process: $(ps -eo comm=,args= | awk '$1=="python3" && /flybrain\.py play/' | wc -l)"
echo "launcher: $(ps -eo comm=,args= | awk '$1=="bash" && /play\/launch\.sh/' | wc -l)"
echo "game health: $(curl -s --max-time 5 http://127.0.0.1:8766/health | sed -n 2p)"
echo "relay health: $(curl -s --max-time 5 http://127.0.0.1:8765/health | head -1 | cut -c1-60)"
echo "pi: $(curl -s --max-time 5 http://127.0.0.1:8765/health | grep '^pi:')"
echo "autostart log: $(grep autostart $HOME/flybrain/play/launcher.log | tail -1)"
echo "youtube log: $(tail -1 $HOME/flybrain/play/youtube.log)"
} > "$OUT" 2>&1
ok=1
for u in flybrain-relay flybrain-discord flybrain-youtube flybrain-watchdog; do
    [ "$(systemctl --user is-active $u)" = active ] || ok=0
done
curl -s --max-time 5 http://127.0.0.1:8766/health | grep -q '^phase=playing ' || ok=0
if [ $ok = 1 ]; then msg="The Pi restarted and everything came back by itself: the game is playing and all services are up."; col=3908957
else msg="The Pi restarted but not everything came back by itself -- see play/reboot_check.log on the Pi."; col=15548997; fi
echo "verdict: $msg" >> "$OUT"
python3 - "$msg" "$col" <<'PY'
import json, os, sys, urllib.request
hook = json.load(open(os.path.expanduser("~/.config/flybrain/discord.json")))["webhooks"]["fly-log"]
body = {"embeds": [{"title": "Reboot check", "description": sys.argv[1], "color": int(sys.argv[2])}],
        "allowed_mentions": {"parse": []}}
req = urllib.request.Request(hook, data=json.dumps(body).encode(), method="POST",
                             headers={"Content-Type": "application/json", "User-Agent": "flybrain-check/1.0"})
urllib.request.urlopen(req, timeout=15).read()
PY
systemctl --user disable flybrain-reboot-check.service
rm -f $HOME/.config/systemd/user/flybrain-reboot-check.service
