"""
End-of-day reversal (research/journal.md, 2026-11-13 end-of-day-reversal, ideas.md 13).

R = log(close of 15:29 bar / open of 09:30 bar). Each day across eligible stocks: bottom decile -> long, top
decile -> short. Entry = open of the 15:31 bar, exit = close of the 15:59 bar. 2 bps/side.
Gate and replication (rank 1-100, 101-277) as steady_movers.stats.

Usage: python scripts/research/end_of_day_reversal.py [--workers=6]
"""
import os
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

import numpy as np
import pandas as pd

import steady_movers as sm

CACHE = sm.CACHE
YEARS = sm.YEARS
COST = sm.COST
OPEN, SIG, ENTRY, EXIT = 570, 929, 931, 959
ETFS = {"SPY", "QQQ", "IWM"}
ET = "America/New_York"


def run_name(sym):
    out, dv = [], {}
    for y in YEARS:
        p = os.path.join(CACHE, f"m1_{sym}_{y}.pkl")
        if not os.path.exists(p):
            continue
        df = pd.read_pickle(p).astype("float64")
        if df.empty:
            continue
        df = df[~df.index.duplicated()].sort_index()
        hm = df.index.hour * 60 + df.index.minute
        df = df[(hm >= OPEN) & (hm <= EXIT)]
        dates = df.index.date
        dv[y] = (df["close"] * df["volume"]).groupby(dates).sum().mean()
        g = pd.DataFrame({"d": dates, "hm": (df.index.hour * 60 + df.index.minute).to_numpy(),
                          "o": df["open"].to_numpy(), "c": df["close"].to_numpy()})
        first = g.groupby("d")["hm"].min()
        k = g[g["hm"] <= SIG].groupby("d").size()
        o930 = g[g["hm"] == OPEN].set_index("d")["o"]
        c1529 = g[g["hm"] == SIG].set_index("d")["c"]
        eo = g[g["hm"] == ENTRY].set_index("d")["o"]
        xc = g[g["hm"] == EXIT].set_index("d")["c"]
        a = pd.concat([o930.rename("o0"), c1529.rename("cs"), eo.rename("eo"), xc.rename("xc")], axis=1, join="inner")
        a = a[(first.reindex(a.index) == OPEN) & (k.reindex(a.index) >= 0.9 * (SIG - OPEN + 1))]
        R = np.log(a["cs"] / a["o0"])
        r = np.log(a["xc"] / a["eo"]) * 1e4
        out += [(sym, y, d, R_, r_) for d, R_, r_ in zip(a.index, R, r)]
    return sym, out, dv


def select(f: pd.DataFrame, q=0.1) -> pd.DataFrame:
    rq = f.groupby("date")["R"].rank(pct=True)
    t = f[(rq > 1 - q) | (rq <= q)].copy()
    t["side"] = -np.sign(t["R"])
    t = t[t["side"] != 0]
    t["bps"] = t["side"] * t["r"]
    return t


def main():
    workers = int(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--workers=")), 6))
    syms = [s for s in sm.names() if s not in ETFS]
    rows, dv = [], {}
    with ProcessPoolExecutor(workers) as ex:
        for i, (sym, r, d) in enumerate(ex.map(run_name, syms)):
            rows += r
            dv.update({(sym, y): v for y, v in d.items()})
            if i % 50 == 0:
                print(f"  done {i + 1}/{len(syms)}", flush=True)
    f = pd.DataFrame(rows, columns=["sym", "year", "date", "R", "r"])
    rank = {}
    for y in YEARS:
        prev = sorted(((dv[(s, y - 1)], s) for s in syms if (s, y - 1) in dv), reverse=True)
        rank.update({(s, y): i + 1 for i, (_, s) in enumerate(prev)})
    f["rank"] = [rank.get((s, y), 0) for s, y in zip(f["sym"], f["year"])]
    print(f"stocks {len(syms)} ({f['sym'].nunique()} with data); eligible name-days {len(f)}, "
          f"median names a day {int(f.groupby('date').size().median())}")
    print("names by year: " + "  ".join(f"{y}:{n}" for y, n in f.groupby("year")["sym"].nunique().items()))

    t = select(f)
    t.to_csv(os.path.join(CACHE, "end_of_day_reversal_trades.csv"), index=False)
    rep = {"rank 1-100": t[(t["rank"] >= 1) & (t["rank"] <= 100)], "rank 101-277": t[t["rank"] >= 101]}
    ok = sm.stats(t, "PRIMARY: fade R decile 15:31 -> 15:59, all stocks, 2016-26", rep=rep)
    print(f"  net at 1 bps/side {(t['bps'] - 2).mean():+.2f}")

    spike = pd.read_csv(os.path.join(CACHE, "spike_wide_trades.csv"))
    spike = spike[spike["rank"] <= 25]
    sday = (spike["bps"] - COST).groupby(pd.to_datetime(spike["entry"], utc=True).dt.tz_convert(ET).dt.date).sum()
    d = (t["bps"] - COST).groupby(t["date"]).sum()
    j = pd.concat([d, sday], axis=1, join="inner")
    print(f"  daily P&L corr with spike_mom rank 1-25: {j.corr().iloc[0, 1]:+.2f} on {len(j)} shared days")

    sm.stats(t[t["side"] > 0], "reported: long leg (losers)", gated=False)
    sm.stats(t[t["side"] < 0], "reported: short leg (winners)", gated=False)
    sm.stats(select(f, 0.05), "reported: top/bottom 5%", gated=False)
    print(f"\nVERDICT end-of-day-reversal: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
