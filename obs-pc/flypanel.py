#!/usr/bin/env python3
"""Flybrain control panel. Runs on the OBS machine, stdlib only.

Design rule, learned the hard way: every check is measured AT THE CONSUMER and
reports the raw number it judged on. OBS saying "connected" is not evidence that
YouTube is live; the relay saying game=live is not evidence the picture moves.
Anything we could not measure says "unknown", never "ok".

    python flypanel.py          -> http://127.0.0.1:8777
"""
import json, os, time, hashlib, threading, http.server, socketserver, urllib.request, urllib.error, pathlib, subprocess, sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import obsws
from config import CFG

PORT = int(CFG['panel_port'])
PI = CFG['pi_host']
RELAY, GAME = f'http://{PI}:8765', f'http://{PI}:8766'
BROADCASTER = CFG['twitch_broadcaster_id']
SCENE = CFG['obs_scene']

# flybrain's control API on the relay. The token is generated on the Pi at
# ~/.config/flybrain/panel.json and copied here BY HAND - deliberately never sent
# over the agent channel. No token = controls stay disabled, status still works.
TOKF = HERE / 'panel_token.txt'


def panel_token():
    try:
        return TOKF.read_text().strip()
    except Exception:
        return ''


def pi_call(path, method='GET', body=None):
    tok = panel_token()
    if not tok:
        raise RuntimeError('no panel token: put it in ' + str(TOKF))
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(RELAY + path, data=data, method=method,
                                 headers={'Authorization': 'Bearer ' + tok,
                                          'Content-Type': 'application/json',
                                          'User-Agent': 'flypanel/1.0'})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)


_cache, _lock = {}, threading.Lock()


def cached(key, ttl, fn):
    """APIs have quotas; the panel polls every few seconds. Never hammer."""
    with _lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < ttl:
            return hit[1]
    try:
        val = fn()
    except Exception as e:
        val = {'state': 'unknown', 'detail': '%s: %s' % (type(e).__name__, e)}
    with _lock:
        _cache[key] = (time.time(), val)
    return val


def http_json(url, timeout=8):
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={'User-Agent': 'flypanel/1.0'}), timeout=timeout) as r:
        return json.load(r)


def http_text(url, timeout=6):
    with urllib.request.urlopen(urllib.request.Request(
            url, headers={'User-Agent': 'flypanel/1.0'}), timeout=timeout) as r:
        return r.read().decode()


# ------------------------------------------------------------------ checks
def chk_twitch():
    # The Pi's bridge is the only holder of the Twitch login (one owner per client:
    # a second grant here died with it on Oct 8), so Twitch status comes from the Pi.
    t = (pi_tools() or {}).get('twitch')
    if not t:
        return {'state': 'warn', 'detail': 'waiting for Pi report'}
    if t.get('live') is None:
        return {'state': 'bad', 'detail': t.get('why') or 'Pi could not reach Twitch'}
    if not t.get('live'):
        return {'state': 'off', 'detail': 'offline per Helix', 'extra': (t.get('title') or '')[:70]}
    n = int(t.get('viewers') or 0)
    chat = t.get('chat')
    bad_chat = chat and chat != 'connected'
    return {'state': 'warn' if bad_chat else 'ok',
            'detail': '%d viewer%s%s' % (n, '' if n == 1 else 's', ' · chat %s' % chat if bad_chat else ''),
            'extra': (t.get('title') or '')[:70]}


# YouTube health is NOT checked here: the YouTube token lives on the Pi, so it
# arrives via the relay's /panel/status instead. Checking it at OBS is exactly the
# mistake that hid a dead broadcast for ten minutes.


def chk_obs():
    o = obsws.connect()
    st = o.call('GetStreamStatus')
    rb = o.call('GetReplayBufferStatus')
    frozen = time.time() - _freeze['last_move'] if st['outputActive'] and _freeze['last_bytes'] is not None else 0
    if frozen >= 30:
        return {'state': 'bad', 'detail': 'stream FROZEN: no data leaving OBS for %d s' % frozen,
                'extra': 'auto-restart of OBS at %d s' % FREEZE_S}
    if not st['outputActive']:
        return {'state': 'off', 'detail': 'OBS not streaming',
                'extra': 'replay buffer %s' % ('armed' if rb['outputActive'] else 'OFF')}
    sk, tot = st['outputSkippedFrames'], max(1, st['outputTotalFrames'])
    prev = _cache.get('_obs_skip')
    _cache['_obs_skip'] = (time.time(), sk, tot)
    new_sk = sk - prev[1] if prev and sk >= prev[1] else 0
    new_tot = tot - prev[2] if prev and tot >= prev[2] else 0
    recent = new_sk / new_tot if new_tot else 0.0
    bad = recent > 0.001 or st['outputCongestion'] > 0.1
    return {'state': 'warn' if bad else 'ok',
            'detail': 'up %.0f min · %s skipped recently · congestion %.0f%%' % (
                st['outputDuration'] / 60000, ('%d frames' % new_sk) if new_sk else 'no frames',
                100 * st['outputCongestion']),
            'extra': 'replay buffer %s · %.3f%% skipped since start' % (
                'armed' if rb['outputActive'] else 'OFF', 100 * sk / tot)}


def chk_feed():
    """Is the picture viewers see actually moving? Encoder stats cannot see a frozen picture.
    On v2 the brain layer animates by itself, so judge the Game layer, and compare it with the
    Pi's own frames: a still game is fine when the Pi's picture is still too (dialogue, standing)."""
    o = obsws.connect()
    scene = o.call('GetCurrentProgramScene')['sceneName']
    v2 = scene == SCENE
    hs, ph = [], []
    for _ in range(3):
        img = o.call('GetSourceScreenshot', {'sourceName': 'Game' if v2 else scene, 'imageFormat': 'jpg',
                                             'imageWidth': 320, 'imageHeight': 180})['imageData']
        hs.append(hashlib.sha256(img.encode()).hexdigest()[:8])
        if v2:
            try:
                with urllib.request.urlopen('http://%s:8765/game.png' % PI, timeout=5) as r:
                    ph.append(hashlib.sha256(r.read()).hexdigest()[:8])
            except Exception:
                pass
        time.sleep(1.2)
    n, pn = len(set(hs)), len(set(ph))
    if v2 and n == 1 and pn > 1:
        return {'state': 'bad', 'detail': 'game picture FROZEN on stream while the Pi keeps playing',
                'extra': 'the game guard reloads it within 30 s'}
    return {'state': 'ok' if (n > 1 or (v2 and pn <= 1)) else 'bad',
            'detail': ('game picture moving' if n > 1 else "game still (the Pi picture is still too)") if v2
                      else '%d/%d distinct frames' % (n, len(hs))}


def _kv(line):
    return {k: v for k, v in (t.split('=', 1) for t in line.split() if '=' in t)}


def health_lines(url):
    """The Pi's /health is line-oriented: 'prefix: k=v k=v'. Parse the lot."""
    txt = http_text(url + '/health')
    out = {'_first': _kv(txt.splitlines()[0] if txt else '')}
    for ln in txt.splitlines():
        ln = ln.strip()
        if ln.startswith('game: '):
            ln = ln[6:]
        if ':' in ln and not ln.startswith('ok'):
            pre, _, rest = ln.partition(':')
            if ' ' not in pre:
                out[pre.strip()] = _kv(rest)
                if pre.strip() == 'thinking':
                    out['thinking_text'] = rest.strip()
        else:
            out['_first'].update(_kv(ln))
    return out


def chk_game():
    first = http_text(GAME + '/health').splitlines()
    kv = _kv(first[0]) if first else {}
    phase = next((l.split('phase=')[1].split()[0] for l in first if 'phase=' in l), '?')
    emu = int(next((l.split('emu=')[1].split()[0] for l in first if 'emu=' in l), 0))
    prev = _cache.get('_emu')
    _cache['_emu'] = (time.time(), emu)
    advancing = prev is None or emu > prev[1] or emu < prev[1]   # lower = game restarted
    return {'state': 'ok' if (phase == 'playing' and advancing) else 'warn',
            'detail': '%s · emulator %s' % (phase, 'advancing' if advancing else 'NOT advancing'),
            'extra': 'emu frame %d · %s window frames' % (emu, kv.get('frames', '?'))}


def chk_relay():
    kv = _kv(http_text(RELAY + '/health').splitlines()[0])
    live = kv.get('game') == 'live'
    return {'state': 'ok' if live else 'warn',
            'detail': 'feed %s · game speed %s' % ('live' if live else kv.get('game', '?'),
                                                  kv.get('game_avg', '?').replace('x', '×')),
            'extra': 'video held %s' % kv.get('video_held', '?')}


