"""
Fill the 1-min SIP cache (scripts/.cache/m1_<SYM>_<YEAR>.pkl) for the 62
backtest_scalp names, 2016-2026, in parallel. The research desk has no API
keys, so everything it reads must already be cached.

Usage: python scripts/fill_m1_cache.py [--workers=6]
"""
import os
import sys
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import backtest_scalp as bt

YEARS = range(2016, 2027)


def fill(job):
    sym, year = job
    df = bt.load_year(sym, year)
    return f"{sym} {year}: {len(df):,} rows"


def main():
    workers = int(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--workers=")), 6))
    cache = os.path.join(os.path.dirname(__file__), ".cache")
    jobs = [(s, y) for s in bt.SYMBOLS + bt.WIDE for y in YEARS
            if not os.path.exists(os.path.join(cache, f"m1_{s}_{y}.pkl"))]
    print(f"{len(jobs)} symbol-years to fetch with {workers} workers", flush=True)
    with Pool(workers) as pool:
        for i, line in enumerate(pool.imap_unordered(fill, jobs), 1):
            print(f"[{i}/{len(jobs)}] {line}", flush=True)


if __name__ == "__main__":
    main()
