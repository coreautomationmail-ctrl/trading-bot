# bot.py
from datetime import datetime, timedelta
import pytz
import requests
import os
import csv
import traceback
import statistics
from typing import List, Dict, Any, Optional

# Optional imports for charting and data handling
try:
    import matplotlib.pyplot as plt
    import pandas as pd
    CHARTING_AVAILABLE = True
except Exception:
    CHARTING_AVAILABLE = False

from alpaca_client import get_client, get_bars, get_account, get_fills  # adapt to your client API
from strategy import generate_signal
from executor import submit_order

# Config
SYMBOLS = ["AAPL", "TSLA", "SPY"]
ET = pytz.timezone("America/New_York")
TRADE_LOG_FILE = "trade_log.csv"
VOLATILITY_WINDOW = 20  # lookback for volatility (std dev of returns)
VOLATILITY_THRESHOLD = 0.02  # example threshold (2% std dev) — tune to your strategy

# Environment keys
TELEGRAM_TOKEN_KEY = "TELEGRAM_TOKEN"
TELEGRAM_CHAT_KEY = "TELEGRAM_CHAT_ID"

# -------------------------
# Messaging helpers
# -------------------------
def send_telegram(text: str):
    token = os.getenv(TELEGRAM_TOKEN_KEY)
    chat_id = os.getenv(TELEGRAM_CHAT_KEY)
    if not token or not chat_id:
        print("Telegram not configured; skipping message.")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        requests.post(url, data=payload, timeout=10)
    except Exception as e:
        print(f"Telegram send error: {e}")

def send_telegram_photo(photo_path: str, caption: Optional[str] = None):
    token = os.getenv(TELEGRAM_TOKEN_KEY)
    chat_id = os.getenv(TELEGRAM_CHAT_KEY)
    if not token or not chat_id:
        print("Telegram not configured; skipping photo.")
        return
    if not os.path.isfile(photo_path):
        print("Photo not found:", photo_path)
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        with open(photo_path, "rb") as f:
            files = {"photo": f}
            data = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
                data["parse_mode"] = "Markdown"
            requests.post(url, data=data, files=files, timeout=30)
    except Exception as e:
        print(f"Telegram photo error: {e}")

# -------------------------
# Utility: market & volatility
# -------------------------
def is_market_open() -> bool:
    try:
        return get_client().get_clock().is_open
    except Exception as e:
        print("Market status check failed:", e)
        return False

def compute_volatility(symbol: str, lookback: int = VOLATILITY_WINDOW) -> Optional[float]:
    """
    Compute simple volatility as std dev of log returns over lookback bars.
    Returns None on failure.
    """
    try:
        bars = get_bars(symbol, timeframe="5Min", limit=lookback + 1)
        closes = bars["close"].astype(float)
        returns = [ (closes.iloc[i] / closes.iloc[i-1]) - 1 for i in range(1, len(closes)) ]
        if len(returns) < 2:
            return None
        vol = statistics.stdev(returns)
        return vol
    except Exception as e:
        print(f"Volatility calc failed for {symbol}: {e}")
        return None

