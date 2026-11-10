"""
Trigger replay (owner, 2026-11-10): a VPS screener watches all 277 names every minute and wakes Claude
only when something happens. Before building it, two questions, answered on cached SIP minute bars and
Alpaca headlines (no network):
  1. How many flags a day would it raise? (sets the Haiku triage and Claude session budget)
  2. Is any move left by the time Claude could enter (2-3 minutes after the flag), and how big?

Rules, fixed before the run. Bars 09:35-15:30 ET; trailing stats = the previous 30 bars of the same day.
  N news:     a single-name headline (its (created_at, headline) pair is in fewer than 5 of the 277 news
              files) created during the session; flagged on the bar it falls in.
  V volume:   bar volume >= 5x trailing mean and |1-min log return| >= 4x trailing std.
  B breakout: after 10:00, close above the day's prior high or below its prior low, volume >= 3x trailing mean.
  One flag per name per 30 minutes (whichever rule fires first); the later ones would reach Claude as the
  same story.
Direction = sign of the move from the flag bar's open to the close 2 bars later (the obvious read Claude
would see). Entry = open of bar flag+3 (about 2-3 minutes of screener, triage and session). Outcomes in
that direction, bps, before costs: the first 2 minutes ("gone": what the bots got), and entry -> +15, +30,
+60 minutes ("left"). Movement left = mean |entry -> +30| vs the same name's mean |30-minute move| at
random minutes of the same days. Costs 4 bps round trip shown separately. t = across days.

Usage: python scripts/research/trigger_replay.py [--days=20]
"""
import collections
import csv
import glob
import gzip
import os
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("STRATTON_CACHE", os.path.join(HERE, "..", ".cache"))
YEAR = 2026
RECAP_NAMES, TRAIL, COOLDOWN = 5, 30, 30
VOL_V, SD_V, VOL_B = 5.0, 4.0, 3.0
LATENCY, HOLDS = 3, (15, 30, 60)
COST = 4.0
ET = "America/New_York"


def news_stamps() -> dict:
    """{sym: sorted UTC ns of single-name headlines}."""
    rows, tags = {}, collections.Counter()
    for path in glob.glob(os.path.join(CACHE, "news", "*.csv.gz")):
        sym = os.path.basename(path)[:-len(".csv.gz")]
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            rows[sym] = [(r["created_at"], r["headline"] or "") for r in csv.DictReader(f)
                         if r["created_at"][:4] == str(YEAR)]
        tags.update(set(rows[sym]))
    return {s: np.sort(pd.to_datetime([c for c, h in items if tags[(c, h)] < RECAP_NAMES], utc=True).as_unit("ns").asi8)
            for s, items in rows.items()}


