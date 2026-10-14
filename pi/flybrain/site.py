"""Who runs this installation: channel names, account ids, invite links.

Kept out of the code (2026-10-13, for the open-source release) so a clone runs
anyone's stream. Read once, in this order, first hit wins per key:
  1. environment: FLYBRAIN_<KEY> (upper case), e.g. FLYBRAIN_TWITCH_CHANNEL
  2. the file named by FLYBRAIN_SITE, else ~/.config/flybrain/site.json
  3. config/site.example.json in the repo (placeholder values)
None of these are secrets -- tokens and keys live in their own files under
~/.config/flybrain/ (see config/README.md) and are never read from here.
"""
import json
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SITE_F = os.environ.get("FLYBRAIN_SITE") or os.path.expanduser("~/.config/flybrain/site.json")
EXAMPLE_F = os.path.join(_REPO, "config", "site.example.json")


def _read(path):
    try:
        with open(path) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


_file = _read(SITE_F)
_example = _read(EXAMPLE_F)


def get(key, default=None):
    env = os.environ.get("FLYBRAIN_" + key.upper())
    if env is not None:
        return env
    if key in _file:
        return _file[key]
    return _example.get(key, default)


TWITCH_CHANNEL = str(get("twitch_channel", "examplechannel")).lower()
TWITCH_BROADCASTER_ID = str(get("twitch_broadcaster_id", ""))
# Twitch user ids allowed to use !menu (the broadcaster, unless widened)
_admins = get("twitch_admin_ids", None)
ADMIN_IDS = set(_admins.split(",") if isinstance(_admins, str) else (_admins or [TWITCH_BROADCASTER_ID])) - {""}
TWITCH_EXT_CLIENT_ID = str(get("twitch_ext_client_id", ""))
YOUTUBE_HANDLE = str(get("youtube_handle", ""))
DISCORD_INVITE = str(get("discord_invite", ""))           # e.g. "discord.gg/abc123"

TWITCH_URL = f"twitch.tv/{TWITCH_CHANNEL}"
YOUTUBE_URL = f"youtube.com/@{YOUTUBE_HANDLE}" if YOUTUBE_HANDLE else ""
