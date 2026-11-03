"""Go-live scorecard for the spike pod -- same pass rule as scripts/scorecard.py.

Reads the pod's closed trades (STATE_DIR/spike_trades.csv) and checks:
  1. at least MIN_TRADES closed trades
  2. still profitable after EXTRA_COST_PER_SHARE on each side
  3. t-stat of per-trade net P&L >= MIN_T
  4. worst drawdown of the pod's P&L curve < MAX_DD_PCT of starting equity
The pod sends it once per week, Saturdays after 16:00 UTC (spike_pod.main).

It also steers the pod (decide): the weekly verdict writes
STATE_DIR/spike_control.json, which the pod reads before every entry.
  - pause when live trading clearly runs below the backtest (live mean bps
    at least 2 standard errors under EXPECTED_BPS, from JUDGE_AFTER trades)
    or the drawdown limit is hit; only the owner resumes it
  - grow size x GROW_STEP (to MAX_MULT) when the go-live checks all pass,
    at most once per GROW_EVERY_DAYS, so live evidence sizes the pod
Settings are fixed rules, never tuned from live results.
"""
import csv
import json
import math
import os
from datetime import date, timedelta

MIN_TRADES = 100
EXTRA_COST_PER_SHARE = 0.02  # per side
MIN_T = 2.0
MAX_DD_PCT = 10.0
STARTING_EQUITY = 100_000
EXPECTED_BPS = 8.0      # IEX replay of the afternoon edge, 2025-26 (+7..10 bps gross)
JUDGE_AFTER = 30        # trades before live results can pause the pod
PAUSE_Z = -2.0
GROW_STEP, MAX_MULT, GROW_EVERY_DAYS = 1.5, 3.0, 28


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


def load_control(path: str) -> dict:
    try:
        with open(path) as f:
            return {"paused": False, "size_mult": 1.0, **json.load(f)}
    except (OSError, ValueError):
        return {"paused": False, "size_mult": 1.0}


def save_control(path: str, control: dict):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(control, f, indent=1)
    os.replace(tmp, path)


def decide(rows: list[dict], control: dict, today: date) -> tuple[dict, str]:
    """This week's control for the pod and a one-line reason."""
    control = dict(control)
    if control.get("paused"):
        return control, f"PAUSED since {control.get('since')}: {control.get('reason')} (owner resumes)"
    s = stats(rows)
    reason = ""
    if s["n"] and s["dd_pct"] >= MAX_DD_PCT:
        reason = f"drawdown {s['dd_pct']:.1f}% hit the {MAX_DD_PCT:g}% limit"
    elif s["n"] >= JUDGE_AFTER:
        bps = [float(r["bps"]) for r in rows]
        sd = math.sqrt(sum((x - s["bps"]) ** 2 for x in bps) / (s["n"] - 1))
        z = (s["bps"] - EXPECTED_BPS) / (sd / math.sqrt(s["n"])) if sd else 0.0
        if z <= PAUSE_Z:
            reason = f"live {s['bps']:+.1f} bps/trade is {abs(z):.1f} SE under the backtest's {EXPECTED_BPS:+g}"
    if reason:
        control.update(paused=True, since=today.isoformat(), reason=reason)
        return control, f"PAUSING new entries: {reason}"
    last = control.get("grown")
    due = not last or today - date.fromisoformat(last) >= timedelta(days=GROW_EVERY_DAYS)
    if (s["n"] >= MIN_TRADES and s["net"] > 0 and s["t"] >= MIN_T and due
            and control["size_mult"] < MAX_MULT):
        control.update(size_mult=round(min(MAX_MULT, control["size_mult"] * GROW_STEP), 2),
                       grown=today.isoformat())
        return control, f"GROWING size to x{control['size_mult']:g}: the edge is holding live"
    return control, f"size x{control['size_mult']:g}, running" + (
        f" (judged from {JUDGE_AFTER} trades)" if s["n"] < JUDGE_AFTER else "")


def build(path: str, date_label: str, control_note: str = "") -> str:
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
    if control_note:
        lines.append(f"Control: {control_note}")
    return "\n".join(lines)
