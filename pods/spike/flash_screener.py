"""The flash desk's screener: wakes Claude only when a name has news AND the tape reacts.

Owner, 2026-11-10: rather than Claude looking three times a day, a program watches every stock and calls
Claude in when something happens. Sized on cached 2026 data first (research/journal.md, trigger replay):
every headline or volume burst would be ~390 flags a day, and their obvious read has nothing left by the
time a session can act, so this flags only what a person would stop for: a single-name headline (3 or
fewer symbols) during 09:35-15:30 ET, then, within the headline's minute and the 2 after it, a 1-minute
IEX bar with volume >= VOL_X times the name's mean over the 30 minutes before (minutes with no IEX trade
count as 0) that also reaches MOVE_PCT from the last close before the headline. Volume alone gave 25+ a
day on IEX (mostly price-target notes nobody traded); with the 0.3% move, replays of 11/05, 11/06 and
11/09 gave 8, 1 and 5. One flag per name per COOLDOWN_MIN; at most DAILY_CAP a day.

Each flag is one JSON line in STATE_DIR/world/flags/<date>.jsonl: the headline(s), the numbers, the last
30 minutes of bars, and SPY/QQQ. Vision (jarvis ops/stratton_flash.py) reads it from /spike, asks Haiku
whether it's worth a look, and only then starts a Claude session (research/theses/flash.md). IEX bars:
real trades, IEX's share of volume only, so ratios compare IEX with IEX. No orders.

Shadow flags (revamp review G6.2, owner's OK 2026-11-13): the same check with no volume or move line by default
(SHADOW_VOL_X, SHADOW_MOVE_PCT = 0: every single-name headline in the window, SHADOW_CAP a day, the same
cooldown; a replay of 11/12 gave 133, against 32 at 1.5x and 0.1%) goes to STATE_DIR/world/flags/shadow/<date>.jsonl with
its own state. Nothing reads them during the session: after the close Vision labels a random sample with both
triage prompts (jarvis ops/stratton_shadow.py) and the bench scores them (intraday_book.py score_shadow), for
the pre-registered triage test T1/T2. The live flags and their cap are unchanged.

Usage: python flash_screener.py   (cron: every minute through the session; the script checks the ET window)
       python flash_screener.py --day 2026-11-09  (replay a past session minute by minute, prints flags)
"""
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from intraday_snapshot import bars, headlines
from spike_pod import ET
from world_recorder import UNIVERSE_FILE, WORLD_DIR

VOL_X, TRAIL, CONFIRM = float(os.environ.get("FLASH_VOL_X", 3.0)), 30, 3   # volume multiple; trailing min; headline min + 2
MOVE_PCT = float(os.environ.get("FLASH_MOVE_PCT", 0.3))   # and the price moved this much from just before the headline
COOLDOWN_MIN, DAILY_CAP = 30, int(os.environ.get("FLASH_CAP", 15))
START, END = 9 * 60 + 35, 15 * 60 + 30
SINGLE_NAME = 3
SHADOW_VOL_X = float(os.environ.get("FLASH_SHADOW_VOL_X", 0))
SHADOW_MOVE_PCT = float(os.environ.get("FLASH_SHADOW_MOVE_PCT", 0))
SHADOW_CAP = int(os.environ.get("FLASH_SHADOW_CAP", 400))
INDEXES = ["SPY", "QQQ"]
FLAG_DIR = os.path.join(WORLD_DIR, "flags")
SHADOW_DIR = os.path.join(FLAG_DIR, "shadow")
UTC = ZoneInfo("UTC")


def minute(t: datetime) -> int:
    return t.hour * 60 + t.minute


