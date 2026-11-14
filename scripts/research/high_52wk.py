# scripts/research/high_52wk.py
"""
52-week-high momentum (George & Hwang 2004). Pre-registered in research/journal.md.

Every 20th session t (once 252 prior sessions exist): ratio = close[t] / max(close[t-251..t]),
close >= $5. Long every name with ratio >= 0.98, enter the open of t+1, exit the close of t+20.
Excess vs the equal-weight pool (hold 20), 5 bps/side. Trades whose window holds a split-like
overnight gap are dropped (the cache is unadjusted). Design = pool ranks 1-500, held out = 501-1000.
Not gated: the George-Hwang top/bottom-decile long-short on the same dates.

Usage: python scripts/research/high_52wk.py   (cached data only)
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

from backtest_swing import load_daily, pool_benchmark, pool_symbols, tstat

SLIP = 5 / 1e4
H = 20
LB = 252
NEAR = 0.98
SPLITS = np.array([1 / 20, 1 / 10, 1 / 5, 1 / 4, 1 / 3, 1 / 2, 2 / 3, 3 / 2, 2, 3, 4, 5, 8, 10, 20])


def split_like(gap: np.ndarray) -> np.ndarray:
    g = gap[..., None]
    return (np.abs(g / SPLITS - 1) <= 0.02).any(axis=-1)


def trades(close: pd.DataFrame, opens: pd.DataFrame, cols: list):
    c, o = close[cols].to_numpy(), opens[cols].to_numpy()
    gap = np.full_like(o, np.nan)
    gap[1:] = o[1:] / c[:-1]
    bad = split_like(np.nan_to_num(gap, nan=1.0))
    crash = np.nan_to_num(gap, nan=1.0) <= 0.75
    days = close.index
    near, gh, stats = [], [], {"dropped": 0, "crash_kept": 0}
    for t in range(LB - 1, len(days) - H, H):
        win = c[t - LB + 1:t + 1]
        hi = np.nanmax(win, axis=0)
        cnt = np.isfinite(win).sum(axis=0)
        ratio = c[t] / hi
        ret = c[t + H] / o[t + 1] - 1
        ok = np.isfinite(ratio) & np.isfinite(ret) & (c[t] >= 5) & (cnt >= 240)
        wbad = bad[t - LB + 2:t + H + 1].any(axis=0)
        stats["dropped"] += int((ok & wbad).sum())
        ok &= ~wbad
        stats["crash_kept"] += int((ok & crash[t - LB + 2:t + H + 1].any(axis=0)).sum())
        idx = np.flatnonzero(ok)
        for j in idx[ratio[idx] >= NEAR]:
            near.append((cols[j], days[t + 1], 1, ratio[j], ret[j]))
        k = len(idx) // 10
        if k >= 1:
            order = idx[np.argsort(ratio[idx])]
            for side, pick in ((1, order[-k:]), (-1, order[:k])):
                for j in pick:
                    gh.append((cols[j], days[t + 1], side, ratio[j], ret[j]))
    names = ["symbol", "entry", "side", "ratio", "ret"]
    return pd.DataFrame(near, columns=names), pd.DataFrame(gh, columns=names), stats


def report(s: pd.DataFrame, label: str, gate: bool = False):
    x = s["excess"].to_numpy()
    yrs = s.groupby("year")["excess"].mean() * 1e4
    early, late = s[s["year"] <= 2020]["excess"].mean(), s[s["year"] >= 2021]["excess"].mean()
    t = tstat(x)
    tf = tstat(s.groupby("entry")["excess"].mean().to_numpy())
    pnl = s.groupby("symbol")["excess"].sum()
    top_sym = pnl.max() / pnl[pnl > 0].sum() if (pnl > 0).any() else np.nan
    k = max(1, len(x) // 100)
    top1 = np.sort(x)[-k:].sum() / x.sum() if x.sum() else np.nan
    yrs_pos = (yrs > 0).mean()
    ok = (len(s) >= 300 and x.mean() >= 0.0015 and t >= 2.5 and yrs_pos >= 0.7
          and np.sign(early) == np.sign(late) == 1 and top_sym <= 0.25)
    print(f"\n{label}: n={len(s):,}  excess {x.mean() * 1e4:+.1f} bps (t={t:+.2f}, per-formation t={tf:+.2f}, "
          f"{s['entry'].nunique()} formations)  median {np.median(x) * 1e4:+.1f}  win {(x > 0).mean():.0%}  "
          f"years+ {(yrs > 0).sum()}/{len(yrs)}  17-20 {early * 1e4:+.1f} / 21-26 {late * 1e4:+.1f}  "
          f"top symbol {top_sym:.0%} ({pnl.idxmax()})  top-1% share {top1:.0%}"
          + (f"  -> {'PASS' if ok else 'FAIL'}" if gate else ""), flush=True)
    print("   excess by year: " + "  ".join(f"{y}:{v:+.0f}" for y, v in yrs.items()), flush=True)


def score(tr: pd.DataFrame, bench: pd.Series) -> pd.DataFrame:
    b = bench.reindex(tr["entry"]).to_numpy()
    tr = tr.assign(excess=tr["side"] * (tr["ret"] - b) - 2 * SLIP).dropna(subset=["excess"])
    return tr.assign(year=tr["entry"].dt.year)


def main():
    syms = pool_symbols()
    daily = load_daily(syms)
    d = daily.reset_index().drop_duplicates(["timestamp", "symbol"])
    d["day"] = pd.to_datetime(d["timestamp"]).dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    close = d.pivot_table(index="day", columns="symbol", values="close").sort_index()
    opens = d.pivot_table(index="day", columns="symbol", values="open").reindex(close.index)
    bench = pool_benchmark(daily, [H])[H]
    bench.index = bench.index.tz_convert("America/New_York").tz_localize(None).normalize()
    bench = bench.groupby(level=0).last()
    for design, ranks, name in ((True, syms[:500], "design ranks 1-500"),
                                (False, syms[500:], "held-out ranks 501-1000")):
        cols = [s for s in ranks if s in close.columns]
        near, gh, st = trades(close, opens, cols)
        near, gh = score(near, bench), score(gh, bench)
        print(f"\n=== {name}: {len(cols)} names; split-suspect trades dropped {st['dropped']}, "
              f"kept trades with a <= -25% gap {st['crash_kept']} ===", flush=True)
        report(near, f"near 52wk high (ratio >= {NEAR}), long only, {name}", gate=design)
        report(gh, "  George-Hwang top/bottom decile, per leg-trade")
        report(gh[gh["side"] == 1], "    top decile long")
        report(gh[gh["side"] == -1], "    bottom decile short")


if __name__ == "__main__":
    main()
