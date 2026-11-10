"""Intraday book, virtual: Claude's check-in trades (research/theses/intraday.md), sealed the moment
they reach GitHub and scored after the close against SPY and random picks over the same minutes.

At each check-in (10:30, 12:00, 13:30 ET) a PC session commits research/theses/<date>-<HHMM>.json:
  {"theses": [{"id": "<date>-<HHMM>-1", "symbol": "AMD", "direction": "long", "exit_by": "15:55",
               "stop_pct": 1.5, "target_pct": 3.0, "confidence": 0.55, "why": ..., "wrong_if": ...}],
   "close": ["<earlier intraday id>"]}
  python intraday_book.py seal    every 2 min through the session: a check-in's file is copied into
                                  data/theses/intraday/ the first time it is seen (up to 90 min after
                                  the check-in). sealed_at is that moment, so nothing is scored on
                                  prices from before the file existed.
  python intraday_book.py score   after the close: data/theses_intraday_scores.csv and
                                  data/theses_intraday_summary.json

A trade enters at the open of the first SIP 1-minute bar starting at or after sealed_at (rounded up to
the minute). It exits at the first of: its stop (fill at the stop, or at a bar's open past it), its
target (same), a later check-in's close (open of the first bar after that file was sealed), exit_by
(open of that minute's bar). A bar touching both stop and target counts as the stop. COST_BPS a side.
Baselines over the same minutes, same direction, no stop: SPY, and the mean of RANDOM_DRAWS random
names from the 277 (seeded by the trade id). Exit test as the morning book: 300 trades, beat both with
day t >= 2. Paper data only: no orders, no account endpoints.
"""
import csv
import json
import os
import random
import sys
from datetime import datetime, timedelta

import requests

import spike_pod
import thesis
from spike_pod import ET

CHECKINS = ["1030", "1200", "1330"]
SEAL_WINDOW_MIN = 90
MAX_NEW = 3
DIR = os.path.join(thesis.STATE_DIR, "theses", "intraday")
SCORES_CSV = os.path.join(thesis.STATE_DIR, "theses_intraday_scores.csv")
SUMMARY_JSON = os.path.join(thesis.STATE_DIR, "theses_intraday_summary.json")
FIELDS = ["date", "id", "symbol", "direction", "confidence", "entry_at", "exit_at", "exit_why", "net_bps",
          "spy_bps", "random_bps", "vs_spy", "vs_random", "why"]


def minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[-2:])


def valid(t: dict, date: str, checkin: str) -> str:
    """'' if the trade can be scored, else why not."""
    if t.get("symbol") not in thesis.UNIVERSE:
        return f"{t.get('symbol')} isn't in the 277-name universe"
    if t.get("direction") not in ("long", "short"):
        return "direction must be long or short"
    if not str(t.get("id", "")).startswith(f"{date}-{checkin}"):
        return "id must start with the date and check-in"
    stop, target = t.get("stop_pct"), t.get("target_pct")
    if not (isinstance(stop, (int, float)) and 0.3 <= stop <= 10):
        return "stop_pct is required, 0.3-10"
    if target is not None and not (isinstance(target, (int, float)) and stop <= target <= 20):
        return "target_pct must be null or between stop_pct and 20"
    by = str(t.get("exit_by", ""))
    if len(by) != 5 or by[2] != ":" or not by.replace(":", "").isdigit() or \
            not minutes(checkin) + 20 <= minutes(by) <= 15 * 60 + 55:
        return "exit_by must be HH:MM, 20+ minutes after the check-in, no later than 15:55"
    return ""


