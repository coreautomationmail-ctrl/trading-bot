"""
Intraday pairs (research/journal.md, 2026-11-10 intraday-pairs, ideas.md F).

Monthly: the 50 pairs among the 277 with the highest correlation of half-hour returns over the prior 60 sessions.
Daily: L = log a - log b, ref = L at the 09:59 close, sd = std of the pair's half-hour L changes over the prior 20
sessions. First bar starting 10:30..15:53 with |L - ref| > 3 sd and no single-name headline on either name in
[bar start - 60 min, bar start): enter next open, long laggard / short leader; exit at the open after L is back at
or across ref, else the 15:55 open. 2 bps/side per leg = 8 bps a pair trade.
Gate: n >= 300, net >= 3, per-trade t >= 2.5, day t >= 2.5, >= 70% years positive, same sign 2016-20 / 2021-26,
top symbol <= 25%. Held out: pairs formed only among names ranked 101-277 by prior-year dollar volume.

Usage: python scripts/research/intraday_pairs.py [--workers=8]
"""
import collections
import csv
import glob
import gzip
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
COST = 8.0
NB = 390                      # bars 09:30 .. 15:59
REF = 29                      # 09:59 bar close = the 10:00 value
TRIG_LO, TRIG_HI = 60, 383    # bar starts 10:30 .. 15:53
EXIT = 385                    # 15:55 bar
HH = [0] + list(range(29, NB, 30))   # half-hour grid: 09:30 open, then closes of 09:59 .. 15:59
FORM, SDWIN, NPAIRS, Z = 60, 20, 50, 3.0
RECAP_NAMES = 5
ET = "America/New_York"


def names() -> list[str]:
    import backtest_scalp as bt
    extra = json.load(open(os.path.join(CACHE, "m1_extra_symbols.json")))
    extra = extra.get("symbols", extra) if isinstance(extra, dict) else extra
    return sorted(set(bt.SYMBOLS + bt.WIDE) | set(extra))


def news_stamps() -> dict:
    """{sym: sorted UTC ns} of single-name headlines (pair in fewer than 5 of the news files)."""
    rows, tags = {}, collections.Counter()
    for path in glob.glob(os.path.join(CACHE, "news", "*.csv.gz")):
        sym = os.path.basename(path)[:-len(".csv.gz")]
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            rows[sym] = [(r["created_at"], r["headline"] or "") for r in csv.DictReader(f)]
        tags.update(set(rows[sym]))
    out = {}
    for sym, items in rows.items():
        keep = [c for c, h in items if tags[(c, h)] < RECAP_NAMES]
        out[sym] = np.sort(pd.to_datetime(keep, utc=True).as_unit("ns").asi8) if keep else np.zeros(0, "int64")
    return out


def load(args):
    """One name-year -> dates, log close / log open (days x 390, previous-close filled), eligible, $ volume."""
    sym, y = args
    p = os.path.join(CACHE, f"m1_{sym}_{y}.pkl")
    if not os.path.exists(p):
        return sym, None
    df = pd.read_pickle(p)
    if df.empty:
        return sym, None
    df = df[~df.index.duplicated()].sort_index()
    hm = df.index.hour * 60 + df.index.minute - 570
    df = df[(hm >= 0) & (hm < NB)]
    hm = (df.index.hour * 60 + df.index.minute - 570).to_numpy()
    dates = df.index.date
    ud, di = np.unique(dates, return_inverse=True)
    C = np.full((len(ud), NB), np.nan, "float64")
    O = np.full((len(ud), NB), np.nan, "float64")
    C[di, hm] = df["close"].to_numpy("float64")
    O[di, hm] = df["open"].to_numpy("float64")
    cnt = np.isfinite(C).sum(1)
    elig = np.isfinite(C[:, 0]) & (cnt >= 0.9 * NB)
    C = pd.DataFrame(C).T.ffill().T.to_numpy()
    prevc = np.concatenate([np.full((len(ud), 1), np.nan), C[:, :-1]], axis=1)
    O = np.where(np.isfinite(O), O, prevc)
    dv = float((df["close"].astype("float64") * df["volume"].astype("float64")).groupby(dates).sum().mean())
    with np.errstate(invalid="ignore", divide="ignore"):
        return sym, (ud, np.log(C).astype("float32"), np.log(O).astype("float32"), elig, dv)


def prior_full(full, idx, k):
    """The last k full sessions before global day idx (half days, where most names fall under 90% of bars, skipped)."""
    f = np.flatnonzero(full[:idx])
    return f[-k:] if len(f) >= k else None


def form_pairs(HL, E, full, idx, allowed):
    """Top pairs by correlation of half-hour returns over the 60 full sessions before idx."""
    w = prior_full(full, idx, FORM)
    if w is None:
        return []
    ok = np.where(E[:, w].all(1) & allowed)[0]
    if len(ok) < 2:
        return []
    r = np.diff(HL[ok][:, w, :], axis=2).reshape(len(ok), -1).astype("float64")
    good = np.isfinite(r).all(1) & (r.std(1) > 0)
    ok, r = ok[good], r[good]
    c = np.corrcoef(r)
    iu = np.triu_indices(len(ok), 1)
    top = np.argsort(c[iu])[::-1][:NPAIRS]
    return [(ok[iu[0][k]], ok[iu[1][k]], c[iu][k]) for k in top]


