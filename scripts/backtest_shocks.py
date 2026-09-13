# scripts/backtest_shocks.py
"""
Replay the live ORB strategy (strategy.generate_signal, unmodified) against
historical 5-min bars for known market-shock windows, to sanity-check the
stop-loss/take-profit/kill-switch behavior before this ever trades real money.

Usage: python scripts/backtest_shocks.py
"""
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

from alpaca_client import get_historical_bars
from strategy import generate_signal, LONG_TERM_TREND_PERIOD, compute_opening_rvol_series
from executor import DAILY_LOSS_LIMIT, MAX_POSITION_PCT

SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]

SHOCK_WINDOWS = [
    ("2020 COVID Crash", "2020-02-15", "2020-04-30"),
    ("2022 Bear Selloff", "2022-01-01", "2022-10-15"),
    ("2024 Aug Flash Crash", "2024-08-01", "2024-08-10"),
]

STARTING_CASH = 100_000.0
MAX_RISK_PCT = 0.02
SIGNAL_EVERY_N_BARS = 3   # ~15 min at 5-min bars, matching the live cron cadence
LOOKBACK_BARS = 100       # matches bot.py's get_bars(..., limit=100)


def get_daily_bars_for_trend(symbol: str, start: str, end: str) -> pd.DataFrame:
    """Enough daily history (extended well before `start`) for the 200-day SMA filter."""
    extended_start = (datetime.strptime(start, "%Y-%m-%d") - timedelta(days=int(LONG_TERM_TREND_PERIOD * 1.6))).strftime("%Y-%m-%d")
    return get_historical_bars(symbol, "1Day", extended_start, end)