def chk_instagram():
    try:
        return chk_instagram_local()
    except Exception as e:
        ig = (cached('_tools', 12, pi_tools) or {}).get('instagram') or {} if panel_token() else {}
        if not ig:
            raise
        timer = ig.get('timer') or {}
        return {'state': 'ok' if timer.get('ActiveState') == 'active' else 'warn',
                'detail': 'via Pi · ' + str(ig.get('last_log', ''))[:80],
                'extra': 'next: %s · local token failed (%s)' % (
                    timer.get('NextElapseUSecRealtime', '?'), type(e).__name__)}


def chk_instagram_local():
    cfg = json.loads((HERE / 'ig_config.json').read_text())
    b = 'https://graph.instagram.com/v21.0/'
    q = http_json(b + 'me/content_publishing_limit?access_token=' + cfg['access_token'])
    used = q['data'][0]['quota_usage']
    st = http_json(b + 'me/stories?fields=id,timestamp&access_token=' + cfg['access_token'])
    items = st.get('data', [])
    if items:
        import datetime
        ts = datetime.datetime.strptime(items[0]['timestamp'], '%Y-%m-%dT%H:%M:%S%z').astimezone()
        last = ts.strftime('%a %I:%M %p').replace(' 0', ' ')
    else:
        last = 'none in last 24h'
    return {'state': 'ok', 'detail': 'quota %d/100 used' % used, 'extra': 'last Story ' + last}


def chk_youtube():
    """Second-hand by necessity: the YouTube token lives on the Pi, so the Pi's
    promoter is what actually asks YouTube. Labelled so it's clear this is not
    measured here - if the Pi is unreachable this says unknown, not 'offline'."""
    if not panel_token():
        return {'state': 'off', 'detail': 'needs panel token', 'extra': ''}
    yt = (pi_call('/panel/status').get('tools') or {}).get('youtube') or {}
    # promoter's own spend + the probe tally (keeper, panel probes, yt_end): the real daily total
    qa, qb = (yt.get('quota') or {}).get('used'), (yt.get('probe_quota') or {}).get('used')
    used = None if qa is None and qb is None else (qa or 0) + (qb or 0)
    # Liveness comes from the on-demand probe (asks YouTube now), not the promoter's
    # 5-minute poll. 'receiving but not live' is the stuck-in-Preparing state that
    # looks perfectly healthy from OBS - the exact gap that hid a dead broadcast.
    p = pi_call('/panel/youtube')
    live, recv = p.get('live'), p.get('receiving')
    if live is not None:
        _cache['_yt_good'] = (time.time(), live, recv)
    else:
        # One failed probe (YouTube returns the odd backendError) is not news: if the
        # last good answer is under 15 min old, keep showing it and say the probe failed.
        g = _cache.get('_yt_good')
        if g and time.time() - g[0] < 900:
            live, recv = g[1], g[2]
            p = dict(p, stale='last probe failed (%s) · showing the answer from %d min ago'
                     % (str(p.get('why'))[:40], round((time.time() - g[0]) / 60)))
    if live is None:
        return {'state': 'unknown', 'viewers': yt.get('viewers'),
                'viewers_age_s': yt.get('viewers_age_s'),
                'detail': 'probe failed: %s' % str(p.get('why'))[:60],
                'extra': 'quota %s/10000 · via Pi' % used}
    if live is False and recv:
        # Not a fault to page about: the Pi-side keeper owns this case and makes
        # its own broadcast live within ~4-5 min. warn, not bad, so it shows on the
        # panel without waking anyone.
        return {'state': 'warn', 'viewers': None, 'viewers_age_s': None,
                'detail': 'receiving video but not live — keeper is handling it',
                'extra': 'auto-recovers in ~5 min · quota %s/10000' % used}
    v, age = yt.get('viewers'), yt.get('viewers_age_s')
    # Three different facts, deliberately not flattened into "0":
    #   N + age -> N watching, that old      null + age -> YouTube HID the count
    #   null + no age -> not read yet        0 -> a real zero
    if v is not None:
        mins = (age or 0) / 60.0
        label = '%d viewer%s' % (v, '' if v == 1 else 's')
        stale = ' (%.0fm ago)' % mins if mins >= 1 else ' (just now)'
    elif age is not None:
        label, stale = 'viewers hidden by YouTube', ''
    else:
        label, stale = 'viewer count not read yet', ''
    return {'state': 'ok' if live else 'off',
            'viewers': v, 'viewers_age_s': age,
            'detail': ('live · ' + label + stale) if live else 'offline (per YouTube API)',
            'extra': p.get('stale') or 'quota %s/10000 · via Pi' % used}


def chk_progress():
    """Run progress. Every check can be green while the fly loops forever, so
    'furthest hasn't moved' is a signal nothing else gives us."""
    h = health_lines(GAME)
    pr, cb, mv = h.get('progress', {}), h.get('cb2', {}), h.get('moved', {})
    furthest = pr.get('furthest', '?')
    prev = _cache.get('_furthest')
    _cache['_furthest'] = (time.time(), furthest)
    stale = prev is not None and prev[1] == furthest and time.time() - prev[0] > 3600
    return {'state': 'warn' if stale else 'ok',
            'detail': 'on map %s · furthest map %s · %s area%s explored' % (
                cb.get('place', '?'), furthest, pr.get('areas', '?'), '' if str(pr.get('areas')) == '1' else 's'),
            'extra': '%s steps · %s bumps this run%s' % (mv.get('tiles', '?'), mv.get('bumps', '?'),
                                              ' · furthest stale 1h+' if stale else '')}


def chk_stuck():
    """'The fly hasn't moved' is the most actionable alert there is - it's exactly
    when Ian would want the dpad.

    Thresholds are scaled by the emulator's speed. 'still' is wall-clock, but what
    we care about is game-time: at 0.33x everything takes three times as long in
    wall-clock without the fly being any more stuck. Without this, anything that
    slows the Pi down (the mirror-world experiment, a thumbnail render) would fire
    false 'fly is stuck' alerts all night."""
    h = health_lines(GAME)
    st = h.get('stuck', {})
    still = st.get('still', '0s')
    secs = int(''.join(c for c in still if c.isdigit()) or 0)
    try:
        speed = float((health_lines(RELAY)['_first'].get('game_avg') or '1x').rstrip('x'))
    except (ValueError, KeyError):
        speed = 1.0
    scale = 1.0 / speed if 0.05 < speed < 1.0 else 1.0
    warn_at, bad_at = 180 * scale, 600 * scale
    note = '' if scale == 1.0 else ' · thresholds x%.1f for %.2fx speed' % (scale, speed)
    return {'state': 'bad' if secs >= bad_at else ('warn' if secs >= warn_at else 'ok'),
            'detail': ('moving' if secs < 5 else 'standing still for %ds' % secs) +
                      ('' if st.get('menu', '-') in ('-', None) else ' · in menu %s' % st.get('menu')),
            'extra': (h.get('thinking_text', '')[:50] + note)}


def chk_pi_power():
    """Top unresolved risk: under-voltage can corrupt saves and learned synapses."""
    h = health_lines(RELAY)
    pi = h.get('pi')
    if not pi:
        return {'state': 'unknown', 'detail': 'relay does not report pi power yet',
                'extra': 'asked flybrain to add a pi: line to /health'}
    def num(k):
        try:
            return float(pi.get(k))
        except (TypeError, ValueError):
            return None
    ext5v, temp = num('ext5v'), num('temp')
    # Alert on the *_now fields and on the voltage. under_volt_ever stays 1 for the
    # whole boot, so alerting on it would mean a permanent red light.
    now_uv = pi.get('under_volt_now') not in ('0', None)
    now_thr = pi.get('throttled_now') not in ('0', None)
    state = 'ok'
    if ext5v is not None and ext5v < 5.0:
        state = 'warn'
    if temp is not None and temp >= 75:
        state = 'warn'
    if now_uv or now_thr or (ext5v is not None and ext5v < 4.85) or (temp is not None and temp >= 82):
        state = 'bad'
    return {'state': state,
            'detail': 'EXT5V=%s temp=%s' % (
                ('%.2fV' % ext5v) if ext5v is not None else '?',
                ('%.1fC' % temp) if temp is not None else '?'),
            'extra': 'healthy ~5.1V, trips ~4.8V · %s' % (
                'under-voltage seen this boot' if pi.get('under_volt_ever') not in ('0', None)
                else 'no under-voltage this boot')}


