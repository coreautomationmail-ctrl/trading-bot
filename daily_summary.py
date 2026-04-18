# daily_summary.py
# Runs after market close (4:05 PM ET via GitHub Actions cron).
# Reads trade_log.csv, fetches account state, and sends a Telegram summary.

from datetime import datetime, timedelta
import os
import csv
import pytz
import requests

ET = pytz.timezone("America/New_York")
TRADE_LOG_FILE = "trade_log.csv"


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured; printing summary instead:\n", text)
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        requests.post(
            url,
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[telegram] send error: {e}")


# ── Account / PnL ─────────────────────────────────────────────────────────────

def get_account_summary() -> dict:
    """Fetch portfolio value, cash, and unrealized PnL from Alpaca."""
    try:
        import alpaca_trade_api as tradeapi
        api = tradeapi.REST(
            os.getenv("ALPACA_API_KEY"),
            os.getenv("ALPACA_SECRET_KEY"),
            os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
            api_version="v2",
        )
        account = api.get_account()
        positions = api.list_positions()
        unrealized_pnl = sum(float(getattr(p, "unrealized_pl", 0)) for p in positions)
        return {
            "portfolio_value": float(account.portfolio_value),
            "cash": float(account.cash),
            "unrealized_pnl": unrealized_pnl,
            "positions": len(positions),
        }
    except Exception as e:
        print(f"[summary] account fetch failed: {e}")
        return {}


# ── Trade log ─────────────────────────────────────────────────────────────────

def read_todays_trades(today: datetime) -> list[dict]:
    """Return rows from trade_log.csv that belong to today (ET)."""
    if not os.path.isfile(TRADE_LOG_FILE):
        return []

    start = datetime(today.year, today.month, today.day, tzinfo=ET)
    end = start + timedelta(days=1)
    rows = []

    with open(TRADE_LOG_FILE, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                # Timestamps are stored as naive strings; treat as ET
                ts = datetime.strptime(row["timestamp"], "%Y-%m-%d %H:%M:%S")
                ts = ET.localize(ts)
                if start <= ts < end:
                    rows.append(row)
            except Exception:
                continue
    return rows


# ── Summary builder ───────────────────────────────────────────────────────────

def build_summary(today: datetime) -> str:
    date_str = today.strftime("%A, %b %d %Y")
    trades = read_todays_trades(today)
    account = get_account_summary()

    buys = [t for t in trades if t.get("side") == "BUY"]
    sells = [t for t in trades if t.get("side") == "SELL"]

    lines = [f"📅 *Daily Summary — {date_str}*\n"]

    # Trade counts
    if trades:
        lines.append(f"Trades today: *{len(trades)}*  (🟢 BUY: *{len(buys)}*  🔴 SELL: *{len(sells)}*)")

        # Per-symbol breakdown
        symbols = sorted(set(t["symbol"] for t in trades))
        for sym in symbols:
            sym_trades = [t for t in trades if t["symbol"] == sym]
            sym_buys = sum(1 for t in sym_trades if t["side"] == "BUY")
            sym_sells = sum(1 for t in sym_trades if t["side"] == "SELL")
            lines.append(f"  • `{sym}` — {sym_buys}B / {sym_sells}S")
    else:
        lines.append("No trades executed today.")

    # Account snapshot
    if account:
        lines.append("")
        lines.append(f"💼 *Portfolio Value:* `${account['portfolio_value']:,.2f}`")
        lines.append(f"💵 *Cash:* `${account['cash']:,.2f}`")
        lines.append(f"📊 *Open Positions:* `{account['positions']}`")
        pnl = account["unrealized_pnl"]
        pnl_emoji = "🟢" if pnl >= 0 else "🔴"
        lines.append(f"{pnl_emoji} *Unrealized PnL:* `${pnl:+,.2f}`")
    else:
        lines.append("\n⚠️ Could not fetch account data.")

    lines.append(f"\n⏰ Generated at {datetime.now(ET).strftime('%I:%M %p ET')}")
    return "\n".join(lines)


# ── Entrypoint ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    today = datetime.now(ET)
    summary = build_summary(today)
    print(summary)
    send_telegram(summary)
