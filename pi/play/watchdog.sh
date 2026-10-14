#!/bin/bash
# Restart the game if it STOPS RATHER THAN CRASHES.
#
# play/launch.sh already brings the game back when it exits: a crash, the
# out-of-memory killer, anything with a non-zero status. What nothing catches
# is a hang -- the process alive, the window up, the emulator frozen on one
# frame. The stream would sit on a still picture indefinitely and nobody would
# know until a person looked, which on a 24/7 stream could be all night.
#
# So: watch the emulator's own frame counter. If the game is running and the
# counter has not moved for STALL_S, kill it; launch.sh sees a non-zero exit
# and starts it again, resuming from its last autosave.
#
# It deliberately does NOTHING when no game is running. Stopping the game is
# how a person stops the stream, and a watchdog that undoes that is worse than
# no watchdog.
HEALTH=http://127.0.0.1:8766/health
STALL_S=${STALL_S:-90}          # no new frame for this long = hung
POLL_S=${POLL_S:-15}
log() { echo "$(date '+%F %T') watchdog: $*" >> $HOME/flybrain/play/watchdog.log; }

GRACE_S=${GRACE_S:-12}          # time given to save before being forced out
# A NEW game process gets this long to start answering before "no answer"
# counts as hung: at a cold boot the connectome loads from disk before the
# game serves /health at all, and 90 s could kill it mid-load in a loop.
BOOT_GRACE_S=${BOOT_GRACE_S:-600}
pid_seen=""; pid_since=$(date +%s); pid_answered=0
last_kill=0

# Ask before forcing. A game that is merely wedged -- a slow frame, a stuck
# network call -- can still handle SIGUSR1, which makes it save the snapshot,
# the learned synapses, the diary and the leaderboard, and exit 75 so
# play/launch.sh brings it straight back. Only a game that ignores that gets
# SIGKILL, which loses up to AUTOSAVE_MS of progress.
#
# NEVER SIGTERM here. The game treats that as a clean shutdown and exits 0,
# and 0 is exactly what stops the launcher's restart loop -- the stream would
# stay down until a person noticed.
end_game() {
    local pid=$1 why=$2 now
    now=$(date +%s)
    if [ $((now - last_kill)) -lt 300 ]; then
        log "REPEAT: $why (last restart $((now - last_kill))s ago -- if this keeps up, something is wrong beyond a hang)"
    fi
    last_kill=$now
    log "$why -- asking pid $pid to save and restart"
    kill -USR1 "$pid" 2>/dev/null
    for _ in $(seq 1 "$GRACE_S"); do
        sleep 1
        kill -0 "$pid" 2>/dev/null || { log "pid $pid saved and exited on its own"; return; }
    done
    log "pid $pid did not respond in ${GRACE_S}s -- forcing it out (progress since the last autosave is lost)"
    kill -9 "$pid" 2>/dev/null
}

last_seen=""
last_change=$(date +%s)
while true; do
    sleep "$POLL_S"
    pids=$(ps -eo pid,comm,args | awk '$2=="python3" && /flybrain\.py play/ {print $1}')
    n=$(printf '%s\n' "$pids" | grep -c '[0-9]')
    if [ "$n" -gt 1 ]; then
        # Two games would fight over the emulator save and the Twitch token.
        # Killing the wrong one is worse than killing neither, so say so and
        # wait for a person.
        log "$n game processes are running ($(echo $pids | tr '\n' ' ')) -- refusing to choose; not acting"
        continue
    fi
    pid=$(printf '%s\n' "$pids" | head -1)
    if [ -z "$pid" ]; then
        last_seen=""                      # no game: not our business
        last_change=$(date +%s)
        continue
    fi
    body=$(curl -s -m 5 "$HEALTH" 2>/dev/null)
    frames=$(printf '%s\n' "$body" | head -1 | sed -n 's/.*frames=\([0-9]*\).*/\1/p')
    if [ "$pid" != "$pid_seen" ]; then
        pid_seen=$pid; pid_since=$(date +%s); pid_answered=0
    fi
    [ -n "$frames" ] && pid_answered=1
    # Prefer the EMULATOR's own counter (emu=) once the game is playing.
    # frames= is the window's publish count: it kept rising with the
    # emulator worker hung, so this watched the wrong clock (found
    # 2026-10-03). Before "playing" the emulator legitimately sits still
    # for minutes while the connectome loads, so frames= is used then.
    if printf '%s\n' "$body" | grep -q '^phase=playing '; then
        emu=$(printf '%s\n' "$body" | sed -n 's/^phase=playing .*emu=\([0-9]*\).*/\1/p')
        [ -n "$emu" ] && frames="emu$emu"
    fi
    now=$(date +%s)
    if [ -z "$frames" ]; then
        # Serving is part of being alive; if it cannot answer for a whole
        # stall window either, treat that as hung too.
        limit=$STALL_S
        [ "$pid_answered" = 0 ] && limit=$BOOT_GRACE_S    # still loading
        if [ $((now - last_change)) -ge "$limit" ]; then
            end_game "$pid" "no answer from the game for $((now - last_change))s"
            last_seen=""; last_change=$(date +%s)
        fi
        continue
    fi
    if [ "$frames" != "$last_seen" ]; then
        last_seen=$frames
        last_change=$now
    elif [ $((now - last_change)) -ge "$STALL_S" ]; then
        end_game "$pid" "frame counter stuck at $frames for $((now - last_change))s"
        last_seen=""
        last_change=$(date +%s)
    fi
done
