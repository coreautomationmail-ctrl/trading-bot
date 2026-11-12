"""Shadow bench engine and evidence ladder (pods/bench)."""
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "pods", "spike"))
sys.path.insert(0, os.path.join(ROOT, "pods", "bench"))

import bench  # noqa: E402
import ladder  # noqa: E402
import strategies  # noqa: E402


def bars(n, spike_at=None, start=datetime(2026, 11, 5, 14, 30, tzinfo=timezone.utc)):
    out = []
    for i in range(n):
        o = 100 + 0.01 * (i % 3)
        c = o + (0.01 if i % 2 else -0.01)
        v = 1000.0
        if i == spike_at:
            c, v = o * 1.01, 20000.0
        out.append({"t": bench.key(start + timedelta(minutes=i)), "o": o, "h": max(o, c), "l": min(o, c),
                    "c": c, "v": v})
    return out


def run(news):
    state = {"seen": [], "pending": []}
    eng = bench.Engine(list(strategies.ALL), bench.Ctx(lambda *a: news), state)
    eng.new_day("2026-11-05")
    sigs = [s for b in bars(120, spike_at=40) for s in eng.feed("AMD", b)]
    return sigs, eng.settle(final=True)


def test_spike_goes_to_quiet_without_news_and_is_scored_backtest_style():
    sigs, done = run([])
    assert [s["strategy"] for s in sigs] == ["spike-quiet"]
    b = bars(120, spike_at=40)
    exp = 1e4 * __import__("math").log(b[40 + 61]["c"] / b[40 + 2]["o"])
    assert abs(done[0]["gross_bps"] - round(exp, 2)) < 0.01
    assert abs(done[0]["net_bps"] - (done[0]["gross_bps"] - 4)) < 0.01


def test_headline_before_the_bar_routes_to_spike_news():
    t = bench.utc(bars(120)[40]["t"])
    assert [s["strategy"] for s in run([t - timedelta(minutes=10)])[0]] == ["spike-news"]
    assert [s["strategy"] for s in run([t])[0]] == ["spike-quiet"]   # not before the bar opened


def test_refeeding_a_day_does_not_duplicate_signals():
    state = {"seen": [], "pending": []}
    eng = bench.Engine(list(strategies.ALL), bench.Ctx(lambda *a: []), state)
    eng.new_day("2026-11-05")
    for b in bars(120, spike_at=40):
        eng.feed("AMD", b)
    eng2 = bench.Engine(list(strategies.ALL), bench.Ctx(lambda *a: []), state)   # a restart
    eng2.new_day("2026-11-05")
    assert sum(len(eng2.feed("AMD", b)) for b in bars(120, spike_at=40)) == 0
    assert len(state["pending"]) == 1


def rows(xs):
    return [{"date": f"2026-11-{1 + i % 20:02d}", "net_bps": x} for i, x in enumerate(xs)]


def test_ladder_promotes_a_shadow_that_fits_its_backtest():
    s = ladder.stats(rows([30, -10, 5] * 20), 8.0)
    assert ladder.decide("shadow", s, 2.0)[0] == "incubation"


def test_ladder_needs_60_trades_and_retires_a_clear_miss():
    assert ladder.decide("shadow", ladder.stats(rows([30, -10, 5] * 19), 8.0), 2.0)[1] is None
    assert ladder.decide("shadow", ladder.stats(rows([-30, -10, -5] * 20), 8.0), 2.0)[0] == "retired"


def test_watch_never_moves_on_forward_results():
    assert ladder.decide("watch", ladder.stats(rows([50, 40, 60] * 40), 8.0), 2.0) == ("watch", None)


def test_incubation_to_pod_checks_slippage_and_drawdown():
    s = ladder.stats(rows([30, -10, 5] * 34), 8.0)
    assert ladder.decide("incubation", s, 2.0, slip=3.0)[0] == "pod"
    assert ladder.decide("incubation", s, 2.0, slip=5.0)[0] == "incubation"
    assert ladder.decide("incubation", s, 2.0, slip=3.0, dd=2000)[0] == "retired"


def test_code_stage_edit_resets_ladder():
    lad = ladder.sync({}, strategies.ALL)
    lad["spike-news"]["stage"] = "retired"
    assert ladder.sync(lad, strategies.ALL)["spike-news"]["stage"] == "retired"
    lad["spike-news"]["code_stage"] = "shadow"     # STAGE edited since
    assert ladder.sync(lad, strategies.ALL)["spike-news"]["stage"] == "watch"


