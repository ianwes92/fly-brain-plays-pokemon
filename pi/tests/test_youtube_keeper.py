"""YouTube keeps itself live with nobody around (Ian, 2026-10-06).

Reproduces today's failure: video arriving and healthy, YouTube's own
auto-start broadcast stuck, never live. The keeper must wait a little (give
auto-start its chance), then start its OWN broadcast and get it live; must not
churn broadcasts if that keeps failing; must alert when OBS stops sending; and
must close its broadcast when the stream is switched off for good."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "play"))
import youtube_promo as yp
import tempfile as _tmp
yp.PROBE_F = _tmp.mktemp()          # never the live probe tally (panel.youtube_probe)
yp.RUN_F = "/nonexistent/run.json"  # titles tested without a run number
yp.END_F = _tmp.mktemp()             # never the live maintenance-end marker

ok = 0


def check(name, cond):
    global ok
    assert cond, name
    ok += 1


class FakeYT:
    def __init__(self):
        self.receiving, self.broadcasts, self.calls, self.n = True, {}, [], 0
        self.transition_fails = False

    def __call__(self, url, data=None, headers=None, method=None, form=False, timeout=20):
        self.calls.append((url, method))
        if url == yp.TOKEN_URL:
            return {"access_token": "AT", "expires_in": 3600}
        if "liveStreams?" in url:
            return {"items": [{"id": "S1", "status": {"streamStatus": "active" if self.receiving else "inactive"}}]}
        if "liveBroadcasts/bind" in url:
            return {"contentDetails": {"boundStreamId": "S1"}}
        if "liveBroadcasts/transition" in url:
            if self.transition_fails:
                raise yp.ApiError(403, "invalidTransition")
            q = dict(p.split("=") for p in url.split("?")[1].split("&"))
            self.broadcasts[q["id"]] = q["broadcastStatus"]
            return {}
        if "liveBroadcasts?" in url and method == "POST":
            self.last_insert = data
            self.n += 1
            bid = f"OWN{self.n}"
            self.broadcasts[bid] = "created"
            return {"id": bid, "status": {"lifeCycleStatus": "created"}}
        if "liveBroadcasts?" in url and "id=" in url:
            bid = url.split("id=")[1].split("&")[0]
            return {"items": [{"status": {"lifeCycleStatus": self.broadcasts[bid]}}]}
        if "liveBroadcasts?" in url:                 # the active-broadcast lookup
            live = [b for b, st in self.broadcasts.items() if st == "live"]
            return {"items": [{"id": b, "snippet": {"liveChatId": "C"}} for b in live]}
        if "videos?" in url:
            return {}
        raise AssertionError(url)

    def inserted(self):
        return sum(1 for u, m in self.calls if "liveBroadcasts?" in u and m == "POST")


now = [1_800_000_000.0]
alerts = []


def client(f):
    return yp.YouTube({"client_id": "x", "refresh_token": "R"}, {}, clock=lambda: now[0], http_fn=f)


def step(yt, live=True):
    now[0] += yp.KEEP_EVERY
    return yp.keep_alive(yt, live, "twitch.tv/x", log=lambda m: None, alert_fn=alerts.append,
                         wait=lambda s: None)


# -- today's failure, fixed unattended --------------------------------------
f = FakeYT(); f.broadcasts["AUTO"] = "ready"          # YouTube's own, stuck
yt = client(f)
check("first stuck check waits (auto-start gets a chance)", step(yt) == "waiting")
check("no broadcast created yet", f.inserted() == 0)
check("second stuck check starts our own broadcast", step(yt) == "golive")
check("and it is live", f.broadcasts["OWN1"] == "live")
cd = f.last_insert["contentDetails"]
check("auto-start and auto-stop are OFF on ours", cd["enableAutoStart"] is False and cd["enableAutoStop"] is False)
check("it is public and not made for kids", f.last_insert["status"] == {"privacyStatus": "public",
                                                                          "selfDeclaredMadeForKids": False})
check("with our title", f.last_insert["snippet"]["title"] == yp.TITLE)
check("the next check sees it live and does nothing", step(yt) == "ok" and f.inserted() == 1)
check("too soon: not checked at all", yp.keep_alive(yt, True, "u", log=lambda m: None) is None)

# -- live already: costs one lookup, nothing else -----------------------------
n = len(f.calls)
step(yt)
check("a healthy check is one API call", len(f.calls) - n == 1)

# -- it will not churn broadcasts -------------------------------------------------
f2 = FakeYT(); f2.transition_fails = True
yt2 = client(f2); alerts.clear()
outs = [step(yt2) for _ in range(8)]
check("at most 2 go-live attempts an hour", f2.inserted() == yp.MAX_GOLIVES_PER_HOUR)
check("then it gives up", outs[-1] == "gave-up")
check("and alerts once, not every check", len([a for a in alerts if "will not go live" in a]) == 1)
now[0] += 3600
f2.transition_fails = False
check("an hour later it may try again, and succeeds", any(step(yt2) == "golive" for _ in range(3)))

# -- OBS stopped sending to YouTube ----------------------------------------------
f3 = FakeYT(); f3.receiving = False
yt3 = client(f3); alerts.clear()
outs = [step(yt3) for _ in range(6)]
check("not receiving: it does NOT create broadcasts", f3.inserted() == 0 and outs[0] == "not-receiving")
check("and alerts once after ~10 min", len(alerts) == 1 and "received no video" in alerts[0])

# -- the stream switched off for good ----------------------------------------------
f4 = FakeYT(); f4.broadcasts["AUTO"] = "ready"
yt4 = client(f4)
step(yt4); step(yt4)                                # goes live with OWN1
check("Twitch off briefly: nothing closed", step(yt4, live=False) == "off" and f4.broadcasts["OWN1"] == "live")
outs = [step(yt4, live=False) for _ in range(12)]
check("off 20+ min: our broadcast is closed", "completed" in outs and f4.broadcasts["OWN1"] == "complete")
check("can't tell if Twitch is live: do nothing", step(yt4, live=None) == "off")
check("YouTube's own auto broadcast is never touched", f4.broadcasts["AUTO"] == "ready")
# -- the clobbering bug (2026-10-06): a new login must survive the service ----
import json, tempfile
d = tempfile.mkdtemp()
yp.CFG_F, yp.STATE_F = os.path.join(d, "c.json"), os.path.join(d, "s.json")
yp.save_private(yp.CFG_F, {"refresh_token": "OLD", "logged_in_at": 1.0})
svc = yp.make_client()                                   # the service, started with OLD
yp.save_private(yp.CFG_F, {"refresh_token": "NEW", "logged_in_at": 2.0, "testing": False})
svc.save()                                               # a save mid-tick, before pickup
check("a save never overwrites a newer login on disk", yp.load_json(yp.CFG_F)["refresh_token"] == "NEW")
check("it adopts it instead", svc.cfg["refresh_token"] == "NEW")
svc.cfg["access_token"] = "AT2"; svc.save()
check("later saves write what it is using now", yp.load_json(yp.CFG_F) ==
      {"refresh_token": "NEW", "logged_in_at": 2.0, "testing": False, "access_token": "AT2"})
print(f"{ok} checks pass")


def _run_title():
    import json as _j, tempfile as _t, os as _o
    rf = _o.path.join(_t.mkdtemp(), "run.json")
    _j.dump({"id": 4, "started": "2026-10-08T13:20:00"}, open(rf, "w"))
    assert yp.title(rf) == "Lab Run 1 · " + yp.TITLE and len(yp.title(rf)) <= 100
    assert yp.title("/nonexistent") == yp.TITLE
    print("test_youtube_keeper: run title checks passed")


_run_title()


# -- maintenance yt_end (obsfly, 2026-10-08): end, never revive, fresh broadcast fast --
def _yt_end():
    import json as _j
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from flybrain import panel as P
    d = _tmp.mkdtemp()
    yp.END_F, yp.STATE_F = os.path.join(d, "end.json"), os.path.join(d, "state.json")
    f = FakeYT(); f.broadcasts["OLD"] = "live"
    yt = client(f)
    check("live before maintenance", step(yt) == "ok")
    yt.state["keeper"]["own"] = "OLD"
    r = P.youtube_end(now=now[0], client=client(f))
    check("yt_end ends the live broadcast", r == {"ok": True, "ended": "OLD"} and f.broadcasts["OLD"] == "complete")
    check("and records it for the keeper", _j.load(open(yp.END_F))["ended"] == "OLD")
    check("a second yt_end finds nothing live", P.youtube_end(now=now[0], client=client(f))["ok"] is False)
    now[0] += yp.FAST_KEEP_S                  # OBS still sending for a moment after the end
    check("no go-live while the old video is still arriving",
          yp.keep_alive(yt, True, "u", log=lambda m: None, wait=lambda s: None) == "waiting"
          and not [b for b, st in f.broadcasts.items() if st == "live"])
    f.receiving = False
    now[0] += yp.FAST_KEEP_S
    check("OBS off: nothing to do", yp.keep_alive(yt, False, "u", log=lambda m: None, wait=lambda s: None) == "off")
    check("the ended broadcast is forgotten, never closed or revived", yt.state["keeper"].get("own") is None)
    f.receiving = True
    now[0] += yp.FAST_KEEP_S
    res = yp.keep_alive(yt, True, "u", log=lambda m: None, wait=lambda s: None)
    check("video back: a fresh broadcast at once, no waiting", res == "golive")
    new = [b for b, st in f.broadcasts.items() if st == "live"]
    check("a new id, and the old one stays complete", new and new[0] != "OLD" and f.broadcasts["OLD"] == "complete")
    now[0] += yp.FAST_KEEP_S
    check("back to slow checks once replaced",
          yp.keep_alive(yt, True, "u", log=lambda m: None, wait=lambda s: None) is None)
    now[0] += yp.KEEP_EVERY
    check("and it is simply live", yp.keep_alive(yt, True, "u", log=lambda m: None, wait=lambda s: None) == "ok")
    print("test_youtube_keeper: yt_end checks passed")


_yt_end()
