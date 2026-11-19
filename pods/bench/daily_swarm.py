"""STRATTON swarm, daily population (docs/swarm-plan.md section 2, fixed 2026-11-17).

Runs once after each US close, from the bench loop. Each bot looks at daily IEX bars up to today's
close and opens virtual positions: entered at the next open and closed at the close `hold` trading
days later, counting the entry day as day 1. The `night` family is the exception: in at today's
close, out at the next open. A bot holds at most one position per symbol at a time.

Closed positions are appended to STATE_DIR/swarm_daily.csv, dated by their exit day, at 2 bps a
side. Pure function of the bars: the state holds only what is still open and the last day run.
"""
import json
import math
import os
from datetime import datetime, timedelta, timezone

import requests

import spike_pod

STATE_DIR = spike_pod.STATE_DIR
STATE_FILE = os.path.join(STATE_DIR, "swarm_daily_state.json")
TRADES_CSV = os.path.join(STATE_DIR, "swarm_daily.csv")
FIELDS = ["strategy", "date", "symbol", "side", "entry_date", "entry_px", "exit_px", "gross_bps", "net_bps"]
COST_BPS = 2.0
HISTORY_DAYS = 300          # calendar days of daily bars fetched (the longest lookback is 120 trading days)
RUN_AFTER_ET = 17 * 60      # today's daily bar is complete well before 17:00 ET
UNIVERSE = spike_pod.SHADOW


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


class DailyBot:
    """signal(closes) -> +1 / -1 / 0 from closes up to and including today. A bot with CROSS set
    instead gives signals({sym: closes}) -> {sym: side} for its whole universe at once (rankings).
    UNIVERSE, when set, limits the symbols it sees (swarm-v2's ETF bots)."""
    NAME = ""
    HOLD = 1
    NIGHT = False
    CROSS = False
    UNIVERSE = None

    def signal(self, closes: list) -> int:
        return 0


class Mom(DailyBot):
    def __init__(self, n: int, sides: str, hold: int):
        self.n, self.sides, self.HOLD = n, sides, hold
        self.NAME = f"sw-dmom-{sides}-{n}-h{hold}"

    def signal(self, closes):
        if len(closes) <= self.n:
            return 0
        side = _sign(closes[-1] / closes[-1 - self.n] - 1)
        return side if self.sides == "both" or side > 0 else 0


class Rev(DailyBot):
    def __init__(self, n: int, x: float, hold: int, skip_runs: bool = False):
        self.n, self.x, self.HOLD, self.skip_runs = n, x, hold, skip_runs
        self.NAME = f"sw-{'drev3' if skip_runs else 'drev'}-{n}-{x:g}-h{hold}"

    def signal(self, closes):
        if len(closes) <= max(self.n, 3):
            return 0
        ret = closes[-1] / closes[-1 - self.n] - 1
        if abs(ret) * 100 <= self.x:
            return 0
        side = -_sign(ret)
        if self.skip_runs:
            run = _sign(closes[-1] / closes[-4] - 1)   # the 3-day run, from the AI Pathways filter
            if run == side:
                return 0
        return side


class Brk(DailyBot):
    def __init__(self, n: int, mode: str, hold: int):
        self.n, self.mode, self.HOLD = n, mode, hold
        self.NAME = f"sw-dbrk-{mode}-{n}-h{hold}"

    def signal(self, closes):
        if len(closes) <= self.n:
            return 0
        window = closes[-1 - self.n:-1]
        side = 1 if closes[-1] > max(window) else -1 if closes[-1] < min(window) else 0
        return side if self.mode == "go" else -side


class Night(DailyBot):
    NIGHT = True

    def __init__(self, when: str):
        self.when = when
        self.NAME = f"sw-night-{when}"

    def signal(self, closes):
        if len(closes) < 2:
            return 0
        day = _sign(closes[-1] / closes[-2] - 1)
        return 1 if self.when == "all" or (self.when == "down" and day < 0) or (self.when == "up" and day > 0) else 0


def build() -> list:
    bots = []
    for n in (20, 60, 120):
        for sides in ("long", "both"):
            for hold in (5, 20):
                bots.append(Mom(n, sides, hold))
    for n in (2, 3, 5):
        for x in (2, 4):
            for hold in (1, 3, 5):
                bots.append(Rev(n, x, hold))
    for n in (20, 55):
        for mode in ("go", "fade"):
            for hold in (5, 10, 20):
                bots.append(Brk(n, mode, hold))
    for n in (2, 3):
        for x in (2, 4):
            for hold in (1, 3):
                bots.append(Rev(n, x, hold, skip_runs=True))
    for when in ("all", "down", "up"):
        bots.append(Night(when))
    return bots