def ig_days_left():
    import datetime
    if panel_token():
        ig = (cached('_tools', 12, pi_tools) or {}).get('instagram') or {}
        if isinstance(ig.get('token_days_left'), (int, float)):
            return int(ig['token_days_left']), ' (Pi)'
    cfg = json.loads((HERE / 'ig_config.json').read_text())
    issued = datetime.date.fromisoformat(cfg.get('issued', '2026-10-04'))
    return (issued + datetime.timedelta(days=60) - datetime.date.today()).days, ''


def chk_tokens():
    """A dead token looks exactly like 'nothing happened'. Count the days."""
    rows, worst = [], 'ok'
    # Instagram: the Pi refreshes the real token weekly, so its count wins once it
    # reports one. Until then, count 60 days from the copy issued to this machine.
    try:
        left, where = ig_days_left()
        rows.append('instagram %dd%s' % (left, where))
        if left < 7:
            worst = 'bad'
        elif left < 21:
            worst = 'warn'
    except Exception:
        rows.append('instagram ?')
    # Twitch: the Pi validates its own token and reports seconds left.
    tsec = ((pi_tools() or {}).get('twitch') or {}).get('token_s')
    if tsec is None:
        rows.append('twitch (Pi)')
    else:
        mins = int(tsec) // 60
        rows.append('twitch %s' % ('%dh' % (mins // 60) if mins >= 60 else 'renewing (auto)' if mins < 10 else '%dm' % mins))
    return {'state': worst, 'detail': ' · '.join(rows),
            'extra': 'youtube: production login on the Pi, no expiry'}


def pi_tools():
    """One fetch, shared by the per-tool checks below."""
    if not panel_token():
        return None
    return pi_call('/panel/status').get('tools') or {}


def _notok():
    return {'state': 'off', 'detail': 'needs panel token', 'extra': ''}


def chk_discord():
    """'The dict exists' is not health. Check the service AND how stale its state is."""
    t = cached('_tools', 12, pi_tools)
    if t is None:
        return _notok()
    dc = t.get('discord') or {}
    svc, age = dc.get('service'), dc.get('state_age_s')
    bad = svc not in ('active', None) or (isinstance(age, (int, float)) and age > 300)
    return {'state': 'bad' if bad else 'ok',
            'detail': '%s · state %ss old · sees stream %s' % (
                'running' if svc == 'active' else 'service %s' % svc, age, 'live' if dc.get('live') else 'offline'),
            'extra': 'last event id %s' % dc.get('last_event_id', '?')}


def chk_watchdog():
    """The thing that restarts the game when it stops answering."""
    t = cached('_tools', 12, pi_tools)
    if t is None:
        return _notok()
    wd = t.get('watchdog') or {}
    svc = wd.get('service')
    return {'state': 'ok' if svc in ('active', None) else 'bad',
            'detail': 'running' if svc == 'active' else 'service %s' % svc,
            'extra': 'last action: %s' % str(wd.get('last_log', 'none'))[:70]}


def chk_threads():
    """Daily 18:00 Threads post, run on the Pi. The token never leaves the Pi;
    this only reads the timer, the last post and the days left."""
    t = cached('_tools', 12, pi_tools)
    if t is None:
        return _notok()
    th = t.get('threads')
    if not th:
        return {'state': 'unknown', 'detail': 'Pi does not report Threads yet', 'extra': ''}
    timer, lp = th.get('timer') or {}, th.get('last_post') or {}
    days = th.get('token_days_left')
    log = str(th.get('last_log') or '')
    failed = any(w in log.lower() for w in ('fail', 'error', 'refused'))
    state = 'ok'
    if not th.get('enabled') or timer.get('ActiveState') != 'active':
        state = 'warn'
    if failed or (isinstance(days, (int, float)) and days < 7):
        state = 'bad'
    kind = {'A': 'daily stats', 'B': 'science', 'C': 'milestone'}.get(lp.get('last_kind'), lp.get('last_kind'))
    detail = ('last post %s (%s)' % (lp['last_date'], kind)) if lp.get('last_date') else 'no post yet'
    nxt = str(timer.get('NextElapseUSecRealtime') or '?')
    return {'state': state, 'detail': detail,
            'extra': 'next %s · token %sd%s' % (nxt[4:20] if len(nxt) > 20 else nxt,
                                                 ('%.0f' % days) if isinstance(days, (int, float)) else '?',
                                                 (' · ' + log[-60:]) if failed else '')}


def chk_backup():
    """Nightly GitHub push of game state + code. A silent failure means a disk loss
    would roll everything back to the last good push."""
    t = cached('_tools', 12, pi_tools)
    if t is None:
        return _notok()
    b = t.get('backup') or {}
    timer, last = b.get('timer') or {}, str(b.get('last_log', ''))
    ok = timer.get('ActiveState') == 'active' and 'pushed' in last
    return {'state': 'ok' if ok else 'warn',
            'detail': last.split(' backup: ')[-1][:60] if last else 'no backup logged',
            'extra': 'last %s · next %s' % (last[:16] or '?', timer.get('NextElapseUSecRealtime', '?'))}


# name, group, fn, what-it-means (shown to the user), how-to-fix-when-red
CHECKS = [
    ('Twitch', 'stream', chk_twitch,
     'Is the Twitch stream actually live, per Twitch itself.',
     'Check OBS is streaming; if OBS says it is, the ingest may have dropped — stop and start the stream.'),
    ('YouTube', 'stream', chk_youtube,
     'Is the YouTube broadcast live. Asked via the Pi at call time, not from a cached poll.',
     'If it says "receiving but not live", do nothing — the Pi-side keeper creates and starts its own broadcast within about 5 minutes. Only "receiving=false" is an OBS problem worth touching.'),
    ('OBS', 'stream', chk_obs,
     'Is OBS encoding and sending, and is it dropping frames.',
     'Skipped frames or congestion mean the encoder or upload is struggling — lower the bitrate.'),
    ('Feed', 'stream', chk_feed,
     'Is the picture actually moving. Hashes the output; OBS can look healthy while frozen.',
     'Reload the Fly Feed browser source in OBS, or check the relay on the Pi.'),
    ('Game', 'game', chk_game,
     'Is the emulator running and advancing frames.',
     'The watchdog restarts the game automatically after ~100s. If it stays stalled, the Pi needs attention.'),
    ('Relay', 'game', chk_relay,
     'The Pi service that turns the game into the video feed.',
     'Restart the relay on the Pi.'),
    ('Progress', 'game', chk_progress,
     'How far the fly has got: current map area, furthest reached, areas explored, tiles walked.',
     'Warns if the furthest point has not moved in an hour — the fly is likely looping. Nudge it somewhere new from the Drive tab.'),
    ('Stuck', 'game', chk_stuck,
     'How long since the fly last moved.',
     'Open the Drive tab and nudge it out with the d-pad — no need to take control.'),
    ('Instagram', 'tools', chk_instagram,
     'Story posting: quota used today and when the last Story went out (12:00 daily, 20:00 on milestones).',
     'If the timer is inactive nothing posts. Check flybrain-igstory.timer on the Pi.'),
    ('Discord', 'tools', chk_discord,
     'The bot that posts go-live, milestones and the digest.',
     'Restart the Discord poster service on the Pi.'),
    ('Watchdog', 'tools', chk_watchdog,
     'The service that restarts the game if it stops responding.',
     'If this is down, a crashed game will stay crashed. Restart it on the Pi.'),
    ('Threads', 'tools', chk_threads,
     'Daily 18:00 Threads post on @ian.moore92, made on the Pi (skipped if the stream is down).',
     'A failed post also alerts #fly-log. Check play/threads.log on the Pi; under 7 days the token needs a fresh login.'),
    ('Backup', 'tools', chk_backup,
     'Nightly push of the save, learning and code to GitHub (04:30).',
     'If it has not pushed in over a day, a disk failure would lose everything since. Check flybrain-backup on the Pi.'),
    ('Tokens', 'tools', chk_tokens,
     'Days left before each API credential expires.',
     'Twitch refreshes itself; the Pi refreshes Instagram weekly when a Story posts and alerts #fly-log 7 days before expiry. YouTube no longer expires.'),
    ('Pi power', 'tools', chk_pi_power,
     'Supply voltage and temperature on the Raspberry Pi.',
     'Under ~5.0V means the power supply is too weak. Fit a 5V/5A (25W) USB-C supply — under-voltage can corrupt the save and the learned synapses.'),
]
# YouTube's live probe costs Data API quota on the Pi (2 units per call). At the old
# 20 s this panel alone spent up to ~8,600 of the 10,000 daily units. 5 min is plenty
# for a status light; the maintenance path probes on demand when it needs to know.
TTL = {'Threads': 60, 'Backup': 300, 'Feed': 8, 'Twitch': 20, 'Instagram': 120, 'Tokens': 600,
       'Pi power': 30, 'Progress': 20, 'YouTube': 300, 'Discord': 15, 'Watchdog': 30}

def pi_ctl():
    """What /panel/control accepts, and who holds the controls right now."""
    if not panel_token():
        return {'caps': [], 'control': {}}
    d = pi_call('/panel/status')
    g = d.get('game') or {}
    return {'caps': d.get('capabilities') or g.get('capabilities') or [],
            'control': g.get('control') or {}}


HIST = HERE / 'history.jsonl'
HIST_MAX = 3_000_000          # ~ a few days; trimmed in place, never unbounded


def snapshot(record=False):
    out = []
    for name, group, fn, means, fix in CHECKS:
        r = cached(name, TTL.get(name, 6), fn)
        row = {'name': name, 'group': group, 'means': means, **r}
        if row.get('state') in ('warn', 'bad', 'unknown'):
            row['fix'] = fix
        out.append(row)
    ctl = cached('_ctl', 4, pi_ctl)
    caps = ctl.get('caps') if isinstance(ctl.get('caps'), list) else []
    held = ctl.get('control') or {}
    snap = {'checks': out, 'at': time.strftime('%H:%M:%S'),
            'control': bool(panel_token()),
            # The Pi advertises what /panel/control accepts. Until it lists 'stim',
            # the PAIN/PLEASURE buttons stay disabled rather than failing on click.
            'stim': 'stim' in caps,
            # pad presses as the streamer in chat, without taking control
            'chat': 'chat' in caps,
            'held': bool(held.get('held')), 'held_by': held.get('by'),
            'held_secs': held.get('secs_left')}
    if record:
        try:
            with HIST.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'t': int(time.time()),
                                    's': {c['name']: c['state'] for c in out},
                                    'd': {c['name']: c.get('detail', '') for c in out}}) + '\n')
            if HIST.stat().st_size > HIST_MAX:
                keep = HIST.read_text(encoding='utf-8').splitlines()[-8000:]
                HIST.write_text('\n'.join(keep) + '\n', encoding='utf-8')
        except Exception:
            pass
    return snap


