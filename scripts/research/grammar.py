"""
Signal grammar: try a whole family of intraday rules at once, with pass marks raised for
the number of tries and a held-out period nobody looked at. Revamp plan, Phase 4.

Family "zmove" (2026-11-07), 432 rules = every combination of:
  move     k = 5, 15 or 30 minutes: log return over the last k bars, raw or minus SPY's
           (market-relative), in units of the stock's trailing 30-bar 1-min sd x sqrt(k)
  trigger  |z| >= 3 or >= 4
  volume   any, or this bar's volume >= 3 x its trailing 30-bar mean
  side     follow the move, or fade it
  window   signal bar 09:45-10:59, 11:00-13:29 or 13:30-15:00 ET
  hold     15, 30 or 60 minutes
A rule fires on the first qualifying bar after `hold` quiet bars (no overlapping trades per
name), enters at the next bar's open, exits at the close `hold` bars later, same day only.
Costs 2 bps a side. All 277 cached names (top-200 dollar volume lists), 2016-2026.

Pass marks, fixed before the run:
  design 2016-2020:   n >= 1000, mean net >= 3 bps, t of summed daily net >= 3.5
                      (about p < 0.05 after 432 tries)
  held-out 2021-2026: same sign, mean net >= 2 bps, day t >= 2, positive in >= 4 of 6 years
A survivor is a CANDIDATE: the desk pre-registers it and runs the full gate (symbol
concentration, top-1% share) before it goes to the shadow bench.

Resumable: each name's daily sums go to .cache/grammar/<family>/<SYM>.pkl.
Usage: python scripts/research/grammar.py [--workers=2]  then  --report
"""
import os
import pickle
import sys
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor
from itertools import product

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import backtest_scalp as bt  # noqa: E402

CACHE = os.environ.get("STRATTON_CACHE", os.path.join(HERE, "..", ".cache"))
FAMILY = "zmove"
OUT = os.path.join(CACHE, "grammar", FAMILY)
RESULTS = os.path.join(HERE, "..", "..", "research", f"grammar_{FAMILY}.csv")
YEARS = range(2016, 2027)
DESIGN, HELD = range(2016, 2021), range(2021, 2027)
KS, BASES, THRS, VOLS, SIDES, HOLDS = (5, 15, 30), ("raw", "rel"), (3, 4), (0, 3), (1, -1), (15, 30, 60)
WINDOWS = {"open": (9 * 60 + 45, 10 * 60 + 59), "mid": (11 * 60, 13 * 60 + 29), "late": (13 * 60 + 30, 15 * 60)}
COST = 4.0   # bps round trip


def names() -> list[str]:
    import json
    extra = json.load(open(os.path.join(CACHE, "m1_extra_symbols.json")))
    return sorted(set(bt.SYMBOLS + bt.WIDE) | set(extra.get("symbols", extra) if isinstance(extra, dict) else extra))


def load(sym: str, year: int):
    path = os.path.join(CACHE, f"m1_{sym}_{year}.pkl")
    return pd.read_pickle(path).astype("float64") if os.path.exists(path) else None


def first_after_quiet(mask: np.ndarray, hold: int) -> np.ndarray:
    """mask bars that come more than `hold` bars after the previous mask bar."""
    idx = np.arange(len(mask))
    last = np.maximum.accumulate(np.where(mask, idx, -10**9))
    prev = np.concatenate(([-10**9], last[:-1]))
    return mask & (idx - prev > hold)


