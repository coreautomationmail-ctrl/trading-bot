# position_manager.py
"""
Weekly long-term/swing position manager.

Separate from bot.py's intraday ORB strategy: no stop-loss/take-profit
brackets, just a 50/200-day SMA trend filter with weekly dollar-cost
averaging into names in an uptrend, and a full exit if the trend breaks.
Rule-based only — no LLM or external API beyond Alpaca.

Runs on Alpaca paper trading only until LIVE_TRADING_APPROVED is set — see
safety.py for the same go-live lock bot.py uses.
"""
import traceback
from datetime import datetime

import pytz

from alpaca_client import get_client, get_bars
from notifications import send_telegram
from safety import enforce_paper_mode

ET = pytz.timezone("America/New_York")

WATCHLIST = ["VOO", "QQQ", "AAPL", "MSFT", "JPM", "KO"]

SMA_FAST = 50
SMA_SLOW = 200

# Dollars added per ticker per week while it's in an uptrend, and the position
# value at which DCA stops (prevents one ticker from eating the whole sleeve
# during a long bull run). Both are placeholders — tune before going live.
WEEKLY_DCA_PER_TICKER = 250.0
MAX_POSITION_VALUE_PER_TICKER = 5000.0


def get_trend(symbol: str) -> dict:
    """
    Golden-cross regime filter on daily bars:
      BULLISH: 50-day SMA > 200-day SMA and price above the 200-day SMA
      BEARISH: 50-day SMA < 200-day SMA
      NEUTRAL: anything else (e.g. right at the cross)
    """
    bars = get_bars(symbol, timeframe="1Day", limit=SMA_SLOW + 10)
    closes = bars["close"].astype(float)
    if len(closes) < SMA_SLOW:
        return {"trend": "UNKNOWN", "price": float(closes.iloc[-1])}

    sma_fast = float(closes.rolling(SMA_FAST).mean().iloc[-1])
    sma_slow = float(closes.rolling(SMA_SLOW).mean().iloc[-1])
    price = float(closes.iloc[-1])

    if sma_fast > sma_slow and price > sma_slow:
        trend = "BULLISH"
    elif sma_fast < sma_slow:
        trend = "BEARISH"
    else:
        trend = "NEUTRAL"

    return {"trend": trend, "price": price, "sma_fast": sma_fast, "sma_slow": sma_slow}


def get_position_qty(api, symbol: str) -> float:
    for p in api.list_positions():
        if p.symbol == symbol:
            return float(p.qty)
    return 0.0


def run():
    now = datetime.now(ET)
    pretty_time = now.strftime("%Y-%m-%d %I:%M %p ET")

    send_telegram(f"🟢 *Long-Term Manager Started*\n⏰ {pretty_time}")
    enforce_paper_mode()

    api = get_client()
    lines = [f"📊 *Weekly Long-Term Review — {pretty_time}*\n"]
    errors = []

    for symbol in WATCHLIST:
        try:
            trend = get_trend(symbol)
            qty = get_position_qty(api, symbol)

            if trend["trend"] == "BULLISH":
                position_value = qty * trend["price"]
                if position_value >= MAX_POSITION_VALUE_PER_TICKER:
                    lines.append(
                        f"⏸ `{symbol}` BULLISH — at position cap (${position_value:,.0f}), no buy"
                    )
                else:
                    shares = max(1, int(WEEKLY_DCA_PER_TICKER / trend["price"]))
                    api.submit_order(
                        symbol=symbol, qty=shares, side="buy",
                        type="market", time_in_force="day",
                    )
                    lines.append(
                        f"🟢 `{symbol}` BULLISH — bought {shares} shares @ ~${trend['price']:.2f}"
                    )

            elif trend["trend"] == "BEARISH" and qty > 0:
                api.close_position(symbol)
                lines.append(f"🔴 `{symbol}` BEARISH — closed {qty:g} shares, trend broke")

            elif trend["trend"] == "BEARISH":
                lines.append(f"⚪ `{symbol}` BEARISH — staying out")

            else:
                lines.append(f"⏸ `{symbol}` {trend['trend']} — holding, no action")

        except Exception as e:
            tb = traceback.format_exc()
            print(f"[position_manager] {symbol} error: {e}\n{tb}")
            errors.append(f"{symbol}: {e}")

    send_telegram("\n".join(lines))
    if errors:
        send_telegram("⚠️ *Errors*\n" + "\n".join(f"`{e}`" for e in errors))

    send_telegram(f"🔵 *Long-Term Manager Finished*\n⏰ {pretty_time}")


if __name__ == "__main__":
    try:
        run()
    except SystemExit:
        raise
    except Exception as e:
        tb = traceback.format_exc()
        print("Unhandled exception:", e, tb)
        send_telegram(f"❌ *Long-term manager crashed*\n\n`{e}`")
        raise
