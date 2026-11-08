"""
Is the spike edge concentrated on FOMC decision days? (research/journal.md, 2026-11-07 spike-events)

Trades come unchanged from .cache/spike_news_scored.csv. Event days are a fixed
list of scheduled decision days; emergency moves are not on it.

Usage: python scripts/research/spike_events.py
"""
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd

from spike_down_no_news import CACHE, capped, clustered_t, line, net, tstat

FOMC = set("""
2017-02-01 2017-03-15 2017-05-03 2017-06-14 2017-07-26 2017-09-20 2017-11-01 2017-12-13
2018-01-31 2018-03-21 2018-05-02 2018-06-13 2018-08-01 2018-09-26 2018-11-08 2018-12-19
2019-01-30 2019-03-20 2019-05-01 2019-06-19 2019-07-31 2019-09-18 2019-10-30 2019-12-11
2020-01-29 2020-04-29 2020-06-10 2020-07-29 2020-09-16 2020-11-05 2020-12-16
2021-01-27 2021-03-17 2021-04-28 2021-06-16 2021-07-28 2021-09-22 2021-11-03 2021-12-15
2022-01-26 2022-03-16 2022-05-04 2022-06-15 2022-07-27 2022-09-21 2022-11-02 2022-12-14
2023-02-01 2023-03-22 2023-05-03 2023-06-14 2023-07-26 2023-09-20 2023-11-01 2023-12-13
2024-01-31 2024-03-20 2024-05-01 2024-06-12 2024-07-31 2024-09-18 2024-11-07 2024-12-18
2025-01-29 2025-03-19 2025-05-07 2025-06-18 2025-07-30 2025-09-17 2025-10-29 2025-12-10
2026-01-28 2026-03-18 2026-04-29 2026-06-17 2026-07-29 2026-09-16
""".split())
_dates = pd.to_datetime(sorted(FOMC))
MINUTES = {d.strftime("%Y-%m-%d") for d in _dates + pd.Timedelta(days=21)}
DAY_AFTER = {d.strftime("%Y-%m-%d") for d in _dates + pd.offsets.BDay(1)}
ETF = {"SPY", "QQQ", "IWM", "DIA"}


