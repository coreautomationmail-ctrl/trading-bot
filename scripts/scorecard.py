# scorecard.py
# Weekly go-live scorecard for the ORB strategy (weekly_scorecard.yml, Saturdays).
# Pulls every ORB round trip since the rig became reliable (2026-09-28) from
# Alpaca and checks the pass rule agreed 2026-10-15 before any real money:
#   1. at least MIN_TRADES closed round trips
#   2. still profitable after EXTRA_COST_PER_SHARE slippage on each side
#      (paper fills are kinder than real ones)
#   3. t-stat of per-trade net P&L >= MIN_T (the edge isn't luck)
#   4. worst drawdown of the ORB P&L curve < MAX_DD_PCT of starting equity
# Until rule 1 is met the verdict is IN PROGRESS; after that PASS or FAIL.
# Sends one Telegram message. Run: python scripts/scorecard.py

from datetime import datetime, timedelta
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from daily_summary import ET, get_api, get_orb_trades, send_telegram  # noqa: E402

START = datetime(2026, 9, 28)
STARTING_EQUITY = 100_000
MIN_TRADES = 100
EXTRA_COST_PER_SHARE = 0.02  # per side
MIN_T = 2.0
MAX_DD_PCT = 10.0


def collect(api, today: datetime) -> list[dict]:
    trades, day = [], START
    while day.date() <= today.date():
        if day.weekday() < 5:
            for t in get_orb_trades(api, day):
                if t["pnl"] is not None:
                    t["day"] = day
                    t["net"] = t["pnl"] - 2 * EXTRA_COST_PER_SHARE * t["qty"]
                    trades.append(t)
        day += timedelta(days=1)
    return trades


def stats(trades: list[dict]) -> dict:
    nets = [t["net"] for t in trades]
    n = len(nets)
    mean = sum(nets) / n if n else 0.0
    sd = math.sqrt(sum((x - mean) ** 2 for x in nets) / (n - 1)) if n > 1 else 0.0
    peak = curve = dd = 0.0
    for x in nets:
        curve += x
        peak = max(peak, curve)
        dd = max(dd, peak - curve)
    return {
        "n": n, "gross": sum(t["pnl"] for t in trades), "net": sum(nets), "mean": mean,
        "t": mean / (sd / math.sqrt(n)) if sd else 0.0,
        "wins": sum(1 for x in nets if x > 0), "dd_pct": dd / STARTING_EQUITY * 100,
    }


def build(today: datetime) -> str:
    s = stats(collect(get_api(), today))
    checks = [
        (f"{MIN_TRADES}+ trades", s["n"] >= MIN_TRADES, f"{s['n']}/{MIN_TRADES}"),
        ("profitable after costs", s["net"] > 0, f"${s['net']:+,.0f}"),
        (f"t-stat ≥ {MIN_T:g}", s["t"] >= MIN_T, f"{s['t']:.2f}"),
        (f"drawdown < {MAX_DD_PCT:g}%", s["dd_pct"] < MAX_DD_PCT, f"{s['dd_pct']:.2f}%"),
    ]
    if s["n"] < MIN_TRADES:
        verdict = "⏳ IN PROGRESS — not enough trades to judge; keep paper trading"
    elif all(ok for _, ok, _ in checks):
        verdict = "✅ PASS — the edge held up; worth discussing real money"
    else:
        verdict = "❌ FAIL — no reliable edge; move to the next intraday signal"
    lines = [
        f"📋 *ORB go-live scorecard — {today:%b %d %Y}*",
        f"since {START:%b %d}: {s['n']} trades, win {s['wins']}/{s['n']}",
        f"gross `${s['gross']:+,.0f}`  →  net after ${EXTRA_COST_PER_SHARE:.2f}/share/side `${s['net']:+,.0f}`"
        f"  (avg `${s['mean']:+,.1f}`/trade)",
        "",
    ]
    lines += [f"{'✅' if ok else '⬜'} {name}: {value}" for name, ok, value in checks]
    lines += ["", f"*{verdict}*"]
    return "\n".join(lines)


if __name__ == "__main__":
    text = build(datetime.now(ET))
    print(text)
    send_telegram(text)