def simulate_symbol(symbol: str, bars: pd.DataFrame, daily_bars: pd.DataFrame = None,
                     stop_style: str = "fixed", use_rvol: bool = False, min_rvol: float = 1.0,
                     rvol_bars: pd.DataFrame = None, eod_flatten: bool = True) -> dict:
    """
    Bar-by-bar replay. Entry signal is only recomputed every
    SIGNAL_EVERY_N_BARS bars (matching the live 15-min cron), but stop/take
    hits are checked every bar — real Alpaca bracket orders live on the
    broker's side and can fill intrabar regardless of when the bot last ran.

    rvol_bars: optional longer intraday history (extending before `bars`) so the
        RVOL trailing baseline is valid from the first simulated day. Falls
        back to `bars` itself, which leaves the first ~lookback days unfiltered.
    """
    rvol_series = None
    if use_rvol:
        rvol_series = compute_opening_rvol_series(rvol_bars if rvol_bars is not None else bars)
    trades = []
    position = None  # {"entry", "stop", "take", "qty"}
    equity = STARTING_CASH
    day_start_equity = equity
    current_day = None
    halted_today = False

    for i in range(LOOKBACK_BARS, len(bars)):
        bar = bars.iloc[i]
        now = bars.index[i].to_pydatetime()
        bar_day = now.date()

        if bar_day != current_day:
            current_day = bar_day
            day_start_equity = equity
            halted_today = False

        # ── Manage an open position: did stop or take-profit hit intrabar? ──
        if position is not None:
            hi, lo = float(bar["high"]), float(bar["low"])
            exit_price = None
            if lo <= position["stop"]:
                exit_price = position["stop"]
            elif hi >= position["take"]:
                exit_price = position["take"]
            if exit_price is not None:
                pnl = (exit_price - position["entry"]) * position["qty"]
                equity += pnl
                trades.append({"time": now, "side": "EXIT", "price": exit_price, "pnl": pnl})
                position = None
            elif eod_flatten and (now.hour, now.minute) >= (15, 45):
                # End-of-day flatten, mirroring bot.py — never hold overnight
                exit_price = float(bar["close"])
                pnl = (exit_price - position["entry"]) * position["qty"]
                equity += pnl
                trades.append({"time": now, "side": "EOD_EXIT", "price": exit_price, "pnl": pnl})
                position = None

        # ── Daily loss guard, mirroring executor.check_daily_loss_limit ──
        if equity - day_start_equity <= DAILY_LOSS_LIMIT:
            halted_today = True

        # ── Entry / early-exit signal, only every N bars (cron cadence) ──
        if not halted_today and (i - LOOKBACK_BARS) % SIGNAL_EVERY_N_BARS == 0:
            window = bars.iloc[max(0, i - LOOKBACK_BARS):i + 1]
            # Only fully-completed prior days — no look-ahead into today's own bar
            daily_slice = daily_bars[daily_bars.index.date < bar_day] if daily_bars is not None else None
            rvol_val = None
            if rvol_series is not None:
                rvol_val = rvol_series.get(pd.Timestamp(bar_day))

            result = generate_signal(window, symbol=symbol, now=now, daily_bars=daily_slice,
                                      stop_style=stop_style, opening_rvol=rvol_val,
                                      min_rvol=min_rvol)
            price = float(bar["close"])

            if result["signal"] == "BUY" and position is None:
                stop_pct = result["stop_pct"]
                take_pct = result["take_pct"]
                risk_dollars = equity * MAX_RISK_PCT * result["size_mult"]
                qty = int(risk_dollars / (price * stop_pct))
                qty = min(qty, int(equity * MAX_POSITION_PCT / price))  # mirrors executor cap
                if qty < 1:
                    continue
                position = {
                    "entry": price, "qty": qty,
                    "stop": price * (1 - stop_pct),
                    "take": price * (1 + take_pct),
                }
                trades.append({
                    "time": now, "side": "BUY", "price": price, "qty": qty,
                    "reason": result["reason"],
                })

            elif result["signal"] == "SELL" and position is not None:
                pnl = (price - position["entry"]) * position["qty"]
                equity += pnl
                trades.append({"time": now, "side": "SELL_SIGNAL_EXIT", "price": price, "pnl": pnl})
                position = None

    # Mark any still-open position to the last close so the run reports fairly
    if position is not None:
        last_price = float(bars["close"].iloc[-1])
        pnl = (last_price - position["entry"]) * position["qty"]
        equity += pnl
        trades.append({"time": bars.index[-1], "side": "MARK_TO_CLOSE", "price": last_price, "pnl": pnl})

    max_dd = 0.0
    running_peak = STARTING_CASH
    running_equity = STARTING_CASH
    for t in trades:
        if "pnl" in t:
            running_equity += t["pnl"]
            running_peak = max(running_peak, running_equity)
            max_dd = min(max_dd, running_equity - running_peak)

    return {
        "symbol": symbol,
        "trades": trades,
        "final_equity": equity,
        "total_pnl": equity - STARTING_CASH,
        "max_drawdown": max_dd,
    }


# Named variants: label -> kwargs forwarded to simulate_symbol. Add more here
# rather than growing CLI flags — keeps every comparison run reproducible by name.
VARIANTS = {
    "fixed":         {"stop_style": "fixed", "use_rvol": False},
    # Diagnostic only: overnight holds are what the live bot used to do by
    # accident (day-TIF bracket legs expired, nothing flattened). Not a live option.
    "fixed+overnight": {"stop_style": "fixed", "use_rvol": False, "eod_flatten": False},
    "atr":           {"stop_style": "atr",   "use_rvol": False},
    "fixed+rvol0.8": {"stop_style": "fixed", "use_rvol": True, "min_rvol": 0.8},
    "fixed+rvol":    {"stop_style": "fixed", "use_rvol": True, "min_rvol": 1.0},
    "fixed+rvol1.2": {"stop_style": "fixed", "use_rvol": True, "min_rvol": 1.2},
    "atr+rvol":      {"stop_style": "atr",   "use_rvol": True, "min_rvol": 1.0},
}

# Calendar days of extra 5-min history fetched before each window so the
# 20-session RVOL baseline is populated from the first simulated day.
RVOL_WARMUP_CALENDAR_DAYS = 35


