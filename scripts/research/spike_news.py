"""
Do spikes with a headline run further than spikes without? (research/journal.md, 2026-11-06 spike-news)

spike_mom exactly as live, from spike_wide.collect(). A signal has news if a
single-name headline was created in the 60 minutes before the signal bar opened.
A headline carried by 5+ of the 277 names counts as a market recap, not news.
Entry is two bars after the signal bar, so the signal bar opens at entry - 2 min.

Usage: python scripts/research/spike_news.py
"""
import collections
import glob
import gzip
import csv
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

import numpy as np
import pandas as pd

from spike_size import DESIGN, COST_BPS, tstat
from spike_wide import collect

CACHE = os.environ.get("STRATTON_CACHE", os.path.join(HERE, "..", ".cache"))
TRADES = os.path.join(CACHE, "spike_news_trades.csv")
RECAP_NAMES = 5
WINDOW_MIN = 60


def load_news() -> dict:
    """{sym: sorted UTC ns of single-name headlines}, plus {sym: same for all headlines}."""
    rows, tags = {}, collections.Counter()
    for path in glob.glob(os.path.join(CACHE, "news", "*.csv.gz")):
        sym = os.path.basename(path)[:-len(".csv.gz")]
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            rows[sym] = [(r["created_at"], r["headline"]) for r in csv.DictReader(f)]
        tags.update(rows[sym])
    single, every = {}, {}
    for sym, items in rows.items():
        every[sym] = np.sort(pd.to_datetime([c for c, _ in items], utc=True).asi8)
        single[sym] = np.sort(pd.to_datetime([c for c, h in items if tags[(c, h)] < RECAP_NAMES], utc=True).asi8)
    return single, every


def counts(t: pd.DataFrame, news: dict, starts: np.ndarray, ends: np.ndarray) -> np.ndarray:
    """Headlines per trade with created_at in [start, end)."""
    out = np.zeros(len(t), dtype=int)
    for sym, idx in t.groupby("sym").indices.items():
        stamps = news.get(sym)
        if stamps is not None:
            out[idx] = np.searchsorted(stamps, ends[idx]) - np.searchsorted(stamps, starts[idx])
    return out


def net(g: pd.DataFrame, cost=COST_BPS) -> pd.Series:
    return g["bps"] - 2 * cost


