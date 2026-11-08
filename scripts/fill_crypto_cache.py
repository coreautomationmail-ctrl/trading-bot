"""
Fill the crypto cache (scripts/.cache/crypto/m1_<BASE>_<YEAR>.pkl) with Alpaca's 1-min
crypto bars, 2021 on, for the research desk (which has no network or keys). Alpaca's
crypto bars need no API key. Same columns as backtest_scalp.load_year, index in UTC;
crypto trades all day every day, so there are no sessions, and minutes with no trade
have no bar.

A finished year is skipped on the next run; the current year is always refetched.

Usage: python scripts/fill_crypto_cache.py [--symbols=BTC/USD,ETH/USD]
"""
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd
import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, ".cache", "crypto")
URL = "https://data.alpaca.markets/v1beta3/crypto/us/bars"
SYMBOLS = ["BTC/USD", "ETH/USD", "SOL/USD", "DOGE/USD", "LTC/USD", "AVAX/USD", "LINK/USD"]
FIRST_YEAR, PAUSE = 2021, 0.35


def fetch_year(sym: str, year: int) -> pd.DataFrame:
    rows, token = [], None
    end = min(datetime(year + 1, 1, 1, tzinfo=timezone.utc), datetime.now(timezone.utc))
    while True:
        params = {"symbols": sym, "timeframe": "1Min", "start": f"{year}-01-01T00:00:00Z",
                  "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "limit": 10000, "sort": "asc"}
        if token:
            params["page_token"] = token
        for attempt in range(5):
            r = requests.get(URL, params=params, timeout=30)
            if r.status_code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        body = r.json()
        rows += (body.get("bars") or {}).get(sym, [])
        token = body.get("next_page_token")
        time.sleep(PAUSE)
        if not token:
            break
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.index = pd.to_datetime(df["t"], utc=True)
    df = df.rename(columns={"o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"})
    return df[["open", "high", "low", "close", "volume"]].astype("float64")


def main():
    syms = SYMBOLS
    for a in sys.argv[1:]:
        if a.startswith("--symbols="):
            syms = a.split("=", 1)[1].split(",")
    os.makedirs(OUT, exist_ok=True)
    now = datetime.now(timezone.utc).year
    for sym in syms:
        base = sym.split("/")[0]
        for year in range(FIRST_YEAR, now + 1):
            path = os.path.join(OUT, f"m1_{base}_{year}.pkl")
            if os.path.exists(path) and year < now:
                continue
            df = fetch_year(sym, year)
            df.to_pickle(path + ".tmp")
            os.replace(path + ".tmp", path)
            print(f"{sym} {year}: {len(df)} bars", flush=True)


if __name__ == "__main__":
    main()
