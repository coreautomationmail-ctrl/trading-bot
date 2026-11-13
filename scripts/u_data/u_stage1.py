"""U family data, stage 1 (journal 2026-11-12 PRE-REGISTRATION u-family; runs on the VPS, whose line is
~2,000x the PC's). Stdlib + requests only, so it runs in the stratton-bench image with a small memory cap.

  1. SEC XBRL frames: shares outstanding per company per quarter-end, 2015-2026 (dei
     EntityCommonStockSharesOutstanding, us-gaap CommonStockSharesOutstanding as fallback). One call per
     quarter instead of 4,599 companyfacts files. A value counts as known KNOWN_AFTER_DAYS after its period
     end (10-Q/10-K filing deadlines are 40-90 days), so no day sees a number before it was public.
  2. Daily bars 2016-01-01..today for the desk's 4,599 common-stock symbols (raw prices, so close x shares is
     the market cap of that day), 100 symbols a request, with the paper account's key (not the live pod's).
  3. The universe rule, day by day, exactly as pre-registered: prior close > $5, median daily dollar volume
     over the PRIOR 60 sessions > $5M, market cap on the prior close $300M-$5B, not in the 277 1-min names.

Outputs in OUT (default /data/u): shares.csv.gz, daily/<first letter>.csv.gz, universe_days.csv.gz
(date,symbol,cap,pclose,dv60), census.txt. Resumable: finished letters are skipped.
Survivorship caveat (desk's own): company_tickers.json lists current tickers only.
Usage: python u_stage1.py <symbols.txt> <company_tickers.json> <missed_universe.json>
"""
import bisect
import csv
import gzip
import json
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import date, timedelta

import requests

