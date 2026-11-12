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
    assert r(thesis.trade_bps([bar(100, 101, 96, 100)], "long", 3.0)) == (-300 - 4 - 3, True)   # stop inside the day, +3 bps stop slippage
    gap = [bar(100, 101, 99, 100), bar(95, 96, 94, 95.5)]                                     # gaps through the stop
    assert r(thesis.trade_bps(gap, "long", 3.0)) == (-500 - 4 - 3, True)


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


# --- revamp review G1-G3: registry bar, versions, futility, inactivity --------------------

import csv  # noqa: E402

import registry  # noqa: E402


def _rows(n, per_day=5, vs=4, version=None):
    return [{"date": f"d{i // per_day:03d}", "net_bps": 10 + i % 7, "vs_spy": vs + i % 6, "vs_random": vs + i % 7,
             "stopped": 0, "confidence": 0.6, **({"version": version} if version else {})} for i in range(n)]


def test_the_bar_rises_with_the_number_of_registered_tests():
    assert [registry.bar(k) for k in (1, 3, 5, 8, 12, 20)] == [2.0, 2.39, 2.58, 2.73, 2.87, 3.02]
    assert registry.bar() == registry.bar(len(registry.load()))       # read from forward_registry.json
    ids = [t["id"] for t in registry.load()]
    assert len(ids) == len(set(ids)) and {"thesis-desk-v1", "intraday-desk-v1", "flash-desk-v1"} <= set(ids)


def test_only_the_current_instruction_version_is_judged(monkeypatch):
    monkeypatch.setattr(registry, "current_version", lambda desk, tests=None: 2)
    s = thesis.summarize(_rows(300) + _rows(5, version="2"))
    assert s["n"] == 5 and s["older_versions_n"] == 300 and s["version"] == 2 and s["verdict"] == "running"


def test_a_desk_that_loses_to_random_stops_early_and_a_pass_needs_60_days():
    losing = [{**r, "vs_random": -abs(r["vs_random"])} for r in _rows(100)]
    assert thesis.summarize(losing)["verdict"].startswith("STOPPED: futility")
    assert thesis.summarize(losing[:99])["verdict"] == "running"
    short = _rows(300, per_day=10)          # 30 trading days: no final verdict yet, however good
    assert thesis.summarize(short)["verdict"] == "running"
    assert thesis.summarize(_rows(300))["verdict"].startswith("PASS")


def test_a_desk_that_stops_trading_is_called_inactive():
    s = thesis.summarize([], "intraday", sessions=[0] * 15)
    assert s["inactive"] and s["verdict"].startswith("inactive")
    assert not thesis.summarize([], "intraday", sessions=[0] * 14)["inactive"]     # not enough sessions yet
    assert not thesis.summarize([], "intraday", sessions=[1] * 15)["inactive"]
    assert thesis.summarize([], "thesis", sessions=[1] * 20)["inactive"]          # thesis needs 1.5 a morning


def test_old_score_files_get_the_version_column_without_losing_rows(tmp_path):
    path = str(tmp_path / "scores.csv")
    old = [f for f in thesis.FIELDS if f != "version"]
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=old)
        w.writeheader()
        w.writerow({k: "1" for k in old})
    thesis.write_rows(path, thesis.FIELDS, [{**{k: "2" for k in old}, "version": 2}])
    rows = list(csv.DictReader(open(path)))
    assert [r["version"] for r in rows] == ["", "2"] and rows[0]["id"] == "1"
