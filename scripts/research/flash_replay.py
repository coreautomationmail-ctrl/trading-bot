"""
flash-replay (research/journal.md, 2026-11-14 PRE-REGISTRATION): the live flash screener's exact rule
(pods/spike/flash_screener.py: single-name headline 09:35-15:30 ET, a bar in its minute + 2 with volume >= 3x the
30 minutes before and >= 0.3% from the last close before it; 30-min cooldown per name; 15 a day) on cached SIP
1-min bars and headlines 2016-26, each flag scored as pods/bench/intraday_book.py score_flag_day: entry at the
open of the bar flagged + 4 min 59 s floored, follow = the move's sign, own minus SPY, minus 4 bps; fade = -follow - 8.

Usage: python scripts/research/flash_replay.py [--workers=6]
"""
import collections
import csv
import glob
import gzip
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.environ.get("STRATTON_CACHE", os.path.join(HERE, "..", ".cache"))
YEARS = range(2016, 2027)
ET = "America/New_York"
SINGLE_NAME, VOL_X, MOVE, TRAIL, CONFIRM = 3, 3.0, 0.003, 30, 3
START, END = 9 * 60 + 35, 15 * 60 + 30
COOLDOWN, CAP, LAG, HOLDS, COST = 30, 15, 5, (30, 60), 4.0   # LAG: flagged (bar + 1) + 4:59 floored = bar + 6


def single_name_headlines() -> dict:
    """{sym: [(created ET minute Timestamp, headline)]} for headlines in <= SINGLE_NAME news files."""
    rows, tags = {}, collections.Counter()
    for path in glob.glob(os.path.join(CACHE, "news", "*.csv.gz")):
        sym = os.path.basename(path)[:-len(".csv.gz")]
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            rows[sym] = list({(r["created_at"], r["headline"] or "") for r in csv.DictReader(f)
                              if r["created_at"][:4] >= "2016"})
        tags.update(rows[sym])
    out = {}
    for sym, items in rows.items():
        keep = [(c, h) for c, h in items if tags[(c, h)] <= SINGLE_NAME]
        if not keep:
            continue
        ts = pd.to_datetime([c for c, _ in keep], utc=True).tz_convert(ET)
        out[sym] = sorted(zip(ts, [h for _, h in keep]), key=lambda x: x[0])
    return out


def load(sym: str) -> pd.DataFrame | None:
    parts = [pd.read_pickle(p) for y in YEARS if os.path.exists(p := os.path.join(CACHE, f"m1_{sym}_{y}.pkl"))]
    if not parts:
        return None
    df = pd.concat(parts).astype("float64")
    return df[~df.index.duplicated()].sort_index()


def flags_for(args) -> list:
    """Candidate flags for one name (cooldown applied; the daily cap is applied across names later)."""
    sym, news = args
    df = load(sym)
    if df is None or df.empty:
        return []
    idx = df.index
    pos = {t: i for i, t in enumerate(idx)}
    o, h, lo, c, v = (df[k].to_numpy() for k in ("open", "high", "low", "close", "volume"))
    out, last = [], None
    for at, headline in news:
        m = at.hour * 60 + at.minute
        if not START <= m <= END:
            continue
        start = at.floor("min")
        day = start.normalize()
        trail = sum(v[pos[t]] for k in range(1, TRAIL + 1)
                    if (t := start - pd.Timedelta(minutes=k)) in pos and t >= day)
        mean = trail / TRAIL
        before = idx.searchsorted(start) - 1
        if mean <= 0 or before < 0 or idx[before].normalize() != day:
            continue
        ref = c[before]
        hit = None
        for k in range(CONFIRM):
            i = pos.get(start + pd.Timedelta(minutes=k))
            if i is not None and v[i] >= VOL_X * mean and max(abs(h[i] / ref - 1), abs(lo[i] / ref - 1)) >= MOVE:
                hit = i
                break
        if hit is None:
            continue
        flagged = idx[hit] + pd.Timedelta(minutes=1)
        if last is not None and flagged - last < pd.Timedelta(minutes=COOLDOWN):
            continue
        last = flagged
        up = 1 if c[hit] >= ref else -1
        out.append({"sym": sym, "date": str(day.date()), "flagged": flagged, "headline": headline[:200],
                    "volume_x": round(v[hit] / mean, 1), "move_pct": round((c[hit] / ref - 1) * 100, 2),
                    "up": up, "entry_at": idx[hit] + pd.Timedelta(minutes=LAG + 1)})
    return out


