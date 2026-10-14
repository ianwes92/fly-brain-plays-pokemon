"""Twitch chat -> the fly, paid with Bits.

Teaching the fly is free; driving it costs Bits.

  !pain  !pleasure       free, anyone, on a cooldown (see FREE_GLOBAL_S and
                         FREE_USER_S) so chat cannot hold the fly down
  !up !down !left        free, always, from anybody: every viewer's key goes
  !right !a !b           straight to the game, all at once, Twitch Plays
                         Pokemon style. These are ADDED to what the fly is
                         doing (it keeps walking and deciding throughout), so
                         chat works with the animal or fights it. Buying
                         steering takes the controls outright for its seconds
                         -- paid always outranks free -- and when it runs out
                         chat has them back.
  cheer50 steer          10 Bits buys 1 second of control (cheer50 = 5 s)
  cheer100 pain          a cheer that names pain or pleasure still counts --
                         one press per 100 Bits, no cooldown, so nobody who
                         cheers for it gets nothing for their money

Presses are the same burst the window's buttons deliver: they teach the
mushroom body, move the mood bar and make the piezo speak. A cheer that buys
several is QUEUED and delivered STIM_GAP_S apart, so paid presses are never
dropped and never land as one continuous shock.

Control time is exclusive: while a viewer's seconds run, their !up !down
!left !right !a !b drive the fly and everyone else's are ignored. Buyers wait
in line, first come first served; a viewer who buys again while in line or
steering has the time added to their own turn.

A cheer that names nothing (or names a stimulus with fewer Bits than one press
costs) triggers nothing -- it is just a cheer, thanked on screen. Nobody's
money does something they did not ask for.

The Bits amount comes from the message's IRC tags (twitch.tv/tags), the same
number Twitch itself counts, never from the text.

Credentials live in ~/.config/flybrain/twitch.json, readable only by you (the
file is refused otherwise), never printed or logged. `flybrain twitch-login`
writes it: Twitch's device-code flow, so the Pi shows a short code, you approve
it on any phone or PC, and no secret is ever typed or pasted. Those tokens
expire after a few hours, which would drop the bot mid-stream, so the bridge
checks the token before every connection and renews it with the refresh token
Twitch issued alongside it, saving the new pair. Standard library
only: IRC over TLS to irc.chat.twitch.tv, PINGs answered, reconnecting with
backoff, in its own thread; the window drains a queue and owns all the paid
bookkeeping (PaidActions), so there is no locking and no network waits in the
game loop.
"""
import json
import os
import queue
import urllib.error
import urllib.parse
import urllib.request
import socket
import ssl
import stat
import threading
import time
from collections import deque

CONFIG = os.path.expanduser("~/.config/flybrain/twitch.json")
HOST, PORT = "irc.chat.twitch.tv", 6697

BITS_PER_PRESS = 100          # pain or pleasure
BITS_PER_SECOND = 10          # steering
STIM_GAP_S = 1.0              # between queued presses -- chat should see its
                              # press land, not wait in a line it cannot see
# Ian, 2026-10-08: "I want people to be able to spam them" -- no per-viewer gap
# on !left/!right/!up/!down/!a/!b (was 0.15 s). Twitch's own chat limits and the
# game's frame rate are the only ceiling. Free !pain/!pleasure keep theirs below.
CONTROL_PACE_S = 0.0          # between one controller's commands; 0 = unlimited
# Loosened 2026-10-08 (Ian: "loosen them"; were 4 s shared, 25 s per viewer):
# spammier, but one viewer still cannot hold the fly in continuous pain.
FREE_GLOBAL_S = 0.0           # between free presses of the same kind, from anyone
FREE_USER_S = 5.0             # between one viewer's free presses
# Chat has the controls all the time. There is no window: a viewer who types
# and gets nothing cannot tell a closed window from a broken bot, and the only
# thing that takes the controls away is somebody paying for them.
TALLY_S = 60.0                # how far back the on-screen press tally counts
TOKEN_CHECK_S = 900.0   # how often a connected bot re-checks its token
TOKEN_MARGIN_S = 1800.0 # ...and how much life left is too little to keep
                        # The margin MUST be larger than the interval, or a
                        # token can pass from "fine" to expired between two
                        # checks and never be caught. Renewing early is free.
PRESS_MS = 700.0              # how long one viewer's key is held -- about one step,
                              # and it is ADDED to what the fly is doing, not
                              # instead of it (Game.nudge), so the fly keeps
                              # walking and chat works with it or against it.
                              # 300 ms (one step) was too short to SEE through
                              # the stream's delay -- by the time a viewer saw
                              # anything it was over. 700 ms moves two or three
                              # tiles, which reads as "my press did that".
BOARD = os.path.expanduser("~/.config/flybrain/leaderboard.json")
BOARD_KEEP = 50               # names kept per board, so the file stays small
SAY_GAP_S = 3.0               # between the fly's own messages (Twitch limits these)
SAY_QUEUE = 5                 # if more than this is waiting, the news is stale: drop it
SAY_MAX = 400                 # characters Twitch will carry

STIM_WORDS = {"pain": "pain", "shock": "pain", "pleasure": "pleasure", "treat": "pleasure",
              "reward": "pleasure"}
STEER_WORDS = {"steer", "drive", "control"}
CONTROLS = {"!up": "up", "!down": "down", "!left": "left", "!right": "right",
            "!a": "a", "!b": "b"}
# !menu: the streamer takes the controls outright to work the game menu --
# teaching HMs is impossible without it, and the fly is deliberately never
# given START. Gated on Twitch USER ID, not name: names can be changed, ids
# cannot, and this command can save, change options and reorder the party.
# The broadcaster, from flybrain/site.py (site.json). Widen only on their say-so.
from .site import ADMIN_IDS                                     # noqa: E402
PANEL_STIM_S = 1.0            # the panel's pain/pleasure: at most one a second
PANEL_ACTIONS = ("take", "press", "release", "nudge", "stim", "chat")
ADMIN_IDLE_S = 300.0          # released automatically after 5 min with no command:
                              # forgetting to release would otherwise freeze the
                              # stream for everyone until somebody noticed
ADMIN_MAX_S = 900.0           # ...and after 15 min regardless. The idle timer only
                              # protects against forgetting if nothing wrongly
                              # refreshes it; a hard ceiling protects against that
                              # too, and against bugs nobody has thought of. Ian
                              # can simply !menu again.
