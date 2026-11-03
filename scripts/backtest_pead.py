# scripts/backtest_pead.py
"""
Post-earnings-announcement drift on REAL announcement dates (a swing pod
candidate). backtest_swing.py's "drift" used any big move as a proxy and
found no excess return; this one only trades earnings.

Events: SEC EDGAR 8-K filings with item 2.02 ("Results of Operations"),
which companies file when they release earnings. The filing's acceptance
time sets the reaction session: after 16:00 ET -> next session, otherwise
the same session (pre-market and intraday releases).

Signal: EAR = reaction-session close / prior close - 1, minus the
equal-weight pool's return that session (the earnings-announcement-return
drift of Brandt, Kishore, Santa-Clara & Venkatachalam 2008). Trade in EAR's
direction when |EAR| >= EAR_MIN, entering at the open of the session after
the reaction (+DELAY), holding H sessions, exit at the close. Scored as
excess vs the pool over the same sessions (cancels market moves and most of
the survivor bias), 5 bps/side.

Pre-registered PASS (per side, some hold): net excess >= 15 bps/trade,
t >= 2.5, positive in >= 7 of the 10 full years 2016-2025, and the same sign
in 2016-20 and 2021-26.

Usage: python scripts/backtest_pead.py [--ear=0.04] [--delay=0] [--slippage=5]
Data: swing pool daily bars (backtest_swing cache) + EDGAR submissions,
cached in scripts/.cache/edgar/.
"""
import json
import math
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

import numpy as np
import pandas as pd

from backtest_sip import CACHE_DIR
from backtest_swing import load_daily, pool_benchmark, pool_symbols, tstat

CACHE = os.path.join(CACHE_DIR, "edgar")
UA = "STRATTON research stratton-research@core-automation-ai.com"   # SEC requires a contact in the User-Agent
HOLDS = (5, 10, 20, 40, 60)

args = dict(a.lstrip("-").split("=", 1) for a in sys.argv[1:] if "=" in a)
EAR_MIN = float(args.get("ear", 0.04))
DELAY = int(args.get("delay", 0))
SLIP = float(args.get("slippage", 5)) / 1e4


def sec_json(url: str, path: str):
    """GET a SEC JSON file once (curl: this machine's Python CA bundle is stale), cached."""
    if os.path.exists(path):
        with open(path, encoding="utf8") as f:
            return json.load(f)
    time.sleep(0.12)   # SEC fair-access limit is 10 requests/second
    r = subprocess.run(["curl", "-sf", "--compressed", "-A", UA, url], capture_output=True, text=True, encoding="utf8")
    if r.returncode != 0:
        return None
    data = json.loads(r.stdout)
    with open(path, "w", encoding="utf8") as f:
        json.dump(data, f)
    return data


def earnings_filings(syms: list) -> pd.DataFrame:
    """(symbol, accepted ET timestamp) for every 8-K with item 2.02, 2015-2026."""
    os.makedirs(CACHE, exist_ok=True)
    tickers = sec_json("https://www.sec.gov/files/company_tickers.json", os.path.join(CACHE, "company_tickers.json"))
    cik = {v["ticker"].replace("-", "."): int(v["cik_str"]) for v in tickers.values()}
    rows, missing = [], []
    for i, sym in enumerate(syms):
        c = cik.get(sym) or cik.get(sym.replace(".", "-"))
        if not c:
            missing.append(sym)
            continue
        sub = sec_json(f"https://data.sec.gov/submissions/CIK{c:010d}.json", os.path.join(CACHE, f"CIK{c:010d}.json"))
        if not sub:
            missing.append(sym)
            continue
        pages = [sub["filings"]["recent"]]
        for extra in sub["filings"].get("files", []):
            page = sec_json(f"https://data.sec.gov/submissions/{extra['name']}", os.path.join(CACHE, extra["name"]))
            if page:
                pages.append(page)
        for p in pages:
            for form, items, acc in zip(p["form"], p["items"], p["acceptanceDateTime"]):
                if form == "8-K" and "2.02" in items.split(",") and acc[:4] >= "2015":
                    rows.append((sym, acc))
        if i % 100 == 0:
            print(f"  edgar {i}/{len(syms)}", flush=True)
    if missing:
        print(f"no SEC CIK/filings for {len(missing)} symbols (renamed/delisted): {' '.join(missing[:15])}...")
    f = pd.DataFrame(rows, columns=["symbol", "accepted"])
    # acceptanceDateTime is ET wall time with a misleading "Z" suffix
    f["accepted"] = pd.to_datetime(f["accepted"].str.slice(0, 19))
    return f.drop_duplicates()


