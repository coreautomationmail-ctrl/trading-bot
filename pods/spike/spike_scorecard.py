"""Go-live scorecard for the spike pod -- same pass rule as scripts/scorecard.py.

Reads the pod's closed trades (STATE_DIR/spike_trades.csv) and checks:
  1. at least MIN_TRADES closed trades
  2. still profitable after EXTRA_COST_PER_SHARE on each side
  3. t-stat of per-trade net P&L >= MIN_T
  4. worst drawdown of the pod's P&L curve < MAX_DD_PCT of starting equity
The pod sends it once per week, Saturdays after 16:00 UTC (spike_pod.main).
"""
import csv
import math
import os

MIN_TRADES = 100
EXTRA_COST_PER_SHARE = 0.02  # per side
MIN_T = 2.0
MAX_DD_PCT = 10.0
STARTING_EQUITY = 100_000


def load(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("pnl")]


def stats(rows: list[dict]) -> dict:
    nets = [float(r["pnl"]) - 2 * EXTRA_COST_PER_SHARE * float(r["qty"]) for r in rows]
    bps = [float(r["bps"]) for r in rows]
    n = len(nets)
    mean = sum(nets) / n if n else 0.0
    sd = math.sqrt(sum((x - mean) ** 2 for x in nets) / (n - 1)) if n > 1 else 0.0
    peak = curve = dd = 0.0
    for x in nets:
        curve += x
        peak = max(peak, curve)
        dd = max(dd, peak - curve)
    return {"n": n, "gross": sum(float(r["pnl"]) for r in rows), "net": sum(nets), "mean": mean,
            "t": mean / (sd / math.sqrt(n)) if sd else 0.0, "wins": sum(1 for x in nets if x > 0),
            "dd_pct": dd / STARTING_EQUITY * 100, "bps": sum(bps) / n if n else 0.0,
            "long": sum(1 for r in rows if r["side"] == "long")}


def build(path: str, date_label: str) -> str:
    s = stats(load(path))
    checks = [
        (f"{MIN_TRADES}+ trades", s["n"] >= MIN_TRADES, f"{s['n']}/{MIN_TRADES}"),
        ("profitable after costs", s["net"] > 0, f"${s['net']:+,.0f}"),
        (f"t-stat >= {MIN_T:g}", s["t"] >= MIN_T, f"{s['t']:.2f}"),
        (f"drawdown < {MAX_DD_PCT:g}%", s["dd_pct"] < MAX_DD_PCT, f"{s['dd_pct']:.2f}%"),
    ]
    if s["n"] < MIN_TRADES:
        verdict = "IN PROGRESS - not enough trades to judge; keep paper trading"
    elif all(ok for _, ok, _ in checks):
        verdict = "PASS - the edge held up; worth discussing real money"
    else:
        verdict = "FAIL - no reliable edge at real fills"
    lines = [f"STRATTON spike pod scorecard - {date_label} [paper]",
             f"{s['n']} trades ({s['long']} long), win {s['wins']}/{s['n']}, avg {s['bps']:+.1f} bps gross",
             f"gross ${s['gross']:+,.0f} -> net after ${EXTRA_COST_PER_SHARE:.2f}/share/side ${s['net']:+,.0f}",
             ""]
    lines += [f"{'[x]' if ok else '[ ]'} {name}: {value}" for name, ok, value in checks]
    lines += ["", verdict]
    return "\n".join(lines)
