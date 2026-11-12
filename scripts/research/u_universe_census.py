"""U family data check (ideas.md U): can the cache build the less-watched universe?

Cached data only, no network. For each year, counts daily-pool names that pass the
pre-registered liquidity rules (price > $5 on the prior close, median daily dollar
volume > $5M over the PRIOR 60 sessions) and are NOT among the 277 1-min names, then
checks what the cache holds for them: market cap inputs (shares outstanding),
1-min SIP bars, Benzinga headlines. Prints the exact download list for what is missing.

Run: STRATTON_CACHE=... python -I scripts/research/u_universe_census.py
"""
import glob
import json
import os
import re

import pandas as pd

CACHE = os.environ["STRATTON_CACHE"]


def main():
    d = pd.read_pickle(os.path.join(CACHE, "swing_daily_2016-01-01_2026-10-02_pool1000.pkl"))
    d = d.reset_index().drop_duplicates(["timestamp", "symbol"])
    d["dv"] = d["close"] * d["volume"]
    d = d.sort_values(["symbol", "timestamp"])
    g = d.groupby("symbol")
    # prior-60-session median dollar volume and prior close (shifted: no same-day info)
    d["dv60"] = g["dv"].transform(lambda s: s.rolling(60, min_periods=60).median().shift(1))
    d["pclose"] = g["close"].shift(1)
    d["year"] = d["timestamp"].dt.year
    ok = d[(d["pclose"] > 5) & (d["dv60"] > 5e6)]

    m1 = {re.match(r"m1_(.+)_\d{4}\.pkl$", os.path.basename(f)).group(1)
          for f in glob.glob(os.path.join(CACHE, "m1_*_*.pkl"))}
    news = {os.path.basename(f)[:-7] for f in glob.glob(os.path.join(CACHE, "news", "*.csv.gz"))}
    print(f"pool names {d['symbol'].nunique()}, 1-min names {len(m1)}, news names {len(news)}")

    # shares outstanding: any cached XBRL companyfacts?
    facts = [f for f in glob.glob(os.path.join(CACHE, "edgar", "CIK*.json")) if "submissions" not in f]
    with_shares = 0
    for f in facts:
        with open(f) as fh:
            j = json.load(fh)
        if "dei" in j.get("facts", {}) or "us-gaap" in j.get("facts", {}):
            with_shares += 1
    print(f"edgar CIK files {len(facts)}, with XBRL facts (shares outstanding) {with_shares}")

    print("\nyear  pass_liq  not_in_277  of_which_1min  of_which_news  median_dv60_$M")
    all_new = set()
    for y, grp in ok.groupby("year"):
        names = set(grp["symbol"])
        new = names - m1
        all_new |= new
        med = grp[grp["symbol"].isin(new)].groupby("symbol")["dv60"].median().median() / 1e6
        print(f"{y}  {len(names):8d}  {len(new):10d}  {len(new & m1):13d}  {len(new & news):13d}  {med:14.1f}")
    print(f"\nnames ever passing liquidity, not in the 277: {len(all_new)}")
    print(f"  with any 1-min file: {len(all_new & m1)}; with news file: {len(all_new & news)}")

    universe = pd.read_pickle(os.path.join(CACHE, "universe.pkl"))
    etf = set(pd.read_pickle(os.path.join(CACHE, "etf_like.pkl")))
    commonish = [s for s in universe if s not in etf and not re.search(r"[.]|(?<=....)[UWR]$", s)]
    print(f"\nuniverse.pkl symbols {len(universe)}, non-ETF and not unit/warrant/right-like {len(commonish)}")
    print(f"  daily pool covers {len(set(commonish) & set(d['symbol']))} of them (pool = top 1000 by 2016Q1 $vol)")


if __name__ == "__main__":
    main()
