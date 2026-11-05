# scripts/research/turn_of_month.py
"""
Turn of the month (Lakonishok & Smidt 1988; McConnell & Xu 2008). Pre-registered
in research/journal.md (2026-11-05 turn-of-month).

Each month: buy the 09:30 open of the last session, sell the close of the 3rd
session of the next month, 5 bps/side. Official score = excess vs the pool over
the same sessions. Second score (not the official gate) = net return minus the
name's same-year mean return over windows that don't touch a turn. Design = the
62 names; held-out = the 215 extra names, top 100 by prior-year dollar volume.

Usage: python scripts/research/turn_of_month.py   (cached data only)
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
from backtest_swing import load_daily, pool_benchmark, pool_symbols, tstat

COST = 2 * 5 / 1e4
YEARS = range(2016, 2027)
CACHE = os.getenv("STRATTON_CACHE") or os.path.join(HERE, "..", ".cache")
WINDOWS = {"4 sessions (last -> 3rd)": (0, 4), "7 sessions (3rd-last -> 3rd)": (-2, 7)}
GAP_HI, GAP_LO = 1.45, 0.68


def sessions(sym: str) -> pd.DataFrame:
    """One row per session: open only if a 09:30 bar exists, last close, dollar volume."""
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
        agg = g.groupby("day").agg(first=("hm", "min"), open=("open", "first"), close=("close", "last"),
                                   dv=("dv", "sum"))
        agg.loc[agg["first"] != 570, "open"] = np.nan
        out.append(agg)
    if not out:
        return pd.DataFrame()
    s = pd.concat(out).sort_index()
    return s[~s.index.duplicated()]


def month_ends(cal: pd.DatetimeIndex) -> np.ndarray:
    """Calendar positions of each month's last session; the unfinished final month is dropped."""
    months = cal.to_period("M")
    ends = np.flatnonzero(months[:-1] != months[1:])
    return ends


def split_like(opens: pd.DataFrame, close: pd.DataFrame) -> pd.DataFrame:
    """The bars are unadjusted: flag overnight gaps big enough to be a split."""
    gap = opens / close.ffill().shift(1)
    return (gap > GAP_HI) | (gap < GAP_LO)


def window_has(bad: np.ndarray, hold: int) -> np.ndarray:
    """True at entry i if an overnight inside i..i+hold-1 is flagged (the gap into i isn't held)."""
    s = pd.Series(bad.astype(float)).rolling(hold - 1).sum().shift(-(hold - 1))
    return (s.fillna(0) > 0).to_numpy()


def clean_benchmark(daily: pd.DataFrame, hold: int) -> pd.Series:
    """pool_benchmark with split-like windows dropped instead of clipped."""
    d = daily.reset_index().drop_duplicates(["timestamp", "symbol"])
    d["day"] = pd.to_datetime(d["timestamp"]).dt.tz_convert("America/New_York").dt.tz_localize(None).dt.normalize()
    o = d.pivot_table(index="day", columns="symbol", values="open").sort_index()
    c = d.pivot_table(index="day", columns="symbol", values="close").reindex(o.index)
    bad = split_like(o, c).to_numpy()
    r = (c.shift(-(hold - 1)) / o - 1).to_numpy(copy=True)
    for j in range(r.shape[1]):
        r[window_has(bad[:, j], hold), j] = np.nan
    return pd.Series(np.nanmean(r, axis=1), index=o.index)


def trades(opens, close, cols, ends, start, hold) -> pd.DataFrame:
    """Open of (month end + start) to close of the session hold-1 later, plus the baseline."""
    cal = close.index
    o, c = opens[cols].to_numpy(), close[cols].to_numpy()
    bad = split_like(opens[cols], close[cols]).to_numpy()
    tom = np.zeros(len(cal), dtype=bool)
    for e in ends:
        tom[max(0, e + start):min(len(cal), e + start + hold)] = True
    rows = []
    for j, sym in enumerate(cols):
        ret = np.full(len(cal), np.nan)
        ret[:len(cal) - hold + 1] = c[hold - 1:, j] / o[:len(cal) - hold + 1, j] - 1
        ret[window_has(bad[:, j], hold)] = np.nan
        touches = np.array([tom[i:i + hold].any() for i in range(len(cal))])
        base = pd.Series(ret[~touches], index=cal[~touches]).dropna()
        base = base.groupby(base.index.year).mean()
        for e in ends:
            i = e + start
            if 0 <= i < len(cal) and not np.isnan(ret[i]) and cal[i].year in base.index:
                rows.append((sym, cal[i], cal[e].to_period("M"), ret[i], base[cal[i].year]))
    return pd.DataFrame(rows, columns=["symbol", "entry", "month", "ret", "base"])


