"""
factor-composite (research/journal.md, 2026-11-14 PRE-REGISTRATION): 18 published factors, LightGBM walk-forward,
weekly long-short on mid/small caps (U), replicated on the 277 liquid names (L).

Usage: python scripts/research/factor_composite.py [U|L]   (both when omitted) -> research/factor_composite.log
"""
import glob
import gzip
import json
import math
import os
import sys
from collections import defaultdict

import lightgbm as lgb
import numpy as np
import pandas as pd
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "..", ".cache")
U = os.path.join(CACHE, "u")
LOG = os.path.join(HERE, "..", "..", "research", "factor_composite_{}.log")
KNOWN_AFTER = pd.Timedelta(days=90)
SPLITS = [1.5, 2, 3, 4, 5, 10, 20]
FEATURES = ["ret1", "ret5", "ret21", "mom12_1", "hi52", "vol21", "vol63", "max21", "amihud", "volratio", "dv60",
            "logcap", "gap1", "newsgap", "news5", "issuance", "ey", "bm"]
B1 = {"ret1": -1, "ret5": -1, "mom12_1": 1, "hi52": 1, "vol21": -1, "max21": -1, "issuance": -1, "ey": 1, "bm": 1}
B2 = {"mom12_1": 1, "ret5": -1}
HOLD, EMBARGO, HELD_FROM, LAST = 5, 10, "2024-01-01", "2026-10-31"
PARAMS = dict(objective="regression", n_estimators=300, learning_rate=0.03, num_leaves=31, min_child_samples=500,
              colsample_bytree=0.8, subsample=0.8, subsample_freq=1, random_state=20261114, verbose=-1)
COST = {"U": 10.0, "L": 5.0}
ETFS = {"SPY", "QQQ", "IWM", "DIA", "XLF", "XLE", "XLK", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLC", "XLRE",
        "GLD", "SLV", "TLT", "HYG", "LQD", "EEM", "EFA", "GDX", "USO", "SMH", "XBI", "KRE", "XOP", "ARKK", "VXX",
        "UVXY", "SQQQ", "TQQQ", "SOXL", "SOXS", "VOO", "VTI", "IVV", "FXI", "EWZ", "IBIT", "TNA", "TZA", "SPXL",
        "SPXS", "UPRO", "SH", "SDS", "LABU", "NUGT", "JNUG", "BITO"}

out_lines: list = []


def say(s: str = "") -> None:
    print(s, flush=True)
    out_lines.append(s)


# ---------- data ----------
def l_names() -> list:
    syms = {os.path.basename(p).split("_")[1] for p in glob.glob(os.path.join(CACHE, "m1_*_*.pkl"))}
    return sorted(syms - ETFS)


def _m1_daily(sym: str) -> pd.DataFrame:
    sys.path.insert(0, HERE)
    from flash_replay import load   # handles both cached layouts, returns ET-indexed RTH bars
    m = load(sym)
    if m is None or m.empty:
        return pd.DataFrame()
    g = m.groupby(m.index.tz_localize(None).normalize())
    d = pd.DataFrame({"o": g["open"].first(), "h": g["high"].max(), "l": g["low"].min(),
                      "c": g["close"].last(), "v": g["volume"].sum()})
    d.index.name = "date"
    return d.reset_index().assign(symbol=sym)


def l_daily(syms: list) -> pd.DataFrame:
    """Daily bars for the 277 from the 1-min cache (the u daily download left them out); cached once."""
    path = os.path.join(CACHE, "l_daily.csv.gz")
    if not os.path.exists(path):
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor() as ex:
            pd.concat(list(ex.map(_m1_daily, syms + ["SPY"]))).to_csv(path, index=False)
    return pd.read_csv(path)


