# daily_summary.py
# Runs after the close via GitHub Actions. Pulls today's ORB fills and account
# state from Alpaca, tallies why signals were rejected from today's bot run logs,
# and sends one Telegram digest. Alpaca is the source of truth for trades —
# nothing is persisted to the repo.

from collections import Counter
from datetime import datetime, timedelta
import io
import os
import re
import zipfile

import pytz
import requests

ET = pytz.timezone("America/New_York")
ORB_TAG = "orb"


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram(text: str):
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("Telegram not configured; printing summary instead:\n", text)
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception as e:
        print(f"[telegram] send error: {e}")


# ── Alpaca ────────────────────────────────────────────────────────────────────

def get_api():
    import alpaca_trade_api as tradeapi
    return tradeapi.REST(
        os.getenv("ALPACA_API_KEY"),
        os.getenv("ALPACA_SECRET_KEY"),
        os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets"),
        api_version="v2",
    )


def get_account_summary(api) -> dict:
    """Portfolio value, cash, open positions, unrealized PnL, and today's total
    account PnL (equity vs. yesterday's close — includes the long-term sleeve)."""
    try:
        account = api.get_account()
        positions = api.list_positions()
        equity = float(account.equity)
        last_equity = float(account.last_equity)
        return {
            "portfolio_value": float(account.portfolio_value),
            "cash": float(account.cash),
            "unrealized_pnl": sum(float(getattr(p, "unrealized_pl", 0)) for p in positions),
            "positions": len(positions),
            "day_pnl": equity - last_equity,
            "day_pnl_pct": (equity / last_equity - 1) * 100 if last_equity else 0.0,
        }
    except Exception as e:
        print(f"[summary] account fetch failed: {e}")
        return {}


def get_orb_trades(api, today: datetime) -> list[dict]:
    """
    Today's ORB round-trips: each tagged entry ("orb-...") paired with whatever
    closed it — a filled bracket leg (nested under the parent) or a tagged exit
    sell ("orbx-..."). Realized PnL is per round-trip; still-open entries show
    exit=None. Untagged orders (position_manager's sleeve) are ignored.
    """
    start = ET.localize(datetime(today.year, today.month, today.day))
    end = start + timedelta(days=1)
    try:
        orders = api.list_orders(status="closed", limit=500, nested=True,
                                 after=start.isoformat(), until=end.isoformat())
    except Exception as e:
        print(f"[summary] order fetch failed: {e}")
        return []

    exits = {}  # symbol -> list of (qty, price) from tagged exit sells
    entries = []
    for o in orders:
        cid = o.client_order_id or ""
        if o.status != "filled":
            continue
        if cid.startswith(f"{ORB_TAG}x-"):
            exits.setdefault(o.symbol, []).append((float(o.filled_qty), float(o.filled_avg_price)))
        elif cid.startswith(f"{ORB_TAG}-") and o.side == "buy":
            entries.append(o)

    trades = []
    for o in entries:
        qty = float(o.filled_qty)
        entry_px = float(o.filled_avg_price)
        exit_px = None
        for leg in getattr(o, "legs", None) or []:
            if leg.status == "filled" and float(leg.filled_qty or 0) > 0:
                exit_px = float(leg.filled_avg_price)
                break
        if exit_px is None and exits.get(o.symbol):
            _, exit_px = exits[o.symbol].pop(0)
        trades.append({
            "symbol": o.symbol, "qty": qty, "entry": entry_px, "exit": exit_px,
            "pnl": (exit_px - entry_px) * qty if exit_px is not None else None,
        })
    return trades


# ── Signal rejection tally from today's bot logs ──────────────────────────────

# bot.py prints "SYMBOL | SIGNAL | $price | reason" once per symbol per run
SIGNAL_LINE = re.compile(r"^(?:.*?\s)?([A-Z]{1,5}) \| (BUY|SELL|HOLD) \| \$[\d.,]+ \| (.+?)\s*$")


def normalize_reason(reason: str) -> str:
    return re.split(r" [\(\[]", reason, maxsplit=1)[0].strip()


