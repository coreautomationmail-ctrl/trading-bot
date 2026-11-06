"""STRATTON's senses: what the world looked like at the time, saved as it happened.

Twice a trading day (before the open, after the close) writes
STATE_DIR/world/<date>-<phase>.json.gz. It keeps what can't be fetched later
(movers, most-actives, option quotes), plus the day's news and market state for
the brief. EDGAR and FINRA files stay available for years, so the desk fetches
those when it needs them. No orders, and a failed source is recorded, not fatal.

Each run also sends one short Telegram message: the pre-open brief, or the
after-close review with that day's missed-opportunity report folded in. Plain
code, no LLM: it says what the numbers say.

Usage: python world_recorder.py pre|post [--no-send]
"""
import csv
import gzip
import json
import math
import os
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from spike_pod import (CONTROL_FILE, DATA_URL, ET, PAPER_URL, SHADOW_CSV, STATE_DIR, TRADES_CSV, UNIVERSE, _headers,
                       send_telegram)

DATA = DATA_URL.rsplit("/v2/", 1)[0]
WORLD_DIR = os.path.join(STATE_DIR, "world")
UNIVERSE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "missed_universe.json")
OPTION_UNDERLYINGS = ["SPY", "QQQ"]
OPTION_DAYS, OPTION_BAND = 35, 0.05     # the month ahead, strikes within 5% of the price
STARTING_EQUITY = 100_000
MISSED_DIR = os.path.join(STATE_DIR, "missed")
# Fed's published calendar, read 2026-11-05: decision days. 2027 wasn't listed yet.
FOMC_DECISIONS = ["2026-01-28", "2026-03-18", "2026-04-29", "2026-06-17", "2026-07-29", "2026-09-16",
                  "2026-10-28", "2026-12-09"]
BIG_MARKET_MOVE_PCT = 2.0     # worth a line when SPY moved this much
RICH_IV_RATIO = 1.3           # implied over realized volatility above this: fear is priced in
BUSY_NEWS = 10                # headlines on one pod name
TELEGRAM_MAX = 4000


def get(url: str, **params):
    r = requests.get(url, headers=_headers(), params=params, timeout=30)
    r.raise_for_status()
    return r.json()


def market(closes: list[float]) -> dict:
    """Trend and realized volatility from daily closes, oldest first."""
    rets = [math.log(b / a) for a, b in zip(closes, closes[1:])][-20:]
    mean = sum(rets) / len(rets)
    return {"close": closes[-1], "ret_1d_pct": round((closes[-1] / closes[-2] - 1) * 100, 2),
            "above_50d": closes[-1] > sum(closes[-50:]) / len(closes[-50:]),
            "vol_20d_pct": round(math.sqrt(sum((x - mean) ** 2 for x in rets) / (len(rets) - 1) * 252) * 100, 1)}


def pod_day(day: str, trades: list[dict], shadow: list[dict]) -> dict:
    """The day's activity: pod trades, shadow signals, share of capital-time in use."""
    done = [t for t in trades if t["entry_time"][:10] == day]
    used = sum(float(t["qty"]) * float(t["entry_px"])
               * (datetime.fromisoformat(t["exit_time"]) - datetime.fromisoformat(t["entry_time"])).total_seconds() / 60
               for t in done)
    return {"trades": len(done), "bps": [float(t["bps"]) for t in done],
            "signals": sum(1 for s in shadow if s.get("date") == day),
            "capital_time_pct": round(used / (390 * STARTING_EQUITY) * 100, 3)}


def compact_option(snap: dict) -> dict:
    quote = snap.get("latestQuote") or {}
    return {"bid": quote.get("bp"), "ask": quote.get("ap"), "iv": snap.get("impliedVolatility"),
            "delta": (snap.get("greeks") or {}).get("delta")}


def options(underlying: str, price: float, today: datetime) -> dict:
    out, token = {}, None
    while True:
        params = {"feed": "indicative", "limit": 1000, "strike_price_gte": round(price * (1 - OPTION_BAND), 2),
                  "strike_price_lte": round(price * (1 + OPTION_BAND), 2),
                  "expiration_date_lte": (today + timedelta(days=OPTION_DAYS)).date().isoformat()}
        if token:
            params["page_token"] = token
        body = get(f"{DATA}/v1beta1/options/snapshots/{underlying}", **params)
        out.update({k: compact_option(v) for k, v in (body.get("snapshots") or {}).items()})
        token = body.get("next_page_token")
        if not token:
            return out


