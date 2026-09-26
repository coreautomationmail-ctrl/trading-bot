"""
Market intraday momentum backtest (index ETFs, last half hour).

Gao, Han, Li & Zhou (2018, JFE): the first half-hour return (prev close ->
10:00) predicts the last half-hour return (15:30 -> 16:00). Baltussen, Da,
Lammers & Martens (2021, JFE): the "rest of day" return (prev close -> 15:30)
predicts it more strongly, especially on high-volatility days (dealer gamma
hedging).

Rule: at 15:30 ET take a position in the sign of the signal, exit at the
16:00 close. One trade per symbol per day, no stop. P&L in bps of notional,
net of --slippage bps per side. Early-close days are skipped.

Signals:
  first  prev close -> 10:00
  rest   prev close -> 15:30
  both   trade only when first and rest agree

Filters:
  --vol=TERCILE  only trade days whose trailing 20-day realized vol of the
                 symbol is in the top tercile of its trailing 1-year history
                 (no look-ahead: both use data before the day)
  --min-move=BPS only trade when |signal| >= BPS
  --long-only / --short-only

Data: 30-min SIP bars 2016-01 -> 2026-09-24 for SPY/QQQ/IWM/DIA, cached.
Usage: python scripts/backtest_intraday_mom.py [--signal=first|rest|both]
         [--slippage=BPS] [--vol] [--min-move=BPS] [--long-only|--short-only]
         [--symbols=SPY,QQQ]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

import backtest_sip as sip

SYMBOLS = ["SPY", "QQQ", "IWM", "DIA"]
START, END = "2016-01-01", "2026-09-25"
SIGNAL = "rest"
SLIPPAGE_BPS = 1.0
VOL_FILTER = False
MIN_MOVE_BPS = 0.0
SIDES = (1, -1)
ONLY = None


def load() -> pd.DataFrame:
    def build():
        df = sip.fetch_bars(SYMBOLS, "30Min", START, END, chunk=len(SYMBOLS))
        return df[["symbol", "open", "close"]]
    return sip.cached(f"idx30_{START}_{END}", build)


def daily_table(bars: pd.DataFrame) -> pd.DataFrame:
    """Per (symbol, date): prev close, 10:00 price, 15:30 price, 16:00 close."""
    rows = []
    for sym, g in bars.groupby("symbol"):
        g = g.assign(date=g.index.date, hm=g.index.strftime("%H:%M"))
        for day, d in g.groupby("date"):
            by = d.set_index("hm")
            if not {"09:30", "15:00", "15:30"} <= set(by.index) or by.index.max() != "15:30":
                continue                                   # early close / gaps
            rows.append((sym, day, float(by.loc["09:30", "close"]),
                         float(by.loc["15:30", "open"]), float(by.loc["15:30", "close"])))
    t = pd.DataFrame(rows, columns=["symbol", "date", "p1000", "p1530", "p1600"])
    t = t.sort_values(["symbol", "date"])
    t["prev_close"] = t.groupby("symbol")["p1600"].shift(1)
    # Trailing realized vol (close-to-close) and its trailing 1y 67th pct.
    r = np.log(t["p1600"] / t["prev_close"])
    t["rv20"] = r.groupby(t["symbol"]).transform(lambda x: x.rolling(20).std().shift(1))
    t["rv_hi"] = t.groupby("symbol")["rv20"].transform(lambda x: x.rolling(252, min_periods=120).quantile(0.67))
    return t.dropna(subset=["prev_close"])


def run(t: pd.DataFrame) -> pd.DataFrame:
    first = t["p1000"] / t["prev_close"] - 1
    rest = t["p1530"] / t["prev_close"] - 1
    if SIGNAL == "first":
        sig = first
    elif SIGNAL == "rest":
        sig = rest
    else:
        sig = rest.where(np.sign(first) == np.sign(rest), 0.0)
    side = np.sign(sig)
    keep = side.isin(SIDES) & (sig.abs() * 1e4 >= MIN_MOVE_BPS)
    if VOL_FILTER:
        keep &= t["rv20"] > t["rv_hi"]
    last = t["p1600"] / t["p1530"] - 1
    out = t.loc[keep, ["symbol", "date"]].copy()
    out["side"] = side[keep]
    out["bps"] = (side[keep] * last[keep]) * 1e4 - 2 * SLIPPAGE_BPS
    out["year"] = pd.to_datetime(out["date"]).dt.year
    return out


def report(tr: pd.DataFrame) -> None:
    def stats(x):
        n = len(x)
        m = x.mean() if n else 0.0
        tstat = m / (x.std(ddof=1) / np.sqrt(n)) if n > 1 and x.std() > 0 else 0.0
        return n, m, (x > 0).mean() if n else 0.0, tstat, x.sum()
    print(f"\n{'':8}{'trades':>7}{'mean bps':>10}{'win':>7}{'t':>7}{'sum bps':>10}")
    for sym, g in tr.groupby("symbol"):
        n, m, w, ts, s = stats(g["bps"])
        print(f"{sym:8}{n:7d}{m:10.2f}{w:7.1%}{ts:7.2f}{s:10.0f}")
    n, m, w, ts, s = stats(tr["bps"])
    print(f"{'ALL':8}{n:7d}{m:10.2f}{w:7.1%}{ts:7.2f}{s:10.0f}")
    print("\nby year (all symbols):")
    pos = 0
    years = sorted(tr["year"].unique())
    for y in years:
        n, m, w, ts, s = stats(tr.loc[tr["year"] == y, "bps"])
        pos += m > 0
        print(f"  {y}  n={n:4d}  mean={m:+6.2f} bps  win={w:5.1%}  t={ts:+5.2f}")
    print(f"  positive years: {pos}/{len(years)}")


if __name__ == "__main__":
    for a in sys.argv[1:]:
        if a.startswith("--signal="):
            SIGNAL = a.split("=")[1]
            assert SIGNAL in ("first", "rest", "both")
        elif a.startswith("--slippage="):
            SLIPPAGE_BPS = float(a.split("=")[1])
        elif a == "--vol":
            VOL_FILTER = True
        elif a.startswith("--min-move="):
            MIN_MOVE_BPS = float(a.split("=")[1])
        elif a == "--long-only":
            SIDES = (1,)
        elif a == "--short-only":
            SIDES = (-1,)
        elif a.startswith("--symbols="):
            ONLY = a.split("=")[1].split(",")
        else:
            sys.exit(f"unknown arg {a}")
    print(f"signal={SIGNAL} | slippage={SLIPPAGE_BPS:g} bps/side | vol_filter={VOL_FILTER} | "
          f"min_move={MIN_MOVE_BPS:g} bps | sides={SIDES}")
    t = daily_table(load())
    if ONLY:
        t = t[t["symbol"].isin(ONLY)]
    report(run(t))