def load_daily(keep: set) -> dict:
    parts = []
    if keep & set(l_names()):
        parts.append(l_daily(l_names()))
    for p in sorted(glob.glob(os.path.join(U, "daily", "*.csv.gz"))):
        d = pd.read_csv(p)
        parts.append(d[d["symbol"].isin(keep)])
    d = pd.concat(parts)
    d["date"] = pd.to_datetime(d["date"])
    d = d.drop_duplicates(["date", "symbol"])
    return {k: d.pivot(index="date", columns="symbol", values=k).sort_index() for k in ("o", "h", "l", "c", "v")}


def split_adjust(px: dict) -> tuple[dict, int]:
    """Divide prices before a detected split by its ratio (journal rule): close-to-next-open within 3% of k or 1/k."""
    o, c = px["o"], px["c"]
    ratio = o / c.shift(1)
    mult = pd.DataFrame(1.0, index=o.index, columns=o.columns)
    n = 0
    for k in SPLITS:
        for r in (k, 1 / k):
            hit = (ratio / r - 1).abs() < 0.03
            hit &= np.log(ratio).abs() > 0.3
            mult[hit] = r
            n += int(hit.sum().sum())
    # price at t is multiplied by the product of split ratios that happen AFTER t
    after = mult[::-1].cumprod()[::-1].shift(-1).fillna(1.0)
    out = {k: px[k] * after for k in ("o", "h", "l", "c")}
    out["v"] = px["v"] / after
    out["raw_c"] = px["c"]
    return out, n


def cik_map() -> dict:
    j = json.load(open(os.path.join(U, "in", "company_tickers.json"), encoding="utf-8"))
    return {v["ticker"].replace("-", "."): int(v["cik_str"]) for v in j.values()} | \
        {v["ticker"]: int(v["cik_str"]) for v in j.values()}


def known_series(df: pd.DataFrame, dates: pd.DatetimeIndex, syms: list, cmap: dict) -> pd.DataFrame:
    """df: cik, known, val -> wide (dates x syms) of the latest value known on each date."""
    out = pd.DataFrame(index=dates, columns=syms, dtype=float)
    g = {cik: s.sort_values("known") for cik, s in df.groupby("cik")}
    for s in syms:
        cik = cmap.get(s)
        if cik not in g:
            continue
        x = g[cik].drop_duplicates("known", keep="last").set_index("known")["val"]
        out[s] = x.reindex(x.index.union(dates)).sort_index().ffill().reindex(dates).to_numpy()
    return out


def fundamentals(dates, syms, cmap) -> dict:
    sh = pd.read_csv(os.path.join(U, "shares.csv.gz"))
    sh = sh.sort_values("source").drop_duplicates(["cik", "end"], keep="first")   # dei before us-gaap
    sh["known"] = pd.to_datetime(sh["end"]) + KNOWN_AFTER
    shares = known_series(sh[sh["val"] > 0], dates, syms, cmap)
    f = pd.read_csv(os.path.join(U, "fundamentals.csv.gz"))
    f["known"] = pd.to_datetime(f["end"]) + KNOWN_AFTER
    eq = known_series(f[f["concept"] == "StockholdersEquity"], dates, syms, cmap)
    ni = f[f["concept"] == "NetIncomeLoss"].sort_values(["cik", "end"]).drop_duplicates(["cik", "end"])
    ni["ttm"] = ni.groupby("cik")["val"].transform(lambda s: s.rolling(4).sum())
    ni["span"] = pd.to_datetime(ni["end"]).groupby(ni["cik"]).diff(3).dt.days
    ttm = known_series(ni[ni["span"].between(250, 300)].assign(val=lambda x: x["ttm"]), dates, syms, cmap)
    sh_lag = known_series(sh[sh["val"] > 0].assign(known=lambda x: x["known"] + pd.Timedelta(days=365)), dates, syms,
                          cmap)
    return {"shares": shares, "equity": eq, "ttm_ni": ttm, "shares_1y": sh_lag}


