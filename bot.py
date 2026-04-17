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

# Try to import alpaca_trade_api (common package). If not present, we'll fall back to your alpaca_client.
ALPACA_REST_AVAILABLE = False
try:
    import alpaca_trade_api as tradeapi
    ALPACA_REST_AVAILABLE = True
except Exception:
    ALPACA_REST_AVAILABLE = False

# Your local client functions (keep these as-is if you already have them)
try:
    from alpaca_client import get_client, get_bars, get_account, get_fills
except Exception:
    # If your alpaca_client isn't present, we'll still try to use alpaca_trade_api above
    get_client = None
    get_bars = None
    get_account = None
    get_fills = None

from strategy import generate_signal
from executor import submit_order

# Config
SYMBOLS = ["AAPL", "TSLA", "SPY"]
ET = pytz.timezone("America/New_York")
TRADE_LOG_FILE = "trade_log.csv"
VOLATILITY_WINDOW = 20
VOLATILITY_THRESHOLD = 0.02

# Env keys
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
# Market & volatility
# -------------------------
def is_market_open() -> bool:
    try:
        if get_client:
            return get_client().get_clock().is_open
        if ALPACA_REST_AVAILABLE:
            key = os.getenv("ALPACA_API_KEY")
            secret = os.getenv("ALPACA_SECRET_KEY")
            base = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
            api = tradeapi.REST(key, secret, base_url=base)
            return api.get_clock().is_open
    except Exception as e:
        print("Market status check failed:", e)
    return False

def compute_volatility(symbol: str, lookback: int = VOLATILITY_WINDOW) -> Optional[float]:
    try:
        if get_bars:
            bars = get_bars(symbol, timeframe="5Min", limit=lookback + 1)
            closes = bars["close"].astype(float)
        elif ALPACA_REST_AVAILABLE:
            key = os.getenv("ALPACA_API_KEY")
            secret = os.getenv("ALPACA_SECRET_KEY")
            base = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
            api = tradeapi.REST(key, secret, base_url=base)
            # Alpaca returns bars in a different structure; adapt to pandas
            barset = api.get_bars(symbol, tradeapi.TimeFrame(5, tradeapi.TimeFrameUnit.Minute), limit=lookback + 1)
            closes = [b.c for b in barset]
            import pandas as _pd
            closes = _pd.Series(closes)
        else:
            return None

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
    Uses alpaca_trade_api if available, otherwise tries get_account() from your alpaca_client.
    """
    try:
        # Preferred: alpaca_trade_api
        if ALPACA_REST_AVAILABLE:
            key = os.getenv("ALPACA_API_KEY")
            secret = os.getenv("ALPACA_SECRET_KEY")
            base = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
            api = tradeapi.REST(key, secret, base_url=base)
            positions = api.list_positions()
            total_unrealized = 0.0
            for p in positions:
                # alpaca_trade_api Position has unrealized_pl attribute
                unreal = float(getattr(p, "unrealized_pl", 0.0))
                total_unrealized += unreal
            return total_unrealized
        # Fallback: your alpaca_client.get_account() or get_account()
        if get_account:
            acct = get_account()
            positions = getattr(acct, "positions", None)
            if positions:
                total_unrealized = 0.0
                for p in positions:
                    if hasattr(p, "unrealized_pl"):
                        total_unrealized += float(p.unrealized_pl)
                    elif isinstance(p, dict) and "unrealized_pl" in p:
                        total_unrealized += float(p["unrealized_pl"])
                return total_unrealized
    except Exception as e:
        print("PnL estimate failed:", e)
    return 0.0

# -------------------------
# Daily summary
# -------------------------
def daily_summary_for_date(date: datetime) -> str:
    if not os.path.isfile(TRADE_LOG_FILE):
        return "No trade log found."
    try:
        import pandas as pd
        df = pd.read_csv(TRADE_LOG_FILE, parse_dates=["timestamp"])
    except Exception:
        lines = []
        with open(TRADE_LOG_FILE, "r") as f:
            lines = f.readlines()
        return f"Trade log exists with {len(lines)-1} entries."
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
    if not CHARTING_AVAILABLE:
        return None
    try:
        if get_bars:
            bars = get_bars(symbol, timeframe=timeframe, limit=limit)
            df = pd.DataFrame({"t": pd.to_datetime(bars["t"]), "close": bars["close"].astype(float)})
        elif ALPACA_REST_AVAILABLE:
            key = os.getenv("ALPACA_API_KEY")
            secret = os.getenv("ALPACA_SECRET_KEY")
            base = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
            api = tradeapi.REST(key, secret, base_url=base)
            barset = api.get_bars(symbol, tradeapi.TimeFrame(5, tradeapi.TimeFrameUnit.Minute), limit=limit)
            df = pd.DataFrame({"t": [b.t for b in barset], "close": [b.c for b in barset]})
            df["t"] = pd.to_datetime(df["t"])
        else:
            return None
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
    send_telegram(f"🟢 *Core Trading Bot Started*\n⏰ {pretty_time}")
    if not is_market_open():
        send_telegram("🔴 *Market is closed. Bot exiting.*")
        return
    trades_made: List[Dict[str, Any]] = []
    errors: List[str] = []
    volatility_alerts: List[str] = []
    for s in SYMBOLS:
        vol = compute_volatility(s)
        if vol is not None and vol > VOLATILITY_THRESHOLD:
            volatility_alerts.append(f"`{s}` volatility high: {vol:.4f}")
    if volatility_alerts:
        send_telegram("⚠️ *Volatility Alerts*\n\n" + "\n".join(volatility_alerts) + f"\n\n⏰ {pretty_time}")
    for symbol in SYMBOLS:
        try:
            # Prefer your get_bars if present
            if get_bars:
                bars = get_bars(symbol, timeframe="5Min", limit=80)
            elif ALPACA_REST_AVAILABLE:
                key = os.getenv("ALPACA_API_KEY")
                secret = os.getenv("ALPACA_SECRET_KEY")
                base = os.getenv("ALPACA_BASE_URL", "https://api.alpaca.markets")
                api = tradeapi.REST(key, secret, base_url=base)
                barset = api.get_bars(symbol, tradeapi.TimeFrame(5, tradeapi.TimeFrameUnit.Minute), limit=80)
                import pandas as _pd
                bars = _pd.DataFrame({"t":[b.t for b in barset],"close":[b.c for b in barset]})
            else:
                raise RuntimeError("No bars provider available")
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
    log_trades(trades_made, timestamp_str)
    if trades_made:
        est_pnl = estimate_pnl_from_account()
        lines = ["🤖 *Core Trading Bot — Trades Executed*\n"]
        for t in trades_made:
            lines.append(f"*{t['side']}* `{t['symbol']}` @ `${t['price']:.2f}`")
        lines.append(f"\n💰 *Est. PnL:* `${est_pnl:.2f}`")
        lines.append(f"⏰ {pretty_time}")
        send_telegram("\n".join(lines))
        for t in trades_made:
            chart = make_price_chart(t["symbol"])
            if chart:
                send_telegram_photo(chart, caption=f"📈 `{t['symbol']}` recent price")
    else:
        send_telegram(f"📭 *No trades executed this run.*\n⏰ {pretty_time}")
    if errors:
        send_telegram("⚠️ *Bot Errors Detected*\n\n" + "\n".join([f"`{e}`" for e in errors]) + f"\n\n⏰ {pretty_time}")
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
        raise
