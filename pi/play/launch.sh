#!/usr/bin/env bash
# Started by the "Fruit Fly Plays Pokémon" desktop shortcut.
# Resumes the saved game (autosaved every 30 s of game time and on exit), or starts
# a new one if there is none. Output goes to launcher.log, so a failed start leaves a reason.
cd "$(dirname "$(readlink -f "$0")")/.." || exit 1
# Twitch chat (Bits -> pain/pleasure and steering) switches on by itself once the
# bot's credentials exist; without them the game runs exactly as before.
# How much thinking time the fly gets per game frame: the game is as fast as
# the brain lets it be, so this is the dial between "normal speed stream" and
# "fly at full speed". FLYBRAIN_SPEED overrides it for one run.
# Threading, decided by measuring the LIVE game rather than the brain alone.
# Spinning threads (OMP_WAIT_POLICY=ACTIVE) halve the cost of a step when the
# machine is otherwise idle -- and are the WORST choice here, because they
# starve the window and the stream relay of the cores they need. Measured, as
# delivered game speed: 4 PASSIVE 0.759x, 3 PASSIVE 0.736x, 2 ACTIVE 0.710x,
# 4 ACTIVE 0.665x, 3 ACTIVE 0.640x. Benchmark the whole system, not the part
# being optimised.
#
# Re-measured 2026-09-26 after the window gained the brain labels, chat feed,
# tug-of-war and diary: 2 PASSIVE 0.770x, 4 PASSIVE 0.717x, 1 PASSIVE 0.609x.
# Two threads now beat four, because the window needs the cores the brain was
# taking. Re-run this comparison whenever the drawing gets heavier.
export OMP_NUM_THREADS=2 OMP_WAIT_POLICY=PASSIVE OMP_PROC_BIND=close OMP_PLACES=cores

# The window is drawn, encoded and served at this rate. Measured A/B on the
# live stream: 15 fps gave game 0.705x, 20 fps gave 0.687x and a delivered
# 19.9 fps -- a third more motion for 2.6% of the fly's speed, which is worth
# it now that the stream is video-only and smoothness is all a viewer gets.

TWITCH=""
[ -f "$HOME/.config/flybrain/twitch.json" ] && TWITCH="--twitch"
# Keep the game up for a live stream: if it dies for any reason (a crash, the
# out-of-memory killer) it comes back on its own, resuming from its last
# autosave, while the relay shows "back in a moment". Closing the window or
# pressing q/esc exits cleanly (status 0) and stops for real.
# Deploying a code change: `kill -USR1 <the game's pid>` (or play/restart.sh).
# The game saves exactly as it does on a clean exit and leaves with this
# status, which means "bring me back now" rather than "the stream is over".
# SIGTERM exits 0 and stops this loop, which is correct for stopping and
# wrong for deploying -- that mistake cost a minute of dead stream.
RESTART_STATUS=75
while true; do
    "$HOME/.local/bin/flybrain" play --continue --serve 8766 --speed "${FLYBRAIN_SPEED:-1.0}" \
        --fps "${FLYBRAIN_FPS:-20}" $TWITCH >> play/launcher.log 2>&1
    status=$?
    [ "$status" -eq 0 ] && break
    if [ "$status" -eq "$RESTART_STATUS" ]; then
        echo "$(date '+%F %T') restarting for new code" >> play/launcher.log
        continue
    fi
    echo "$(date '+%F %T') game exited with status $status -- restarting in 5 s" >> play/launcher.log
    sleep 5
done