def news_l(syms) -> dict:
    out = defaultdict(list)
    for s in syms:
        p = os.path.join(CACHE, "news", f"{s}.csv.gz")
        if os.path.exists(p):
            n = pd.read_csv(p, usecols=["created_at"])
            out[s] = pd.to_datetime(n["created_at"], utc=True).dt.tz_convert("America/New_York").tolist()
    return out


def news_u(syms: set) -> dict:
    out = defaultdict(list)
    for p in sorted(glob.glob(os.path.join(U, "news", "*.csv.gz"))):
        n = pd.read_csv(p)
        n["syms"] = n["symbols"].fillna("").str.split()
        n = n[n["syms"].str.len().between(1, 3)]
        t = pd.to_datetime(n["created_at"], utc=True).dt.tz_convert("America/New_York")
        for ts, ss in zip(t, n["syms"]):
            for s in ss:
                if s in syms:
                    out[s].append(ts)
    return out


def news_matrices(news: dict, idx: pd.DatetimeIndex, syms: list) -> tuple:
    """overnight[d, s] = headline between prior 16:00 and d 09:30; daycount[d, s] = headlines up to d 16:00."""
    over = np.zeros((len(idx), len(syms)), dtype=bool)
    cnt = np.zeros((len(idx), len(syms)))
    loc = idx.tz_localize("America/New_York")
    closes = loc + pd.Timedelta(hours=16)
    opens = loc + pd.Timedelta(hours=9, minutes=30)
    for k, s in enumerate(syms):
        ts = pd.DatetimeIndex(sorted(news.get(s, [])))
        if len(ts) == 0:
            continue
        i = closes.searchsorted(ts)          # session whose 16:00 is the first at or after ts
        np.add.at(cnt[:, k], i[i < len(idx)], 1.0)
        j = opens.searchsorted(ts)           # first session opening after ts
        prev_close = closes[np.clip(j - 1, 0, len(idx) - 1)]
        on = (j < len(idx)) & ((j == 0) | (ts >= prev_close))
        over[j[on], k] = True
    return pd.DataFrame(over, index=idx, columns=syms), pd.DataFrame(cnt, index=idx, columns=syms)


# ---------- features ----------
def build(px, fund, over, cnt, members: pd.DataFrame, fdates) -> pd.DataFrame:
    c, o, h, v = px["c"], px["o"], px["h"], px["v"]
    r = np.log(c / c.shift(1))
    dv = (px["raw_c"] * px["v"]).rolling(60, min_periods=40).median()
    gap = o / c.shift(1) - 1
    feats = {
        "ret1": c / c.shift(1) - 1, "ret5": c / c.shift(5) - 1, "ret21": c / c.shift(21) - 1,
        "mom12_1": c.shift(21) / c.shift(252) - 1, "hi52": c / h.rolling(252, min_periods=200).max(),
        "vol21": r.rolling(21, min_periods=15).std(), "vol63": r.rolling(63, min_periods=40).std(),
        "max21": r.rolling(21, min_periods=15).max(),
        "amihud": (r.abs() / (px["raw_c"] * v)).rolling(63, min_periods=40).mean(),
        "volratio": v.rolling(5).mean() / v.rolling(60, min_periods=40).mean(), "dv60": np.log(dv),
        "gap1": gap,
    }
    cap = fund["shares"] * px["raw_c"]
    feats["logcap"] = np.log(cap)
    last_news_gap = gap.where(over).ffill(limit=4)       # the latest overnight-news gap within the week
    feats["newsgap"] = last_news_gap.fillna(0.0)
    feats["news5"] = cnt.rolling(5).sum()
    feats["issuance"] = fund["shares"] / fund["shares_1y"] - 1
    feats["ey"] = fund["ttm_ni"] / cap
    feats["bm"] = fund["equity"] / cap
    fpos = c.index.get_indexer(fdates)
    entry = o.shift(-1).iloc[fpos]
    exit_ = o.shift(-1 - HOLD).iloc[fpos]
    last = c.ffill().shift(-HOLD).iloc[fpos]                # delisted mid-hold: last close seen
    fwd = (exit_.fillna(last) / entry - 1)
    rows = []
    for k, d in enumerate(fdates):
        m = members.columns[members.loc[d].to_numpy()] if d in members.index else []
        if len(m) < 50:
            continue
        x = pd.DataFrame({f: feats[f].loc[d, m] for f in FEATURES})
        x["fwd"] = fwd.loc[d, m]
        x["uni63"] = (c.loc[d, m] / c.shift(63).loc[d, m] - 1).mean()   # regime, known at formation
        x["univol"] = feats["vol21"].loc[d, m].mean()
        x["date"] = d
        x["sym"] = m
        rows.append(x.dropna(subset=["fwd"]))
    df = pd.concat(rows, ignore_index=True)
    lo, hi = df.groupby("date")["fwd"].transform(lambda s: s.quantile(0.01)), \
        df.groupby("date")["fwd"].transform(lambda s: s.quantile(0.99))
    df["fwd"] = df["fwd"].clip(lo, hi)
    df["excess"] = df["fwd"] - df.groupby("date")["fwd"].transform("mean")
    for f in FEATURES:
        df[f] = df[f].replace([np.inf, -np.inf], np.nan)
        df[f] = df.groupby("date")[f].rank(pct=True)
        df[f] = df[f].fillna(df.groupby("date")[f].transform("median")).fillna(0.5)
    df["target"] = df.groupby("date")["excess"].rank(pct=True)
    return df


