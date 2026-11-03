# scripts/research/pre_earnings_runup.py
"""
Pre-earnings run-up (earnings announcement premium; Barber et al. 2013,
Frazzini & Lamont 2007). Pre-registered in research/journal.md.

For every earnings 8-K (item 2.02) P, forecast the next-year release as
P + 364 days; f = the forecast reaction session. Long at the open of f-5,
exit at the close of f-1. Variant (not gated): exit at the close of f.
Scored as excess vs the equal-weight pool over the same sessions, 5 bps/side.
Design slice = pool ranks 1-500, held-out = ranks 501-1000.

Usage: python scripts/research/pre_earnings_runup.py   (cached data only)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

from backtest_pead import earnings_filings
from backtest_swing import load_daily, pool_benchmark, pool_symbols, tstat

SLIP = 5 / 1e4
LEAD = 5


def trades(daily: pd.DataFrame, filings: pd.DataFrame) -> pd.DataFrame:
    d = daily.reset_index().drop_duplicates(["timestamp", "symbol"])
    d["day"] = pd.to_datetime(d["timestamp"]).dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    close = d.pivot_table(index="day", columns="symbol", values="close")
    opens = d.pivot_table(index="day", columns="symbol", values="open")
    days = close.index
    rows = []
    for sym, g in filings.groupby("symbol"):
        if sym not in close:
            continue
        c, o = close[sym].to_numpy(), opens[sym].to_numpy()
        seen = set()
        for ts in g["accepted"]:
            after_close = ts.hour * 60 + ts.minute >= 16 * 60
            fc = ts.normalize() + pd.Timedelta(days=364)
            f = days.searchsorted(fc, side="right" if after_close else "left")
            e = f - LEAD
            if e < 1 or f >= len(days) or f in seen:
                continue
            seen.add(f)
            if np.isnan(c[e - 1]) or c[e - 1] < 5 or np.isnan(o[e]):
                continue
            for var, x in (("pre", f - 1), ("thru", f)):
                if not np.isnan(c[x]):
                    rows.append((sym, days[e], var, x - e + 1, c[x] / o[e] - 1))
    return pd.DataFrame(rows, columns=["symbol", "entry", "var", "hold", "ret"])


def report(s: pd.DataFrame, label: str, gate: bool):
    x = s["excess"].to_numpy()
    yrs = s.groupby("year")["excess"].mean() * 1e4
    early, late = s[s["year"] <= 2020]["excess"].mean(), s[s["year"] >= 2021]["excess"].mean()
    t = tstat(x)
    pnl = s.groupby("symbol")["excess"].sum()
    top_sym = pnl.max() / pnl[pnl > 0].sum() if (pnl > 0).any() else np.nan
    k = max(1, len(x) // 100)
    top1 = np.sort(x)[-k:].sum() / x.sum() if x.sum() else np.nan
    yrs_pos = (yrs > 0).mean()
    ok = (len(s) >= 300 and x.mean() >= 0.0015 and t >= 2.5 and yrs_pos >= 0.7
          and np.sign(early) == np.sign(late) == 1 and top_sym <= 0.25)
    print(f"\n{label}: n={len(s):,}  raw net {s['net'].mean() * 1e4:+.1f}  excess {x.mean() * 1e4:+.1f} bps "
          f"(t={t:+.2f})  median {np.median(x) * 1e4:+.1f}  win {(x > 0).mean():.0%}  "
          f"years+ {(yrs > 0).sum()}/{len(yrs)}  16-20 {early * 1e4:+.1f} / 21-26 {late * 1e4:+.1f}  "
          f"top symbol {top_sym:.0%} ({pnl.idxmax()})  top-1% share {top1:.0%}"
          + (f"  -> {'PASS' if ok else 'FAIL'}" if gate else ""))
    print("   excess by year: " + "  ".join(f"{y}:{v:+.0f}" for y, v in yrs.items()))


def main():
    syms = pool_symbols()
    daily = load_daily(syms)
    filings = earnings_filings(syms)
    print(f"{len(filings):,} earnings 8-Ks for {filings['symbol'].nunique()} names")
    tr = trades(daily, filings)
    bench = pool_benchmark(daily, sorted(tr["hold"].unique()))
    for h in bench:
        bench[h].index = bench[h].index.tz_convert("America/New_York").tz_localize(None).normalize()
    b = np.array([bench[h].get(d, np.nan) for h, d in zip(tr["hold"], tr["entry"])])
    tr["excess"] = tr["ret"] - b - 2 * SLIP
    tr["net"] = tr["ret"] - 2 * SLIP
    tr = tr.dropna(subset=["excess"])
    tr["year"] = tr["entry"].dt.year
    rank = {s: i for i, s in enumerate(syms)}
    tr["design"] = tr["symbol"].map(rank) < 500
    for var, name in (("pre", "f-5 open -> f-1 close"), ("thru", "f-5 open -> f close (variant)")):
        for design, slice_ in ((True, "design ranks 1-500"), (False, "held-out ranks 501-1000")):
            s = tr[(tr["var"] == var) & (tr["design"] == design)]
            report(s, f"{name}, {slice_}", gate=(var == "pre"))


if __name__ == "__main__":
    main()
