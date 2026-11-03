"""
Do bigger spikes run further? (research/journal.md, 2026-11-02 spike-size-bucket)

spike_mom exactly as live: vol >= 8x trailing mean, |ret| >= 5 trailing sigma,
signal 13:00-14:54 ET, enter next bar's open + 1-bar delay, hold 60 min.
Names: top 25 of the 62 by PRIOR-year average daily dollar volume.
Size terciles (move in sigmas, and volume multiple) are cut on 2017-21 and
applied unchanged to the held-out 2022-26.

Usage: python scripts/research/spike_size.py
"""
import os
import sys

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

import numpy as np
import pandas as pd

import backtest_scalp as bt

YEARS = range(2017, 2027)
DESIGN = range(2017, 2022)
VOL_X, K, HOLD, DELAY = 8, 5, 60, 1
FIRST, LAST = 13 * 60, 14 * 60 + 54
COST_BPS = 2.0  # per side


def collect() -> pd.DataFrame:
    names = bt.SYMBOLS + bt.WIDE
    dv, rows = {}, []
    for sym in names:
        for y in range(YEARS.start - 1, YEARS.stop):
            df = bt.load_year(sym, y)
            if df.empty:
                continue
            dv[(sym, y)] = (df["close"].astype("float64") * df["volume"]).groupby(df.index.date).sum().mean()
            if y not in YEARS:
                continue
            f = bt.features(df.astype("float64"))
            hm = f.index.hour * 60 + f.index.minute
            m = ((hm >= FIRST) & (hm <= LAST) & (f["ret_std"] > 0) & (f["volume"] >= VOL_X * f["vol_mean"])
                 & (f["ret"].abs() >= K * f["ret_std"])).to_numpy()
            side = np.sign(f["ret"].to_numpy())
            sig = (f["ret"].abs() / f["ret_std"]).to_numpy()
            vx = (f["volume"] / f["vol_mean"]).to_numpy()
            opens, closes, dates = f["open"].to_numpy(), f["close"].to_numpy(), f["date"].to_numpy()
            busy = -1
            for i in np.flatnonzero(m):           # same skip rules as bt.trades
                j = i + HOLD + DELAY
                if i <= busy or j >= len(f) or dates[j] != dates[i] or hm[j] > 959:
                    continue
                bps = side[i] * np.log(closes[j] / opens[i + 1 + DELAY]) * 1e4
                rows.append((sym, y, int(side[i]), sig[i], vx[i], bps))
                busy = j
        print(f"done {sym}", flush=True)
    t = pd.DataFrame(rows, columns=["sym", "year", "side", "sigma", "volx", "bps"])
    rank = {}
    for y in YEARS:
        prev = sorted(((dv[(s, y - 1)], s) for s in names if (s, y - 1) in dv), reverse=True)
        rank.update({(s, y): r + 1 for r, (_, s) in enumerate(prev)})
    t["rank"] = [rank.get((s, y), 99) for s, y in zip(t["sym"], t["year"])]
    return t[t["rank"] <= 25].reset_index(drop=True)


def tstat(x) -> float:
    x = np.asarray(x, dtype=float)
    return x.mean() / x.std(ddof=1) * np.sqrt(len(x)) if len(x) > 1 and x.std(ddof=1) > 0 else 0.0


def diff_t(a, b) -> tuple[float, float]:
    a, b = np.asarray(a, float), np.asarray(b, float)
    d = a.mean() - b.mean()
    se = np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))
    return d, d / se if se else 0.0


def report(t: pd.DataFrame, col: str) -> bool:
    design = t[t["year"].isin(DESIGN)]
    cuts = design[col].quantile([1 / 3, 2 / 3]).to_numpy()
    t = t.assign(q=np.digitize(t[col], cuts))   # 0 small, 1 mid, 2 big
    print(f"\n== {col}: tercile cuts from 2017-21 = {cuts[0]:.2f}, {cuts[1]:.2f}")
    verdict = {}
    for label, part in (("2017-21 design", t[t["year"].isin(DESIGN)]), ("2022-26 held out", t[~t["year"].isin(DESIGN)])):
        line = []
        for q in range(3):
            g = part[part["q"] == q]["bps"]
            line.append(f"q{q} n={len(g):4d} {g.mean() - 2 * COST_BPS:+6.1f} net (t {tstat(g - 2 * COST_BPS):+.1f})")
        d, dt = diff_t(part[part["q"] == 2]["bps"], part[part["q"] == 0]["bps"])
        verdict[label] = (d, dt)
        print(f"  {label:17s} " + " | ".join(line) + f" | big-small {d:+.1f} bps (t {dt:+.2f})")
    (dd, _), (hd, ht) = verdict["2017-21 design"], verdict["2022-26 held out"]
    ok = hd >= 5 and ht >= 2 and np.sign(dd) == np.sign(hd)
    print(f"  -> {'PASS' if ok else 'FAIL'}")
    return ok


def main():
    t = collect()
    print(f"\ntrades n={len(t)}  all {t['bps'].mean() - 2 * COST_BPS:+.1f} bps net (t {tstat(t['bps'] - 2 * COST_BPS):+.2f})")
    print("by year (net bps): " + "  ".join(f"{y}:{g.mean() - 2 * COST_BPS:+.0f}" for y, g in t.groupby("year")["bps"]))
    ok = report(t, "sigma")
    report(t, "volx")
    top = t.groupby("sym")["bps"].sum().sort_values(ascending=False)
    total = t["bps"].sum()
    shares = ", ".join(f"{s} {v / total:.0%}" for s, v in top.head(3).items())
    print(f"\ntop symbols' share of gross bps: {shares}")
    print(f"\nPRIMARY (sigma) verdict: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