# ---------- model and scoring ----------
def walk_forward(df: pd.DataFrame, sessions: pd.DatetimeIndex) -> pd.Series:
    """Train on formation dates whose 5-session target (exit at t+1+HOLD) ends >= EMBARGO sessions before Y."""
    pred = pd.Series(np.nan, index=df.index)
    for y in range(2019, 2027):
        start = pd.Timestamp(f"{y}-01-01")
        test = df["date"].dt.year == y
        if not test.any():
            continue
        cut = sessions[sessions.searchsorted(start) - EMBARGO - 1 - HOLD]
        train = df["date"] <= cut
        m = lgb.LGBMRegressor(**PARAMS).fit(df.loc[train, FEATURES], df.loc[train, "target"])
        pred[test] = m.predict(df.loc[test, FEATURES])
        if y == 2024:
            imp = sorted(zip(m.feature_importances_, FEATURES), reverse=True)
            say("  feature importance (model for 2024): " + ", ".join(f"{f} {i}" for i, f in imp[:10]))
    return pred


def evaluate(df: pd.DataFrame, col: str, cost: float) -> dict:
    d = df.dropna(subset=[col])
    ic = d.groupby("date").apply(lambda x: x[col].corr(x["excess"], method="spearman"))
    legs, prev = [], {"L": set(), "S": set()}
    for dt, x in d.groupby("date"):
        q = x[col].rank(pct=True)
        L, S = set(x["sym"][q > 0.9]), set(x["sym"][q <= 0.1])
        gross = (x["fwd"][q > 0.9].mean() - x["fwd"][q <= 0.1].mean()) * 1e4
        to = (1 - len(L & prev["L"]) / max(len(L), 1)) + (1 - len(S & prev["S"]) / max(len(S), 1))
        legs.append((dt, gross, gross - cost * 2 * to, to))
        prev = {"L": L, "S": S}
    ls = pd.DataFrame(legs, columns=["date", "gross", "net", "turnover"]).set_index("date")
    return {"ic": ic, "ls": ls}


def tstat(s: pd.Series) -> float:
    s = s.dropna()
    return s.mean() / s.std() * math.sqrt(len(s)) if len(s) > 2 and s.std() > 0 else float("nan")


