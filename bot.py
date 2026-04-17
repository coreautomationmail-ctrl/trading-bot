from datetime import datetime
import pytz
import requests
import os
from alpaca_client import get_client, get_bars
from strategy import generate_signal
from executor import submit_order

SYMBOLS = ["AAPL", "TSLA", "SPY"]
ET = pytz.timezone("America/New_York")

def send_telegram(message):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(url, data={"chat_id": chat_id, "text": message}, timeout=10)
    except Exception as e:
        print(f"Telegram error: {e}")

def is_market_open():
    try:
        return get_client().get_clock().is_open
    except Exception as e:
        print(f"Could not check market status: {e}")
        return False

def run():
    now = datetime.now(ET)
    print(f"Bot running at {now.strftime('%H:%M:%S ET')}")

    if not is_market_open():
        print("Market is closed. Exiting.")
        return

    trades_made = []
    errors = []

    for symbol in SYMBOLS:
        try:
            bars = get_bars(symbol, timeframe="5Min", limit=80)
            signal = generate_signal(bars, now=now)
            price = bars["close"].iloc[-1]
            print(f"{symbol} | Signal: {signal} | Price: ${price:.2f}")

            if signal in ["BUY", "SELL"]:
                order = submit_order(symbol, signal, price)
                if order is not None:
                    trades_made.append(f"{signal} {symbol} @ ${price:.2f}")

        except Exception as e:
            msg = f"Error on {symbol}: {e}"
            print(msg)
            errors.append(msg)

    # Send Telegram alerts
    if trades_made:
        message = "🤖 Core Trading Bot\n\n"
        message += "\n".join(trades_made)
        message += f"\n\n⏰ {now.strftime('%I:%M %p ET')}"
        send_telegram(message)

    if errors:
        error_msg = "⚠️ Bot Errors\n\n" + "\n".join(errors)
        send_telegram(error_msg)

if __name__ == "__main__":
    run()
