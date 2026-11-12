"""
spike-regime-filter (research/journal.md, 2026-11-12): can a take/skip filter, retrained
monthly on earlier trades only, lift spike_mom's mean net per trade?

Trades: .cache/spike_wide_trades.csv (spike_mom as live), side from spike_news_scored.csv.
Features from SPY 1-min bars up to entry - 1 min. Ridge (alpha 10) on net bps, retrained
at each month start on all earlier trades of the same universe; take if prediction > 0.

Usage: STRATTON_CACHE=... python -I scripts/research/spike_regime_filter.py
"""
import os

import numpy as np
import pandas as pd

CACHE = os.environ["STRATTON_CACHE"]
COST = 2.0
ALPHA = 10.0
FIRST_SCORED = pd.Period("2018-01", "M")
FEATS = ["side", "trend_today", "rv60", "rv20d", "trend_20d", "minute"]


def tstat(x) -> float:
    x = np.asarray(x, float)
    return x.mean() / x.std(ddof=1) * np.sqrt(len(x)) if len(x) > 1 and x.std(ddof=1) > 0 else 0.0


def welch(a, b) -> float:
    a, b = np.asarray(a, float), np.asarray(b, float)
    return (a.mean() - b.mean()) / np.sqrt(a.var(ddof=1) / len(a) + b.var(ddof=1) / len(b))


def spy_bars() -> pd.DataFrame:
    parts = [pd.read_pickle(os.path.join(CACHE, f"m1_SPY_{y}.pkl")) for y in range(2016, 2027)]
    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated()].astype("float64")
    df.index = df.index.tz_convert("America/New_York")
    return df[(df.index.hour * 60 + df.index.minute >= 570) & (df.index.hour * 60 + df.index.minute <= 959)]


def add_features(t: pd.DataFrame, spy: pd.DataFrame) -> pd.DataFrame:
    lr = np.log(spy["close"]).diff()
    day = spy.index.normalize()
    dn = day.to_numpy()
    lr[np.r_[True, dn[1:] != dn[:-1]]] = np.nan          # no overnight gap in 1-min vol
    rv60 = lr.rolling(60, min_periods=40).std() * 1e4
    day_open = spy.groupby(day)["open"].transform("first")
    daily_close = spy.groupby(day)["close"].last()
    dret = np.log(daily_close).diff()
    rv20d = dret.rolling(20).std().shift(1) * 1e4       # prior 20 sessions, known at today's open
    tr20 = np.log(daily_close).diff(20).shift(1) * 1e4  # 20-session return to the prior close

    sig = t["entry"] - pd.Timedelta(minutes=1)          # last bar fully closed at entry
    pos = spy.index.searchsorted(sig, side="right") - 1
    ok = (pos >= 0) & (spy.index[pos.clip(0)].normalize() == sig.dt.normalize())
    i = pos.clip(0)
    t["trend_today"] = t["side"] * np.log(spy["close"].to_numpy()[i] / day_open.to_numpy()[i]) * 1e4
    t["rv60"] = rv60.to_numpy()[i]
    d = sig.dt.normalize()
    t["rv20d"] = rv20d.reindex(d).to_numpy()
    t["trend_20d"] = t["side"] * tr20.reindex(d).to_numpy()
    t["minute"] = t["entry"].dt.hour * 60 + t["entry"].dt.minute - 13 * 60
    t.loc[~ok, "trend_today"] = np.nan
    return t


def ridge_fit(X, y):
    mu, sd = X.mean(0), X.std(0)
    sd[sd == 0] = 1
    Z = (X - mu) / sd
    ym = y.mean()
    w = np.linalg.solve(Z.T @ Z + ALPHA * np.eye(Z.shape[1]), Z.T @ (y - ym))
    return lambda Xn: ((Xn - mu) / sd) @ w + ym


def walk_forward(t: pd.DataFrame) -> pd.DataFrame:
    t = t.sort_values("entry").copy()
    t["month"] = t["entry"].dt.tz_localize(None).dt.to_period("M")
    t["pred"] = np.nan
    for m in sorted(t["month"].unique()):
        if m < FIRST_SCORED:
            continue
        tr, te = t["month"] < m, t["month"] == m
        f = ridge_fit(t.loc[tr, FEATS].to_numpy(), t.loc[tr, "net"].to_numpy())
        t.loc[te, "pred"] = f(t.loc[te, FEATS].to_numpy())
    s = t[t["pred"].notna()].copy()
    s["take"] = s["pred"] > 0
    return s


