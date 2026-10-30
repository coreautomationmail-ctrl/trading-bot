"""
STRATTON allocator test (skfolio): how should capital be split across pods?

Streams (daily returns, 2016-2026, from the 1-min SIP cache):
  spike   spike_mom 8x/k5/hold 60, 1-bar entry delay, 4 bps round-trip cost,
          on the 12 core names; pod capital = 4 slots x $5k
  sleeve  buy-and-hold proxy: equal-weight daily returns of QQQ AAPL MSFT JPM
          (+SPY in place of VOO; KO isn't cached before 2022)
Walk-forward: fit on all prior years, hold the weights for the next year.
Compares EqualWeighted, InverseVolatility, HRP, and max-Sharpe MeanRisk.

Usage: python scripts/allocator_skfolio.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd
from skfolio import RiskMeasure
from skfolio.optimization import (EqualWeighted, HierarchicalRiskParity, InverseVolatility,
                                  MeanRisk, ObjectiveFunction)

import backtest_scalp as bt

YEARS = range(2016, 2027)
SLEEVE = ["SPY", "QQQ", "AAPL", "MSFT", "JPM"]
COST_BPS = 4.0
POD_CAPITAL = 20_000
POS_USD = 5_000


def spike_daily() -> pd.Series:
    rows = []
    for sym in bt.SYMBOLS:
        for y in YEARS:
            f = bt.features(bt.load_year(sym, y))
            hm = f.index.hour * 60 + f.index.minute
            w = (hm >= 9 * 60 + 35) & (hm <= 15 * 60 + 30) & (f["ret_std"] > 0)
            m = (w & (f["volume"] >= 8 * f["vol_mean"]) & (f["ret"].abs() >= 5 * f["ret_std"])).to_numpy()
            rows += bt.trades(f, m, np.sign(f["ret"].to_numpy()), 60, 1)
    t = pd.DataFrame(rows, columns=["date", "bps"])
    t["usd"] = (t["bps"] - COST_BPS) / 1e4 * POS_USD
    return (t.groupby("date")["usd"].sum() / POD_CAPITAL).rename("spike")


def sleeve_daily() -> pd.Series:
    closes = {}
    for sym in SLEEVE:
        c = pd.concat([bt.load_year(sym, y)["close"] for y in YEARS]).astype("float64")
        closes[sym] = c.groupby(c.index.date).last()
    return pd.DataFrame(closes).pct_change().mean(axis=1).rename("sleeve")


def stats(r: pd.Series) -> str:
    eq = (1 + r).cumprod()
    dd = (eq / eq.cummax() - 1).min()
    sh = r.mean() / r.std() * np.sqrt(252) if r.std() else 0
    return f"CAGR {eq.iloc[-1] ** (252 / len(r)) - 1:+6.1%}  Sharpe {sh:5.2f}  maxDD {dd:6.1%}"


def main():
    X = pd.concat([spike_daily(), sleeve_daily()], axis=1).dropna(subset=["sleeve"]).fillna(0.0)
    X.index = pd.to_datetime(X.index)
    print("stream stats, full sample:")
    for c in X:
        print(f"  {c:7s} {stats(X[c])}")
    print(f"  corr {X.corr().iloc[0, 1]:+.3f}")

    models = {
        "equal": EqualWeighted(),
        "inv_vol": InverseVolatility(),
        "hrp": HierarchicalRiskParity(risk_measure=RiskMeasure.CVAR),
        "max_sharpe": MeanRisk(objective_function=ObjectiveFunction.MAXIMIZE_RATIO),
    }
    oos = {k: [] for k in models}
    print("\nwalk-forward weights (spike / sleeve):")
    for y in range(2018, 2027):
        train, test = X[X.index.year < y], X[X.index.year == y]
        line = f"  {y}"
        for k, m in models.items():
            w = m.fit(train.to_numpy()).weights_
            oos[k].append(test.to_numpy() @ w)
            line += f"  {k} {w[0]:.2f}/{w[1]:.2f}"
        print(line)
    print("\nout-of-sample 2018-2026:")
    idx = X[X.index.year >= 2018].index
    for k, parts in oos.items():
        print(f"  {k:10s} {stats(pd.Series(np.concatenate(parts), index=idx))}")


if __name__ == "__main__":
    main()
