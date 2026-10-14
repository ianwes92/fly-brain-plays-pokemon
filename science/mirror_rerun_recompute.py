#!/usr/bin/env python3
"""Independent recompute of the mirror-world handedness test - RERUN 2026-10-08 (post steering fix).

Written against the PREREG definitions only, BEFORE seeing flybrain's scorer output:
    L  = 100*left/(left+right)        (fly's own frame, per arm)
    I  = (L_n + L_m)/2 - 50           intrinsic: survives mirroring
    E  = (L_n - L_m)/2                environmental: flips with the world
    one-sample t over the 12 seeds, |t| > 2.201 (df=11, two-tailed, alpha .05)
    secondary: same decomposition on T = 100*(1 - turn_below_zero)
    precondition: L_n significantly below 50
"""
import math

RAW = """0,normal,123,138,0.755789630957717
0,mirror,106,113,0.7274764679515913
1,normal,102,169,0.7688629911848199
1,mirror,121,131,0.7577319587628866
2,normal,127,158,0.7951591214701927
2,mirror,133,140,0.6919916330494547
3,normal,112,102,0.7299133811230586
3,mirror,87,109,0.6638278798744958
4,normal,94,122,0.7212759599581653
4,mirror,93,138,0.6815329448677723
5,normal,102,126,0.7604960406394741
5,mirror,120,123,0.7418198117436128
6,normal,135,173,0.8136112356193037
6,mirror,98,129,0.706484386672643
7,normal,97,111,0.6602506714413607
7,mirror,128,149,0.6921618204804045
8,normal,143,149,0.7790228597041685
8,mirror,115,165,0.7034961900493052
9,normal,84,144,0.6633049454654116
9,mirror,113,119,0.7303899596593456
10,normal,109,142,0.6260619766517939
10,mirror,101,100,0.6526221425369789
11,normal,122,146,0.7869415807560137
11,mirror,90,92,0.6608396832511579"""

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