def report(label: str, s: pd.DataFrame, primary: bool) -> dict:
    tk, sk = s[s["take"]], s[~s["take"]]
    gap = tk["net"].mean() - s["net"].mean()
    wt = welch(tk["net"], sk["net"]) if len(sk) > 1 else 0.0
    print(f"\n== {label}: scored {len(s)} trades {s['entry'].min():%Y-%m}..{s['entry'].max():%Y-%m}, "
          f"taken {len(tk)} ({len(tk) / len(s):.0%})")
    for name, g in (("all (fixed rule)", s), ("taken", tk), ("skipped", sk)):
        if len(g) < 2:
            print(f"  {name:17s} n={len(g)}")
            continue
        daily = g.groupby(g["entry"].dt.date)["net"].sum()
        by = g.groupby("year")["net"].mean()
        print(f"  {name:17s} n={len(g):5d} net {g['net'].mean():+6.2f} (t {tstat(g['net']):+.2f}, day t "
              f"{tstat(daily):+.2f}) sum {g['net'].sum():+8.0f} win {(g['net'] > 0).mean():.0%} "
              f"median {g['net'].median():+.1f} years+ {(by > 0).sum()}/{len(by)}")
    print(f"  gap taken - all {gap:+.2f} bps; taken vs skipped Welch t {wt:+.2f}")
    res = {"gap": gap, "wt": wt}
    if len(tk) > 1:
        by = tk.groupby("year")["net"].mean()
        print("  taken by year: " + "  ".join(f"{y}:{v:+.1f}" for y, v in by.items()))
        early, late = tk[tk["year"] <= 2020]["net"].mean(), tk[tk["year"] >= 2021]["net"].mean()
        syms = tk.groupby("sym")["net"].sum()
        share = syms.max() / syms[syms > 0].sum() if (syms > 0).any() else 1.0
        x = np.sort(tk["net"].to_numpy())[::-1]
        top = x[: max(1, len(x) // 100)].sum() / x.sum() if x.sum() != 0 else np.nan
        daily = tk.groupby(tk["entry"].dt.date)["net"].sum()
        print(f"  2018-20 {early:+.2f}  2021-26 {late:+.2f}; top symbol {syms.idxmax()} {share:.0%}; "
              f"top-1% share of sum {top:.0%}")
        if primary:
            checks = {
                "gap >= 3": gap >= 3, "Welch t >= 2.5": wt >= 2.5, "n >= 300": len(tk) >= 300,
                "net >= 3": tk["net"].mean() >= 3, "trade t >= 2.5": tstat(tk["net"]) >= 2.5,
                "day t >= 2.5": tstat(daily) >= 2.5, "years+ >= 70%": (by > 0).mean() >= 0.7,
                "same sign halves": np.sign(early) == np.sign(late) and early > 0, "top sym <= 25%": share <= 0.25,
            }
            for k, v in checks.items():
                print(f"    {'ok  ' if v else 'FAIL'} {k}")
            res["pass"] = all(checks.values())
            res["n_ok"] = sum(checks.values())
    return res


def main():
    t = pd.read_csv(os.path.join(CACHE, "spike_wide_trades.csv"))
    s = pd.read_csv(os.path.join(CACHE, "spike_news_scored.csv"), usecols=["sym", "exit", "side"])
    t = t.merge(s, on=["sym", "exit"], how="left")
    assert t["side"].notna().all()
    t["entry"] = pd.to_datetime(t["entry"], utc=True).dt.tz_convert("America/New_York")
    t["net"] = t["bps"] - 2 * COST
    t = add_features(t, spy_bars())
    miss = t[FEATS].isna().any(axis=1)
    print(f"trades {len(t)}, dropped for missing features {miss.sum()}")
    t = t[~miss]

    prim = walk_forward(t[t["rank"] <= 25])
    r = report("PRIMARY rank 1-25", prim, True)
    held = walk_forward(t[t["rank"].between(26, 100)])
    h = report("HELD OUT rank 26-100", held, False)
    print(f"\nheld out: gap {h['gap']:+.2f}, Welch t {h['wt']:+.2f} "
          f"({'same sign' if np.sign(h['gap']) == np.sign(r['gap']) else 'opposite sign'})")

    # last model's coefficients, for the record (standardized)
    X, y = prim[FEATS].to_numpy(), prim["net"].to_numpy()
    Z = (X - X.mean(0)) / X.std(0)
    w = np.linalg.solve(Z.T @ Z + ALPHA * np.eye(len(FEATS)), Z.T @ (y - y.mean()))
    print("standardized ridge weights on all scored rank 1-25 trades (descriptive): "
          + ", ".join(f"{f} {v:+.2f}" for f, v in zip(FEATS, w)))
    ok = r.get("pass", False) and np.sign(h["gap"]) == np.sign(r["gap"]) and h["wt"] >= 2
    print(f"\nVERDICT: {'PASS' if ok else 'FAIL'} ({r.get('n_ok', 0)} of 9 primary checks)")


if __name__ == "__main__":
    main()
