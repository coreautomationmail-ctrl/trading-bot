"""
Does skipping down spikes with no headline help on other names? (research/journal.md, 2026-11-06 spike-down-no-news)

Trades and news tags come unchanged from .cache/spike_news_scored.csv, written by
spike_news.py. Test group: rank 26-100. Rank 101+ is scored only if the test passes.

Usage: python scripts/research/spike_down_no_news.py
"""
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

import numpy as np
import pandas as pd

CACHE = os.environ.get("STRATTON_CACHE", os.path.join(HERE, "..", ".cache"))
COST_BPS = 2.0     # per side
MAX_OPEN = 4
PERIODS = (("2017-21", range(2017, 2022)), ("2022-26", range(2022, 2027)),
           ("2017-20", range(2017, 2021)), ("2021-26", range(2021, 2027)))


def tstat(x) -> float:
    x = np.asarray(x, dtype=float)
    return x.mean() / x.std(ddof=1) * np.sqrt(len(x)) if len(x) > 1 and x.std(ddof=1) > 0 else float("nan")


def net(g: pd.DataFrame, cost=COST_BPS) -> pd.Series:
    return g["bps"] - 2 * cost


def line(label, g):
    if g.empty:
        print(f"  {label:32s} n=    0")
        return
    x = net(g)
    by = x.groupby(g["year"]).mean()
    day = x.groupby(g["day"]).sum()
    pos = x.sum()
    top = np.sort(x.to_numpy())[::-1][:max(1, len(x) // 100)].sum() / pos if pos else float("nan")
    print(f"  {label:32s} n={len(g):5d} net {x.mean():+6.1f} (t {tstat(x):+.2f}, by day {tstat(day):+.2f}) "
          f"years+ {(by > 0).sum()}/{len(by)} win {(x > 0).mean():.0%} med {x.median():+.1f} top1% {top:+.0%}")


def welch(a: pd.Series, b: pd.Series):
    d = a.mean() - b.mean()
    return d, d / np.sqrt(a.var() / len(a) + b.var() / len(b))


def clustered_t(g: pd.DataFrame, in_a: pd.Series) -> float:
    """t of mean(a) - mean(b) with same-day trades clustered (OLS on a dummy, CR0 errors)."""
    x, a = net(g).to_numpy(), in_a.to_numpy()
    ma, mb = x[a].mean(), x[~a].mean()
    e = x - np.where(a, ma, mb)
    # influence of each day on each mean
    df = pd.DataFrame({"day": g["day"].to_numpy(), "ea": np.where(a, e, 0.0), "eb": np.where(a, 0.0, e)})
    by = df.groupby("day")[["ea", "eb"]].sum()
    var = (((by["ea"] / a.sum()) - (by["eb"] / (~a).sum())) ** 2).sum()
    return (ma - mb) / np.sqrt(var)


def capped(t: pd.DataFrame) -> pd.DataFrame:
    """Trades the pod could take with MAX_OPEN positions, earliest entry first (spike_wide.capped)."""
    keep, open_ = [], []
    for row in t.sort_values("entry").itertuples():
        open_ = [x for x in open_ if x > row.entry]
        if len(open_) < MAX_OPEN:
            keep.append(row.Index)
            open_.append(row.exit)
    return t.loc[keep]


def evaluate(label, g) -> bool:
    skip = (g["side"] == -1) & (g["n60"] == 0)
    kept, skipped = g[~skip], g[skip]
    print(f"\n== {label}")
    line("all", g)
    line("kept (not down + no news)", kept)
    line("skipped (down + no news)", skipped)
    for side, name in ((1, "up"), (-1, "down")):
        for has, tag in ((True, "news"), (False, "no news")):
            line(f"{name} + {tag}", g[(g["side"] == side) & ((g["n60"] > 0) == has)])
    for name, yrs in PERIODS[:2]:
        line(f"kept, {name}", kept[kept["year"].isin(yrs)])
        line(f"skipped, {name}", skipped[skipped["year"].isin(yrs)])

    x, k, s = net(g), net(kept), net(skipped)
    improve = k.mean() - x.mean()
    gap, gap_t = welch(k, s)
    print(f"\n  improvement {improve:+.2f} bps; kept minus skipped {gap:+.2f} (Welch t {gap_t:+.2f}, "
          f"day-clustered t {clustered_t(g, ~skip):+.2f})")
    d = g[g["side"] == -1]
    dn = d["n60"] > 0
    dg, dt = welch(net(d[dn]), net(d[~dn]))
    print(f"  down + news minus down + no news {dg:+.1f} (t {dt:+.2f}, day-clustered {clustered_t(d, dn):+.2f}), "
          f"n {dn.sum()} vs {(~dn).sum()}")
    up = g["side"] == 1
    print(f"  skip ALL down: improvement {net(g[up]).mean() - x.mean():+.2f}; up minus down "
          f"{welch(net(g[up]), net(g[~up]))[0]:+.2f} (t {welch(net(g[up]), net(g[~up]))[1]:+.2f}, "
          f"day-clustered {clustered_t(g, up):+.2f})")
    print(f"  at 4 bps/side: all {net(g, 4).mean():+.1f}, kept {net(kept, 4).mean():+.1f}")
    ca, ck = capped(g), capped(kept)
    print(f"  4-position cap: all n={len(ca)} {net(ca).mean():+.1f}, sum {net(ca).sum():+.0f}; "
          f"kept n={len(ck)} {net(ck).mean():+.1f}, sum {net(ck).sum():+.0f}")

    imp = {}
    for name, yrs in PERIODS:
        sel = g["year"].isin(yrs)
        imp[name] = net(g[sel & ~skip]).mean() - net(g[sel]).mean()
    print("  improvement by period: " + "  ".join(f"{n} {v:+.2f}" for n, v in imp.items()))
    print("  kept by year: " + "  ".join(f"{y}:{v:+.1f}" for y, v in k.groupby(kept["year"]).mean().items()))
    print("  gap by year: " + "  ".join(
        f"{y}:{net(kept[kept['year'] == y]).mean() - net(skipped[skipped['year'] == y]).mean():+.1f}"
        for y in sorted(g["year"].unique())))

    by = k.groupby(kept["year"]).mean()
    syms = k.groupby(kept["sym"]).sum()
    share = syms.max() / syms[syms > 0].sum()
    k1 = k[kept["year"].isin(PERIODS[2][1])].mean()
    k2 = k[kept["year"].isin(PERIODS[3][1])].mean()
    print(f"  top symbol {syms.idxmax()} {share:.0%}; kept 2017-20 {k1:+.1f}, 2021-26 {k2:+.1f}")
    checks = {
        "improvement >= +3": improve >= 3,
        "gap Welch t >= 2": gap_t >= 2,
        "improvement > 0 in 2017-21 and 2022-26": imp["2017-21"] > 0 and imp["2022-26"] > 0,
        "improvement > 0 in 2017-20 and 2021-26": imp["2017-20"] > 0 and imp["2021-26"] > 0,
        "kept n >= 300": len(k) >= 300,
        "kept mean net >= 3": k.mean() >= 3,
        "kept t >= 2.5": tstat(k) >= 2.5,
        "kept years+ >= 70%": (by > 0).mean() >= 0.7,
        "kept same sign 2017-20 and 2021-26": k1 * k2 > 0,
        "kept top symbol <= 25%": share <= 0.25,
    }
    for name, ok in checks.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    verdict = all(checks.values())
    print(f"{label} verdict: {'PASS' if verdict else 'FAIL'}")
    return verdict


def main():
    t = pd.read_csv(os.path.join(CACHE, "spike_news_scored.csv"))
    t["entry"] = pd.to_datetime(t["entry"], utc=True)
    t["exit"] = pd.to_datetime(t["exit"], utc=True)
    t["day"] = t["entry"].dt.tz_convert("America/New_York").dt.date
    test = t[t["rank"].between(26, 100)].reset_index(drop=True)
    print(f"rank 26-100 trades {len(test)}, names {test['sym'].nunique()}, "
          f"bars left out of range: {(~test['year'].between(2017, 2026)).sum()}")
    print("share with news by year: " + "  ".join(
        f"{y}:{v:.0%}" for y, v in (test["n60"] > 0).groupby(test["year"]).mean().items()))

    passed = evaluate("PRIMARY rank 26-100", test)

    ref = t[t["rank"] <= 25]
    a = net(test).groupby(test["day"]).sum()
    skip = (test["side"] == -1) & (test["n60"] == 0)
    b = net(ref).groupby(ref["day"]).sum()
    c = net(test[~skip]).groupby(test.loc[~skip, "day"]).sum()
    both = pd.concat({"a": a, "b": b, "c": c}, axis=1).fillna(0.0)
    print(f"\ncorrelation of daily P&L with rank 1-25: all {both['a'].corr(both['b']):+.2f}, kept {both['c'].corr(both['b']):+.2f}")

    if passed:
        held = t[t["rank"].between(101, 9998)].reset_index(drop=True)
        evaluate("HELD OUT rank 101+", held)
    else:
        print("rank 101+ not scored: the primary test did not pass.")


if __name__ == "__main__":
    main()
