"""A network failure while checking or setting the title must not escape
(2026-10-08 13:25: a TLS handshake timeout in current_title crashed the game)."""
import os, sys, socket, urllib.error
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from flybrain import twitch as tw

def boom(*a, **k):
    raise urllib.error.URLError(socket.timeout("_ssl.c:975: The handshake operation timed out"))

tw.validate = boom
cfg = {"oauth": "oauth:x", "client_id": "c"}
assert tw.current_title(cfg) is None
ok, why = tw.set_title(cfg, "t")
assert ok is False and "URLError" in why, why
print("test_title_network: 2 checks passed")
url, why = tw.make_clip(cfg)
assert url is None and "URLError" in why, why
print("test_title_network: clip check passed")