def no_top(g: pd.DataFrame) -> pd.DataFrame:
    """The group without its best 1% of trades. Not tradable; shows how much the tail carries."""
    keep = net(g).sort_values(ascending=False).index[max(1, len(g) // 100):]
    return g.loc[keep]


def best_days(g: pd.DataFrame, k=5) -> str:
    day = net(g).groupby(g["day"]).sum()
    top = day.sort_values(ascending=False)
    return (f"sum {day.sum():+.0f}, 5 best days {top.head(k).sum():+.0f}; best "
            + " ".join(f"{d}:{v:+.0f}" for d, v in top.head(k).items())
            + "; worst " + " ".join(f"{d}:{v:+.0f}" for d, v in top.tail(3).items()))


def split(label, g):
    ev = g["day"].isin(FOMC)
    e, o = g[ev], g[~ev]
    print(f"\n== {label}")
    line("all", g)
    line("event", e)
    line("ordinary", o)
    line("event, top 1% removed", no_top(e))
    line("ordinary, top 1% removed", no_top(o))
    for name, yrs in (("2017-21", range(2017, 2022)), ("2022-26", range(2022, 2027))):
        line(f"event, {name}", e[e["year"].isin(yrs)])
        line(f"ordinary, {name}", o[o["year"].isin(yrs)])
    et = e["entry"].dt.tz_convert("America/New_York")
    sig = et - pd.Timedelta(minutes=2)
    late = sig.dt.hour >= 14
    line("event, signal before 14:00", e[~late])
    line("event, signal from 14:00", e[late])
    for side, name in ((1, "up"), (-1, "down")):
        line(f"event, {name}", e[e["side"] == side])
        line(f"ordinary, {name}", o[o["side"] == side])
    line("event, ETFs", e[e["sym"].isin(ETF)])
    line("event, stocks", e[~e["sym"].isin(ETF)])
    line("ordinary, ETFs", o[o["sym"].isin(ETF)])
    line("ordinary, stocks", o[~o["sym"].isin(ETF)])
    line("minutes days", g[g["day"].isin(MINUTES)])
    line("day after a decision", g[g["day"].isin(DAY_AFTER)])
    line("rest, no event/minutes/after", g[~g["day"].isin(FOMC | MINUTES | DAY_AFTER)])
    print(f"  event by year: " + "  ".join(f"{y}:{v:+.1f}" for y, v in net(e).groupby(e["year"]).mean().items()))
    print(f"  ordinary by year: " + "  ".join(f"{y}:{v:+.1f}" for y, v in net(o).groupby(o["year"]).mean().items()))
    print(f"  event days: {best_days(e)}")
    print(f"  ordinary days: {best_days(o)}")
    print(f"  at 4 bps/side: event {net(e, 4).mean():+.1f}, ordinary {net(o, 4).mean():+.1f}")
    ca = capped(g)
    ce, co = ca[ca["day"].isin(FOMC)], ca[~ca["day"].isin(FOMC)]
    print(f"  4-position cap: event n={len(ce)} {net(ce).mean():+.1f} sum {net(ce).sum():+.0f} "
          f"(t {tstat(net(ce)):+.2f}, by day {tstat(net(ce).groupby(ce['day']).sum()):+.2f}); "
          f"ordinary n={len(co)} {net(co).mean():+.1f} sum {net(co).sum():+.0f} "
          f"(t {tstat(net(co)):+.2f}, by day {tstat(net(co).groupby(co['day']).sum()):+.2f})")
    return e, o, ev


def after_the_fact(pod: pd.DataFrame, wide: pd.DataFrame):
    """Cuts chosen after reading the first run's output, so leads and checks, not evidence."""
    print("\n== after the fact: not pre-registered")
    three = {"2025-04-09", "2022-11-30", "2019-08-01"}
    worst = {"2022-11-02", "2024-09-18", "2022-09-21"}
    for name, g in (("rank 1-25", pod), ("rank 26-100", wide)):
        print(f" {name}")
        for d in sorted(three):
            x = g[g["day"] == d]
            et = x["entry"].dt.tz_convert("America/New_York")
            print(f"  {d}: n={len(x)} up {(x['side'] == 1).sum()} sum {net(x).sum():+.0f} mean {net(x).mean():+.1f} "
                  f"max {net(x).max():+.0f} entries {et.min():%H:%M}-{et.max():%H:%M}")
        rest = g[~g["day"].isin(three)]
        line("without the 3 days", rest)
        line("ordinary without the 3 days", rest[~rest["day"].isin(FOMC)])
        line("event without its 3 worst days", g[g["day"].isin(FOMC - worst)])
        line("without the 3 best and 3 worst", g[~g["day"].isin(three | worst)])
        line("minutes without 2025-04-09", g[g["day"].isin(MINUTES - three)])
        line("day after without 2019-08-01", g[g["day"].isin(DAY_AFTER - three)])
        print("  without the 3 days by year: " + "  ".join(
            f"{y}:{v:+.1f}" for y, v in net(rest).groupby(rest["year"]).mean().items()))
        c = capped(g)
        c3 = c[c["day"].isin(three)]
        cr = c[~c["day"].isin(three)]
        print(f"  4-position cap: all n={len(c)} sum {net(c).sum():+.0f}; the 3 days n={len(c3)} sum {net(c3).sum():+.0f}; "
              f"without them {net(cr).mean():+.1f} (t {tstat(net(cr)):+.2f}, by day {tstat(net(cr).groupby(cr['day']).sum()):+.2f})")


def main():
    t = pd.read_csv(os.path.join(CACHE, "spike_news_scored.csv"))
    t["entry"] = pd.to_datetime(t["entry"], utc=True)
    t["exit"] = pd.to_datetime(t["exit"], utc=True)
    t["day"] = t["entry"].dt.tz_convert("America/New_York").dt.strftime("%Y-%m-%d")
    print(f"trades {len(t)}; last day {t['day'].max()}; calendar days {len(FOMC)}; "
          f"overlap minutes/decision {len(MINUTES & FOMC)}, after/decision {len(DAY_AFTER & FOMC)}")

    pod = t[t["rank"] <= 25].reset_index(drop=True)
    e, o, ev = split("PRIMARY rank 1-25", pod)

    x, oo = net(e), net(o)
    by = x.groupby(e["year"]).mean()
    syms = net(e).groupby(e["sym"]).sum()
    share = syms.max() / syms[syms > 0].sum() if (syms > 0).any() else float("nan")
    day_t = tstat(x.groupby(e["day"]).sum())
    gap = x.mean() - oo.mean()
    gap_t = clustered_t(pod, ev)
    p = pod["year"] <= 2021
    g1 = net(pod[ev & p]).mean() - net(pod[~ev & p]).mean()
    g2 = net(pod[ev & ~p]).mean() - net(pod[~ev & ~p]).mean()
    a, b = x[e["year"] <= 2020], x[e["year"] > 2020]
    print(f"\n  top symbol {syms.idxmax()} {share:.0%}; gap {gap:+.1f}, clustered t {gap_t:+.2f}; "
          f"2017-21 {g1:+.1f}, 2022-26 {g2:+.1f}; event 2017-20 {a.mean():+.1f}, 2021-26 {b.mean():+.1f}; "
          f"event by-day t {day_t:+.2f}")
    checks = {
        "n >= 300": len(e) >= 300,
        "mean net >= 3": x.mean() >= 3,
        "t >= 2.5": tstat(x) >= 2.5,
        "years+ >= 70%": (by > 0).mean() >= 0.7,
        "same sign 2017-20 and 2021-26": a.mean() * b.mean() > 0,
        "top symbol <= 25%": share <= 0.25,
        "by-day t >= 2": day_t >= 2,
        "gap >= +3": gap >= 3,
        "gap clustered t >= 2": gap_t >= 2,
        "gap same sign both periods": g1 * g2 > 0,
    }
    for k, v in checks.items():
        print(f"  {'ok  ' if v else 'FAIL'} {k}")
    print(f"PRIMARY verdict: {'PASS' if all(checks.values()) else 'FAIL'}")

    wide = t[t["rank"].between(26, 100)].reset_index(drop=True)
    _, _, wev = split("REPLICATION rank 26-100, reported whatever the verdict", wide)
    w1, w0 = net(wide[wev]), net(wide[~wev])
    print(f"\n  rank 26-100 gap {w1.mean() - w0.mean():+.1f}, clustered t {clustered_t(wide, wev):+.2f}")
    d1 = net(pod).groupby(pod["day"]).sum()
    d2 = net(wide).groupby(wide["day"]).sum()
    both = pd.concat([d1, d2], axis=1, keys=["a", "b"]).fillna(0)
    print(f"  daily P&L correlation rank 26-100 vs rank 1-25: {both['a'].corr(both['b']):+.2f}")
    dd = net(pod).groupby(pod["day"]).sum()
    dd_o = dd[~dd.index.isin(FOMC)]
    print(f"  rank 1-25 daily sum: all {dd.sum():+.0f} over {len(dd)} days; without FOMC {dd_o.sum():+.0f} over {len(dd_o)}")
    after_the_fact(pod, wide)


if __name__ == "__main__":
    main()
