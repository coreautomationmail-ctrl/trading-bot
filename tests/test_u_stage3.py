"""U3's spike rule in u_stage3 (stdlib, runs on the VPS) matches backtest_scalp's signals()/trades()."""
import os
import random
import sys

import numpy as np
import pandas as pd

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "scripts", "u_data"))

import backtest_scalp as bs  # noqa: E402
import u_stage3 as u3  # noqa: E402


def _synthetic(days=6, seed=3):
    rng = random.Random(seed)
    rows, day_bars = [], {}
    for k in range(days):
        d = pd.Timestamp("2024-03-04") + pd.Timedelta(days=k)
        px, bars = 20.0, []
        for m in range(9 * 60 + 30, 16 * 60):
            if rng.random() < 0.08:          # gaps: minutes with no trade
                continue
            jump = rng.random() < 0.02
            o = px
            c = o * (1 + (rng.choice([-1, 1]) * 0.03 if jump else rng.gauss(0, 0.001)))
            v = (rng.randint(5000, 9000) if jump else rng.randint(100, 900))
            h, low = max(o, c) * 1.0005, min(o, c) * 0.9995
            bars.append((m, o, h, low, c, v))
            rows.append((d + pd.Timedelta(minutes=m), o, h, low, c, v))
            px = c
        day_bars[d.date().isoformat()] = bars
    df = pd.DataFrame(rows, columns=["t", "open", "high", "low", "close", "volume"]).set_index("t")
    return df, day_bars


def test_u3_matches_backtest_scalp(monkeypatch):
    monkeypatch.setattr(bs, "VOL_X", u3.VOL_X)
    monkeypatch.setattr(bs, "K_SPIKE", u3.K)
    df, day_bars = _synthetic()
    f = bs.features(df)
    mask, side = bs.signals(f)["spike_mom"]
    hm = f.index.hour * 60 + f.index.minute
    mask = mask & (hm <= u3.SIG_TO)                       # U3's window ends at 14:58
    ref = bs.trades(f, mask, side, u3.HOLD, delay=u3.DELAY)
    mine = u3.u3_trades(day_bars, sorted(day_bars))
    assert len(ref) > 5 and len(mine) == len(ref)
    assert np.allclose([r[1] for r in ref], [m[5] for m in mine], atol=0.01)


def test_universe_days_limit_the_signals():
    _, day_bars = _synthetic()
    days = sorted(day_bars)
    some = u3.u3_trades(day_bars, days[:2])
    assert {t[0] for t in some} <= set(days[:2])
