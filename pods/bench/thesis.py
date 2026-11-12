"""Thesis pod, virtual (revamp plan part 5): Claude's morning trade theses, sealed before
the open and scored after the close against SPY and random picks.

Each weekday before the open a PC session (Vision's standing order stratton-thesis, which
follows research/theses/agent.md) commits research/theses/<ET date>.json:
  {"theses": [{"id": "2026-11-10-1", "symbol": "NVDA", "direction": "long" | "short",
               "hold_days": 0-5, "stop_pct": 3.0 | null, "confidence": 0.0-1.0,
               "why": "...", "wrong_if": "..."}]}
Entry is the regular-session open of that date; exit is the close hold_days trading days
later (0 = same day); a stop, if given, exits at stop_pct from the entry when a day's
high/low reaches it (a stop and a gap through it fill at the open; same-day ambiguity
counts as stopped). Costs: COST_BPS a side.

  python thesis.py seal    every 5 min before the open: copy that day's file from GitHub
                           into data/theses/sealed/. Only sealed theses are scored, so
                           nothing written after the open counts.
  python thesis.py score   after the close: score every sealed thesis whose exit day has
                           closed, append data/theses_scores.csv, rewrite data/theses_summary.json

Baselines per thesis: the same trade on SPY, and the mean of RANDOM_DRAWS random names from
the 277 (seeded by the thesis id, so a re-score gives the same draw). The plan's exit: after
300 scored theses, the pod must beat both with day t >= 2, or it stops trading virtually and
stays on only as an idea source. Paper data only: no orders, no account endpoints.
"""
import csv
import json
import math
import os
import random
import sys
from datetime import datetime, timedelta

import requests

import registry
import spike_pod
from spike_pod import ET

STATE_DIR = os.getenv("STATE_DIR", "/data")
SEALED_DIR = os.path.join(STATE_DIR, "theses", "sealed")
SCORES_CSV = os.path.join(STATE_DIR, "theses_scores.csv")
SUMMARY_JSON = os.path.join(STATE_DIR, "theses_summary.json")
REPO_FILE = "https://api.github.com/repos/coreautomationmail-ctrl/trading-bot/contents/research/theses/{date}.json?ref=master"
UNIVERSE = json.load(open(os.path.join(os.path.dirname(os.path.abspath(spike_pod.__file__)), "missed_universe.json")))
COST_BPS = 2.0
STOP_SLIP_BPS = 3.0    # a stop becomes a market order in a moving stock: it fills worse than its price (G7)
HIGH_COST_BPS = 5.0    # reported alongside: does a result survive 5 bps a side? (G7)
RANDOM_DRAWS = 20
MAX_THESES, MAX_HOLD = 5, 5
EXIT_N = 300
MIN_DAYS = 60            # the final test needs this many trading days as well as EXIT_N trades
FUTILITY_N = 100         # from here, a desk whose day t vs random is <= 0 stops early (can't make a false pass)
# Inactivity, per desk: (sessions looked at, fewest trades a session on average). Revamp review G3.
INACTIVE = {"thesis": (20, 1.5), "intraday": (15, 1.0), "flash": (40, 0.5)}
FIELDS = ["date", "id", "symbol", "direction", "hold_days", "stop_pct", "confidence", "exit_date", "stopped",
          "net_bps", "spy_bps", "random_bps", "vs_spy", "vs_random", "why", "version", "net_5bps"]


def write_rows(path: str, fields: list, new: list) -> None:
    """Append rows; a file written before a column was added is rewritten with the new header
    (missing values left empty: an empty version reads as 1)."""
    if not new:
        return
    old_fields = next(csv.reader(open(path)), []) if os.path.exists(path) else []
    if old_fields and old_fields != fields:
        rows = list(csv.DictReader(open(path)))
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
            w.writeheader()
            w.writerows(rows)
    first = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if first:
            w.writeheader()
        w.writerows(new)


def valid(t: dict, date: str) -> str:
    """'' if the thesis can be scored, else why not."""
    if t.get("symbol") not in UNIVERSE:
        return f"{t.get('symbol')} isn't in the 277-name universe"
    if t.get("direction") not in ("long", "short"):
        return "direction must be long or short"
    if not isinstance(t.get("hold_days"), int) or not 0 <= t["hold_days"] <= MAX_HOLD:
        return f"hold_days must be 0-{MAX_HOLD}"
    stop = t.get("stop_pct")
    if stop is not None and not (isinstance(stop, (int, float)) and 0.5 <= stop <= 20):
        return "stop_pct must be null or 0.5-20"
    if not str(t.get("id", "")).startswith(date):
        return "id must start with the date"
    return ""