def test_paper_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "KILL_FILE", str(tmp_path / "KILL"))
    monkeypatch.setenv("INCUB_ALPACA_API_KEY", "x")
    st = {"open": [], "realized_today": 0.0}
    assert not bench.may_enter(st, "watch", "a", "AMD", set())
    assert not bench.may_enter(st, "shadow", "a", "AMD", set())
    assert bench.may_enter(st, "incubation", "a", "AMD", set())
    assert not bench.may_enter(st, "incubation", "a", "AMD", {"AMD"})
    st["open"] = [{"strategy": "a", "symbol": "X"}, {"strategy": "a", "symbol": "Y"}]
    assert not bench.may_enter(st, "incubation", "a", "AMD", set())
    st["open"], st["realized_today"] = [], -300.0
    assert not bench.may_enter(st, "pod", "a", "AMD", set())
    st["realized_today"] = 0.0
    (tmp_path / "KILL").write_text("")
    assert not bench.may_enter(st, "pod", "a", "AMD", set())
    monkeypatch.delenv("INCUB_ALPACA_API_KEY")
    os.remove(tmp_path / "KILL")
    assert not bench.may_enter(st, "pod", "a", "AMD", set())


def test_golive_needs_all_four_and_reports_progress():
    import golive

    trades = [{"date": f"2026-{m:02d}-{d:02d}", "symbol": f"S{i % 10}", "net_bps": 12 if i % 3 else -6}
              for i, (m, d) in enumerate((m, d) for m in (1, 2, 3, 4) for d in range(1, 29) for _ in range(3))]
    paper = [{"date": "2026-04-01", "symbol": "S1", "net_bps": 5, "slip_bps": 1.5}] * 60
    ops = [{"date": f"d{i}", "errors": "0"} for i in range(25)]
    r = golive.evaluate(trades, paper, 2.0, ops)
    assert r["checks"] == {"evidence": True, "fills": True, "risk": True, "operations": True} and r["ready"]
    assert not golive.evaluate(trades, paper, 2.0, ops[:-1] + [{"date": "x", "errors": "2"}])["ready"]
    assert not golive.evaluate(trades, [dict(p, slip_bps=3.0) for p in paper], 2.0, ops)["checks"]["fills"]
    assert not golive.evaluate(trades[:200], paper, 2.0, ops)["checks"]["evidence"]
    assert "READY" in golive.fmt("x", r, 2.0)


def test_wide_universe_is_the_research_set():
    assert len(strategies.WIDE) == 277 and "AAPL" in strategies.WIDE


def test_golive_counts_each_signal_once_and_only_full_months(tmp_path, monkeypatch):
    """Revamp review G5: a signal with a virtual row and a paper row is one trade; a 2-day month isn't a month."""
    import golive

    trades = [{"date": f"2026-{m:02d}-{d:02d}", "symbol": "S", "net_bps": 10} for m in (1, 2, 3) for d in range(1, 29)]
    thin = trades + [{"date": "2026-04-01", "symbol": "S", "net_bps": 10}] * 2
    r = golive.evaluate(thin, [], 2.0, [])
    assert r["months"] == 0 and not r["checks"]["evidence"]      # 28 a month < 30: none count
    assert r["median_bps"] == 10 and r["mean_ex_top1_bps"] == 10
    rows = {"bench_virtual.csv": [{"strategy": "x", "date": "2026-01-02", "symbol": "AA", "signal_bar": "T1", "net_bps": "5"},
                                  {"strategy": "x", "date": "2026-01-02", "symbol": "BB", "signal_bar": "T2", "net_bps": "5"}],
            "bench_paper.csv": [{"strategy": "x", "date": "2026-01-02", "symbol": "AA", "signal_bar": "T1", "net_bps": "4",
                                 "slip_bps": "1"}]}
    seen = []
    monkeypatch.setattr(golive, "read", lambda path: rows.get(os.path.basename(path), []))
    monkeypatch.setattr(golive, "evaluate", lambda t, p, c, o: seen.append(len(t)) or {
        "checks": {"evidence": False, "fills": False, "risk": False, "operations": False}, "ready": False})
    monkeypatch.setattr(golive, "fmt", lambda *a: "")
    monkeypatch.setattr(golive, "spike_pod", lambda *a: ([], []))

    class S:
        NAME, COST_BPS = "x", 2.0
    golive.report("spike", "bench", [S], [], {"x": "incubation"})
    assert seen[-1] == 2       # AA once (paper), BB once (virtual); seen[0] is the spike pod