def deflated_sharpe(sr_obs: float, n_obs: int, srs: list, skew: float, kurt: float) -> float:
    """Bailey & Lopez de Prado: P(true SR > 0) after N trials (per-period SRs)."""
    n = len(srs)
    var = np.var(srs, ddof=1)
    emc = 0.5772156649
    sr0 = math.sqrt(var) * ((1 - emc) * stats.norm.ppf(1 - 1 / n) + emc * stats.norm.ppf(1 - 1 / (n * math.e)))
    z = (sr_obs - sr0) * math.sqrt(n_obs - 1) / math.sqrt(1 - skew * sr_obs + (kurt - 1) / 4 * sr_obs ** 2)
    return float(stats.norm.cdf(z))


def report(name: str, ev: dict, held: bool) -> None:
    ic, ls = ev["ic"], ev["ls"]
    sel = (ic.index >= HELD_FROM) if held else (ic.index < HELD_FROM)
    i, l = ic[sel], ls[(ls.index >= HELD_FROM) if held else (ls.index < HELD_FROM)]
    say(f"  {name}: IC {i.mean():+.4f} (t {tstat(i):+.2f}, weeks {len(i)}); L/S gross {l['gross'].mean():+.1f}, "
        f"net {l['net'].mean():+.1f} bps/wk (t {tstat(l['net']):+.2f}), turnover {l['turnover'].mean():.2f}, "
        f"ann. Sharpe {l['net'].mean() / l['net'].std() * math.sqrt(52):+.2f}")