def seal(now: datetime) -> str:
    et = now.astimezone(ET)
    if et.weekday() > 4 or not (8 * 60 <= et.hour * 60 + et.minute < 9 * 60 + 30):
        return "outside the sealing window"
    date = et.date().isoformat()
    r = requests.get(REPO_FILE.format(date=date), headers={"Accept": "application/vnd.github.raw+json"}, timeout=20)
    if r.status_code == 404:
        return f"no theses for {date} yet"
    r.raise_for_status()
    theses = (json.loads(r.text).get("theses") or [])[:MAX_THESES]
    kept = [t for t in theses if not valid(t, date)]
    os.makedirs(SEALED_DIR, exist_ok=True)
    with open(os.path.join(SEALED_DIR, f"{date}.json"), "w") as f:
        json.dump({"sealed_at": now.isoformat(), "prompt_version": int(json.loads(r.text).get("prompt_version") or 1),
                   "theses": kept,
                   "rejected": [{"id": t.get("id"), "why": valid(t, date)} for t in theses if valid(t, date)]}, f, indent=1)
    return f"sealed {len(kept)} of {len(theses)} theses for {date}"


def daily_bars(symbols: list, start: str, end: str) -> dict:
    """SIP daily bars (the close is long past, so the free key may read SIP): {sym: {date: bar}}."""
    out, token = {}, None
    while True:
        params = {"symbols": ",".join(symbols), "timeframe": "1Day", "feed": "sip", "adjustment": "split",
                  "start": start, "end": end, "limit": 10000}
        if token:
            params["page_token"] = token
        r = requests.get(spike_pod.DATA_URL, headers=spike_pod._headers(), params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        for sym, bars in (body.get("bars") or {}).items():
            for b in bars:
                out.setdefault(sym, {})[b["t"][:10]] = b
        token = body.get("next_page_token")
        if not token:
            return out


def trade_bps(days: list, direction: str, stop_pct) -> tuple[float, bool]:
    """Net bps of one trade over its daily bars (entry day first), and whether the stop hit."""
    sign = 1 if direction == "long" else -1
    entry = days[0]["o"]
    exit_px, stopped = days[-1]["c"], False
    if stop_pct:
        level = entry * (1 - sign * stop_pct / 100)
        for i, d in enumerate(days):
            gap_through = i > 0 and (d["o"] - level) * sign <= 0
            if gap_through:
                exit_px, stopped = d["o"], True
                break
            if (d["l"] if sign > 0 else d["h"]) * sign <= level * sign:
                exit_px, stopped = level, True
                break
    return sign * (exit_px / entry - 1) * 1e4 - 2 * COST_BPS - (STOP_SLIP_BPS if stopped else 0), stopped


def score_day(sealed: dict, date: str, sessions: list, bars: dict) -> list:
    """Rows for one sealed day whose exits have all closed; [] if any exit is still open."""
    rows = []
    for t in sealed["theses"]:
        i = sessions.index(date)
        if i + t["hold_days"] >= len(sessions):
            return []
        span = sessions[i:i + t["hold_days"] + 1]
        pick = lambda s: [bars.get(s, {}).get(d) for d in span]  # noqa: E731
        own = pick(t["symbol"])
        if None in own:
            continue  # halted or missing data: unscored, and said so in the summary
        net, stopped = trade_bps(own, t["direction"], t.get("stop_pct"))
        spy = trade_bps(pick("SPY"), t["direction"], None)[0]
        rng = random.Random(t["id"])
        draws = [trade_bps(d, t["direction"], None)[0]
                 for d in (pick(s) for s in rng.sample([u for u in UNIVERSE if u != t["symbol"]], RANDOM_DRAWS))
                 if None not in d]
        rand = sum(draws) / len(draws)
        rows.append({"date": date, "id": t["id"], "symbol": t["symbol"], "direction": t["direction"],
                     "hold_days": t["hold_days"], "stop_pct": t.get("stop_pct") or "",
                     "confidence": t.get("confidence", ""), "exit_date": span[-1], "stopped": int(stopped),
                     "net_bps": round(net, 1), "spy_bps": round(spy, 1), "random_bps": round(rand, 1),
                     "vs_spy": round(net - spy, 1), "vs_random": round(net - rand, 1),
                     "why": str(t.get("why", ""))[:200], "version": sealed.get("prompt_version", 1),
                     "net_5bps": round(net - 2 * (HIGH_COST_BPS - COST_BPS), 1)})
    return rows


def _t(values: list) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    sd = math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))
    return round(mean / (sd / math.sqrt(len(values))), 2) if sd else None


