"""Run report: one PDF per run of the fly, posted to Discord #run-reports.

    python run_report.py --run 4            # build (draft if the run is still live)
    python run_report.py --run 2 --post     # build and upload to #run-reports

Source of truth is the Pi's /panel/run_export?id=N (run-scoped totals, a 10-minute
cumulative series, viewers, ledger, thoughts, notes on what is missing and why). This
machine's minute-level raw archive (data/raw) only adds what the Pi doesn't keep:
audience and Pi temperature. Missing data is shown as missing, never guessed.
"""
import argparse, datetime as dt, json, pathlib, sys, textwrap, urllib.request

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as md
from matplotlib.backends.backend_pdf import PdfPages

HERE = pathlib.Path(__file__).resolve().parent
RAW = HERE / 'data' / 'raw'
sys.path.insert(0, str(HERE))

INK, DIM, FAINT, LINE = '#1b1b24', '#5c5c6e', '#9a9aab', '#e4e4ec'
OK, WARN, BAD, ACC, TW = '#2f9e6b', '#d98a1c', '#d23c41', '#7c4ddb', '#8a3ffc'
plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 9, 'axes.edgecolor': LINE,
                     'axes.labelcolor': DIM, 'xtick.color': DIM, 'ytick.color': DIM,
                     'axes.spines.top': False, 'axes.spines.right': False, 'axes.grid': True,
                     'grid.color': '#f0f0f5', 'grid.linewidth': .8, 'axes.titleweight': 'bold',
                     'axes.titlesize': 10.5, 'axes.titlecolor': INK, 'axes.titlelocation': 'left'})
A4 = (8.27, 11.69)
from config import CFG
FOOTER = ('Fly Brain Plays Pokémon'
          + (' · twitch.tv/' + CFG['twitch_channel'] if CFG.get('twitch_channel') else '')
          + (' · youtube.com/' + CFG['youtube_handle'] if CFG.get('youtube_handle') else ''))
LAB_FIRST = 4      # runs 1-3 were test runs; run 4 is the first lab run (Ian, 2026-10-08)


def run_label(run):
    run = int(run)
    return ('Lab Run %d' % (run - LAB_FIRST + 1)) if run >= LAB_FIRST else 'Test Run %d' % run


def post(pdf_path, text, channel='run-reports'):
    """Upload the PDF to a Discord channel through its webhook (multipart)."""
    import uuid
    hook = json.loads((HERE / 'discord_ids.json').read_text())[channel]['webhook']
    b = uuid.uuid4().hex
    pdf = pathlib.Path(pdf_path)
    nl = '\r\n'
    head = lambda extra: ('--%s' % b + nl + extra + nl + nl).encode()
    body = (head('Content-Disposition: form-data; name="payload_json"' + nl + 'Content-Type: application/json')
            + json.dumps({'content': text, 'allowed_mentions': {'parse': []}}).encode() + nl.encode()
            + head('Content-Disposition: form-data; name="files[0]"; filename="%s"' % pdf.name + nl + 'Content-Type: application/pdf')
            + pdf.read_bytes() + (nl + '--%s--' % b + nl).encode())
    req = urllib.request.Request(hook + '?wait=true', data=body, method='POST',
                                 headers={'Content-Type': 'multipart/form-data; boundary=' + b, 'User-Agent': 'flybrain-reports/1.0'})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r).get('id')


# ------------------------------------------------------------------ data
def export(run):
    import flypanel
    return flypanel.pi_call('/panel/run_export?id=%d' % int(run))


def epoch(iso):
    return dt.datetime.fromisoformat(iso).timestamp() if iso else None


def load_raw(t0, t1):
    out, d = [], dt.date.fromtimestamp(t0)
    while d <= dt.date.fromtimestamp(t1):
        f = RAW / ('%s.jsonl' % d.isoformat())
        if f.exists():
            for line in f.open(encoding='utf-8'):
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                if t0 <= r.get('t', 0) <= t1:
                    out.append(r)
        d += dt.timedelta(days=1)
    return out


def num(v):
    try:
        return float(str(v).rstrip('sx%'))
    except (TypeError, ValueError):
        return None


