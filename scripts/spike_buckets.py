"""
Where does the spike_mom edge live? (8x / k5 / hold 60 / 1-bar delay, SIP, 2023-2026)

Pools all 62 names (12 core + 50 wide) and splits trades by:
  dv_rank  the name's rank by PRIOR-year average daily dollar volume among the
           62 (ex-ante, no hindsight): top 10 / 11-25 / 26-62
  tod      signal time: 09:35-10:30 / 10:30-13:00 / 13:00-14:58
  side     long / short
Pre-registered: a bucket counts only if gross >= 6 bps and t >= 2 in BOTH the
core and wide halves (so a rank effect isn't just "the 12 names I picked").

Usage: python scripts/spike_buckets.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd

import backtest_scalp as bt

YEARS = range(2023, 2027)


def main():
    names = bt.SYMBOLS + bt.WIDE
    dv = {}   # (sym, year) -> avg daily dollar volume
    rows = []
    for sym in names:
        for y in range(YEARS.start - 1, YEARS.stop):
            df = bt.load_year(sym, y)
            if df.empty:
                continue
            dv[(sym, y)] = (df["close"].astype("float64") * df["volume"]).groupby(df.index.date).sum().mean()
            if y not in YEARS:
                continue
            f = bt.features(df)
            hm = f.index.hour * 60 + f.index.minute
            w = (hm >= 575) & (hm <= 930) & (f["ret_std"] > 0)
            m = (w & (f["volume"] >= 8 * f["vol_mean"]) & (f["ret"].abs() >= 5 * f["ret_std"])).to_numpy()
            side = np.sign(f["ret"].to_numpy())
            # trades() returns (date, bps); rebuild signal minute and side with the same skip rules
            idx = np.flatnonzero(m)
            tr = bt.trades(f, m, side, 60, 1)
            # map each trade back to its signal bar: trades are taken in idx order, skipping busy/edge
            busy, k = -1, 0
            for i in idx:
                j = i + 61
                if i <= busy or j >= len(f) or f["date"].iat[j] != f["date"].iat[i] or hm[j] > 959:
                    continue
                rows.append((sym, y, hm[i], int(side[i]), tr[k][1]))
                busy, k = j, k + 1
        print(f"done {sym}", flush=True)
    t = pd.DataFrame(rows, columns=["sym", "year", "hm", "side", "bps"])
    rank = {}
    for y in YEARS:
        prev = sorted(((dv[(s, y - 1)], s) for s in names if (s, y - 1) in dv), reverse=True)
        rank.update({(s, y): r + 1 for r, (_, s) in enumerate(prev)})
    t["rank"] = [rank.get((s, y), 99) for s, y in zip(t["sym"], t["year"])]
    t["dv"] = pd.cut(t["rank"], [0, 10, 25, 99], labels=["top10", "11-25", "26+"])
    t["tod"] = pd.cut(t["hm"], [0, 630, 780, 999], labels=["open", "mid", "late"])
    t["side_"] = np.where(t["side"] > 0, "long", "short")
    t["half"] = np.where(t["sym"].isin(bt.SYMBOLS), "core", "wide")

    def line(g):
        n, mu, sd = len(g), g.mean(), g.std()
        return f"n={n:5d} {mu:+6.2f} bps t={mu / sd * np.sqrt(n) if n > 1 and sd else 0:+5.2f}"
    for col in ("dv", "tod", "side_"):
        print(f"\n== {col}")
        for b, g in t.groupby(col, observed=True):
            core, wide = g[g["half"] == "core"]["bps"], g[g["half"] == "wide"]["bps"]
            ok = all(len(x) > 30 and x.mean() >= 6 and x.mean() / x.std() * np.sqrt(len(x)) >= 2 for x in (core, wide))
            print(f"  {b!s:6s} all {line(g['bps'])} | core {line(core)} | wide {line(wide)} {'PASS' if ok else ''}")


if __name__ == "__main__":
    main()
