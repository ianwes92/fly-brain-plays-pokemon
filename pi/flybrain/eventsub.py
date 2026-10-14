"""Channel points -> pain / pleasure, over Twitch EventSub WebSockets.

Channel points cost a viewer nothing and accrue just from watching, so spending
them on the fly rewards people for staying -- the loyalty loop a 24/7 stream
needs. Bits stay as the premium route. A redemption becomes exactly the event a
cheer does, ('stim', kind, 1, name), on the SAME queue chat uses, so it gets the
same pacing, the same leaderboard credit and the same ticker with no second
code path to drift.

EventSub over a WebSocket suits the Pi: the connection is outbound only, so it
needs no public URL and no open port, unlike the webhook transport.

Standard library only, like the rest of the Twitch code: a minimal RFC 6455
CLIENT. EventSub only pushes to us, so this needs the handshake, frame reading,
pong and close -- not a general WebSocket library.

The rewards themselves are created by the bot (channel:manage:redemptions), so
nothing has to be set up by hand in the dashboard. A token without that scope
is refused with instructions rather than failing silently -- the chat:edit gap
once hid for a day by failing quietly.
"""
import base64
import hashlib
import json
import os
import socket
import ssl
import struct
import threading
import time
import urllib.error
import urllib.request

WS_HOST, WS_PATH = "eventsub.wss.twitch.tv", "/ws"
API = "https://api.twitch.tv/helix"
SCOPE = "channel:manage:redemptions"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"   # fixed by RFC 6455

# The rewards the fly offers. Titles are what a viewer sees; the kind is what
# the fly receives. Cost and cooldown are deliberately modest: the point is
# participation, and a long cooldown is what stops chat holding the fly down.
REWARDS = (
    {"title": "Give the fly pleasure", "kind": "pleasure", "cost": 100,
     "prompt": "A real dopamine hit to a real fly brain's reward neurons. It learns from it."},
    {"title": "Give the fly pain", "kind": "pain", "cost": 100,
     "prompt": "Triggers the fly's punishment neurons. It learns to avoid what it was doing."},
)
COOLDOWN_S = 30


# -- a minimal WebSocket client -------------------------------------------------
def accept_key(key):
    """What the server must echo back to prove it speaks WebSocket."""
    return base64.b64encode(hashlib.sha1((key + WS_GUID).encode()).digest()).decode()


class Conn:
    """A socket plus whatever arrived ahead of being asked for.

    Twitch sends the HTTP 101 reply AND the first WebSocket frame in the same
    TCP packet. Reading up to the end of the headers and discarding the rest
    threw away the start of session_welcome, and every frame after it was then
    parsed from the middle -- the first test of this decoded garbage. So the
    bytes past the headers are kept and served first.
    """

    def __init__(self, sock, leftover=b""):
        self.sock, self.buf = sock, leftover

    def recv(self, n):
        if self.buf:
            out, self.buf = self.buf[:n], self.buf[n:]
            return out
        return self.sock.recv(n)

    def sendall(self, data):
        self.sock.sendall(data)

    def settimeout(self, t):
        self.sock.settimeout(t)

    def close(self):
        self.sock.close()


def ws_connect(host=WS_HOST, path=WS_PATH, timeout=20):
    """Open a TLS WebSocket and complete the upgrade. Returns the socket."""
    raw = socket.create_connection((host, 443), timeout=timeout)
    sock = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\n"
                  f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
                  f"Sec-WebSocket-Version: 13\r\nUser-Agent: flybrain/1.0\r\n\r\n").encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("closed during the WebSocket handshake")
        head += chunk
        if len(head) > 16384:
            raise ConnectionError("oversized handshake response")
    header_bytes, leftover = head.split(b"\r\n\r\n", 1)
    lines = header_bytes.decode(errors="replace").split("\r\n")
    if " 101 " not in lines[0]:
        raise ConnectionError(f"upgrade refused: {lines[0]}")
    hdr = {l.split(":", 1)[0].strip().lower(): l.split(":", 1)[1].strip()
           for l in lines[1:] if ":" in l}
    if hdr.get("sec-websocket-accept") != accept_key(key):
        raise ConnectionError("server failed the WebSocket accept check")
    return Conn(sock, leftover)


def _read_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("WebSocket closed")
        buf += chunk
    return buf


