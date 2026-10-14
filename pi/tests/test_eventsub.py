"""Channel points over EventSub: the WebSocket client, and what a redemption does.

Three bugs were found writing this, each pinned here:
  1. Twitch's FIRST frame is a ping, not the welcome; parsing message one as
     JSON failed on every connect.
  2. The 101 reply and the first frame can share a TCP packet; discarding the
     bytes after the headers drops the start of a frame.
  3. An EXPIRED token is not a MISSING scope. Treating "cannot validate" as
     "refused" switched channel points off for a whole run, because the chat
     bridge renews a stale token a moment after start-up.
"""
import json
import os
import queue
import socket
import struct
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flybrain import eventsub as es

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


# -- RFC 6455 accept key, against the worked example in the RFC itself ----
check("accept key matches RFC 6455 section 1.3",
      es.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")


class Pipe:
    """A fake socket: what we write, and what the 'server' has queued."""

    def __init__(self, incoming=b""):
        self.incoming, self.sent = incoming, b""

    def recv(self, n):
        out, self.incoming = self.incoming[:n], self.incoming[n:]
        return out

    def sendall(self, data):
        self.sent += data


def server_frame(opcode, payload=b"", fin=True):
    """How Twitch sends: UNmasked."""
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | opcode])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


# -- frame parsing, all three length encodings -----------------------------
for size, label in ((5, "small"), (300, "16-bit length"), (70000, "64-bit length")):
    body = bytes(range(256)) * (size // 256 + 1)
    body = body[:size]
    op, data, fin = es.read_frame(Pipe(server_frame(0x1, body)))
    check(f"reads a {label} frame intact", op == 0x1 and data == body and fin)

# -- fragmented messages are joined; a control frame mid-message is not lost
frag = (server_frame(0x1, b'{"a":', fin=False) + server_frame(0x0, b'1}', fin=True))
op, data = es.read_message(Pipe(frag))
check("fragments are joined into one message", op == 0x1 and json.loads(data) == {"a": 1})

# -- what we send is masked, and decodes back to what we meant -------------
p = Pipe()
es.send_frame(p, 0xA, b"hello")
b0, b1 = p.sent[0], p.sent[1]
check("client frames are masked (RFC 6455 5.3)", b1 & 0x80)
check("and carry the right opcode", b0 & 0x0F == 0xA)
mask = p.sent[2:6]
check("and unmask to the payload",
      bytes(c ^ mask[i % 4] for i, c in enumerate(p.sent[6:])) == b"hello")

# -- BUG 1: the welcome arrives AFTER a ping --------------------------------
welcome = json.dumps({"metadata": {"message_type": "session_welcome"},
                      "payload": {"session": {"id": "abc123"}}}).encode()
p = Pipe(server_frame(0x9) + server_frame(0x1, welcome))
check("a leading ping does not hide the welcome", es.wait_welcome(p) == "abc123")
check("and the ping was answered with a pong", p.sent and p.sent[0] & 0x0F == 0xA)

p = Pipe(server_frame(0x8))
try:
    es.wait_welcome(p)
    check("a close before the welcome is an error", False)
except ConnectionError:
    check("a close before the welcome is an error", True)

# -- BUG 2: bytes that arrive with the handshake are kept ------------------
c = es.Conn(Pipe(b"later"), leftover=server_frame(0x1, b"first"))
op, data, _ = es.read_frame(c)
check("bytes past the handshake are served before the socket", data == b"first")

# -- a redemption becomes exactly the event a cheer does -------------------
rewards = {"rid-p": "pleasure", "rid-x": "pain"}
note = {"metadata": {"message_type": "notification"},
        "payload": {"event": {"reward": {"id": "rid-p"}, "user_name": "Viewer1"}}}
check("pleasure redemption -> the same stim event a cheer makes, tagged as points",
      es.redemption_event(note, rewards) == ("stim", "pleasure", 1, "Viewer1", "points"))
from flybrain.twitch import PaidActions
pa = PaidActions(clock=lambda: 0.0)
pa.feed(es.redemption_event(note, rewards))
check("the paid queue takes the tagged event", list(pa.stims) == [("pleasure", "Viewer1")])
note["payload"]["event"]["reward"]["id"] = "rid-x"
check("pain redemption", es.redemption_event(note, rewards)[1] == "pain")
note["payload"]["event"]["reward"]["id"] = "somebody-elses-reward"
check("a reward that is not the fly's is ignored", es.redemption_event(note, rewards) is None)
check("a malformed payload is ignored, not raised", es.redemption_event({}, rewards) is None)
del note["payload"]["event"]["user_name"]
note["payload"]["event"]["reward"]["id"] = "rid-p"
note["payload"]["event"]["user_login"] = "viewer1"
check("falls back to the login name", es.redemption_event(note, rewards)[3] == "viewer1")

# -- the rewards themselves ------------------------------------------------
kinds = {r["kind"] for r in es.REWARDS}
check("offers exactly pleasure and pain", kinds == {"pleasure", "pain"})
for r in es.REWARDS:
    check(f"'{r['title']}' fits Twitch's 45-char title limit", len(r["title"]) <= 45)
    check(f"'{r['title']}' prompt fits the 200-char limit", len(r["prompt"]) <= 200)
    check(f"'{r['title']}' costs something", r["cost"] >= 1)
check("there is a cooldown, so chat cannot hold the fly down", es.COOLDOWN_S >= 10)

# -- BUG 3: expired is not refused -----------------------------------------
logs = []
calls = {"n": 0}


def expired_then_refused(token):
    calls["n"] += 1
    return None if calls["n"] == 1 else {"user_id": "1", "scopes": ["chat:read"]}


es_sleep = es.time.sleep
es.time.sleep = lambda s: None            # do not actually back off in a test
try:
    b = es.EventSubBridge({"oauth": "oauth:x"}, queue.Queue(), None,
                          log=logs.append, validate=expired_then_refused)
    b.start(); b.join(timeout=10)
finally:
    es.time.sleep = es_sleep
check("an expired token is retried, not taken as a refusal", calls["n"] >= 2)
check("only a VALID token without the scope switches it off",
      b.state == f"needs scope {es.SCOPE}")
check("and says how to fix it", any("twitch-login" in l for l in logs))
check("the scope it needs is actually requested at login",
      es.SCOPE in __import__("flybrain.twitch", fromlist=["SCOPES"]).SCOPES)

print(f"{ok} checks pass")