_live = {'snap': None}


def refresher():
    """Keep a fresh snapshot ready so /api/status never waits on a slow check
    (Feed alone takes ~2.5 s). Each check still keeps its own TTL."""
    while True:
        try:
            _live['snap'] = snapshot()
        except Exception as e:
            print('refresher:', e, flush=True)
        time.sleep(4)


def current_snapshot():
    return _live['snap'] or snapshot()


def history_summary(hours=24):
    """What a snapshot cannot tell you: did it break at all today?"""
    cutoff = time.time() - hours * 3600
    counts, total = {}, 0
    try:
        for ln in HIST.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r['t'] < cutoff:
                continue
            total += 1
            for k, v in r['s'].items():
                c = counts.setdefault(k, {'ok': 0, 'bad': 0, 'warn': 0, 'other': 0})
                c[v if v in c else 'other'] += 1
    except FileNotFoundError:
        pass
    return {'samples': total, 'hours': hours, 'counts': counts}


RANK = {'ok': 0, 'off': 1, 'unknown': 2, 'warn': 3, 'bad': 4}


def _num(rx, text):
    import re
    m = re.search(rx, text or '')
    return float(m.group(1)) if m else None


def history_timeline(hours=24, buckets=96):
    """Per-check health over time, plus the few numbers worth charting (viewers,
    Pi voltage and temperature), bucketed so the page stays light."""
    now = time.time()
    t0, width = now - hours * 3600, hours * 3600 / buckets
    worst = {}                 # check -> [state or None] * buckets
    detail = {}                # check -> [detail of the worst sample in that bucket]
    series = {k: [[] for _ in range(buckets)] for k in ('twitch', 'youtube', 'volts', 'temp')}
    try:
        lines = HIST.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        lines = []
    for ln in lines:
        try:
            r = json.loads(ln)
        except Exception:
            continue
        if r['t'] < t0:
            continue
        i = min(buckets - 1, int((r['t'] - t0) / width))
        d = r.get('d', {})
        for k, v in r['s'].items():
            w = worst.setdefault(k, [None] * buckets)
            if w[i] is None or RANK.get(v, 2) > RANK.get(w[i], 2):
                w[i] = v
                detail.setdefault(k, [''] * buckets)[i] = d.get(k, '')[:70]
        tv = _num(r'(\d+) viewers?', d.get('Twitch')) if r['s'].get('Twitch') == 'ok' else None
        yv = _num(r'(\d+) viewers?', d.get('YouTube'))
        for key, val in (('twitch', tv), ('youtube', yv),
                         ('volts', _num(r'EXT5V=([\d.]+)V', d.get('Pi power'))),
                         ('temp', _num(r'temp=([\d.]+)C', d.get('Pi power')))):
            if val is not None:
                series[key][i].append(val)
    avg = lambda v: round(sum(v) / len(v), 3) if v else None
    return {'hours': hours, 'start': int(t0), 'width': width,
            'checks': {k: {'states': v, 'details': detail.get(k, [])} for k, v in worst.items()},
            'series': {k: [avg(b) for b in v] for k, v in series.items()},
            'peak': {k: [max(b) if b else None for b in v] for k, v in series.items()},
            'low': {k: [min(b) if b else None for b in v] for k, v in series.items()}}


# ---------------------------------------------------------------- alerts
ALERT_AFTER = 3               # consecutive bad polls before shouting
_alert_state = {}


def discord(text):
    hooks = json.loads((HERE / 'discord_ids.json').read_text())
    url = hooks.get('fly-log', {}).get('webhook')
    if not url:
        return
    body = json.dumps({'username': 'Flypanel', 'content': text,
                       'allowed_mentions': {'parse': []}}).encode()
    urllib.request.urlopen(urllib.request.Request(
        url, data=body, headers={'Content-Type': 'application/json',
                                 'User-Agent': 'flypanel/1.0'}, method='POST'), timeout=10)


def alert_pass(snap):
    """Alert on sustained failure, and once on recovery. Never one per poll."""
    for c in snap['checks']:
        name, bad = c['name'], c['state'] in ('bad', 'unknown')
        st = _alert_state.setdefault(name, {'run': 0, 'firing': False})
        st['run'] = st['run'] + 1 if bad else 0
        if bad and st['run'] >= ALERT_AFTER and not st['firing']:
            st['firing'] = True
            try:
                discord('🔴 **%s** %s — %s' % (name, c['state'], c.get('detail', '')))
            except Exception:
                st['firing'] = False
        elif not bad and st['firing']:
            st['firing'] = False
            try:
                discord('🟢 **%s** recovered — %s' % (name, c.get('detail', '')))
            except Exception:
                pass


# ---------------------------------------------------------------- science
SCI = HERE / 'behaviour.jsonl'


def sci_sample():
    """Raw behaviour counters. These are cumulative since the run started, so the
    interesting quantities are their RATES - which the front end derives by
    differencing consecutive samples."""
    h = health_lines(GAME)
    mv, tr, dr = h.get('moved', {}), h.get('turns', {}), h.get('drive', {})
    pr, cb, se = h.get('progress', {}), h.get('cb2', {}), h.get('senses', {})

    def i(d, k):
        try:
            return int(d.get(k))
        except (TypeError, ValueError):
            return None

    def f(d, k):
        try:
            return float(d.get(k))          # opinions are signed, e.g. +0.123
        except (TypeError, ValueError):
            return None

    # Ships at the next game restart. Note the key is 'exits', not 'doors'.
    # pain/pleasure count bursts since THIS game process started, so they reset on
    # restart - the front end already treats a decrease as a reset, not a negative.
    ln = h.get('learned', {})
    learned = {'people': f(ln, 'people'), 'exits': f(ln, 'exits'),
               'pain': i(ln, 'pain'), 'pleasure': i(ln, 'pleasure'),
               'toward_person': i(ln, 'toward_person'), 'toward_exit': i(ln, 'toward_exit'),
               'away_from_person': i(ln, 'away_from_person'),
               'away_from_exit': i(ln, 'away_from_exit')}
    # Fly-vs-human attribution: a frame is the fly's own only when no human key
    # reached the game for 5 s of brain time. Science numbers use the clean side.
    at = h.get('attrib', {})
    attrib = {k: i(at, k) for k in ('clean_frames', 'fly_frames', 'nudge_frames', 'driver_frames',
                                     'admin_frames', 'tiles_clean', 'tiles_assisted', 'bumps_clean',
                                     'bumps_assisted', 'human_pain', 'human_pleasure',
                                     'game_pain', 'game_pleasure', 'rule_pain')}
    attrib['human_share'] = f(at, 'human_share_5min')
    return {'t': int(time.time()), **learned, **attrib,
            'tiles': i(mv, 'tiles'), 'bumps': i(mv, 'bumps'), 'bouts': i(mv, 'bouts'),
            'left': i(tr, 'left'), 'right': i(tr, 'right'), 'a': i(tr, 'a'), 'b': i(tr, 'b'),
            'stalled': i(dr, 'stalled'), 'furthest': i(pr, 'furthest'), 'areas': i(pr, 'areas'),
            'place': i(cb, 'place'), 'battle': 1 if cb.get('battle') == 'True' else 0,
            'spikes': i(se, 'spikes'), 'emu': i(h.get('_first', {}), 'emu')}


