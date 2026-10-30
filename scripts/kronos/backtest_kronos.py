"""
Kronos zero-shot intraday forecast test (run with .kronos_venv).

For each symbol, sample DAYS_PER_YEAR random trading days per year (fixed
seed) and forecast at 10:30 and 13:30 ET from the last LOOKBACK 5-min bars
(SIP, regular session, built from the 1-min cache of backtest_scalp.py).
Kronos-small predicts the next PRED_LEN bars (mean of SAMPLES paths). We
record predicted and realized close-to-close log returns at 5, 30 and 60
minutes. Rows are appended to scripts/.cache/kronos_preds.csv so a crash
resumes where it stopped. Analysis: scripts/kronos/analyze_kronos.py.

Caveat: Kronos was pretrained on data through ~2024-25, so 2016-2024 may
be in its training set. 2025-2026 is the honest out-of-sample period.
"""
import os
import pickle
import sys
import time

sys.path.insert(0, r"C:\Users\Silvine Raza\projects\vendor\Kronos")

import numpy as np
import pandas as pd
import torch
from model import Kronos, KronosPredictor, KronosTokenizer

HERE = os.path.dirname(__file__)
CACHE = os.path.join(HERE, "..", ".cache")
OUT = os.path.join(CACHE, "kronos_preds.csv")
SYMBOLS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "TSLA",
           "META", "AMD", "GOOGL", "JPM"]
YEARS = range(2025, 2027)   # out-of-sample only: 2016-24 may be in Kronos pretraining
DAYS_PER_YEAR = 24
ORIGINS = ("10:25", "13:25")     # 5-min bar labels; their close is 10:30 / 13:30
LOOKBACK = 400
PRED_LEN = 12
HORIZONS = (1, 6, 12)
SAMPLES = 3
BATCH = 16
SEED = 7


def bars5(sym: str) -> pd.DataFrame:
    frames = []
    for y in YEARS:
        path = os.path.join(CACHE, f"m1_{sym}_{y}.pkl")
        if not os.path.exists(path):
            continue
        with open(path, "rb") as f:
            frames.append(pickle.load(f))
    df = pd.concat(frames).astype("float64")
    b = df.resample("5min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}).dropna()
    b = b.between_time("09:30", "15:55")
    b["amount"] = b["volume"] * b["close"]
    return b


def jobs_for(sym: str, b: pd.DataFrame, rng: np.random.Generator) -> list:
    labels = b.index.strftime("%H:%M")
    dates = pd.Series(b.index.date)
    out = []
    for y in YEARS:
        days = sorted(set(d for d in dates if d.year == y))
        if len(days) < 30:
            continue
        pick = rng.choice(len(days) - 5, size=min(DAYS_PER_YEAR, len(days) - 5), replace=False) + 5
        for k in sorted(pick):
            day = days[k]
            for o in ORIGINS:
                hits = np.flatnonzero((dates.to_numpy() == day) & (labels == o))
                if len(hits) != 1:
                    continue
                i = int(hits[0])
                if i < LOOKBACK or i + PRED_LEN >= len(b) or b.index[i + PRED_LEN].date() != day:
                    continue
                out.append((sym, str(day), o, i))
    return out


def main():
    torch.set_num_threads(6)
    done = set()
    if os.path.exists(OUT):
        prev = pd.read_csv(OUT)
        done = set(zip(prev["sym"], prev["date"], prev["origin"]))
    tok = KronosTokenizer.from_pretrained("NeoQuasar/Kronos-Tokenizer-base")
    model = Kronos.from_pretrained("NeoQuasar/Kronos-small")
    pred = KronosPredictor(model, tok, device="cpu", max_context=512)
    rng = np.random.default_rng(SEED)

    header = not os.path.exists(OUT)
    for sym in SYMBOLS:
        b = bars5(sym)
        jobs = [j for j in jobs_for(sym, b, rng) if (j[0], j[1], j[2]) not in done]
        print(f"{sym}: {len(jobs)} forecasts to run", flush=True)
        idx = b.index.tz_localize(None)
        cols = ["open", "high", "low", "close", "volume", "amount"]
        for s in range(0, len(jobs), BATCH):
            chunk = jobs[s:s + BATCH]
            xs, xts, yts = [], [], []
            for _, _, _, i in chunk:
                xs.append(b.iloc[i - LOOKBACK + 1:i + 1][cols].reset_index(drop=True))
                xts.append(pd.Series(idx[i - LOOKBACK + 1:i + 1]))
                yts.append(pd.Series(idx[i + 1:i + 1 + PRED_LEN]))
            t0 = time.time()
            outs = pred.predict_batch(xs, xts, yts, pred_len=PRED_LEN, T=1.0, top_p=0.9,
                                      sample_count=SAMPLES, verbose=False)
            rows = []
            closes = b["close"].to_numpy()
            for (sym_, day, o, i), f in zip(chunk, outs):
                c0 = closes[i]
                fc = f["close"].to_numpy()
                row = {"sym": sym_, "date": day, "origin": o}
                for h in HORIZONS:
                    row[f"pred_{h}"] = np.log(fc[h - 1] / c0) * 1e4
                    row[f"real_{h}"] = np.log(closes[i + h] / c0) * 1e4
                rows.append(row)
            pd.DataFrame(rows).to_csv(OUT, mode="a", header=header, index=False)
            header = False
            print(f"  {sym} {s + len(chunk)}/{len(jobs)}  {time.time() - t0:.1f}s/batch", flush=True)


if __name__ == "__main__":
    main()