def news(since: datetime) -> list[dict]:
    with open(UNIVERSE_FILE) as f:
        symbols = ",".join(json.load(f))
    out, token = [], None
    while True:
        params = {"symbols": symbols, "start": since.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "limit": 50, "sort": "asc"}
        if token:
            params["page_token"] = token
        body = get(f"{DATA}/v1beta1/news", **params)
        out += [{k: n.get(k) for k in ("created_at", "headline", "summary", "symbols", "source", "url")}
                for n in body.get("news") or []]
        token = body.get("next_page_token")
        if not token:
            return out


def atm_iv(chain: dict, price: float, today: date, min_days: int = 20):
    """Mean implied volatility of the at-the-money call and put in the first expiry at least
    min_days out. Contract symbols look like SPY261112C00743000."""
    picked = {}
    for sym, quote in chain.items():
        core = sym[-15:]
        expiry = date(2000 + int(core[:2]), int(core[2:4]), int(core[4:6]))
        if (expiry - today).days >= min_days and quote.get("iv"):
            picked.setdefault(expiry, []).append((abs(int(core[7:]) / 1000 - price), core[6], quote["iv"]))
    if not picked:
        return None
    nearest = {}
    for gap, kind, iv in sorted(picked[min(picked)], key=lambda x: x[0]):
        nearest.setdefault(kind, iv)
    return sum(nearest.values()) / len(nearest)


def calendar_line(day: date) -> str:
    third_friday = next(d for d in (day.replace(day=n) for n in range(15, 22)) if d.weekday() == 4)
    notes = []
    upcoming = [date.fromisoformat(d) for d in FOMC_DECISIONS if date.fromisoformat(d) >= day]
    if not upcoming:
        notes.append("FOMC dates need updating")
    elif upcoming[0] == day:
        notes.append("FOMC decision today, 14:00 ET")
    else:
        notes.append(f"next FOMC {upcoming[0]:%b} {upcoming[0].day} ({(upcoming[0] - day).days} days)")
    if third_friday == day:
        notes.append("monthly options expiry today")
    return "Calendar: " + ", ".join(notes) + ". CPI and earnings dates aren't tracked yet."


def noticing(data: dict, today: date) -> list[str]:
    """Plain rules for what deserves a line. Facts for the owner, never inputs to the pod."""
    notes = []
    spy = data.get("market_SPY") or {}
    if abs(spy.get("ret_1d_pct", 0)) >= BIG_MARKET_MOVE_PCT:
        notes.append(f"SPY moved {spy['ret_1d_pct']:+.1f}% last session: a stress test for the signals")
    iv = atm_iv(data.get("options_SPY") or {}, spy.get("close", 0), today) if spy else None
    if iv and spy.get("vol_20d_pct"):
        ratio = iv * 100 / spy["vol_20d_pct"]
        if ratio >= RICH_IV_RATIO:
            notes.append(f"Options price SPY at {iv * 100:.0f}% volatility vs {spy['vol_20d_pct']:.0f}% realized: more turbulence is priced in")
    if data.get("errors"):
        notes.append("Recorder errors: " + ", ".join(data["errors"]))
    return notes


def pod_news(items: list[dict], names: list[str] = UNIVERSE) -> tuple[Counter, list[str]]:
    """Headline counts on the pod's names and the newest few headlines about them."""
    counts, lines = Counter(), []
    for n in reversed(items):
        hit = [s for s in n.get("symbols") or [] if s in names]
        counts.update(hit)
        if hit and len(lines) < 4:
            lines.append(f"{hit[0]}: {(n.get('headline') or '')[:90]}")
    return counts, lines


def control_line() -> str:
    try:
        with open(CONTROL_FILE) as f:
            c = json.load(f)
    except (OSError, ValueError):
        c = {}
    if c.get("paused"):
        return f"Pod: PAUSED since {c.get('since')}: {c.get('reason')}"
    return f"Pod: running, size x{c.get('size_mult', 1.0):g}, {len(UNIVERSE)} names, signals 13:00-14:54 ET"


