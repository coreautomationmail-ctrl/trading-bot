"""
STRATTON missed-opportunity report (daily, no orders).

After the close: the day's biggest movers among the 277 names we hold 1-min
history for, what the spike pod did about each, and why it did nothing when it
did nothing. Sent to Telegram; STATE_DIR/missed/ keeps each day's report, a
log of every mover, and a rollup for the research desk.

Movers are picked with hindsight, so this finds leads to test, never trades.
It runs as its own short-lived container: it can't stop the pod, and it never
calls an order, position or account endpoint.

Usage: python missed_report.py [YYYY-MM-DD] [--no-send] [--force]
"""
import csv
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

import requests

import spike_pod as pod

TOP_N = 10
ROLLUP_DAYS = 20
GAP_SHARE = 0.6      # no signal and this share of the move came at the open: a gap
MIN_PRICE = 5.0
MIN_DOLLAR_VOLUME = 20e6   # the day's; old list names can be thin (or a reused ticker)
SIP_DELAY = timedelta(minutes=16)   # free data: SIP bars from the last 15 min are refused
MISSED_DIR = os.path.join(pod.STATE_DIR, "missed")
LOG_CSV = os.path.join(MISSED_DIR, "missed_log.csv")
LOG_FIELDS = ["date", "symbol", "move_pct", "gap_pct", "category", "signal_et", "signal_side", "signal_bps", "in_pod", "in_shadow"]
HERE = os.path.dirname(os.path.abspath(__file__))

CATEGORY_TEXT = {
    "traded": "pod traded it",
    "pod_missed": "signal in window on a pod name, no trade",
    "outside_window": "signal on a pod name, outside the 13:00-14:54 window",
    "not_in_pod": "signal in the window, name not in the pod",
    "not_in_pod_outside_window": "signal, name not in the pod and outside the window",
    "gap": "moved at the open, no signal",
    "drift": "moved steadily, no signal",
}