def report(s: pd.DataFrame, col: str, label: str, gate: bool = False):
    s = s.dropna(subset=[col])
    x = s[col].to_numpy()
    yrs = s.groupby(s["entry"].dt.year)[col].mean() * 1e4
    early, late = s[s["entry"].dt.year <= 2020][col].mean(), s[s["entry"].dt.year >= 2021][col].mean()
    tm, tp = tstat(s.groupby("month")[col].mean().to_numpy()), tstat(x)
    pnl = s.groupby("symbol")[col].sum()
    top_sym = pnl.max() / pnl[pnl > 0].sum() if (pnl > 0).any() else np.nan
    k = max(1, len(x) // 100)
    top1 = np.sort(x)[-k:].sum() / x.sum() if x.sum() else np.nan
    line = (f"{label}: n={len(x):,}  mean {x.mean() * 1e4:+.1f} bps (monthly t={tm:+.2f}, per-trade t={tp:+.2f})  "
            f"median {np.median(x) * 1e4:+.1f}  win {(x > 0).mean():.0%}  years+ {(yrs > 0).sum()}/{len(yrs)}  "
            f"16-20 {early * 1e4:+.1f} / 21-26 {late * 1e4:+.1f}  top symbol {top_sym:.0%} ({pnl.idxmax()})  "
            f"top-1% share {top1:.0%}")
    if gate:
        ok = (len(x) >= 300 and x.mean() >= 15e-4 and tm >= 2.5 and (yrs > 0).mean() >= 0.7
              and np.sign(early) == np.sign(late) == 1 and top_sym <= 0.25)
        line += f"  -> {'PASS' if ok else 'FAIL'}"
    print("\n" + line, flush=True)
    print("   by year: " + "  ".join(f"{y}:{v:+.0f}" for y, v in yrs.items()), flush=True)


def scored(tr: pd.DataFrame, bench: pd.Series, clean: pd.Series) -> pd.DataFrame:
    tr = tr.copy()
    tr["net"] = tr["ret"] - COST
    tr["excess"] = tr["ret"] - bench.reindex(tr["entry"]).to_numpy() - COST
    tr["excess_clean"] = tr["ret"] - clean.reindex(tr["entry"]).to_numpy() - COST
    tr["timing"] = tr["net"] - tr["base"]
    return tr


def main():
    extra = json.load(open(os.path.join(CACHE, "m1_extra_symbols.json")))
    base = bt.SYMBOLS + bt.WIDE
    extra = [s for s in extra if s not in base]
    data = {s: x for s, x in ((s, sessions(s)) for s in base + extra) if not x.empty}
    cal = data["SPY"].index
    opens = pd.DataFrame({s: x["open"] for s, x in data.items()}).reindex(cal)
    close = pd.DataFrame({s: x["close"] for s, x in data.items()}).reindex(cal)
    dv = pd.DataFrame({s: x["dv"] for s, x in data.items()}).reindex(cal)
    ends = month_ends(cal)
    print(f"calendar {cal[0].date()}..{cal[-1].date()}, {len(ends)} month ends, {len(data)} names", flush=True)

    daily = load_daily(pool_symbols())
    benches = pool_benchmark(daily, [h for _, h in WINDOWS.values()])
    for h, b in benches.items():
        b.index = b.index.tz_convert("America/New_York").tz_localize(None).normalize()
        benches[h] = b.groupby(level=0).last()
    cleans = {h: clean_benchmark(daily, h) for h in benches}
    both = pd.DataFrame({"pool": benches[4], "clean": cleans[4]}).dropna()
    print(f"pool benchmark, hold 4: raw mean {both['pool'].mean() * 1e4:+.1f} bps, split-filtered "
          f"{both['clean'].mean() * 1e4:+.1f} bps, largest gap {(both['pool'] - both['clean']).abs().max() * 1e4:.0f} bps",
          flush=True)

    yearly_dv = dv.groupby(dv.index.year).mean()
    top = {}
    for y in YEARS[1:]:
        top[y] = set(yearly_dv.loc[y - 1].reindex(extra).dropna().sort_values(ascending=False).index[:100])

    for wname, (start, hold) in WINDOWS.items():
        print(f"\n########## window {wname} ##########", flush=True)
        design = hold == 4
        cols = [s for s in base if s in close.columns]
        tr = scored(trades(opens, close, cols, ends, start, hold), benches[hold], cleans[hold])
        print(f"\n=== design: the 62, {tr['symbol'].nunique()} names, {tr['month'].nunique()} months ===", flush=True)
        report(tr, "excess", "excess vs pool, net (OFFICIAL gate)" if design else "excess vs pool, net", gate=design)
        report(tr, "excess_clean", "  excess vs split-filtered pool, net")
        report(tr, "timing", "timing, net (second score, same thresholds)" if design else "timing, net", gate=design)
        report(tr, "net", "  raw long, net")
        for sym in ("SPY", "QQQ"):
            t1 = tr[tr["symbol"] == sym]
            print(f"\n--- {sym} alone ---", flush=True)
            report(t1, "net", f"  {sym} raw long, net 5 bps/side")
            report(t1, "timing", f"  {sym} timing, net 5 bps/side")
            report(t1, "excess", f"  {sym} excess vs pool, net 5 bps/side")
            t2 = t1.assign(net2=t1["ret"] - 4 / 1e4)
            report(t2, "net2", f"  {sym} raw long, net 2 bps/side")
            t3 = t1.assign(tim2=t1["ret"] - 4 / 1e4 - t1["base"])
            report(t3, "tim2", f"  {sym} timing, net 2 bps/side")

        cols = [s for s in extra if s in close.columns]
        hr = scored(trades(opens, close, cols, ends, start, hold), benches[hold], cleans[hold])
        keep = [s in top.get(d.year, ()) for s, d in zip(hr["symbol"], hr["entry"])]
        hr = hr[keep]
        print(f"\n=== held-out: extra top 100, {hr['symbol'].nunique()} names, {hr['month'].nunique()} months ===",
              flush=True)
        report(hr, "excess", "excess vs pool, net")
        report(hr, "excess_clean", "  excess vs split-filtered pool, net")
        report(hr, "timing", "timing, net")
        report(hr, "net", "  raw long, net")


if __name__ == "__main__":
    main()