def events(daily: pd.DataFrame, filings: pd.DataFrame) -> pd.DataFrame:
    d = daily.reset_index().drop_duplicates(["timestamp", "symbol"])
    d["day"] = pd.to_datetime(d["timestamp"]).dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    close = d.pivot_table(index="day", columns="symbol", values="close")
    opens = d.pivot_table(index="day", columns="symbol", values="open")
    pool_ret = (close / close.shift(1) - 1).clip(-0.9, 5).mean(axis=1)
    days = close.index
    rows = []
    for sym, g in filings.groupby("symbol"):
        if sym not in close:
            continue
        c, o = close[sym].to_numpy(), opens[sym].to_numpy()
        seen = set()
        for ts in g["accepted"]:
            after_close = ts.hour * 60 + ts.minute >= 16 * 60
            r = days.searchsorted(ts.normalize(), side="right" if after_close else "left")
            e = r + 1 + DELAY
            if r < 1 or r in seen or e >= len(days) or np.isnan(c[r]) or np.isnan(c[r - 1]) or c[r - 1] < 5:
                continue
            seen.add(r)   # one event per reaction day (amended/duplicate 8-Ks)
            ear = c[r] / c[r - 1] - 1 - pool_ret.iat[r]
            for h in HOLDS:
                x = e + h - 1
                if x < len(days) and not np.isnan(o[e]) and not np.isnan(c[x]):
                    rows.append((sym, days[e], days[r], h, ear, c[x] / o[e] - 1))
    return pd.DataFrame(rows, columns=["symbol", "entry", "reaction", "hold", "ear", "ret"])


def main():
    syms = pool_symbols()
    daily = load_daily(syms)
    filings = earnings_filings(syms)
    print(f"{len(filings):,} earnings 8-Ks for {filings['symbol'].nunique()} of {len(syms)} pool names")
    ev = events(daily, filings)
    ev = ev[ev["ear"].abs() >= EAR_MIN].copy()
    ev["side"] = np.sign(ev["ear"])
    bench = pool_benchmark(daily, HOLDS)
    for h in HOLDS:   # the benchmark is keyed by tz-aware session timestamps; re-key by ET date
        bench[h].index = bench[h].index.tz_convert("America/New_York").tz_localize(None).normalize()
    b = np.array([bench[h].get(d, np.nan) for h, d in zip(ev["hold"], ev["entry"])])
    ev["excess"] = ev["side"] * (ev["ret"] - b) - 2 * SLIP
    ev["net"] = ev["side"] * ev["ret"] - 2 * SLIP
    ev = ev.dropna(subset=["excess"])
    ev["year"] = ev["entry"].dt.year
    print(f"|EAR| >= {EAR_MIN:.0%}, entry = open of reaction+{1 + DELAY}, {SLIP * 1e4:.0f} bps/side; "
          f"{ev[ev['hold'] == HOLDS[0]].shape[0]:,} events")
    for side in (1, -1):
        for h in HOLDS:
            s = ev[(ev["side"] == side) & (ev["hold"] == h)]
            if len(s) < 30:
                continue
            yrs = s.groupby("year")["excess"].mean() * 1e4
            full = yrs[(yrs.index >= 2016) & (yrs.index <= 2025)]
            early, late = s[s["year"] <= 2020]["excess"].mean(), s[s["year"] >= 2021]["excess"].mean()
            t = tstat(s["excess"].to_numpy())
            ok = (s["excess"].mean() >= 0.0015 and t >= 2.5 and (full > 0).sum() >= 7
                  and np.sign(early) == np.sign(late) == 1)
            print(f"\n{'LONG ' if side == 1 else 'SHORT'} hold={h:2d}: n={len(s):5,}  net {s['net'].mean() * 1e4:+6.1f}  "
                  f"excess {s['excess'].mean() * 1e4:+6.1f} bps (t={t:+.1f})  win {(s['excess'] > 0).mean():.0%}  "
                  f"years+ {(full > 0).sum()}/{len(full)}  16-20 {early * 1e4:+.0f} / 21-26 {late * 1e4:+.0f}"
                  f"{'  PASS' if ok else ''}")
            print("   excess by year: " + "  ".join(f"{y}:{v:+.0f}" for y, v in yrs.items()))


if __name__ == "__main__":
    main()