OUT = os.getenv("U_OUT", "/data/u")
UA = "CoreAutomationAI-research/1.0"   # generic client name (no personal contact details)
KNOWN_AFTER_DAYS = 90
CAP_LO, CAP_HI, PRICE_MIN, DV_MIN, DV_DAYS = 3e8, 5e9, 5.0, 5e6, 60
DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
START = "2016-01-01"
# The free data plan refuses SIP data from the last 15 minutes; a date-only end of "today" counts as recent.
from datetime import datetime as _dt, timezone as _tz  # noqa: E402
END = (_dt.now(_tz.utc) - timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:%SZ")


# The market-data key is the spike pod's (the paper account's key gets 403 on data). So the download
# shares the pod's rate limit: it runs only outside US market hours and at most MAX_PER_MIN requests a minute.
MAX_PER_MIN = 100
_calls: list = []


def alpaca_headers() -> dict:
    return {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}


def market_hours_now() -> bool:
    """09:15-16:15 ET on a weekday: keep out of the live pod's way."""
    from zoneinfo import ZoneInfo
    from datetime import datetime
    et = datetime.now(ZoneInfo("America/New_York"))
    return et.weekday() < 5 and 9 * 60 + 15 <= et.hour * 60 + et.minute <= 16 * 60 + 15


def pace() -> None:
    while market_hours_now():
        time.sleep(300)
    now = time.monotonic()
    while _calls and now - _calls[0] > 60:
        _calls.pop(0)
    if len(_calls) >= MAX_PER_MIN:
        time.sleep(60 - (now - _calls[0]) + 0.1)
    _calls.append(time.monotonic())


def get(url, **kw):
    for attempt in range(6):
        r = requests.get(url, timeout=60, **kw)
        if r.status_code == 429 or r.status_code >= 500:
            time.sleep(2 ** attempt)
            continue
        r.raise_for_status()
        return r
    r.raise_for_status()


def fetch_shares(path: str) -> None:
    if os.path.exists(path):
        return
    rows = []
    for concept in ("dei/EntityCommonStockSharesOutstanding", "us-gaap/CommonStockSharesOutstanding"):
        for year in range(2015, date.today().year + 1):
            for q in (1, 2, 3, 4):
                url = f"https://data.sec.gov/api/xbrl/frames/{concept}/shares/CY{year}Q{q}I.json"
                r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
                time.sleep(0.15)   # SEC asks for <= 10 requests a second
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                for x in r.json().get("data", []):
                    rows.append((x["cik"], x["end"], int(x["val"]), concept.split("/")[0]))
    with gzip.open(path, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cik", "end", "val", "source"])
        w.writerows(rows)
    print(f"shares: {len(rows)} company-quarters", flush=True)


def load_shares(path: str) -> dict:
    """cik -> sorted [(known_from_date, shares)], dei preferred over us-gaap for the same period end."""
    by = defaultdict(dict)
    with gzip.open(path, "rt") as f:
        for r in csv.DictReader(f):
            key = (int(r["cik"]), r["end"])
            if r["source"] == "dei" or key not in by[int(r["cik"])]:
                by[int(r["cik"])][key] = int(r["val"])
    out = {}
    for cik, vals in by.items():
        out[cik] = sorted(((date.fromisoformat(end) + timedelta(days=KNOWN_AFTER_DAYS)).isoformat(), v)
                          for (_, end), v in vals.items() if v > 0)
    return out


def shares_on(series: list, day: str):
    i = bisect.bisect_right(series, (day, float("inf"))) - 1
    return series[i][1] if i >= 0 else None


def fetch_daily(symbols: list) -> dict:
    """{sym: [(date, o, h, l, c, v)]}, raw (unadjusted) daily bars."""
    out = defaultdict(list)
    token = None
    while True:
        params = {"symbols": ",".join(symbols), "timeframe": "1Day", "feed": "sip", "adjustment": "raw",
                  "start": START, "end": END, "limit": 10000}
        if token:
            params["page_token"] = token
        pace()
        body = get(DATA_URL, headers=alpaca_headers(), params=params).json()
        for sym, bars in (body.get("bars") or {}).items():
            out[sym] += [(b["t"][:10], b["o"], b["h"], b["l"], b["c"], b["v"]) for b in bars]
        token = body.get("next_page_token")
        if not token:
            return out


def universe_rows(sym: str, bars: list, series: list | None) -> list:
    """Days on which sym is in the universe, judged only on information before that day."""
    rows, dv = [], []
    for i, (d, o, h, l, c, v) in enumerate(bars):
        if i >= DV_DAYS and series:
            pclose = bars[i - 1][4]
            dv60 = statistics.median(dv[-DV_DAYS:])
            shares = shares_on(series, d)
            cap = shares * pclose if shares else None
            if pclose > PRICE_MIN and dv60 > DV_MIN and cap and CAP_LO <= cap <= CAP_HI:
                rows.append((d, sym, round(cap), pclose, round(dv60)))
        dv.append(c * v)
    return rows


def main():
    symbols_path, tickers_path, missed_path = sys.argv[1:4]
    symbols = [s.strip() for s in open(symbols_path) if s.strip()]
    excluded = set(json.load(open(missed_path)))
    cik_of = {v["ticker"]: int(v["cik_str"]) for v in json.load(open(tickers_path)).values()}
    os.makedirs(os.path.join(OUT, "daily"), exist_ok=True)
    fetch_shares(os.path.join(OUT, "shares.csv.gz"))
    shares = load_shares(os.path.join(OUT, "shares.csv.gz"))
    todo = [s for s in symbols if s not in excluded]
    by_letter = defaultdict(list)
    for s in todo:
        by_letter[s[0]].append(s)
    for letter in sorted(by_letter):
        done_marker = os.path.join(OUT, "daily", f"{letter}.done")
        if os.path.exists(done_marker):
            continue
        uni, n_bars = [], 0
        with gzip.open(os.path.join(OUT, "daily", f"{letter}.csv.gz"), "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["date", "symbol", "o", "h", "l", "c", "v"])
            group = by_letter[letter]
            for i in range(0, len(group), 100):
                bars = fetch_daily(group[i:i + 100])
                for sym, rows in bars.items():
                    rows.sort()
                    n_bars += len(rows)
                    w.writerows((d, sym, o, h, l, c, v) for d, o, h, l, c, v in rows)
                    uni += universe_rows(sym, rows, shares.get(cik_of.get(sym)))
        with gzip.open(os.path.join(OUT, f"universe_{letter}.csv.gz"), "wt", newline="") as f:
            csv.writer(f).writerows(uni)
        open(done_marker, "w").write(str(n_bars))
        print(f"{letter}: {len(by_letter[letter])} symbols, {n_bars} daily bars, {len(uni)} universe name-days", flush=True)
    # merge and census
    per_year = defaultdict(set)
    with gzip.open(os.path.join(OUT, "universe_days.csv.gz"), "wt", newline="") as out:
        w = csv.writer(out)
        w.writerow(["date", "symbol", "cap", "pclose", "dv60"])
        for letter in sorted(by_letter):
            with gzip.open(os.path.join(OUT, f"universe_{letter}.csv.gz"), "rt") as f:
                for r in csv.reader(f):
                    w.writerow(r)
                    per_year[r[0][:4]].add(r[1])
    no_cik = sum(1 for s in todo if s not in cik_of)
    lines = [f"symbols {len(symbols)}, not in the 277: {len(todo)}, without a CIK (no shares, never in universe): {no_cik}",
             "year  names_in_universe"] + [f"{y}  {len(n)}" for y, n in sorted(per_year.items())]
    ever = set().union(*per_year.values()) if per_year else set()
    lines.append(f"names ever in the universe: {len(ever)}")
    open(os.path.join(OUT, "census.txt"), "w").write("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)


if __name__ == "__main__":
    main()