ADMIN_OFF = {"off", "done", "exit", "close", "stop", "end"}
ADMIN_BANNER = "STREAMER IS AT THE CONTROLS - teaching the fly a new move"
FREE_STIM = {"!" + w: kind for w, kind in STIM_WORDS.items()}
# Questions the fly will answer in chat. Somebody arriving asks "what is it
# doing?" and until now only the broadcaster could tell them.
ASK = {"!fly": "fly", "!team": "team", "!story": "story", "!help": "help",
       "!brain": "fly", "!mood": "fly", "!what": "help",
       "!starter": "starter", "!starters": "starter"}   # Ian, 2026-10-08: the scoreboard
ASK_GLOBAL_S = 6.0            # between answers to anybody
ASK_USER_S = 45.0             # between answers to the same viewer
PRICES = ("!left !right !up !down !a !b  -  free, always: drive the fly    "
          "!pain / !pleasure  -  free, teach it",
          f"cheer{BITS_PER_SECOND * 5} steer = 5 s of control ({BITS_PER_SECOND} bits per second)")

# Shown one at a time, under the game. Somebody arriving sees a sprite and
# some panels and no reason to care: these say what this is and what they can
# do about it, without needing anyone to explain it in chat.
EXPLAINERS = (
    "a real fly's brain plays this -- 139,255 scanned neurons",
    "nobody scripted it. it sees, decides, walks",
    "your press is ADDED to what the fly already wants",
    "!pain / !pleasure teach it -- real dopamine, real learning",
    "the map on the right is its brain, live",
    "it has never played this before, and it cannot read",
    # Channel points: two rewards went live on 2026-10-02 and nothing told a
    # viewer they existed. Points cost nothing but watching, so they belong
    # where newcomers look; the free chat commands keep the title.
    "spend channel points to give the fly pleasure or pain",
)
EXPLAIN_S = 11.0              # how long each line stays up

# The stream's Discord, mentioned in chat once an hour while live.
# PERMANENT, UNLIMITED invite -- Ian's older ones expire in 30 days, and a dead
# link posted hourly is worse than no link at all, so this one is pinned here
# rather than picked up from wherever it was last seen.
from .site import DISCORD_INVITE                                # noqa: E402
# Whether the channel is actually live is already known by the Discord poster,
# which asks Twitch once a minute. Reading its state beats running a second
# poller on the same token.
POSTER_STATE = os.path.expanduser("~/.config/flybrain/discord_state.json")
DISCORD_EVERY_S = 3600.0      # from the last one, not on the wall-clock hour:
                              # otherwise somebody arriving at :55 sees it
                              # twice in five minutes.
# Rotated, because the same string every hour reads like a bot. In the fly's
# own voice, like everything else the bridge says.
DISCORD_LINES = (
    # obsfly's wording, 2026-10-02: the stream is advertised as 24/7. Still
    # gated on the stream being live (discord_due) -- "24/7" is the claim,
    # not a licence to advertise into an offline channel.
    f"the fly plays Pokemon 24/7. catches, badges and live alerts: {DISCORD_INVITE}",
    f"this stream never stops. the fly's Discord: {DISCORD_INVITE}",
    f"139,255 neurons, playing around the clock. {DISCORD_INVITE}",
)


def discord_line(n):
    """One of the rotating lines, chosen by how many have been posted."""
    return DISCORD_LINES[n % len(DISCORD_LINES)]


def discord_due(now, last_at, live, heard_since):
    """Should the Discord line go out?

    Three gates, each for a reason worth keeping:
      live        -- posting hourly into an offline channel is advertising to
                     an empty room, and it is all anyone would see in the VOD.
      the hour    -- measured from the LAST post, not the clock.
      heard_since -- if not one person has typed since the last one, there is
                     nobody to advertise to. Cheap to check, and it keeps the
                     VOD from looking like a bot talking to itself.
    """
    if not live:
        return False
    if heard_since <= 0:
        return False
    return (now - last_at) >= DISCORD_EVERY_S
STEER_HELP = "type !up !down !left !right !a !b"


ID_BASE = "https://id.twitch.tv/oauth2"
API_BASE = "https://api.twitch.tv/helix"
TITLE_EVERY_S = 300.0         # Twitch is fine with more; viewers are not
# chat:read takes in commands and cheers; chat:edit lets the fly answer in
# chat (milestones). A token
# minted with chat:read alone still CONNECTS and still reads, and its sends
# vanish without an error -- the bot looked fine and said nothing.
SCOPES = ("chat:read", "chat:edit", "channel:manage:broadcast", "clips:edit",
          "channel:manage:redemptions")   # channel points -> pain/pleasure (eventsub.py)


def _http(method, url, form=None, headers=None, timeout=15):
    """(status, parsed json) -- Twitch answers errors with JSON bodies too."""
    data = urllib.parse.urlencode(form).encode() if form is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def validate(access, base=ID_BASE):
    """{'login', 'expires_in', ...} for a live token, or None."""
    code, body = _http("GET", f"{base}/validate",
                       headers={"Authorization": f"OAuth {access}"})
    return body if code == 200 else None