def leg(df_pos, o, idx, entry_at, exit_at):
    i, j = df_pos.get(entry_at), df_pos.get(exit_at)
    if i is None:   # first bar at or after entry, same day
        k = idx.searchsorted(entry_at)
        i = k if k < len(idx) and idx[k].normalize() == entry_at.normalize() and idx[k] < exit_at else None
    if j is None:
        k = idx.searchsorted(exit_at)
        j = k if k < len(idx) and idx[k].normalize() == exit_at.normalize() else None
    if i is None or j is None or j <= i:
        return None
    return (o[j] / o[i] - 1) * 1e4


def score(args) -> list:
    sym, flags, spy_path = args
    df, spy = load(sym), pd.concat([pd.read_pickle(p) for p in spy_path])
    spy = spy[~spy.index.duplicated()].sort_index().astype("float64")
    rows = []
    for d, (idx, o) in (("own", (df.index, df["open"].to_numpy())), ("spy", (spy.index, spy["open"].to_numpy()))):
        pos = {t: i for i, t in enumerate(idx)}
        for f in flags:
            for hold in HOLDS:
                f[f"{d}{hold}"] = leg(pos, o, idx, f["entry_at"], f["entry_at"] + pd.Timedelta(minutes=hold))
    for f in flags:
        for hold in HOLDS:
            own, s = f.pop(f"own{hold}"), f.pop(f"spy{hold}")
            f[f"follow{hold}"] = None if own is None or s is None else round(f["up"] * (own - s) - COST, 2)
            f[f"fade{hold}"] = None if f[f"follow{hold}"] is None else round(-f[f"follow{hold}"] - 2 * COST, 2)
        rows.append(f)
    return rows


def stats(g: pd.DataFrame, col: str) -> dict:
    x = g[col].dropna()
    if len(x) < 2:
        return {"n": len(x)}
    daily = g.dropna(subset=[col]).groupby("date")[col].sum()
    yr = g.dropna(subset=[col]).groupby(g["date"].str[:4])[col].mean()
    pos_sym = g[g[col] > 0].groupby("sym")[col].sum()
    return {"n": len(x), "days": len(daily), "mean": x.mean(), "t": x.mean() / x.std() * np.sqrt(len(x)),
            "day_t": daily.mean() / daily.std() * np.sqrt(len(daily)) if len(daily) > 1 else np.nan,
            "win": (x > 0).mean(), "median": x.median(), "years": yr,
            "top_sym": (pos_sym.idxmax(), pos_sym.max() / pos_sym.sum()) if len(pos_sym) else ("-", 0)}


def line(name: str, s: dict) -> str:
    if s["n"] < 2:
        return f"  {name:<28} n={s['n']}"
    return (f"  {name:<28} n={s['n']:>6} days={s['days']:>5} net {s['mean']:+6.2f} (t {s['t']:+6.2f}, day t "
            f"{s['day_t']:+6.2f}) win {s['win']:.0%} med {s['median']:+.1f}")


def gate(g: pd.DataFrame, col: str, out: list) -> None:
    s = stats(g, col)
    out.append(f"\n== {col} (all 2016-26)")
    out.append(line("all", s))
    out.append("  by year: " + "  ".join(f"{y}:{m:+.1f}" for y, m in s["years"].items()))
    early = stats(g[g["date"] < "2021"], col)
    late = stats(g[g["date"] >= "2021"], col)
    design = stats(g[g["date"] < "2023"], col)
    held = stats(g[g["date"] >= "2023"], col)
    for name, x in (("2016-20", early), ("2021-26", late), ("design 2016-22", design), ("held out 2023-26", held)):
        out.append(line(name, x))
    out.append(f"  top symbol {s['top_sym'][0]} {s['top_sym'][1]:.0%} of positive P&L")
    yrs = design["years"]
    checks = [("n >= 300", design["n"] >= 300), ("mean net >= +3", design["mean"] >= 3),
              ("per-trade t >= 2.9", design["t"] >= 2.9), ("day t >= 2.9", design["day_t"] >= 2.9),
              (">= 70% of years positive", (yrs > 0).mean() >= 0.7),
              ("same sign 2016-20 / 2021-26", np.sign(early["mean"]) == np.sign(late["mean"])),
              ("top symbol <= 25%", design["top_sym"][1] <= 0.25),
              ("held out same sign, day t >= 2", np.sign(held["mean"]) == np.sign(design["mean"]) and abs(held["day_t"]) >= 2)]
    for name, ok in checks:
        out.append(f"    {'ok  ' if ok else 'FAIL'} {name}")
    out.append(f"  verdict {col}: {'PASS' if all(ok for _, ok in checks) else 'FAIL'} ({sum(ok for _, ok in checks)}/{len(checks)})")


