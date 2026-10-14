"""The fly speaking in chat.

It connects with the BROADCASTER's token, so anything it says that begins
with '/' or '.' would be run by Twitch as a moderator command rather than
spoken. Announcements quote the game (species names) and chat (display
names), so the sanitising here is what stands between a caught Pokemon's
name and a /ban. A real socket is used: the check is what goes on the wire.
"""
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import twitch                                     # noqa: E402
from flybrain.twitch import ChatBridge                          # noqa: E402

ok = True


def check(label, cond, detail=""):
    global ok
    ok = ok and bool(cond)
    print(f"  {'PASS' if cond else 'FAIL'}  {label} {detail}")


print("1. nothing dangerous reaches the wire")
br = ChatBridge({"nick": "FlyBot", "oauth": "oauth:x", "channel": "flychannel"})
br.connected = True
for bad, why in (("/ban someone", "a slash command"),
                 (".timeout viewer 600", "a dot command"),
                 ("  //me waves", "a slash after spaces"),
                 ("", "nothing at all"),
                 ("   ", "only spaces")):
    before = len(br._say)
    sent = br.say(bad)
    line = br._say[-1] if len(br._say) > before else ""
    check(f"{why} is not sent as one",
          not line.startswith(("/", ".")), repr(line))
check("a newline cannot smuggle a second command",
      br.say("hello\r\nPRIVMSG #other :hi") and "\n" not in br._say[-1] and "\r" not in br._say[-1],
      repr(br._say[-1]))
check("long lines are cut to what Twitch carries",
      br.say("x" * 900) and len(br._say[-1]) == twitch.SAY_MAX, str(len(br._say[-1])))

print("2. it only speaks when there is somewhere to speak")
off = ChatBridge({"nick": "F", "oauth": "o", "channel": "c"})
check("nothing is queued while disconnected", off.say("hello") is False and not off._say)
full = ChatBridge({"nick": "F", "oauth": "o", "channel": "c"})
full.connected = True
sent = [full.say(f"line {i}") for i in range(twitch.SAY_QUEUE + 3)]
check(f"at most {twitch.SAY_QUEUE} wait; stale news is dropped, not stored",
      len(full._say) == twitch.SAY_QUEUE and sent[-1] is False, str(len(full._say)))

print("3. against a server, in the right form and paced")
srv = socket.socket(); srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", 0)); srv.listen(1)
port = srv.getsockname()[1]
got = {"lines": [], "when": []}


def server():
    c, _ = srv.accept()
    c.settimeout(6.0)
    buf = b""
    start = time.monotonic()
    while time.monotonic() - start < 5.0:
        try:
            chunk = c.recv(1024)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        while b"\r\n" in buf:
            line, buf = buf.split(b"\r\n", 1)
            text = line.decode()
            if text.startswith("PRIVMSG"):
                got["lines"].append(text)
                got["when"].append(time.monotonic())
        if len(got["lines"]) >= 2:
            break
    c.close()


th = threading.Thread(target=server, daemon=True); th.start()
br2 = ChatBridge({"nick": "FlyBot", "oauth": "oauth:secret", "channel": "flychannel"},
                 host="127.0.0.1", port=port, use_tls=False, log=lambda *_: None)
br2.start()
for _ in range(50):
    if br2.connected:
        break
    time.sleep(0.1)
br2.say("RATTATA was caught!")
br2.say("BULBASAUR reached level 7")
th.join(timeout=8)
br2.stop()
check("addressed to the channel, as speech",
      got["lines"][:1] == ["PRIVMSG #flychannel :RATTATA was caught!"], str(got["lines"][:1]))
check("both got out", len(got["lines"]) == 2, str(got["lines"]))
if len(got["when"]) == 2:
    gap = got["when"][1] - got["when"][0]
    check(f"spaced by at least {twitch.SAY_GAP_S:.0f}s (Twitch drops floods)",
          gap >= twitch.SAY_GAP_S - 0.3, f"{gap:.2f}s")
else:
    check("spaced out", False, "did not get two lines")
check("the token is never echoed", not any("secret" in l for l in got["lines"]))

print("\n" + ("ALL PASS" if ok else "FAILURES PRESENT"))
sys.exit(0 if ok else 1)
