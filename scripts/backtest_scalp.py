"""
1-minute scalping backtest: can small intraday moves be traded above cost?

Data: Alpaca SIP 1-min bars, regular session, 2016-01 -> 2026-10, for a dozen
of the most liquid names. Cached per symbol-year (scripts/.cache/m1_SYM_YEAR).

Rules (signal on bar i's close, enter at bar i+1's open, exit at the close of
bar i+HOLD, one position per symbol at a time, no entries before 09:35 or after
15:30, never held past 15:59):

  spike_mom   volume >= VOL_X x trailing 30-bar mean AND |bar return| >= K x
              trailing 30-bar std  -> trade WITH the bar
  spike_fade  same trigger, trade AGAINST the bar (overreaction)
  vwap_fade   close deviates from session VWAP by >= K x trailing 30-bar std
              of that deviation -> trade back toward VWAP

Output: gross bps/trade, t-stat on daily means, per-year sign, and the
break-even cost per side (gross / 2). The cost to beat: SPY/QQQ ~0.5-1 bps
per side (half-spread + slippage on market orders), mega-cap stocks ~1-3 bps.
Adoption bar: gross >= 2x a realistic round-trip cost, t >= 3, and positive
in >= 8 of 11 years.

Usage: python scripts/backtest_scalp.py [--fetch-only] [--symbols=SPY,QQQ]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pandas as pd

import backtest_sip as sip

SYMBOLS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "TSLA",
           "META", "AMD", "GOOGL", "JPM"]
# Out-of-sample universe: the most liquid non-ETF names by dollar volume in
# Q1 2016 (swing_pool_rank_2016Q1), minus the 12 above and GOOG -- picked
# without hindsight about which names later ran.
WIDE = ["BAC", "XOM", "GE", "PFE", "GILD", "C", "WFC", "T", "CVX", "JNJ", "MCD",
        "VZ", "DIS", "PG", "BHC", "BKNG", "V", "INTC", "CSCO", "GS", "WMT", "BA",
        "HD", "IBM", "CMG", "CMCSA", "KO", "SLB", "AVGO", "QCOM", "RTX", "ORCL",
        "NKE", "SBUX", "AMGN", "MRK", "COP", "BIDU", "AIG", "F", "PEP", "MDLZ",
        "BIIB", "ABBV", "CVS", "UNP", "AXP", "BMY", "CAT", "PM"]
YEARS = range(2016, 2027)
END = "2026-10-27"
FEED = "sip"
HOLDS = (5, 15, 30)
VOL_X = 5.0
K_SPIKE = 4.0
K_VWAP = 3.0


def load_year(sym: str, year: int) -> pd.DataFrame:
    def build():
        start = f"{year}-01-01"
        end = END if year == 2026 else f"{year + 1}-01-01"
        df = sip.fetch_bars([sym], "1Min", start, end, chunk=1, feed=FEED)
        if df.empty:
            return df
        return df[["open", "high", "low", "close", "volume"]].astype("float32")
    prefix = "m1" if FEED == "sip" else f"m1{FEED}"
    return sip.cached(f"{prefix}_{sym}_{year}", build)


def features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["date"] = df.index.date
    g = df.groupby("date", sort=False)
    ret = np.log(df["close"] / df["open"])
    df["ret"] = ret
    # trailing stats exclude the current bar; computed within the day
    df["vol_mean"] = g["volume"].transform(lambda v: v.rolling(30, min_periods=20).mean().shift(1))
    df["ret_std"] = ret.groupby(df["date"]).transform(lambda r: r.rolling(30, min_periods=20).std().shift(1))
    tp = (df["high"] + df["low"] + df["close"]) / 3
    pv = (tp * df["volume"]).groupby(df["date"]).cumsum()
    vv = df["volume"].groupby(df["date"]).cumsum()
    dev = np.log(df["close"] / (pv / vv))
    df["vdev"] = dev
    df["vdev_std"] = dev.groupby(df["date"]).transform(lambda d: d.rolling(30, min_periods=20).std().shift(1))
    return df


def signals(df: pd.DataFrame) -> dict:
    hm = df.index.hour * 60 + df.index.minute
    window = (hm >= 9 * 60 + 35) & (hm <= 15 * 60 + 30)
    spike = window & (df["volume"] >= VOL_X * df["vol_mean"]) & \
        (df["ret"].abs() >= K_SPIKE * df["ret_std"]) & (df["ret_std"] > 0)
    stretch = window & (df["vdev"].abs() >= K_VWAP * df["vdev_std"]) & (df["vdev_std"] > 0)
    s = np.sign(df["ret"].to_numpy())
    v = np.sign(df["vdev"].to_numpy())
    return {
        "spike_mom": (spike.to_numpy(), s),
        "spike_fade": (spike.to_numpy(), -s),
        "vwap_fade": (stretch.to_numpy(), -v),
    }


def trades(df: pd.DataFrame, mask: np.ndarray, side: np.ndarray, hold: int, delay: int = 0) -> list:
    opens = df["open"].to_numpy()
    closes = df["close"].to_numpy()
    dates = df["date"].to_numpy()
    hm = df.index.hour * 60 + df.index.minute
    n = len(df)
    out = []
    busy_until = -1
    for i in np.flatnonzero(mask):
        if i <= busy_until or i + hold + delay >= n:
            continue
        j = i + hold + delay
        if dates[j] != dates[i] or hm[j] > 15 * 60 + 59:
            continue
        entry, exit_ = opens[i + 1 + delay], closes[j]
        out.append((dates[i], side[i] * np.log(exit_ / entry) * 1e4))
        busy_until = j
    return out


def sweep(syms):
    """spike_mom robustness: VOL_X x K x hold x entry delay (extra bars of latency)."""
    grid = [(vx, k, h, dl) for vx in (3, 5, 8) for k in (3, 4, 5)
            for h in (15, 30, 60) for dl in (0, 1)]
    res = {g: [] for g in grid}
    for sym in syms:
        for y in YEARS:
            f = features(load_year(sym, y))
            hm = f.index.hour * 60 + f.index.minute
            window = (hm >= 9 * 60 + 35) & (hm <= 15 * 60 + 30) & (f["ret_std"] > 0)
            side = np.sign(f["ret"].to_numpy())
            for vx in (3, 5, 8):
                vol_ok = window & (f["volume"] >= vx * f["vol_mean"])
                for k in (3, 4, 5):
                    mask = (vol_ok & (f["ret"].abs() >= k * f["ret_std"])).to_numpy()
                    for h in (15, 30, 60):
                        for dl in (0, 1):
                            res[(vx, k, h, dl)].extend(
                                (sym, d, b) for d, b in trades(f, mask, side, h, dl))
        print(f"swept {sym}", flush=True)
    print("\nvol_x  k  hold delay  trades  gross  t(daily) yrs+  stock  net@2bps/side")
    for (vx, k, h, dl), rows in res.items():
        t = pd.DataFrame(rows, columns=["sym", "date", "bps"])
        daily = t.groupby("date")["bps"].mean()
        ts = daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily)))
        yrs = t.groupby(pd.to_datetime(t["date"]).dt.year)["bps"].mean()
        stk = t[~t["sym"].isin(["SPY", "QQQ", "IWM"])]["bps"].mean()
        print(f"{vx:5d} {k:2d} {h:5d} {dl:4d}  {len(t):6d} {t['bps'].mean():6.2f} {ts:7.2f}  "
              f"{(yrs > 0).sum():2d}/{len(yrs)}  {stk:5.2f}  {t['bps'].mean() - 4:6.2f}")


def iex_check(syms, cfgs=((5, 4, 30), (8, 4, 60), (8, 5, 60))):
    """Detect spikes on IEX bars (what Alpaca's free live feed shows), fill on
    SIP prices (what the market trades at). Compare with SIP-detected spikes."""
    global FEED
    res = {}
    for sym in syms:
        for y in YEARS:
            FEED = "sip"
            s = features(load_year(sym, y))
            FEED = "iex"
            iex = features(load_year(sym, y))
            # IEX signal at minute t -> same minute on the SIP grid
            iex = iex.reindex(s.index)
            hm = s.index.hour * 60 + s.index.minute
            w = (hm >= 9 * 60 + 35) & (hm <= 15 * 60 + 30)
            for vx, k, h in cfgs:
                for src, f in (("sip", s), ("iex", iex)):
                    m = (w & (f["ret_std"] > 0) & (f["volume"] >= vx * f["vol_mean"]) &
                         (f["ret"].abs() >= k * f["ret_std"])).fillna(False).to_numpy()
                    side = np.sign(f["ret"].fillna(0).to_numpy())
                    res.setdefault((vx, k, h, src), []).extend(
                        (sym, d, b) for d, b in trades(s, m, side, h, 1))
    FEED = "sip"
    print("\nentry delayed 1 bar (latency); prices = SIP")
    print("vol_x  k  hold  detect  trades  gross  t(daily)")
    for (vx, k, h, src), rows in sorted(res.items()):
        t = pd.DataFrame(rows, columns=["sym", "date", "bps"])
        daily = t.groupby("date")["bps"].mean()
        ts = daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily))) if len(daily) > 2 else float("nan")
        print(f"{vx:5d} {k:2d} {h:5d}   {src:4s}  {len(t):6d} {t['bps'].mean():6.2f} {ts:7.2f}")


def main():
    syms = SYMBOLS
    fetch_only = False
    global YEARS, FEED
    do_sweep = False
    for a in sys.argv[1:]:
        if a.startswith("--symbols="):
            v = a.split("=", 1)[1]
            syms = WIDE if v == "wide" else v.split(",")
        elif a.startswith("--years="):
            lo, hi = a.split("=", 1)[1].split("-")
            YEARS = range(int(lo), int(hi) + 1)
        elif a.startswith("--feed="):
            FEED = a.split("=", 1)[1]
        elif a == "--fetch-only":
            fetch_only = True
        elif a == "--sweep":
            do_sweep = True
        elif a == "--iex-check":
            return iex_check(syms)
    if do_sweep:
        return sweep(syms)

    results = {}   # (rule, hold) -> list of (sym, date, bps)
    for sym in syms:
        for y in YEARS:
            df = load_year(sym, y)
            print(f"{sym} {y}: {len(df)} bars", flush=True)
            if fetch_only or df.empty:
                continue
            f = features(df)
            for rule, (mask, side) in signals(f).items():
                for h in HOLDS:
                    results.setdefault((rule, h), []).extend(
                        (sym, d, b) for d, b in trades(f, mask, side, h))
    if fetch_only:
        return

    print("\nrule        hold  trades   gross_bps  t(daily)  yrs+/n  breakeven/side  ETF bps  stock bps")
    for (rule, h), rows in sorted(results.items()):
        t = pd.DataFrame(rows, columns=["sym", "date", "bps"])
        if t.empty:
            continue
        daily = t.groupby("date")["bps"].mean()
        tstat = daily.mean() / (daily.std(ddof=1) / np.sqrt(len(daily))) if len(daily) > 1 else 0
        yrs = t.groupby(pd.to_datetime(t["date"]).dt.year)["bps"].mean()
        etf = t[t["sym"].isin(["SPY", "QQQ", "IWM"])]["bps"].mean()
        stk = t[~t["sym"].isin(["SPY", "QQQ", "IWM"])]["bps"].mean()
        print(f"{rule:11s} {h:4d}  {len(t):6d}   {t['bps'].mean():8.2f}  {tstat:7.2f}   "
              f"{(yrs > 0).sum():2d}/{len(yrs):2d}   {t['bps'].mean() / 2:8.2f}      {etf:7.2f}  {stk:8.2f}")
        print("            by year: " + " ".join(f"{y % 100:02d}:{v:+.1f}" for y, v in yrs.items()))


if __name__ == "__main__":
    main()