def summarize(rows: list, desk: str = "thesis", sessions: list | None = None, k: int | None = None) -> dict:
    """The running record a desk's session reads, and its registered test (revamp review G1-G3).
    Only the desk's current instruction version is judged; older versions' rows are counted, not used.
    sessions: trades per session, oldest first, for the inactivity check. k: tests registered (bar)."""
    version = registry.current_version(desk)
    older = sum(int(r.get("version") or 1) != version for r in rows)
    rows = [r for r in rows if int(r.get("version") or 1) == version]
    by_day: dict[str, list] = {}
    for r in rows:
        by_day.setdefault(r["date"], []).append(r)
    day_mean = lambda key: [sum(float(r[key]) for r in d) / len(d) for d in by_day.values()]  # noqa: E731
    mean = lambda key, rs=rows: round(sum(float(r[key]) for r in rs) / len(rs), 1) if rs else None  # noqa: E731
    tiers = {}
    for name, lo, hi in (("low", 0, 0.5), ("mid", 0.5, 0.7), ("high", 0.7, 1.01)):
        rs = [r for r in rows if r["confidence"] != "" and lo <= float(r["confidence"]) < hi]
        tiers[name] = {"n": len(rs), "net_bps": mean("net_bps", rs), "vs_random": mean("vs_random", rs)}
    n = len(rows)
    t_spy, t_rand = _t(day_mean("vs_spy")), _t(day_mean("vs_random"))
    need = registry.bar(k)
    looked, least = INACTIVE.get(desk, INACTIVE["thesis"])
    recent = (sessions or [])[-looked:]
    inactive = len(recent) >= looked and sum(recent) / len(recent) < least
    beats = (t_rand or 0) >= need and (desk == "flash" or (t_spy or 0) >= need)
    futility_n = 60 if desk == "flash" else FUTILITY_N
    if n >= EXIT_N and len(by_day) >= MIN_DAYS:
        verdict = "PASS: beats " + ("random picks" if desk == "flash" else "SPY and random picks") if beats else             "FAIL: stop virtual trading; keep as an idea source"
    elif n >= futility_n and (t_rand or 0) <= 0:
        verdict = f"STOPPED: futility, day t vs random {t_rand} after {n} trades; keep as an idea source"
    elif inactive:
        verdict = f"inactive: {sum(recent)} trades in the last {len(recent)} sessions (needs {least:g} a session)"
    else:
        verdict = "running"
    net5 = [float(r["net_5bps"]) for r in rows if r.get("net_5bps") not in ("", None)]
    return {"n": n, "of": EXIT_N, "days": len(by_day), "version": version, "older_versions_n": older,
            "net_bps_at_5bps": round(sum(net5) / len(net5), 1) if net5 else None,
            "bar": need, "inactive": inactive, "net_bps": mean("net_bps"), "vs_spy": mean("vs_spy"),
            "vs_random": mean("vs_random"), "day_t_vs_spy": t_spy, "day_t_vs_random": t_rand,
            "hit_rate": round(sum(float(r["net_bps"]) > 0 for r in rows) / n, 2) if n else None,
            "stopped": sum(int(r["stopped"]) for r in rows), "by_confidence": tiers, "verdict": verdict}


def score(now: datetime) -> str:
    path = lambda d: os.path.join(SEALED_DIR, f"{d}.json")  # noqa: E731
    sealed = {f[:-5]: json.load(open(path(f[:-5]))) for f in sorted(os.listdir(SEALED_DIR)) if f.endswith(".json")} \
        if os.path.isdir(SEALED_DIR) else {}
    todo = sorted(d for d, s in sealed.items() if not s.get("scored_at"))
    today = now.astimezone(ET).date()
    if todo:
        start = todo[0]
        end = (now - timedelta(minutes=20)).isoformat()  # SIP refuses anything in the last 15 minutes
        cal = spike_pod.api("GET", f"/v2/calendar?start={start}&end={(today + timedelta(days=10)).isoformat()}")
        closed = [c["date"] for c in cal if c["date"] < today.isoformat() or now.astimezone(ET).hour >= 17]
        symbols = sorted({t["symbol"] for d in todo for t in sealed[d]["theses"]} | {"SPY"} | set(UNIVERSE))
        bars = {}
        for i in range(0, len(symbols), 100):
            bars.update(daily_bars(symbols[i:i + 100], start, end))
        new = []
        for d in todo:
            if d not in closed:
                continue
            rows = score_day(sealed[d], d, closed, bars)
            if rows or not sealed[d]["theses"] or all(
                    closed.index(d) + t["hold_days"] < len(closed) for t in sealed[d]["theses"]):
                new += rows  # every exit has closed: the day is final, even if a name had no data
                with open(path(d), "w") as f:
                    json.dump({**sealed[d], "scored_at": now.isoformat(), "scored": len(rows)}, f, indent=1)
        write_rows(SCORES_CSV, FIELDS, new)
    rows = list(csv.DictReader(open(SCORES_CSV))) if os.path.exists(SCORES_CSV) else []
    sessions = [len(sealed[d]["theses"]) for d in sorted(sealed)]   # every morning a file was sealed, empty ones too
    summary = summarize(rows, "thesis", sessions)
    with open(SUMMARY_JSON, "w") as f:
        json.dump({"at": now.isoformat(), **summary}, f, indent=1)
    return f"scored {len(rows)} theses so far: {json.dumps(summary)}"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    now = datetime.now(ET)
    if cmd == "seal":
        print(seal(now), flush=True)
    elif cmd == "score":
        print(score(now), flush=True)
    else:
        sys.exit(__doc__)
