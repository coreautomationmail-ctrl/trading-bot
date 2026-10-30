"""The live spike detector must flag exactly the bars the backtest flags.

Runs against the cached IEX 1-min bars from scripts/backtest_scalp.py
(skips if the cache isn't on this machine).
"""
import os
import sys

import numpy as np
import pytest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.join(ROOT, "pods", "spike"))

import backtest_scalp as bt  # noqa: E402
import spike_pod as pod  # noqa: E402

CACHE = os.path.join(ROOT, "scripts", ".cache")


@pytest.mark.parametrize("sym", ["IWM", "META", "AMD"])
def test_live_detector_matches_backtest(sym):
    path = os.path.join(CACHE, f"m1iex_{sym}_2026.pkl")
    if not os.path.exists(path):
        pytest.skip("IEX cache not present")
    bt.FEED = "iex"
    df = bt.load_year(sym, 2026).astype("float64")
    f = bt.features(df)
    hm = f.index.hour * 60 + f.index.minute
    w = (hm >= pod.FIRST_ENTRY) & (hm <= pod.LAST_ENTRY) & (f["ret_std"] > 0)
    expect = (w & (f["volume"] >= pod.VOL_X * f["vol_mean"]) &
              (f["ret"].abs() >= pod.K * f["ret_std"])).to_numpy()
    expect_side = np.sign(f["ret"].to_numpy())

    st = pod.SymState()
    got = np.zeros(len(df), dtype=int)
    for i, (ts, row) in enumerate(df.iterrows()):
        got[i] = st.update(ts.to_pydatetime(), row["open"], row["close"], row["volume"])

    assert expect.sum() > 0, "no spikes in sample; pick another symbol"
    assert np.array_equal(got != 0, expect)
    assert np.array_equal(got[expect], expect_side[expect].astype(int))


def test_no_signal_before_warmup_or_outside_window():
    from datetime import datetime
    st = pod.SymState()
    t0 = datetime(2026, 10, 1, 9, 30, tzinfo=pod.ET)
    for m in range(pod.MIN_BARS):
        assert st.update(t0.replace(minute=30 + m), 100, 100.01, 1000) == 0
    late = datetime(2026, 10, 1, 15, 40, tzinfo=pod.ET)
    st2 = pod.SymState()
    for m in range(25):
        st2.update(late.replace(hour=15, minute=10 + m), 100, 100.01 + 0.001 * (m % 3), 1000)
    assert st2.update(late, 100, 103, 10**7) == 0      # 15:40 is after LAST_ENTRY


def test_order_flow_enter_exit_and_log(tmp_path, monkeypatch):
    """enter -> timed exit order -> fill -> CSV row, against a fake Alpaca."""
    from datetime import datetime, timedelta
    monkeypatch.setattr(pod, "STATE_DIR", str(tmp_path))
    monkeypatch.setattr(pod, "TRADES_CSV", str(tmp_path / "t.csv"))
    monkeypatch.setattr(pod, "send_telegram", lambda m: None)
    orders, fills = {}, {}

    def fake_api(method, path, **kw):
        if path == "/v2/positions":
            return [{"symbol": "AAPL"}]                       # someone else's position
        if path.startswith("/v2/assets/"):
            return {"shortable": True, "easy_to_borrow": True}
        if method == "POST" and path == "/v2/orders":
            oid = f"o{len(orders)}"
            orders[oid] = kw["json"]
            return {"id": oid}
        if path.startswith("/v2/orders/"):
            oid = path.rsplit("/", 1)[1]
            return {"status": "filled", "filled_avg_price": str(fills[oid])}
        raise AssertionError(path)

    monkeypatch.setattr(pod, "api", fake_api)
    state = {"open": [], "day": "2026-10-30", "realized_today": 0.0}
    t0 = datetime(2026, 10, 30, 11, 0, 5, tzinfo=pod.ET)
    pod.enter(state, "AAPL", 1, {"c": 200.0, "t": "2026-10-30T14:59:00Z"}, t0)   # blocked: held
    assert state["open"] == []
    pod.enter(state, "META", -1, {"c": 500.0, "t": "2026-10-30T14:59:00Z"}, t0)
    assert len(state["open"]) == 1 and orders["o0"]["side"] == "sell" and orders["o0"]["qty"] == "10"
    fills["o0"] = 500.0
    pod.manage_exits(state, t0 + timedelta(minutes=30))      # not due yet
    assert "exit_order" not in state["open"][0]
    pod.manage_exits(state, t0 + timedelta(minutes=pod.HOLD_MIN))
    assert orders["o1"]["side"] == "buy"
    fills["o1"] = 495.0
    pod.manage_exits(state, t0 + timedelta(minutes=pod.HOLD_MIN + 1))
    assert state["open"] == [] and state["realized_today"] == 50.0
    rows = (tmp_path / "t.csv").read_text().splitlines()
    assert len(rows) == 2 and ",short,10," in rows[1]