def run_symbol(sym: str) -> str:
    path = os.path.join(OUT, f"{sym}.pkl")
    if os.path.exists(path):
        return f"{sym} cached"
    days = {}      # (rule, date) -> [sum gross bps, n]
    for year in YEARS:
        df = load(sym, year)
        spy = load("SPY", year)
        if df is None or spy is None or len(df) < 1000:
            continue
        f = bt.features(df)
        close, open_ = f["close"].to_numpy(), f["open"].to_numpy()
        dates = np.asarray(f["date"])
        hm = (f.index.hour * 60 + f.index.minute).to_numpy()
        sd = f["ret_std"].to_numpy()
        vm = (f["volume"] / f["vol_mean"]).to_numpy()
        spy_close = spy["close"].reindex(f.index).ffill().to_numpy()
        n = len(f)
        for k in KS:
            lag = np.full(n, np.nan)
            lag[k:] = close[:-k]
            same = np.zeros(n, bool)
            same[k:] = dates[k:] == dates[:-k]
            r = np.where(same, np.log(close / lag), np.nan)
            slag = np.full(n, np.nan)
            slag[k:] = spy_close[:-k]
            rs = np.where(same, np.log(spy_close / slag), np.nan)
            for base in BASES:
                move = r - rs if base == "rel" else r
                with np.errstate(invalid="ignore", divide="ignore"):
                    z = move / (sd * np.sqrt(k))
                for thr, vol, (wname, (w0, w1)) in product(THRS, VOLS, WINDOWS.items()):
                    m = (np.abs(z) >= thr) & (hm >= w0) & (hm <= w1) & (sd > 0)
                    if vol:
                        m &= vm >= vol
                    m = np.nan_to_num(m, nan=False).astype(bool)
                    for hold in HOLDS:
                        sig = np.flatnonzero(first_after_quiet(m, hold))
                        sig = sig[sig + 1 + hold < n]
                        sig = sig[dates[sig + 1 + hold] == dates[sig]]
                        if not len(sig):
                            continue
                        gross = np.sign(z[sig]) * np.log(close[sig + 1 + hold] / open_[sig + 1]) * 1e4
                        uniq, inv = np.unique(dates[sig], return_inverse=True)
                        sums, counts = np.bincount(inv, gross), np.bincount(inv)
                        for d, g, c in zip(uniq, sums, counts):
                            days[(k, base, thr, vol, wname, hold, d)] = [float(g), int(c)]
    os.makedirs(OUT, exist_ok=True)
    with open(path + ".tmp", "wb") as fh:
        pickle.dump(days, fh)
    os.replace(path + ".tmp", path)
    return f"{sym} {len(days)} rule-days"


def stats(day_rows: list[tuple]) -> dict:
    """day_rows: (date, net sum, n). Per-trade mean, day t, yearly signs."""
    n = sum(r[2] for r in day_rows)
    if n == 0:
        return {"n": 0, "mean": np.nan, "t": np.nan, "years_pos": 0, "years": 0}
    total = sum(r[1] for r in day_rows)
    v = np.array([r[1] for r in day_rows])
    t = v.mean() / (v.std(ddof=1) / np.sqrt(len(v))) if len(v) > 1 and v.std(ddof=1) > 0 else np.nan
    years = defaultdict(float)
    for d, s, _ in day_rows:
        years[d.year] += s
    return {"n": n, "mean": total / n, "t": t, "years_pos": sum(s > 0 for s in years.values()), "years": len(years)}


def report():
    agg = defaultdict(lambda: defaultdict(lambda: [0.0, 0]))   # rule -> date -> [gross, n]
    files = [f for f in os.listdir(OUT) if f.endswith(".pkl")]
    for fname in files:
        with open(os.path.join(OUT, fname), "rb") as fh:
            for (k, base, thr, vol, w, hold, d), (g, c) in pickle.load(fh).items():
                cell = agg[(k, base, thr, vol, w, hold)][d]
                cell[0] += g
                cell[1] += c
    rows = []
    for rule, by_day in agg.items():
        for side in SIDES:
            def split(years):
                return [(d, side * g - COST * c, c) for d, (g, c) in by_day.items() if d.year in years]
            de, ho = stats(split(DESIGN)), stats(split(HELD))
            passed_design = de["n"] >= 1000 and de["mean"] >= 3 and de["t"] >= 3.5
            passed_held = (ho["n"] > 0 and np.sign(ho["mean"]) == np.sign(de["mean"]) and ho["mean"] >= 2
                           and ho["t"] >= 2 and ho["years_pos"] >= 4)
            k, base, thr, vol, w, hold = rule
            rows.append({"rule": f"{'follow' if side > 0 else 'fade'} {base} {k}m |z|>={thr}"
                                 f"{f' vol>={vol}x' if vol else ''} {w} hold {hold}",
                         "d_n": de["n"], "d_mean": round(de["mean"], 2), "d_t": round(de["t"], 2),
                         "h_n": ho["n"], "h_mean": round(ho["mean"], 2), "h_t": round(ho["t"], 2),
                         "h_years_pos": f"{ho['years_pos']}/{ho['years']}",
                         "design": passed_design, "candidate": passed_design and passed_held})
    out = pd.DataFrame(rows).sort_values(["candidate", "design", "d_t"], ascending=False)
    out.to_csv(RESULTS, index=False)
    print(f"{len(files)} names, {len(out)} rules; design passes {int(out['design'].sum())}, "
          f"candidates {int(out['candidate'].sum())} -> {RESULTS}")
    print(out.head(15).to_string(index=False))


def main():
    if "--report" in sys.argv:
        report()
        return
    workers = next((int(a.split("=")[1]) for a in sys.argv if a.startswith("--workers=")), 2)
    syms = names()
    with ProcessPoolExecutor(workers) as ex:
        for i, msg in enumerate(ex.map(run_symbol, syms), 1):
            print(f"[{i}/{len(syms)}] {msg}", flush=True)
    report()


if __name__ == "__main__":
    main()
