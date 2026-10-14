#!/usr/bin/env bash
# Deploy a code change to the live stream: tell the running game to save,
# stop, and come straight back on the new code. play/launch.sh does the
# bringing back (see RESTART_STATUS there).
#
# NEVER match the process with `pgrep -f`/`pkill -f` here. The pattern that
# finds the game also matches this script's own command line, and killing
# that takes the whole session with it -- which has happened three times.
# Matching on the command NAME and reading the arguments separately cannot.
set -u

pid=$(ps -eo pid=,comm=,args= | awk '$2 == "python3" && /flybrain\.py play/ {print $1; exit}')
if [ -z "$pid" ]; then
    echo "no game running" >&2
    exit 1
fi

echo "asking the game ($pid) to save and restart"
echo "  (it tells chat first, then saves; the stream freezes ~20s)"
kill -USR1 "$pid" || exit 1

# It saves the snapshot, the leaderboard and the diary on the way out, then
# the launcher starts it again; loading the connectome and calibrating takes
# a while, and the relay shows "back in a moment" throughout.
#
# Ask the GAME, on 8766. The relay on 8765 said "game=live" six seconds after
# a restart, while the game was still loading the connectome -- it answers for
# its own video source, which it had not yet noticed was gone. The watchdog
# polls 8766 for the same reason. Waiting for a new pid as well, because the
# old process answers on that port until it actually exits.
# 90 polls x 2 s was 3 minutes and the game once came back at 3:12 -- the
# script reported failure for a restart that had worked. Loading the
# connectome and calibrating is not a fixed cost; it competes with whatever
# the brain is doing. (On 2026-10-02 it "gave up" at 6:07 -- that was the
# phase= parsing bug below, not a slow load.) It waits 10 minutes and says
# how long it took.
start=$(date +%s)
for _ in $(seq 1 300); do
    sleep 2
    now=$(ps -eo pid=,comm=,args= | awk '$2 == "python3" && /flybrain\.py play/ {print $1; exit}')
    if [ -z "$now" ] || [ "$now" = "$pid" ]; then
        continue                  # not started yet, or the old one still going
    fi
    # Not just "it answers": it answers seconds after starting, while it is
    # still loading the connectome and calibrating, with the stream showing
    # the warm-up. Wait for the phase that means it is playing.
    # The FIRST WORD after phase=: the line also carries title=..., and
    # matching the whole remainder never equalled "playing" -- every restart
    # since title= was added "timed out" while the game was already back.
    phase=$(curl -s --max-time 4 http://127.0.0.1:8766/health | sed -n 's/^phase=\([a-z_]*\).*/\1/p')
    if [ "$phase" = "playing" ]; then
        echo "back up as $now, playing, after $(( $(date +%s) - start ))s"
        exit 0
    fi
done
echo "still not back after $(( $(date +%s) - start ))s -- check play/launcher.log" >&2
exit 1
