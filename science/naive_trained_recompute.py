"""Blind recompute of naive_trained.jsonl against naive_trained_PREREG.md (obsfly)."""
import json, math, statistics as st
rows = [json.loads(l) for l in open('naive_trained.jsonl', encoding='utf-8') if l.strip()]
by = {}
for r in rows:
    by.setdefault(int(r['seed']), {})[r['arm']] = r
seeds = sorted(by)
assert seeds == list(range(12)) and all(set(by[s]) == {'naive', 'trained'} for s in seeds)

def betacf(a, b, x):
    qab, qap, qam, c, d = a + b, a + 1, a - 1, 1.0, 1 - (a + b) * x / (a + 1)
    d = 1 / (d if abs(d) > 1e-300 else 1e-300); h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1 + aa * d; d = 1 / (d if abs(d) > 1e-300 else 1e-300)
        c = 1 + aa / c if abs(1 + aa / c) > 1e-300 else 1e-300
        de = d * c; h *= de
        if abs(de - 1) < 1e-14: break
    return h

def ibeta(a, b, x):
    if x <= 0: return 0.0
    if x >= 1: return 1.0
    lb = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b) + a * math.log(x) + b * math.log(1 - x)
    if x < (a + 1) / (a + b + 2): return math.exp(lb) * betacf(a, b, x) / a
    return 1 - math.exp(lb) * betacf(b, a, 1 - x) / b

def p_two(t, df):
    return ibeta(df / 2, 0.5, df / (df + t * t))

CRIT = 2.201
def paired(f):
    d = []
    for s in seeds:
        a, b = f(by[s]['trained']), f(by[s]['naive'])
        if a is None or b is None: continue
        d.append(a - b)
    n = len(d); m = st.mean(d); sd = st.stdev(d) if n > 1 else 0
    if sd == 0:
        return dict(n=n, mean=m, sd=0, t=None, p=None, ci=(m, m))
    se = sd / math.sqrt(n); t = m / se
    return dict(n=n, mean=m, sd=sd, t=t, p=p_two(t, n - 1), ci=(m - CRIT * se, m + CRIT * se), d=d)

nm = st.mean(by[s]['naive']['attraction_turns'] for s in seeds)
tm = st.mean(by[s]['trained']['attraction_turns'] for s in seeds)
P = paired(lambda r: r['attraction_turns'])
print('MANIPULATION CHECK opinions_start (trained vs naive):', by[0]['trained']['opinions_start'], by[0]['naive']['opinions_start'])
print('PRIMARY attraction reactivity: naive mean %.3f, trained mean %.3f' % (nm, tm))
print('  d per seed:', [int(x) for x in P['d']])
print('  mean d %.3f, sd %.3f, t(11)=%.3f, p=%.4f, 95%% CI [%.2f, %.2f]; +/-20%% of naive mean = +/-%.2f' % (P['mean'], P['sd'], P['t'], P['p'], P['ci'][0], P['ci'][1], .2 * nm))
within = -0.2 * nm <= P['ci'][0] and P['ci'][1] <= 0.2 * nm
print('  DECISION:', 'SIGNIFICANT' if abs(P['t']) > CRIT else ('NO DETECTABLE EFFECT (CI within +/-20%)' if within else 'INCONCLUSIVE'))
E = paired(lambda r: r['opportunities'])
print('EXPOSURE opportunities: mean d %.1f, t(11)=%.3f -> %s' % (E['mean'], E['t'], 'ARMS DIFFER IN EXPOSURE' if abs(E['t']) > CRIT else 'no difference'))

def ratio(num, den):
    return lambda r: (r[num] - r[den]) / (r[num] + r[den]) if (r[num] + r[den]) else None
sec = [('1 mean mood valence', lambda r: r['mean_valence']),
       ('2 moves', lambda r: r['moves']),
       ('3 bumps per tile', lambda r: r['bumps'] / r['moves'] if r['moves'] else None),
       ('4 walking fraction', lambda r: r['walking_frac']),
       ('5 left share of turns', lambda r: r['left'] / (r['left'] + r['right'])),
       ('6 attraction turns per opportunity', lambda r: r['attraction_turns'] / r['opportunities']),
       ('7 net approach to people', ratio('toward_person', 'away_from_person')),
       ('8 net approach to exits', ratio('toward_exit', 'away_from_exit'))]
res = [(n, paired(f)) for n, f in sec]
# Holm across all eight; an untestable measure (sd 0, d = 0 everywhere) enters with p = 1.
order = sorted(range(8), key=lambda i: res[i][1]['p'] if res[i][1]['p'] is not None else 1.0)
holm, prev = {}, 0
for k, i in enumerate(order):
    p = res[i][1]['p'] if res[i][1]['p'] is not None else 1.0
    adj = min(1.0, max(prev, (8 - k) * p)); prev = adj; holm[i] = adj
print('SECONDARIES (Holm across 8):')
for i, (n, r) in enumerate(res):
    if r['t'] is None:
        print('  %-36s n=%d  d=%.4f for every pair (sd 0): untestable, no away turns possible' % (n, r['n'], r['mean'])); continue
    print('  %-36s n=%d mean d %+.5f  t(11)=%+.3f  p=%.5f  Holm p=%.5f  %s  CI [%+.5f, %+.5f]'
          % (n, r['n'], r['mean'], r['t'], r['p'], holm[i], 'SURVIVES' if holm[i] < .05 else 'descriptive', r['ci'][0], r['ci'][1]))
bpt = [abs(r['bumps_per_tile'] - r['bumps'] / r['moves']) for r in rows if r['moves']]
print('check: file bumps_per_tile vs bumps/moves max abs diff %.2e' % max(bpt))
