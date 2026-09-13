# bot.py
from datetime import datetime, timedelta
import pytz
import traceback
import statistics
from typing import List, Dict, Any, Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from alpaca_client import get_client, get_bars
from strategy import generate_signal, LONG_TERM_TREND_PERIOD
from executor import submit_order, check_daily_loss_limit, flatten_intraday
from notifications import send_telegram, send_telegram_photo
from safety import enforce_paper_mode

# ── Config ────────────────────────────────────────────────────────────────────

SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]
ET = pytz.timezone("America/New_York")
VOLATILITY_THRESHOLD = 0.02
FLATTEN_BEFORE_CLOSE = timedelta(minutes=15)

# Telegram policy: one heartbeat on the first eligible run of the day, one
# message per run that trades or errors, one flatten report before the close.
# Everything else goes to the Actions log; daily_summary.py digests it.
HEARTBEAT_WINDOW = ((9, 40), (9, 58))


# ── Market ────────────────────────────────────────────────────────────────────

def get_market_clock():
    """Returns (is_open, next_close as tz-aware ET datetime) or (False, None)."""
    try:
        clock = get_client().get_clock()
        next_close = clock.next_close
        if hasattr(next_close, "to_pydatetime"):
            next_close = next_close.to_pydatetime()
        return bool(clock.is_open), next_close.astimezone(ET)
    except Exception as e:
        print(f"[market] status check failed: {e}")
        return False, None


# ── Volatility scan ───────────────────────────────────────────────────────────

def compute_volatility(symbol: str, lookback: int = 20) -> Optional[float]:
    try:
        bars = get_bars(symbol, timeframe="5Min", limit=lookback + 1)
        closes = bars["close"].astype(float)
        returns = [(closes.iloc[i] / closes.iloc[i - 1]) - 1 for i in range(1, len(closes))]
        return statistics.stdev(returns) if len(returns) >= 2 else None
    except Exception as e:
        print(f"[volatility] {symbol}: {e}")
        return None


# ── PnL ───────────────────────────────────────────────────────────────────────

def estimate_pnl() -> float:
    try:
        positions = get_client().list_positions()
        return sum(float(getattr(p, "unrealized_pl", 0)) for p in positions)
    except Exception as e:
        print(f"[pnl] {e}")
        return 0.0


# ── Charting ──────────────────────────────────────────────────────────────────

def make_price_chart(symbol: str, timeframe: str = "5Min", limit: int = 120) -> Optional[str]:
    try:
        bars = get_bars(symbol, timeframe=timeframe, limit=limit)
        fig, ax = plt.subplots(figsize=(6, 3))
        ax.plot(bars.index, bars["close"].astype(float), linewidth=1.2)
        ax.set_title(f"{symbol} — last {limit} bars ({timeframe})")
        fig.autofmt_xdate()
        plt.tight_layout()
        path = f"{symbol}_chart.png"
        fig.savefig(path, dpi=100)
        plt.close(fig)
        return path
    except Exception as e:
        print(f"[chart] {symbol}: {e}")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    now = datetime.now(ET)
    pretty_time = now.strftime("%I:%M %p ET")
    print(f"[bot] run at {pretty_time}")

    enforce_paper_mode()

    is_open, next_close = get_market_clock()
    if not is_open:
        print("[bot] market closed, exiting")
        return

    # Last run before the close (early-close days included): flatten today's
    # ORB trades so nothing is held overnight.
    if now >= next_close - FLATTEN_BEFORE_CLOSE:
        closed = flatten_intraday()
        if closed:
            lines = ["🏁 *End-of-Day Flatten*\n"] + [
                f"SELL `{t['symbol']}` x{t['qty']} @ `${t['price']:.2f}`" for t in closed
            ] + [f"\n💰 *Unrealized PnL:* `${estimate_pnl():.2f}`", f"⏰ {pretty_time}"]
            send_telegram("\n".join(lines))
        else:
            print("[bot] EOD: no open ORB trades to flatten")
        return

    # Daily loss guard — alert and exit if limit hit
    if check_daily_loss_limit():
        pnl = estimate_pnl()
        send_telegram(
            f"⛔ *Daily Loss Limit Hit*\n\n"
            f"Unrealized PnL: `${pnl:.2f}`\n"
            f"Bot will not trade further today.\n"
            f"⏰ {pretty_time}"
        )
        return

    # Volatility scan — logged every run, only messaged in the heartbeat
    vol_alerts: List[str] = []
    for s in SYMBOLS:
        vol = compute_volatility(s)
        if vol is not None and vol > VOLATILITY_THRESHOLD:
            vol_alerts.append(f"`{s}` {vol:.4f}")
    if vol_alerts:
        print("[bot] high volatility: " + ", ".join(vol_alerts))

    if HEARTBEAT_WINDOW[0] <= (now.hour, now.minute) < HEARTBEAT_WINDOW[1]:
        msg = f"🟢 *Bot online* — {now:%a %b %d}, close {next_close:%I:%M %p} ET"
        if vol_alerts:
            msg += "\n⚠️ High volatility: " + ", ".join(vol_alerts)
        send_telegram(msg)

    # Signal loop
    trades_made: List[Dict[str, Any]] = []
    errors: List[str] = []
    reasons: Dict[str, str] = {}

    for symbol in SYMBOLS:
        try:
            bars = get_bars(symbol, timeframe="5Min", limit=100)
            daily_bars = get_bars(symbol, timeframe="1Day", limit=LONG_TERM_TREND_PERIOD + 10)
            result = generate_signal(bars, symbol=symbol, now=now, daily_bars=daily_bars)
            signal = result["signal"]
            price = float(bars["close"].iloc[-1])
            reasons[symbol] = result["reason"]

            # daily_summary.py parses this exact line format from the Actions log
            print(f"{symbol} | {signal} | ${price:.2f} | {result['reason']}")

            if signal in ("BUY", "SELL"):
                order = submit_order(
                    symbol=symbol,
                    signal=signal,
                    price=price,
                    size_mult=result["size_mult"],
                    stop_pct=result["stop_pct"],
                    take_pct=result["take_pct"],
                )
                if order is not None:
                    trades_made.append(order)

        except Exception as e:
            tb = traceback.format_exc()
            print(f"[bot] {symbol} error: {e}\n{tb}")
            errors.append(f"{symbol}: {e}")

    if trades_made:
        pnl = estimate_pnl()
        lines = [f"🤖 *Trades — {pretty_time}*\n"]
        for t in trades_made:
            lines.append(f"*{t['side']}* `{t['symbol']}` x{t.get('qty','')} @ `${t['price']:.2f}`"
                         f"\n   _{reasons.get(t['symbol'], '')}_")
        lines.append(f"\n💰 *Unrealized PnL:* `${pnl:.2f}`")
        send_telegram("\n".join(lines))
        for t in trades_made:
            chart = make_price_chart(t["symbol"])
            if chart:
                send_telegram_photo(chart, caption=f"📈 `{t['symbol']}`")

    if errors:
        send_telegram(f"⚠️ *Errors — {pretty_time}*\n" + "\n".join(f"`{e}`" for e in errors))


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        tb = traceback.format_exc()
        print("Unhandled exception:", e, tb)
        send_telegram(f"❌ *Bot crashed*\n\n`{e}`")
        raise
