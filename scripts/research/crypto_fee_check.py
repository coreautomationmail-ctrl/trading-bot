"""
crypto-fee-check (ideas.md G), pre-registered in research/journal.md 2026-11-11.

Part 1: break-even on the spike rule (vol x8, |ret| >= 5 sd over the prior 30 bars, rolling through
midnight, 1-bar delay, hold 60). Part 2 (gated): 4-hour time-series momentum, long only, runs of held
windows as one trade, 25 bps/side (15 reported). Cached data only.

Usage: STRATTON_CACHE=... python scripts/research/crypto_fee_check.py
"""
import os

import numpy as np
import pandas as pd

CACHE = os.environ["STRATTON_CACHE"]
DESIGN = ["BTC", "ETH", "SOL", "DOGE"]
HELD = ["LTC", "AVAX", "LINK"]
FEE, FEE_LO = 25.0, 15.0
YEARS = range(2021, 2027)


def load(base: str) -> pd.DataFrame:
    parts = [pd.read_pickle(os.path.join(CACHE, "crypto", f"m1_{base}_{y}.pkl")) for y in YEARS
             if os.path.exists(os.path.join(CACHE, "crypto", f"m1_{base}_{y}.pkl"))]
    df = pd.concat(parts).sort_index()
    df = df[~df.index.duplicated(keep="first")]
    grid = pd.date_range(df.index[0], df.index[-1], freq="1min")
    df = df.reindex(grid)
    c = df["close"].ffill()
    miss = df["open"].isna()
    for col in ("open", "high", "low"):
        df.loc[miss, col] = c[miss]
    df["close"] = c
    df["volume"] = df["volume"].fillna(0.0)
    return df


def tstat(x) -> float:
    x = np.asarray(x, float)
    return x.mean() / (x.std(ddof=1) / np.sqrt(len(x))) if len(x) > 2 and x.std() > 0 else float("nan")


def spike_trades(base: str, df: pd.DataFrame) -> pd.DataFrame:
    ret = np.log(df["close"] / df["open"])
    vm = df["volume"].rolling(30, min_periods=20).mean().shift(1)
    sd = ret.rolling(30, min_periods=20).std().shift(1)
    mask = ((df["volume"] >= 8 * vm) & (vm > 0) & (ret.abs() >= 5 * sd) & (sd > 0)).to_numpy()
    o, c, s = df["open"].to_numpy(), df["close"].to_numpy(), np.sign(ret.to_numpy())
    n, busy, out = len(df), -1, []
    for i in np.flatnonzero(mask):
        j = i + 61
        if i <= busy or j >= n:
            continue
        out.append((base, df.index[i], s[i], s[i] * np.log(c[j] / o[i + 2]) * 1e4))
        busy = j
    return pd.DataFrame(out, columns=["coin", "t", "side", "gross"])


def tsmom(base: str, df: pd.DataFrame):
    """Returns (trades, windows): trades are runs of held 4h windows; windows are all 4h windows."""
    b = df.index[(df.index.minute == 0) & (df.index.hour % 4 == 0)]
    o = df["open"].reindex(b).to_numpy()
    prev_close = df["close"].shift(1).reindex(b).to_numpy()          # close of the bar before the boundary
    back = df["close"].shift(241).reindex(b).to_numpy()              # close 4 hours before that bar
    sig = np.log(prev_close / back) > 0
    wret = np.log(o[1:] / o[:-1]) * 1e4                              # window k: boundary k open -> k+1 open
    held = sig[:-1]
    win = pd.DataFrame({"coin": base, "t": b[:-1], "gross": wret, "held": held}).dropna()
    rows, k, m = [], 0, len(held)
    while k < m:
        if not held[k] or np.isnan(wret[k]):
            k += 1
            continue
        s = k
        while k < m and held[k] and not np.isnan(wret[k]):
            k += 1
        rows.append((base, b[s], b[k], k - s, wret[s:k].sum()))
    tr = pd.DataFrame(rows, columns=["coin", "entry", "exit", "windows", "gross"])
    return tr, win