def sci_record():
    try:
        with SCI.open('a', encoding='utf-8') as f:
            f.write(json.dumps(sci_sample()) + '\n')
        if SCI.stat().st_size > 4_000_000:
            keep = SCI.read_text(encoding='utf-8').splitlines()[-10000:]
            SCI.write_text('\n'.join(keep) + '\n', encoding='utf-8')
    except Exception:
        pass


RAW = HERE / 'data' / 'raw'


def raw_record():
    """Archive EVERYTHING measurable once a minute, not just what the charts use:
    every key on the game's and relay's /health (attribution, mood, thoughts,
    senses, team, drive, control...), the Pi's power line, OBS output stats and
    audience numbers. One JSON line per minute, one file per day, kept forever
    (~3 MB/day). Analyses we have not thought of yet can be run on this later."""
    try:
        RAW.mkdir(parents=True, exist_ok=True)
        g = health_lines(GAME)
        rl = health_lines(RELAY)
        # The verbatim text too: some lines (learning: outstanding[...]) don't fit
        # the k=v parser, and the raw form is what any future parser will want.
        row = {'t': int(time.time()), 'game_raw': http_text(GAME + '/health'),
               'game': {k: v for k, v in g.items() if k != '_first'},
               'game_first': g.get('_first', {}),
               'relay': rl.get('_first', {}), 'pi': rl.get('pi', {})}
        try:
            st = obsws.connect().call('GetStreamStatus')
            row['obs'] = {k: st.get(k) for k in ('outputActive', 'outputDuration', 'outputSkippedFrames',
                                                 'outputTotalFrames', 'outputBytes', 'outputCongestion')}
        except Exception:
            row['obs'] = None
        snap = _live.get('snap') or {}
        aud = {}
        for c in snap.get('checks', []):
            if c['name'] == 'Twitch':
                aud['twitch'] = _num(r'(\d+) viewers?', c.get('detail')) if c.get('state') == 'ok' else None
            if c['name'] == 'YouTube':
                aud['youtube'] = c.get('viewers')
                aud['youtube_live'] = c.get('state') == 'ok'
        row['audience'] = aud
        with (RAW / time.strftime('%Y-%m-%d.jsonl')).open('a', encoding='utf-8') as f:
            f.write(json.dumps(row) + '\n')
    except Exception as e:
        print('raw_record:', e, flush=True)


def _bracket(text, name):
    """learning: outstanding[src=1.0,src2=2.0] -> {src: float}. Comma OR space separated."""
    import re
    m = re.search(name + r'\[([^\]]*)\]', text or '')
    out = {}
    if m:
        for tok in re.split(r'[,\s]+', m.group(1).strip()):
            if '=' in tok:
                k, v = tok.split('=', 1)
                try:
                    out[k] = float(v)
                except ValueError:
                    pass
    return out


def _floats(d):
    out = {}
    for k, v in (d or {}).items():
        try:
            out[k] = float(v)
        except (TypeError, ValueError):
            pass
    return out


def brain_series(hours=6, n=150):
    """Trends for the Who-trained-it and Brain-and-senses cards, from the raw archive."""
    cutoff = time.time() - hours * 3600
    rows = []
    days = {time.strftime('%Y-%m-%d', time.localtime(cutoff + i * 86400))
            for i in range(int(hours / 24) + 2)}
    for day in sorted(days):
        p = RAW / (day + '.jsonl')
        if not p.exists():
            continue
        for ln in p.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get('t', 0) < cutoff:
                continue
            g = r.get('game', {})
            lr = g.get('learning', {})
            raw = r.get('game_raw', '')
            lline = next((l for l in raw.splitlines() if l.startswith('learning:')), '')
            rows.append({'t': r['t'],
                         'depressed': _floats(lr).get('depressed_now'),
                         'human_share': _floats(lr).get('human_share'),
                         'outstanding': _bracket(lline, 'outstanding'),
                         'hz': _floats(g.get('brain_hz')),
                         'eyes': _floats(g.get('eyes_in'))})
    if len(rows) > n:                       # thin evenly; these are slow-moving
        step = len(rows) / n
        rows = [rows[int(i * step)] for i in range(n)]
    return rows


def brain_now():
    g = health_lines(GAME)
    raw = http_text(GAME + '/health')
    line = lambda p: next((l for l in raw.splitlines() if l.startswith(p)), '')
    build = line('build:')
    return {'learning': _floats(g.get('learning')),
            'outstanding': _bracket(line('learning:'), 'outstanding'),
            'depressed_total': _bracket(line('learning:'), 'depressed_total'),
            'fingerprint': (g.get('learning') or {}).get('fingerprint'),
            'build': build[len('build:'):].strip(),
            'hz': _floats(g.get('brain_hz')), 'eyes': _floats(g.get('eyes_in')),
            'motor': g.get('motor') or {}, 'attrib': _floats(g.get('attrib')),
            'sides': g.get('turn_sides') or {},
            'turns': g.get('turns') or {},
            'starters': g.get('starters') or {},
            'since_human_s': (g.get('attrib') or {}).get('since_human_s')}


def live_state():
    """Small, fast snapshot for the animated brain card (polled ~2 Hz, shared cache).
    Uses the Pi's /panel/live when it exists, else builds the same shape from /health."""
    try:
        if panel_token():
            d = pi_call('/panel/live')
            if isinstance(d, dict) and d.get('regions'):
                # Pi names -> the card's plain-English names
                r, e = d['regions'], d.get('eyes_in') or {}
                ren = {'cx': 'navigation', 'kc': 'memory', 'mbon': 'memory out', 'pam': 'reward', 'ppl1': 'punishment'}
                d['regions'] = {ren.get(k, k): v for k, v in r.items() if v is not None}
                lr = lambda k: [e.get(k + '_left'), e.get(k + '_right')]
                d['senses'] = {'people': lr('people'), 'doors': lr('exits'), 'obstacle': lr('obstacle')}
                d.pop('eyes_in', None)
                d['src'] = 'pi'
                return d
    except Exception:
        pass
    raw = http_text(GAME + '/health', timeout=3)
    g = {}
    for l in raw.splitlines():
        k, _, rest = l.partition(':')
        if rest and ' ' not in k:
            g[k.strip()] = rest.strip()
    kv = lambda k: dict(x.split('=', 1) for x in g.get(k, '').split() if '=' in x)
    num = lambda v: (float(v.rstrip('s')) if v not in (None, '', '-') else None)
    def nums(d):
        out = {}
        for k, v in d.items():
            try:
                out[k] = float(v)
            except ValueError:
                pass
        return out
    hz, m, mood, tr, st, cb, tm = (kv('brain_hz'), kv('motor'), kv('mood'), kv('turns'),
                                  kv('stuck'), kv('cb2'), kv('team'))
    f = nums(hz)
    regions = {'eyes': f.get('sc_optic'), 'vision': f.get('sc_visual_projection'),
               'senses': f.get('sc_sensory'), 'navigation': f.get('cx_central_complex'),
               'memory': f.get('kc'), 'memory out': f.get('mbon'), 'reward': f.get('pam'),
               'punishment': f.get('ppl1'), 'descending': f.get('sc_descending'), 'motor': f.get('sc_motor')}
    side = lambda v: None if v is None else round(v, 1)
    e = nums(kv('eyes_in'))
    return {'src': 'health', 't': time.time(),
            'regions': {k: v for k, v in regions.items() if v is not None},
            'senses': {'people': [side(e.get('people_left')), side(e.get('people_right'))],
                       'doors': [side(e.get('exits_left')), side(e.get('exits_right'))],
                       'obstacle': [side(e.get('obstacle_left')), side(e.get('obstacle_right'))],
                       'eye': side(e.get('eye')), 'ear': side(e.get('ear')), 'touch': side(e.get('touch')),
                       'pain': side(e.get('pain')), 'pleasure': side(e.get('pleasure'))},
            'keys': m.get('fly_keys'), 'heading': m.get('heading'), 'walking': m.get('bout') == 'True',
            'mood': {'valence': num(mood.get('valence')), 'label': mood.get('label'),
                     'smell_people': num(mood.get('smell_people')), 'smell_exits': num(mood.get('smell_exits'))},
            'thinking': g.get('thinking'), 'battle': cb.get('battle') == 'True',
            'menu_s': num(st.get('menu')), 'still_s': num(st.get('still')),
            'turns': {k: int(v) for k, v in tr.items() if v.isdigit()},
            'place': cb.get('place'), 'lead': ('%s Lv %s' % (tm.get('lead'), tm.get('lv'))) if tm.get('lead') not in (None, '-') else None}


