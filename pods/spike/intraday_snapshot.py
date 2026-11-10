"""What the market looks like right now, for the intraday desk (research/theses/intraday.md).

Ten minutes before each check-in (10:30, 12:00 and 13:30 ET) writes STATE_DIR/world/<date>-mid-<HHMM>.md: index moves, the 277 names' biggest movers on
the day and in the last 30 minutes, single-name headlines since the last check-in, the spike pod's
shadow signals and open trades today, and a price line for every name. Vision reads it from /spike
and hands it to the desk's Claude session as its prompt. IEX bars (the free live feed): prices are
real trades, volumes are IEX's share only, so relative volume compares IEX with IEX.

Usage: python intraday_snapshot.py auto|1030|1200|1330 [--at "2026-11-09 10:20"] (--at replays a past day, to stdout)
  auto: /etc/cron.d/stratton-intraday runs it every 5 minutes through the US session (the VPS keeps
  Berlin time); it writes the check-in whose snapshot is due 6-10 minutes from now, once.
"""
import csv
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from spike_pod import ET, SHADOW_CSV, STATE_DIR
from world_recorder import DATA, UNIVERSE_FILE, WORLD_DIR, get

CHECKINS = ["1030", "1200", "1330"]
INDEXES = ["SPY", "QQQ", "IWM"]
TOP, RECENT = 15, 10
MAX_HEADLINES = 60
SINGLE_NAME = 3   # a headline tagged to more than this many symbols is a roundup, left out


def bars(symbols: list, timeframe: str, start: datetime, end: datetime | None = None) -> dict:
    out = {}
    for i in range(0, len(symbols), 100):
        token = None
        while True:
            params = {"symbols": ",".join(symbols[i:i + 100]), "timeframe": timeframe, "feed": "iex",
                      "start": start.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                      "limit": 10000, "sort": "asc", "adjustment": "split"}
            if end:
                params["end"] = end.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
            if token:
                params["page_token"] = token
            body = get(f"{DATA}/v2/stocks/bars", **params)
            for sym, bs in (body.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bs)
            token = body.get("next_page_token")
            if not token:
                break
    return out


def headlines(symbols: list, since: datetime, until: datetime) -> list:
    out, token = [], None
    while True:
        params = {"symbols": ",".join(symbols), "start": since.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "end": until.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 50, "sort": "desc"}
        if token:
            params["page_token"] = token
        body = get(f"{DATA}/v1beta1/news", **params)
        out += [n for n in body.get("news") or [] if len(n.get("symbols") or []) <= SINGLE_NAME]
        token = body.get("next_page_token")
        if not token or len(out) >= MAX_HEADLINES:
            return out[:MAX_HEADLINES]


