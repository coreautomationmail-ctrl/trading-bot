"""swarm-replay-v1 data: Alpaca SIP 1-minute bars for the registered period and universe, cached
one gzip CSV per session (09:30-16:00 ET), plus split-adjusted daily bars for prior closes and
SPY's open-to-close. Split-adjusted so a split day does not show up as a fake gap.

Usage: python replay_fetch.py OUT_DIR     (resumable: months already complete are skipped)
"""
import csv
import gzip
import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime

import requests

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "spike"))
import spike_pod  # noqa: E402

FIRST, LAST = date(2022, 1, 3), date(2026, 11, 13)
URL = "https://data.alpaca.markets/v2/stocks/bars"
OPEN_HM, CLOSE_HM = 9 * 60 + 30, 16 * 60


def _load_env():
    path = os.path.join(os.path.dirname(__file__), "..", "..", ".env")
    for line in open(path, encoding="utf-8"):
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _get(params: dict) -> dict:
    for attempt in range(8):
        r = requests.get(URL, headers=spike_pod._headers(), params=params, timeout=60)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(5 * (attempt + 1))
            continue
        r.raise_for_status()
        time.sleep(0.32)                     # 200 requests a minute
        return r.json()
    r.raise_for_status()


def fetch(symbols, start: str, end: str, timeframe: str) -> dict:
    out, token = {}, None
    while True:
        p = {"symbols": ",".join(symbols), "timeframe": timeframe, "feed": "sip", "adjustment": "split",
             "start": start, "end": end, "limit": 10000, "sort": "asc"}
        if token:
            p["page_token"] = token
        body = _get(p)
        for s, bars in (body.get("bars") or {}).items():
            out.setdefault(s, []).extend(bars)
        token = body.get("next_page_token")
        if not token:
            return out


def months():
    y, m = FIRST.year, FIRST.month
    while (y, m) <= (LAST.year, LAST.month):
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def main(out_dir: str):
    _load_env()
    os.makedirs(out_dir, exist_ok=True)
    syms = list(spike_pod.SHADOW)
    done_path = os.path.join(out_dir, "months_done.json")
    done = json.load(open(done_path)) if os.path.exists(done_path) else []

    lock = threading.Lock()
    if not os.path.exists(os.path.join(out_dir, "daily.json")):
        _daily(syms, out_dir)
    with ThreadPoolExecutor(4) as ex:
        list(ex.map(lambda ym: _month(ym, syms, out_dir, done, done_path, lock), [ym for ym in months()
                                                                                  if f"{ym[0]}-{ym[1]:02d}" not in done]))


def _daily(syms, out_dir):
    daily = fetch(syms, "2021-12-01", LAST.isoformat() + "T23:59:59Z", "1Day")
    with open(os.path.join(out_dir, "daily.json"), "w") as f:
        json.dump({s: [{"t": b["t"], "o": b["o"], "c": b["c"]} for b in bars] for s, bars in daily.items()}, f)
    print(f"daily: {sum(len(v) for v in daily.values())} bars, {len(daily)} symbols", flush=True)



def _month(ym, syms, out_dir, done, done_path, lock):
    y, m = ym
    tag = f"{y}-{m:02d}"
    if True:
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        t0 = time.time()
        bars = fetch(syms, f"{tag}-01T13:00:00Z", f"{ny}-{nm:02d}-01T00:00:00Z", "1Min")
        by_day = {}
        for s, bs in bars.items():
            for b in bs:
                et = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(spike_pod.ET)
                hm = et.hour * 60 + et.minute
                d = et.date()
                if not (OPEN_HM <= hm < CLOSE_HM) or d < FIRST or d > LAST:
                    continue
                by_day.setdefault(d.isoformat(), []).append(
                    {"t": b["t"], "symbol": s, "o": b["o"], "h": b["h"], "l": b["l"], "c": b["c"], "v": b["v"]})
        for d, rows in by_day.items():
            rows.sort(key=lambda r: (r["t"], r["symbol"]))
            with gzip.open(os.path.join(out_dir, f"{d}.csv.gz"), "wt", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["t", "symbol", "o", "h", "l", "c", "v"])
                w.writeheader()
                w.writerows(rows)
        with lock:
            done.append(tag)
            json.dump(done, open(done_path, "w"))
        print(f"{tag}: {len(by_day)} sessions, {sum(len(r) for r in by_day.values())} bars, "
              f"{time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
