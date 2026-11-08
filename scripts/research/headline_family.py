"""
Headline family, scorer S1 (research/journal.md, 2026-11-07 PRE-REGISTRATION headline-family, and the
2026-11-08 desk run plan under it).

S1 = Loughran-McDonald word list: score = positive minus negative words (runs of A-Z after uppercasing).
Single-name headline = its (created_at, headline) pair is in fewer than 5 of the 277 news files.
  T1 news-direction: headline 09:45-14:50 ET, enter at the open of the 2nd bar after it, its direction,
     hold 60 bars, same day; first scored headline per name per 60 min, no overlapping trades per name.
  T2 spike-agreement: vol >= 8x, |move| >= 5 sd, 09:35-14:58, 1-bar delay, hold 60; trade only when the
     summed headline score of the prior 60 min agrees with the spike (disagreeing group reported).
  T3 earnings-open: 8-K 2.02 filed from 16:00 of the previous trading day to 09:25; sign = summed score
     from 1 h before the filing to 09:25; enter at the 10:00 open, exit at the 15:55 open.
Costs 2 bps a side. Gate per test: n >= 300, mean net >= 3, day t >= 2.9, >= 70% of years positive,
same sign 2016-20 and 2021-26, top symbol <= 25%; held out (rank 101-277): same sign, day t >= 2.

Usage: python scripts/research/headline_family.py [--workers=6]
"""
import collections
import csv
import glob
import gzip
import json
import os
import re
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
COST = 4.0                      # bps round trip
RECAP_NAMES = 5
VOL_X, K, HOLD, DELAY = 8, 5, 60, 1
NS_MIN = 60_000_000_000
TOKEN = re.compile(r"[A-Z]+")
ET = "America/New_York"


def names() -> list[str]:
    import backtest_scalp as bt
    extra = json.load(open(os.path.join(CACHE, "m1_extra_symbols.json")))
    extra = extra.get("symbols", extra) if isinstance(extra, dict) else extra
    return sorted(set(bt.SYMBOLS + bt.WIDE) | set(extra))


def lexicon():
    lm = pd.read_csv(os.path.join(CACHE, "lexicon", "lm_master_1993-2025.csv"), usecols=["Word", "Negative", "Positive"])
    lm["Word"] = lm["Word"].astype(str).str.upper()
    return set(lm.loc[lm["Positive"] != 0, "Word"]), set(lm.loc[lm["Negative"] != 0, "Word"])


def scored_news() -> dict:
    """{sym: (sorted UTC ns, score)} for single-name headlines, same-timestamp scores summed."""
    pos, neg = lexicon()
    rows, tags = {}, collections.Counter()
    for path in glob.glob(os.path.join(CACHE, "news", "*.csv.gz")):
        sym = os.path.basename(path)[:-len(".csv.gz")]
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            rows[sym] = [(r["created_at"], r["headline"] or "") for r in csv.DictReader(f)]
        tags.update(set(rows[sym]))
    out = {}
    for sym, items in rows.items():
        keep = [(c, h) for c, h in items if tags[(c, h)] < RECAP_NAMES]
        if not keep:
            out[sym] = (np.zeros(0, "int64"), np.zeros(0))
            continue
        sc = []
        for _, h in keep:
            w = TOKEN.findall(h.upper())
            sc.append(sum(x in pos for x in w) - sum(x in neg for x in w))
        s = pd.Series(sc, index=pd.to_datetime([c for c, _ in keep], utc=True).as_unit("ns").asi8).groupby(level=0).sum()
        out[sym] = (s.index.to_numpy("int64"), s.to_numpy(float))
    return out


def filings(syms) -> dict:
    """{sym: sorted ET-naive ns of 8-K 2.02 acceptances}, cache only."""
    import backtest_pead as bp
    real = bp.sec_json
    bp.sec_json = lambda url, path: real(url, path) if os.path.exists(path) else None   # never touch the network
    f = bp.earnings_filings(list(syms))
    return {s: np.sort(g["accepted"].to_numpy("datetime64[ns]").astype("int64")) for s, g in f.groupby("symbol")}


def window_sum(stamps, scores, lo, hi):
    """Sum of scores with lo <= stamp < hi (vectorised over lo, hi)."""
    c = np.concatenate([[0.0], np.cumsum(scores)])
    return c[np.searchsorted(stamps, hi)] - c[np.searchsorted(stamps, lo)]