def seal(now: datetime) -> str:
    et = now.astimezone(ET)
    date, m = et.date().isoformat(), et.hour * 60 + et.minute
    if et.weekday() > 4:
        return "weekend"
    os.makedirs(DIR, exist_ok=True)
    done = []
    for c in CHECKINS:
        path = os.path.join(DIR, f"{date}-{c}.json")
        if os.path.exists(path) or not minutes(c) - 10 <= m <= minutes(c) + SEAL_WINDOW_MIN:
            continue
        if m > minutes(c) + 30 and m % 10 >= 2:
            continue  # every 2 minutes for the first half hour, then every 10: GitHub allows 60 unsigned calls an hour
        r = requests.get(thesis.REPO_FILE.format(date=f"{date}-{c}"),
                         headers={"Accept": "application/vnd.github.raw+json"}, timeout=20)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        body = json.loads(r.text)
        theses = (body.get("theses") or [])[:MAX_NEW]
        earlier = {t["id"] for f in sorted(os.listdir(DIR)) if f.startswith(date) and f < f"{date}-{c}.json"
                   for t in json.load(open(os.path.join(DIR, f)))["theses"]}
        with open(path, "w") as f:
            json.dump({"sealed_at": now.isoformat(), "checkin": c, "read": str(body.get("read", ""))[:300],
                       "theses": [t for t in theses if not valid(t, date, c)],
                       "close": [i for i in body.get("close") or [] if i in earlier],
                       "rejected": [{"id": t.get("id"), "why": valid(t, date, c)} for t in theses if valid(t, date, c)]},
                      f, indent=1)
        done.append(f"sealed {c}: {sum(not valid(t, date, c) for t in theses)} of {len(theses)} trades")
    return "; ".join(done)


