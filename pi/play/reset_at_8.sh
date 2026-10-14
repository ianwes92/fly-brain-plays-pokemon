#!/usr/bin/env bash
# One-shot: Ian's run reset, 2026-10-04 08:00 EDT. Choices are his, relayed by
# obsfly 2026-10-03: keep the leaderboard, start the fly naive.
# Writes the flag, then deploys the normal way; the game archives run 1 and
# clears it at startup (flybrain/reset.py). Everything goes to play/reset.log.
set -u
cd $HOME/flybrain
LOG=play/reset.log
say() { echo "$(date '+%F %T') $*" >> "$LOG"; }
say "reset: writing the flag (keep_leaderboard=true, keep_learning=false)"
printf '{"keep_leaderboard": true, "keep_learning": false, "label": "run1"}\n' > "$HOME/.config/flybrain/reset_run.json"
if ! bash play/restart.sh >> "$LOG" 2>&1; then
    say "reset: restart.sh did not confirm (if no game was running, the flag stays and the reset happens at the next start)"
fi
if [ -e "$HOME/.config/flybrain/reset_run.json" ]; then
    say "reset: FLAG STILL PRESENT -- the reset has NOT happened yet"
else
    say "reset: flag consumed -- run 1 archived, new run started"
    grep "^reset:" play/launcher.log | tail -3 >> "$LOG"
fi
curl -s --max-time 4 http://127.0.0.1:8766/health | sed -n 2p >> "$LOG"
