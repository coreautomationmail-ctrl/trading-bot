"""U family data, stage 3: 1-minute SIP bars, reduced on the VPS to what the pre-registered tests need
(journal 2026-11-12 PRE-REGISTRATION u-family). Raw bars aren't kept: about 1.2 billion of them.

  python u_stage3.py cost   FIRST. The pre-registered cost: 50 universe names drawn at random (seeded) from
                            those in the universe in 2024; over their 2024 universe days, every 10:30-15:00
                            minute with volume below that name-day's median volume gives (high-low)/close/2.
                            Writes OUT/cost.txt: the pooled median in bps and the per-side cost
                            max(10 bps, median). Nothing else is computed until this exists.
  python u_stage3.py bars   Then, per symbol ever in the universe, its 1-min bars over its universe days,
                            reduced to:
                            m1/day/<SYM>.csv  date,o0930,c0944,t_entry,o_entry,c1559 per universe day (U1, U2:
                                              the 09:30 open, the 09:44 close, the first bar at or after 09:50
                                              and its open, the last close at or before 15:59)
                            m1/u3/<SYM>.csv   date,t_signal,side,entry,exit,gross_bps: backtest_scalp's spike
                                              rule at U3's settings (volume >= 8 x the prior-30-bar mean,
                                              |log(close/open)| >= 5 x the prior-30-bar sd, both over the day's
                                              bars only with at least 20 of them, sd > 0), signals 09:35-14:58
                                              on universe days, followed, entry at the open two bars after
                                              the signal (trades(delay=1)), exit at the close 61 bars after,
                                              same day and by 15:59, one trade at a time per name
                            Resumable per symbol (m1/done/<SYM>).

Bars: SIP, raw prices, regular session 09:30-15:59 ET only (as backtest_sip.fetch_bars). Same pacing as
stage 1 (the pod's data key, 100 requests a minute, nothing in market hours). Gross returns only: costs are
applied by the family test, from cost.txt.
Usage: python u_stage3.py cost|bars [--limit N]
"""
import csv
import gzip
import math
import os
import random
import statistics
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import u_stage1 as s1  # noqa: E402

ET = ZoneInfo("America/New_York")
OUT = s1.OUT
M1 = os.path.join(OUT, "m1")
COST_SEED, COST_N, COST_YEAR, COST_FLOOR = 2024, 50, 2024, 10.0
VOL_X, K, HOLD, DELAY, TRAIL, MIN_TRAIL = 8.0, 5.0, 60, 1, 30, 20
SIG_FROM, SIG_TO = 9 * 60 + 35, 14 * 60 + 58


def universe_days() -> dict:
    """{sym: sorted [date, ...]} from stage 1."""
    out = defaultdict(list)
    with gzip.open(os.path.join(OUT, "universe_days.csv.gz"), "rt") as f:
        for r in csv.DictReader(f):
            out[r["symbol"]].append(r["date"])
    return {s: sorted(d) for s, d in out.items()}


