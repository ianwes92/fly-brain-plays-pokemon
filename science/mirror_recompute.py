#!/usr/bin/env python3
"""Independent recompute of the mirror-world handedness test.

Written against the PREREG definitions only, BEFORE seeing flybrain's scorer output:
    L  = 100*left/(left+right)        (fly's own frame, per arm)
    I  = (L_n + L_m)/2 - 50           intrinsic: survives mirroring
    E  = (L_n - L_m)/2                environmental: flips with the world
    one-sample t over the 12 seeds, |t| > 2.201 (df=11, two-tailed, alpha .05)
    secondary: same decomposition on T = 100*(1 - turn_below_zero)
    precondition: L_n significantly below 50
"""
import math

RAW = """0,normal,69,82,0.4972
0,mirror,106,113,0.7275
1,normal,74,82,0.5382
1,mirror,121,131,0.7577
2,normal,79,79,0.2415
2,mirror,133,140,0.6920
3,normal,95,101,0.4912
3,mirror,87,109,0.6638
4,normal,74,47,0.2937
4,mirror,93,138,0.6815
5,normal,104,87,0.3393
5,mirror,120,123,0.7418
6,normal,103,101,0.5035
6,mirror,98,129,0.7065
7,normal,69,80,0.2482
7,mirror,128,149,0.6922
8,normal,79,77,0.4672
8,mirror,115,165,0.7035
9,normal,110,120,0.3003
9,mirror,97,100,0.6597
10,normal,63,101,0.3899
10,mirror,101,100,0.6526
11,normal,79,81,0.2308
11,mirror,90,92,0.6608"""

rows = {}
for line in RAW.strip().splitlines():
    seed, arm, left, right, tbz = line.split(',')
    rows.setdefault(int(seed), {})[arm] = (int(left), int(right), float(tbz))

seeds = sorted(rows)


def mean(v):
    return sum(v) / len(v)


def sd(v):
    m = mean(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def ttest(v, mu=0.0):
    """One-sample t against mu. n=12 -> df=11 -> critical 2.201."""
    n = len(v)
    s = sd(v)
    t = (mean(v) - mu) / (s / math.sqrt(n)) if s else float('inf')
    ci = 2.201 * s / math.sqrt(n)
    return mean(v), s, t, ci


def report(name, vals, mu=0.0):
    m, s, t, ci = ttest(vals, mu)
    sig = abs(t) > 2.201
    print('  %-26s mean %+7.3f   sd %6.3f   t %+7.3f   95%% CI [%+.3f, %+.3f]   %s'
          % (name, m, s, t, m - ci, m + ci, 'SIGNIFICANT' if sig else 'not significant'))
    return m, t, sig


print('=== primary: turn choice (L = %% left) ===')
Ln = [100 * rows[s]['normal'][0] / (rows[s]['normal'][0] + rows[s]['normal'][1]) for s in seeds]
Lm = [100 * rows[s]['mirror'][0] / (rows[s]['mirror'][0] + rows[s]['mirror'][1]) for s in seeds]
print('  L_normal per seed:', ' '.join('%.1f' % x for x in Ln))
print('  L_mirror per seed:', ' '.join('%.1f' % x for x in Lm))
print()
print('  PRECONDITION - is L_normal significantly below 50?')
report('L_normal vs 50', Ln, 50.0)
print()
I = [(a + b) / 2 - 50 for a, b in zip(Ln, Lm)]
E = [(a - b) / 2 for a, b in zip(Ln, Lm)]
report('I (intrinsic)', I)
report('E (environmental)', E)

print()
print('=== secondary: brain signal (T = %% turn signal above zero) ===')
Tn = [100 * (1 - rows[s]['normal'][2]) for s in seeds]
Tm = [100 * (1 - rows[s]['mirror'][2]) for s in seeds]
print('  T_normal per seed:', ' '.join('%.1f' % x for x in Tn))
print('  T_mirror per seed:', ' '.join('%.1f' % x for x in Tm))
print()
report('T_normal vs 50', Tn, 50.0)
IT = [(a + b) / 2 - 50 for a, b in zip(Tn, Tm)]
ET = [(a - b) / 2 for a, b in zip(Tn, Tm)]
report('I_signal (intrinsic)', IT)
report('E_signal (environmental)', ET)