def get(r, *path):
    for p in path:
        r = r.get(p) if isinstance(r, dict) else None
    return r


def deltas(samples, key):
    """Per-minute rate between consecutive cumulative samples (restart-proof on the Pi)."""
    ts, out = [], []
    for a, b in zip(samples, samples[1:]):
        if key in a and key in b and b['t'] > a['t'] and b[key] >= a[key]:
            ts.append(b['t']); out.append((b[key] - a[key]) / (b['t'] - a['t']) * 60)
    return ts, out


def bucket(ts, vals, width, fn=None):
    b = {}
    for t, v in zip(ts, vals):
        if v is not None:
            b.setdefault(int(t // width), []).append(v)
    keys = sorted(b)
    fn = fn or (lambda a: sum(a) / len(a))
    return [dt.datetime.fromtimestamp(k * width + width / 2) for k in keys], [fn(b[k]) for k in keys]


# ------------------------------------------------------------------ page helpers
_PAGE = [0]


def page(title, sub=None):
    _PAGE[0] += 1
    fig = plt.figure(figsize=A4)
    fig.text(.07, .955, title, fontsize=17, weight='bold', color=INK)
    if sub:
        fig.text(.07, .935, sub, fontsize=9, color=DIM)
    fig.text(.07, .025, FOOTER, fontsize=7.5, color=FAINT)
    fig.text(.93, .025, 'page %d' % _PAGE[0], fontsize=7.5, color=FAINT, ha='right')
    return fig


def para(fig, x, y, text, width=95, size=9, color=DIM, lh=.0155):
    lines = []
    for block in text.split('\n'):
        lines += textwrap.wrap(block, width) or ['']
    for i, l in enumerate(lines):
        fig.text(x, y - i * lh, l, fontsize=size, color=color)
    return y - len(lines) * lh


def label(fig, x, y, text):
    fig.text(x, y, text.upper(), fontsize=7.5, color=FAINT, weight='bold')


def kpis(fig, y, items, x0=.07, w=.215):
    for i, (v, l) in enumerate(items):
        x, yy = x0 + (i % 4) * w, y - (i // 4) * .062
        v = '—' if v is None else str(v)
        fig.text(x, yy, v, fontsize=17 if len(v) <= 11 else 12, weight='bold', color=INK)
        fig.text(x, yy - .018, l.upper(), fontsize=6.8, color=FAINT)
    return y - ((len(items) + 3) // 4) * .062


def table(fig, x, y, rows, cols, widths, size=8, lh=.0172, head=True, floor=.06):
    if head:
        for c, h in enumerate(cols):
            fig.text(x + sum(widths[:c]), y, h.upper(), fontsize=6.8, color=FAINT)
        y -= lh
    for r in rows:
        if y < floor:
            fig.text(x, y, '…', fontsize=size, color=FAINT)
            break
        for c, v in enumerate(r):
            fig.text(x + sum(widths[:c]), y, str(v), fontsize=size, color=INK if c == 0 else DIM)
        y -= lh
    return y


def timefmt(ax, span_h):
    ax.xaxis.set_major_formatter(md.DateFormatter('%b %d' if span_h > 240 else '%b %d\n%H:%M'))
    ax.xaxis.set_major_locator(md.AutoDateLocator(maxticks=7))


def missing(ax, text):
    ax.axis('off')
    ax.text(0, .5, text, fontsize=9, color=FAINT, transform=ax.transAxes)


# ------------------------------------------------------------------ analysis
def diagnose(E, raw):
    """What held the fly back this run: measured failure modes with candidate changes.
    Changes are made only between runs, with Ian, and logged in the next report."""
    out, a, span = [], E.get('attrib') or {}, (E.get('attrib') or {}).get('span_h') or 0
    st = get(E, 'stillness', 'fly') or {}
    if st and span:
        share = st.get('total_s', 0) / (span * 3600)
        if share > .05 or (st.get('longest_s') or 0) > 300:
            out.append(('Standing still',
                        'Still for %.0f%% of fly-only time; longest stretch %.0f s; %.0f%% of it in dialogue; near people %.0f%% of the time.'
                        % (100 * share, st.get('longest_s', 0), 100 * (st.get('dialogue_share') or 0), 100 * (st.get('near_people_share') or 0)),
                        'If stretches cluster near people, it is stalling at NPCs: try weaker people attraction, or more spontaneous turning when nothing changes.'))
    if a.get('tiles_clean'):
        bs = a['bumps_clean'] / max(1, a['bumps_clean'] + a['tiles_clean'])
        if bs > .2:
            out.append(('Walking into things', '%.0f%% of its own moves were bumps into walls or objects.' % (100 * bs),
                        'A stronger obstacle signal to the eyes, or a short back-off after two bumps on the same tile.'))
    ph = get(E, 'thoughts', 'phantom_stop_pct')
    if ph and ph > 35:
        out.append(('Phantom stops', '%.0f%% of its stops had nothing actually in the way.' % ph,
                    'The "blocked" readout fires on noise: raise its threshold or require two readings in a row.'))
    if span and a.get('rule_pain') is not None and a['rule_pain'] / span > 5:
        out.append(('Stillness-rule pain', '%d automatic pains, %.1f per hour of play.' % (a['rule_pain'], a['rule_pain'] / span),
                    'A high rate teaches it to fear wherever it stalls. Check where they land; consider a longer grace period.'))
    att = get(E, 'progress', 'attributed') or []
    areas = [x for x in att if x.get('kind') == 'area']
    helped = [x for x in areas if x.get('by') != 'fly']
    if len(areas) >= 3 and len(helped) / len(areas) > .4:
        out.append(('New areas needed help', '%d of %d new areas were reached with chat helping.' % (len(helped), len(areas)),
                    'Doors and route exits are hard for it: stronger door attraction so it leaves towns on its own.'))
    if raw:
        n = len(raw)
        sb = sum(1 for r in raw if get(r, 'game', 'cb2', 'battle') == 'True' and (num(get(r, 'game', 'stuck', 'menu')) or 0) >= 60) / n
        if sb > .03:
            out.append(('Slow battles', '%.0f%% of minutes were a battle menu open 60 s+ without progress.' % (100 * sb),
                        'Battles depend on random A/B and cursor presses: a clearer FIGHT cue, or retune the A-press cap.'))
    rs = E.get('restarts') or {}
    if (rs.get('crashes') or 0) + (rs.get('watchdog') or 0) > 3:
        out.append(('Reliability', '%d crashes and %d watchdog restarts.' % (rs.get('crashes', 0), rs.get('watchdog', 0)),
                    'Each restart resets per-process counters and interrupts behaviour; fix the causes before the next run.'))
    if not out:
        out.append(('Nothing stood out', 'No failure mode crossed its threshold this run.',
                    'Keep the settings for the next run so the runs stay comparable.'))
    return out


# ------------------------------------------------------------------ report
def build(run, out, E=None, week=None):
    E = E or export(run)
    t0 = epoch(E['started'])
    live = not E.get('ended')
    t1 = epoch(E['ended']) if E.get('ended') else dt.datetime.now().timestamp()
    span_h = (t1 - t0) / 3600
    raw = load_raw(t0, t1)
    P, A, L = E.get('progress') or {}, E.get('attrib') or {}, E.get('learning')
    S = (E.get('series') or {}).get('samples') or []
    OP = (E.get('series') or {}).get('opinions') or []
    V = sorted(E.get('viewers') or [], key=lambda v: -(v.get('inputs') or 0))
    att = P.get('attributed') or []
    st = P.get('starter') or {}
    width = 3600 if span_h <= 96 else 6 * 3600
    tw = [num(get(r, 'audience', 'twitch')) for r in raw]
    tw = [v for v in tw if v is not None]
    name = run_label(run)
    _PAGE[0] = 0

    with PdfPages(out) as pdf:
        # ---- 1. cover
        fig = page('Run %s · %s%s' % (run, name, (' · Week %d report' % week) if week else (' (in progress)' if live else '')),
                   '%s → %s · %.1f days · %s' % (dt.datetime.fromtimestamp(t0).strftime('%b %d %Y, %H:%M'),
                                                   'now' if live else dt.datetime.fromtimestamp(t1).strftime('%b %d %Y, %H:%M'),
                                                   span_h / 24, E.get('reason') or 'still running'))
        intro = ('A simulated fruit-fly brain (139,255 neurons from the FlyWire connectome) plays Pokémon FireRed on a '
                 'live stream. Chat can steer it and train it with !pain / !pleasure. Every run starts from a brand-new '
                 'game and an untrained brain.')
        if E.get('kind') == 'test':
            intro += (' This was a TEST RUN, made while the system was still being built: measurement was incomplete and '
                      'the code changed during the run, so treat its numbers as rough. Lab runs start with run 4.')
        y = para(fig, .07, .9, intro, size=9.5)
        own = A.get('tiles_clean'), A.get('tiles_assisted')
        alone = [x for x in att if x.get('by') == 'fly']
        y = kpis(fig, y - .03, [
            ('%.0f h' % span_h if span_h < 72 else '%.1f d' % (span_h / 24), 'run length'),
            (A.get('tiles'), 'steps walked' + (' (last %.0f h)' % A['span_h'] if A.get('span_h') and A['span_h'] < span_h * .9 else '')),
            (('%.0f%%' % (100 * own[0] / max(1, own[0] + own[1]))) if own[0] is not None else None, 'steps on its own'),
            (('%d / %d' % (len(alone), len(att))) if att else None, 'milestones alone' + (' (tracked)' if att and len(att) < len(P.get('milestones') or []) else '')),
            ((st.get('species') or '—').title(), 'starter' + {'fly': ' (fly)', '?': ' (unknown)'}.get(st.get('by'), '')),
            (P.get('badges'), 'badges'),
            (len(P.get('caught') or []), 'pokémon caught' + (' (logged)' if E.get('kind') == 'test' else '')),
            (P.get('furthest'), 'furthest place'),
            (sum(v.get('inputs') or 0 for v in V) if V else None, 'human inputs'),
            (len([v for v in V if v.get('login') != 'streamer']) if V else None, 'viewers who played'),
            (int(max(tw)) if tw else None, 'twitch peak viewers'),
            ((E.get('restarts') or {}).get('crashes'), 'crashes' + (' (incl. dev loops)' if ((E.get('restarts') or {}).get('crashes') or 0) > 20 else '')),
        ])
        notes = list(E.get('notes') or [])
        W = E.get('windows') or {}
        late = [(k, v) for k, v in W.items() if k.endswith('_from') and epoch(v) and epoch(v) - t0 > 3600]
        if late:
            nice = {'viewers_journal_from': 'people table', 'attrib_samples_from': 'steps and dopamine counts',
                    'ledger_from': 'learning by source', 'learning_bursts_from': 'trainer ranking'}
            notes.append('Each block counts only from its own start: ' + '; '.join(
                '%s from %s' % (nice.get(k, k), dt.datetime.fromisoformat(v).strftime('%b %d %H:%M')) for k, v in late)
                + '. Learning from before the ledger existed is booked as "before tracking", not to any viewer.')
        if notes:
            label(fig, .07, y - .015, 'What is missing or different in this run')
            y = y - .035
            for n in notes:
                y = para(fig, .07, y, '• ' + n, size=8.3, color=DIM) - .004
        y -= .02
        label(fig, .07, y, 'Milestones')
        import re
        key = lambda t: re.sub(r'\s*\(.*?\)', '', t).strip().upper()
        byt = {}
        for x in att:
            byt.setdefault(key(x['text']), []).append(x.get('by'))
        ms = []
        for m in (P.get('milestones') or []):
            b = byt.get(key(m['text']))
            ms.append({'text': m['text'], 'kind': m.get('kind'), 'by': b.pop(0) if b else m.get('by')})
        seen = {key(m['text']) for m in ms}
        ms = [{'text': x['text'], 'kind': x.get('kind'), 'by': x.get('by')} for x in att if key(x['text']) not in seen and x.get('kind') == 'area'] + ms
        rows = [[('★ ' if m.get('kind') in ('caught', 'party', 'badge', 'evolve') else '· ') + m['text'],
                 {'fly': 'fly alone', 'assisted': 'chat helped'}.get(m.get('by'), 'not attributable')] for m in ms]
        room = (y - .02) - .06
        lh = max(.0098, min(.0158, room / max(1, len(rows))))
        table(fig, .07, y - .02, rows, ['', ''], [.6, .2], head=False, lh=lh, size=8 if lh > .0125 else 6.6)
        pdf.savefig(fig); plt.close(fig)

        # ---- 2. progress & who drove
        fig = page('Progress and who was driving', 'Fly alone vs humans, and the people who played')
        label(fig, .07, .9, 'Areas reached'); para(fig, .07, .88, ', '.join(P.get('areas') or ['—']), size=9)
        label(fig, .07, .84, 'Caught'); para(fig, .07, .82, ', '.join(c.replace('CAUGHT ', '').rstrip('!').title() for c in (P.get('caught') or [])) or '—', size=9)
        ax = fig.add_axes([.09, .55, .84, .2])
        if len(S) > 2 and 'tiles_assisted' in S[0]:
            ts, cl = deltas(S, 'tiles_clean'); _, asx = deltas(S, 'tiles_assisted')
            sh = [100 * b / (a_ + b) if (a_ + b) > 0 else None for a_, b in zip(cl, asx)]
            x, v = bucket(ts, sh, width)
            ax.fill_between(x, v, color=TW, alpha=.18); ax.plot(x, v, color=TW, lw=1.4)
            ax.set_ylim(0, 100); ax.set_ylabel('% of steps'); timefmt(ax, span_h)
            ax.set_title('Share of steps where a human was helping')
        else:
            missing(ax, 'No step attribution recorded for this run.')
        label(fig, .07, .49, 'People (top %d of %d)' % (min(22, len(V)), len(V)))
        rows = []
        for v in V[:22]:
            vs = v.get('vs_fly') or {}
            s_ = sum(vs.values())
            d = v.get('delivered') or {}
            rows.append([{'streamer': 'streamer (panel)'}.get(v['login'], v['login'])[:26], v.get('inputs', 0),
                         '%d%%' % (100 * (v.get('accepted') or 0) / max(1, v.get('inputs') or 1)),
                         ('%d%% with · %d%% against' % (100 * vs.get('together', 0) / s_, 100 * vs.get('against', 0) / s_)) if s_ else '—',
                         '%d / %d' % (d.get('pain', 0), d.get('pleasure', 0)), ', '.join(v.get('flags') or [])])
        if rows:
            table(fig, .07, .465, rows, ['who', 'inputs', 'through', 'steering vs the fly', 'pain / pleasure', 'flags'],
                  [.24, .08, .08, .22, .12, .12])
        else:
            para(fig, .07, .465, 'No input journal for this run.', color=FAINT)
        pdf.savefig(fig); plt.close(fig)

        # ---- 3. learning
        fig = page('Learning', 'What shaped the brain, and how its opinions moved')
        ax = fig.add_axes([.09, .63, .84, .25])
        if OP:
            for k, c, lab in [('people', OK, 'people'), ('exits', WARN, 'doors / exits')]:
                ax.plot([dt.datetime.fromtimestamp(o['t']) for o in OP], [o[k] for o in OP], color=c, lw=1.2, label=lab)
            ax.axhline(0, color=FAINT, lw=.8, ls='--'); ax.set_ylim(-1, 1); timefmt(ax, span_h)
            ax.legend(frameon=False, loc='upper left'); ax.set_title('Opinion of people and doors after each lesson (−1 avoids … +1 seeks)')
        else:
            missing(ax, 'No opinion history for this run.')
        if L:
            label(fig, .07, .56, 'Learned change booked to each source')
            src = {'human': 'chat (humans)', 'game': 'the game', 'rule': 'stillness rule', 'before_ledger': 'before tracking'}
            rows = [[src.get(k, k), '%.0f' % (L.get('depressed') or {}).get(k, 0), '%.0f' % (L.get('outstanding') or {}).get(k, 0)]
                    for k in sorted(set((L.get('depressed') or {})) | set(L.get('outstanding') or {}), key=lambda k: -((L.get('depressed') or {}).get(k, 0)))]
            y = table(fig, .07, .535, rows, ['source', 'total', 'still in effect'], [.2, .1, .12])
            para(fig, .07, min(y, .41) - .02, 'Faded this run: %.0f units. Lessons fade with a 2-hour time constant.' % (L.get('recovered') or 0), size=8.5)
        else:
            para(fig, .07, .54, 'No learning ledger for this run (it started Oct 7).', color=FAINT)
        label(fig, .52, .56, 'Dopamine delivered')
        table(fig, .52, .535, [[l, A.get(k, '—')] for k, l in [('human_pain', 'chat pain'), ('human_pleasure', 'chat pleasure'),
              ('game_pain', 'game pain'), ('game_pleasure', 'game pleasure'), ('rule_pain', 'stillness-rule pain')]],
              ['source', 'count'], [.25, .1])
        tr = get(E, 'trainers', 'trainers') or []
        if tr:
            label(fig, .07, .3, 'Top trainers (learned change booked)')
            table(fig, .07, .275, [[t['who'][:24], t['stims'], '%.0f' % float(t['booked'])] for t in tr[:12]],
                  ['who', 'stims', 'units'], [.26, .08, .1])
        pdf.savefig(fig); plt.close(fig)

        # ---- 4. behaviour & brain
        fig = page('Behaviour and brain', 'Movement, neural activity and thoughts')
        ax = fig.add_axes([.09, .7, .84, .18])
        if len(S) > 2:
            for k, c, lab in [('tiles_clean', OK, "fly's own steps"), ('tiles_assisted', TW, 'chat-assisted steps'), ('bumps_clean', BAD, "fly's own bumps")]:
                if k in S[0]:
                    x, v = bucket(*deltas(S, k), width)
                    ax.plot(x, v, color=c, lw=1.3, label=lab)
            ax.set_ylabel('per minute'); timefmt(ax, span_h); ax.legend(frameon=False, ncol=3, loc='upper left', fontsize=8)
            ax.set_title('Movement')
        else:
            missing(ax, 'No movement samples for this run.')
        ax = fig.add_axes([.09, .46, .84, .17])
        if len(S) > 2 and 'spikes' in S[0]:
            ts, v = deltas(S, 'spikes')
            x, v = bucket(ts, [s / 1e6 for s in v], width)
            ax.fill_between(x, v, color=ACC, alpha=.15); ax.plot(x, v, color=ACC, lw=1.3); timefmt(ax, span_h)
            ax.set_ylabel('million / min'); ax.set_title('Neural activity: spikes across all 139,255 neurons')
        else:
            missing(ax, 'No neural-activity samples for this run.')
        T = E.get('thoughts') or {}
        if T.get('kinds'):
            label(fig, .07, .39, 'Thoughts (the captions on stream): %s total, %.0f per minute, %.0f%% of stops had nothing in the way'
                  % ('{:,}'.format(T.get('total', 0)), T.get('per_min') or 0, T.get('phantom_stop_pct') or 0))
            tot = sum(k.get('n', 0) for k in T['kinds'].values()) or 1
            rows = [[k.replace('_', ' ').replace(' X', ' left/right')[:40], v.get('n', 0), '%.0f%%' % (100 * v.get('n', 0) / tot),
                     '%.0f%%' % (100 * v.get('clean', 0) / max(1, v.get('n', 1)))]
                    for k, v in sorted(T['kinds'].items(), key=lambda kv: -kv[1].get('n', 0))[:12]]
            table(fig, .07, .365, rows, ['thought', 'count', 'share', "fly's own"], [.42, .1, .1, .1])
        now = E.get('now') or {}
        if now.get('turn_sides'):
            pass
        pdf.savefig(fig); plt.close(fig)

        # ---- 5. what held it back
        fig = page('What held it back', 'Measured failure modes this run, and candidate changes for the next run')
        y = para(fig, .07, .9, 'Each item is measured from this run. Suggestions are candidates only: any change is made between '
                 'runs, agreed with the streamer, logged in the next report, and never made during an experiment, so every run '
                 'stays a clean comparison.', size=8.5, color=FAINT) - .02
        for head, ev_, sug in diagnose(E, raw):
            fig.text(.07, y, head, fontsize=11, weight='bold', color=INK)
            y = para(fig, .07, y - .02, ev_, size=9, color=DIM)
            y = para(fig, .07, y - .004, 'Next run: ' + sug, size=9, color=ACC) - .02
        if E.get('settings'):
            label(fig, .07, y - .01, 'Behaviour settings this run')
            y = para(fig, .07, y - .03, ', '.join('%s=%s' % kv for kv in sorted(E['settings'].items()) if not isinstance(kv[1], (dict, list)))[:900], size=7.5)
        pdf.savefig(fig); plt.close(fig)

        # ---- 6. experiments, reliability, audience
        fig = page('Experiments, reliability and audience')
        y = .9
        label(fig, .07, y, 'Experiments during this run'); y -= .025
        ex = E.get('experiments') or []
        for e in ex:
            y = para(fig, .07, y, '• %s (%s): %s' % (e['name'].replace('_', ' '), e.get('night', ''), e.get('status', '')), size=9) - .006
        if not ex:
            y = para(fig, .07, y, 'None.', size=9, color=FAINT)
        rs = E.get('restarts') or {}
        label(fig, .07, y - .02, 'Reliability')
        y = para(fig, .07, y - .045, 'Code deploys: %s · crashes: %s · watchdog restarts: %s. Every restart resets in-process counters; '
                 'run totals here are restart-proof.' % (rs.get('deploys', '—'), rs.get('crashes', '—'), rs.get('watchdog', '—')), size=9)
        ax = fig.add_axes([.09, .3, .84, .22])
        if tw:
            for k, c, lab in [('twitch', TW, 'Twitch'), ('youtube', '#e0443c', 'YouTube')]:
                x, v = bucket([r['t'] for r in raw], [num(get(r, 'audience', k)) for r in raw], width, max)
                ax.plot(x, v, color=c, lw=1.4, label=lab)
            ax.set_ylim(bottom=0); timefmt(ax, span_h); ax.legend(frameon=False, loc='upper left')
            cover = len(raw) / max(1, (t1 - t0) / 60)
            ax.set_title('Peak viewers per %s (recorded for %.0f%% of the run)' % ('hour' if width == 3600 else '6 hours', 100 * min(1, cover)))
        else:
            missing(ax, 'Audience was not recorded during this run.')
        para(fig, .07, .2, 'Data: FlyWire FAFB v783 (CC-BY 4.0); Dorkenwald et al. 2024, Schlegel et al. 2024. Counts on the Pi '
             'are cumulative and restart-proof; a step, frame or milestone is the fly\'s own only if no human key reached the '
             'game in the previous 5 seconds.', size=8, color=FAINT)
        pdf.savefig(fig); plt.close(fig)
    return out


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--run', required=True, type=int)
    ap.add_argument('--out')
    ap.add_argument('--post', action='store_true', help='upload to #run-reports when done')
    ap.add_argument('--week', type=int, help='interim weekly report for a live run')
    a = ap.parse_args()
    E = export(a.run)
    out = a.out or str(HERE / 'reports' / ('run%d_%s%s.pdf' % (a.run, run_label(a.run).lower().replace(' ', '_'),
                         ('_week%d' % a.week) if a.week else ('' if E.get('ended') else '_draft'))))
    pathlib.Path(out).parent.mkdir(exist_ok=True)
    print(build(a.run, out, E, a.week))
    if a.post and a.week:
        print('posted', post(out, '**Run %d · %s · Week %d report** (run in progress, day %d): %s → now' % (
            a.run, run_label(a.run), a.week, a.week * 7, E['started'][:10])))
    elif a.post:
        msg = '**Run %d · %s** report: %s → %s%s' % (
            a.run, run_label(a.run), E['started'][:10], (E.get('ended') or 'in progress')[:10],
            ('. ' + E['reason'].replace('manual: ', 'Ended early: ')) if E.get('reason') else '')
        if E.get('kind') == 'test':
            msg += '\n_Test run: built while the system was still being developed, so measurement is incomplete. Lab runs start with run 4._'
        print('posted', post(out, msg))
