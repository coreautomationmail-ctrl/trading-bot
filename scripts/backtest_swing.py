# scripts/backtest_swing.py
"""
Multi-day swing signals on daily bars -- the "tens of bps per trade" test the
intraday families (ORB, Stocks-in-Play, gap-and-go, index momentum) failed.

Signals (all entries at the NEXT session's open, so nothing uses same-bar data):
  drift   event-day drift, a post-earnings/news-drift proxy (Bernard & Thomas
          1989; Chan, Jegadeesh & Lakonishok 1996). Event = |close-to-close|
          >= MOVE and volume >= VOLX x 20-day average. Trade in the move's
          direction, hold H sessions, exit at the close.
  rsi2    Connors-style mean reversion: close > SMA200 and RSI(2) < 10 ->
          buy; exit at the first close above SMA5 or after 10 sessions.

Every trade is also scored as EXCESS return: side x (trade return minus the
equal-weight pool's return over the same sessions, entry open to exit close).
The pool is built from symbols still listed today, so delisted losers are
missing and raw long returns are flattered; benchmarking against the same
pool on the same dates cancels most of that bias and the market move.

Costs: --slippage=BPS per side (default 5 -> 10 bps round trip).

Usage: python scripts/backtest_swing.py [--signal=drift|rsi2] [--slippage=5]
                                         [--move=0.05] [--volx=3] [--pool=1000]
Data: Alpaca SIP daily bars 2016-01-01..2026-10-02, cached in scripts/.cache/.
"""
import os
import sys
import math

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

from backtest_sip import cached, fetch_bars, list_universe, list_etf_like

START, END = "2016-01-01", "2026-10-02"
POOL_START, POOL_END = "2016-01-01", "2016-03-31"   # pool ranked on data from the first quarter only
HOLDS = (1, 5, 10, 20)

args = dict(a.lstrip("-").split("=", 1) for a in sys.argv[1:] if "=" in a)
SIGNAL = args.get("signal", "drift")
SLIP = float(args.get("slippage", 5)) / 1e4
MOVE = float(args.get("move", 0.05))
VOLX = float(args.get("volx", 3))
POOL = int(args.get("pool", 1000))


def pool_symbols() -> list:
    """The POOL most liquid non-ETF names by dollar volume in Q1 2016."""
    def build():
        etf = cached("etf_like", list_etf_like)
        syms = [s for s in cached("universe", list_universe) if s not in etf]
        d = fetch_bars(syms, "1Day", POOL_START, POOL_END, chunk=500)
        dv = (d["close"] * d["volume"]).groupby(d["symbol"]).mean()
        px = d.groupby("symbol")["close"].last()
        dv = dv[px.reindex(dv.index) > 5]
        return list(dv.sort_values(ascending=False).index)
    return cached("swing_pool_rank_2016Q1", build)[:POOL]


def load_daily(syms: list) -> pd.DataFrame:
    return cached(f"swing_daily_{START}_{END}_pool{len(syms)}",
                  lambda: fetch_bars(syms, "1Day", START, END, chunk=100))


def rsi(close: pd.Series, n: int) -> pd.Series:
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + up / dn.replace(0, np.nan))


def trades_for(sym: str, g: pd.DataFrame) -> list:
    g = g.sort_index()
    o, c, v = g["open"].values, g["close"].values, g["volume"].values
    dates = g.index
    n = len(g)
    out = []
    if SIGNAL == "drift":
        ret = g["close"].pct_change().values
        adv = g["volume"].rolling(20).mean().shift(1).values
        for t in range(21, n - 1):
            if not (abs(ret[t]) >= MOVE and adv[t] > 0 and v[t] >= VOLX * adv[t] and c[t] > 5):
                continue
            side = 1 if ret[t] > 0 else -1
            e = t + 1                                  # enter next open
            for h in HOLDS:
                x = e + h - 1                          # exit at close of the h-th session
                if x >= n:
                    continue
                gross = side * (c[x] / o[e] - 1)
                out.append((sym, dates[e], side, h, gross, ret[t]))
    else:  # rsi2
        sma200 = g["close"].rolling(200).mean().values
        sma5 = g["close"].rolling(5).mean().values
        r2 = rsi(g["close"], 2).values
        t = 200
        while t < n - 1:
            if c[t] > sma200[t] and r2[t] < 10:
                e = t + 1
                x = e
                while x < n - 1 and x - e < 9 and not (c[x] > sma5[x]):
                    x += 1
                out.append((sym, dates[e], 1, x - e + 1, c[x] / o[e] - 1, r2[t]))
                t = x + 1
            else:
                t += 1
    return out


def pool_benchmark(daily: pd.DataFrame, holds) -> dict:
    """{h: Series(entry date -> equal-weight mean of close[e+h-1]/open[e]-1)}."""
    d = daily.reset_index().drop_duplicates(["timestamp", "symbol"]).set_index("timestamp")
    o = d.pivot_table(index=d.index, columns="symbol", values="open")
    c = d.pivot_table(index=d.index, columns="symbol", values="close")
    out = {}
    for h in holds:
        r = c.shift(-(h - 1)) / o - 1
        out[h] = r.clip(-0.9, 5).mean(axis=1)
    return out


def tstat(x: np.ndarray) -> float:
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x)))) if len(x) > 2 else float("nan")


def main():
    syms = pool_symbols()
    daily = load_daily(syms)
    print(f"signal={SIGNAL} pool={len(syms)} rows={len(daily):,} slippage={SLIP*1e4:.0f}bps/side"
          + (f" move>={MOVE:.0%} vol>={VOLX}x" if SIGNAL == "drift" else ""))
    rows = []
    for sym, g in daily.groupby("symbol"):
        g = g[~g.index.duplicated()].sort_index()
        if len(g) < 250:
            continue
        rows += trades_for(sym, g)
    t = pd.DataFrame(rows, columns=["symbol", "date", "side", "hold", "gross", "trigger"])
    t["net"] = t["gross"] - 2 * SLIP
    bench = pool_benchmark(daily, sorted(t["hold"].unique()))
    b = np.array([bench[h].get(d, np.nan) for h, d in zip(t["hold"], t["date"])])
    t["excess"] = t["gross"] - t["side"] * b
    t = t.dropna(subset=["excess"])
    t["year"] = pd.to_datetime(t["date"]).dt.year

    groups = [("hold", h) for h in sorted(t["hold"].unique())] if SIGNAL == "drift" else [("all", None)]
    for name, h in groups:
        sub = t if h is None else t[t["hold"] == h]
        for side in ((1, -1) if SIGNAL == "drift" else (1,)):
            s = sub[sub["side"] == side]
            if len(s) < 30:
                continue
            yrs = s.groupby("year")["net"].mean() * 1e4
            print(f"\n{'LONG ' if side == 1 else 'SHORT'} {name}={h}: n={len(s):,} "
                  f"gross {s.gross.mean()*1e4:+.1f}bps  net {s.net.mean()*1e4:+.1f}bps (t={tstat(s.net.values):.1f})  "
                  f"excess {s.excess.mean()*1e4:+.1f}bps (t={tstat(s.excess.values):.1f})  "
                  f"win {(s.net > 0).mean():.0%}  years+ {int((yrs > 0).sum())}/{len(yrs)}")
            print("   net by year (bps): " + "  ".join(f"{y}:{v:+.0f}" for y, v in yrs.items()))


if __name__ == "__main__":
    main()