def flags_for(sym: str, stamps: np.ndarray) -> tuple[list, dict]:
    p = os.path.join(CACHE, f"m1_{sym}_{YEAR}.pkl")
    if not os.path.exists(p):
        return [], {}
    df = pd.read_pickle(p).astype("float64")
    df = df[~df.index.duplicated()].sort_index()
    if df.empty:
        return [], {}
    o, h, lo_, c, v = (df[k].to_numpy() for k in ("open", "high", "low", "close", "volume"))
    hm = (df.index.hour * 60 + df.index.minute).to_numpy()
    day = pd.Series(df.index.date)
    dnum = pd.factorize(day)[0]
    g = pd.DataFrame({"r": np.log(c / o), "v": v, "h": h, "l": lo_, "d": dnum})
    by = g.groupby("d")
    vmean = by["v"].transform(lambda s: s.shift(1).rolling(TRAIL, min_periods=TRAIL).mean()).to_numpy()
    rsd = by["r"].transform(lambda s: s.shift(1).rolling(TRAIL, min_periods=TRAIL).std()).to_numpy()
    dhi = by["h"].transform(lambda s: s.shift(1).cummax()).to_numpy()
    dlo = by["l"].transform(lambda s: s.shift(1).cummin()).to_numpy()
    r = g["r"].to_numpy()
    t_utc = df.index.tz_convert("UTC").as_unit("ns").asi8
    has_news = np.zeros(len(df), bool)
    k = np.searchsorted(t_utc, stamps, side="right") - 1     # the bar each headline falls in
    ok = (k >= 0) & (k < len(df))
    k = k[ok][(stamps[ok] - t_utc[k[ok]]) < 60_000_000_000]
    has_news[k] = True
    live = (hm >= 9 * 60 + 35) & (hm <= 15 * 60 + 30)
    V = live & (v >= VOL_V * vmean) & (np.abs(r) >= SD_V * rsd)
    B = live & (hm >= 600) & (v >= VOL_B * vmean) & ((c > dhi) | (c < dlo))
    N = live & has_news
    out, last = [], {}
    n = len(df)
    for i in np.flatnonzero(N | V | B):
        if i - last.get(dnum[i], -10**9) < COOLDOWN:
            continue
        e = i + LATENCY
        if e + max(HOLDS) >= n or dnum[e + max(HOLDS)] != dnum[i]:
            x_ok = {hd: e + hd < n and dnum[e + hd] == dnum[i] for hd in HOLDS}
        else:
            x_ok = {hd: True for hd in HOLDS}
        if e >= n or dnum[e] != dnum[i] or not x_ok[15]:
            continue
        last[dnum[i]] = i
        side = np.sign(c[i + 2] - o[i]) or 1.0
        row = {"sym": sym, "date": day[i], "et": f"{hm[i] // 60:02d}:{hm[i] % 60:02d}",
               "kind": "N" if N[i] else "V" if V[i] else "B", "news": bool(N[i]), "side": side,
               "gone": side * np.log(c[i + 2] / o[i]) * 1e4}
        for hd in HOLDS:
            row[f"left{hd}"] = side * np.log(c[e + hd - 1] / o[e]) * 1e4 if x_ok[hd] else np.nan
        row["abs30"] = abs(row["left30"]) if x_ok[30] else np.nan
        out.append(row)
    # baseline: this name's mean |30-minute move| from random live minutes, per day
    rng = np.random.default_rng(abs(hash(sym)) % 2**32)
    base = {}
    for d in np.unique(dnum):
        idx = np.flatnonzero((dnum == d) & live)
        idx = idx[idx + 30 < n]
        idx = idx[dnum[np.minimum(idx + 30, n - 1)] == d]
        if len(idx):
            pick = rng.choice(idx, min(20, len(idx)), replace=False)
            base[day[pick[0]]] = float(np.mean(np.abs(np.log(c[pick + 29] / o[pick]) * 1e4)))
    return out, base


def tstat(x: pd.Series, dates: pd.Series) -> float:
    m = x.groupby(dates).mean().dropna()
    return float(m.mean() / m.std(ddof=1) * np.sqrt(len(m))) if len(m) > 2 and m.std(ddof=1) > 0 else float("nan")


def report(f: pd.DataFrame, base: dict, label: str, ndays: int) -> None:
    print(f"\n== {label}: {ndays} trading days ==")
    per = f.groupby("date").size()
    print(f"flags/day: mean {per.mean():.0f}, median {per.median():.0f}, max {per.max()}  "
          f"(news {f.kind.eq('N').sum() / ndays:.0f}, volume {f.kind.eq('V').sum() / ndays:.0f}, "
          f"breakout {f.kind.eq('B').sum() / ndays:.0f}); names flagged/day {f.groupby('date').sym.nunique().mean():.0f}")
    for name, s in (("all", f), ("news", f[f.kind == "N"]), ("volume", f[f.kind == "V"]), ("breakout", f[f.kind == "B"])):
        if len(s) < 20:
            continue
        b = np.nanmean([base.get((x.sym, x.date), np.nan) for x in s.itertuples()])
        cells = "  ".join(f"+{hd}m {s[f'left{hd}'].mean():+.1f} (t {tstat(s[f'left{hd}'], s.date):+.1f})" for hd in HOLDS)
        print(f"  {name:8s} n {len(s):5d} | gone in first 2 min {s.gone.mean():+.1f} | left, same direction: {cells} "
              f"| |move| next 30m {s.abs30.mean():.0f} vs random {b:.0f} bps | cost {COST:.0f}")


def main():
    days_arg = next((int(a.split("=")[1]) for a in sys.argv[1:] if a.startswith("--days=")), 20)
    import headline_family as hf  # noqa: E402  (the 277 names, same list as every desk test)
    stamps = news_stamps()
    rows, base = [], {}
    for sym in hf.names():
        out, b = flags_for(sym, stamps.get(sym, np.zeros(0, "int64")))
        rows += out
        base.update({(sym, d): x for d, x in b.items()})
    f = pd.DataFrame(rows)
    dates = sorted(f.date.unique())
    last = f[f.date.isin(dates[-days_arg:])]
    report(last, base, f"last {days_arg} days to {dates[-1]}", days_arg)
    report(f, base, f"all of {YEAR} (sanity)", len(dates))
    f.to_csv(os.path.join(CACHE, "trigger_replay_flags.csv"), index=False)


if __name__ == "__main__":
    sys.path.insert(0, HERE)
    sys.path.insert(0, os.path.join(HERE, ".."))
    main()
