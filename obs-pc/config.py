"""Site settings for the OBS-PC side. Everything specific to one streamer lives in
config.json next to this file (copy config.example.json and fill it in); secrets live in
their own git-ignored files (panel_token.txt, panel_access.txt, dbot.txt, discord_ids.json)."""
import json
import pathlib

HERE = pathlib.Path(__file__).resolve().parent

DEFAULTS = {
    "pi_host": "raspberrypi.local",      # the Pi running the brain, game and relay
    "panel_port": 8777,
    "twitch_channel": "",                # e.g. "mychannel" (used for links and labels)
    "twitch_broadcaster_id": "",         # numeric Twitch user id
    "youtube_channel_id": "",            # UC... (Live Control Room link)
    "youtube_handle": "",                # e.g. "@mychannel"
    "instagram": "",                     # handle, optional
    "obs_collection": "Flybrain",
    "obs_scene": "Fly Brain v2",
    "maintenance": [["07:40", 120], ["19:40", 120]],   # daily stream restarts: [HH:MM, seconds down]
    "clips_dir": "",                     # where OBS saves replay clips (shown in the panel)
}


def load():
    cfg = dict(DEFAULTS)
    try:
        cfg.update(json.loads((HERE / 'config.json').read_text(encoding='utf-8')))
    except FileNotFoundError:
        pass
    return cfg


CFG = load()
