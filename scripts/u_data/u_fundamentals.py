"""Quarterly fundamentals for the factor composite (journal 2026-11-14 factor-composite), from SEC XBRL frames:
us-gaap NetIncomeLoss (3-month duration frames CY{y}Q{q}) and StockholdersEquity (instant frames CY{y}Q{q}I),
2015-2026, one call per concept-quarter. Each value counts as known 90 days after its period end, the same rule as
shares in u_stage1.py, so no day sees a number before it was public.

Output: .cache/u/fundamentals.csv.gz (cik,end,concept,val). Usage: python scripts/u_data/u_fundamentals.py
"""
import csv
import gzip
import os
import time
from datetime import date

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "..", ".cache", "u", "fundamentals.csv.gz")
UA = "CoreAutomationAI-research/1.0"   # generic client name (no personal contact details)
CONCEPTS = [("NetIncomeLoss", "USD", ""), ("StockholdersEquity", "USD", "I")]


def main() -> None:
    rows = []
    for concept, unit, inst in CONCEPTS:
        for year in range(2015, date.today().year + 1):
            for q in (1, 2, 3, 4):
                url = f"https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/{unit}/CY{year}Q{q}{inst}.json"
                for attempt in range(5):
                    r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
                    time.sleep(0.15)   # SEC asks for <= 10 requests a second
                    if r.status_code in (429, 500, 502, 503):
                        time.sleep(2 ** attempt)
                        continue
                    break
                if r.status_code == 404:
                    continue
                r.raise_for_status()
                data = r.json().get("data", [])
                rows += [(x["cik"], x["end"], concept, x["val"]) for x in data]
                print(f"{concept} CY{year}Q{q}{inst}: {len(data)}", flush=True)
    with gzip.open(OUT, "wt", newline="") as f:
        w = csv.writer(f)
        w.writerow(["cik", "end", "concept", "val"])
        w.writerows(rows)
    print(f"{len(rows)} rows -> {OUT}")


if __name__ == "__main__":
    main()
