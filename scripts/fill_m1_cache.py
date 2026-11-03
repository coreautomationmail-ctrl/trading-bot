"""
Fill the 1-min SIP cache (scripts/.cache/m1_<SYM>_<YEAR>.pkl) for the 62
backtest_scalp names, 2016-2026, in parallel (2 workers: more trips Alpaca's
200 requests/min limit). The research desk has no API
keys, so everything it reads must already be cached.

Usage: python scripts/fill_m1_cache.py [--workers=2] [--extra]

  --extra  also the names in .cache/m1_extra_symbols.json (the ~215 others
           that ranked top-200 by dollar volume in 2017, 2021 or 2025), newest
           years first so recent data lands soonest
"""
import json
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest_scalp as bt

YEARS = range(2016, 2027)


def fill(job):
    sym, year = job
    try:
        df = bt.load_year(sym, year)
    except Exception as e:   # one bad symbol-year must not stop the other ~2,000
        return f"{sym} {year}: ERROR {type(e).__name__}: {e}"[:200]
    if df.empty:   # a rate-limited fetch comes back empty; don't let the cache keep it
        os.remove(os.path.join(os.path.dirname(__file__), ".cache", f"m1_{sym}_{year}.pkl"))
        return f"{sym} {year}: EMPTY (not cached; rerun to retry)"
    return f"{sym} {year}: {len(df):,} rows"


def main():
    workers = int(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--workers=")), 2))
    cache = os.path.join(os.path.dirname(__file__), ".cache")
    names = bt.SYMBOLS + bt.WIDE
    if "--extra" in sys.argv:
        with open(os.path.join(cache, "m1_extra_symbols.json")) as f:
            names += json.load(f)
    jobs = [(s, y) for y in reversed(YEARS) for s in names
            if not os.path.exists(os.path.join(cache, f"m1_{s}_{y}.pkl"))]
    print(f"{len(jobs)} symbol-years to fetch with {workers} workers", flush=True)
    with Pool(workers) as pool:
        for i, line in enumerate(pool.imap_unordered(fill, jobs), 1):
            print(f"[{i}/{len(jobs)}] {line}", flush=True)


if __name__ == "__main__":
    main()
