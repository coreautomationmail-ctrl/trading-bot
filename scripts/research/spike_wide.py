"""
Does the live spike signal work on more names? (research/journal.md, 2026-11-04 spike-wide-universe)

spike_mom exactly as live on the 62 + the 215 extra names, split by PRIOR-year
dollar-volume rank: 1-25 (reference) and 26-100 (the names being tested).
Costs 2 bps/side; also reported at 4 bps/side and with the pod's 4-position cap.

Usage: python scripts/research/spike_wide.py
"""
import json
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

import numpy as np
import pandas as pd

import backtest_scalp as bt
from spike_size import YEARS, DESIGN, VOL_X, K, HOLD, DELAY, FIRST, LAST, COST_BPS, tstat

MAX_OPEN = 4


def collect() -> pd.DataFrame:
    names = bt.SYMBOLS + bt.WIDE
    extra = json.load(open(os.path.join(HERE, "..", ".cache", "m1_extra_symbols.json")))
    names += [s for s in extra if s not in names]
    dv, rows = {}, []
    for sym in names:
        for y in range(YEARS.start - 1, YEARS.stop):
            df = bt.load_year(sym, y)
            if df.empty:
                continue
            dv[(sym, y)] = (df["close"].astype("float64") * df["volume"]).groupby(df.index.date).sum().mean()
            if y not in YEARS:
                continue
            f = bt.features(df.astype("float64"))
            hm = f.index.hour * 60 + f.index.minute
            m = ((hm >= FIRST) & (hm <= LAST) & (f["ret_std"] > 0) & (f["volume"] >= VOL_X * f["vol_mean"])
                 & (f["ret"].abs() >= K * f["ret_std"])).to_numpy()
            side = np.sign(f["ret"].to_numpy())
            opens, closes, dates = f["open"].to_numpy(), f["close"].to_numpy(), f["date"].to_numpy()
            busy = -1
            for i in np.flatnonzero(m):           # same skip rules as spike_size
                j = i + HOLD + DELAY
                if i <= busy or j >= len(f) or dates[j] != dates[i] or hm[j] > 959:
                    continue
                bps = side[i] * np.log(closes[j] / opens[i + 1 + DELAY]) * 1e4
                rows.append((sym, y, f.index[i + 1 + DELAY], f.index[j], bps))
                busy = j
        print(f"done {sym}", flush=True)
    t = pd.DataFrame(rows, columns=["sym", "year", "entry", "exit", "bps"])
    rank = {}
    for y in YEARS:
        prev = sorted(((dv[(s, y - 1)], s) for s in names if (s, y - 1) in dv), reverse=True)
        rank.update({(s, y): r + 1 for r, (_, s) in enumerate(prev)})
    t["rank"] = [rank.get((s, y), 9999) for s, y in zip(t["sym"], t["year"])]
    return t


def capped(t: pd.DataFrame) -> pd.DataFrame:
    """Trades the pod could actually take with MAX_OPEN positions, earliest entry first."""
    keep, open_ = [], []
    for row in t.sort_values("entry").itertuples():
        open_ = [x for x in open_ if x > row.entry]
        if len(open_) < MAX_OPEN:
            keep.append(row.Index)
            open_.append(row.exit)
    return t.loc[keep]


def line(label, g, cost=COST_BPS):
    x = g["bps"] - 2 * cost
    by = g.assign(x=x).groupby("year")["x"].mean()
    print(f"  {label:26s} n={len(g):5d} net {x.mean():+6.1f} (t {tstat(x):+.2f})  years+ {(by > 0).sum()}/{len(by)}")


def main():
    t = collect()
    t.to_csv(os.path.join(HERE, "..", ".cache", "spike_wide_trades.csv"), index=False)
    held = ~t["year"].isin(DESIGN)
    ref, test = t["rank"] <= 25, t["rank"].between(26, 100)
    for label, sel in (("rank 1-25 (reference)", ref), ("rank 26-100 (test)", test)):
        print(f"\n== {label}")
        line("2017-21 design", t[sel & ~held])
        line("2022-26 held out", t[sel & held])
        line("2022-26 at 4 bps/side", t[sel & held], 4.0)
    g = t[test & held]
    x = g["bps"] - 2 * COST_BPS
    print("\nheld-out by year (net bps): " + "  ".join(f"{y}:{v:+.1f}" for y, v in g.assign(x=x).groupby("year")["x"].mean().items()))
    syms = g.groupby("sym")["bps"].sum()
    share = syms.max() / syms[syms > 0].sum()
    print(f"top symbol share of positive gross bps: {syms.idxmax()} {share:.0%}; trades per year {len(g) / 5:.0f}")
    print("\n== pod's 4-position cap, held out, rank 1-100 pooled")
    pooled = t[(t["rank"] <= 100) & held]
    c = capped(pooled)
    line("all signals", pooled)
    line("taken under cap", c)
    print(f"  sum of net bps: top 25 {(t[ref & held]['bps'] - 2 * COST_BPS).sum():+.0f}, capped pool {(c['bps'] - 2 * COST_BPS).sum():+.0f}")
    by = g.assign(x=x).groupby("year")["x"].mean()
    d = t[test & ~held]["bps"] - 2 * COST_BPS
    ok = len(g) >= 300 and x.mean() >= 5 and tstat(x) >= 2.5 and (by > 0).sum() >= 4 and d.mean() > 0 and share <= 0.25
    print(f"\nPRIMARY verdict: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
