#!/usr/bin/env bash
# Start the game after a reboot or power cut (Ian, 2026-10-06: "make sure all
# of this is able to self manage"). Run by ~/.config/autostart at desktop login
# (the Pi auto-logs in). The relay, Discord poster, YouTube promoter, watchdog
# and Instagram timer are systemd user services and come back on their own;
# the game needs the desktop, so it starts here.
#
# Never a second copy: two games fight over the save and the Twitch token.
# Match on the command NAME, never `pgrep -f` (see play/restart.sh).
sleep 20                                    # let the desktop and network settle
if ps -eo comm=,args= | awk '$1 == "python3" && /flybrain\.py play/' | grep -q . ||
   ps -eo comm=,args= | awk '$1 == "bash" && /play\/launch\.sh/' | grep -q .; then
    echo "$(date '+%F %T') autostart: a game or launcher is already running; not starting another" \
        >> $HOME/flybrain/play/launcher.log
    exit 0
fi
echo "$(date '+%F %T') autostart: starting the game after login" >> $HOME/flybrain/play/launcher.log
cd $HOME/flybrain && exec bash play/launch.sh