def recent_news(stamps, t0):
    """Per bar of the day: any stamp in [start - 60 min, start)."""
    starts = t0 + np.arange(NB, dtype="int64") * 60_000_000_000
    return (np.searchsorted(stamps, starts) - np.searchsorted(stamps, starts - 3_600_000_000_000)) > 0


def day_trades(pairs, d, gidx, LC, LO, Ey, HL, E, full, news, syms, t0, use_news=True):
    out = []
    cache = {}
    for a, b, rho in pairs:
        if not (Ey[a, d] and Ey[b, d]):
            continue
        w = prior_full(full, gidx, SDWIN)
        if w is None:
            continue
        both = E[a, w] & E[b, w]
        if both.sum() < 15:
            continue
        ch = np.diff(HL[a, w, :] - HL[b, w, :], axis=1)[both].ravel().astype("float64")
        sd = ch.std(ddof=1)
        if not np.isfinite(sd) or sd <= 0:
            continue
        L = (LC[a, d] - LC[b, d]).astype("float64")
        dev = L - L[REF]
        hit = np.abs(dev) > Z * sd
        hit[:TRIG_LO] = False
        hit[TRIG_HI + 1:] = False
        if use_news:
            for s in (a, b):
                if s not in cache:
                    cache[s] = recent_news(news.get(syms[s], np.zeros(0, "int64")), t0)
                hit &= ~cache[s]
        ms = np.flatnonzero(hit)
        if not len(ms):
            continue
        m = ms[0]
        sgn = np.sign(dev[m])
        e = m + 1
        back = np.flatnonzero(sgn * dev[e:] <= 0)
        x, rev = EXIT, False
        if len(back) and e + back[0] + 1 < EXIT:
            x, rev = e + back[0] + 1, True
        ra = float(LO[a, d, x] - LO[a, d, e])
        rb = float(LO[b, d, x] - LO[b, d, e])
        if not (np.isfinite(ra) and np.isfinite(rb)):
            continue
        bps = -sgn * (ra - rb) * 1e4
        out.append((syms[a], syms[b], rho, m, x - e, rev, sgn * dev[m] / sd, bps))
    return out


