"""
Steady movers (research/journal.md, 2026-11-09 steady-movers, ideas.md E).

At T (12:00 primary; 11:00, 13:00 reported), using bars closing before T:
  R  = log(close at T-1 / open at 09:30), ER = |R| / sum |1-min log steps|.
Each day across eligible names: R in the top or bottom decile AND ER in the top decile -> go with R.
Entry = open of the T+1 bar, exit = open of the 15:55 bar. 2 bps/side.
Gate: n >= 300, net >= 3, per-trade t >= 2.5, day t >= 2.5, >= 70% years positive, same sign 2016-20 /
2021-26, top symbol <= 25%. Replication: rank 1-100 and 101-277 by prior-year dollar volume, same sign, day t >= 2.

Usage: python scripts/research/steady_movers.py [--workers=6]
"""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))

import numpy as np
import pandas as pd

CACHE = os.environ.get("STRATTON_CACHE", os.path.join(HERE, "..", ".cache"))
YEARS = range(2016, 2027)
EARLY = range(2016, 2021)
COST = 4.0
OPEN, EXIT = 570, 955
TIMES = (720, 660, 780)          # 12:00 primary, then 11:00, 13:00
ET = "America/New_York"


def names() -> list[str]:
    import backtest_scalp as bt
    extra = json.load(open(os.path.join(CACHE, "m1_extra_symbols.json")))
    extra = extra.get("symbols", extra) if isinstance(extra, dict) else extra
    return sorted(set(bt.SYMBOLS + bt.WIDE) | set(extra))


def run_name(sym):
    out, dv = [], {}
    for y in YEARS:
        p = os.path.join(CACHE, f"m1_{sym}_{y}.pkl")
        if not os.path.exists(p):
            continue
        df = pd.read_pickle(p).astype("float64")
        if df.empty:
            continue
        df = df[~df.index.duplicated()].sort_index()
        hm = df.index.hour * 60 + df.index.minute
        df = df[(hm >= OPEN) & (hm <= 959)]
        dates = df.index.date
        dv[y] = (df["close"] * df["volume"]).groupby(dates).sum().mean()
        hm = (df.index.hour * 60 + df.index.minute).to_numpy()
        d = pd.Series(dates, index=df.index)
        c, o = df["close"], df["open"]
        prev = c.groupby(dates).shift(1)
        step = np.log(c / prev.fillna(o)).to_numpy()
        g = pd.DataFrame({"d": dates, "hm": hm, "s": step, "a": np.abs(step), "o": o.to_numpy()})
        first = g.groupby("d")["hm"].min()
        ex = g[g["hm"] == EXIT].set_index("d")["o"]
        for T in TIMES:
            pre = g[g["hm"] < T]
            agg = pre.groupby("d").agg(R=("s", "sum"), A=("a", "sum"), k=("s", "size"))
            en = g[g["hm"] == T + 1].set_index("d")["o"]
            agg = agg.join(en.rename("eo"), how="inner").join(ex.rename("xo"), how="inner")
            agg = agg[(first.reindex(agg.index) == OPEN) & (agg["k"] >= 0.9 * (T - OPEN)) & (agg["A"] > 0)]
            ret = np.log(agg["xo"] / agg["eo"]) * 1e4
            for day, R, A, r in zip(agg.index, agg["R"], agg["A"], ret):
                out.append((T, sym, y, day, R, abs(R) / A, r))
    return sym, out, dv