def get_rejection_tally(today: datetime) -> tuple[Counter, int]:
    """Download today's successful trading_bot.yml run logs from GitHub and
    count HOLD reasons. Returns (Counter, number_of_runs). Needs GITHUB_TOKEN
    and GITHUB_REPOSITORY, both provided automatically inside Actions."""
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        return Counter(), 0

    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    date_str = today.strftime("%Y-%m-%d")
    tally = Counter()
    runs_seen = 0
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{repo}/actions/workflows/trading_bot.yml/runs",
            params={"created": f">={date_str}", "status": "success", "per_page": 100},
            headers=headers, timeout=20,
        )
        resp.raise_for_status()
        for run in resp.json().get("workflow_runs", []):
            logs = requests.get(run["logs_url"], headers=headers, timeout=30)
            if logs.status_code != 200:
                continue
            runs_seen += 1
            with zipfile.ZipFile(io.BytesIO(logs.content)) as zf:
                for name in zf.namelist():
                    if "Run bot" not in name:
                        continue
                    for raw in zf.read(name).decode("utf-8", "replace").splitlines():
                        m = SIGNAL_LINE.match(raw)
                        if m and m.group(2) == "HOLD":
                            tally[normalize_reason(m.group(3))] += 1
    except Exception as e:
        print(f"[summary] log tally failed: {e}")
    return tally, runs_seen


# ── Summary builder ───────────────────────────────────────────────────────────

def build_summary(today: datetime) -> str:
    api = get_api()
    trades = get_orb_trades(api, today)
    account = get_account_summary(api)
    tally, runs = get_rejection_tally(today)

    lines = [f"📅 *Daily Summary — {today:%A, %b %d %Y}*\n"]

    closed = [t for t in trades if t["pnl"] is not None]
    if trades:
        wins = [t for t in closed if t["pnl"] > 0]
        losses = [t for t in closed if t["pnl"] <= 0]
        realized = sum(t["pnl"] for t in closed)
        lines.append(f"*ORB trades:* {len(trades)}  |  realized `${realized:+,.2f}`")
        if closed:
            avg_w = sum(t["pnl"] for t in wins) / len(wins) if wins else 0.0
            avg_l = sum(t["pnl"] for t in losses) / len(losses) if losses else 0.0
            lines.append(f"win rate {len(wins)}/{len(closed)}  |  avg win `${avg_w:+,.0f}`  avg loss `${avg_l:+,.0f}`")
        for t in trades:
            if t["pnl"] is None:
                lines.append(f"  • `{t['symbol']}` {t['qty']:g} @ {t['entry']:.2f} → *still open*")
            else:
                mark = "🟢" if t["pnl"] > 0 else "🔴"
                lines.append(f"  {mark} `{t['symbol']}` {t['qty']:g} @ {t['entry']:.2f} → {t['exit']:.2f}  `${t['pnl']:+,.2f}`")
    else:
        lines.append("*ORB trades:* none today.")

    if runs:
        lines.append(f"\n*Why no entry* ({runs} runs, {sum(tally.values())} evaluations):")
        for reason, n in tally.most_common(6):
            lines.append(f"  • {reason}: {n}")

    if account:
        pnl = account["day_pnl"]
        lines.append("")
        lines.append(f"{'🟢' if pnl >= 0 else '🔴'} *Account today:* `${pnl:+,.2f}` ({account['day_pnl_pct']:+.2f}%)")
        lines.append(f"💼 Portfolio `${account['portfolio_value']:,.2f}`  💵 Cash `${account['cash']:,.2f}`")
        lines.append(f"📊 Open positions `{account['positions']}`  unrealized `${account['unrealized_pnl']:+,.2f}`")
    else:
        lines.append("\n⚠️ Could not fetch account data.")

    lines.append(f"\n⏰ {datetime.now(ET):%I:%M %p ET}")
    return "\n".join(lines)


if __name__ == "__main__":
    summary = build_summary(datetime.now(ET))
    print(summary)
    send_telegram(summary)
