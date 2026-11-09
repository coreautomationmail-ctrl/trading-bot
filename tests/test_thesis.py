"""Thesis pod scoring (pods/bench/thesis.py): validation, trade maths, baselines, summary."""
import json
import os
import sys
from datetime import datetime

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "pods", "spike"))
sys.path.insert(0, os.path.join(ROOT, "pods", "bench"))

import thesis  # noqa: E402
from spike_pod import ET  # noqa: E402

DAYS = ["2026-11-10", "2026-11-11", "2026-11-12"]


def bar(o, h, l, c):  # noqa: E741
    return {"o": o, "h": h, "l": l, "c": c}


def flat_bars(symbols, move=0.0):
    return {s: {d: bar(100, 101, 99, 100 * (1 + move)) for d in DAYS} for s in symbols}


def test_validation_keeps_the_grammar_tight():
    good = {"id": "2026-11-10-1", "symbol": thesis.UNIVERSE[0], "direction": "long", "hold_days": 1, "stop_pct": 3}
    assert thesis.valid(good, "2026-11-10") == ""
    assert "universe" in thesis.valid({**good, "symbol": "ZZZZQ"}, "2026-11-10")
    assert "hold_days" in thesis.valid({**good, "hold_days": 9}, "2026-11-10")
    assert "stop_pct" in thesis.valid({**good, "stop_pct": 0.1}, "2026-11-10")
    assert "id" in thesis.valid({**good, "id": "x"}, "2026-11-10")


def test_trade_maths_costs_stops_and_gaps():
    r = lambda t: (round(t[0], 6), t[1])  # noqa: E731
    up = [bar(100, 103, 99.5, 102)]
    assert r(thesis.trade_bps(up, "long", None)) == (200 - 4, False)
    assert r(thesis.trade_bps(up, "short", None)) == (-200 - 4, False)
    assert r(thesis.trade_bps([bar(100, 101, 96, 100)], "long", 3.0)) == (-300 - 4, True)   # stop inside the day
    gap = [bar(100, 101, 99, 100), bar(95, 96, 94, 95.5)]                                     # gaps through the stop
    assert r(thesis.trade_bps(gap, "long", 3.0)) == (-500 - 4, True)


def test_score_day_compares_with_spy_and_random_and_waits_for_the_exit():
    sym = thesis.UNIVERSE[0]
    bars = flat_bars(thesis.UNIVERSE + ["SPY"])
    bars[sym] = {d: bar(100, 103, 99.5, 102) for d in DAYS}
    sealed = {"theses": [{"id": "2026-11-10-1", "symbol": sym, "direction": "long", "hold_days": 1,
                          "confidence": 0.8, "why": "test"}]}
    [row] = thesis.score_day(sealed, "2026-11-10", DAYS, bars)
    assert row["exit_date"] == "2026-11-11" and row["net_bps"] == 196 and row["spy_bps"] == -4
    assert row["random_bps"] == -4 and row["vs_random"] == 200
    assert thesis.score_day(sealed, "2026-11-10", DAYS[:1], bars) == []   # exit day hasn't closed yet


def test_summary_needs_300_and_both_baselines():
    rows = [{"date": f"2026-11-{10 + i % 5}", "net_bps": 10 + i % 3, "vs_spy": 5 + i % 2, "vs_random": 4 + i % 3,
             "stopped": 0, "confidence": 0.8} for i in range(20)]
    s = thesis.summarize(rows)
    assert s["n"] == 20 and s["verdict"] == "running" and s["by_confidence"]["high"]["n"] == 20
    many = [{"date": f"d{i // 5}", "net_bps": 10 + i % 7, "vs_spy": 5 + i % 6, "vs_random": 4 + i % 7,
             "stopped": 0, "confidence": 0.8} for i in range(300)]
    assert thesis.summarize(many)["verdict"].startswith("PASS")
    losing = [{**r, "vs_random": -r["vs_random"]} for r in many]
    assert thesis.summarize(losing)["verdict"].startswith("FAIL")


def test_seal_only_before_the_open(tmp_path, monkeypatch):
    monkeypatch.setattr(thesis, "SEALED_DIR", str(tmp_path))
    assert thesis.seal(datetime(2026, 11, 10, 9, 31, tzinfo=ET)) == "outside the sealing window"
    assert thesis.seal(datetime(2026, 11, 8, 9, 0, tzinfo=ET)) == "outside the sealing window"   # Sunday

    class R:
        status_code, text = 200, json.dumps({"theses": [
            {"id": "2026-11-10-1", "symbol": thesis.UNIVERSE[0], "direction": "long", "hold_days": 0},
            {"id": "2026-11-10-2", "symbol": "ZZZZQ", "direction": "long", "hold_days": 0}]})

        def raise_for_status(self):
            pass

    monkeypatch.setattr(thesis.requests, "get", lambda *a, **k: R())
    assert thesis.seal(datetime(2026, 11, 10, 9, 20, tzinfo=ET)) == "sealed 1 of 2 theses for 2026-11-10"
    saved = json.load(open(tmp_path / "2026-11-10.json"))
    assert len(saved["theses"]) == 1 and "universe" in saved["rejected"][0]["why"]