def save_config(cfg, path=CONFIG):
    """Write atomically, private from the first byte."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f, indent=1)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def device_login(client_id, channel, path=CONFIG, base=ID_BASE, show=print,
                 sleep=time.sleep):
    """Twitch device-code flow: show a code, wait for approval, save the tokens."""
    code, dev = _http("POST", f"{base}/device",
                      {"client_id": client_id, "scopes": " ".join(SCOPES)})
    if code != 200:
        raise SystemExit(f"Twitch refused the login request ({code}): {dev.get('message', dev)}\n"
                         "Check the Client ID, and that the app's Client Type is 'Public'.")
    show(f"\n  On any phone or computer, open:  {dev['verification_uri']}\n"
         f"  and enter the code:              {dev['user_code']}\n"
         f"  (signed in as the account the bot should use; expires in "
         f"{int(dev['expires_in']) // 60} min)\n")
    interval = float(dev.get("interval", 5))
    deadline = time.monotonic() + float(dev["expires_in"])
    while time.monotonic() < deadline:
        sleep(interval)
        code, tok = _http("POST", f"{base}/token", {
            "client_id": client_id, "scopes": " ".join(SCOPES),
            "device_code": dev["device_code"],
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
        if code == 200 and tok.get("access_token"):
            who = validate(tok["access_token"], base) or {}
            # Keep whatever else was set (announce, and anything added later):
            # logging in again to widen the scopes should not quietly undo the
            # settings that made the login worth widening.
            try:                                   # may be the very first login
                cfg = dict(load_config(path) or {})
            except SystemExit:
                cfg = {}
            cfg.update({"nick": who.get("login", channel),
                        "channel": channel.lstrip("#").lower(),
                        "oauth": "oauth:" + tok["access_token"],
                        "refresh_token": tok.get("refresh_token", ""),
                        "client_id": client_id})
            save_config(cfg, path)
            show(f"  approved -- the bot reads chat as '{cfg['nick']}' in #{cfg['channel']}.\n"
                 f"  saved to {path} (private). Launch with --twitch, or just use the desktop shortcut.")
            return cfg
        if tok.get("message") not in ("authorization_pending", "slow_down"):
            raise SystemExit(f"login failed: {tok.get('message', tok)}")
        if tok.get("message") == "slow_down":
            interval += 2
    raise SystemExit("the code expired before it was approved; run twitch-login again")


TITLE_MEMO = os.path.expanduser("~/.config/flybrain/title.json")


def remember_title(title, path=TITLE_MEMO):
    """Keep the last title WE set, across restarts.

    Without this the fly forgets what it wrote, and the first update after a
    restart cannot tell the broadcaster's own correction from its own work --
    so it overwrites it, again, which is exactly the complaint."""
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump({"title": title}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except OSError:
        pass


def recall_title(path=TITLE_MEMO):
    try:
        with open(path) as f:
            return (json.load(f) or {}).get("title") or ""
    except (OSError, ValueError):
        return ""


def current_title(cfg, base=API_BASE, id_base=ID_BASE):
    """What the channel says right now, or None if it cannot be read."""
    access = cfg.get("oauth", "").split(":", 1)[-1]
    try:
        who = validate(access, id_base)
    except (OSError, ValueError):
        # 2026-10-08 13:25: a TLS handshake timeout here escaped into the
        # play loop and the whole game exited. A title check is never worth that.
        return None
    if not who or not who.get("user_id"):
        return None
    req = urllib.request.Request(
        f"{base}/channels?broadcaster_id={who['user_id']}",
        headers={"Client-Id": cfg.get("client_id", ""),
                 "Authorization": f"Bearer {access}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            rows = (json.loads(r.read() or b"{}") or {}).get("data") or []
        return rows[0].get("title") if rows else None
    except (OSError, ValueError):
        return None


def set_title(cfg, title, base=API_BASE, id_base=ID_BASE):
    """Put the fly's progress in the stream title. Returns (ok, why).

    The browse page is the only place most people will ever see this stream,
    and "Just Chatting" tells them nothing. Needs channel:manage:broadcast,
    which a token minted before that scope existed will not have -- and Twitch
    says so plainly (401), which is worth passing back rather than swallowing.
    """
    access = cfg.get("oauth", "").split(":", 1)[-1]
    try:
        who = validate(access, id_base)
    except (OSError, ValueError) as e:            # network trouble: try again next time
        return False, f"{type(e).__name__}"
    if not who or not who.get("user_id"):
        return False, "the login could not be checked"
    if "channel:manage:broadcast" not in (who.get("scopes") or []):
        return False, ("this login predates the title feature -- run "
                       "`flybrain twitch-login` again to add channel:manage:broadcast")
    url = f"{base}/channels?broadcaster_id={who['user_id']}"
    body = json.dumps({"title": title[:140]}).encode()
    req = urllib.request.Request(url, data=body, method="PATCH", headers={
        "Client-Id": cfg.get("client_id", ""),
        "Authorization": f"Bearer {access}",
        "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return (200 <= r.status < 300), f"HTTP {r.status}"
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = (json.loads(e.read() or b"{}") or {}).get("message", "")
        except ValueError:
            pass
        return False, f"HTTP {e.code} {detail}".strip()
    except OSError as e:
        return False, f"{type(e).__name__}"


CLIP_GAP_S = 150.0            # a clip is 30 s of stream; do not stack them
CLIP_READY_S = 20.0           # Twitch needs a moment before the link plays


def make_clip(cfg, base=API_BASE, id_base=ID_BASE):
    """Cut a clip of what just happened. Returns (url, why).

    A badge or an evolution takes hours of watching to arrive and then scrolls
    away in five seconds. A clip is the only thing the stream produces that
    outlives the broadcast, and it captures the PRECEDING half-minute, so it
    has to be cut at the moment the news lands, not later.

    Needs clips:edit, and needs the channel to actually be live -- Twitch
    answers 404 when it is not, which is a normal answer here rather than a
    fault, since the game runs whether or not anybody is broadcasting.
    """
    access = cfg.get("oauth", "").split(":", 1)[-1]
    try:
        who = validate(access, id_base)
    except (OSError, ValueError) as e:            # network trouble: no clip, no crash
        return None, f"{type(e).__name__}"
    if not who or not who.get("user_id"):
        return None, "the login could not be checked"
    if "clips:edit" not in (who.get("scopes") or []):
        return None, ("this login predates clips -- run `flybrain twitch-login` "
                      "again to add clips:edit")
    req = urllib.request.Request(
        f"{base}/clips?broadcaster_id={who['user_id']}", data=b"", method="POST",
        headers={"Client-Id": cfg.get("client_id", ""),
                 "Authorization": f"Bearer {access}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            rows = (json.loads(r.read() or b"{}") or {}).get("data") or []
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None, "not live"
        detail = ""
        try:
            detail = (json.loads(e.read() or b"{}") or {}).get("message", "")
        except ValueError:
            pass
        return None, f"HTTP {e.code} {detail}".strip()
    except OSError as e:
        return None, f"{type(e).__name__}"
    if not rows or not rows[0].get("id"):
        return None, "Twitch returned no clip"
    return f"https://clips.twitch.tv/{rows[0]['id']}", ""


TITLE_MAX = 140               # Twitch's limit
# Ian's format, 2026-10-02 (via obsfly): advertised as 24/7, kept automated.
# The call to action is his example's shorter wording -- it names the FREE chat
# commands (channel points cost points, so they go on screen instead) and buys
# back 14 of the characters the longer hook spends.
TITLE_HEAD = "Fruit Fly Plays Pokemon 24/7"
TITLE_CTA = "!pain !pleasure to train it"
# Which gym each badge is, so a badge can be told as something that HAPPENED
# somewhere rather than as a number. FireRed order.
# Where the fly IS, by the game's own grouping (gMapHeader.regionMapSectionId).
#
# From pret/pokefirered, src/data/region_map/region_map_sections.json: a flat
# ordered list, so the array index IS the constant. The Kanto block starts at
# 88 (MAPSEC_PALLET_TOWN, 0x58) and runs to 196. Relayed and cross-checked by
# obsfly; the geography hangs together (consecutive routes in numeric order,
# dungeons grouped after them), and id 103 was separately confirmed against
# the screen -- a rocky grass route, with the fly past Brock and Pewter, which
# is Route 3 and not the Route 2 first reported.
#
# Ids 0-87 are Hoenn, inherited from Emerald, and should never appear here. If
# one does, it is a sign something is wrong, so it stays unnamed rather than
# putting a Hoenn town in the title of a Kanto game.
PLACES = {
    88: "Pallet Town", 89: "Viridian City", 90: "Pewter City", 91: "Cerulean City",
    92: "Lavender Town", 93: "Vermilion City", 94: "Celadon City", 95: "Fuchsia City",
    96: "Cinnabar Island", 97: "Indigo Plateau", 98: "Saffron City",
    # 99 and 100 are the Route 4 and Route 10 Pokemon Centers. They are real
    # map sections and they are deliberately ABSENT: they are buildings, and
    # "reached the Route 4 Pokemon Center" is a healing stop, not progress.
    101: "Route 1", 102: "Route 2", 103: "Route 3", 104: "Route 4", 105: "Route 5",
    106: "Route 6", 107: "Route 7", 108: "Route 8", 109: "Route 9", 110: "Route 10",
    111: "Route 11", 112: "Route 12", 113: "Route 13", 114: "Route 14", 115: "Route 15",
    116: "Route 16", 117: "Route 17", 118: "Route 18", 119: "Route 19", 120: "Route 20",
    121: "Route 21", 122: "Route 22", 123: "Route 23", 124: "Route 24", 125: "Route 25",
    126: "Viridian Forest", 127: "Mt. Moon", 128: "the S.S. Anne",
    129: "Underground Path", 130: "Underground Path", 131: "Diglett's Cave",
    132: "Victory Road", 133: "Rocket Hideout", 134: "Silph Co.",
    135: "Pokemon Mansion", 136: "the Safari Zone", 137: "the Pokemon League",
    138: "Rock Tunnel", 139: "Seafoam Islands", 140: "Pokemon Tower",
    141: "Cerulean Cave", 142: "the Power Plant",
    # Kanto ends at 142. 143 is MAPSEC_ONE_ISLAND, where Sevii begins, and the
    # Sevii names are left out on purpose: this run is Kanto. They are a
    # one-line addition from the decomp if the fly ever gets that far.
}

GYMS = (("Brock", "Pewter City"), ("Misty", "Cerulean City"),
        ("Lt. Surge", "Vermilion City"), ("Erika", "Celadon City"),
        ("Koga", "Fuchsia City"), ("Sabrina", "Saffron City"),
        ("Blaine", "Cinnabar Island"), ("Giovanni", "Viridian City"))


def _nice(name):
    """RATTATA -> Rattata, MR. MIME -> Mr. Mime."""
    return " ".join(w.capitalize() for w in str(name).split())


def last_catch(diary):
    """The species most recently caught, from the fly's own diary."""
    for _, kind, text in reversed(getattr(diary, "entries", []) or []):
        if kind == "caught" and text.upper().startswith("CAUGHT "):
            return _nice(text[7:].rstrip("!").strip())
    return None