def _t(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(ET)


def check(news: list, bar_map: dict, now: datetime, vol_x: float = VOL_X, move_pct: float = MOVE_PCT) -> list:
    """Flags from headlines and closed 1-minute bars ({sym: [bar, ...]}). A headline is decided once its
    CONFIRM minutes have closed, or earlier if one of them already shows the volume."""
    out = []
    for n in news:
        syms = n.get("symbols") or []
        if not 0 < len(syms) <= SINGLE_NAME:
            continue
        at = _t(n["created_at"])
        start = at.replace(second=0, microsecond=0)
        if not START <= minute(at) <= END:
            continue
        for sym in syms:
            bs = [b for b in bar_map.get(sym, []) if _t(b["t"]).date() == at.date()]
            vol = {_t(b["t"]).replace(second=0): b for b in bs}
            trail = [vol.get(start - timedelta(minutes=k), {}).get("v", 0) for k in range(1, TRAIL + 1)]
            mean = sum(trail) / TRAIL
            window = [vol[start + timedelta(minutes=k)] for k in range(CONFIRM) if start + timedelta(minutes=k) in vol]
            pre = [b for b in bs if _t(b["t"]) < start]
            ref = pre[-1]["c"] if pre else None
            hit = next((b for b in window if mean > 0 and b["v"] >= vol_x * mean and ref and
                        max(abs(b["h"] / ref - 1), abs(b["l"] / ref - 1)) * 100 >= move_pct), None)
            closed = now >= start + timedelta(minutes=CONFIRM + 1)   # the last confirming bar has closed
            if hit is None and not closed:
                continue
            out.append({"news_id": n.get("id"), "sym": sym, "headline": (n.get("headline") or "").strip(),
                        "created": at.isoformat(), "hit": hit, "mean": mean, "bars": bs[-TRAIL:]})
    return out


def flag_record(c: dict, prev: dict, idx: dict, now: datetime) -> dict:
    bs, hit = c["bars"], c["hit"]
    last = bs[-1]["c"]
    at = _t(hit["t"])
    pc = prev.get(c["sym"])
    pre = [b for b in bs if _t(b["t"]) < _t(c["created"]).replace(second=0)]
    before = pre[-1]["c"] if pre else bs[0]["o"]
    return {"id": f"{now.date()}-F{at:%H%M}-{c['sym']}", "sym": c["sym"], "flagged_at": now.isoformat(),
            "headline": c["headline"], "headline_at": c["created"], "volume_x": round(hit["v"] / c["mean"], 1),
            "last": last, "day_pct": round((last / pc - 1) * 100, 2) if pc else None,
            "since_headline_pct": round((last / before - 1) * 100, 2),
            "index": {s: round((idx[s] / prev[s] - 1) * 100, 2) for s in INDEXES if s in idx and prev.get(s)},
            "bars": [{"t": _t(b["t"]).strftime("%H:%M"), "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]}
                     for b in bs]}


def prev_closes(names: list, now: datetime, path: str | None) -> dict:
    if path and os.path.exists(path):
        return json.load(open(path))
    daily = bars(names, "1Day", now - timedelta(days=10), now.replace(hour=0, minute=0))
    out = {s: bs[-1]["c"] for s, bs in daily.items() if bs}
    if path:
        with open(path, "w") as f:
            json.dump(out, f)
    return out


def _decide(news: list, bm: dict, now: datetime, state: dict, prev: dict, idx: dict,
            vol_x: float, move_pct: float, cap: int) -> list:
    flags = []
    for c in check([n for n in news if str(n.get("id")) not in state["seen"]], bm, now, vol_x, move_pct):
        state["seen"].append(str(c["news_id"]))
        last = state["last"].get(c["sym"])
        if c["hit"] is None or state["count"] >= cap or \
                (last and now - datetime.fromisoformat(last) < timedelta(minutes=COOLDOWN_MIN)):
            continue
        flags.append(flag_record(c, prev, idx, now))
        state["last"][c["sym"]], state["count"] = now.isoformat(), state["count"] + 1
    return flags


def _load_state(path: str) -> dict:
    return json.load(open(path)) if os.path.exists(path) else {"seen": [], "last": {}, "count": 0}


def _save(directory: str, day: str, flags: list, state: dict) -> None:
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, f"{day}.jsonl"), "a", encoding="utf-8") as f:
        f.writelines(json.dumps(r) + "\n" for r in flags)
    path = os.path.join(directory, f"{day}.state.json")
    with open(path + ".tmp", "w") as f:
        json.dump(state, f)
    os.replace(path + ".tmp", path)


def run(now: datetime, state: dict | None = None, prev: dict | None = None, shadow: dict | None = None,
        shadow_out: list | None = None) -> list:
    """One minute. With state given (a replay), nothing is read from or written to disk; pass shadow (a
    state) and shadow_out (a list) to replay the shadow flags too."""
    names = sorted(set(json.load(open(UNIVERSE_FILE))))
    day = now.date().isoformat()
    live = state is None
    os.makedirs(FLAG_DIR, exist_ok=True)
    if live:
        state = _load_state(os.path.join(FLAG_DIR, f"{day}.state.json"))
        shadow = _load_state(os.path.join(SHADOW_DIR, f"{day}.state.json"))
    since = now - timedelta(minutes=CONFIRM + 3)   # long enough to see a headline through its confirming minutes
    seen = set(state["seen"]) & set(shadow["seen"]) if shadow is not None else set(state["seen"])
    news = [n for n in headlines(names, since, now) if str(n.get("id")) not in seen]
    syms = sorted({s for n in news for s in n.get("symbols") or [] if s in names})
    if not syms:
        return []
    closed_to = now.replace(second=0, microsecond=0)          # only bars that have closed
    bm = bars(syms + INDEXES, "1Min", closed_to - timedelta(minutes=TRAIL + CONFIRM + 8), closed_to - timedelta(seconds=1))
    prev = prev or prev_closes(names + INDEXES, now, os.path.join(FLAG_DIR, f"{day}.prev.json") if live else None)
    idx = {s: bm[s][-1]["c"] for s in INDEXES if bm.get(s)}
    flags = _decide(news, bm, now, state, prev, idx, VOL_X, MOVE_PCT, DAILY_CAP)
    if shadow is not None:
        extra = _decide(news, bm, now, shadow, prev, idx, SHADOW_VOL_X, SHADOW_MOVE_PCT, SHADOW_CAP)
        if shadow_out is not None:
            shadow_out.extend(extra)
        if live:
            _save(SHADOW_DIR, day, extra, shadow)
    if live:
        _save(FLAG_DIR, day, flags, state)
    return flags


def main():
    if "--day" in sys.argv:   # replay a past session minute by minute, to stdout
        d = datetime.fromisoformat(sys.argv[sys.argv.index("--day") + 1]).replace(tzinfo=ET)
        state, shadow, extra = {"seen": [], "last": {}, "count": 0}, {"seen": [], "last": {}, "count": 0}, []
        names = sorted(set(json.load(open(UNIVERSE_FILE))))
        prev = prev_closes(names + INDEXES, d, None)
        for m in range(START, END + CONFIRM + 2):
            for r in run(d.replace(hour=m // 60, minute=m % 60, second=5), state, prev, shadow, extra):
                print(json.dumps({k: v for k, v in r.items() if k != "bars"}), flush=True)
        print(f"shadow flags: {len(extra)}", flush=True)
        return
    now = datetime.now(ET)
    if now.weekday() > 4 or not START <= minute(now) <= END + CONFIRM + 1:
        return
    for r in run(now):
        print(f"{now:%H:%M} flag {r['id']} x{r['volume_x']} {r['headline'][:90]}", flush=True)


if __name__ == "__main__":
    main()
