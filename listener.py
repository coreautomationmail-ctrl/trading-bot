# listener.py
# Telegram command handler. Runs on a short cron, drains any pending commands,
# answers them, acknowledges the updates, and exits — a few seconds of runtime.
# (It used to long-poll for 6h per run, ~24 runner-hours/day, which is what
# exhausted the private-repo Actions quota and is against Actions usage terms.)
#
# Supported commands (send in your Telegram chat):
#   /status    — portfolio value, cash, open positions, unrealized PnL
#   /positions — list all open positions with entry price and unrealized PnL
#   /summary   — today's trade summary (same as daily_summary.py output)

import os
import pytz
import requests
from datetime import datetime

ET = pytz.timezone("America/New_York")

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")


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


def get_updates(offset: int = 0) -> list:
    """Fetch pending updates without long-polling. Calling this with
    offset=last_id+1 is also how Telegram acknowledges them."""
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates",
            params={"offset": offset, "timeout": 0},
            timeout=15,
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
    "/summary — today's trade summary\n\n"
    "_Commands are picked up every 15 minutes._"
)


def handle_message(text: str):
    cmd = text.strip().split()[0].lower()
    if cmd in ("/help", "/start"):
        send(HELP_TEXT)
    elif cmd in COMMANDS:
        send(COMMANDS[cmd]())
    else:
        send(f"Unknown command: `{cmd}`\n\n{HELP_TEXT}")


# ── Entrypoint ────────────────────────────────────────────────────────────────

def run():
    print(f"[listener] run at {datetime.now(ET).strftime('%I:%M %p ET')}")
    updates = get_updates()
    if not updates:
        print("[listener] no pending commands")
        return

    for update in updates:
        message = update.get("message") or update.get("edited_message")
        if not message:
            continue
        # Only respond to the configured chat
        if str(message.get("chat", {}).get("id", "")) != TELEGRAM_CHAT_ID:
            continue
        text = message.get("text", "").strip()
        if not text:
            continue
        print(f"[listener] received: {text}")
        handle_message(text)

    # Acknowledge everything we just processed so the next run doesn't re-read it
    get_updates(offset=updates[-1]["update_id"] + 1)


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        print(f"[listener] crashed: {e}")
        send(f"❌ *Listener crashed*\n\n`{e}`")
        raise