def line(label, g):
    if g.empty:
        print(f"  {label:34s} n=    0")
        return
    x = net(g)
    by = x.groupby(g["year"]).mean()
    day = x.groupby(g["bar"].dt.date).sum()
    day_t = day.mean() / day.std() * np.sqrt(len(day)) if len(day) > 1 else float("nan")
    top = np.sort(x.to_numpy())[::-1][:max(1, len(x) // 100)].sum() / x.sum() if x.sum() else float("nan")
    print(f"  {label:34s} n={len(g):5d} net {x.mean():+6.1f} (t {tstat(x):+.2f}, by day {day_t:+.2f}) "
          f"years+ {(by > 0).sum()}/{len(by)} win {(x > 0).mean():.0%} med {x.median():+.1f} top1% {top:+.0%}")


def diff(a: pd.DataFrame, b: pd.DataFrame):
    x, y = net(a), net(b)
    d = x.mean() - y.mean()
    return d, d / np.sqrt(x.var() / len(x) + y.var() / len(y))


def compare(label, t, has):
    print(f"\n== {label}")
    held = ~t["year"].isin(DESIGN)
    line("news, all years", t[has])
    line("no news, all years", t[~has])
    line("news, 2017-21", t[has & ~held])
    line("no news, 2017-21", t[~has & ~held])
    line("news, 2022-26", t[has & held])
    line("no news, 2022-26", t[~has & held])
    d, dt = diff(t[has], t[~has])
    print(f"  news minus no news {d:+.1f} bps (t {dt:+.2f})")


def main():
    if os.path.exists(TRADES):
        t = pd.read_csv(TRADES)
    else:
        t = collect()
        t.to_csv(TRADES, index=False)
    t["entry"] = pd.to_datetime(t["entry"], utc=True)
    t["bar"] = t["entry"] - pd.Timedelta(minutes=2)
    single, every = load_news()
    missing = sorted(set(t["sym"]) - set(single))
    print(f"trades {len(t)}; names without a news file, left out: {missing}")
    t = t[t["sym"].isin(single)].reset_index(drop=True)
    t["bar"] = t["entry"] - pd.Timedelta(minutes=2)

    bar = t["bar"].to_numpy("datetime64[ns]").astype("int64")
    t["n60"] = counts(t, single, bar - WINDOW_MIN * 60_000_000_000, bar)
    t["n15"] = counts(t, single, bar - 15 * 60_000_000_000, bar)
    t["n60all"] = counts(t, every, bar - WINDOW_MIN * 60_000_000_000, bar)
    t["nbar"] = counts(t, single, bar, bar + 60_000_000_000)   # known only as the bar closes
    et = t["bar"].dt.tz_convert("America/New_York")
    close = (et.dt.tz_localize(None).dt.normalize() - pd.offsets.BDay(1) + pd.Timedelta(hours=16)).dt.tz_localize(
        "America/New_York", ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")
    t["nsession"] = counts(t, single, close.to_numpy("datetime64[ns]").astype("int64"), bar)
    t.drop(columns=["bar"]).to_csv(os.path.join(CACHE, "spike_news_scored.csv"), index=False)

    pool, wider = t[t["rank"] <= 25], t[t["rank"].between(26, 100)]
    print("\nshare of rank 1-25 signals with news, by year: "
          + "  ".join(f"{y}:{v:.0%}" for y, v in (pool["n60"] > 0).groupby(pool["year"]).mean().items()))
    bysym = (pool["n60"] > 0).groupby(pool["sym"]).agg(["mean", "size"]).sort_values("size", ascending=False)
    print("by name: " + "  ".join(f"{s}:{r['mean']:.0%}/{int(r['size'])}" for s, r in bysym.head(25).iterrows()))

    has = pool["n60"] > 0
    compare("PRIMARY rank 1-25, single-name headline in the prior 60 min", pool, has)

    print("\n== same-name, same-year check (each trade minus its symbol-year mean)")
    x = net(pool)
    within = x - x.groupby([pool["sym"], pool["year"]]).transform("mean")
    print(f"  news {within[has].mean():+.1f}, no news {within[~has].mean():+.1f}, "
          f"gap {within[has].mean() - within[~has].mean():+.1f} bps")

    for label, col in (("prior 15 min", "n15"), ("since the prior close", "nsession"), ("60 min, recaps included", "n60all")):
        compare(f"reported only: rank 1-25, {label}", pool, pool[col] > 0)
    compare("reported only: rank 1-25, 60 min or during the signal bar", pool, (pool["n60"] + pool["nbar"]) > 0)
    for side, name in ((1, "up spikes"), (-1, "down spikes")):
        g = pool[pool["side"] == side]
        compare(f"reported only: rank 1-25, {name}", g, g["n60"] > 0)
    compare("reported only: rank 26-100", wider, wider["n60"] > 0)

    g = pool[has]
    y = net(g)
    by = y.groupby(g["year"]).mean()
    held = ~g["year"].isin(DESIGN)
    syms = g.groupby("sym").apply(lambda s: net(s).sum(), include_groups=False)
    share = syms.max() / syms[syms > 0].sum()
    d, dt = diff(g, pool[~has])
    pre = pool["year"].isin(DESIGN)
    d1, _ = diff(pool[has & pre], pool[~has & pre])
    d2, _ = diff(pool[has & ~pre], pool[~has & ~pre])
    checks = {
        "n >= 300": len(g) >= 300,
        "mean net >= 3": y.mean() >= 3,
        "t >= 2.5": tstat(y) >= 2.5,
        "years+ >= 70%": (by > 0).mean() >= 0.7,
        "same sign 2017-21 and 2022-26": y[~held].mean() * y[held].mean() > 0,
        "top symbol <= 25%": share <= 0.25,
        "gap >= +3": d >= 3,
        "gap t >= 2": dt >= 2,
        "gap same sign both periods": d1 * d2 > 0,
    }
    print(f"\ntop symbol {syms.idxmax()} {share:.0%}; gap 2017-21 {d1:+.1f}, 2022-26 {d2:+.1f}")
    for k, v in checks.items():
        print(f"  {'ok  ' if v else 'FAIL'} {k}")
    print(f"PRIMARY verdict: {'PASS' if all(checks.values()) else 'FAIL'}")


if __name__ == "__main__":
    main()
