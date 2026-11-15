"""Turn-of-the-month on SPY (tom-spy), pre-registered in research/journal.md 2026-11-14 before any result.

Long SPY from the close of the 2nd-to-last trading day of a month to the close of the 3rd trading day of the
next (TOM days = -1, +1, +2, +3), flat otherwise, 5 bps per side. Primary: TOM-day mean minus other-day mean.
Usage: .backtest_venv/Scripts/python.exe scripts/research/tom_spy.py
"""
import math
from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

COST = 5e-4  # per side
d = pd.read_csv(Path(__file__).resolve().parents[1] / ".cache" / "l_daily.csv.gz")
spy = d[d.symbol == "SPY"].assign(date=lambda x: pd.to_datetime(x.date)).set_index("date")["c"].sort_index()
ret = spy.pct_change().dropna()

# Position by trading-day rank within its month: last day of month = -1, first days of a month = +1, +2, +3.
ym = ret.index.to_period("M")
first_rank = ret.groupby(ym).cumcount() + 1
last_rank = ret.groupby(ym).cumcount(ascending=False) + 1
tom = ((last_rank == 1) | (first_rank <= 3)).to_numpy()
# The first month in the data has no preceding month end; drop partial months at both edges.
full = (ym != ym[0]) & (ym != ym[-1])
r, tom = ret[full], tom[full]

a, b = r[tom], r[~tom]
t, p = stats.ttest_ind(a, b, equal_var=False)
print(f"sessions {len(r)}, TOM days {len(a)} ({len(a) / len(r):.1%}), months {len(a) // 4}")
print(f"TOM mean {a.mean() * 1e4:+.2f} bps/day, other {b.mean() * 1e4:+.2f} bps/day, diff {(a.mean() - b.mean()) * 1e4:+.2f} (Welch t {t:+.2f}, p {p:.3f})")

halves = {}
for name, m in (("2016-2020", r.index.year <= 2020), ("2021-2026", r.index.year >= 2021)):
    x, y = r[m & tom], r[m & ~tom]
    tt = stats.ttest_ind(x, y, equal_var=False).statistic
    halves[name] = x.mean() - y.mean()
    print(f"  {name}: diff {(x.mean() - y.mean()) * 1e4:+.2f} bps/day (t {tt:+.2f}, TOM n {len(x)})")

# Strategy: in the market on TOM days only; one entry and one exit per month, 5 bps each.
pos = pd.Series(tom.astype(float), index=r.index)
entries = ((pos == 1) & (pos.shift(1, fill_value=0) == 0)).sum()
strat = pos * r
strat.loc[(pos == 1) & (pos.shift(1, fill_value=0) == 0)] -= COST  # entry at the prior close
strat.loc[(pos == 1) & (pos.shift(-1, fill_value=0) == 0)] -= COST  # exit at this close
yrs = len(r) / 252
ann = lambda s: (1 + s).prod() ** (1 / yrs) - 1
sharpe = lambda s: s.mean() / s.std() * math.sqrt(252)
expo = pos.mean()
print(f"round trips {entries} ({entries / yrs:.1f}/yr), exposure {expo:.1%}")
print(f"TOM net: {ann(strat):+.2%}/yr, Sharpe {sharpe(strat):+.2f}, max DD {((1 + strat).cumprod() / (1 + strat).cumprod().cummax() - 1).min():.1%}")
print(f"SPY buy & hold: {ann(r):+.2%}/yr, Sharpe {sharpe(r):+.2f}, max DD {((1 + r).cumprod() / (1 + r).cumprod().cummax() - 1).min():.1%}")
matched = ann(r) * expo
print(f"exposure-matched SPY (return x {expo:.1%}): {matched:+.2%}/yr")

diff = a.mean() - b.mean()
checks = {
    "diff > 0 with t >= 2.0": diff > 0 and t >= 2.0,
    "both halves diff > 0": all(v > 0 for v in halves.values()),
    "net Sharpe >= buy & hold": sharpe(strat) >= sharpe(r),
    "net return > exposure-matched": ann(strat) > matched,
}
kill = diff <= 0 or sharpe(strat) < sharpe(r)
print("GATE: " + "; ".join(f"{k}: {'ok' if v else 'FAIL'}" for k, v in checks.items()))
print("VERDICT:", "PASS" if all(checks.values()) else "KILL" if kill else "INCONCLUSIVE")