def run_name(args):
    sym, stamps, scores, fil = args
    rows, dv = [], {}
    for y in YEARS:
        p = os.path.join(CACHE, f"m1_{sym}_{y}.pkl")
        if not os.path.exists(p):
            continue
        df = pd.read_pickle(p).astype("float64")
        if df.empty:
            continue
        df = df[~df.index.duplicated()].sort_index()
        dates = df.index.date
        dv[y] = (df["close"] * df["volume"]).groupby(dates).sum().mean()
        o, c, v = df["open"].to_numpy(), df["close"].to_numpy(), df["volume"].to_numpy()
        hm = (df.index.hour * 60 + df.index.minute).to_numpy()
        t_utc = df.index.tz_convert("UTC").as_unit("ns").asi8
        dnum = pd.factorize(dates)[0]
        n = len(df)

        # T1: news direction
        lo, hi = np.searchsorted(stamps, [t_utc[0], t_utc[-1] + NS_MIN])
        last_taken, busy = -np.inf, -1
        for k in range(lo, hi):
            s = scores[k]
            if s == 0:
                continue
            ts = stamps[k]
            et = pd.Timestamp(ts, tz="UTC").tz_convert(ET)
            m = et.hour * 60 + et.minute + et.second / 60
            if m < 9 * 60 + 45 or m > 14 * 60 + 50 or ts - last_taken < 60 * NS_MIN:
                continue
            e = np.searchsorted(t_utc, ts, side="right") + 1
            x = e + HOLD - 1
            if x >= n or e <= busy or dnum[x] != dnum[e] or dnum[e] != dnum[e - 1] or hm[x] > 959:
                continue
            if pd.Timestamp(t_utc[e - 1], tz="UTC").tz_convert(ET).date() != et.date():
                continue
            side = np.sign(s)
            rows.append(("T1", sym, y, dates[e], side, side * np.log(c[x] / o[e]) * 1e4, t_utc[e]))
            last_taken, busy = ts, x

        # T2: spike agreement (backtest_scalp.features, within-day trailing stats excluding the bar)
        ret = np.log(c / o)
        g = pd.DataFrame({"r": ret, "v": v, "d": dnum})
        vm = g.groupby("d")["v"].transform(lambda a: a.rolling(30, min_periods=20).mean().shift(1)).to_numpy()
        sd = g.groupby("d")["r"].transform(lambda a: a.rolling(30, min_periods=20).std().shift(1)).to_numpy()
        with np.errstate(invalid="ignore"):
            sig = (hm >= 9 * 60 + 35) & (hm <= 14 * 60 + 58) & (sd > 0) & (v >= VOL_X * vm) & (np.abs(ret) >= K * sd)
        idx = np.flatnonzero(sig)
        hs = window_sum(stamps, scores, t_utc[idx] - 60 * NS_MIN, t_utc[idx]) if len(idx) else np.zeros(0)
        busy = -1
        for i, h in zip(idx, hs):
            j = i + HOLD + DELAY
            if i <= busy or j >= n or dnum[j] != dnum[i] or hm[j] > 959:
                continue
            side = np.sign(ret[i])
            bps = side * np.log(c[j] / o[i + 1 + DELAY]) * 1e4
            busy = j
            if h == 0:
                continue
            rows.append(("T2" if np.sign(h) == side else "T2dis", sym, y, dates[i], side, bps, t_utc[i + 1 + DELAY]))

        # T3: earnings open
        if fil is not None and len(fil):
            et_naive = df.index.tz_localize(None).as_unit("ns").asi8
            days = pd.Series(np.arange(n)).groupby(dnum)
            first, last = days.min().to_numpy(), days.max().to_numpy()
            for d in range(1, len(first)):
                day0 = pd.Timestamp(et_naive[first[d]]).normalize()
                prev = pd.Timestamp(et_naive[first[d - 1]]).normalize()
                a = (prev + pd.Timedelta(hours=16)).value
                b = (day0 + pd.Timedelta(hours=9, minutes=25)).value
                f0, f1 = np.searchsorted(fil, [a, b])
                if f1 <= f0:
                    continue
                sl = slice(first[d], last[d] + 1)
                hmd = hm[sl]
                ie, ix = np.flatnonzero(hmd == 600), np.flatnonzero(hmd == 955)
                if not len(ie) or not len(ix):
                    continue
                lo_utc = pd.Timestamp(fil[f0] - 3600 * 10**9).tz_localize(ET, ambiguous=True, nonexistent="shift_forward").tz_convert("UTC").value
                hi_utc = pd.Timestamp(b).tz_localize(ET).tz_convert("UTC").value
                h = window_sum(stamps, scores, np.array([lo_utc]), np.array([hi_utc]))[0]
                if h == 0:
                    continue
                e, x = first[d] + ie[0], first[d] + ix[0]
                side = np.sign(h)
                rows.append(("T3", sym, y, dates[e], side, side * np.log(o[x] / o[e]) * 1e4, t_utc[e]))
    return sym, rows, dv