def report(name: str, tr: pd.DataFrame, win: pd.DataFrame, spike_daily: pd.Series) -> dict:
    tr = tr.copy()
    tr["net"] = tr["gross"] - 2 * FEE
    tr["net_lo"] = tr["gross"] - 2 * FEE_LO
    tr["year"] = tr["entry"].dt.year
    daily = tr.groupby(tr["exit"].dt.date)["net"].sum()
    yr = tr.groupby("year")["net"].mean()
    early = tr.loc[tr["year"] <= 2023, "net"].mean()
    late = tr.loc[tr["year"] >= 2024, "net"].mean()
    bycoin = tr.groupby("coin")["net"].sum()
    pos = bycoin[bycoin > 0]
    top_coin = (pos.max() / pos.sum()) if len(pos) else float("nan")
    srt = tr["net"].sort_values(ascending=False)
    k = max(1, int(round(len(srt) * 0.01)))
    top1 = srt.iloc[:k].sum() / srt.sum() if srt.sum() != 0 else float("nan")
    hw = win.loc[win["held"], "gross"]
    aw = win["gross"]
    se = np.sqrt(hw.var(ddof=1) / len(hw) + aw.var(ddof=1) / len(aw))
    beta_gap, beta_t = hw.mean() - aw.mean(), (hw.mean() - aw.mean()) / se
    d = {
        "n": len(tr), "net": tr["net"].mean(), "net_lo": tr["net_lo"].mean(), "gross": tr["gross"].mean(),
        "t": tstat(tr["net"]), "day_t": tstat(daily), "yrs_pos": f"{(yr > 0).sum()}/{len(yr)}",
        "yrs_frac": (yr > 0).mean(), "early": early, "late": late, "top_coin": top_coin,
        "beta_gap": beta_gap, "beta_t": beta_t,
    }
    print(f"\n== {name} ==")
    print(f"n={d['n']} trades, mean gross {d['gross']:+.1f}, net@25 {d['net']:+.2f}, net@15 {d['net_lo']:+.2f}, "
          f"per-trade t {d['t']:+.2f}, day t {d['day_t']:+.2f} ({len(daily)} days)")
    print(f"win {np.mean(tr['net'] > 0):.0%}, median net {tr['net'].median():+.1f}, top-1% share {top1:.0%}, "
          f"mean hold {tr['windows'].mean() * 4:.1f} h")
    print("by year net:", ", ".join(f"{y} {v:+.1f}" for y, v in yr.items()), f"| 2021-23 {early:+.1f}, 2024-26 {late:+.1f}")
    print("by coin net sum (bps):", ", ".join(f"{c} {v:+.0f}" for c, v in bycoin.items()), f"| top coin share {top_coin:.0%}")
    print(f"beta check: held windows {hw.mean():+.2f} gross (n={len(hw)}) vs all windows {aw.mean():+.2f} "
          f"(n={len(aw)}), gap {beta_gap:+.2f}, Welch t {beta_t:+.2f}")
    if spike_daily is not None:
        j = pd.concat([daily, spike_daily], axis=1, join="inner").dropna()
        if len(j) > 30:
            print(f"daily P&L corr with spike_mom rank 1-25: {j.iloc[:, 0].corr(j.iloc[:, 1]):+.2f} on {len(j)} shared days")
    return d


def gate(d: dict) -> list:
    checks = [
        ("n >= 300", d["n"] >= 300), ("net >= 3", d["net"] >= 3), ("t >= 2.5", d["t"] >= 2.5),
        ("day t >= 2.5", d["day_t"] >= 2.5), ("years >= 70%", d["yrs_frac"] >= 0.7),
        ("same sign halves", np.sign(d["early"]) == np.sign(d["late"]) and d["early"] > 0),
        ("top coin <= 25%", d["top_coin"] <= 0.25), ("beta gap > 0, t >= 2", d["beta_gap"] > 0 and d["beta_t"] >= 2),
    ]
    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return checks


def main():
    sp = pd.read_csv(os.path.join(CACHE, "spike_wide_trades.csv"))
    sp = sp[sp["rank"] <= 25]
    sp["day"] = pd.to_datetime(sp["exit"], utc=True).dt.date
    spike_daily = (sp.groupby("day")["bps"].sum() - 4 * sp.groupby("day").size())

    spikes, trs, wins = [], {}, {}
    for base in DESIGN + HELD:
        df = load(base)
        print(f"{base}: {len(df)} minutes {df.index[0]} .. {df.index[-1]}, "
              f"buy-and-hold {np.log(df['close'].iloc[-1] / df['open'].iloc[0]) * 100:+.0f}% log", flush=True)
        spikes.append(spike_trades(base, df))
        trs[base], wins[base] = tsmom(base, df)

    s = pd.concat(spikes)
    print("\n== Part 1: spike rule break-even (all 7 coins, followed, gross bps) ==")
    for name, x in [("all", s), ("design 4", s[s.coin.isin(DESIGN)]), ("up only (long)", s[s.side > 0]),
                    ("down only", s[s.side < 0])]:
        print(f"{name}: n={len(x)}, mean {x.gross.mean():+.1f}, median {x.gross.median():+.1f}, t {tstat(x.gross):+.2f}, "
              f"share > 50 bps {np.mean(x.gross > 50):.0%}, net@25 {x.gross.mean() - 50:+.1f}")
    print("by coin mean gross:", ", ".join(f"{c} {v:+.1f} (n={k})" for c, (v, k) in
                                           s.groupby("coin")["gross"].agg(["mean", "size"]).iterrows()))

    print("\n== Part 2: 4-hour TSMOM, long only ==")
    d = report("design BTC ETH SOL DOGE", pd.concat([trs[b] for b in DESIGN]),
               pd.concat([wins[b] for b in DESIGN]), spike_daily)
    checks = gate(d)
    h = report("held out LTC AVAX LINK", pd.concat([trs[b] for b in HELD]),
               pd.concat([wins[b] for b in HELD]), None)
    held_ok = np.sign(h["net"]) == np.sign(d["net"]) and h["day_t"] >= 2
    print(f"  held out same sign with day t >= 2: {'PASS' if held_ok else 'FAIL'}")
    passed = sum(ok for _, ok in checks)
    print(f"\nVERDICT: {'PASS' if passed == len(checks) and held_ok else 'FAIL'} ({passed} of {len(checks)} design checks)")


if __name__ == "__main__":
    main()
