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
matplotlib.use("Agg")
import matplotlib.pyplot as plt

from alpaca_client import get_client, get_bars, get_account
from strategy import generate_signal
from executor import submit_order, check_daily_loss_limit

# ── Config ────────────────────────────────────────────────────────────────────

SYMBOLS = ["SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "AMZN"]
ET = pytz.timezone("America/New_York")
TRADE_LOG_FILE = "trade_log.csv"
VOLATILITY_THRESHOLD = 0.02

# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured:", text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[telegram] error: {e}")


def send_telegram_photo(photo_path: str, caption: Optional[str] = None):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id or not os.path.isfile(photo_path):
        return
    try:
        with open(photo_path, "rb") as f:
            data: Dict[str, Any] = {"chat_id": chat_id}
            if caption:
                data["caption"] = caption
                data["parse_mode"] = "Markdown"
            requests.post(
                f"https://api.telegram.org/bot{token}/sendPhoto",
                data=data, files={"photo": f}, timeout=30,
            )
    except Exception as e:
        print(f"[telegram] photo error: {e}")


# ── Market ────────────────────────────────────────────────────────────────────

def is_market_open() -> bool:
    try:
        return get_client().get_clock().is_open
    except Exception as e:
        print(f"[market] status check failed: {e}")
        return False


# ── Volatility scan ───────────────────────────────────────────────────────────

def compute_volatility(symbol: str, lookback: int = 20) -> Optional[float]:
    try:
        bars = get_bars(symbol, timeframe="5Min", limit=lookback + 1)
        closes = bars["close"].astype(float)
        returns = [(closes.iloc[i] / closes.iloc[i - 1]) - 1 for i in range(1, len(closes))]
        return statistics.stdev(returns) if len(returns) >= 2 else None
    except Exception as e:
        print(f"[volatility] {symbol}: {e}")
        return None


# ── Trade logging ─────────────────────────────────────────────────────────────

def log_trades(trades: List[Dict[str, Any]], timestamp_str: str):
    if not trades:
        return
    header = ["timestamp", "symbol", "side", "price", "qty"]
    file_exists = os.path.isfile(TRADE_LOG_FILE)
    with open(TRADE_LOG_FILE, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(header)
        for t in trades:
            writer.writerow([timestamp_str, t["symbol"], t["side"],
                             f"{t['price']:.2f}", t.get("qty", "")])
    # Persist to repo so daily_summary.py can read it
    try:
        subprocess.run(["git", "config", "user.email", "bot@core-automation-ai"], check=True)
        subprocess.run(["git", "config", "user.name", "Core Trading Bot"], check=True)
        subprocess.run(["git", "add", TRADE_LOG_FILE], check=True)
        subprocess.run(["git", "commit", "-m", f"chore: trade log {timestamp_str}"], check=True)
        subprocess.run(["git", "push"], check=True)
        print("[log] trade_log.csv committed")
    except subprocess.CalledProcessError as e:
        print(f"[log] git push failed (non-fatal): {e}")


# ── PnL ───────────────────────────────────────────────────────────────────────

def estimate_pnl() -> float:
    try:
        positions = get_client().list_positions()
        return sum(float(getattr(p, "unrealized_pl", 0)) for p in positions)
    except Exception as e:
        print(f"[pnl] {e}")
        return 0.0


# ── Charting ──────────────────────────────────────────────────────────────────

def make_price_chart(symbol: str, timeframe: str = "5Min", limit: int = 120) -> Optional[str]:
    try:
        bars = get_bars(symbol, timeframe=timeframe, limit=limit)
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
        print(f"[chart] {symbol}: {e}")
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

    # Daily loss guard — alert and exit if limit hit
    if check_daily_loss_limit():
        pnl = estimate_pnl()
        send_telegram(
            f"⛔ *Daily Loss Limit Hit*\n\n"
            f"Unrealized PnL: `${pnl:.2f}`\n"
            f"Bot will not trade further today.\n"
            f"⏰ {pretty_time}"
        )
        return

    # Volatility scan
    vol_alerts: List[str] = []
    for s in SYMBOLS:
        vol = compute_volatility(s)
        if vol is not None and vol > VOLATILITY_THRESHOLD:
            vol_alerts.append(f"`{s}` {vol:.4f}")
    if vol_alerts:
        send_telegram("⚠️ *High Volatility*\n" + "\n".join(vol_alerts) + f"\n⏰ {pretty_time}")

    # Signal loop
    trades_made: List[Dict[str, Any]] = []
    errors: List[str] = []
    signal_log: List[str] = []

    for symbol in SYMBOLS:
        try:
            bars = get_bars(symbol, timeframe="5Min", limit=100)
            result = generate_signal(bars, symbol=symbol, now=now)
            signal = result["signal"]
            price = float(bars["close"].iloc[-1])

            signal_log.append(
                f"`{symbol}` {signal} | reason: {result['reason']} | ${price:.2f}"
            )
            print(f"{symbol} | {signal} | ${price:.2f} | {result['reason']}")

            if signal in ("BUY", "SELL"):
                order = submit_order(
                    symbol=symbol,
                    signal=signal,
                    price=price,
                    size_mult=result["size_mult"],
                    stop_pct=result["stop_pct"],
                    take_pct=result["take_pct"],
                )
                if order is not None:
                    trades_made.append(order)

        except Exception as e:
            tb = traceback.format_exc()
            print(f"[bot] {symbol} error: {e}\n{tb}")
            errors.append(f"{symbol}: {e}")

    log_trades(trades_made, timestamp_str)

    # Signal summary (sent every run so you can see what the bot evaluated)
    if signal_log:
        send_telegram(
            f"🔍 *Signal Scan — {pretty_time}*\n\n" + "\n".join(signal_log)
        )

    # Trade report
    if trades_made:
        pnl = estimate_pnl()
        lines = ["🤖 *Trades Executed*\n"]
        for t in trades_made:
            lines.append(f"*{t['side']}* `{t['symbol']}` x{t.get('qty','')} @ `${t['price']:.2f}`")
        lines.append(f"\n💰 *Unrealized PnL:* `${pnl:.2f}`")
        lines.append(f"⏰ {pretty_time}")
        send_telegram("\n".join(lines))
        for t in trades_made:
            chart = make_price_chart(t["symbol"])
            if chart:
                send_telegram_photo(chart, caption=f"📈 `{t['symbol']}`")
    else:
        send_telegram(f"📭 *No trades this run.*\n⏰ {pretty_time}")

    if errors:
        send_telegram("⚠️ *Errors*\n" + "\n".join(f"`{e}`" for e in errors))

    send_telegram(f"🔵 *Bot Finished*\n⏰ {pretty_time}")


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        tb = traceback.format_exc()
        print("Unhandled exception:", e, tb)
        send_telegram(f"❌ *Bot crashed*\n\n`{e}`")
        raise