def minute_bars(symbols: list, day: str, end: str) -> dict:
    """SIP 1-minute bars for one session: {sym: [bar, ...]} in time order."""
    out = {}
    for i in range(0, len(symbols), 100):
        token = None
        while True:
            params = {"symbols": ",".join(symbols[i:i + 100]), "timeframe": "1Min", "feed": "sip",
                      "adjustment": "split", "start": f"{day}T13:00:00Z", "end": end, "limit": 10000}
            if token:
                params["page_token"] = token
            r = requests.get(spike_pod.DATA_URL, headers=spike_pod._headers(), params=params, timeout=30)
            r.raise_for_status()
            body = r.json()
            for sym, bars in (body.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            token = body.get("next_page_token")
            if not token:
                break
    return out


def _et(bar: dict) -> datetime:
    return datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(ET)


def first_at(bars: list, at: datetime) -> int | None:
    return next((i for i, b in enumerate(bars) if _et(b) >= at), None)


def trade(bars: list, sign: int, entry_at: datetime, exit_at: datetime, stop_pct=None, target_pct=None):
    """(net bps, exit time, why) over one session's bars, or None without an entry bar."""
    i = first_at(bars, entry_at)
    if i is None or _et(bars[i]) >= exit_at:
        return None
    entry = bars[i]["o"]
    stop = entry * (1 - sign * stop_pct / 100) if stop_pct else None
    target = entry * (1 + sign * target_pct / 100) if target_pct else None
    px, when, why = None, None, "exit_by"
    for b in bars[i:]:
        t = _et(b)
        if t >= exit_at:
            px, when = b["o"], t
            break
        if stop is not None:
            if b is not bars[i] and (b["o"] - stop) * sign <= 0:
                px, when, why = b["o"], t, "stop"
                break
            if (b["l"] if sign > 0 else b["h"]) * sign <= stop * sign:
                px, when, why = stop, t, "stop"
                break
        if target is not None:
            if b is not bars[i] and (b["o"] - target) * sign >= 0:
                px, when, why = b["o"], t, "target"
                break
            if (b["h"] if sign > 0 else b["l"]) * sign >= target * sign:
                px, when, why = target, t, "target"
                break
    if px is None:
        px, when = bars[-1]["c"], _et(bars[-1])
    return sign * (px / entry - 1) * 1e4 - 2 * thesis.COST_BPS, when, why


def score_day(date: str, files: list, bars: dict) -> list:
    closes = {}
    for s in files:
        for i in s["close"]:
            closes.setdefault(i, datetime.fromisoformat(s["sealed_at"]).astimezone(ET))
    rows = []
    for s in files:
        sealed = datetime.fromisoformat(s["sealed_at"]).astimezone(ET)
        entry_at = (sealed + timedelta(seconds=59)).replace(second=0, microsecond=0)
        for t in s["theses"]:
            sign = 1 if t["direction"] == "long" else -1
            planned = datetime.fromisoformat(f"{date}T{t['exit_by']}:00").replace(tzinfo=ET)
            by = planned
            if t["id"] in closes and closes[t["id"]] > sealed:
                by = min(by, (closes[t["id"]] + timedelta(seconds=59)).replace(second=0, microsecond=0))
            own = trade(bars.get(t["symbol"], []), sign, entry_at, by, t["stop_pct"], t.get("target_pct"))
            if own is None:
                continue  # no bar to enter on: unscored
            net, out_at, why = own
            if why == "exit_by" and by < planned:
                why = "closed"
            base = lambda sym: trade(bars.get(sym, []), sign, entry_at, out_at + timedelta(seconds=1))  # noqa: E731
            spy = base("SPY")
            rng = random.Random(t["id"])
            draws = [d[0] for d in (base(x) for x in rng.sample([u for u in thesis.UNIVERSE if u != t["symbol"]],
                                                                 thesis.RANDOM_DRAWS)) if d]
            if spy is None or not draws:
                continue
            rand = sum(draws) / len(draws)
            rows.append({"date": date, "id": t["id"], "symbol": t["symbol"], "direction": t["direction"],
                         "confidence": t.get("confidence", ""), "entry_at": entry_at.strftime("%H:%M"),
                         "exit_at": out_at.strftime("%H:%M"), "exit_why": why, "net_bps": round(net, 1),
                         "spy_bps": round(spy[0], 1), "random_bps": round(rand, 1), "vs_spy": round(net - spy[0], 1),
                         "vs_random": round(net - rand, 1), "why": str(t.get("why", ""))[:200]})
    return rows


def score(now: datetime) -> str:
    os.makedirs(DIR, exist_ok=True)
    today = now.astimezone(ET).date().isoformat()
    after_close = now.astimezone(ET).hour >= 17
    days = sorted({f[:10] for f in os.listdir(DIR) if f.endswith(".json")})
    done = {r["date"] for r in csv.DictReader(open(SCORES_CSV))} if os.path.exists(SCORES_CSV) else set()
    new = []
    for d in days:
        if d in done or (d == today and not after_close) or d > today:
            continue
        files = [json.load(open(os.path.join(DIR, f))) for f in sorted(os.listdir(DIR)) if f.startswith(d)]
        if not any(f["theses"] for f in files):
            continue
        end = min(now - timedelta(minutes=20), datetime.fromisoformat(f"{d}T23:00:00+00:00"))
        bars = minute_bars(sorted(set(thesis.UNIVERSE) | {"SPY"}), d, end.strftime("%Y-%m-%dT%H:%M:%SZ"))
        new += score_day(d, files, bars)
    if new:
        first = not os.path.exists(SCORES_CSV)
        with open(SCORES_CSV, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FIELDS)
            if first:
                w.writeheader()
            w.writerows(new)
    rows = list(csv.DictReader(open(SCORES_CSV))) if os.path.exists(SCORES_CSV) else []
    summary = thesis.summarize([{**r, "stopped": int(r["exit_why"] == "stop")} for r in rows])
    with open(SUMMARY_JSON, "w") as f:
        json.dump({"at": now.isoformat(), **summary}, f, indent=1)
    return f"intraday: {len(new)} new, {len(rows)} scored so far: {json.dumps(summary)}"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    now = datetime.now(ET)
    if cmd == "seal":
        if text := seal(now):
            print(f"{now:%Y-%m-%d %H:%M} {text}", flush=True)
    elif cmd == "score":
        print(score(now), flush=True)
    else:
        sys.exit(__doc__)
