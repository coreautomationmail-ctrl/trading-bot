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
    monkeypatch.setattr(pod, "CONTROL_FILE", str(tmp_path / "control.json"))
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


def test_scorecard_from_trade_log(tmp_path):
    import spike_scorecard as sc
    p = tmp_path / "t.csv"
    p.write_text("symbol,side,qty,entry_time,entry_px,exit_time,exit_px,pnl,bps,signal_bar\n"
                 "META,short,10,a,500,b,495,50,100.5,x\n"
                 "AMD,long,20,a,100,b,99,-20,-100.5,x\n")
    s = sc.stats(sc.load(str(p)))
    assert s["n"] == 2 and s["long"] == 1 and s["gross"] == 30
    assert abs(s["net"] - (30 - 0.04 * 30)) < 1e-9
    assert "IN PROGRESS" in sc.build(str(p), "Oct 31 2026")
    assert sc.build(str(tmp_path / "missing.csv"), "x").startswith("STRATTON")


def _rows(bps_list, qty=10, px=100.0):
    return [{"pnl": str(b / 1e4 * px * qty), "bps": str(b), "qty": str(qty), "side": "long"} for b in bps_list]


def test_control_pauses_when_live_runs_under_the_backtest():
    from datetime import date
    import spike_scorecard as sc
    fresh = {"paused": False, "size_mult": 1.0}
    few = _rows([-30, 10] * 10)                                   # 20 trades: too few to judge
    assert sc.decide(few, fresh, date(2026, 11, 7))[0]["paused"] is False
    bad = _rows([-40, 0, 10] * 20)                                # 60 trades averaging -10 bps
    control, note = sc.decide(bad, fresh, date(2026, 11, 7))
    assert control["paused"] and "PAUSING" in note
    again, note = sc.decide(_rows([50] * 200), control, date(2026, 12, 5))
    assert again["paused"] and "owner resumes" in note        # only the owner unpauses


def test_control_grows_size_on_proven_edge_once_a_month():
    from datetime import date
    import spike_scorecard as sc
    good = _rows([60, -20, 30, 10] * 30)                          # 120 trades, +20 bps
    c1, note = sc.decide(good, {"paused": False, "size_mult": 1.0}, date(2026, 11, 7))
    assert c1["size_mult"] == 1.5 and "GROWING" in note
    c2, _ = sc.decide(good, c1, date(2026, 11, 14))
    assert c2["size_mult"] == 1.5                                 # not again within 28 days
    c3, _ = sc.decide(good, c2, date(2026, 12, 5))
    assert c3["size_mult"] == 2.25
    c4, _ = sc.decide(good, {**c3, "size_mult": 3.0}, date(2027, 2, 1))
    assert c4["size_mult"] == 3.0                                 # capped


def test_paused_pod_does_not_enter(tmp_path, monkeypatch):
    from datetime import datetime
    import spike_scorecard as sc
    monkeypatch.setattr(pod, "CONTROL_FILE", str(tmp_path / "c.json"))
    sc.save_control(pod.CONTROL_FILE, {"paused": True, "size_mult": 1.0})

    def no_api(*a, **k):
        raise AssertionError("a paused pod must not call Alpaca")

    monkeypatch.setattr(pod, "api", no_api)
    state = {"open": [], "day": "2026-11-02", "realized_today": 0.0}
    pod.enter(state, "META", 1, {"c": 500.0, "t": "x"}, datetime(2026, 11, 2, 13, 5, tzinfo=pod.ET))
    assert state["open"] == []


def test_shadow_signal_settles_like_the_backtest(tmp_path, monkeypatch):
    """Entry = open 2 bars after the signal bar, exit = close 61 bars after; logged once."""
    import csv
    from datetime import datetime, timedelta
    monkeypatch.setattr(pod, "SHADOW_CSV", str(tmp_path / "s.csv"))
    st = pod.SymState(pod.SHADOW_FIRST, pod.SHADOW_LAST)
    st.last_std, st.last_vol_mean = 0.001, 1000.0
    t0 = datetime.now(pod.ZoneInfo("UTC")).replace(second=0, microsecond=0) - timedelta(minutes=70)
    key = lambda m: (t0 + timedelta(minutes=m)).strftime("%Y-%m-%dT%H:%M:00Z")
    sig_bar = {"t": key(0), "o": 100.0, "c": 101.0, "v": 9000}
    state = {}
    pod.shadow_signal(state, "GE", 1, st, sig_bar, t0.astimezone(pod.ET))
    recent = {"GE": {key(0): sig_bar, key(2): {"o": 101.2, "c": 101.3}}}
    pod.shadow_settle(state, recent)
    assert state["shadow"][0]["entry_px"] == 101.2 and not (tmp_path / "s.csv").exists()
    recent["GE"][key(62)] = {"o": 102.0, "c": 102.212}             # minute 61 missing: next bar used
    pod.shadow_settle(state, recent)
    assert state["shadow"] == []
    rows = list(csv.DictReader(open(tmp_path / "s.csv")))
    assert len(rows) == 1 and rows[0]["side"] == "1" and abs(float(rows[0]["bps"]) - 99.6) < 0.5
    assert rows[0]["sigma"] == "9.95" and rows[0]["volx"] == "9.0"


def test_scorecard_reports_shadow_and_slippage(tmp_path):
    import spike_scorecard as sc
    s = tmp_path / "s.csv"
    s.write_text("date,et,symbol,signal_bar,side,sigma,volx,entry_px,exit_px,bps\n"
                 "2026-11-03,10:05,GE,x,1,6,9,100,101,99.5\n"
                 "2026-11-03,13:30,GE,x,-1,6,9,100,101,-99.5\n")
    out = sc.shadow_summary(str(s))
    assert "2 signals" in out and "before 13:00 ET: n 1" in out and "13:00+ ET: n 1" in out
    t = tmp_path / "t.csv"
    t.write_text("symbol,side,qty,entry_time,entry_px,exit_time,exit_px,pnl,bps,signal_bar,signal_px,slip_bps\n"
                 "META,short,10,a,500,b,495,50,100.5,x,500.2,4.0\n")
    assert "slippage vs signal price: avg +4.0 bps" in sc.build(str(t), "x")
