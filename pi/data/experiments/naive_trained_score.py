"""Scores naive_trained.jsonl exactly as naive_trained_PREREG.md says.
Written before any data; not to be changed after."""
import json
import math
import os
import statistics as st

HERE = os.path.dirname(os.path.abspath(__file__))
T_CRIT = 2.201          # t(11), two-sided 0.05


def t_test(d):
    n = len(d)
    m = st.mean(d)
    se = st.stdev(d) / math.sqrt(n) if n > 1 else float("nan")
    t = m / se if se else float("nan")
    return m, se, t, (m - T_CRIT * se, m + T_CRIT * se)


def p_two_sided(t, df=11):
    # Student t survival by numerical integration (stdlib only).
    x = abs(t)
    f = lambda u: (1 + u * u / df) ** (-(df + 1) / 2)
    c = math.gamma((df + 1) / 2) / (math.sqrt(df * math.pi) * math.gamma(df / 2))
    n, hi = 20000, x + 60
    h = (hi - x) / n
    tail = c * h * (f(x) / 2 + sum(f(x + i * h) for i in range(1, n)) + f(hi) / 2)
    return min(1.0, 2 * tail)


def main(path=os.path.join(HERE, "naive_trained.jsonl")):
    rows = [json.loads(x) for x in open(path) if x.strip()]
    by = {}
    for r in rows:
        by.setdefault(r["seed"], {})[r["arm"]] = r
    seeds = sorted(s for s, a in by.items() if "naive" in a and "trained" in a)
    print(f"{len(seeds)} paired seeds")

    # Manipulation check
    def op(r, k):
        return (r.get("opinions_start") or {}).get(k, 0.0)
    for k in ("people", "exits"):
        dn = st.mean(op(by[s]["trained"], k) - op(by[s]["naive"], k) for s in seeds)
        print(f"precondition: mean trained-naive opinion of {k}: {dn:+.3f}")
    ok = any(abs(st.mean(op(by[s]["trained"], k) - op(by[s]["naive"], k) for s in seeds)) > 0.05
             for k in ("people", "exits"))
    print("PRECONDITION", "MET" if ok else "FAILED -- the snapshot carries no behaviourally relevant training")

    # Primary
    d = [by[s]["trained"]["attraction_turns"] - by[s]["naive"]["attraction_turns"] for s in seeds]
    naive_mean = st.mean(by[s]["naive"]["attraction_turns"] for s in seeds)
    print("\nPRIMARY per seed (naive, trained, d):")
    for s_, dd in zip(seeds, d):
        print(f"  seed {s_}: {by[s_]['naive']['attraction_turns']}, "
              f"{by[s_]['trained']['attraction_turns']}, {dd:+d}")
    print(f"  arm means: naive {naive_mean:.2f}, trained "
          f"{st.mean(by[s_]['trained']['attraction_turns'] for s_ in seeds):.2f}")
    m, se, t, ci = t_test(d)
    print(f"\nPRIMARY attraction reactivity: trained-naive {m:+.2f} (SE {se:.2f}), t={t:+.2f}, "
          f"95% CI [{ci[0]:+.2f}, {ci[1]:+.2f}], naive mean {naive_mean:.1f}")
    if abs(t) > T_CRIT:
        print("  -> SIGNIFICANT: the training changes attraction turns "
              f"({'more' if m > 0 else 'fewer'} with training)")
    elif ci[0] >= -0.2 * naive_mean and ci[1] <= 0.2 * naive_mean:
        print("  -> NULL FINDING: no detectable effect (CI within +/-20% of the naive mean)")
    else:
        print("  -> INCONCLUSIVE (not 'no effect')")

    # Exposure, reported next to the primary
    eo = [by[s_]["trained"]["opportunities"] - by[s_]["naive"]["opportunities"] for s_ in seeds]
    em, ese, et, eci = t_test(eo)
    print(f"EXPOSURE opportunity frames: trained-naive {em:+.1f}, t={et:+.2f}"
          + ("  -> ARMS DIFFER IN EXPOSURE: read the primary with this" if abs(et) > T_CRIT
             else "  -> no detectable exposure difference"))

    # Secondaries, Holm
    def net(r, k):
        a, b = r[f"toward_{k}"], r[f"away_from_{k}"]
        return (a - b) / (a + b) if a + b else None

    def left_share(r):
        n = r["left"] + r["right"]
        return r["left"] / n if n else None
    sec = {"mean_valence": lambda r: r.get("mean_valence"), "moves": lambda r: r["moves"],
           "bumps_per_tile": lambda r: r["bumps_per_tile"], "walking_frac": lambda r: r["walking_frac"],
           "left_share": left_share,
           "attraction_per_opportunity": lambda r: (r["attraction_turns"] / r["opportunities"]
                                                    if r["opportunities"] else None),
           "net_approach_people": lambda r: net(r, "person"),
           "net_approach_exits": lambda r: net(r, "exit")}
    res = []
    for name, get in sec.items():
        dd = [get(by[s]["trained"]) - get(by[s]["naive"]) for s in seeds
              if get(by[s]["trained"]) is not None and get(by[s]["naive"]) is not None]
        if len(dd) < 3:
            res.append((name, None, None, None, None))
            continue
        m, se, t, ci = t_test(dd)
        res.append((name, m, t, ci, p_two_sided(t)))
    print("\nSECONDARY (Holm across eight)")
    order = sorted([r for r in res if r[4] is not None], key=lambda r: r[4])
    k = len(order)
    holm = {}
    stop = False
    for i, r in enumerate(order):
        thr = 0.05 / (k - i)
        if stop or r[4] > thr:
            stop = True
            holm[r[0]] = False
        else:
            holm[r[0]] = True
    for name, m, t, ci, p in res:
        if m is None:
            print(f"  {name}: not enough data")
            continue
        print(f"  {name}: {m:+.4f}, t={t:+.2f}, p={p:.4f}, 95% CI [{ci[0]:+.4f}, {ci[1]:+.4f}]"
              f" -> {'survives Holm' if holm.get(name) else 'descriptive only'}")


if __name__ == "__main__":
    main()
