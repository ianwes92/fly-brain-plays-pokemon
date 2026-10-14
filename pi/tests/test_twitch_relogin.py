"""Twitch login mended from a phone via #fly-log (2026-10-13)."""
import os, sys, tempfile
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))
import twitch_relogin as R
R.STATE_F = os.path.join(tempfile.mkdtemp(), "s.json")
posts, restarts = [], []
post = lambda t, x, c: posts.append((t, x))
def login_ok():
    R_show("\n  On any phone or computer, open:  https://www.twitch.tv/activate\n  and enter the code:              ABCD1234\n")
    return {"oauth": "oauth:new"}
R_show = None
def login():
    # emulate device_login calling show() then succeeding
    return login_ok()
assert R.check(validate=lambda a: {"login": "x"}, post=post) == "ok" and not posts
assert R.check(validate=lambda a: None, post=post) == "watching"            # one bad check: quiet
captured = {}
def fake_login():
    captured["called"] = True
    return {}
# second bad check: a code is posted (through show) and the game restarted
import flybrain.twitch as tw
orig = tw.device_login
def dl(client_id, channel, show=print, **k):
    show("\n  On any phone or computer, open:  https://www.twitch.tv/activate\n  and enter the code:              ABCD1234\n")
    return {}
tw.device_login = dl
orig_load = tw.load_config
tw.load_config = lambda *a, **k: {"oauth": "oauth:dead", "client_id": "c", "channel": "chan"}
try:
    r = R.check(validate=lambda a: None, post=post, restart=lambda: restarts.append(1))
finally:
    tw.device_login, tw.load_config = orig, orig_load
assert r == "relogged" and restarts == [1], r
assert "activate?device-code=ABCD1234" in posts[0][1] and "Authorize" in posts[0][1], posts[0]
assert "fixed" in posts[-1][0]
print("test_twitch_relogin: 5 checks passed")