def read_frame(sock):
    """One frame -> (opcode, payload). Server frames are never masked."""
    b0, b1 = _read_exact(sock, 2)
    fin, opcode = b0 & 0x80, b0 & 0x0F
    masked, length = b1 & 0x80, b1 & 0x7F
    if length == 126:
        length = struct.unpack(">H", _read_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _read_exact(sock, 8))[0]
    mask = _read_exact(sock, 4) if masked else None
    data = _read_exact(sock, length) if length else b""
    if mask:
        data = bytes(c ^ mask[i % 4] for i, c in enumerate(data))
    return opcode, data, bool(fin)


def read_message(sock):
    """A whole message, joining fragments. Control frames are returned at once."""
    opcode, data, fin = read_frame(sock)
    if opcode >= 0x8 or fin:
        return opcode, data
    parts = [data]
    while True:
        op2, d2, fin = read_frame(sock)
        if op2 >= 0x8:                       # a control frame mid-message
            return op2, d2
        parts.append(d2)
        if fin:
            return opcode, b"".join(parts)


def send_frame(sock, opcode, payload=b""):
    """Client frames MUST be masked (RFC 6455 5.3)."""
    mask = os.urandom(4)
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    sock.sendall(head + mask + bytes(c ^ mask[i % 4] for i, c in enumerate(payload)))


def wait_welcome(sock, limit=20):
    """Read until session_welcome and return its session id.

    The FIRST thing Twitch sends on a new connection is a ping, not the
    welcome -- found by looking at the raw frame (0x89 0x00) after a version of
    this that parsed message one as JSON failed on every single connect.
    Control frames are answered and skipped; anything else unexpected is a
    protocol error worth reconnecting over.
    """
    for _ in range(limit):
        op, data = read_message(sock)
        if op == 0x9:
            send_frame(sock, 0xA, data)
            continue
        if op == 0xA:
            continue
        if op == 0x8:
            raise ConnectionError("closed before the welcome")
        if op != 0x1:
            continue
        msg = json.loads(data.decode())
        if msg.get("metadata", {}).get("message_type") == "session_welcome":
            return msg["payload"]["session"]["id"]
    raise ConnectionError("no session_welcome")