ALL = build()
NAMES = {b.NAME for b in ALL}


# ---------- one run ----------

def step(daily: dict, state: dict, today: str, bots: list = None) -> list:
    """Settle what can be settled, then open today's positions. `daily` is {sym: [bar, ...]} with
    bars dated "YYYY-MM-DD" in "d", oldest first, ending today. Returns the closed trades."""
    bots = bots or ALL
    by_name = {b.NAME: b for b in bots}
    closed, still = [], []
    for p in state.get("open", []):
        bars = daily.get(p["symbol"]) or []
        days = [b for b in bars if b["d"] > p["signal_date"]]
        bot = by_name.get(p["strategy"])
        need = 1 if (bot and bot.NIGHT) else p["hold"]
        if bot is None or len(days) < need:
            still.append(p)
            continue
        entry = bars[[b["d"] for b in bars].index(p["signal_date"])]["c"] if bot.NIGHT else days[0]["o"]
        exit_ = days[0]["o"] if bot.NIGHT else days[need - 1]["c"]
        gross = p["side"] * math.log(exit_ / entry) * 1e4
        closed.append({"strategy": p["strategy"], "date": days[need - 1]["d"] if not bot.NIGHT else days[0]["d"],
                       "symbol": p["symbol"], "side": p["side"],
                       "entry_date": p["signal_date"] if bot.NIGHT else days[0]["d"],
                       "entry_px": entry, "exit_px": exit_, "gross_bps": round(gross, 2),
                       "net_bps": round(gross - 2 * COST_BPS, 2)})
    held = {(p["strategy"], p["symbol"]) for p in still}
    fresh = {sym: [b["c"] for b in bars] for sym, bars in daily.items()
             if bars and bars[-1]["d"] == today}          # no bar for today: no new position on stale data
    for bot in bots:
        seen = {s: c for s, c in fresh.items() if bot.UNIVERSE is None or s in bot.UNIVERSE}
        sides = bot.signals(seen) if bot.CROSS else {s: bot.signal(c) for s, c in seen.items()}
        for sym, side in sides.items():
            if side and (bot.NAME, sym) not in held:
                still.append({"strategy": bot.NAME, "symbol": sym, "side": side, "signal_date": today,
                              "hold": bot.HOLD})
    state["open"] = still
    state["last_day"] = today
    return closed


def fetch_daily(symbols: list, end: datetime, days: int = HISTORY_DAYS) -> dict:
    """Daily IEX bars for the last `days` calendar days: {sym: [{"d", "o", "c"}, ...]}."""
    out, token = {}, None
    start = end - timedelta(days=days)
    while True:
        params = {"symbols": ",".join(symbols), "timeframe": "1Day", "feed": "iex", "limit": 10000,
                  "start": start.date().isoformat(), "end": end.isoformat(), "sort": "asc"}
        if token:
            params["page_token"] = token
        r = requests.get(spike_pod.DATA_URL, headers=spike_pod._headers(), params=params, timeout=30)
        r.raise_for_status()
        body = r.json()
        for sym, bars in (body.get("bars") or {}).items():
            out.setdefault(sym, []).extend({"d": _et_day(b["t"]), "o": b["o"], "c": b["c"]}
                                           for b in bars)
        token = body.get("next_page_token")
        if not token:
            return out


def _et_day(t: str) -> str:
    return datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(spike_pod.ET).date().isoformat()


def load_state(path: str = STATE_FILE) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(state: dict, path: str = STATE_FILE):
    with open(path + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(path + ".tmp", path)


def maybe_run(now_et: datetime, append_csv, fetch=fetch_daily) -> int | None:
    """Once per trading day after RUN_AFTER_ET. Returns the number of trades closed, or None
    if it didn't run (too early, already ran, or no bar for today: a holiday or weekend)."""
    today = now_et.date().isoformat()
    if now_et.hour * 60 + now_et.minute < RUN_AFTER_ET:
        return None
    state = load_state()
    if state.get("last_day") == today or state.get("checked") == today:
        return None
    daily = fetch(UNIVERSE, now_et.astimezone(timezone.utc))
    if not any(bars and bars[-1]["d"] == today for bars in daily.values()):
        state["checked"] = today          # market closed today: nothing to do until tomorrow
        save_state(state)
        return None
    closed = step(daily, state, today)
    append_csv(TRADES_CSV, FIELDS, closed)
    save_state(state)
    return len(closed)
