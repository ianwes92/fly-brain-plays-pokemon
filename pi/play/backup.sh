#!/usr/bin/env bash
# Daily off-device backup of the run (Ian, 2026-10-06: "make sure everything
# works without claude"). The Pi has had repeated under-voltage, and SSD
# corruption of the save and the learned synapses is the failure that cannot be
# undone -- so a copy lives on GitHub, in a PRIVATE repository, every day.
#
# GAME STATE ONLY. Credentials (twitch.json, youtube.json, instagram.json,
# discord.json, panel.json) are never copied: the list below is an allow-list.
set -u
export PATH=$HOME/.local/bin:$PATH
REPO=$HOME/flybrain-backup
LOG=$HOME/flybrain/play/backup.log
say() { echo "$(date '+%F %T') $*" >> "$LOG"; }
cd "$REPO" || { say "no backup repo"; exit 1; }
mkdir -p play config
for f in firered.sav firered.resume.ss firered.resume.ss.mb.npz; do
    src=$HOME/flybrain/play/$f
    # Never back up an empty or missing file over a good one.
    [ -s "$src" ] && cp -p "$src" play/
done
# stats_log.json: the Threads sampler's restart-proof counter totals (no credentials)
for f in places.json diary.json leaderboard.json events.json stats_log.json; do
    src=$HOME/.config/flybrain/$f
    [ -s "$src" ] && cp -p "$src" config/
done
# The CODE too (2026-10-06): ~/flybrain is not a git repo, so an SSD failure
# would have taken every change ever made with it. Source, compiled helpers,
# the processed connectome (the 203 MB raw FlyWire table is re-fetchable with
# fetch_data.py), experiments, the site, and the Pi's own startup config.
# Never: credentials (they live in ~/.config/flybrain, not copied), ROMs,
# battery saves (backed up above), logs, media.
mkdir -p code system
rsync -a --delete --prune-empty-dirs \
    --include='*/' \
    --exclude='__pycache__/' --exclude='shorts/' --exclude='runs/' \
    --exclude='*.gba' --exclude='*.sav' --exclude='*.ss' --exclude='*.ss1' --exclude='*.npz.tmp*' \
    --exclude='play/*.resume.ss*' --exclude='*.log' --exclude='*.part' \
    --exclude='data/connections_no_threshold.csv.gz' \
    --include='*.py' --include='*.c' --include='*.so' --include='*.sh' --include='*.md' \
    --include='*.html' --include='*.css' --include='*.png' --include='*.jsonl' --include='*.txt' \
    --include='data/*.npz' --include='data/*.csv.gz' --include='current_game' \
    --include='data/thoughts/*.json' --include='data/learning/*.npz' \
    --exclude='*' \
    "$HOME/flybrain/" code/
rsync -a --delete "$HOME/.config/systemd/user/"flybrain-*.service "$HOME/.config/systemd/user/"flybrain-*.timer system/ 2>/dev/null
cp -p "$HOME/.config/autostart/flybrain-game.desktop" system/ 2>/dev/null
grep -E '^RuntimeWatchdogSec' /etc/systemd/system.conf > system/system.conf.watchdog 2>/dev/null
git add -A play config code system
if git diff --cached --quiet; then
    say "backup: nothing changed"
    exit 0
fi
git commit -q -m "run state $(date '+%F %H:%M')" &&
if git push -q origin main 2>>"$LOG"; then
    say "backup: pushed $(git rev-parse --short HEAD)"
else
    say "backup: PUSH FAILED (kept locally; will retry tomorrow)"
    # Loud, not just a log line: a backup that silently stops is found out
    # only on the day it is needed.
    python3 - <<'PYA'
import json, os, urllib.request
hook = (json.load(open(os.path.expanduser("~/.config/flybrain/discord.json"))).get("webhooks") or {}).get("fly-log")
if hook:
    body = {"embeds": [{"title": "Daily backup failed",
                        "description": "Today's off-Pi backup of the fly's save could not be pushed to GitHub. "
                                       "It retries tomorrow; if this repeats, the GitHub login on the Pi "
                                       "needs renewing (`gh auth login`).", "color": 0xED4245}],
            "allowed_mentions": {"parse": []}}
    req = urllib.request.Request(hook, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "flybrain-backup/1.0"})
    urllib.request.urlopen(req, timeout=15).read()
PYA
    exit 1
fi
