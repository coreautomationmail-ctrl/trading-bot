"""
Score Kronos forecasts in scripts/.cache/kronos_preds.csv.

Per horizon: rank IC (Spearman of predicted vs realized return), hit rate,
and the trading result of "take the sign of the forecast when |forecast| >=
THRESH bps" in gross bps/trade, with t-stat. Split in-sample-risk years
(2016-2024) from out-of-sample (2025-2026). Adoption bar: OOS gross per
trade >= 2x round-trip cost (>= ~4 bps for mega caps, ~2 bps SPY/QQQ),
t >= 2 on OOS, and same sign in most years.
"""
import os

import numpy as np
import pandas as pd

OUT = os.path.join(os.path.dirname(__file__), "..", ".cache", "kronos_preds.csv")
THRESH = (0, 2, 5, 10)


def tstat(x):
    x = np.asarray(x, dtype=float)
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 else float("nan")


def main():
    d = pd.read_csv(OUT)
    d["year"] = d["date"].str[:4].astype(int)
    d["period"] = np.where(d["year"] >= 2025, "OOS 25-26", "IS? 16-24")
    print(f"{len(d)} forecasts, {d['sym'].nunique()} symbols\n")
    for h in (1, 6, 12):
        p, r = f"pred_{h}", f"real_{h}"
        print(f"=== horizon {h * 5} min ===")
        for per, g in d.groupby("period"):
            ic = g[p].rank().corr(g[r].rank())
            hit = (np.sign(g[p]) == np.sign(g[r]))[g[r] != 0].mean()
            print(f"  {per}: n={len(g)}  IC={ic:+.3f}  hit={hit:.1%}  |pred| median {g[p].abs().median():.1f} bps"
                  f"  |real| median {g[r].abs().median():.1f} bps")
            for th in THRESH:
                t = g[g[p].abs() >= th]
                if len(t) < 10:
                    continue
                pnl = np.sign(t[p]) * t[r]
                print(f"    trade |pred|>={th:>2} bps: n={len(t):5d}  gross {pnl.mean():+6.2f} bps/trade  t={tstat(pnl):+5.2f}")
        yr = d.groupby("year").apply(lambda g: (np.sign(g[p]) * g[r]).mean(), include_groups=False)
        print("  by year (all trades, bps): " + " ".join(f"{y % 100:02d}:{v:+.1f}" for y, v in yr.items()))
        print()


if __name__ == "__main__":
    main()
