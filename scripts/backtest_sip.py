# scripts/backtest_sip.py
"""
"Stocks in Play" opening-range-breakout backtest, after Zarattini, Barbon &
Aziz (2024, SSRN 4729284), run under THIS bot's safety envelope: no margin,
per-name allocation cap, risk cap, flatten at 15:45 ET.

Paper rules reproduced:
  universe   price > $5, 14-session ADV >= 1M shares, daily ATR14 > $0.50
  selection  first-5-min relative volume (RVOL5) >= 1.0, trade top 20 by RVOL5
  OR         the 9:30 5-min bar; direction = its close vs open
  entry      --entry=open  market at the 9:35 bar open (paper's method)
             --entry=break stop order at the OR extreme (this bot's method;
                           stop checked pessimistically inside the entry bar)
             long-only unless --short
  stop       0.10 x ATR14 from entry; no profit target
  exit       stop, else end of day (here 15:45 like executor.flatten_intraday)

Deviation: per window the candidate pool is the POOL_SIZE most liquid
eligible names ranked *before* the window starts (dollar ADV), not the whole
market -- 5-min bars for ~7,000 symbols over months is millions of rows.

Usage: python scripts/backtest_sip.py [--entry=open|break] [--slippage=BPS] [--short] [--pool=N] [--top=N]
Needs .env with Alpaca keys; SIP historical data works on the free plan.
Fetched bars are cached in scripts/.cache/ (gitignored).
"""
import os
import sys
import time
import pickle
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from alpaca_client import get_client
from strategy import get_atr

CACHE_DIR = os.path.join(os.path.dirname(__file__), ".cache")

WINDOWS = [
    ("2020 COVID Crash", "2020-02-15", "2020-04-30"),
    ("2022 Bear H1", "2022-01-01", "2022-05-31"),   # split: 9 months of 5-min bars OOMs
    ("2022 Bear H2", "2022-06-01", "2022-10-15"),
    ("2023 Calm Grind", "2023-05-01", "2023-07-31"),
    ("2024 Aug Flash Crash", "2024-08-01", "2024-08-10"),
]

STARTING_CASH = 100_000.0
MAX_RISK_PCT = 0.02          # mirrors executor.MAX_RISK_PCT
TOP_N = 20                   # paper: top-20 by RVOL5
POOL_SIZE = 400              # see "Deviation" above
MIN_PRICE = 5.0
MIN_ADV = 1_000_000
MIN_ATR = 0.50
MIN_RVOL = 1.0
RVOL_SESSIONS = 14
ATR_STOP_MULT = 0.10
FLATTEN_AT = (15, 45)
SLIPPAGE_BPS = 0.0
ALLOW_SHORT = False
ENTRY = "open"
DAILY_WARMUP_DAYS = 45       # calendar days before window for ADV14/ATR14
INTRADAY_WARMUP_DAYS = 28    # calendar days before window for the RVOL5 baseline
EXCHANGES = {"NYSE", "NASDAQ", "ARCA", "AMEX"}


# -- Data -------------------------------------------------------------------

def cached(name: str, build):
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, name + ".pkl")
    if os.path.exists(path):
        with open(path, "rb") as f:
            return pickle.load(f)
    data = build()
    with open(path, "wb") as f:
        pickle.dump(data, f)
    return data


