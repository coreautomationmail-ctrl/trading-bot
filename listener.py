import os
import time
import requests
from datetime import datetime
import pytz

from bot import (
    daily_summary_for_date,
    compute_volatility,
    make_price_chart,
    estimate_pnl_from_account,
)
from strategy import generate_signal
from alpaca_client import get_bars

ET = pytz.timezone("America/New_York")

TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
BASE_URL = f"https://api.telegram.org/bot{TOKEN}"

LAST_UPDATE_ID = None

def send_message(text):
    requests.post(f"{BASE_URL}/sendMessage", data={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "Markdown"
    })

def send_photo(path, caption=None):
    with open(path, "rb") as f:
        requests.post(f"{BASE_URL}/sendPhoto", data={
            "chat_id": CHAT_ID,
            "caption": caption or "",
            "parse_mode": "Markdown"
        }, files={"photo": f})

def handle_command(cmd):
    parts = cmd.split()

    if cmd == "/status":
        now = datetime.now(ET).strftime("%I:%M %p ET")
        pnl = estimate_pnl_from_account()
        send_message(f"🟦 *Bot Status*\n\nTime: {now}\nPnL: `${pnl:.2f}`")

    elif cmd == "/summary":
        today = datetime.now(ET)
        summary = daily_summary_for_date(today)
        send_message(summary)

    elif parts[0] == "/chart" and len(parts) == 2:
        symbol = parts[1].upper()
        chart = make_price_chart(symbol)
        if chart:
            send_photo(chart, caption=f"📈 `{symbol}` chart")
        else:
            send_message("Chart unavailable.")

    elif parts[0] == "/signal" and len(parts) == 2:
        symbol = parts[1].upper()
        bars = get_bars(symbol, timeframe="5Min", limit=80)
        signal = generate_signal(bars, now=datetime.now(ET))
        send_message(f"📊 *Signal for {symbol}:* `{signal}`")

    elif cmd == "/vol":
        symbols = ["AAPL", "TSLA", "SPY"]
        lines = []
        for s in symbols:
            vol = compute_volatility(s)
            if vol:
                lines.append(f"`{s}` → {vol:.4f}")
        send_message("📉 *Volatility*\n\n" + "\n".join(lines))

    elif cmd == "/log":
        if not os.path.isfile("trade_log.csv"):
            send_message("No trade log found.")
            return
        with open("trade_log.csv") as f:
            lines = f.readlines()[-10:]
        send_message("📝 *Last 10 Trades*\n\n" + "".join(lines))

    else:
        send_message("Unknown command.")

def poll():
    global LAST_UPDATE_ID

    while True:
        try:
            resp = requests.get(f"{BASE_URL}/getUpdates", params={
                "offset": LAST_UPDATE_ID,
                "timeout": 10
            }).json()

            for update in resp.get("result", []):
                LAST_UPDATE_ID = update["update_id"] + 1

                if "message" in update:
                    text = update["message"].get("text", "")
                    if text.startswith("/"):
                        handle_command(text)

        except Exception as e:
            print("Listener error:", e)

        time.sleep(2)

if __name__ == "__main__":
    poll()
