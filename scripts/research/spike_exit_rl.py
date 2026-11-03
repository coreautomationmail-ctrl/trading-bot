"""
Can a learned exit beat the fixed 60-min hold? (research/journal.md, 2026-11-03 spike-exit-rl)

Same trades as spike_size.py. Each trade's path is followed up to 120 min.
Batch RL (fitted value iteration over a small state grid, decisions every
5 min) learns hold/exit on 2017-21 and is applied unchanged to 2022-26.

Usage: python scripts/research/spike_exit_rl.py
"""
import os
import sys
from collections import defaultdict

HERE = os.path.dirname(__file__)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(HERE, "..", ".."))

import numpy as np
import pandas as pd

import backtest_scalp as bt
from spike_size import YEARS, DESIGN, VOL_X, K, DELAY, FIRST, LAST, tstat

MAX_HOLD, STEP, BASE, MIN_N = 120, 5, 60, 30
PNL_EDGES = [-50, -20, 0, 20, 50]
MOVE_EDGES = [-5, 5]


def collect():
    """[(sym, year, path)] where path[k-1] = side-adjusted bps after k minutes held."""
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
            opens, closes, dates = f["open"].to_numpy(), f["close"].to_numpy(), f["date"].to_numpy()
            busy = -1
            for i in np.flatnonzero(m):           # same skip rules as spike_size (60-min busy window)
                j = i + BASE + DELAY
                if i <= busy or j >= len(f) or dates[j] != dates[i] or hm[j] > 959:
                    continue
                e = i + 1 + DELAY
                path = []
                for k in range(1, MAX_HOLD + 1):
                    x = e + k - 1
                    if x >= len(f) or dates[x] != dates[i] or hm[x] > 959:
                        break
                    path.append(side[i] * np.log(closes[x] / opens[e]) * 1e4)
                rows.append((sym, y, np.array(path)))
                busy = j
        print(f"done {sym}", flush=True)
    rank = {}
    for y in YEARS:
        prev = sorted(((dv[(s, y - 1)], s) for s in names if (s, y - 1) in dv), reverse=True)
        rank.update({(s, y): r + 1 for r, (_, s) in enumerate(prev)})
    return [r for r in rows if rank.get((r[0], r[1]), 99) <= 25]


def at(path, k):
    return 0.0 if k == 0 else path[k - 1]


def last_check(path):
    return min(len(path), MAX_HOLD) // STEP * STEP


def state(path, k):
    r = at(path, k)
    return k, int(np.digitize(r, PNL_EDGES)), int(np.digitize(r - at(path, k - STEP), MOVE_EDGES))


def learn(paths):
    """Backward induction: hold[(k, pnl, move)] = True/False where n >= MIN_N."""
    value = [0.0] * len(paths)            # value of continuing past the next checkpoint, per trade
    policy = {}
    for k in range(MAX_HOLD - STEP, 0, -STEP):
        gains = defaultdict(list)
        for t, p in enumerate(paths):
            if k < last_check(p):
                gains[state(p, k)].append((t, at(p, k + STEP) - at(p, k) + value[t]))
        new = [0.0] * len(paths)
        for s, g in gains.items():
            q = np.mean([x for _, x in g])
            hold = q > 0 if len(g) >= MIN_N else k < BASE
            policy[s] = (hold, len(g), q)
            for t, x in g:
                new[t] = x if hold else 0.0
        value = new
    return policy


def run(policy, p):
    """(bps at exit, minutes held) for one trade under the learned policy."""
    end = last_check(p)
    for k in range(STEP, end, STEP):
        hold, _, _ = policy.get(state(p, k), (k < BASE, 0, 0))
        if not hold:
            return at(p, k), k
    return at(p, end), end


def main():
    rows = collect()
    years = np.array([y for _, y, _ in rows])
    paths = [p for _, _, p in rows]
    design = [p for p, y in zip(paths, years) if y in DESIGN]
    policy = learn(design)
    fixed = lambda h: np.array([at(p, min(h, len(p))) for p in paths])
    base = fixed(BASE)
    out = [run(policy, p) for p in paths]
    pol, mins = np.array([o[0] for o in out]), np.array([o[1] for o in out])
    d = pol - base
    print(f"\ntrades n={len(paths)}  learned states (n>={MIN_N}): {sum(v[1] >= MIN_N for v in policy.values())}")
    verdict = {}
    for label, sel in (("2017-21 design", np.isin(years, list(DESIGN))), ("2022-26 held out", ~np.isin(years, list(DESIGN)))):
        fx = "  ".join(f"fixed{h} {fixed(h)[sel].mean():+.1f}" for h in (30, 60, 90, 120))
        verdict[label] = (d[sel].mean(), tstat(d[sel]))
        print(f"  {label:17s} n={sel.sum():4d}  policy {pol[sel].mean():+.1f} gross (avg {mins[sel].mean():.0f} min) | {fx}"
              f" | policy-fixed60 {d[sel].mean():+.1f} bps (paired t {tstat(d[sel]):+.2f})")
    print("held-out diff by year: " + "  ".join(f"{y}:{d[years == y].mean():+.1f}" for y in sorted(set(years)) if y not in DESIGN))
    exits = sorted((s, v) for s, v in policy.items() if v[1] >= MIN_N and not v[0])
    print(f"learned exit states ({len(exits)}): " + ", ".join(f"k{s[0]}/pnl{s[1]}/mv{s[2]} q{v[2]:+.1f} n{v[1]}" for s, v in exits[:15]))
    (dd, _), (hd, ht) = verdict["2017-21 design"], verdict["2022-26 held out"]
    ok = hd >= 3 and ht >= 2 and np.sign(dd) == np.sign(hd)
    print(f"\nVERDICT: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