def main():
    workers = int(next((a.split("=")[1] for a in sys.argv if a.startswith("--workers=")), 6))
    news = single_name_headlines()
    print(f"names with single-name headlines: {len(news)}; headlines {sum(map(len, news.values()))}", flush=True)
    with ProcessPoolExecutor(workers) as ex:
        cands = [f for fs in ex.map(flags_for, sorted(news.items())) for f in fs]
    # the daily cap across names, in flag-time order
    cands.sort(key=lambda f: f["flagged"])
    per_day, flags = collections.Counter(), []
    for f in cands:
        if per_day[f["date"]] < CAP:
            per_day[f["date"]] += 1
            flags.append(f)
    print(f"candidates {len(cands)}, after cap {len(flags)}", flush=True)
    by_sym = collections.defaultdict(list)
    for f in flags:
        by_sym[f["sym"]].append(f)
    spy = [p for y in YEARS if os.path.exists(p := os.path.join(CACHE, f"m1_SPY_{y}.pkl"))]
    with ProcessPoolExecutor(workers) as ex:
        rows = [r for rs in ex.map(score, [(s, fs, spy) for s, fs in by_sym.items()]) for r in rs]
    g = pd.DataFrame(rows).sort_values("flagged")
    g["flagged"] = g["flagged"].astype(str)
    g["entry_at"] = g["entry_at"].astype(str)
    g.to_csv(os.path.join(CACHE, "flash_replay_flags.csv"), index=False)

    out = [f"flags {len(g)} on {g['date'].nunique()} days; per flag-day mean {per_day_mean(g):.1f}, "
           f"days at the cap of {CAP}: {(g.groupby('date').size() >= CAP).sum()}",
           "flags per day by year: " + "  ".join(f"{y}:{n / d:.1f}" for y, n, d in per_year(g))]
    for col in ("follow60", "fade60"):
        gate(g, col, out)
    out.append("\n== reported, not gated (follow; fade = -follow - 8)")
    for col in ("follow30", "follow60"):
        out.append(line(f"{col} all", stats(g, col)))
    vx = pd.cut(g["volume_x"], [3, 5, 10, np.inf], right=False, labels=["vol 3-5x", "vol 5-10x", "vol 10x+"])
    mv = pd.cut(g["move_pct"].abs(), [0, 0.5, 1, 2, np.inf], right=False, labels=["move <0.5%", "0.5-1%", "1-2%", "2%+"])
    hr = np.where(pd.to_datetime(g["flagged"].str[11:16], format="%H:%M").dt.hour < 12, "morning", "afternoon")
    for name, key in (("volume", vx), ("move", mv), ("time", hr)):
        for k, sub in g.groupby(key, observed=True):
            out.append(line(f"{name} {k} follow60", stats(sub, "follow60")))
    recent = g[g["date"] >= "2026-07-01"]
    out.append(line("2026-07..10 follow30", stats(recent, "follow30")))
    out.append(line("2026-07..10 follow60", stats(recent, "follow60")))
    text = "\n".join(out)
    print(text)
    with open(os.path.join(HERE, "..", "..", "research", "flash_replay.log"), "w", encoding="utf-8") as f:
        f.write(text + "\n")


def per_day_mean(g):
    return g.groupby("date").size().mean()


def per_year(g):
    for y, sub in g.groupby(g["date"].str[:4]):
        yield y, len(sub), sub["date"].nunique()


if __name__ == "__main__":
    main()
