# scripts/research/short_term_reversal.py
"""
Short-term (1-week) reversal (Lehmann 1990; Jegadeesh 1990). Pre-registered
in research/journal.md.

Every 5th session t: rank the slice by r5 = close[t]/close[t-5]-1 (close >= $5),
long the bottom 5%, short the top 5%, enter the open of t+1, exit the close of
t+5. Scored as excess vs the equal-weight pool over the same sessions, 5 bps/side.
Design slice = pool ranks 1-500, held-out = ranks 501-1000 (ranked within slice).

Usage: python scripts/research/short_term_reversal.py   (cached data only)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

from backtest_swing import load_daily, pool_benchmark, pool_symbols, tstat

SLIP = 5 / 1e4
H = 5
TAIL = 0.05


def trades(close: pd.DataFrame, opens: pd.DataFrame, vol60: pd.DataFrame, cols: list) -> pd.DataFrame:
    c, o, v = close[cols].to_numpy(), opens[cols].to_numpy(), vol60[cols].to_numpy()
    days = close.index
    rows = []
    for t in range(60, len(days) - H, H):
        r5 = c[t] / c[t - 5] - 1
        ret = c[t + H] / o[t + 1] - 1
        ok = ~np.isnan(r5) & ~np.isnan(ret) & (c[t] >= 5)
        idx = np.flatnonzero(ok)
        k = int(len(idx) * TAIL)
        if k < 1:
            continue
        order = idx[np.argsort(r5[idx])]
        vmed = np.nanmedian(v[t][idx])
        for side, pick in ((1, order[:k]), (-1, order[-k:])):
            for j in pick:
                rows.append((cols[j], days[t + 1], side, r5[j], ret[j], v[t][j] > vmed))
    return pd.DataFrame(rows, columns=["symbol", "entry", "side", "r5", "ret", "hivol"])


def report(s: pd.DataFrame, label: str, gate: bool = False):
    x = s["excess"].to_numpy()
    yrs = s.groupby("year")["excess"].mean() * 1e4
    early, late = s[s["year"] <= 2020]["excess"].mean(), s[s["year"] >= 2021]["excess"].mean()
    t = tstat(x)
    tw = tstat(s.groupby("entry")["excess"].mean().to_numpy())
    pnl = s.groupby("symbol")["excess"].sum()
    top_sym = pnl.max() / pnl[pnl > 0].sum() if (pnl > 0).any() else np.nan
    k = max(1, len(x) // 100)
    top1 = np.sort(x)[-k:].sum() / x.sum() if x.sum() else np.nan
    yrs_pos = (yrs > 0).mean()
    ok = (len(s) >= 300 and x.mean() >= 0.0015 and t >= 2.5 and yrs_pos >= 0.7
          and np.sign(early) == np.sign(late) == 1 and top_sym <= 0.25)
    print(f"\n{label}: n={len(s):,}  excess {x.mean() * 1e4:+.1f} bps (t={t:+.2f}, weekly t={tw:+.2f})  "
          f"median {np.median(x) * 1e4:+.1f}  win {(x > 0).mean():.0%}  years+ {(yrs > 0).sum()}/{len(yrs)}  "
          f"16-20 {early * 1e4:+.1f} / 21-26 {late * 1e4:+.1f}  top symbol {top_sym:.0%} ({pnl.idxmax()})  "
          f"top-1% share {top1:.0%}" + (f"  -> {'PASS' if ok else 'FAIL'}" if gate else ""), flush=True)
    print("   excess by year: " + "  ".join(f"{y}:{v:+.0f}" for y, v in yrs.items()), flush=True)


def main():
    syms = pool_symbols()
    daily = load_daily(syms)
    d = daily.reset_index().drop_duplicates(["timestamp", "symbol"])
    d["day"] = pd.to_datetime(d["timestamp"]).dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    close = d.pivot_table(index="day", columns="symbol", values="close").sort_index()
    opens = d.pivot_table(index="day", columns="symbol", values="open").reindex(close.index)
    vol60 = close.pct_change(fill_method=None).rolling(60, min_periods=40).std()
    bench = pool_benchmark(daily, [H])[H]
    bench.index = bench.index.tz_convert("America/New_York").tz_localize(None).normalize()
    bench = bench.groupby(level=0).last()
    for design, ranks, name in ((True, syms[:500], "design ranks 1-500"),
                                (False, syms[500:], "held-out ranks 501-1000")):
        cols = [s for s in ranks if s in close.columns]
        tr = trades(close, opens, vol60, cols)
        b = bench.reindex(tr["entry"]).to_numpy()
        tr["excess"] = tr["side"] * (tr["ret"] - b) - 2 * SLIP
        tr = tr.dropna(subset=["excess"])
        tr["year"] = tr["entry"].dt.year
        print(f"\n=== {name}: {len(cols)} names, {tr['entry'].nunique()} weeks ===", flush=True)
        report(tr, f"long+short, {name}", gate=design)
        report(tr[tr["side"] == 1], "  long losers")
        report(tr[tr["side"] == -1], "  short winners")
        report(tr[tr["hivol"]], "  high 60d vol half", gate=False)
        report(tr[~tr["hivol"]], "  low 60d vol half", gate=False)


if __name__ == "__main__":
    main()
