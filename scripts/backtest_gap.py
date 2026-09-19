# scripts/backtest_gap.py
"""
Gap-and-go continuation backtest on the Stocks-in-Play universe, under this
bot's safety envelope (no margin, equity/TOP_N per name, 2% risk cap,
flatten 15:45 ET). Reuses backtest_sip's cached universe/daily/5-min data,
so run backtest_sip.py once first to populate scripts/.cache/.

Candidates each session: pool names passing the SIP daily filters, not
ETF-like, whose 9:30 open gaps >= MIN_GAP vs the prior close (long; mirror
for shorts with --short), ranked by first-5-min RVOL, top TOP_N.

Entry (both wait for the market to prove the gap holds instead of paying
the 9:35 spread):
  --entry=pullback  (default) after the 9:30 bar, wait for the first
                    pullback (a bar's low under the prior bar's low), then
                    buy a stop at the pre-pullback high when price resumes.
                    Stop = pullback low; skipped if the pullback broke the
                    day's open (gap failed) or no resumption by ENTRY_CUTOFF.
  --entry=or15      buy a stop at the 15-min opening-range high after 9:45;
                    stop = OR low.
Exit: stop, else 15:45. No target.

Usage: python scripts/backtest_gap.py [--entry=pullback|or15] [--gap=PCT]
         [--slippage=BPS] [--short | --short-only] [--top=N] [--cutoff=HH:MM]
"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pandas as pd

import backtest_sip as sip

MIN_GAP = 0.03
ENTRY = "pullback"
ENTRY_CUTOFF = (11, 0)
MIN_STOP_ATR = 0.05          # floor on stop distance as a fraction of ATR14 (pullback mode)
SLIPPAGE_BPS = 0.0
ALLOW_SHORT = False
LONG = True
TOP_N = 20


def _no_cache(what):
    raise RuntimeError(f"{what} not cached -- run backtest_sip.py first")


def _enter(side, raw, stop, ts, alloc, equity, slip):
    entry = raw * (1 + side * slip)
    stop_dist = abs(entry - stop)
    if stop_dist <= 0:
        return None
    qty = min(int(alloc / entry), int(equity * sip.MAX_RISK_PCT / stop_dist))
    if qty < 1:
        return None
    return {"entry": entry, "qty": qty, "stop": stop, "time": ts}


def simulate_day(bars: pd.DataFrame, side: int, atr: float, alloc: float, equity: float):
    slip = SLIPPAGE_BPS / 10_000.0
    session = bars.between_time("09:30", "15:45")
    if session.empty or (session.index[0].hour, session.index[0].minute) != (9, 30):
        return None
    day_open = float(session.iloc[0]["open"])
    pos = None
    trigger = stop = None

    if ENTRY == "or15":
        orb = session.between_time("09:30", "09:40")      # 9:30, 9:35, 9:40 bars
        if len(orb) < 3:
            return None
        trigger = float(orb["high"].max()) if side == 1 else float(orb["low"].min())
        stop = float(orb["low"].min()) if side == 1 else float(orb["high"].max())
        rest = session.iloc[3:]
    else:
        first = session.iloc[0]
        # "high"/"low" below are in trade direction: for shorts they're mirrored
        pre_high = float(first["high"]) if side == 1 else float(first["low"])
        prev_lo = float(first["low"]) if side == 1 else float(first["high"])
        in_pb, pb_low = False, None
        rest = session.iloc[1:]

    for ts, bar in rest.iterrows():
        hi, lo, op, cl = (float(bar[k]) for k in ("high", "low", "open", "close"))
        ext, adv = (hi, lo) if side == 1 else (lo, hi)     # with / against the trade
        if pos is None:
            if (ts.hour, ts.minute) >= ENTRY_CUTOFF:
                return None
            if ENTRY == "pullback":
                if not in_pb:
                    if (adv < prev_lo) if side == 1 else (adv > prev_lo):
                        in_pb, pb_low = True, adv
                    else:
                        pre_high = max(pre_high, ext) if side == 1 else min(pre_high, ext)
                    prev_lo = adv
                    continue
                pb_low = min(pb_low, adv) if side == 1 else max(pb_low, adv)
                if (pb_low < day_open) if side == 1 else (pb_low > day_open):
                    return None                              # gap failed
                resumed = ext > pre_high if side == 1 else ext < pre_high
                if not resumed:
                    continue
                trigger, stop = pre_high, pb_low
                if abs(trigger - stop) < MIN_STOP_ATR * atr:
                    stop = trigger - side * MIN_STOP_ATR * atr
            else:
                hit = hi >= trigger if side == 1 else lo <= trigger
                if not hit:
                    continue
            raw = max(trigger, op) if side == 1 else min(trigger, op)   # gap-through fills at the open
            pos = _enter(side, raw, stop, ts, alloc, equity, slip)
            if pos is None:
                return None
        stopped = lo <= pos["stop"] if side == 1 else hi >= pos["stop"]
        if stopped:
            raw = min(pos["stop"], op) if side == 1 else max(pos["stop"], op)
            return sip._close(pos, raw * (1 - side * slip), side, ts, "stop")
        if (ts.hour, ts.minute) >= sip.FLATTEN_AT:
            return sip._close(pos, cl * (1 - side * slip), side, ts, "eod")
    if pos is not None:
        return sip._close(pos, float(session["close"].iloc[-1]) * (1 - side * slip), side, session.index[-1], "eod")
    return None


def run_window(label: str, start: str, end: str) -> dict:
    print(f"\n{'=' * 70}\n{label}  ({start} to {end})\n{'=' * 70}", flush=True)
    tag = f"{start}_{end}"
    daily = sip.cached(f"daily_{tag}", lambda: _no_cache("daily"))
    feats = sip.daily_features(daily)
    sessions = sorted(d for d in set(feats.index.get_level_values("date")) if str(d) >= start)
    f0 = feats.xs(sessions[0], level="date")
    elig0 = f0[(f0.prev_close > sip.MIN_PRICE) & (f0.adv14 >= sip.MIN_ADV) & (f0.atr14 > sip.MIN_ATR)]
    pool = list((elig0.adv14 * elig0.prev_close).sort_values(ascending=False).head(sip.POOL_SIZE).index)
    intraday = sip.cached(f"intraday_{tag}_pool{sip.POOL_SIZE}", lambda: _no_cache("intraday"))
    rvol = sip.rvol5_table(intraday)
    rvol_days = set(rvol.index.get_level_values("date"))
    etfs = sip.cached("etf_like", sip.list_etf_like)
    o = intraday.between_time("09:30", "09:30")
    opens = pd.Series(o["open"].values,
                      index=pd.MultiIndex.from_arrays([o["symbol"].values, o.index.date], names=["symbol", "date"]))
    intraday = intraday.set_index("symbol", append=True).swaplevel().sort_index()
    have = set(intraday.index.get_level_values(0))

    equity = sip.STARTING_CASH
    trades, curve = [], [equity]
    n_cand = 0
    for day in sessions:
        try:
            fd = feats.xs(day, level="date")
            op = opens.xs(day, level="date")
        except KeyError:
            curve.append(equity)
            continue
        fd = fd[fd.index.isin(pool)]
        elig = fd[(fd.prev_close > sip.MIN_PRICE) & (fd.adv14 >= sip.MIN_ADV) & (fd.atr14 > sip.MIN_ATR)]
        elig = elig[~elig.index.isin(etfs)]
        if day not in rvol_days:
            curve.append(equity)
            continue
        gap = (op.reindex(elig.index) / elig.prev_close - 1.0).dropna()
        longs = set(gap[gap >= MIN_GAP].index) if LONG else set()
        shorts = set(gap[gap <= -MIN_GAP].index) if ALLOW_SHORT else set()
        rv = rvol.xs(day, level="date")
        rv = rv[rv.index.isin(longs | shorts) & (rv >= sip.MIN_RVOL)].sort_values(ascending=False).head(TOP_N)
        n_cand += len(rv)
        day_equity = equity
        alloc = day_equity / TOP_N
        for sym in rv.index:
            if sym not in have:
                continue
            g = intraday.loc[sym]
            bars = g[g.index.date == day]
            side = 1 if sym in longs else -1
            t = simulate_day(bars, side, float(elig.loc[sym, "atr14"]), alloc, day_equity)
            if t:
                t.update(symbol=sym, date=day, rvol5=float(rv[sym]), gap=float(gap[sym]))
                trades.append(t)
                equity += t["pnl"]
        curve.append(equity)
    print(f"  candidates={n_cand} ({n_cand / max(len(sessions), 1):.1f}/day)")
    return sip.summarize(label, trades, curve, len(sessions))


if __name__ == "__main__":
    for a in sys.argv[1:]:
        if a.startswith("--slippage="):
            SLIPPAGE_BPS = float(a.split("=")[1])
        elif a.startswith("--gap="):
            MIN_GAP = float(a.split("=")[1]) / 100.0
        elif a.startswith("--entry="):
            ENTRY = a.split("=")[1]
            assert ENTRY in ("pullback", "or15")
        elif a.startswith("--top="):
            TOP_N = int(a.split("=")[1])
        elif a.startswith("--cutoff="):
            h, m = a.split("=")[1].split(":")
            ENTRY_CUTOFF = (int(h), int(m))
        elif a == "--short":
            ALLOW_SHORT = True
        elif a == "--short-only":
            ALLOW_SHORT, LONG = True, False
        else:
            sys.exit(f"unknown arg {a}")
    print(f"entry={ENTRY} | gap>={MIN_GAP:.1%} | slippage={SLIPPAGE_BPS:g} bps/side | "
          f"long={LONG} short={ALLOW_SHORT} | top={TOP_N} | cutoff={ENTRY_CUTOFF}")
    results = [run_window(*w) for w in sip.WINDOWS]
    print(f"\n{'=' * 70}\nTOTAL\n{'=' * 70}")
    n = sum(r["trades"] for r in results)
    wr = sum(r["mean_R"] * r["trades"] for r in results) / n if n else 0.0
    print(f"  trades={n} | PnL=${sum(r['pnl'] for r in results):,.2f} | mean R={wr:+.3f} | "
          f"positive windows={sum(r['pnl'] > 0 for r in results)}/{len(results)}")
