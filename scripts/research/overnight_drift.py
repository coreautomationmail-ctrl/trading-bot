# scripts/research/overnight_drift.py
"""
Overnight drift (Lou, Polk & Skouras 2019). Pre-registered in research/journal.md
(2026-11-04 overnight-drift).

Each name-night: buy the 15:59 bar's close, sell the next session's 09:30 bar
open, 2 bps/side. Also SPY-hedged (minus SPY's same-night return) and, not
gated, the day part (09:30 open -> 15:59 close). Design = the 62 names;
held-out = the 215 extra names, top 100 by prior-year dollar volume.

Usage: python scripts/research/overnight_drift.py   (cached data only)
"""
import json
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

import numpy as np
import pandas as pd

import backtest_scalp as bt
from backtest_swing import tstat

COST = 2 * 2 / 1e4
YEARS = range(2016, 2027)
CACHE = os.getenv("STRATTON_CACHE") or os.path.join(HERE, "..", ".cache")


def sessions(sym: str) -> pd.DataFrame:
    """One row per full session: open (09:30 bar), close (15:59 bar), dollar volume."""
    out = []
    for y in YEARS:
        if not os.path.exists(os.path.join(CACHE, f"m1_{sym}_{y}.pkl")):
            continue
        df = bt.load_year(sym, y)
        if df.empty:
            continue
        hm = df.index.hour * 60 + df.index.minute
        day = df.index.normalize().tz_localize(None)
        g = pd.DataFrame({"day": day, "hm": hm, "open": df["open"].astype("float64"),
                          "close": df["close"].astype("float64"),
                          "dv": df["close"].astype("float64") * df["volume"]})
        agg = g.groupby("day").agg(first=("hm", "min"), last=("hm", "max"), open=("open", "first"),
                                   close=("close", "last"), dv=("dv", "sum"))
        out.append(agg)
    if not out:
        return pd.DataFrame()
    s = pd.concat(out).sort_index()
    s = s[~s.index.duplicated()]
    s["night"] = np.log(s["open"].shift(-1) / s["close"])        # close of d -> open of next session
    ok = (s["last"] == 959) & (s["first"].shift(-1) == 570)
    s.loc[~ok, "night"] = np.nan
    s["day_ret"] = np.where((s["first"] == 570) & (s["last"] == 959), np.log(s["close"] / s["open"]), np.nan)
    return s


def report(t: pd.DataFrame, col: str, label: str, gate: bool = False, hedged_ok=None):
    x = t[col].to_numpy()
    nightly = t.groupby("day")[col].mean()
    tn, tp = tstat(nightly.to_numpy()), tstat(x)
    yrs = t.groupby(t["day"].dt.year)[col].mean() * 1e4
    early, late = t[t["day"].dt.year <= 2020][col].mean(), t[t["day"].dt.year >= 2021][col].mean()
    pnl = t.groupby("symbol")[col].sum()
    top_sym = pnl.max() / pnl[pnl > 0].sum() if (pnl > 0).any() else np.nan
    k = max(1, len(x) // 100)
    top1 = np.sort(x)[-k:].sum() / x.sum() if x.sum() else np.nan
    line = (f"{label}: n={len(x):,}  mean {x.mean() * 1e4:+.2f} bps (nightly t={tn:+.2f}, per-trade t={tp:+.2f})  "
            f"median {np.median(x) * 1e4:+.1f}  win {(x > 0).mean():.0%}  years+ {(yrs > 0).sum()}/{len(yrs)}  "
            f"16-20 {early * 1e4:+.2f} / 21-26 {late * 1e4:+.2f}  top symbol {top_sym:.0%} ({pnl.idxmax()})  "
            f"top-1% share {top1:.0%}")
    if gate:
        ok = (len(x) >= 300 and x.mean() >= 3e-4 and tn >= 2.5 and (yrs > 0).mean() >= 0.7
              and np.sign(early) == np.sign(late) == 1 and top_sym <= 0.25 and hedged_ok)
        line += f"  -> {'PASS' if ok else 'FAIL'}"
    print("\n" + line, flush=True)
    print("   by year: " + "  ".join(f"{y}:{v:+.1f}" for y, v in yrs.items()), flush=True)
    return x.mean(), tn


def main():
    extra = json.load(open(os.path.join(CACHE, "m1_extra_symbols.json")))
    base = bt.SYMBOLS + bt.WIDE
    extra = [s for s in extra if s not in base]
    data = {}
    for sym in base + extra:
        s = sessions(sym)
        if not s.empty:
            data[sym] = s
    spy = data["SPY"]["night"]
    rows = []
    for sym, s in data.items():
        d = s.reset_index().rename(columns={"index": "day"})
        d["symbol"] = sym
        rows.append(d[["day", "symbol", "night", "day_ret", "dv"]])
    allr = pd.concat(rows, ignore_index=True)
    allr["spy"] = spy.reindex(allr["day"]).to_numpy()
    allr["net"] = allr["night"] - COST
    allr["hedged"] = allr["night"] - allr["spy"] - COST
    allr["day_net"] = allr["day_ret"] - COST
    # held-out membership: top 100 of the extra names by prior-year mean daily dollar volume
    dv = allr.groupby(["symbol", allr["day"].dt.year])["dv"].mean()
    top = set()
    for y in YEARS[1:]:
        prev = dv.xs(y - 1, level=1).reindex(extra).dropna().sort_values(ascending=False)
        top.update((s, y) for s in prev.index[:100])
    allr["yr"] = allr["day"].dt.year
    held_mask = [(s, y) in top for s, y in zip(allr["symbol"], allr["yr"])]
    slices = (("design: the 62", allr[allr["symbol"].isin(base)], True),
              ("held-out: extra top 100", allr[held_mask], False))
    for name, sl, design in slices:
        nt = sl.dropna(subset=["night", "spy"])
        print(f"\n=== {name}: {nt['symbol'].nunique()} names, {nt['day'].nunique()} nights ===", flush=True)
        hm, ht = report(nt, "hedged", "  overnight SPY-hedged, net")
        report(nt, "net", "overnight long, net (gated)" if design else "overnight long, net",
               gate=design, hedged_ok=(hm > 0 and ht >= 2))
        report(nt, "night", "  overnight long, gross")
        dt = sl.dropna(subset=["day_ret"])
        report(dt, "day_net", "  day part 09:30->15:59 long, net (not gated)")
        if design:
            stocks = nt[~nt["symbol"].isin(["SPY", "QQQ", "IWM"])]
            report(stocks, "net", "  overnight long, net, stocks only (no ETFs)")


if __name__ == "__main__":
    main()
