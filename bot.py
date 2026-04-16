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
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    requests.post(url, data={"chat_id": chat_id, "text": message})

def is_market_open():
    return get_client().get_clock().is_open

def run():
    now = datetime.now(ET)
    print(f"Bot running at {now.strftime('%H:%M:%S ET')}")

    if not is_market_open():
        print("Market is closed. Exiting.")
        return

    if now.hour >= 15 and now.minute >= 45:
        print("Too close to market close. Exiting.")
        return

    trades_made = []

    for symbol in SYMBOLS:
        try:
            bars = get_bars(symbol, timeframe="5Min", limit=80)
            signal = generate_signal(bars)
            price = bars["close"].iloc[-1]
            print(f"{symbol} | Signal: {signal} | Price: ${price:.2f}")

            if signal in ["BUY", "SELL"]:
                submit_order(symbol, signal, price)
                trades_made.append(f"{signal} {symbol} @ ${price:.2f}")

        except Exception as e:
            error_msg = f"Error on {symbol}: {e}"
            print(error_msg)
            send_telegram(f"⚠️ Trading Bot Error\n{error_msg}")

    if trades_made:
        message = "🤖 Core Trading Bot\n\n"
        message += "\n".join(trades_made)
        message += f"\n\n⏰ {now.strftime('%I:%M %p ET')}"
        send_telegram(message)

if __name__ == "__main__":
    run()