def run_window(label: str, start: str, end: str, variants: list) -> dict:
    """Fetch each symbol's data once, then run every variant on it. Returns
    {variant: {"label", "pnl", "trades", "max_dd"}}."""
    print(f"\n{'=' * 70}\n{label}  ({start} to {end})\n{'=' * 70}")
    totals = {v: {"label": label, "pnl": 0.0, "trades": 0, "max_dd": 0.0} for v in variants}
    need_rvol = any(VARIANTS[v].get("use_rvol") for v in variants)
    warmup_start = (datetime.strptime(start, "%Y-%m-%d")
                    - timedelta(days=RVOL_WARMUP_CALENDAR_DAYS)).strftime("%Y-%m-%d")

    for symbol in SYMBOLS:
        try:
            bars = get_historical_bars(symbol, "5Min", start, end)
        except Exception as e:
            print(f"  {symbol}: failed to fetch bars — {e}")
            continue

        if len(bars) < LOOKBACK_BARS + SIGNAL_EVERY_N_BARS:
            print(f"  {symbol}: not enough bars in window ({len(bars)}), skipping")
            continue

        try:
            daily_bars = get_daily_bars_for_trend(symbol, start, end)
        except Exception as e:
            print(f"  {symbol}: failed to fetch daily bars — {e}")
            daily_bars = None

        rvol_bars = None
        if need_rvol:
            try:
                rvol_bars = get_historical_bars(symbol, "5Min", warmup_start, end)
            except Exception as e:
                print(f"  {symbol}: failed to fetch RVOL warmup bars — {e}; using window only")

        for v in variants:
            result = simulate_symbol(symbol, bars, daily_bars=daily_bars,
                                     rvol_bars=rvol_bars, **VARIANTS[v])
            n_entries = sum(1 for t in result["trades"] if t["side"] == "BUY")
            wins = sum(1 for t in result["trades"] if t.get("pnl", 0) > 0)
            losses = sum(1 for t in result["trades"] if t.get("pnl", 0) < 0)
            print(
                f"  {symbol:6s} {v:14s} | trades={n_entries:3d} | win/loss={wins}/{losses} | "
                f"PnL=${result['total_pnl']:9,.2f} | max_dd=${result['max_drawdown']:9,.2f}"
            )
            t = totals[v]
            t["pnl"] += result["total_pnl"]
            t["trades"] += n_entries
            t["max_dd"] = min(t["max_dd"], result["max_drawdown"])

    print(f"  {'-' * 66}")
    for v in variants:
        print(f"  WINDOW TOTAL {v:14s} | trades={totals[v]['trades']:3d} | PnL=${totals[v]['pnl']:,.2f}")
    return totals


if __name__ == "__main__":
    variants = sys.argv[1:] or ["fixed"]
    for v in variants:
        if v not in VARIANTS:
            sys.exit(f"Unknown variant '{v}'. Choose from: {', '.join(VARIANTS)}")
    summary = {v: [] for v in variants}

    for label, start, end in SHOCK_WINDOWS:
        totals = run_window(label, start, end, variants)
        for v in variants:
            summary[v].append(totals[v])

    if len(variants) > 1:
        print(f"\n{'=' * 70}\nVARIANT COMPARISON\n{'=' * 70}")
        for label, _, _ in SHOCK_WINDOWS:
            print(f"\n{label}")
            for v in variants:
                row = next(r for r in summary[v] if r["label"] == label)
                print(f"  {v:14s} | trades={row['trades']:3d} | PnL=${row['pnl']:9,.2f} | max_dd=${row['max_dd']:9,.2f}")
        print(f"\nTOTAL")
        for v in variants:
            total_pnl = sum(r["pnl"] for r in summary[v])
            total_trades = sum(r["trades"] for r in summary[v])
            per_trade = total_pnl / total_trades if total_trades else 0.0
            print(f"  {v:14s} | trades={total_trades:3d} | PnL=${total_pnl:9,.2f} | per_trade=${per_trade:7,.2f}")