def run(univ: str) -> None:
    say(f"===== universe {univ} =====")
    cmap = cik_map()
    ud = pd.read_csv(os.path.join(U, "universe_days.csv.gz"), parse_dates=["date"])
    lset = l_names()
    usyms = set(ud["symbol"])
    keep = usyms if univ == "U" else set(lset)
    px, nsplit = split_adjust(load_daily(keep | {"SPY"}))
    idx = px["c"].index
    syms = [s for s in px["c"].columns if s != "SPY"]
    say(f"daily bars: {len(syms)} names, {len(idx)} sessions, splits adjusted {nsplit}")
    if univ == "U":
        ud = ud[ud["symbol"].isin(syms)]
        members = pd.crosstab(ud["date"], ud["symbol"]).reindex(index=idx, columns=syms, fill_value=0).astype(bool)
        news = news_u(set(syms))
    else:
        members = px["c"][syms].notna() & px["c"][syms].shift(252).notna()
        news = news_l(syms)
    fund = fundamentals(idx, syms, cmap)
    over, cnt = news_matrices(news, idx, syms)
    first = members.any(axis=1).idxmax()
    sess = idx[idx >= first]
    fdates = sess[::5]
    fdates = fdates[(fdates <= LAST) & (idx.get_indexer(fdates) + 1 + HOLD < len(idx))]
    df = build({k: v[syms] for k, v in px.items()}, fund, over[syms], cnt[syms], members, fdates)
    say(f"formation dates {df['date'].nunique()} ({df['date'].min():%Y-%m-%d}..{df['date'].max():%Y-%m-%d}), "
        f"name-weeks {len(df)}, names/week {df.groupby('date').size().median():.0f}; coverage: "
        + ", ".join(f"{f} {1 - (df[f] == 0.5).mean():.0%}" for f in ("logcap", "ey", "bm", "issuance", "newsgap")))
    df["model"] = walk_forward(df, idx)
    df["b1"] = sum(sgn * df[f] for f, sgn in B1.items())
    df["b2"] = sum(sgn * df[f] for f, sgn in B2.items())
    scored = df[df["date"].dt.year >= 2019].copy()
    evs = {k: evaluate(scored, k, COST[univ]) for k in ("model", "b1", "b2")}
    for held in (False, True):
        say(f"-- {'HELD OUT 2024-01..2026-10 (verdict)' if held else 'design 2019-2023'}")
        for k in ("model", "b1", "b2"):
            report(k, evs[k], held)
    m, b2 = evs["model"], evs["b2"]
    h = m["ic"].index >= HELD_FROM
    ic_h, ls_h = m["ic"][h], m["ls"][m["ls"].index >= HELD_FROM]["net"]
    diff = (m["ic"] - b2["ic"])[h]
    years = ls_h.groupby(ls_h.index.year).sum()
    top5 = ls_h.nlargest(5).sum() / ls_h.sum() if ls_h.sum() > 0 else float("nan")
    design_sign = np.sign(m["ic"][~h].mean()) == np.sign(ic_h.mean())
    singles = {}
    say("-- single factors (rank IC vs 5-day excess; design | held out):")
    for f in FEATURES:
        ic = scored.groupby("date").apply(lambda x: x[f].corr(x["excess"], method="spearman"))
        singles[f] = evaluate(scored, f, COST[univ])
        say(f"  {f:9s} {ic[ic.index < HELD_FROM].mean():+.4f} (t {tstat(ic[ic.index < HELD_FROM]):+.2f}) | "
            f"{ic[ic.index >= HELD_FROM].mean():+.4f} (t {tstat(ic[ic.index >= HELD_FROM]):+.2f})")
    srs = [e["ls"]["net"][e["ls"].index >= HELD_FROM].mean() / e["ls"]["net"][e["ls"].index >= HELD_FROM].std()
           for e in list(evs.values()) + list(singles.values())]
    srs = [abs(s) for s in srs if np.isfinite(s)]   # a factor's sign is free, so count its best direction
    sr = ls_h.mean() / ls_h.std()
    dsr = deflated_sharpe(sr, len(ls_h), srs, float(stats.skew(ls_h)), float(stats.kurtosis(ls_h, fisher=False)))
    say("-- regimes (held out, model net bps/wk):")
    reg = scored.groupby("date")[["uni63", "univol"]].first()
    vmed = reg["univol"].median()
    for name, mask in (("universe 63d up", reg["uni63"] > 0), ("universe 63d down", reg["uni63"] <= 0),
                       ("high vol", reg["univol"] > vmed), ("low vol", reg["univol"] <= vmed)):
        x = ls_h[ls_h.index.isin(mask[mask].index)]
        say(f"  {name}: n {len(x)}, net {x.mean():+.1f} (t {tstat(x):+.2f})")
    say("-- per year, model (IC, net bps/wk):")
    for y in range(2019, 2027):
        i, l = m["ic"][m["ic"].index.year == y], m["ls"]["net"][m["ls"].index.year == y]
        say(f"  {y}: IC {i.mean():+.4f}, net {l.mean():+.1f}, weeks {len(i)}")
    checks = {
        "1 IC >= 0.02 and t >= 2.87": ic_h.mean() >= 0.02 and tstat(ic_h) >= 2.87,
        "2 L/S net > 0 with t >= 2.87": ls_h.mean() > 0 and tstat(ls_h) >= 2.87,
        "3 model IC - B2 IC > 0, t >= 2": diff.mean() > 0 and tstat(diff) >= 2.0,
        "4 >= 2 of 3 held-out years net > 0": int((years > 0).sum()) >= 2,
        "5 top 5 weeks < 50% of net": bool(top5 < 0.5),
        "6 design IC same sign": bool(design_sign),
    }
    say(f"-- GATE ({univ}): " + "; ".join(f"{k}: {'ok' if v else 'FAIL'}" for k, v in checks.items()))
    say(f"   model - B2 IC {diff.mean():+.4f} (t {tstat(diff):+.2f}); top-5-week share {top5:.0%}; "
        f"deflated Sharpe P(SR>0 | N={len(srs)}) {dsr:.3f}")
    say(f"   held-out model IC mean {ic_h.mean():+.4f} (sign for L replication)")
    say()


if __name__ == "__main__":
    for u in (sys.argv[1:] or ["U", "L"]):
        out_lines.clear()
        run(u)
        with open(LOG.format(u), "w", encoding="utf-8") as fh:
            fh.write("\n".join(out_lines) + "\n")
