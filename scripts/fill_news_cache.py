"""
Fill the news cache (scripts/.cache/news/<SYM>.csv.gz) with Benzinga headlines
for the 277 names we hold 1-min history for, 2016 on. The research desk has no
API keys, so everything it reads must already be cached.

One worker, under Alpaca's 200 requests/min limit. A finished symbol is skipped
on the next run, so a crash just means running it again.

Usage: python scripts/fill_news_cache.py [--symbols=AAPL,MSFT]
"""
import csv
import gzip
import json
import os
import sys
import time

import requests
from dotenv import load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(HERE, "..", ".env"))
NEWS_DIR = os.path.join(HERE, ".cache", "news")
UNIVERSE_FILE = os.path.join(HERE, "..", "pods", "spike", "missed_universe.json")
URL = "https://data.alpaca.markets/v1beta1/news"
START, PAUSE = "2016-01-01T00:00:00Z", 0.35   # about 170 requests/min


def fetch_symbol(sym: str, headers: dict) -> int:
    path = os.path.join(NEWS_DIR, f"{sym}.csv.gz")
    if os.path.exists(path):
        return -1
    n, token = 0, None
    with gzip.open(path + ".tmp", "wt", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["created_at", "headline"])
        while True:
            params = {"symbols": sym, "start": START, "limit": 50, "sort": "asc", "include_content": "false"}
            if token:
                params["page_token"] = token
            r = requests.get(URL, headers=headers, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(30)
                continue
            r.raise_for_status()
            body = r.json()
            for item in body.get("news") or []:
                w.writerow([item["created_at"], item.get("headline", "")])
                n += 1
            token = body.get("next_page_token")
            time.sleep(PAUSE)
            if not token:
                break
    os.replace(path + ".tmp", path)
    return n


def main():
    arg = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--symbols=")), None)
    with open(UNIVERSE_FILE) as f:
        symbols = arg.split(",") if arg else json.load(f)
    headers = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"], "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}
    os.makedirs(NEWS_DIR, exist_ok=True)
    for i, sym in enumerate(symbols, 1):
        try:
            n = fetch_symbol(sym, headers)
        except Exception as e:   # one bad symbol must not stop the rest; rerun retries it
            print(f"{i}/{len(symbols)} {sym}: ERROR {type(e).__name__}", flush=True)
            continue
        print(f"{i}/{len(symbols)} {sym}: {'cached' if n < 0 else n}", flush=True)


if __name__ == "__main__":
    main()