def market_line(data: dict) -> str:
    parts = []
    for sym in OPTION_UNDERLYINGS:
        m = data.get(f"market_{sym}")
        if m:
            parts.append(f"{sym} {m['close']:g} ({m['ret_1d_pct']:+.1f}%, {'above' if m['above_50d'] else 'below'} 50-day, "
                         f"{m['vol_20d_pct']:.0f}% vol)")
    return "Market: " + ("; ".join(parts) or "no data")


def brief(data: dict, today: date) -> str:
    counts, heads = pod_news(data.get("news") or [])
    lines = [f"STRATTON brief, {today:%a %b} {today.day}", market_line(data), calendar_line(today), control_line()]
    notes = noticing(data, today)
    busy = [f"{s} {n}" for s, n in counts.most_common(4) if n >= BUSY_NEWS]
    if busy:
        notes.append("Busy news on pod names: " + ", ".join(busy))
    if notes:
        lines += ["", "Worth noticing:"] + [f"- {n}" for n in notes]
    lines += ["", f"{len(data.get('news') or [])} headlines since yesterday afternoon."] + heads
    return "\n".join(lines)


def review(data: dict, today: date) -> str:
    pod = data.get("pod") or {}
    trades = ", ".join(f"{b:+.0f}" for b in pod.get("bps", [])) or "none"
    lines = [f"STRATTON review, {today:%a %b} {today.day}", market_line(data),
             f"Pod: {pod.get('trades', 0)} trades (bps: {trades}), {pod.get('signals', 0)} shadow signals settled, "
             f"{pod.get('capital_time_pct', 0):.2f}% of capital-time used"]
    if data.get("errors"):
        lines.append("Recorder errors: " + ", ".join(data["errors"]))
    path = os.path.join(MISSED_DIR, f"{today.isoformat()}.md")
    try:
        with open(path) as f:
            lines += ["", f.read().strip()]
    except OSError:
        lines += ["", "No missed-opportunity report today."]
    return "\n".join(lines)[:TELEGRAM_MAX]


def read_csv(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def snapshot(phase: str, now: datetime) -> dict:
    day = now.date().isoformat()
    out, errors = {"phase": phase, "taken_at": now.isoformat()}, {}

    def source(name, fn):
        try:
            out[name] = fn()
        except Exception as e:
            errors[name] = f"{type(e).__name__}: {e}"[:200]

    source("movers", lambda: get(f"{DATA}/v1beta1/screener/stocks/movers", top=25))
    source("most_actives", lambda: get(f"{DATA}/v1beta1/screener/stocks/most-actives", top=25))
    source("news", lambda: news(now - timedelta(hours=18)))
    closes = {}
    for sym in OPTION_UNDERLYINGS:
        def daily(sym=sym):
            bars = get(f"{DATA}/v2/stocks/bars", symbols=sym, timeframe="1Day", feed="iex", limit=100,
                       start=(now - timedelta(days=120)).date().isoformat())["bars"][sym]
            closes[sym] = [b["c"] for b in bars]
            return market(closes[sym])
        source(f"market_{sym}", daily)
        if sym in closes:
            source(f"options_{sym}", lambda sym=sym: options(sym, closes[sym][-1], now))
    if phase == "post":
        source("pod", lambda: pod_day(day, read_csv(TRADES_CSV), read_csv(SHADOW_CSV)))
    out["errors"] = errors
    return out


def main():
    phase = sys.argv[1] if len(sys.argv) > 1 else "post"
    now = datetime.now(ET)
    day = now.date().isoformat()
    if not get(f"{PAPER_URL}/v2/calendar", start=day, end=day):
        print(f"{day} {phase}: market closed today", flush=True)
        return
    data = snapshot(phase, now)
    os.makedirs(WORLD_DIR, exist_ok=True)
    path = os.path.join(WORLD_DIR, f"{day}-{phase}.json.gz")
    with gzip.open(path + ".tmp", "wt") as f:
        json.dump(data, f)
    os.replace(path + ".tmp", path)
    print(f"{day} {phase}: news {len(data.get('news', []))}, "
          f"options {sum(len(data.get(f'options_{s}', {})) for s in OPTION_UNDERLYINGS)}, "
          f"{os.path.getsize(path) // 1024} KB, errors {data['errors'] or 'none'}", flush=True)
    text = brief(data, now.date()) if phase == "pre" else review(data, now.date())
    if "--no-send" in sys.argv:
        print(text, flush=True)
    else:
        send_telegram(text)


if __name__ == "__main__":
    main()