def rows_for(now: datetime) -> list:
    """One dict per name with today's numbers, from IEX daily and 5-minute bars."""
    names = sorted(set(json.load(open(UNIVERSE_FILE))) | set(INDEXES))
    daily = bars(names, "1Day", now - timedelta(days=10))
    opened = now.replace(hour=9, minute=30, second=0, microsecond=0)
    five = bars(names, "5Min", opened, now - timedelta(minutes=5))  # only bars that have closed
    out = []
    for s in names:
        d, f = daily.get(s, []), five.get(s, [])
        prev = [b for b in d if b["t"][:10] < now.date().isoformat()]
        if not prev or not f:
            continue
        pc, o, last = prev[-1]["c"], f[0]["o"], f[-1]["c"]
        ago = [b for b in f if b["t"] <= (now - timedelta(minutes=30)).astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")]
        hi, lo = max(b["h"] for b in f), min(b["l"] for b in f)
        frac = max((now - opened).total_seconds() / 23400, 0.05)
        vol = sum(b["v"] for b in f)
        out.append({"sym": s, "last": last, "day": (last / pc - 1) * 100, "open": (last / o - 1) * 100,
                    "m30": (last / ago[-1]["c"] - 1) * 100 if ago else 0.0,
                    "range": (last - lo) / (hi - lo) * 100 if hi > lo else 50.0,
                    "rvol": vol / frac / prev[-1]["v"] if prev[-1]["v"] else 0.0,
                    "gap": (o / pc - 1) * 100})
    return out


def line(r: dict) -> str:
    return (f"| {r['sym']} | {r['last']:.2f} | {r['day']:+.1f}% | {r['gap']:+.1f}% | {r['open']:+.1f}% | {r['m30']:+.2f}% "
            f"| {r['range']:.0f} | {r['rvol']:.1f}x |")


HEAD = "| sym | last | day | gap | since open | last 30m | day range % | rel vol |\n|---|---|---|---|---|---|---|---|"


def build(now: datetime, checkin: str) -> str:
    rows = rows_for(now)
    by = {r["sym"]: r for r in rows}
    stocks = [r for r in rows if r["sym"] not in INDEXES]
    if len(stocks) < 50:
        return ""  # market closed (holiday) or the feed failed: no check-in
    i = CHECKINS.index(checkin)
    since = now.replace(hour=4, minute=0, second=0, microsecond=0) if i == 0 else \
        now.replace(hour=int(CHECKINS[i - 1][:2]), minute=int(CHECKINS[i - 1][2:]), second=0, microsecond=0) - timedelta(minutes=10)
    out = [f"# STRATTON intraday snapshot {now.date()} {now:%H:%M} ET (check-in {checkin[:2]}:{checkin[2:]})", "",
           "IEX bars. day = vs yesterday's close; gap = open vs yesterday's close; day range % = where the last price "
           "sits between today's low (0) and high (100); rel vol = today's IEX volume, scaled to a full day, over "
           "yesterday's.", "", "## Indexes", HEAD, *[line(by[s]) for s in INDEXES if s in by], ""]
    for title, key, rev in (("Biggest gainers today", "day", True), ("Biggest losers today", "day", False)):
        out += [f"## {title}", HEAD, *[line(r) for r in sorted(stocks, key=lambda r: r[key], reverse=rev)[:TOP]], ""]
    out += ["## Biggest moves in the last 30 minutes", HEAD,
            *[line(r) for r in sorted(stocks, key=lambda r: abs(r["m30"]), reverse=True)[:RECENT]], ""]
    try:
        news = headlines([r["sym"] for r in stocks], since, now)
        out += [f"## Single-name headlines since {since:%H:%M} ET (newest first, {len(news)})"]
        for n in news:
            t = datetime.fromisoformat(n["created_at"].replace("Z", "+00:00")).astimezone(ET)
            out.append(f"- {t:%H:%M} {','.join(n.get('symbols') or [])}: {n.get('headline', '').strip()}")
    except Exception as e:
        out.append(f"## Headlines unavailable: {type(e).__name__}: {e}"[:200])
    day = now.date().isoformat()
    shadow = [r for r in csv.DictReader(open(SHADOW_CSV))] if os.path.exists(SHADOW_CSV) else []
    shadow = [r for r in shadow if r["date"] == day]
    state = json.load(open(os.path.join(STATE_DIR, "spike_state.json"))) if os.path.exists(
        os.path.join(STATE_DIR, "spike_state.json")) else {}
    pending = [s for s in state.get("shadow", []) if state.get("day") == day]
    out += ["", "## Spike pod today (volume >= 8x and a 5-sigma 1-minute bar; real paper trades only 13:00-14:54)",
            f"- settled shadow signals: " + ("; ".join(f"{r['et']} {r['symbol']} {'long' if r['side'] == '1' else 'short'} "
                                                     f"{float(r['bps']):+.0f} bps" for r in shadow) or "none"),
            f"- shadow signals still running: " + ("; ".join(f"{s['et']} {s['symbol']} {'long' if s['side'] > 0 else 'short'}"
                                                         for s in pending) or "none"),
            f"- open paper trades: " + ("; ".join(f"{t.get('symbol')} {t.get('side')}" for t in state.get("open", [])) or "none"),
            f"- realized today: ${state.get('realized_today', 0):+.0f}" if state.get("day") == day else "- realized today: $0"]
    out += ["", "## Every name (sym last day% since-open% 30m%)",
            " ".join(f"{r['sym']} {r['last']:.2f} {r['day']:+.1f} {r['open']:+.1f} {r['m30']:+.1f};" for r in stocks)]
    return "\n".join(out) + "\n"


def main():
    checkin = sys.argv[1] if len(sys.argv) > 1 else ""
    if checkin == "auto":
        now = datetime.now(ET)
        m = now.hour * 60 + now.minute
        due = [c for c in CHECKINS if 6 <= int(c[:2]) * 60 + int(c[2:]) - m <= 10]
        if not due or os.path.exists(os.path.join(WORLD_DIR, f"{now.date()}-mid-{due[0]}.md")):
            return
        checkin = due[0]
    if checkin not in CHECKINS:
        sys.exit(__doc__)
    at = sys.argv[sys.argv.index("--at") + 1] if "--at" in sys.argv else ""
    if at:
        print(build(datetime.fromisoformat(at).replace(tzinfo=ET), checkin))
        return
    now = datetime.now(ET)
    text = build(now, checkin) if now.weekday() <= 4 else ""
    if not text:
        print(f"{now:%H:%M} no snapshot (market closed or no data)", flush=True)
        return
    os.makedirs(WORLD_DIR, exist_ok=True)
    path = os.path.join(WORLD_DIR, f"{now.date()}-mid-{checkin}.md")
    with open(path + ".tmp", "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(path + ".tmp", path)  # Vision never reads half a file
    print(f"{now:%H:%M} wrote {path} ({os.path.getsize(path)} bytes)", flush=True)


if __name__ == "__main__":
    main()
