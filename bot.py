# bot.py
from datetime import datetime, timedelta
import pytz
import requests
import os
import csv
import traceback
import statistics
import subprocess
from typing import List, Dict, Any, Optional

import pandas as pd
import matplotlib
matplotlib.use("Agg")  # headless — no display needed in CI
import matplotlib.pyplot as plt
import alpaca_trade_api as tradeapi

from alpaca_client import get_client, get_bars, get_account
from strategy import generate_signal
from executor import submit_order

# ── Config ────────────────────────────────────────────────────────────────────

SYMBOLS = ["AAPL", "TSLA", "SPY"]
ET = pytz.timezone("America/New_York")
TRADE_LOG_FILE = "trade_log.csv"
VOLATILITY_WINDOW = 20
VOLATILITY_THRESHOLD = 0.02

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured; skipping message.")
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


def send_telegram_photo(photo_path: str, caption: Optional[str] = None):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured; skipping photo.")
        return
    if not os.path.isfile(photo_path):
        print(f"[telegram] photo not found: {photo_path}")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendPhoto"
        with open(photo_path, "rb") as f:
            data: Dict[str, Any] = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
                data["parse_mode"] = "Markdown"
            requests.post(url, data=data, files={"photo": f}, timeout=30)
    except Exception as e:
        print(f"[telegram] photo error: {e}")


# ── Market status ─────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    try:
        return get_client().get_clock().is_open
    except Exception as e:
        print(f"[market] status check failed: {e}")
        return False


# ── Volatility ────────────────────────────────────────────────────────────────

def compute_volatility(symbol: str, lookback: int = VOLATILITY_WINDOW) -> Optional[float]:
    try:
        bars = get_bars(symbol, timeframe="5Min", limit=lookback + 1)
        closes = bars["close"].astype(float)
        returns = [(closes.iloc[i] / closes.iloc[i - 1]) - 1 for i in range(1, len(closes))]
        if len(returns) < 2:
            return None
        return statistics.stdev(returns)
    except Exception as e:
        print(f"[volatility] calc failed for {symbol}: {e}")
        return None


# ── Trade logging ─────────────────────────────────────────────────────────────

def log_trades(trades: List[Dict[str, Any]], timestamp_str: str):
    """Append trades to CSV and commit the file back to the repo so it persists across runs."""
    if not trades:
        return

    header = ["timestamp", "symbol", "side", "price", "qty"]
    file_exists = os.path.isfile(TRADE_LOG_FILE)

    with open(TRADE_LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(header)
        for t in trades:
            writer.writerow([
                timestamp_str,
                t["symbol"],
                t["side"],
                f"{t['price']:.2f}",
                t.get("qty", ""),
            ])

    # Persist across GitHub Actions runs by committing back to the repo.
    # Requires the workflow to checkout with a token that has write access,
    # or use a PAT stored in secrets and passed as GIT_TOKEN.
    try:
        subprocess.run(["git", "config", "user.email", "bot@core-automation-ai"], check=True)
        subprocess.run(["git", "config", "user.name", "Core Trading Bot"], check=True)
        subprocess.run(["git", "add", TRADE_LOG_FILE], check=True)
        subprocess.run(
            ["git", "commit", "-m", f"chore: trade log update {timestamp_str}"],
            check=True,
        )
        subprocess.run(["git", "push"], check=True)
        print(f"[log] trade_log.csv committed and pushed")
    except subprocess.CalledProcessError as e:
        print(f"[log] git commit/push failed (non-fatal): {e}")


# ── PnL ───────────────────────────────────────────────────────────────────────

def estimate_pnl_from_account() -> float:
    try:
        api = get_client()
        positions = api.list_positions()
        return sum(float(getattr(p, "unrealized_pl", 0.0)) for p in positions)
    except Exception as e:
        print(f"[pnl] estimate failed: {e}")
        return 0.0


# ── Charting ──────────────────────────────────────────────────────────────────

def make_price_chart(symbol: str, timeframe: str = "5Min", limit: int = 120) -> Optional[str]:
    try:
        bars = get_bars(symbol, timeframe=timeframe, limit=limit)
        # bars.index is already a DatetimeIndex in ET after get_bars()
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
        print(f"[chart] generation failed for {symbol}: {e}")
        return None


# ── Main ──────────────────────────────────────────────────────────────────────

def run():
    now = datetime.now(ET)
    timestamp_str = now.strftime("%Y-%m-%d %H:%M:%S")
    pretty_time = now.strftime("%I:%M %p ET")

    send_telegram(f"🟢 *Core Trading Bot Started*\n⏰ {pretty_time}")

    if not is_market_open():
        send_telegram("🔴 *Market is closed. Bot exiting.*")
        return

    # Volatility scan
    volatility_alerts: List[str] = []
    for s in SYMBOLS:
        vol = compute_volatility(s)
        if vol is not None and vol > VOLATILITY_THRESHOLD:
            volatility_alerts.append(f"`{s}` vol: {vol:.4f}")
    if volatility_alerts:
        send_telegram("⚠️ *Volatility Alerts*\n\n" + "\n".join(volatility_alerts) + f"\n\n⏰ {pretty_time}")

    # Signal loop
    trades_made: List[Dict[str, Any]] = []
    errors: List[str] = []

    for symbol in SYMBOLS:
        try:
            bars = get_bars(symbol, timeframe="5Min", limit=80)
            signal = generate_signal(bars, now=now)
            price = float(bars["close"].iloc[-1])
            print(f"{symbol} | Signal: {signal} | Price: ${price:.2f}")

            if signal in ("BUY", "SELL"):
                result = submit_order(symbol, signal, price)
                if result is not None:
                    trades_made.append(result)

        except Exception as e:
            tb = traceback.format_exc()
            print(f"[bot] error for {symbol}: {e}\n{tb}")
            errors.append(f"{symbol}: {e}")

    # Log and report
    log_trades(trades_made, timestamp_str)

    if trades_made:
        est_pnl = estimate_pnl_from_account()
        lines = ["🤖 *Core Trading Bot — Trades Executed*\n"]
        for t in trades_made:
            qty_str = f" x{t['qty']}" if t.get("qty") else ""
            lines.append(f"*{t['side']}* `{t['symbol']}`{qty_str} @ `${t['price']:.2f}`")
        lines.append(f"\n💰 *Est. Unrealized PnL:* `${est_pnl:.2f}`")
        lines.append(f"⏰ {pretty_time}")
        send_telegram("\n".join(lines))

        for t in trades_made:
            chart = make_price_chart(t["symbol"])
            if chart:
                send_telegram_photo(chart, caption=f"📈 `{t['symbol']}` recent price")
    else:
        send_telegram(f"📭 *No trades executed this run.*\n⏰ {pretty_time}")

    if errors:
        error_text = "\n".join(f"`{e}`" for e in errors)
        send_telegram(f"⚠️ *Bot Errors*\n\n{error_text}\n\n⏰ {pretty_time}")

    send_telegram(f"🔵 *Core Trading Bot Finished*\n⏰ {pretty_time}")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        tb = traceback.format_exc()
        print("Unhandled exception:", e)
        print(tb)
        send_telegram(f"❌ *Bot crashed*\n\n`{e}`\n\nSee logs for details.")
        raise