def _to_ny(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    if df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    df.index = df.index.tz_convert("America/New_York")
    return df


def fetch_bars(symbols: list, timeframe: str, start: str, end: str, chunk: int) -> pd.DataFrame:
    api = get_client()
    frames = []
    n_chunks = -(-len(symbols) // chunk)
    for i in range(0, len(symbols), chunk):
        batch = symbols[i:i + chunk]
        df = None
        for attempt in range(3):
            try:
                df = api.get_bars(batch, timeframe, start=start, end=end, feed="sip").df
                break
            except Exception as e:
                print(f"    fetch {timeframe} chunk {i // chunk + 1}/{n_chunks} failed ({e}); retry")
                time.sleep(5)
        if df is None:
            continue
        df = _to_ny(df)
        if timeframe != "1Day":   # regular session only -- extended hours are ~40% of rows
            df = df.between_time("09:30", "15:59")
        frames.append(df[["open", "high", "low", "close", "volume", "symbol"]])
        print(f"    fetched {timeframe} chunk {i // chunk + 1}/{n_chunks} ({len(df)} rows)", flush=True)
        del df
    return pd.concat(frames) if frames else pd.DataFrame()


def list_universe() -> list:
    api = get_client()
    assets = api.list_assets(status="active", asset_class="us_equity")
    return sorted(a.symbol for a in assets
                  if a.tradable and a.exchange in EXCHANGES and a.symbol.isalpha() and len(a.symbol) <= 5)


ETF_WORDS = ("ETF", "ETN", "TRUST", "FUND", "SHARES", "INDEX", "PROSHARES", "DIREXION",
             "ISHARES", "SPDR", "BULL", "BEAR", "2X", "3X", "ULTRA", "INVERSE", "LEVERAGED")


def list_etf_like() -> set:
    """Symbols whose Alpaca asset name looks like an ETF/ETN. The paper's
    universe is stocks; leveraged ETFs otherwise dominate the RVOL ranking."""
    api = get_client()
    return {a.symbol for a in api.list_assets(status="active", asset_class="us_equity")
            if any(w in (a.name or "").upper() for w in ETF_WORDS)}


def daily_features(daily: pd.DataFrame) -> pd.DataFrame:
    """Per (symbol, session date): prev_close, adv14, atr14 -- all from bars
    strictly before that date, so nothing in today's session is used."""
    out = []
    for sym, g in daily.groupby("symbol"):
        g = g.sort_index()
        out.append(pd.DataFrame({
            "symbol": sym,
            "date": g.index.date,
            "prev_close": g["close"].shift(1).values,
            "adv14": g["volume"].rolling(RVOL_SESSIONS).mean().shift(1).values,
            "atr14": get_atr(g, period=14).shift(1).values,
        }))
    return pd.concat(out).set_index(["symbol", "date"])


def rvol5_table(intraday: pd.DataFrame) -> pd.Series:
    """RVOL5 per (symbol, date): today's 9:30 bar volume / mean 9:30-bar
    volume over the prior RVOL_SESSIONS sessions (needs >= 10 of them)."""
    first = intraday.between_time("09:30", "09:30")
    first = first.assign(date=first.index.date)
    rows = []
    for sym, g in first.groupby("symbol"):
        g = g.sort_values("date")
        base = g["volume"].rolling(RVOL_SESSIONS, min_periods=10).mean().shift(1)
        rows.append(pd.DataFrame({"symbol": sym, "date": g["date"].values,
                                  "rvol5": (g["volume"] / base).values}))
    return pd.concat(rows).set_index(["symbol", "date"])["rvol5"]


# -- Simulation -------------------------------------------------------------

def simulate_day(bars: pd.DataFrame, atr: float, alloc: float, equity: float):
    """One symbol, one session. Returns a trade dict or None if no entry."""
    slip = SLIPPAGE_BPS / 10_000.0
    session = bars.between_time("09:30", "15:45")
    if session.empty or (session.index[0].hour, session.index[0].minute) != (9, 30):
        return None
    first = session.iloc[0]
    if first["close"] > first["open"]:
        side = 1
    elif first["close"] < first["open"] and ALLOW_SHORT:
        side = -1
    else:
        return None
    trigger = float(first["high"]) if side == 1 else float(first["low"])
    stop_dist = ATR_STOP_MULT * atr
    pos = None

    for ts, bar in session.iloc[1:].iterrows():
        hi, lo, op, cl = (float(bar[k]) for k in ("high", "low", "open", "close"))
        if pos is None:
            if ENTRY == "open":
                raw = op                     # second candle's open, paper's entry
            else:
                hit = hi >= trigger if side == 1 else lo <= trigger
                if not hit:
                    continue
                raw = max(trigger, op) if side == 1 else min(trigger, op)   # gap-through fills at the open
            entry = raw * (1 + side * slip)
            qty = min(int(alloc / entry), int(equity * MAX_RISK_PCT / stop_dist))
            if qty < 1:
                return None
            pos = {"entry": entry, "qty": qty, "stop": entry - side * stop_dist, "time": ts}
            # Pessimistic: a stop touched inside the entry bar counts as a stop-out
        stopped = lo <= pos["stop"] if side == 1 else hi >= pos["stop"]
        if stopped:
            raw = min(pos["stop"], op) if side == 1 else max(pos["stop"], op)
            return _close(pos, raw * (1 - side * slip), side, ts, "stop")
        if (ts.hour, ts.minute) >= FLATTEN_AT:
            return _close(pos, cl * (1 - side * slip), side, ts, "eod")
    if pos is not None:   # data ended before the flatten bar -- mark at last close
        return _close(pos, float(session["close"].iloc[-1]) * (1 - side * slip), side, session.index[-1], "eod")
    return None


def _close(pos, exit_price, side, ts, how) -> dict:
    pnl = side * (exit_price - pos["entry"]) * pos["qty"]
    risk = abs(pos["entry"] - pos["stop"]) * pos["qty"]
    return {"entry_time": pos["time"], "exit_time": ts, "side": "long" if side == 1 else "short",
            "entry": pos["entry"], "exit": exit_price, "qty": pos["qty"],
            "pnl": pnl, "R": pnl / risk if risk else 0.0, "how": how}


def run_window(label: str, start: str, end: str) -> dict:
    print(f"\n{'=' * 70}\n{label}  ({start} to {end})\n{'=' * 70}", flush=True)
    tag = f"{start}_{end}"
    d_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=DAILY_WARMUP_DAYS)).strftime("%Y-%m-%d")
    i_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=INTRADAY_WARMUP_DAYS)).strftime("%Y-%m-%d")

    universe = cached("universe", list_universe)
    print(f"  universe: {len(universe)} symbols", flush=True)
    daily = cached(f"daily_{tag}", lambda: fetch_bars(universe, "1Day", d_start, end, chunk=200))
    feats = daily_features(daily)

    # Pool: eligible on the first session of the window, ranked by dollar ADV
    sessions = sorted(d for d in set(feats.index.get_level_values("date")) if str(d) >= start)
    first_day = sessions[0]
    f0 = feats.xs(first_day, level="date")
    elig0 = f0[(f0.prev_close > MIN_PRICE) & (f0.adv14 >= MIN_ADV) & (f0.atr14 > MIN_ATR)]
    pool = list((elig0.adv14 * elig0.prev_close).sort_values(ascending=False).head(POOL_SIZE).index)
    print(f"  eligible on {first_day}: {len(elig0)}; pool = top {len(pool)} by dollar ADV", flush=True)

    intraday = cached(f"intraday_{tag}_pool{POOL_SIZE}",
                      lambda: fetch_bars(pool, "5Min", i_start, end, chunk=40))
    rvol = rvol5_table(intraday)
    rvol_days = set(rvol.index.get_level_values("date"))
    etfs = cached("etf_like", list_etf_like)
    intraday = intraday.set_index("symbol", append=True).swaplevel().sort_index()
    have = set(intraday.index.get_level_values(0))

    equity = STARTING_CASH
    trades, curve = [], [equity]
    for day in sessions:
        try:
            fd = feats.xs(day, level="date")
        except KeyError:
            continue
        fd = fd[fd.index.isin(pool)]
        elig = fd[(fd.prev_close > MIN_PRICE) & (fd.adv14 >= MIN_ADV) & (fd.atr14 > MIN_ATR)]
        elig = elig[~elig.index.isin(etfs)]
        if day not in rvol_days:
            curve.append(equity)
            continue
        rv = rvol.xs(day, level="date")
        rv = rv[rv.index.isin(elig.index) & (rv >= MIN_RVOL)].sort_values(ascending=False).head(TOP_N)
        day_equity = equity
        alloc = day_equity / TOP_N
        for sym in rv.index:
            if sym not in have:
                continue
            g = intraday.loc[sym]
            bars = g[g.index.date == day]
            t = simulate_day(bars, float(elig.loc[sym, "atr14"]), alloc, day_equity)
            if t:
                t.update(symbol=sym, date=day, rvol5=float(rv[sym]))
                trades.append(t)
                equity += t["pnl"]
        curve.append(equity)

    return summarize(label, trades, curve, len(sessions))