def gate(t: pd.DataFrame, label: str, held: pd.DataFrame | None = None) -> bool:
    if t.empty:
        print(f"\n== {label}: n=0")
        return False
    x = t["bps"] - COST
    day = x.groupby(t["date"]).sum()
    day_t = day.mean() / day.std(ddof=1) * np.sqrt(len(day)) if len(day) > 1 else 0.0
    tr_t = x.mean() / x.std(ddof=1) * np.sqrt(len(x))
    by = x.groupby(t["year"]).mean()
    syms = x.groupby(t["sym"]).sum()
    share = syms.max() / syms[syms > 0].sum() if (syms > 0).any() else float("nan")
    top = np.sort(x.to_numpy())[::-1][:max(1, len(x) // 100)].sum() / x.sum() if x.sum() else float("nan")
    early, late = x[t["year"].isin(EARLY)].mean(), x[~t["year"].isin(EARLY)].mean()
    print(f"\n== {label}")
    print(f"  n={len(x)} on {len(day)} days, net {x.mean():+.2f} bps (per-trade t {tr_t:+.2f}, day t {day_t:+.2f}), "
          f"win {(x > 0).mean():.0%}, median {x.median():+.1f}, top-1% share {top:+.0%}")
    print(f"  by year: " + "  ".join(f"{y}:{v:+.1f}/{int(m)}" for (y, v), m in zip(by.items(), t.groupby("year").size())))
    print(f"  2016-20 {early:+.2f}, 2021-26 {late:+.2f}; top symbol {syms.idxmax()} {share:.0%}; "
          f"long {x[t['side'] > 0].mean():+.1f} (n={int((t['side'] > 0).sum())}), short {x[t['side'] < 0].mean():+.1f} (n={int((t['side'] < 0).sum())})")
    print(f"  at 4 bps/side: {(t['bps'] - 8).mean():+.2f}")
    checks = {
        "n >= 300": len(x) >= 300,
        "mean net >= 3": x.mean() >= 3,
        "day t >= 2.9": day_t >= 2.9,
        "years+ >= 70%": (by > 0).mean() >= 0.7,
        "same sign 2016-20 / 2021-26": early * late > 0,
        "top symbol <= 25%": share <= 0.25,
    }
    if held is not None:
        hx = held["bps"] - COST
        hd = hx.groupby(held["date"]).sum()
        ht = hd.mean() / hd.std(ddof=1) * np.sqrt(len(hd)) if len(hd) > 1 else 0.0
        print(f"  held out rank 101-277: n={len(hx)}, net {hx.mean() if len(hx) else float('nan'):+.2f}, day t {ht:+.2f}")
        checks["held out same sign"] = len(hx) > 0 and hx.mean() * x.mean() > 0
        checks["held out day t >= 2"] = ht >= 2
    for k, v in checks.items():
        print(f"    {'ok  ' if v else 'FAIL'} {k}")
    ok = all(checks.values())
    print(f"  verdict: {'PASS' if ok else 'FAIL'} ({sum(checks.values())}/{len(checks)})")
    return ok


def main():
    workers = int(next((a.split("=")[1] for a in sys.argv[1:] if a.startswith("--workers=")), 6))
    syms = names()
    news = scored_news()
    allsc = np.concatenate([s for _, s in news.values()])
    print(f"names {len(syms)}; single-name headlines {len(allsc)}, scored good {(allsc > 0).mean():.0%}, "
          f"bad {(allsc < 0).mean():.0%}, neutral {(allsc == 0).mean():.0%}")
    fil = filings(syms)
    print(f"8-K 2.02 filings for {len(fil)} names, {sum(len(v) for v in fil.values())} total; "
          f"none for {len(set(syms) - set(fil))}: {' '.join(sorted(set(syms) - set(fil)))}")
    empty = (np.zeros(0, "int64"), np.zeros(0))
    jobs = [(s, *news.get(s, empty), fil.get(s)) for s in syms]
    rows, dv = [], {}
    with ProcessPoolExecutor(workers) as ex:
        for k, (sym, r, d) in enumerate(ex.map(run_name, jobs)):
            rows += r
            dv.update({(sym, y): v for y, v in d.items()})
            if k % 25 == 0:
                print(f"  done {k + 1}/{len(syms)}", flush=True)
    t = pd.DataFrame(rows, columns=["test", "sym", "year", "date", "side", "bps", "entry_ns"])
    rank = {}
    for y in YEARS:
        prev = sorted(((dv[(s, y - 1)], s) for s in syms if (s, y - 1) in dv), reverse=True)
        rank.update({(s, y): r + 1 for r, (_, s) in enumerate(prev)})
    t["rank"] = [rank.get((s, y), 0) for s, y in zip(t["sym"], t["year"])]
    t.to_csv(os.path.join(CACHE, "headline_family_s1_trades.csv"), index=False)

    spike = pd.read_csv(os.path.join(CACHE, "spike_wide_trades.csv"))
    spike = spike[spike["rank"] <= 25]
    sday = (spike["bps"] - COST).groupby(pd.to_datetime(spike["entry"], utc=True).dt.tz_convert(ET).dt.date).sum()

    verdicts = {}
    for test in ("T1", "T2", "T3"):
        g = t[t["test"] == test]
        verdicts[test] = gate(g, f"{test} (S1), all 277 names, 2016-26", g[g["rank"] >= 101])
        d = (g["bps"] - COST).groupby(g["date"]).sum()
        j = pd.concat([d, sday], axis=1, join="inner")
        if len(j) > 2:
            print(f"  daily P&L corr with spike_mom rank 1-25 trades: {j.corr().iloc[0, 1]:+.2f} on {len(j)} shared days")
    gate(t[t["test"] == "T2dis"], "reported only: T2 disagreeing (spike against the headline)")
    print("\nFAMILY (S1): " + ", ".join(f"{k} {'PASS' if v else 'FAIL'}" for k, v in verdicts.items()))


if __name__ == "__main__":
    main()