# -------------------------
# Trade logging & PnL
# -------------------------
def log_trades(trades: List[Dict[str, Any]], timestamp_str: str):
    if not trades:
        return
    header = ["timestamp", "symbol", "side", "price"]
    file_exists = os.path.isfile(TRADE_LOG_FILE)
    with open(TRADE_LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(header)
        for t in trades:
            writer.writerow([timestamp_str, t["symbol"], t["side"], f"{t['price']:.2f}"])

def estimate_pnl_from_account() -> float:
    """
    Try to estimate PnL using Alpaca account positions and unrealized PL.
    Returns 0.0 if not available.
    """
    try:
        account = get_account()
        # If your client returns positions with unrealized_pl, sum them
        positions = getattr(account, "positions", None)
        if positions:
            total_unrealized = 0.0
            for p in positions:
                # adapt to your client structure
                unreal = float(p.unrealized_pl) if hasattr(p, "unrealized_pl") else 0.0
                total_unrealized += unreal
            return total_unrealized
        # fallback: use fills or other endpoints if available
        return 0.0
    except Exception as e:
        print("PnL estimate failed:", e)
        return 0.0

# -------------------------
# Daily summary
# -------------------------
def daily_summary_for_date(date: datetime) -> str:
    """
    Read trade_log.csv and produce a summary for the given date (ET).
    """
    if not os.path.isfile(TRADE_LOG_FILE):
        return "No trade log found."

    df = None
    try:
        import pandas as pd
        df = pd.read_csv(TRADE_LOG_FILE, parse_dates=["timestamp"])
    except Exception:
        # fallback simple parser
        lines = []
        with open(TRADE_LOG_FILE, "r") as f:
            lines = f.readlines()
        # crude summary
        return f"Trade log exists with {len(lines)-1} entries."

    # convert to ET timezone naive comparison
    start = datetime(date.year, date.month, date.day, tzinfo=ET)
    end = start + timedelta(days=1)
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    mask = (df["timestamp"] >= start) & (df["timestamp"] < end)
    day_df = df.loc[mask]
    if day_df.empty:
        return "No trades for that date."

    total_trades = len(day_df)
    buys = len(day_df[day_df["side"] == "BUY"])
    sells = len(day_df[day_df["side"] == "SELL"])
    # PnL placeholder
    pnl = estimate_pnl_from_account()

    lines = [
        f"📅 *Daily Summary — {start.strftime('%Y-%m-%d')}*",
        f"Total trades: *{total_trades}* (BUY: *{buys}*, SELL: *{sells}*)",
        f"Estimated PnL: *${pnl:.2f}*",
    ]
    return "\n".join(lines)

# -------------------------
# Optional charting
# -------------------------
def make_price_chart(symbol: str, timeframe: str = "5Min", limit: int = 120) -> Optional[str]:
    """
    Create a small PNG chart for the symbol and return the file path.
    Requires matplotlib and pandas. Returns None if not available.
    """
    if not CHARTING_AVAILABLE:
        return None
    try:
        bars = get_bars(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame({
            "t": pd.to_datetime(bars["t"]),
            "close": bars["close"].astype(float)
        })
        df.set_index("t", inplace=True)
        path = f"{symbol}_chart.png"
        plt.figure(figsize=(6,3))
        plt.plot(df.index, df["close"], linewidth=1.2)
        plt.title(f"{symbol} recent")
        plt.tight_layout()
        plt.savefig(path, dpi=100)
        plt.close()
        return path
    except Exception as e:
        print("Chart generation failed:", e)
        return None

# -------------------------
# Main run logic
# -------------------------
def run():
    now = datetime.now(ET)
    timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
    pretty_time = now.strftime("%I:%M %p ET")

    # Notify start (also useful when run inside GitHub Actions)
    send_telegram(f"🟢 *Core Trading Bot Started*\n⏰ {pretty_time}")

    # Market open check
    if not is_market_open():
        send_telegram("🔴 *Market is closed. Bot exiting.*")
        return

    trades_made: List[Dict[str, Any]] = []
    errors: List[str] = []
    volatility_alerts: List[str] = []

    # Pre-check volatility across symbols
    for s in SYMBOLS:
        vol = compute_volatility(s)
        if vol is not None and vol > VOLATILITY_THRESHOLD:
            volatility_alerts.append(f"`{s}` volatility high: {vol:.4f}")

    if volatility_alerts:
        send_telegram("⚠️ *Volatility Alerts*\n\n" + "\n".join(volatility_alerts) + f"\n\n⏰ {pretty_time}")

    # Main symbol loop
    for symbol in SYMBOLS:
        try:
            bars = get_bars(symbol, timeframe="5Min", limit=80)
            signal = generate_signal(bars, now=now)
            price = float(bars["close"].iloc[-1])

            print(f"{symbol} | Signal: {signal} | Price: ${price:.2f}")

            if signal in ["BUY", "SELL"]:
                order = submit_order(symbol, signal, price)
                if order is not None:
                    trades_made.append({"symbol": symbol, "side": signal, "price": price})

        except Exception as e:
            tb = traceback.format_exc()
            print(f"Error for {symbol}: {e}\n{tb}")
            errors.append(f"{symbol}: {e}")

    # Log trades
    log_trades(trades_made, timestamp_str)

    # Send trade summary or no-trade notice
    if trades_made:
        est_pnl = estimate_pnl_from_account()
        lines = ["🤖 *Core Trading Bot — Trades Executed*\n"]
        for t in trades_made:
            lines.append(f"*{t['side']}* `{t['symbol']}` @ `${t['price']:.2f}`")
        lines.append(f"\n💰 *Est. PnL:* `${est_pnl:.2f}`")
        lines.append(f"⏰ {pretty_time}")
        send_telegram("\n".join(lines))

        # Optionally send charts for each traded symbol (if available)
        for t in trades_made:
            chart = make_price_chart(t["symbol"])
            if chart:
                send_telegram_photo(chart, caption=f"📈 `{t['symbol']}` recent price")
    else:
        send_telegram(f"📭 *No trades executed this run.*\n⏰ {pretty_time}")

    # Send errors if any
    if errors:
        send_telegram("⚠️ *Bot Errors Detected*\n\n" + "\n".join([f"`{e}`" for e in errors]) + f"\n\n⏰ {pretty_time}")

    # Finish notification
    send_telegram(f"🔵 *Core Trading Bot Finished*\n⏰ {pretty_time}")

# -------------------------
# Entrypoint with failure reporting
# -------------------------
if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        tb = traceback.format_exc()
        print("Unhandled exception:", e)
        print(tb)
        send_telegram(f"❌ *Bot crashed*\n\n`{e}`\n\nSee logs for details.")
        # Re-raise so CI knows the job failed (if running in Actions)
        raise