def stats(t: pd.DataFrame, label: str, gated=True, held=None) -> bool:
    if t.empty:
        print(f"\n== {label}: n=0")
        return False
    x = t["bps"] - COST
    day = x.groupby(t["date"]).sum()
    day_t = day.mean() / day.std(ddof=1) * np.sqrt(len(day))
    tr_t = x.mean() / x.std(ddof=1) * np.sqrt(len(x))
    by = x.groupby(t["year"]).mean()
    syms = pd.concat([(x / 2).groupby(t["a"]).sum(), (x / 2).groupby(t["b"]).sum()]).groupby(level=0).sum()
    share = syms.max() / syms[syms > 0].sum() if (syms > 0).any() else float("nan")
    pr = x.groupby(t["a"] + "/" + t["b"]).sum()
    pshare = pr.max() / pr[pr > 0].sum() if (pr > 0).any() else float("nan")
    top = np.sort(x.to_numpy())[::-1][:max(1, len(x) // 100)].sum() / x.sum() if x.sum() else float("nan")
    early, late = x[t["year"].isin(EARLY)].mean(), x[~t["year"].isin(EARLY)].mean()
    print(f"\n== {label}")
    print(f"  n={len(x)} on {len(day)} days, net {x.mean():+.2f} bps (gross {t['bps'].mean():+.2f}; per-trade t {tr_t:+.2f}, "
          f"day t {day_t:+.2f}), win {(x > 0).mean():.0%}, median {x.median():+.1f}, top-1% share {top:+.0%}")
    print("  by year: " + "  ".join(f"{y}:{v:+.1f}/{int(m)}" for (y, v), m in zip(by.items(), t.groupby("year").size())))
    print(f"  2016-20 {early:+.2f}, 2021-26 {late:+.2f}; top symbol {syms.idxmax()} {share:.0%}; top pair {pr.idxmax()} "
          f"{pshare:.0%} (n={int((t['a'] + '/' + t['b'] == pr.idxmax()).sum())}); median hold {t['hold'].median():.0f} min, "
          f"reverted {t['rev'].mean():.0%}, median entry z {t['z'].median():.2f}; at 1 bps/side {(t['bps'] - 4).mean():+.2f}")
    top_pairs = (t["a"] + "/" + t["b"]).value_counts().head(8)
    print("  most traded pairs: " + ", ".join(f"{k} {v}" for k, v in top_pairs.items()))
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
    if held is not None:
        hx = held["bps"] - COST
        hd = hx.groupby(held["date"]).sum()
        ht = hd.mean() / hd.std(ddof=1) * np.sqrt(len(hd)) if len(hd) > 1 else 0.0
        print(f"  held out (pairs among rank 101-277): n={len(hx)}, net {hx.mean() if len(hx) else float('nan'):+.2f}, day t {ht:+.2f}")
        checks["held out same sign, day t >= 2"] = len(hx) > 0 and hx.mean() * x.mean() > 0 and ht >= 2
    for k, v in checks.items():
        print(f"    {'ok  ' if v else 'FAIL'} {k}")
    ok = all(checks.values())
    print(f"  verdict: {'PASS' if ok else 'FAIL'} ({sum(checks.values())}/{len(checks)})")
    return ok


def main():
    workers = int(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--workers=")), 8))
    syms = names()
    N = len(syms)
    print(f"names {N}; loading headlines", flush=True)
    news = news_stamps()
    gdays, HLs, Es, fulls = [], [], [], []
    dv = {}
    rows = {"main": [], "nonews": [], "held": []}
    pairs = {"main": [], "held": []}
    month = None
    with ProcessPoolExecutor(workers) as ex:
        for y in YEARS:
            res = dict(ex.map(load, [(s, y) for s in syms]))
            cal = sorted(set().union(*[set(r[0]) for r in res.values() if r is not None]))
            pos = {d: i for i, d in enumerate(cal)}
            D = len(cal)
            LC = np.full((N, D, NB), np.nan, "float32")
            LO = np.full((N, D, NB), np.nan, "float32")
            Ey = np.zeros((N, D), bool)
            for i, s in enumerate(syms):
                r = res.get(s)
                if r is None:
                    continue
                ix = [pos[d] for d in r[0]]
                LC[i, ix], LO[i, ix], Ey[i, ix] = r[1], r[2], r[3]
                dv[(s, y)] = r[4]
            del res
            hl = LC[:, :, HH].copy()
            hl[:, :, 0] = LO[:, :, 0]
            HLs.append(hl)
            Es.append(Ey)
            has = np.isfinite(LC[:, :, 0]).sum(0)
            fulls.append(Ey.sum(0) >= 0.5 * np.maximum(has, 1))
            base = len(gdays)
            gdays += cal
            HL, E, full = np.concatenate(HLs, axis=1), np.concatenate(Es, axis=1), np.concatenate(fulls)
            prev = sorted(((dv[(s, y - 1)], i) for i, s in enumerate(syms) if (s, y - 1) in dv), reverse=True)
            held_ok = np.zeros(N, bool)
            for k, (_, i) in enumerate(prev):
                held_ok[i] = k >= 100
            ntr, npm = 0, []
            for d, day in enumerate(cal):
                g = base + d
                if (day.year, day.month) != month:
                    month = (day.year, day.month)
                    pairs["main"] = form_pairs(HL, E, full, g, np.ones(N, bool))
                    pairs["held"] = form_pairs(HL, E, full, g, held_ok) if prev else []
                    npm.append(len(pairs["main"]))
                t0 = pd.Timestamp(f"{day} 09:30", tz=ET).value
                for key, pk, nw in (("main", "main", True), ("nonews", "main", False), ("held", "held", True)):
                    tr = day_trades(pairs[pk], d, g, LC, LO, Ey, HL, E, full, news, syms, t0, use_news=nw)
                    rows[key] += [(day, y, *t) for t in tr]
                    ntr += len(tr) if key == "main" else 0
            print(f"  {y}: {D} days ({int((~fulls[-1]).sum())} half/thin), main trades {ntr}, pairs by month {npm}, "
                  f"December top 3 {[(syms[a], syms[b], round(c, 3)) for a, b, c in pairs['main'][:3]]}", flush=True)
            del LC, LO

    cols = ["date", "year", "a", "b", "rho", "sig_bar", "hold", "rev", "z", "bps"]
    T = {k: pd.DataFrame(v, columns=cols) for k, v in rows.items()}
    T["main"].to_csv(os.path.join(CACHE, "intraday_pairs_trades.csv"), index=False)
    ok = stats(T["main"], "PRIMARY: top-50 pairs, 3 sd, no headline, all 277, 2016-26", held=T["held"])
    stats(T["held"], "held out: pairs among rank 101-277 (prior-year $ volume), 2017-26", gated=False)
    stats(T["nonews"], "reported: no headline filter", gated=False)

    spike = pd.read_csv(os.path.join(CACHE, "spike_wide_trades.csv"))
    spike = spike[spike["rank"] <= 25]
    sday = (spike["bps"] - 4.0).groupby(pd.to_datetime(spike["entry"], utc=True).dt.tz_convert(ET).dt.date).sum()
    m = T["main"]
    j = pd.concat([(m["bps"] - COST).groupby(m["date"]).sum(), sday], axis=1, join="inner")
    print(f"\n  daily P&L corr with spike_mom rank 1-25: {j.corr().iloc[0, 1]:+.2f} on {len(j)} shared days")
    print(f"\nVERDICT intraday-pairs: {'PASS' if ok else 'FAIL'}")


if __name__ == "__main__":
    main()
