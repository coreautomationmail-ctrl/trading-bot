"""index-deletion-auction-v1 (pods/bench/replay_registry.json): ONE registered run.

Stage 1 (always): fetch official auction prints (Alpaca /v2/stocks/auctions, SIP) for every classified event
and print coverage. If the pooled exit + migration-down coverage is < 80%, stop.
Stage 2: buy at the closing auction of the last session before the effective date, sell at the next
opening auction. Compare with SPY and with 20 seeded random SHADOW names the same night.

Prices: close = the '6' print (closing auction, primary venue), else the largest 'M'; open = the 'O' print,
else the largest 'Q'. Usage: python backtest.py   (writes results.json next to this file)
"""
import json
import math
import random
import sys
import time
from pathlib import Path

import pandas as pd
import requests

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent.parent / "pods" / "bench"))
sys.path.insert(0, str(HERE.parent.parent / "pods" / "spike"))
import replay_fetch  # noqa: E402  (loads ../../.env keys)
import spike_pod  # noqa: E402

URL = "https://data.alpaca.markets/v2/stocks/auctions"
COST, COST_HI, BAR, SEED, N_RANDOM = 5.0, 10.0, 2.5, 20261122, 20
GATING = ("exit", "migration-down")


def get(symbols, start, end):
    out, token = {}, None
    while True:
        p = {"symbols": ",".join(symbols), "start": start, "end": end, "feed": "sip", "limit": 10000}
        if token:
            p["page_token"] = token
        for attempt in range(20):
            r = requests.get(URL, headers=spike_pod._headers(), params=p, timeout=60)
            if r.status_code in (429,) or r.status_code >= 500:
                time.sleep(5 * (attempt + 1))
                continue
            r.raise_for_status()
            break
        body = r.json()
        time.sleep(0.32)
        for s, days in (body.get("auctions") or {}).items():
            out.setdefault(s, []).extend(days)
        token = body.get("next_page_token")
        if not token:
            return out


def prices(days):
    """{date: (open_auction, close_auction)} from one symbol's auction days."""
    out = {}
    for d in days:
        o = d.get("o") or []
        c = d.get("c") or []
        op = next((x["p"] for x in o if x["c"] == "O"), None) or \
            (max((x for x in o if x["c"] == "Q"), key=lambda x: x["s"])["p"] if any(x["c"] == "Q" for x in o) else None)
        cp = next((x["p"] for x in c if x["c"] == "6"), None) or \
            (max((x for x in c if x["c"] == "M"), key=lambda x: x["s"])["p"] if any(x["c"] == "M" for x in c) else None)
        out[d["d"]] = (op, cp)
    return out


def tstat(xs):
    n = len(xs)
    if n < 3:
        return float("nan")
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m / sd * math.sqrt(n) if sd else float("nan")


def main():
    replay_fetch._load_env()
    ev = pd.read_csv(HERE / "events_classified.csv")
    ev = ev[ev.cls != "exclude"].reset_index(drop=True)
    shadow = sorted(s for s in spike_pod.SHADOW if s not in ("SPY", "QQQ", "IWM"))

    # sessions: SPY auction days over the whole window
    spy = prices(get(["SPY"], "2019-12-15", "2026-11-20")["SPY"])
    sessions = sorted(d for d, (o, c) in spy.items() if o and c)

    rows = []
    for e in ev.itertuples():
        before = [d for d in sessions if d < e.effective]
        if not before:
            continue
        d0 = before[-1]
        d1 = next((d for d in sessions if d > d0), None)
        a = prices(get([e.symbol], d0, d1).get(e.symbol, []))
        c0 = a.get(d0, (None, None))[1]
        o1 = a.get(d1, (None, None))[0] if d1 else None
        rows.append({"effective": e.effective, "symbol": e.symbol, "cls": e.cls, "index": e.index,
                     "d0": d0, "d1": d1, "close_auction": c0, "next_open_auction": o1})
    df = pd.DataFrame(rows)
    df["ok"] = df.close_auction.notna() & df.next_open_auction.notna()

    print("COVERAGE (events -> both auction prints present)")
    print(df.groupby("cls").ok.agg(["sum", "count"]).assign(pct=lambda x: (100 * x["sum"] / x["count"]).round(1)).to_string())
    g = df[df.cls.isin(GATING)]
    cov = g.ok.mean() if len(g) else 0
    print(f"pooled gating coverage: {cov:.1%}")
    df.to_csv(HERE / "coverage.csv", index=False)
    if cov < 0.80:
        print("STOP: coverage < 80% (registry rule). No returns computed.")
        return

    # stage 2: returns
    rng = random.Random(SEED)
    nights = sorted({(r.d0, r.d1) for r in df[df.ok].itertuples()})
    night_bench = {}
    for d0, d1 in nights:
        pick = rng.sample(shadow, N_RANDOM)
        a = get(pick, d0, d1)
        rets = []
        for s in pick:
            p = prices(a.get(s, []))
            c0, o1 = p.get(d0, (None, None))[1], p.get(d1, (None, None))[0]
            if c0 and o1:
                rets.append((o1 / c0 - 1) * 1e4)
        sp_c0, sp_o1 = spy.get(d0, (None, None))[1], spy.get(d1, (None, None))[0]
        night_bench[(d0, d1)] = ((sp_o1 / sp_c0 - 1) * 1e4 if sp_c0 and sp_o1 else None,
                                 sum(rets) / len(rets) if rets else None)
    ok = df[df.ok].copy()
    ok["gross"] = (ok.next_open_auction / ok.close_auction - 1) * 1e4
    ok["net"] = ok.gross - 2 * COST
    ok["net_hi"] = ok.gross - 2 * COST_HI
    ok["spy"] = [night_bench[(r.d0, r.d1)][0] for r in ok.itertuples()]
    ok["rand"] = [night_bench[(r.d0, r.d1)][1] for r in ok.itertuples()]
    ok.to_csv(HERE / "trades.csv", index=False)

    def summary(sub):
        sub = sub.dropna(subset=["spy", "rand"]).sort_values("d0")
        vs_spy, vs_rand = list(sub.net - sub.spy), list(sub.net - sub.rand)
        h = len(sub) // 2
        first, second = sub.iloc[:h], sub.iloc[h:]
        return {"n": len(sub), "mean_net_bps": round(sub.net.mean(), 1), "mean_net_bps_10bps_cost": round(sub.net_hi.mean(), 1),
                "median_net_bps": round(sub.net.median(), 1), "win_rate": round((sub.net > 0).mean(), 3),
                "t_vs_spy": round(tstat(vs_spy), 2), "t_vs_random": round(tstat(vs_rand), 2),
                "half1_vs_spy_bps": round((first.net - first.spy).mean(), 1), "half2_vs_spy_bps": round((second.net - second.spy).mean(), 1)}

    res = {"pooled_gating": summary(ok[ok.cls.isin(GATING)])}
    for c in ("exit", "migration-down", "migration-up"):
        res[c] = summary(ok[ok.cls == c])
    ok["year"] = ok.d0.str[:4]
    res["per_year_pooled"] = {y: summary(s) for y, s in ok[ok.cls.isin(GATING)].groupby("year")}
    p = res["pooled_gating"]
    p["PASS"] = bool(p["mean_net_bps"] > 0 and p["t_vs_spy"] >= BAR and p["t_vs_random"] >= BAR
                     and p["half1_vs_spy_bps"] > 0 and p["half2_vs_spy_bps"] > 0)
    (HERE / "results.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