def stats(t: pd.DataFrame, label: str, gated=True, rep=None) -> bool:
    if t.empty:
        print(f"\n== {label}: n=0")
        return False
    x = t["bps"] - COST
    day = x.groupby(t["date"]).sum()
    day_t = day.mean() / day.std(ddof=1) * np.sqrt(len(day))
    tr_t = x.mean() / x.std(ddof=1) * np.sqrt(len(x))
    by = x.groupby(t["year"]).mean()
    syms = x.groupby(t["sym"]).sum()
    share = syms.max() / syms[syms > 0].sum() if (syms > 0).any() else float("nan")
    top = np.sort(x.to_numpy())[::-1][:max(1, len(x) // 100)].sum() / x.sum() if x.sum() else float("nan")
    early, late = x[t["year"].isin(EARLY)].mean(), x[~t["year"].isin(EARLY)].mean()
    print(f"\n== {label}")
    print(f"  n={len(x)} on {len(day)} days, net {x.mean():+.2f} bps (gross {t['bps'].mean():+.2f}; per-trade t {tr_t:+.2f}, "
          f"day t {day_t:+.2f}), win {(x > 0).mean():.0%}, median {x.median():+.1f}, top-1% share {top:+.0%}")
    print("  by year: " + "  ".join(f"{y}:{v:+.1f}/{int(m)}" for (y, v), m in zip(by.items(), t.groupby("year").size())))
    print(f"  2016-20 {early:+.2f}, 2021-26 {late:+.2f}; top symbol {syms.idxmax()} {share:.0%}; "
          f"long {x[t['side'] > 0].mean():+.1f} (n={int((t['side'] > 0).sum())}), "
          f"short {x[t['side'] < 0].mean():+.1f} (n={int((t['side'] < 0).sum())}); at 4 bps/side {(t['bps'] - 8).mean():+.2f}")
    if not gated:
        return False
    checks = {
        "n >= 300": len(x) >= 300,
        "mean net >= 3": x.mean() >= 3,
        "per-trade t >= 2.5": tr_t >= 2.5,
        "day t >= 2.5": day_t >= 2.5,
        "years+ >= 70%": (by > 0).mean() >= 0.7,
        "same sign 2016-20 / 2021-26": early * late > 0,
        "top symbol <= 25%": share <= 0.25,
    }
    if rep is not None:
        for lab, h in rep.items():
            hx = h["bps"] - COST
            hd = hx.groupby(h["date"]).sum()
            ht = hd.mean() / hd.std(ddof=1) * np.sqrt(len(hd)) if len(hd) > 1 else 0.0
            print(f"  replication {lab}: n={len(hx)}, net {hx.mean() if len(hx) else float('nan'):+.2f}, day t {ht:+.2f}")
            checks[f"{lab} same sign, day t >= 2"] = len(hx) > 0 and hx.mean() * x.mean() > 0 and ht >= 2
    for k, v in checks.items():
        print(f"    {'ok  ' if v else 'FAIL'} {k}")
    ok = all(checks.values())
    print(f"  verdict: {'PASS' if ok else 'FAIL'} ({sum(checks.values())}/{len(checks)})")
    return ok


def select(f: pd.DataFrame, use_er=True) -> pd.DataFrame:
    g = f.groupby("date")
    rq = g["R"].rank(pct=True)
    eq = g["ER"].rank(pct=True)
    keep = (rq > 0.9) | (rq <= 0.1)
    if use_er:
        keep &= eq > 0.9
    t = f[keep].copy()
    t["side"] = np.sign(t["R"])
    t = t[t["side"] != 0]
    t["bps"] = t["side"] * t["r"]
    return t


def main():
    workers = int(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--workers=")), 6))
    syms = names()
    rows, dv = [], {}
    with ProcessPoolExecutor(workers) as ex:
        for k, (sym, r, d) in enumerate(ex.map(run_name, syms)):
            rows += r
            dv.update({(sym, y): v for y, v in d.items()})
            if k % 25 == 0:
                print(f"  done {k + 1}/{len(syms)}", flush=True)
    f = pd.DataFrame(rows, columns=["T", "sym", "year", "date", "R", "ER", "r"])
    rank = {}
    for y in YEARS:
        prev = sorted(((dv[(s, y - 1)], s) for s in syms if (s, y - 1) in dv), reverse=True)
        rank.update({(s, y): i + 1 for i, (_, s) in enumerate(prev)})
    f["rank"] = [rank.get((s, y), 0) for s, y in zip(f["sym"], f["year"])]
    print(f"names {len(syms)}; eligible name-days at 12:00 {int((f['T'] == 720).sum())}, "
          f"median names a day {int(f[f['T'] == 720].groupby('date').size().median())}")

    spike = pd.read_csv(os.path.join(CACHE, "spike_wide_trades.csv"))
    spike = spike[spike["rank"] <= 25]
    sday = (spike["bps"] - COST).groupby(pd.to_datetime(spike["entry"], utc=True).dt.tz_convert(ET).dt.date).sum()

    main_t = select(f[f["T"] == 720])
    main_t.to_csv(os.path.join(CACHE, "steady_movers_trades.csv"), index=False)
    rep = {"rank 1-100": main_t[(main_t["rank"] >= 1) & (main_t["rank"] <= 100)],
           "rank 101-277": main_t[main_t["rank"] >= 101]}
    ok = stats(main_t, "PRIMARY 12:00, R decile + ER top decile, all 277, 2016-26", rep=rep)
    d = (main_t["bps"] - COST).groupby(main_t["date"]).sum()
    j = pd.concat([d, sday], axis=1, join="inner")
    print(f"  daily P&L corr with spike_mom rank 1-25: {j.corr().iloc[0, 1]:+.2f} on {len(j)} shared days")

    for T, lab in ((660, "11:00"), (780, "13:00")):
        stats(select(f[f["T"] == T]), f"reported: {lab}", gated=False)
    for T, lab in ((720, "12:00"), (660, "11:00"), (780, "13:00")):
        stats(select(f[f["T"] == T], use_er=False), f"reported: {lab} R decile only, no ER filter", gated=False)
    print(f"\nVERDICT steady-movers: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
