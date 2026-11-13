"""U family data, stage 2: Benzinga headlines (Alpaca news API) for every name in the universe, per year
(journal 2026-11-12 u-family; U1 and U2 need a headline's created_at, which must be before the entry).
Same pacing as stage 1 (u_stage1.pace: the pod's data key, 100 requests a minute, nothing in market hours).

Output in OUT: news/<year>.csv.gz with created_at,symbols,headline (only the headline: no article text),
one row per headline that names at least one universe symbol of that year. Resumable per year.
Usage: python u_stage2.py   (reads OUT/universe_days.csv.gz)
"""
import csv
import gzip
import os
import sys
from collections import defaultdict

import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import u_stage1 as s1  # noqa: E402

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"
BATCH = 50   # symbols a request


def fetch(symbols: list, start: str, end: str) -> list:
    rows, token = [], None
    while True:
        params = {"symbols": ",".join(symbols), "start": start, "end": end, "limit": 50,
                  "include_content": "false", "sort": "asc"}
        if token:
            params["page_token"] = token
        s1.pace()
        body = s1.get(NEWS_URL, headers=s1.alpaca_headers(), params=params).json()
        for n in body.get("news") or []:
            rows.append((n["created_at"], " ".join(n.get("symbols") or []), " ".join(n.get("headline", "").split())))
        token = body.get("next_page_token")
        if not token:
            return rows


def main():
    out_dir = os.path.join(s1.OUT, "news")
    os.makedirs(out_dir, exist_ok=True)
    by_year = defaultdict(set)
    with gzip.open(os.path.join(s1.OUT, "universe_days.csv.gz"), "rt") as f:
        for r in csv.DictReader(f):
            by_year[r["date"][:4]].add(r["symbol"])
    for year in sorted(by_year):
        path = os.path.join(out_dir, f"{year}.csv.gz")
        if os.path.exists(path):
            continue
        names = sorted(by_year[year])
        start, end = f"{year}-01-01T00:00:00Z", min(f"{year}-12-31T23:59:59Z", s1.END)
        seen, rows = set(), []
        for i in range(0, len(names), BATCH):
            for row in fetch(names[i:i + BATCH], start, end):
                key = (row[0], row[2])
                if key not in seen:   # a headline naming two batched symbols comes back twice
                    seen.add(key)
                    rows.append(row)
        rows.sort()
        tmp = path + ".tmp"
        with gzip.open(tmp, "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["created_at", "symbols", "headline"])
            w.writerows(rows)
        os.replace(tmp, path)
        print(f"{year}: {len(names)} names, {len(rows)} headlines", flush=True)


if __name__ == "__main__":
    main()
