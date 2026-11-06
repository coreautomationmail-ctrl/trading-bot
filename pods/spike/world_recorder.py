"""STRATTON's senses: what the world looked like at the time, saved as it happened.

Twice a trading day (before the open, after the close) writes
STATE_DIR/world/<date>-<phase>.json.gz. It keeps what can't be fetched later
(movers, most-actives, option quotes), plus the day's news and market state for
the brief. EDGAR and FINRA files stay available for years, so the desk fetches
those when it needs them. No orders, and a failed source is recorded, not fatal.
"""
import csv
import gzip
import json
import math
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

from spike_pod import DATA_URL, ET, PAPER_URL, SHADOW_CSV, STATE_DIR, TRADES_CSV, _headers

DATA = DATA_URL.rsplit("/v2/", 1)[0]
WORLD_DIR = os.path.join(STATE_DIR, "world")
UNIVERSE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "missed_universe.json")
OPTION_UNDERLYINGS = ["SPY", "QQQ"]
OPTION_DAYS, OPTION_BAND = 35, 0.05     # the month ahead, strikes within 5% of the price
STARTING_EQUITY = 100_000


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


if __name__ == "__main__":
    main()
