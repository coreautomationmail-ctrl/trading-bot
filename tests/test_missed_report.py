import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "pods", "spike"))

import missed_report as mr


def _bars(n=120, spike_at=None):
    t0, out, px = datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc), [], 100.0
    for i in range(n):
        o, c, v = px, px * (1 + (0.0001 if i % 2 else -0.0001)), 1000
        if i == spike_at:
            c, v = px * 1.02, 50000
        out.append({"t": (t0 + timedelta(minutes=i)).isoformat().replace("+00:00", "Z"), "o": o, "c": c, "v": v})
        px = c
    return out


def test_movers_rank_by_abs_move_and_skip_cheap_names():
    def d(prev, o, c, v=1_000_000):
        return [{"t": "2026-11-04T05:00:00Z", "c": prev}, {"t": "2026-11-05T05:00:00Z", "o": o, "c": c, "v": v}]
    top = mr.movers({"A": d(100, 100, 103), "B": d(100, 95, 90), "PENNY": d(2, 2, 3), "THIN": d(100, 100, 150, 1000)}, "2026-11-05")
    assert [m["symbol"] for m in top] == ["B", "A"]
    assert round(top[0]["move_pct"]) == -10 and round(top[0]["gap_pct"]) == -5


def test_first_signal_uses_the_pods_rule_and_backtest_outcome():
    sig = mr.first_signal(_bars(spike_at=40))
    assert sig["et"] == "10:10" and sig["side"] == 1 and sig["bps"] is not None
    assert mr.first_signal(_bars()) is None
    assert mr.first_signal(_bars(n=50, spike_at=40))["bps"] is None  # session too short for the 60-min hold


def test_classify():
    in_window, early = {"minute": 13 * 60 + 30}, {"minute": 10 * 60}
    m = {"move_pct": 10.0, "gap_pct": 8.0}
    assert mr.classify(m, in_window, True, True) == "traded"
    assert mr.classify(m, in_window, False, True) == "pod_missed"
    assert mr.classify(m, early, False, True) == "outside_window"
    assert mr.classify(m, in_window, False, False) == "not_in_pod"
    assert mr.classify(m, early, False, False) == "not_in_pod_outside_window"
    assert mr.classify(m, None, False, False) == "gap"
    assert mr.classify({"move_pct": 10.0, "gap_pct": 1.0}, None, False, True) == "drift"


def test_rollup_counts_categories_and_keeps_no_news_text():
    log = [{"date": "2026-11-05", "symbol": "A", "move_pct": "-10.00", "category": "gap", "signal_bps": ""},
           {"date": "2026-11-05", "symbol": "B", "move_pct": "4.00", "category": "not_in_pod", "signal_bps": "20.0"}]
    text = mr.rollup(log, [{"date": "2026-11-05", "bps": "10"}])
    assert "HINDSIGHT" in text and "| moved at the open, no signal | 1 | 10.0% | 0 | - |" in text
    assert "| signal in the window, name not in the pod | 1 | 4.0% | 1 | +20.0 |" in text