TITLE_NOTICE = os.path.expanduser("~/.config/flybrain/title_notice.json")


def title_notice(path=TITLE_NOTICE, today=None):
    """A temporary segment for the title, e.g. the run reset: {"what": "RESET",
    "on": "2026-10-04"}. Worded from the date, so it can never go stale --
    "RESET TOMORROW" the day before, "RESET TODAY" on the day, and nothing at
    all after it. A notice still up the day after makes the channel look
    unattended, the opposite of what announcing a reset is for. The reset
    itself also deletes the file (flybrain/reset.py)."""
    import datetime
    try:
        with open(path) as f:
            d = json.load(f)
        what = str(d["what"]).strip().upper()
        on = datetime.date.fromisoformat(d["on"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    today = today or datetime.date.today()
    days = (on - today).days
    if days < 0 or not what:
        return None
    if days == 0:
        return f"{what} TODAY"
    if days == 1:
        return f"{what} TOMORROW"
    return f"{what} {on.strftime('%a %b')} {on.day}".upper()


def stream_title(progress, team, diary=None, place=None, notice=None, run=None):
    """The browse page line: what just HAPPENED, not how much has accumulated.

    Counters ("1 badge, 3 caught") tell a stranger nothing and never change in
    a way worth re-reading. A species name and a gym leader do. Four segments;
    when they will not fit, whole ones are dropped -- the head is what makes
    the stream findable and the call to action is what makes it joinable, so
    those two are never the ones to go.
    """
    p = progress or {}
    # "Run 4 · " first (Ian, 2026-10-08): returning viewers see it's a new run.
    # None = read run.json; tests pass "" to leave it out.
    if run is None:
        from .runinfo import prefix
        run = prefix()
    live = [m for m in (team or []) if not m.get("egg")]
    # The strongest, not whoever happens to be walking in front. The party
    # lead is often a low-level thing the fly just caught, and naming that
    # instead of the starter reads as a mistake to anyone who knows the game.
    star = max(live, key=lambda m: m["level"]) if live else None
    lead = f"{_nice(star['name'])} lv {star['level']}" if star else ""
    catch = last_catch(diary)
    badges = int(p.get("badges", 0) or 0)
    gym = ""
    if 0 < badges <= len(GYMS):
        # The gym's TOWN is deliberately not named here. "Beat Brock @ Pewter
        # City!" is true and still misleads: it is the only location-shaped
        # phrase in the line, so on the browse page it reads as where the fly
        # IS -- and a milestone persists while the fly keeps walking, so that
        # reading is wrong permanently. Ian reported it as a bug twice, once by
        # editing the title by hand to say Mt. Moon. The badge is the news; the
        # place belongs to the live segment below.
        gym = f"Beat {GYMS[badges - 1][0]}!"
    here = PLACES.get(place)
    where_now = f"@ {here}" if here else ""

    def build(with_gym=True, with_catch=True, with_place=True):
        two = ", ".join(x for x in (lead, f"Caught a {catch}" if catch and with_catch else "") if x)
        # A notice (title_notice) goes straight after the hook, the second
        # thing read, and is never dropped: while it is up, it IS the news.
        parts = [TITLE_HEAD, notice or "", two, where_now if with_place else "",
                 gym if with_gym else "", TITLE_CTA]
        return run + " - ".join(x for x in parts if x)

    # Drop order when it will not fit: the milestone goes before the location,
    # because the location changes often and shows the stream is live, while a
    # badge is the same news it was hours ago. The hook and the call to action
    # never go.
    for attempt in (build(), build(with_gym=False), build(with_gym=False, with_catch=False),
                    build(with_gym=False, with_catch=False, with_place=False),
                    run + " - ".join(x for x in (TITLE_HEAD, notice, TITLE_CTA) if x)):
        if len(attempt) <= TITLE_MAX:
            return attempt
    return f"{run}{TITLE_HEAD} - {TITLE_CTA}"[:TITLE_MAX]


def refresh(cfg, path=CONFIG, base=ID_BASE):
    """Swap the refresh token for a fresh pair and save it. Returns cfg."""
    code, tok = _http("POST", f"{base}/token", {
        "client_id": cfg["client_id"], "grant_type": "refresh_token",
        "refresh_token": cfg["refresh_token"]})
    if code != 200 or not tok.get("access_token"):
        raise RuntimeError(f"token refresh refused ({code}): {tok.get('message', '')}")
    cfg["oauth"] = "oauth:" + tok["access_token"]
    cfg["refresh_token"] = tok.get("refresh_token", cfg["refresh_token"])
    save_config(cfg, path)
    return cfg


def load_config(path=CONFIG):
    """Read the bot's credentials, refusing a file others can read."""
    if not os.path.exists(path):
        raise SystemExit(f"no Twitch config at {path}; see flybrain/twitch.py for the format")
    mode = os.stat(path).st_mode
    if mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise SystemExit(f"{path} is readable by others; run: chmod 600 {path}")
    with open(path) as f:
        cfg = json.load(f)
    for k in ("nick", "oauth", "channel"):
        if not cfg.get(k):
            raise SystemExit(f"{path} is missing '{k}'")
    if not cfg["oauth"].startswith("oauth:"):
        cfg["oauth"] = "oauth:" + cfg["oauth"]
    cfg["channel"] = cfg["channel"].lstrip("#").lower()
    return cfg


_UNESCAPE = {"\\s": " ", "\\:": ";", "\\\\": "\\", "\\r": "\r", "\\n": "\n"}


def _unescape(v):
    out, i = [], 0
    while i < len(v):
        two = v[i:i + 2]
        if two in _UNESCAPE:
            out.append(_UNESCAPE[two]); i += 2
        else:
            out.append(v[i]); i += 1
    return "".join(out)


def parse(line):
    """A chat line -> dict(user, name, text, bits), or None if not chat.

    Handles Twitch's tagged form: '@k=v;k=v :nick!nick@host PRIVMSG #chan :text'."""
    tags = {}
    if line.startswith("@"):
        raw, _, line = line[1:].partition(" ")
        for kv in raw.split(";"):
            k, _, v = kv.partition("=")
            tags[k] = _unescape(v)
    if " PRIVMSG " not in line or not line.startswith(":"):
        return None
    prefix, _, rest = line[1:].partition(" PRIVMSG ")
    user = prefix.split("!", 1)[0].lower()
    _, _, text = rest.partition(" :")
    try:
        bits = int(tags.get("bits", "0") or 0)
    except ValueError:
        bits = 0
    return {"user": user, "name": tags.get("display-name") or user,
            "text": text.strip(), "bits": max(bits, 0), "uid": tags.get("user-id", "")}


def interpret(msg):
    """A parsed chat message -> an event tuple, or None.

      ('stim', kind, count, name)         presses bought with a cheer
      ('free', kind, user, name)          a free !pain / !pleasure
      ('steer', seconds, user, name)      paid control time
      ('cheer', bits, name)               a cheer that bought nothing
      ('ask', topic, user, name)          a question for the fly to answer
      ('control', key, user)              a steering command: honoured for
                                          whoever is steering right now, and
                                          for anybody when nobody has bought it
      ('admin', on, user, name, uid)      !menu / !menu off (checked in feed)
    """
    words = msg["text"].lower().split()
    if msg["bits"] > 0:
        for w in words:
            w = w.strip("!.,")
            if w in STIM_WORDS:
                n = msg["bits"] // BITS_PER_PRESS
                if n >= 1:
                    return ("stim", STIM_WORDS[w], n, msg["name"])
                return ("cheer", msg["bits"], msg["name"])
            if w in STEER_WORDS:
                return ("steer", msg["bits"] / BITS_PER_SECOND, msg["user"], msg["name"])
        return ("cheer", msg["bits"], msg["name"])
    if words and words[0] == "!menu":
        on = not (len(words) > 1 and words[1] in ADMIN_OFF)
        return ("admin", on, msg["user"], msg["name"], msg.get("uid", ""))
    if words and words[0] == "!start":
        return ("control", "start", msg["user"])     # honoured only for the admin
    if words and words[0] in ASK:
        return ("ask", ASK[words[0]], msg["user"], msg["name"])
    if words and words[0] in CONTROLS:
        return ("control", CONTROLS[words[0]], msg["user"])
    if words and words[0] in FREE_STIM:
        return ("free", FREE_STIM[words[0]], msg["user"], msg["name"])
    return None


def rehearsal_line(user, bits, text):
    """A cheer exactly as Twitch's IRC delivers one, for rehearsing the Pi's
    half of the path (the broadcaster cannot cheer in their own channel).
    The display name is marked TEST so a rehearsal on stream is never
    mistaken for a real viewer's money."""
    user = "".join(ch for ch in user.lower() if ch.isalnum() or ch == "_") or "tester"
    return (f"@badge-info=;badges=bits/1;bits={int(bits)};color=;display-name=TEST\\s{user};"
            f"emotes=;id=rehearsal;mod=0;room-id=0;subscriber=0;tmi-sent-ts=0;turbo=0;"
            f"user-id=0;user-type= :{user}!{user}@{user}.tmi.twitch.tv PRIVMSG #rehearsal :{text}")


def broadcaster_login(path=CONFIG):
    """The channel owner's Twitch login, as chat would credit them."""
    try:
        with open(path) as f:
            ch = json.load(f).get("channel") or ""
    except (OSError, ValueError):
        ch = ""
    from .site import TWITCH_CHANNEL
    return ch.lstrip("#").lower() or TWITCH_CHANNEL


class PaidActions:
    """What paid chat is owed, released in order. Owned by the window thread.

    feed(event) takes what the bridge parsed; tick() returns what should
    happen now: a list of ('stim', kind, name) and ('control', key, name).
    """

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.stims = deque()             # (kind, name)
        self._next_stim = 0.0
        self.line = deque()              # [user, name, seconds] waiting to steer
        self.driver = None               # [user, name, until]
        self._last_cmd = {}
        self._pending_control = None     # the driver's latest command, until tick()
        self._pending_nudge = None       # chat's latest press, until tick()
        self._ask_at, self._ask_user = -1e9, {}
        self._free_kind = {}             # kind -> when anyone last got one free
        self._free_user = {}             # viewer -> when they last got one
        # Who has done what, for the leaderboard on screen.
        self.trainers = {}               # name -> presses delivered
        self.drivers = {}                # name -> seconds of steering bought
        # Chat's own driving: always on, unless somebody has bought control.
        self.last_press = None           # (key, user, when) for the caption
        self.presses = 0                 # how many have got through, ever
        self.press_log = deque(maxlen=400)   # (when, key) for the rolling tally
        self.chat_log = deque(maxlen=20)  # (when, text) -- what chat just did
        self.admin = None                # [login, name, idle deadline, hard deadline] during !menu
        self._admin_out = []             # ('admin', on, why) / ('tap', key) for tick()
        self.last_outcome = None         # what feed() did with the last event (journal)

    def feed(self, ev):
        """Take one parsed chat event. Returns a line to show the viewer, or
        None when there is nothing to say.

        Every outcome is reported, including refusals: a command that is
        silently dropped looks exactly like a broken bot from the other side
        of a ten-second stream delay, and the viewer tries again, and gives up.
        """
        kind = ev[0]
        # What became of it, for the input journal (attrib.Journal): "ok"
        # unless a branch below says why not.
        self.last_outcome = "ok"
        if kind == "admin":
            self.last_outcome = "admin"
            return self._admin(ev)
        if kind == "control" and self.admin is not None:
            _, key, user = ev
            if user != self.admin[0]:
                self.last_outcome = "refused_admin_held"
                return None              # the banner and the chat line already say why
            self.admin[2] = self.clock() + ADMIN_IDLE_S
            self._admin_out.append(("tap", key))
            return f"streamer: {key.upper()}"
        if kind == "control" and ev[1] == "start":
            self.last_outcome = "ignored"
            return None                  # START is the streamer's, inside !menu only
        if kind == "stim":
            _, what, n, name = ev[:4]
            self.stims.extend([(what, name)] * n)
            self.trainers[name] = self.trainers.get(name, 0) + n
            # cheered and channel-point stims show in the chat feed too (obsfly, 2026-10-09)
            self.chat_log.append((self.clock(), f"{name}  {what.upper()}" + (f" x{n}" if n > 1 else "")))
        elif kind == "free":
            # Free, so it is dropped rather than queued when it is too soon:
            # nobody is owed anything, and a queue of free presses would let
            # chat keep the fly frightened long after they stopped asking.
            _, what, user, name = ev
            now = self.clock()
            wait = FREE_GLOBAL_S - (now - self._free_kind.get(what, -1e9))
            if wait > 0:
                self.last_outcome = "cooldown_global"
                return f"{name}: {what} in {wait:.0f}s (someone just did)"
            wait = FREE_USER_S - (now - self._free_user.get(user, -1e9))
            if wait > 0:
                self.last_outcome = "cooldown_user"
                return f"{name}: your next press in {wait:.0f}s"
            self._free_kind[what], self._free_user[user] = now, now
            self.stims.append((what, name))
            self.trainers[name] = self.trainers.get(name, 0) + 1
            self.chat_log.append((now, f"{name}  {what.upper()}"))
            return f"{name}: {what.upper()}"
        elif kind == "steer":
            _, secs, user, name = ev
            now = self.clock()
            self.last_outcome = "queued"
            self.drivers[name] = self.drivers.get(name, 0.0) + secs
            if self.driver and self.driver[0] == user and now < self.driver[2]:
                self.driver[2] += secs
                return
            for entry in self.line:
                if entry[0] == user:
                    entry[2] += secs
                    return
            self.line.append([user, name, secs])
        elif kind == "control":
            _, key, user = ev
            now = self.clock()
            self._advance(now)
            if not self.driver:
                # No queue, no turns, no waiting for a window -- whoever types,
                # drives. The per-user pace is the only limit, and with no
                # window it is the ONLY thing stopping one viewer flooding the
                # input, so it stays.
                if now - self._last_cmd.get(user, -1e9) < CONTROL_PACE_S:
                    self.last_outcome = "paced"
                    return None
                self._last_cmd[user] = now
                self._pending_nudge = (key, user, PRESS_MS)
                self.last_press = (key, user, now)
                self.presses += 1
                self.press_log.append((now, key))
                self.chat_log.append((now, f"{user}  {key.upper()}"))
                return f"{user}: {key.upper()}"
            if self.driver[0] != user:
                self.last_outcome = "refused_driver"
                return (f"{self.driver[1]} bought the controls "
                        f"({max(0.0, self.driver[2] - now):.0f}s left)")
            if now - self._last_cmd.get(user, -1e9) < CONTROL_PACE_S:
                self.last_outcome = "paced"
                return None
            self._last_cmd[user] = now
            self.last_outcome = "ok_driver"
            self._pending_control = (key, self.driver[1])
            self.chat_log.append((now, f"{self.driver[1]}  {key.upper()}"))   # in the feed, as "full"
            return f"{self.driver[1]}: {key.upper()}"

    PANEL_USER = "streamer"          # the control panel, on screen and in logs

    def panel(self, action, key=None):
        """The Windows control panel (relay /panel/control). Returns (ok, note).

        Out-of-band by construction: presses and taps never reach the
        leaderboard (trainers/drivers count only paid stims and steering), and
        no button press enters the learning rule at all -- the fly learns only
        from dopamine. 'take' is exactly !menu's admin mode, with its banner,
        chat notices, idle timeout, hard ceiling and paid-steering refusal."""
        me = self.PANEL_USER
        held = self.admin is not None and self.admin[0] == me
        uid = next(iter(ADMIN_IDS))
        if action == "take":
            if held:
                return True, "already held by the panel"
            if self.admin is not None:
                return False, f"{self.admin[1]} holds control (!menu) -- release it first"
            note = self._admin(("admin", True, me, "streamer", uid))
            return self.admin is not None and self.admin[0] == me, note
        if action == "release":
            if not held:
                return False, "the panel does not hold control"
            return True, self._admin(("admin", False, me, "streamer", uid))
        if action == "press":
            if key not in set(CONTROLS.values()) | {"start"}:
                return False, f"unknown key {key!r}"
            if not held:
                return False, "take control first"
            self.admin[2] = self.clock() + ADMIN_IDLE_S
            self._admin_out.append(("tap", key))
            return True, f"streamer: {key.upper()}"
        if action == "chat":
            # Exactly as if the broadcaster typed the command in Twitch chat:
            # same code path, same credit (their own login, read from the
            # bot's config), same per-user pace -- shared with their real chat
            # presses, since it is the same user. START outside !menu is
            # ignored here just as it is in chat (the fly never opens the menu).
            if key not in set(CONTROLS.values()) | {"start"}:
                return False, f"unknown key {key!r}"
            if key == "start" and self.admin is None:
                return False, "START works only inside !menu / take, as in chat"
            note = self.feed(("control", key, broadcaster_login()))
            return note is not None, note or "not taken"
        if action == "nudge":
            if key not in set(CONTROLS.values()):
                return False, f"unknown key {key!r}"
            if self.admin is not None:
                return False, "control is held -- use press"
            note = self.feed(("control", key, me))
            return note is not None, note or "not taken"
        if action == "stim":
            # Works while the fly runs free: the point is to reward or punish
            # what IT did, not the streamer's own driving. Same path and size
            # as a viewer's free !pain/!pleasure (one burst through the paced
            # stim queue), but kept off the leaderboard and the "N people have
            # trained it" count. Unlike a press it changes the weights, so it
            # is limited to one a second.
            if key not in ("pain", "pleasure"):
                return False, f"unknown stim {key!r}"
            now = self.clock()
            wait = PANEL_STIM_S - (now - getattr(self, "_panel_stim_at", -1e9))
            if wait > 0:
                return False, f"one stim a second ({wait:.1f}s)"
            self._panel_stim_at = now
            self.stims.append((key, me))
            self.chat_log.append((now, f"{me}  {key.upper()}"))
            return True, f"{me}: {key.upper()}"
        return False, f"unknown action {action!r}"

    def _admin(self, ev):
        _, on, user, name, uid = ev
        if str(uid) not in ADMIN_IDS:
            return None                  # not a command for viewers; say nothing
        now = self.clock()
        if not on:
            if self.admin is None:
                return None
            self.admin = None
            self._admin_out.append(("admin", False, "released"))
            return "the controls are back with the fly and chat"
        if self.admin is not None:
            self.admin[2] = now + ADMIN_IDLE_S
            self._admin_out.append(("tap", "start"))
            return "streamer: START"
        self._advance(now)
        if self.driver is not None or self.line:
            # Somebody paid Bits for these seconds; an admin action must not
            # eat them. Refused, and said, so the streamer knows why.
            who = self.driver[1] if self.driver else self.line[0][1]
            return f"!menu waits: {who} has paid steering time -- try again after their turn"
        self.admin = [user, name, now + ADMIN_IDLE_S, now + ADMIN_MAX_S]
        self._admin_out += [("admin", True, "taken"), ("tap", "start")]
        return ADMIN_BANNER

    def _advance(self, now):
        if self.admin is not None:
            return                       # paid turns wait while the streamer works
        if self.driver and now >= self.driver[2]:
            self.driver = None
        if self.driver is None and self.line:
            user, name, secs = self.line.popleft()
            self.driver = [user, name, now + secs]
    def may_answer(self, user):
        """Is the fly allowed to answer this viewer now? Questions are free,
        so they are rate limited per viewer and overall, and refused silently
        -- a bot that says "too soon" is worse than one that says nothing."""
        now = self.clock()
        if now - self._ask_at < ASK_GLOBAL_S:
            return False
        if now - self._ask_user.get(user, -1e9) < ASK_USER_S:
            return False
        self._ask_at = self._ask_user[user] = now
        return True

    def chat_status(self):
        """(chat may drive?, last press, presses so far, tally of the last
        TALLY_S) -- what the window shows about chat's hand on the controls."""
        now = self.clock()
        last = self.last_press if self.last_press and now - self.last_press[2] < 4.0 else None
        tally = {}
        for when, key in self.press_log:
            if now - when <= TALLY_S:
                tally[key] = tally.get(key, 0) + 1
        return (self.driver is None and self.admin is None,
                last[:2] if last else None, self.presses, tally)

    def recent(self, secs=12.0):
        """What chat has done lately, newest first -- for the on-screen feed.
        Viewers cannot tell a working bot from a broken one unless the screen
        keeps saying that their presses arrived."""
        now = self.clock()
        return [t for when, t in reversed(self.chat_log) if now - when < secs]

    def tick(self):
        now = self.clock()
        out = []
        if self.admin is not None and now >= min(self.admin[2], self.admin[3]):
            why = "timeout" if now >= self.admin[2] else "ceiling"
            self.admin = None
            self._admin_out.append(("admin", False, why))
        out += self._admin_out
        self._admin_out = []
        self._advance(now)
        if self.stims and now >= self._next_stim:
            what, name = self.stims.popleft()
            out.append(("stim", what, name))
            self._next_stim = now + STIM_GAP_S
        pc = self._pending_control
        if pc is not None:
            out.append(("control",) + pc)
            self._pending_control = None
        pn = self._pending_nudge
        if pn is not None:
            out.append(("nudge",) + pn)
            self._pending_nudge = None
        return out

    def enqueue(self, kind, n=1, name="the game"):
        """Queue presses that nobody in chat asked for -- the game's own
        events rewarding the fly. Paced like any other press, but not
        credited to a viewer on the leaderboard."""
        self.stims.extend([(kind, name)] * max(int(n), 0))

    def save_board(self, path=BOARD):
        """Keep the leaderboard across restarts -- the stream restarts on its
        own, and regulars should not lose what they put in. Written the same
        atomic way as the game snapshot: a whole old file survives a power
        cut, a half-written new one never appears."""
        data = {"trainers": dict(self.top("trainers", BOARD_KEEP)),
                "drivers": dict(self.top("drivers", BOARD_KEEP))}
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)

    def load_board(self, path=BOARD):
        """Restore it, if there is one. A missing or damaged file is not worth
        stopping the stream for: the boards simply start empty."""
        try:
            with open(path) as f:
                data = json.load(f)
            for which in ("trainers", "drivers"):
                board = {str(k): v for k, v in data.get(which, {}).items()
                         if isinstance(v, (int, float)) and v > 0}
                getattr(self, which).update(board)
            return True
        except (OSError, ValueError, AttributeError):
            return False

    def top(self, which, n=2):
        """The leaderboard: [(name, score)] for 'trainers' or 'drivers'."""
        board = self.trainers if which == "trainers" else self.drivers
        return sorted(board.items(), key=lambda kv: -kv[1])[:n]

    def status(self):
        """(driver name, seconds left, waiting count, queued presses)."""
        now = self.clock()
        self._advance(now)
        if self.driver:
            return self.driver[1], max(0.0, self.driver[2] - now), len(self.line), len(self.stims)
        return None, 0.0, len(self.line), len(self.stims)


class ChatBridge(threading.Thread):
    """Reads chat in the background; the window drains .events each frame."""

    def __init__(self, cfg, host=HOST, port=PORT, use_tls=True, log=print,
                 path=CONFIG, id_base=ID_BASE):
        super().__init__(daemon=True)
        self.cfg, self.host, self.port, self.use_tls = cfg, host, port, use_tls
        self.path, self.id_base = path, id_base
        self.events = queue.Queue()
        # Set whenever an event is queued, so the window loop can wake for it
        # instead of sleeping out the rest of its frame (playview, 2026-10-06).
        self.wake = threading.Event()
        self.running = True
        self.connected = False
        self.log = log
        self._sock = None
        # Chat messages seen from the channel. Cheers arrive down this same
        # pipe, so watching this tick up when you type in your own chat checks
        # Twitch's half of the path for free (a broadcaster cannot cheer in
        # their own channel).
        self.messages = 0
        # What the fly wants to say in chat, if announcing is switched on.
        self._say = deque()
        self._say_at = 0.0
        self._token_at = 0.0       # next time to re-check the login (see _maybe_renew)
        self.said = 0          # lines actually written to the socket

    def say(self, text):
        """Queue one line for the channel. Returns False if it was dropped.

        The token this connects with is the broadcaster's, so a line starting
        with '/' or '.' would run a Twitch COMMAND (/ban, /timeout) rather
        than talk. Everything sent is forced to be plain speech: no leading
        slash or dot, no newlines smuggling a second IRC command, length
        capped. Announcements quote the game (species names) and chat (display
        names), so this is not hypothetical."""
        text = " ".join(str(text).split())[:SAY_MAX]
        text = text.lstrip("/.").strip()
        if not text or not self.connected or len(self._say) >= SAY_QUEUE:
            return False
        self._say.append(text)
        return True

    def _drain_say(self, send, now):
        if self._say and now >= self._say_at:
            send(f"PRIVMSG #{self.cfg['channel']} :{self._say.popleft()}")
            self.said += 1
            self._say_at = now + SAY_GAP_S

    def _can_refresh(self):
        return bool(self.cfg.get("refresh_token") and self.cfg.get("client_id"))

    def _maybe_renew(self, now):
        """Renew the token WHILE CONNECTED, not only when connecting.

        Twitch keeps an established IRC session working long after the token
        that opened it has expired, so a bot that only renews on connect can
        sit there reading and writing chat for days with a dead token in its
        config. Everything else that uses that token then fails: the stream
        title stopped updating ("stream title not set: the login could not be
        checked", thousands of times in the launcher log), clips would refuse,
        and the Discord poster cannot ask whether the stream is live.

        This is also why nothing ELSE may refresh that token -- refresh tokens
        rotate when used, so a second refresher locks this one out for good.
        The bridge is the single owner; everyone else reads the file.
        """
        if now < self._token_at:
            return
        self._token_at = now + TOKEN_CHECK_S
        self._ensure_token()

    def _ensure_token(self):
        """Renew the token if it is dead or about to be.
        A network hiccup here is not fatal: try the token we have."""
        if not self._can_refresh():
            return
        try:
            who = validate(self.cfg["oauth"].split(":", 1)[-1], self.id_base)
            if who is None or int(who.get("expires_in", 0)) < TOKEN_MARGIN_S:
                refresh(self.cfg, self.path, self.id_base)
                self.log("twitch: login token renewed")
        except (OSError, RuntimeError, ValueError) as e:
            self.log(f"twitch: could not check the token ({type(e).__name__}); trying it anyway")

    def _connect(self):
        self._ensure_token()
        raw = socket.create_connection((self.host, self.port), timeout=30)
        if self.use_tls:
            raw = ssl.create_default_context().wrap_socket(raw, server_hostname=self.host)
        raw.settimeout(1.0)
        send = lambda s: raw.sendall((s + "\r\n").encode("utf-8"))
        # Tags carry the Bits amount; without them a cheer is just text.
        send("CAP REQ :twitch.tv/tags twitch.tv/commands")
        send(f"PASS {self.cfg['oauth']}")          # never echoed anywhere
        send(f"NICK {self.cfg['nick'].lower()}")
        send(f"JOIN #{self.cfg['channel']}")
        return raw, send

    def run(self):
        backoff = 1.0
        while self.running:
            try:
                sock, send = self._connect()
                self._sock = sock
                self.connected = True
                self._say.clear()          # nothing stale after a reconnection
                self.log(f"twitch: reading #{self.cfg['channel']}")
                backoff = 1.0
                buf = b""
                while self.running:
                    try:
                        chunk = sock.recv(4096)
                    except socket.timeout:
                        now = time.monotonic()
                        self._drain_say(send, now)
                        self._maybe_renew(now)
                        continue
                    now = time.monotonic()
                    self._drain_say(send, now)
                    self._maybe_renew(now)
                    if not chunk:
                        raise ConnectionError("server closed the connection")
                    buf += chunk
                    while b"\r\n" in buf:
                        raw_line, buf = buf.split(b"\r\n", 1)
                        line = raw_line.decode("utf-8", "replace")
                        if line.startswith("PING"):
                            send("PONG" + line[4:])
                            continue
                        if " NOTICE " in line:
                            # Twitch explains a refused message here -- a
                            # missing chat:edit scope, a ban, follower-only
                            # mode. Without this the bot fails in silence.
                            self.log(f"twitch NOTICE: {line.split(':', 2)[-1]}")
                            continue
                        if "Login authentication failed" in line:
                            if self._can_refresh():
                                try:
                                    refresh(self.cfg, self.path, self.id_base)
                                    self.log("twitch: login token renewed after a refusal")
                                    raise ConnectionError("reconnecting with the new token")
                                except RuntimeError:
                                    pass
                            self.log("twitch: login failed -- run `flybrain twitch-login` again")
                            self.running = False
                            break
                        msg = parse(line)
                        if msg:
                            self.messages += 1
                            ev = interpret(msg)
                            if ev:
                                self.events.put(ev)
                                self.wake.set()
            except (OSError, ConnectionError) as e:
                self.connected = False
                if not self.running:
                    break
                self.log(f"twitch: {type(e).__name__}, reconnecting in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            finally:
                self.connected = False
                if self._sock is not None:
                    try:
                        self._sock.close()
                    except OSError:
                        pass
                    self._sock = None

    def stop(self):
        self.running = False