def run_info():
    """Current run from the game's /health "run: id=3 started=2026-10-08T09:16:35"."""
    import datetime
    g = health_lines(GAME)
    r = g.get('run') or {}
    rid, st = r.get('id'), r.get('started')
    out = {'id': None if rid in (None, '-') else rid, 'started': None, 'label': None}
    out['name'] = (r.get('name') or '').replace('_', ' ') or None
    out['day'], out['ends'] = r.get('day'), r.get('ends')
    out['of'] = r.get('of')
    if st and st != '-':
        try:
            dt = datetime.datetime.fromisoformat(st)          # Pi local time (same zone as here)
            out['started'] = int(dt.timestamp())
            out['label'] = dt.strftime('%b %d, %I:%M %p').replace(' 0', ' ')
        except ValueError:
            pass
    if not out['of'] and out['ends'] and out['started']:
        try:
            out['of'] = round((datetime.datetime.fromisoformat(out['ends']).timestamp() - out['started']) / 86400)
        except ValueError:
            pass
    return out


def sci_series(hours=6):
    cutoff = time.time() - hours * 3600
    rows = []
    try:
        for ln in SCI.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if r.get('t', 0) >= cutoff:
                rows.append(r)
    except FileNotFoundError:
        pass
    return rows


# ---------------------------------------------------------------- maintenance
# Scheduled stream restarts: splits the Twitch VOD so they don't grow unbounded,
# and gives a predictable window for checks.
#   (HH:MM, seconds down, YYYY-MM-DD or None for daily)
# Re-enabled 2026-10-06 on a RECOVERY basis, not a prevention one. Tested and ruled
# out: sync-stop=false (the target dies with the shared encoder) and a dedicated
# encoder for the target (it still stops with the main output). A sub-80s window
# would keep YouTube's broadcast alive but Twitch then treats it as a reconnect and
# won't split the VOD, which was the whole point. So the rollover is unavoidable:
# instead, do_maintenance() verifies YouTube at the destination and reattaches the
# encoder until it is genuinely live, and shouts in #fly-log if it can't.
#   (HH:MM, seconds down, YYYY-MM-DD or None for daily)
# Exactly 12 h apart: YouTube only archives live streams up to 12 h, and each window
# now ENDS the YouTube broadcast (the Pi starts a fresh one when video returns), so
# every broadcast is ~11 h 57 m and gets a VOD. Clear of the 12:00/20:00 Stories and
# the 18:00 Threads post. (Was 08:00/20:10: 12 h 10 m apart, and the keeper kept one
# broadcast alive across windows, so nothing past 12 h was archived.)
MAINT = [(t, int(secs), None) for t, secs in CFG['maintenance']]
MAINT_LOG = HERE / 'maintenance.log'
_maint = {'until': 0, 'done': set()}


def in_maintenance():
    return time.time() < _maint['until']