def fetch_m1(sym: str, first: str, last: str) -> dict:
    """{date: [(minute, o, h, l, c, v), ...]} in time order, 09:30-15:59 ET only."""
    start = datetime.fromisoformat(first).replace(tzinfo=ET).astimezone(timezone.utc)
    end = min(datetime.fromisoformat(last).replace(tzinfo=ET) + timedelta(days=1), datetime.now(ET)).astimezone(timezone.utc)
    end = min(end, datetime.now(timezone.utc) - timedelta(minutes=20))
    days, token = defaultdict(list), None
    while True:
        params = {"symbols": sym, "timeframe": "1Min", "feed": "sip", "adjustment": "raw", "limit": 10000,
                  "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "end": end.strftime("%Y-%m-%dT%H:%M:%SZ")}
        if token:
            params["page_token"] = token
        s1.pace()
        body = s1.get(s1.DATA_URL, headers=s1.alpaca_headers(), params=params).json()
        for b in (body.get("bars") or {}).get(sym, []):
            t = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
            m = t.hour * 60 + t.minute
            if 9 * 60 + 30 <= m <= 15 * 60 + 59:
                days[t.date().isoformat()].append((m, b["o"], b["h"], b["l"], b["c"], b["v"]))
        token = body.get("next_page_token")
        if not token:
            return days


def quiet_half_spreads(bars: list) -> list:
    if not bars:
        return []
    med = statistics.median(b[5] for b in bars)
    return [(b[2] - b[3]) / b[4] / 2 * 1e4 for b in bars
            if 10 * 60 + 30 <= b[0] <= 15 * 60 and b[5] < med and b[4] > 0]


def cost() -> str:
    path = os.path.join(OUT, "cost.txt")
    if os.path.exists(path):
        return open(path).read()
    uni = universe_days()
    pool = sorted(s for s, ds in uni.items() if any(d.startswith(str(COST_YEAR)) for d in ds))
    sample = random.Random(COST_SEED).sample(pool, COST_N)
    spreads, per_name = [], {}
    for sym in sample:
        ds = [d for d in uni[sym] if d.startswith(str(COST_YEAR))]
        bars = fetch_m1(sym, ds[0], ds[-1])
        mine = [x for d in ds for x in quiet_half_spreads(bars.get(d, []))]
        spreads += mine
        per_name[sym] = round(statistics.median(mine), 2) if mine else None
    med = statistics.median(spreads)
    text = (f"u-family cost (journal 2026-11-12), measured {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC\n"
            f"sample: {COST_N} names in the {COST_YEAR} universe, seed {COST_SEED}; quiet minutes: {len(spreads)}\n"
            f"pooled median half-spread: {med:.2f} bps\nper-side cost: {max(COST_FLOOR, med):.2f} bps\n"
            f"per-name medians: {per_name}\n")
    with open(path, "w") as f:
        f.write(text)
    return text


def u3_trades(day_bars: dict, days: list) -> list:
    """backtest_scalp signals()/trades() at U3's settings over one symbol's bars: one array across days, as
    trades() indexes, with the trailing stats computed within each day."""
    flat = [(d, *b) for d in sorted(day_bars) for b in day_bars[d]]
    allowed = set(days)
    n, out, busy = len(flat), [], -1
    start_of_day = {}
    for i, row in enumerate(flat):
        start_of_day.setdefault(row[0], i)
    for i in range(n):
        d, m, o, h, low, c, v = flat[i]
        if i <= busy or d not in allowed or not SIG_FROM <= m <= SIG_TO or o <= 0 or c <= 0:
            continue
        lo = max(start_of_day[d], i - TRAIL)
        prior = flat[lo:i]
        if len(prior) < MIN_TRAIL:
            continue
        vol_mean = sum(p[6] for p in prior) / len(prior)
        rets = [math.log(p[5] / p[2]) for p in prior if p[2] > 0 and p[5] > 0]
        if len(rets) < MIN_TRAIL:
            continue
        sd = statistics.stdev(rets)
        r = math.log(c / o)
        if not (sd > 0 and v >= VOL_X * vol_mean and abs(r) >= K * sd):
            continue
        j = i + HOLD + DELAY
        if j >= n or flat[j][0] != d or flat[j][1] > 15 * 60 + 59:
            continue
        side = 1 if r > 0 else -1 if r < 0 else 0
        entry, exit_ = flat[i + 1 + DELAY][2], flat[j][5]
        out.append((d, f"{m // 60:02d}:{m % 60:02d}", side, entry, exit_, round(side * math.log(exit_ / entry) * 1e4, 2)))
        busy = j
    return out


def day_rows(day_bars: dict, days: list) -> list:
    rows = []
    for d in days:
        bs = day_bars.get(d) or []
        if not bs:
            continue
        o930 = next((b[1] for b in bs if b[0] <= 9 * 60 + 44), "")
        c944 = next((b[4] for b in reversed(bs) if b[0] <= 9 * 60 + 44), "")
        ent = next((b for b in bs if b[0] >= 9 * 60 + 50), None)
        c1559 = bs[-1][4]
        rows.append((d, o930, c944, f"{ent[0] // 60:02d}:{ent[0] % 60:02d}" if ent else "", ent[1] if ent else "", c1559))
    return rows


def bars(limit: int | None = None) -> str:
    if not os.path.exists(os.path.join(OUT, "cost.txt")):
        sys.exit("run `u_stage3.py cost` first (the cost is fixed before any return)")
    for sub in ("day", "u3", "done"):
        os.makedirs(os.path.join(M1, sub), exist_ok=True)
    uni = universe_days()
    todo = [s for s in sorted(uni) if not os.path.exists(os.path.join(M1, "done", s))]
    done = 0
    for sym in todo[:limit]:
        days = uni[sym]
        by_year = defaultdict(list)
        for d in days:
            by_year[d[:4]].append(d)
        trades, rows = [], []
        for _, ds in sorted(by_year.items()):   # one year at a time keeps memory small
            day_bars = fetch_m1(sym, ds[0], ds[-1])
            trades += u3_trades(day_bars, ds)
            rows += day_rows(day_bars, ds)
        for name, header, data in (("u3", ["date", "t_signal", "side", "entry", "exit", "gross_bps"], trades),
                                   ("day", ["date", "o0930", "c0944", "t_entry", "o_entry", "c1559"], rows)):
            with open(os.path.join(M1, name, f"{sym}.csv"), "w", newline="") as f:
                w = csv.writer(f)
                w.writerow(header)
                w.writerows(data)
        open(os.path.join(M1, "done", sym), "w").close()
        done += 1
        print(f"{datetime.now(timezone.utc):%H:%M} {sym}: {len(rows)} days, {len(trades)} U3 trades "
              f"({len(todo) - done} left)", flush=True)
    return f"bars: {done} symbols this run, {len(todo) - done} left"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    lim = int(sys.argv[sys.argv.index("--limit") + 1]) if "--limit" in sys.argv else None
    if cmd == "cost":
        print(cost(), flush=True)
    elif cmd == "bars":
        print(bars(lim), flush=True)
    else:
        sys.exit(__doc__)