# -- the Helix side --------------------------------------------------------------
def _helix(cfg, method, path, body=None):
    access = cfg.get("oauth", "").split(":", 1)[-1]
    req = urllib.request.Request(
        API + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Client-Id": cfg.get("client_id", ""),
                 "Authorization": f"Bearer {access}",
                 "Content-Type": "application/json", "User-Agent": "flybrain/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            raw = r.read()
            return r.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {}


def has_scope(who):
    return SCOPE in ((who or {}).get("scopes") or [])


def ensure_rewards(cfg, broadcaster_id):
    """Create the fly's rewards if they are not there. Returns {reward_id: kind}.

    Only rewards THIS app created can be managed by it, so the bot creates its
    own rather than adopting ones made in the dashboard -- that is also why no
    hand setup is needed.
    """
    code, have = _helix(cfg, "GET", f"/channel_points/custom_rewards?broadcaster_id="
                                     f"{broadcaster_id}&only_manageable_rewards=true")
    if code != 200:
        raise RuntimeError(f"could not list rewards: HTTP {code} {have.get('message', '')}")
    by_title = {r["title"]: r["id"] for r in have.get("data", [])}
    out = {}
    for spec in REWARDS:
        rid = by_title.get(spec["title"])
        if rid is None:
            code, made = _helix(cfg, "POST", f"/channel_points/custom_rewards?broadcaster_id="
                                             f"{broadcaster_id}", {
                "title": spec["title"], "cost": spec["cost"], "prompt": spec["prompt"],
                "is_enabled": True, "is_global_cooldown_enabled": True,
                "global_cooldown_seconds": COOLDOWN_S,
                "should_redemptions_skip_request_queue": True})
            if code not in (200, 201) or not made.get("data"):
                raise RuntimeError(f"could not create '{spec['title']}': "
                                   f"HTTP {code} {made.get('message', '')}")
            rid = made["data"][0]["id"]
        out[rid] = spec["kind"]
    return out


def redemption_event(payload, rewards):
    """A notification payload -> ('stim', kind, 1, name), or None if not ours."""
    try:
        ev = payload["payload"]["event"]
        kind = rewards.get(ev["reward"]["id"])
        if kind is None:
            return None
        # 5th element: the route, so the input journal can tell channel
        # points from bits (PaidActions.feed reads only the first four).
        return ("stim", kind, 1, ev.get("user_name") or ev.get("user_login") or "someone", "points")
    except (KeyError, TypeError):
        return None


class EventSubBridge(threading.Thread):
    """Keeps a WebSocket open and puts redemptions on the chat event queue."""

    def __init__(self, cfg, events, broadcaster_id, log=print, validate=None):
        super().__init__(daemon=True)
        self.cfg, self.events, self.bid, self.log = cfg, events, broadcaster_id, log
        self.validate = validate
        self.running = True
        self.connected = False
        self.redeemed = 0
        self.state = "starting"

    def run(self):
        backoff, moved_to, rewards = 2.0, None, None
        while self.running:
            try:
                if moved_to is None:
                    # A fresh start: check the login, make sure the rewards
                    # exist, and subscribe on a new session.
                    if self.validate is not None:
                        who = self.validate(self.cfg.get("oauth", "").split(":", 1)[-1])
                        if who is None:
                            # "Cannot validate" is NOT "lacks the scope". At
                            # start-up the chat bridge renews a stale token a
                            # moment AFTER this runs (it shares this cfg dict,
                            # so the fresh token appears here) -- treating an
                            # expired token as a refusal switched channel
                            # points off for the whole run. Wait and look again.
                            self.state = "waiting for a valid login"
                            raise ConnectionError("login not valid yet")
                        if not has_scope(who):
                            self.state = f"needs scope {SCOPE}"
                            self.log(f"channel points off: the login lacks {SCOPE} -- "
                                     f"run `flybrain twitch-login` again to add it")
                            return
                        if not self.bid:
                            self.bid = who.get("user_id")
                    rewards = ensure_rewards(self.cfg, self.bid)
                    sock = ws_connect()
                    sock.settimeout(30)
                    self._subscribe(self._welcome(sock))
                else:
                    # Twitch MOVED us (session_reconnect). The subscriptions
                    # travel with the session, so subscribing again would be a
                    # duplicate -- likely refused, which would turn a routine
                    # move into a backoff loop. Connect, take the welcome, go.
                    sock = ws_connect(*moved_to)
                    sock.settimeout(30)
                    self._welcome(sock)
                self.connected, self.state, backoff = True, "listening", 2.0
                self.log(f"channel points: listening for {len(rewards)} rewards")
                moved_to = self._listen(sock, rewards)
                try:
                    sock.close()
                except OSError:
                    pass
            except Exception as exc:
                self.connected, moved_to = False, None   # a failure restarts from scratch
                self.state = f"reconnecting ({type(exc).__name__})"
                self.log(f"channel points: {type(exc).__name__}: {exc}; retrying in {backoff:.0f}s")
                time.sleep(backoff)
                backoff = min(backoff * 2, 120.0)

    def _welcome(self, sock):
        return wait_welcome(sock)

    def _subscribe(self, session_id):
        code, body = _helix(self.cfg, "POST", "/eventsub/subscriptions", {
            "type": "channel.channel_points_custom_reward_redemption.add", "version": "1",
            "condition": {"broadcaster_user_id": str(self.bid)},
            "transport": {"method": "websocket", "session_id": session_id}})
        if code not in (200, 202):
            raise RuntimeError(f"subscribe refused: HTTP {code} {body.get('message', '')}")

    def _listen(self, sock, rewards):
        """Read until the socket ends. Returns a new (host, path) on reconnect."""
        while self.running:
            try:
                op, data = read_message(sock)
            except socket.timeout:
                raise ConnectionError("no keepalive from Twitch")
            if op == 0x9:                              # ping -> pong
                send_frame(sock, 0xA, data)
                continue
            if op == 0x8:                              # close
                return None
            if op != 0x1:
                continue
            msg = json.loads(data.decode())
            kind = msg.get("metadata", {}).get("message_type")
            if kind == "notification":
                ev = redemption_event(msg, rewards)
                if ev is not None:
                    self.events.put(ev)
                    self.redeemed += 1
            elif kind == "session_reconnect":
                # Twitch moves us; the new URL keeps our subscriptions.
                new = msg["payload"]["session"]["reconnect_url"]
                host = new.split("//", 1)[1].split("/", 1)[0]
                path = "/" + new.split("//", 1)[1].split("/", 1)[1]
                return (host, path)
            elif kind == "revocation":
                raise RuntimeError("subscription revoked by Twitch")
        return None