def mlog(msg):
    line = '%s %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(line, flush=True)
    try:
        with MAINT_LOG.open('a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def yt_probe():
    """Ask YouTube, right now, whether a broadcast is live. Queries the API at call
    time (2 units, shared for 20s). live=None means the PROBE failed - unknown, not
    dead, and must never trigger a restart."""
    try:
        return pi_call('/panel/youtube')
    except Exception as e:
        return {'live': None, 'receiving': None, 'why': '%s: %s' % (type(e).__name__, e)}


def reattach(reason):
    """Stop and start the OBS output so the encoder re-attaches to YouTube's new
    broadcast. This is the cure that has worked every time we've hit 'Preparing'."""
    mlog('maintenance: %s -> reattaching encoder' % reason)
    o = obsws.connect()
    if o.call('GetStreamStatus')['outputActive']:
        o.call('StopStream')
    time.sleep(8)
    obsws.connect().call('StartStream')
    time.sleep(15)


def do_maintenance(down_s):
    """Stop the stream, wait, start it, then VERIFY YOUTUBE ACTUALLY WENT LIVE.

    The bug this replaces: the old version retried only when OBS's own output was
    inactive. But the failure mode is OBS active and YouTube dead, so the retry
    could never fire. Check the destination, not the encoder."""
    _maint['until'] = time.time() + down_s + 620     # 20 s restart + up to 8 min of YouTube probes
    try:
        # End the YouTube broadcast first, so it closes as a <12 h VOD. The Pi owns the
        # YouTube login; its keeper makes a fresh broadcast when video arrives again.
        # Best effort: a failure here never blocks the Twitch split.
        try:
            r = pi_call('/panel/control', 'POST', {'action': 'yt_end'})
            mlog('maintenance: YouTube end -> %s' % json.dumps(r)[:160])
        except Exception as e:
            mlog('maintenance: YouTube end failed (%s: %s) - continuing' % (type(e).__name__, str(e)[:100]))
        o = obsws.connect()
        if o.call('GetStreamStatus')['outputActive']:
            o.call('StopStream')
            mlog('maintenance: stream stopped for %ds' % down_s)
        else:
            mlog('maintenance: stream was already down')
        time.sleep(down_s)
        obsws.connect().call('StartStream')
        time.sleep(20)
        mlog('maintenance: restarted, OBS active=%s'
             % obsws.connect().call('GetStreamStatus')['outputActive'])

        # SPLIT OF DUTIES with flybrain's YouTube keeper on the Pi:
        #   receiving=True, live=False -> THE KEEPER'S JOB. It creates our own
        #       broadcast (auto-start off) and transitions it live in ~19s. We must
        #       NOT reattach here: doing so would roll the broadcast over at exactly
        #       the moment the keeper is making one live. This is the case I spent
        #       two hours fighting and could never have fixed from this side.
        #   receiving=False -> OURS. Nothing is arriving at all, which is an
        #       OBS-side problem only this machine can fix.
        # Since yt_end, every window starts a FRESH broadcast on the Pi (create, bind, testing,
        # live: ~2-3 min). While the Pi's keeper reports end_pending it is still doing that, and
        # reattaching the encoder here only restarts its work. Reattach only if nothing arrives
        # with no fresh broadcast pending, or if the keeper is still pending 5 min after OBS is back.
        back, reattached = time.time(), 0
        for attempt in range(8):            # ~8 min
            time.sleep(60)
            p = yt_probe()
            live, recv = p.get('live'), p.get('receiving')
            pending = bool((p.get('keeper') or {}).get('end_pending'))
            mlog('maintenance: probe %d -> live=%s receiving=%s id=%s keeper_pending=%s'
                 % (attempt, live, recv, p.get('broadcast_id'), pending))
            if live is True:
                mlog('maintenance: YouTube live on %s - done' % p.get('broadcast_id'))
                return
            if live is None:
                mlog('maintenance: probe failed (%s) - doing nothing on unknown'
                     % p.get('why'))
                continue
            stuck = pending and time.time() - back >= 300
            if reattached < 2 and ((recv is False and not pending) or stuck):
                reattach('keeper still pending after 5 min' if stuck else 'nothing reaching YouTube (receiving=False)')
                reattached += 1
            # otherwise the keeper is on it: leave it alone.
        p = yt_probe()
        if p.get('live') is not True:
            mlog('maintenance: YouTube still not live after 6 min (live=%s receiving=%s)'
                 % (p.get('live'), p.get('receiving')))
            try:
                discord('🟠 **YouTube still not live 6 min after a maintenance restart** — '
                        'live=%s receiving=%s. Twitch is unaffected. If receiving=true the '
                        'Pi-side keeper owns it and will alert separately.'
                        % (p.get('live'), p.get('receiving')))
            except Exception:
                pass
    except Exception as e:
        mlog('maintenance FAILED: %s: %s' % (type(e).__name__, e))
    finally:
        _maint['until'] = time.time() + 120            # settle before alerting again


def maint_check():
    now = time.localtime()
    hhmm, today = time.strftime('%H:%M', now), time.strftime('%Y-%m-%d', now)
    for at, down_s, on_date in MAINT:
        if hhmm != at or (on_date and on_date != today):
            continue
        key = today + at
        if key in _maint['done']:
            continue
        _maint['done'].add(key)
        mlog('maintenance window %s (%ds) starting' % (at, down_s))
        threading.Thread(target=do_maintenance, args=(down_s,), daemon=True).start()


# ------------------------------------------------------------------ frozen-output guard
# 2026-10-08 21:22: an NVIDIA driver reset (nvlddmkm 153) killed NVENC. OBS kept saying the
# stream was "active" while sending 0 bytes, so nothing noticed; StopStream/StartStream could
# not help (dead encoder). Only a full OBS restart recovers it. This watches the bytes.
_freeze = {'last_bytes': None, 'last_move': 0.0, 'restarted': 0.0}
OBS_EXE = os.path.join(os.environ.get('ProgramFiles', 'C:/Program Files'), 'obs-studio', 'bin', '64bit', 'obs64.exe')
FREEZE_S = 90


def restart_obs(reason):
    import glob
    mlog('freeze guard: %s - restarting OBS' % reason)
    subprocess.run(['taskkill', '/F', '/IM', 'obs64.exe'], capture_output=True)
    time.sleep(5)
    for f in glob.glob(os.path.join(os.environ.get('APPDATA', ''), 'obs-studio', '.sentinel', '*')):
        try:
            os.remove(f)
        except OSError:
            pass
    subprocess.Popen([OBS_EXE, '--collection', CFG['obs_collection'], '--scene', SCENE, '--disable-updater',
                      '--startstreaming', '--startreplaybuffer'], cwd=os.path.dirname(OBS_EXE))
    try:
        discord('🔴 **OBS stream froze** (%s). Restarted OBS automatically; the stream should be back within ~1 min.' % reason)
    except Exception:
        pass


_gameguard = {'same': 0, 'last': None, 'pi_last': None, 'reloads': 0, 'last_reload': 0.0}


def game_guard():
    """2026-10-09: the OBS "Game" layer (Pi /game, an MJPEG <img>) froze on its last frame while
    the Pi kept producing frames: a stalled stream never fires onerror. Compare the layer with the
    Pi's own frames every 10 s; if OBS is stuck 30 s while the Pi's picture changes, reload it."""
    import hashlib
    while True:
        try:
            o = obsws.connect()
            if o.call('GetCurrentProgramScene')['currentProgramSceneName'] == SCENE and not in_maintenance():
                img = o.call('GetSourceScreenshot', {'sourceName': 'Game', 'imageFormat': 'png', 'imageWidth': 240})['imageData']
                h = hashlib.md5(img.encode()).hexdigest()
                with urllib.request.urlopen('http://%s:8765/game.png' % PI, timeout=5) as r:
                    ph = hashlib.md5(r.read()).hexdigest()
                obs_same = h == _gameguard['last']
                pi_moved = _gameguard['pi_last'] is not None and ph != _gameguard['pi_last']
                _gameguard['same'] = _gameguard['same'] + 1 if (obs_same and pi_moved) else (_gameguard['same'] if obs_same else 0)
                _gameguard['last'], _gameguard['pi_last'] = h, ph
                if _gameguard['same'] >= 3:
                    o.call('PressInputPropertiesButton', {'inputName': 'Game', 'propertyName': 'refreshnocache'})
                    _gameguard['same'] = 0; _gameguard['reloads'] += 1; _gameguard['last_reload'] = time.time()
                    mlog('game guard: OBS Game layer frozen while the Pi kept playing - reloaded it')
                    if _gameguard['reloads'] in (1, 5, 20):
                        try:
                            discord('🟠 **Game picture froze on stream** (the Pi kept playing). Reloaded the Game layer automatically'
                                    + (' (%d times so far today).' % _gameguard['reloads'] if _gameguard['reloads'] > 1 else '.'))
                        except Exception:
                            pass
        except Exception:
            pass
        time.sleep(10)


def freeze_guard():
    while True:
        try:
            st = obsws.connect().call('GetStreamStatus')
            now, b = time.time(), st.get('outputBytes')
            if not st.get('outputActive') or b is None or _freeze['last_bytes'] is None or b != _freeze['last_bytes']:
                _freeze['last_move'] = now
            _freeze['last_bytes'] = b if st.get('outputActive') else None
            stuck = now - _freeze['last_move']
            if (st.get('outputActive') and stuck >= FREEZE_S and not in_maintenance()
                    and now - _freeze['restarted'] > 600):
                _freeze['restarted'] = now
                restart_obs('no data left OBS for %d s while it reported streaming' % stuck)
                _freeze['last_bytes'], _freeze['last_move'] = None, time.time()
        except Exception:
            _freeze['last_bytes'], _freeze['last_move'] = None, time.time()   # OBS down: the supervisor owns that
        time.sleep(15)


def watcher():
    while True:
        try:
            maint_check()
            snap = snapshot(record=True)
            if in_maintenance():
                # planned downtime: record it, but do not page anyone
                for st in _alert_state.values():
                    st['run'] = 0
            else:
                alert_pass(snap)
            sci_record()
            raw_record()
        except Exception as e:
            print('watcher:', e, flush=True)
        time.sleep(60)


# ------------------------------------------------------------------ live view
# Steering needs to see the game with no lag. Twitch runs seconds behind, so the
# panel shows OBS's own program output instead. One shared websocket and a short
# cache keep it cheap however many tabs are watching.
_frame = {'t': 0.0, 'jpg': b'', 'obs': None}
_frame_lock = threading.Lock()


def live_frame():
    import base64
    with _frame_lock:
        if time.time() - _frame['t'] < 0.2 and _frame['jpg']:
            return _frame['jpg']
        for attempt in (0, 1):
            try:
                if _frame['obs'] is None:
                    _frame['obs'] = obsws.connect()
                o = _frame['obs']
                scene = o.call('GetCurrentProgramScene')['sceneName']
                data = o.call('GetSourceScreenshot', {'sourceName': scene, 'imageFormat': 'jpg',
                                                      'imageWidth': 640, 'imageHeight': 360,
                                                      'imageCompressionQuality': 70})['imageData']
                _frame['jpg'] = base64.b64decode(data.split(',', 1)[1])
                _frame['t'] = time.time()
                return _frame['jpg']
            except Exception:
                _frame['obs'] = None      # reconnect once, then give up
                if attempt:
                    raise


# ------------------------------------------------------------------ actions
def act(name):
    o = obsws.connect()
    if name == 'stream_start':
        o.call('StartStream'); return 'stream started'
    if name == 'stream_stop':
        o.call('StopStream'); return 'stream stopped'
    if name == 'clip':
        o.call('SaveReplayBuffer')
        time.sleep(3)
        p = o.call('GetLastReplayBufferReplay').get('savedReplayPath', '?')
        return 'saved ' + p
    raise ValueError('unknown action ' + name)


# ---------------------------------------------------------------- access
# The panel can start/stop the stream and drive the fly, so opening it to the LAN
# without a secret would let anything on the network do both. localhost is exempt;
# everything else needs ?k=<token> once, then rides a cookie.
ACCF = HERE / 'panel_access.txt'


def access_token():
    if not ACCF.exists():
        import secrets
        ACCF.write_text(secrets.token_urlsafe(18))
        try:
            os.chmod(ACCF, 0o600)
        except OSError:
            pass
    return ACCF.read_text().strip()


class H(http.server.BaseHTTPRequestHandler):
    def authed(self):
        if self.client_address[0] in ('127.0.0.1', '::1'):
            return True
        tok = access_token()
        if 'k=' + tok in (self.path or ''):
            self._cookie = tok
            return True
        return ('flypanel=' + tok) in (self.headers.get('Cookie') or '')

    def _send(self, code, body, ctype='application/json'):
        b = body.encode() if isinstance(body, str) else body
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(b)))
        if getattr(self, '_cookie', None):
            self.send_header('Set-Cookie',
                             'flypanel=%s; Path=/; Max-Age=31536000; SameSite=Lax' % self._cookie)
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if not self.authed():
            return self._send(403, 'Add ?k=<token> to the URL once. The token is in '
                                   'panel_access.txt on the OBS machine.', 'text/plain')
        if self.path.startswith('/api/status'):
            return self._send(200, json.dumps(current_snapshot()))
        if self.path.startswith('/api/overlay'):
            # the Pi's overlay state for the OBS HUD; {} until the Pi publishes /panel/overlay
            try:
                return self._send(200, json.dumps(cached('_overlay', 0.8, lambda: pi_call('/panel/overlay'))))
            except Exception:
                return self._send(200, '{}')
        if self.path.startswith('/api/brain_layout'):
            # real neuron positions for the stream's brain layer (fixed sample; cached for a day)
            hit = _cache.get('_brain_layout')
            if not hit or time.time() - hit[0] > 86400:
                try:
                    d = pi_call('/panel/brain_layout')
                    if isinstance(d, dict) and d.get('neurons'):
                        _cache['_brain_layout'] = hit = (time.time(), d)
                except Exception:
                    pass
            return self._send(200, json.dumps(hit[1]) if hit else '{}')
        if self.path.startswith('/api/brain'):
            hrs = 6
            if '=' in self.path:
                try:
                    hrs = max(1, min(168, int(self.path.rsplit('=', 1)[1])))
                except ValueError:
                    pass
            now = cached('_brainnow', 5, brain_now)
            days = max(1, -(-hrs // 24))
            tr = cached('_trainers%d' % days, 60, lambda: pi_call('/panel/trainers?days=%d' % days))
            return self._send(200, json.dumps({'now': now, 'series': brain_series(hrs), 'trainers': tr}))
        if self.path.startswith('/api/config'):
            pub = {k: CFG.get(k) for k in ('twitch_channel', 'youtube_channel_id', 'youtube_handle', 'instagram', 'clips_dir')}
            return self._send(200, json.dumps(pub))
        if self.path.startswith('/api/live'):
            return self._send(200, json.dumps(cached('_live', 0.4, live_state)))
        if self.path.startswith('/api/findings'):
            # Hand-curated experiment results (findings.json), edited after each test.
            try:
                return self._send(200, (HERE / 'findings.json').read_text(encoding='utf-8'))
            except Exception:
                return self._send(200, '{}')
        if self.path.startswith('/api/inputs'):
            hrs = 24
            if '=' in self.path:
                try:
                    hrs = max(1, min(168, int(self.path.rsplit('=', 1)[1])))
                except ValueError:
                    pass
            # Per-viewer input report (chat/panel commands incl. refusals), from the Pi.
            d = cached('_inputs%d' % hrs, 60, lambda: pi_call('/panel/inputs?hours=%d' % hrs))
            return self._send(200, json.dumps(d))
        if self.path.startswith('/api/thoughts'):
            days = 1
            if '=' in self.path:
                try:
                    days = max(1, min(60, int(self.path.rsplit('=', 1)[1])))
                except ValueError:
                    pass
            # The fly's thought log, scored by flybrain against the tile map. Cached:
            # it only changes minute to minute.
            d = cached('_thoughts%d' % days, 60, lambda: pi_call('/panel/thoughts?days=%d' % days))
            return self._send(200, json.dumps(d))
        if self.path.startswith('/api/timeline'):
            return self._send(200, json.dumps(history_timeline()))
        if self.path.startswith('/api/history'):
            return self._send(200, json.dumps(history_summary()))
        if self.path.startswith('/api/science'):
            hrs = 6
            if '=' in self.path:
                try:
                    hrs = max(1, min(168, int(self.path.rsplit('=', 1)[1])))
                except ValueError:
                    pass
            return self._send(200, json.dumps({'hours': hrs, 'rows': sci_series(hrs),
                                               'run': cached('_run', 60, run_info)}))
        if self.path.startswith('/api/frame'):
            try:
                return self._send(200, live_frame(), 'image/jpeg')
            except Exception:
                return self._send(503, b'', 'image/jpeg')
        # strip ?k=<token> before routing, or the phone URL 404s
        if self.path.split('?')[0] in ('/', '/index.html'):
            # read_text() defaults to cp1252 on Windows, which mangles the arrows
            # and dashes into mojibake once re-encoded as utf-8.
            return self._send(200, (HERE / 'panel.html').read_text(encoding='utf-8'),
                              'text/html; charset=utf-8')
        if self.path.split('?')[0] == '/hud':
            return self._send(200, (HERE / 'hud.html').read_text(encoding='utf-8'), 'text/html; charset=utf-8')
        if self.path.split('?')[0] == '/brainviz':
            # the stream's brain layer (OBS browser source on this machine)
            return self._send(200, (HERE / 'brainviz.html').read_text(encoding='utf-8'),
                              'text/html; charset=utf-8')
        self._send(404, '{}')

    def do_POST(self):
        if not self.authed():
            return self._send(403, json.dumps({'ok': False, 'msg': 'not authorised'}))
        if self.path.startswith('/api/act/'):
            try:
                return self._send(200, json.dumps({'ok': True, 'msg': act(self.path.rsplit('/', 1)[1])}))
            except Exception as e:
                return self._send(200, json.dumps({'ok': False, 'msg': '%s: %s' % (type(e).__name__, e)}))
        if self.path == '/api/input':
            # Proxy straight to flybrain's /panel/control. We do not interpret it;
            # the Pi owns admin mode, the ceilings and the paid-steering refusal.
            try:
                n = int(self.headers.get('Content-Length', 0))
                body = json.loads(self.rfile.read(n) or b'{}')
                return self._send(200, json.dumps({'ok': True, **pi_call('/panel/control', 'POST', body)}))
            except urllib.error.HTTPError as e:
                code = {401: 'bad panel token', 403: 'not on the LAN', 429: 'rate limited'}
                return self._send(200, json.dumps({'ok': False, 'msg': code.get(e.code, 'HTTP %d' % e.code)}))
            except Exception as e:
                return self._send(200, json.dumps({'ok': False, 'msg': '%s: %s' % (type(e).__name__, e)}))
        self._send(404, '{}')

    def log_message(self, *a):
        pass


REPORTS_POSTED = HERE / 'reports_posted.json'


def report_poster():
    """Run reports to #run-reports: a "Week N" report every 7 days while a run is live, and the
    final report when the Pi marks the run ended (14-day clock or a manual reset). Each runs as a
    separate process (matplotlib stays out of the panel), retries every 10 min, never posts twice.
    reports_posted.json holds run ids (final) and "id:wN" (weekly)."""
    import subprocess, datetime as _dt
    time.sleep(120)

    def run_report(args, key, done):
        res = subprocess.run([sys.executable, str(HERE / 'run_report.py')] + args + ['--post'],
                             capture_output=True, text=True, timeout=600, cwd=str(HERE))
        if res.returncode == 0 and 'posted' in res.stdout:
            done.add(key)
            REPORTS_POSTED.write_text(json.dumps(sorted(done, key=str)))
            mlog('report: %s posted to #run-reports' % key)
        else:
            mlog('report: %s failed (%s) - retrying in 10 min' % (key, (res.stderr or res.stdout)[-200:]))

    while True:
        try:
            done = set(json.loads(REPORTS_POSTED.read_text()) if REPORTS_POSTED.exists() else [])
            for r in pi_call('/panel/runs') or []:
                rid = r['id']
                if r.get('ended') and not r.get('live'):
                    if rid not in done:
                        run_report(['--run', str(rid)], rid, done)
                    continue
                if not r.get('live') or r.get('kind') == 'test' or not r.get('started'):
                    continue
                started = _dt.datetime.fromisoformat(r['started']).timestamp()
                ends = _dt.datetime.fromisoformat(r['ends']).timestamp() if r.get('ends') else started + 14 * 86400
                week = int((time.time() - started) // (7 * 86400))
                for k in range(1, week + 1):
                    # the week that ends with the run is covered by the final report
                    if started + k * 7 * 86400 < ends - 3600 and '%s:w%d' % (rid, k) not in done:
                        run_report(['--run', str(rid), '--week', str(k)], '%s:w%d' % (rid, k), done)
        except Exception as e:
            mlog('report: check failed (%s: %s)' % (type(e).__name__, str(e)[:120]))
        time.sleep(600)


if __name__ == '__main__':
    # Prime the cache before serving. Otherwise the first page load races a cold
    # snapshot (12 checks, ~10 s) and shows an empty panel, which looks broken.
    print('priming checks...', flush=True)
    try:
        _live['snap'] = snapshot(record=True)
    except Exception as e:
        print('prime failed (continuing):', e, flush=True)
    threading.Thread(target=watcher, daemon=True).start()
    threading.Thread(target=refresher, daemon=True).start()
    threading.Thread(target=report_poster, daemon=True).start()
    threading.Thread(target=freeze_guard, daemon=True).start()
    threading.Thread(target=game_guard, daemon=True).start()
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    # Bound to all interfaces so a phone on the same wi-fi can reach it. Anything
    # that isn't localhost must present the token in panel_access.txt.
    with socketserver.ThreadingTCPServer(('0.0.0.0', PORT), H) as srv:
        import socket as _s
        try:
            ip = _s.gethostbyname(_s.gethostname())
        except Exception:
            ip = '<this-machine>'
        print('flypanel: http://127.0.0.1:%d  (local, no token needed)' % PORT, flush=True)
        print('          http://%s:%d/?k=%s  (phone, same wi-fi)' % (ip, PORT, access_token()),
              flush=True)
        srv.serve_forever()