def sip_bars(symbols: list, timeframe: str, start: datetime, end: datetime) -> dict:
    """SIP bars, split-adjusted so a split doesn't read as a huge move: {sym: [bar, ...]}."""
    out = {}
    for i in range(0, len(symbols), 100):
        token = None
        while True:
            params = {"symbols": ",".join(symbols[i:i + 100]), "timeframe": timeframe, "feed": "sip", "adjustment": "split",
                      "start": start.isoformat(), "end": end.isoformat(), "limit": 10000, "sort": "asc"}
            if token:
                params["page_token"] = token
            r = requests.get(pod.DATA_URL, headers=pod._headers(), params=params, timeout=30)
            r.raise_for_status()
            body = r.json()
            for sym, bars in (body.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            token = body.get("next_page_token")
            if not token:
                break
    return out


def movers(daily: dict, day: str, n: int = TOP_N) -> list:
    """The day's biggest close-to-close moves: [{symbol, move_pct, gap_pct}], biggest first."""
    rows = []
    for sym, bars in daily.items():
        today = [b for b in bars if b["t"][:10] == day]
        prior = [b for b in bars if b["t"][:10] < day]
        if not today or not prior or prior[-1]["c"] < MIN_PRICE or today[0]["c"] * today[0]["v"] < MIN_DOLLAR_VOLUME:
            continue
        prev, b = prior[-1]["c"], today[0]
        rows.append({"symbol": sym, "move_pct": (b["c"] / prev - 1) * 100, "gap_pct": (b["o"] / prev - 1) * 100})
    return sorted(rows, key=lambda r: abs(r["move_pct"]), reverse=True)[:n]


def first_signal(bars: list):
    """The spike rule (same code as the pod) over one session: the first signal with its
    backtest-style outcome (enter the open two bars later, exit the close 60 bars after that), or None."""
    st = pod.SymState(0, 15 * 60 + 59)   # the 16:00 bar is the closing auction, not a tradable spike
    for i, b in enumerate(bars):
        ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(pod.ET)
        side = st.update(ts, b["o"], b["c"], b["v"])
        if side:
            entry, exit_ = i + 2, i + 2 + pod.HOLD_MIN
            bps = None
            if exit_ < len(bars):
                bps = side * math.log(bars[exit_]["c"] / bars[entry]["o"]) * 1e4
            return {"et": ts.strftime("%H:%M"), "minute": ts.hour * 60 + ts.minute, "side": side, "bps": bps}
    return None


def classify(mover: dict, sig, traded: bool, in_pod: bool) -> str:
    if traded:
        return "traded"
    if sig:
        in_window = pod.FIRST_ENTRY <= sig["minute"] <= pod.LAST_ENTRY
        if in_pod:
            return "pod_missed" if in_window else "outside_window"
        return "not_in_pod" if in_window else "not_in_pod_outside_window"
    move, gap = mover["move_pct"], mover["gap_pct"]
    return "gap" if move and gap / move >= GAP_SHARE else "drift"


def read_csv(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def headline(sym: str, day: str) -> str:
    """Best-effort reason from Alpaca's news feed; a report without one is still a report."""
    try:
        r = requests.get("https://data.alpaca.markets/v1beta1/news", headers=pod._headers(), timeout=15,
                         params={"symbols": sym, "start": f"{day}T00:00:00Z", "end": f"{day}T23:59:59Z", "limit": 1})
        r.raise_for_status()
        news = r.json().get("news") or []
        return news[0]["headline"][:110] if news else ""
    except Exception as e:
        print(f"news {sym}: {e}", flush=True)
        return ""


def signed(x: float) -> str:
    return f"{x:+.1f}%"


def build(day: str, rows: list, trades: list, shadow: list, spy) -> str:
    pnl = sum(float(t["pnl"]) for t in trades)
    lines = [f"STRATTON missed-opportunity report, {day}"]
    if spy is not None:
        lines.append(f"SPY {signed(spy)}. Pod: {len(trades)} trade(s), ${pnl:+.2f}.")
    else:
        lines.append(f"Pod: {len(trades)} trade(s), ${pnl:+.2f}.")
    lines.append("")
    lines.append(f"Biggest {len(rows)} moves of 277 names:")
    for r in rows:
        sig = ""
        if r["signal_et"]:
            direction = "up" if r["signal_side"] > 0 else "down"
            result = f"{r['signal_bps']:+.0f} bps in 60 min" if r["signal_bps"] is not None else "no 60-min result"
            sig = f" Signal {r['signal_et']} ET, {direction}: {result}."
        lines.append(f"{r['symbol']} {signed(r['move_pct'])} (gap {signed(r['gap_pct'])}): {CATEGORY_TEXT[r['category']]}.{sig}")
        if r.get("headline"):
            lines.append(f"  {r['headline']}")
    done = [float(s["bps"]) for s in shadow if s.get("bps") not in (None, "")]
    if done:
        lines += ["", f"Shadow log (62 names, no orders): {len(done)} signal(s), average {sum(done) / len(done):+.1f} bps gross."]
    lines += ["", "Picked with hindsight: leads to test, not trades."]
    return "\n".join(lines)


def rollup(log: list, shadow: list) -> str:
    """What the research desk reads: patterns only, no news text."""
    days = sorted({r["date"] for r in log})[-ROLLUP_DAYS:]
    recent = [r for r in log if r["date"] in days]
    lines = ["# Missed-opportunity rollup", "",
             f"Last {len(days)} session(s), {days[0]} to {days[-1]}. Top {TOP_N} movers a day of 277 names, picked with",
             "HINDSIGHT: a lead for a pre-registered test, never evidence of an edge.", "",
             "| why the pod didn't get it | movers | avg abs move | signals with a 60-min result | avg bps gross |", "|---|---|---|---|---|"]
    groups = defaultdict(list)
    for r in recent:
        groups[r["category"]].append(r)
    for cat, rs in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        bps = [float(r["signal_bps"]) for r in rs if r["signal_bps"]]
        avg = f"{sum(bps) / len(bps):+.1f}" if bps else "-"
        move = sum(abs(float(r["move_pct"])) for r in rs) / len(rs)
        lines.append(f"| {CATEGORY_TEXT[cat]} | {len(rs)} | {move:.1f}% | {len(bps)} | {avg} |")
    repeat = Counter(r["symbol"] for r in recent).most_common(10)
    lines += ["", "Names on the list most often: " + ", ".join(f"{s} {n}" for s, n in repeat)]
    done = [float(s["bps"]) for s in shadow if s.get("bps") not in (None, "") and s["date"] in days]
    if done:
        lines.append(f"Shadow log over the same days: {len(done)} signals, average {sum(done) / len(done):+.1f} bps gross.")
    return "\n".join(lines) + "\n"


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    day = args[0] if args else str(datetime.now(pod.ET).date())
    path = os.path.join(MISSED_DIR, f"{day}.md")
    if os.path.exists(path) and "--force" not in sys.argv:
        print(f"{day} already reported", flush=True)
        return
    universe = json.load(open(os.path.join(HERE, "missed_universe.json")))
    d = datetime.fromisoformat(day).replace(tzinfo=pod.ET)
    latest = datetime.now(pod.ET) - SIP_DELAY
    daily = sip_bars(universe, "1Day", d - timedelta(days=10), min(d.replace(hour=23), latest))
    top = movers(daily, day)
    if not top:
        print(f"no bars for {day}: market closed", flush=True)
        return
    minute = sip_bars([m["symbol"] for m in top], "1Min", d.replace(hour=9, minute=30), min(d.replace(hour=16), latest))
    trades = [t for t in read_csv(pod.TRADES_CSV) if t["entry_time"][:10] == day]
    traded = {t["symbol"] for t in trades}
    shadow_all = read_csv(pod.SHADOW_CSV)
    rows = []
    for m in top:
        sym = m["symbol"]
        sig = first_signal(minute.get(sym, []))
        rows.append({**m, "category": classify(m, sig, sym in traded, sym in pod.UNIVERSE),
                     "signal_et": sig["et"] if sig else "", "signal_side": sig["side"] if sig else 0,
                     "signal_bps": sig["bps"] if sig else None, "in_pod": sym in pod.UNIVERSE, "in_shadow": sym in pod.SHADOW,
                     "headline": headline(sym, day)})
    spy = next((m["move_pct"] for m in movers({"SPY": daily["SPY"]}, day)), None) if "SPY" in daily else None
    text = build(day, rows, trades, [s for s in shadow_all if s["date"] == day], spy)
    os.makedirs(MISSED_DIR, exist_ok=True)
    log = [r for r in read_csv(LOG_CSV) if r["date"] != day]
    for r in rows:
        log.append({"date": day, **{k: r[k] for k in LOG_FIELDS if k != "date"},
                    "move_pct": f"{r['move_pct']:.2f}", "gap_pct": f"{r['gap_pct']:.2f}",
                    "signal_bps": "" if r["signal_bps"] is None else f"{r['signal_bps']:.1f}"})
    with open(LOG_CSV, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS)
        w.writeheader()
        w.writerows(sorted(log, key=lambda r: r["date"]))
    with open(os.path.join(MISSED_DIR, "missed_summary.md"), "w") as f:
        f.write(rollup(log, shadow_all))
    with open(path, "w") as f:
        f.write(text + "\n")
    if "--no-send" in sys.argv:
        print(text, flush=True)
    else:
        pod.send_telegram(text)


if __name__ == "__main__":
    main()
