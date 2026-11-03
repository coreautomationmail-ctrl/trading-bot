"""
Kronos as a filter on spike_mom trades (run with .kronos_venv).

Every spike_mom trade (8x / k5 / hold 60 / 1-bar delay, SIP, the 62 names of
backtest_scalp, 2025-2026 only: Kronos may have seen 2016-24 in pretraining)
gets a Kronos-small forecast made at the signal bar's close from the last
LOOKBACK 5-min bars (the last bar is partial up to the signal minute, which
is all that is known then). pred_60 = forecast log return over the next 60
minutes. A filter is worth wiring into the pod only if the trades Kronos
AGREES with beat the ones it disagrees with by a margin that survives a
t-test, in both the afternoon (live v2 window) and the rest of the day.

Rows are appended to scripts/.cache/kronos_spike.csv (resumable).
Usage: python scripts/kronos/kronos_spike_filter.py [--count] [--analyze]
"""
import os
import sys
import time

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, ".."))
sys.path.insert(0, r"C:\Users\Silvine Raza\projects\vendor\Kronos")

import types

import numpy as np
import pandas as pd

# .kronos_venv has no Alpaca client; every bar used here is already cached.
sys.modules.setdefault("alpaca_client", types.SimpleNamespace(get_client=None))
sys.modules.setdefault("strategy", types.SimpleNamespace(get_atr=None))
import backtest_scalp as bt

OUT = os.path.join(HERE, "..", ".cache", "kronos_spike.csv")
YEARS = (2025, 2026)
LOOKBACK, PRED_LEN, SAMPLES, BATCH = 400, 12, 3, 16
COLS = ["open", "high", "low", "close", "volume", "amount"]


def spike_trades(sym: str):
    """(signal timestamp, side, bps) for every spike_mom trade, same skip rules as bt.trades."""
    df = pd.concat([bt.load_year(sym, y) for y in YEARS])
    if df.empty:
        return df, []
    f = bt.features(df)
    hm = f.index.hour * 60 + f.index.minute
    m = ((hm >= 575) & (hm <= 930) & (f["ret_std"] > 0) & (f["volume"] >= 8 * f["vol_mean"])
         & (f["ret"].abs() >= 5 * f["ret_std"])).to_numpy()
    side = np.sign(f["ret"].to_numpy())
    tr = bt.trades(f, m, side, 60, 1)
    out, busy, k = [], -1, 0
    for i in np.flatnonzero(m):
        j = i + 61
        if i <= busy or j >= len(f) or f["date"].iat[j] != f["date"].iat[i] or hm[j] > 959:
            continue
        out.append((f.index[i], int(side[i]), tr[k][1]))
        busy, k = j, k + 1
    return df, out


def context(df1: pd.DataFrame, ts) -> pd.DataFrame:
    """5-min bars up to and including the signal minute (last bar partial)."""
    hist = df1.loc[:ts].iloc[-LOOKBACK * 5 - 2000:].astype("float64")
    b = hist.resample("5min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    b = b.between_time("09:30", "15:55")
    b["amount"] = b["volume"] * b["close"]
    return b.iloc[-LOOKBACK:]


def run(count_only: bool):
    jobs = []
    for sym in bt.SYMBOLS + bt.WIDE:
        df, tr = spike_trades(sym)
        jobs += [(sym, ts, s, bps, df) for ts, s, bps in tr]
    print(f"{len(jobs)} spike trades 2025-26 across {len(bt.SYMBOLS + bt.WIDE)} names", flush=True)
    if count_only:
        return
    import torch
    from model import Kronos, KronosPredictor, KronosTokenizer
    torch.set_num_threads(6)
    done = set()
    if os.path.exists(OUT):
        prev = pd.read_csv(OUT)
        done = set(zip(prev["sym"], prev["ts"]))
    jobs = [j for j in jobs if (j[0], str(j[1])) not in done]
    jobs = [j for j in jobs if len(context(j[4], j[1])) == LOOKBACK]   # first days of 2025 lack history
    print(f"{len(jobs)} to forecast", flush=True)
    tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    pred = KronosPredictor(Kronos.from_pretrained("NeoQuasar/Kronos-small"), tok, device="cpu", max_context=512)
    header = not os.path.exists(OUT)
    for s in range(0, len(jobs), BATCH):
        chunk, xs, xts, yts, c0 = jobs[s:s + BATCH], [], [], [], []
        for sym, ts, _, _, df in chunk:
            b = context(df, ts)
            idx = b.index.tz_localize(None)
            xs.append(b[COLS].reset_index(drop=True))
            xts.append(pd.Series(idx))
            yts.append(pd.Series(pd.date_range(idx[-1] + pd.Timedelta(minutes=5), periods=PRED_LEN, freq="5min")))
            c0.append(b["close"].iat[-1])
        t0 = time.time()
        outs = pred.predict_batch(xs, xts, yts, pred_len=PRED_LEN, T=1.0, top_p=0.9, sample_count=SAMPLES, verbose=False)
        rows = [{"sym": sym, "ts": str(ts), "side": side, "bps": bps, "pred_60": np.log(o["close"].iat[-1] / c) * 1e4}
                for (sym, ts, side, bps, _), o, c in zip(chunk, outs, c0)]
        pd.DataFrame(rows).to_csv(OUT, mode="a", header=header, index=False)
        header = False
        print(f"  {s + len(chunk)}/{len(jobs)}  {time.time() - t0:.0f}s/batch", flush=True)


def analyze():
    t = pd.read_csv(OUT)
    t["ts"] = pd.to_datetime(t["ts"], utc=True).dt.tz_convert("America/New_York")
    t["late"] = t["ts"].dt.hour * 60 + t["ts"].dt.minute >= 780
    t["agree"] = np.sign(t["pred_60"]) == t["side"]
    t["edge"] = t["side"] * t["pred_60"]   # Kronos-predicted bps in the trade's direction

    def line(g):
        n, mu, sd = len(g), g.mean(), g.std()
        return f"n={n:4d} {mu:+6.2f} bps t={mu / sd * np.sqrt(n) if n > 1 and sd else 0:+5.2f}"
    print(f"all trades       {line(t['bps'])}")
    for name, g in (("all day", t), ("afternoon", t[t["late"]]), ("before 13:00", t[~t["late"]])):
        a, d = g[g["agree"]]["bps"], g[~g["agree"]]["bps"]
        diff = a.mean() - d.mean()
        se = np.sqrt(a.var() / len(a) + d.var() / len(d)) if len(a) > 1 and len(d) > 1 else np.nan
        print(f"{name:13s} agree {line(a)} | disagree {line(d)} | diff {diff:+.2f} t={diff / se:+.2f}")
    print("\nby Kronos-predicted edge in the trade's direction (quintiles):")
    t["q"] = pd.qcut(t["edge"], 5, labels=False, duplicates="drop")
    for q, g in t.groupby("q"):
        print(f"  q{q} edge {g['edge'].min():+6.1f}..{g['edge'].max():+6.1f}  {line(g['bps'])}")
    print(f"\nIC(edge, bps) = {t['edge'].rank().corr(t['bps'].rank()):+.3f}")


if __name__ == "__main__":
    if "--analyze" in sys.argv:
        analyze()
    else:
        run("--count" in sys.argv)