def summarize(label, trades, curve, n_days) -> dict:
    df = pd.DataFrame(trades)
    n = len(df)
    wins = df[df.pnl > 0] if n else df
    losses = df[df.pnl < 0] if n else df
    gp = wins.pnl.sum() if n else 0.0
    gl = -losses.pnl.sum() if n else 0.0
    peak, dd = curve[0], 0.0
    for e in curve:
        peak = max(peak, e)
        dd = min(dd, e - peak)
    res = {"label": label, "trades": n, "days": n_days, "pnl": curve[-1] - curve[0],
           "win_rate": len(wins) / n if n else 0.0, "mean_R": df.R.mean() if n else 0.0,
           "median_R": df.R.median() if n else 0.0, "pf": gp / gl if gl else float("inf"),
           "max_dd": dd, "stops": int((df.how == "stop").sum()) if n else 0}
    print(f"  trades={n} ({n / max(n_days, 1):.1f}/day) | win={res['win_rate']:.1%} | "
          f"mean R={res['mean_R']:+.3f} | median R={res['median_R']:+.3f} | PF={res['pf']:.2f} | "
          f"PnL=${res['pnl']:,.2f} | max_dd=${dd:,.2f} | stopped={res['stops']}")
    if n:
        by = df.groupby("symbol").pnl.sum().sort_values()
        print(f"  worst: {', '.join(f'{s} {v:,.0f}' for s, v in by.head(3).items())} | "
              f"best: {', '.join(f'{s} {v:,.0f}' for s, v in by.tail(3)[::-1].items())}")
    return res


if __name__ == "__main__":
    for a in sys.argv[1:]:
        if a.startswith("--slippage="):
            SLIPPAGE_BPS = float(a.split("=")[1])
        elif a == "--short":
            ALLOW_SHORT = True
        elif a.startswith("--entry="):
            ENTRY = a.split("=")[1]
            assert ENTRY in ("open", "break")
        elif a.startswith("--pool="):
            POOL_SIZE = int(a.split("=")[1])
        elif a.startswith("--top="):
            TOP_N = int(a.split("=")[1])
        else:
            sys.exit(f"unknown arg {a}")
    print(f"entry={ENTRY} | slippage={SLIPPAGE_BPS:g} bps/side | short={ALLOW_SHORT} | pool={POOL_SIZE} | top={TOP_N}")
    results = [run_window(*w) for w in WINDOWS]
    print(f"\n{'=' * 70}\nTOTAL\n{'=' * 70}")
    n = sum(r["trades"] for r in results)
    wr = sum(r["mean_R"] * r["trades"] for r in results) / n if n else 0.0
    print(f"  trades={n} | PnL=${sum(r['pnl'] for r in results):,.2f} | mean R={wr:+.3f}")
