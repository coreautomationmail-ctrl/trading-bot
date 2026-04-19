# listener.py
# Long-running Telegram command listener.
# Deployed via GitHub Actions (restarts every 6h to stay within runner limits).
#
# Supported commands (send in your Telegram chat):
#   /status   — portfolio value, cash, open positions, unrealized PnL
#   /positions — list all open positions with entry price and unrealized PnL
#   /summary  — today's trade summary (same as daily_summary.py output)
#   /stop     — gracefully exit the listener (useful before deploying changes)

import os
import time
import pytz
import requests
from datetime import datetime

ET = pytz.timezone("America/New_York")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
POLL_INTERVAL = 3  # seconds between Telegram long-poll calls
TIMEOUT = 30       # long-poll timeout in seconds


# ── Telegram helpers ──────────────────────────────────────────────────────────

def send(text: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram not configured:", text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[telegram] send error: {e}")


def get_updates(offset: int) -> list:
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": TIMEOUT},
            timeout=TIMEOUT + 5,
        )
        data = resp.json()
        if data.get("ok"):
            return data.get("result", [])
    except Exception as e:
        print(f"[telegram] poll error: {e}")
    return []


# ── Alpaca helpers ────────────────────────────────────────────────────────────

def _api():
    import alpaca_trade_api as tradeapi
    return tradeapi.REST(
        os.getenv("ALPACA_API_KEY"),
        os.getenv("ALPACA_SECRET_KEY"),
        os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        api_version="v2",
    )


def cmd_status() -> str:
    try:
        api = _api()
        account = api.get_account()
        positions = api.list_positions()
        unrealized = sum(float(getattr(p, "unrealized_pl", 0)) for p in positions)
        pnl_emoji = "🟢" if unrealized >= 0 else "🔴"
        return (
            f"📊 *Account Status*\n\n"
            f"💼 Portfolio: `${float(account.portfolio_value):,.2f}`\n"
            f"💵 Cash: `${float(account.cash):,.2f}`\n"
            f"📋 Open positions: `{len(positions)}`\n"
            f"{pnl_emoji} Unrealized PnL: `${unrealized:+,.2f}`\n"
            f"⏰ {datetime.now(ET).strftime('%I:%M %p ET')}"
        )
    except Exception as e:
        return f"❌ Status fetch failed: `{e}`"


def cmd_positions() -> str:
    try:
        api = _api()
        positions = api.list_positions()
        if not positions:
            return "📭 *No open positions.*"
        lines = ["📋 *Open Positions*\n"]
        for p in positions:
            pnl = float(getattr(p, "unrealized_pl", 0))
            pnl_emoji = "🟢" if pnl >= 0 else "🔴"
            lines.append(
                f"{pnl_emoji} `{p.symbol}` — {int(float(p.qty))} shares "
                f"@ ${float(p.avg_entry_price):.2f} | PnL: `${pnl:+.2f}`"
            )
        return "\n".join(lines)
    except Exception as e:
        return f"❌ Positions fetch failed: `{e}`"


def cmd_summary() -> str:
    try:
        from daily_summary import build_summary
        return build_summary(datetime.now(ET))
    except Exception as e:
        return f"❌ Summary failed: `{e}`"


# ── Command dispatch ──────────────────────────────────────────────────────────

COMMANDS = {
    "/status": cmd_status,
    "/positions": cmd_positions,
    "/summary": cmd_summary,
}

HELP_TEXT = (
    "🤖 *Core Trading Bot — Commands*\n\n"
    "/status — portfolio snapshot\n"
    "/positions — open positions\n"
    "/summary — today's trade summary\n"
    "/stop — stop this listener"
)


def handle_message(text: str) -> bool:
    """Process a command. Returns True if listener should stop."""
    cmd = text.strip().split()[0].lower()

    if cmd == "/stop":
        send("🛑 Listener stopping. Restart via GitHub Actions when ready.")
        return True

    if cmd in ("/help", "/start"):
        send(HELP_TEXT)
        return False

    if cmd in COMMANDS:
        send(COMMANDS[cmd]())
    else:
        send(f"Unknown command: `{cmd}`\n\n{HELP_TEXT}")

    return False


# ── Main loop ─────────────────────────────────────────────────────────────────

def run():
    print(f"[listener] started at {datetime.now(ET).strftime('%I:%M %p ET')}")
    send(f"👂 *Listener online* — {datetime.now(ET).strftime('%I:%M %p ET')}\nSend /help for commands.")

    offset = 0
    while True:
        updates = get_updates(offset)
        for update in updates:
            offset = update["update_id"] + 1
            message = update.get("message") or update.get("edited_message")
            if not message:
                continue

            # Only respond to the configured chat
            chat_id = str(message.get("chat", {}).get("id", ""))
            if chat_id != TELEGRAM_CHAT_ID:
                continue

            text = message.get("text", "").strip()
            if not text:
                continue

            print(f"[listener] received: {text}")
            should_stop = handle_message(text)
            if should_stop:
                return

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        run()
    except KeyboardInterrupt:
        print("[listener] stopped by keyboard interrupt")
    except Exception as e:
        print(f"[listener] crashed: {e}")
        send(f"❌ *Listener crashed*\n\n`{e}`")
        raise