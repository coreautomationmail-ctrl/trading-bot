"""STRATTON swarm-v1 (docs/swarm-plan.md): the bots' rules, the daily runner and the allocator."""
import csv
import math
import os
import sys
from datetime import datetime, timedelta, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "pods", "spike"))
sys.path.insert(0, os.path.join(ROOT, "pods", "bench"))

import bench  # noqa: E402
import daily_swarm  # noqa: E402
import swarm  # noqa: E402
import swarm_bots  # noqa: E402

OPEN_UTC = datetime(2026, 11, 18, 14, 30, tzinfo=timezone.utc)   # 09:30 ET


def day_bars(path: list, start=OPEN_UTC, vol=1000.0):
    """One bar per minute from 09:30 ET; `path` is the close of each minute."""
    out, prev = [], path[0]
    for i, c in enumerate(path):
        o = prev
        out.append({"t": bench.key(start + timedelta(minutes=i)), "o": o, "h": max(o, c), "l": min(o, c),
                    "c": c, "v": vol})
        prev = c
    return out


def engine_for(bots, prior=None):
    ctx = bench.Ctx(lambda *a: [], lambda day: prior or {})
    eng = bench.Engine(bots, ctx, {"seen": [], "pending": []})
    eng.new_day("2026-11-18")
    return eng


def feed(eng, sym, bars):
    return [s for b in bars for s in eng.feed(sym, b)]


def test_the_population_matches_the_plan():
    assert len(swarm_bots.ALL) == 98 and len(daily_swarm.ALL) == 53
    assert len(swarm_bots.NAMES) == 98 and len(daily_swarm.NAMES) == 53       # every name unique
    assert not swarm_bots.NAMES & daily_swarm.NAMES
    for b in swarm_bots.ALL:            # a hold always ends before the 15:55 flatten
        assert b.LAST + b.HOLD_MIN + 3 <= swarm_bots.END


def test_gap_fade_and_go_trade_opposite_ways_once():
    fade = swarm_bots.Gap(1, "fade", 9 * 60 + 35, 30)
    go = swarm_bots.Gap(1, "go", 9 * 60 + 35, 30)
    eng = engine_for([fade, go], prior={"AMD": 100.0})
    bars = day_bars([102.0] * 60)                      # opened 2% above the prior close
    sigs = feed(eng, "AMD", bars)
    assert sorted((s["strategy"], s["side"], s["et"]) for s in sigs) == [
        (fade.NAME, -1, "09:35"), (go.NAME, 1, "09:35")]
    eng2 = engine_for([swarm_bots.Gap(2, "fade", 9 * 60 + 35, 30)], prior={"AMD": 100.0})
    assert feed(eng2, "AMD", day_bars([101.0] * 60)) == []       # a 1% gap is under the 2% bar


def test_orb_trades_the_first_break_of_the_opening_range():
    go = swarm_bots.Orb(5, "go", 30)
    eng = engine_for([go])
    path = [100.0, 100.2, 99.9, 100.1, 100.0] + [100.0] * 10 + [100.5] + [100.0] * 30
    sigs = feed(eng, "AMD", day_bars(path))
    assert [(s["side"], s["et"]) for s in sigs] == [(1, "09:45")]


def test_vwap_reverts_only_after_1030_and_past_its_distance():
    rev = swarm_bots.Vwap(1, "revert", 30)
    eng = engine_for([rev])
    path = [100.0] * 61 + [100.5] * 5 + [102.0] * 5      # 10:31 onward: 2% over a ~100 VWAP
    sigs = feed(eng, "AMD", day_bars(path))
    assert len(sigs) == 1 and sigs[0]["side"] == -1 and sigs[0]["et"] >= "10:30"


def test_trend_needs_the_move_and_vwap_on_the_same_side():
    follow = swarm_bots.Trend(30, 0.5, "follow", 30)
    eng = engine_for([follow])
    path = [100.0] * 31 + [100.0 + 0.05 * i for i in range(1, 30)]
    sigs = feed(eng, "AMD", day_bars(path))
    assert sigs and sigs[0]["side"] == 1


def test_late_picks_the_universes_biggest_mover_from_the_open():
    fade = swarm_bots.Late(14 * 60, "fade", 30)
    eng = engine_for([fade])
    n = 14 * 60 - 9 * 60 - 30 + 10
    movers = {"AMD": [100.0] * 30 + [103.0] * (n - 30), "AAPL": [100.0] * 30 + [101.0] * (n - 30),
              "MSFT": [100.0] * 30 + [97.5] * (n - 30)}
    rows = sorted(((b["t"], s, b) for s, p in movers.items() for b in day_bars(p)), key=lambda r: (r[0], r[1]))
    sigs = [x for t, s, b in rows for x in eng.feed(s, b)]
    assert [(x["symbol"], x["side"]) for x in sigs] == [("AMD", -1)]


def test_swarm_trades_go_to_their_own_file_and_never_order(tmp_path, monkeypatch):
    monkeypatch.setattr(bench, "SWARM_CSV", str(tmp_path / "swarm.csv"))
    monkeypatch.setattr(bench, "VIRTUAL_CSV", str(tmp_path / "bench.csv"))
    bench.write_trades([{"strategy": swarm_bots.ALL[0].NAME, "date": "2026-11-18", "net_bps": 1.0},
                        {"strategy": "spike-quiet", "date": "2026-11-18", "net_bps": 2.0}])
    assert [r["strategy"] for r in csv.DictReader(open(tmp_path / "swarm.csv"))] == [swarm_bots.ALL[0].NAME]
    assert [r["strategy"] for r in csv.DictReader(open(tmp_path / "bench.csv"))] == ["spike-quiet"]


# ---------- daily ----------

def daily(closes: list, start="2026-08-03"):
    d0 = datetime.fromisoformat(start)
    days, out = [], []
    d = d0
    while len(days) < len(closes):
        if d.weekday() < 5:
            days.append(d.date().isoformat())
        d += timedelta(days=1)
    for day, c in zip(days, closes):
        out.append({"d": day, "o": c * 0.999, "c": c})
    return out


def test_daily_positions_open_at_the_next_open_and_close_after_hold_days():
    rev = daily_swarm.Rev(2, 2, 3)
    bars = daily([100.0] * 10 + [100.0, 97.0, 95.0])            # down 5% over 2 days: buy
    state = {}
    assert daily_swarm.step({"AMD": bars}, state, bars[-1]["d"], [rev]) == []
    assert [(p["strategy"], p["side"]) for p in state["open"]] == [(rev.NAME, 1)]
    later = daily([100.0] * 10 + [100.0, 97.0, 95.0, 96.0, 97.0, 98.0])
    closed = daily_swarm.step({"AMD": later}, state, later[-1]["d"], [rev])
    assert len(closed) == 1
    t = closed[0]
    assert t["entry_px"] == later[13]["o"] and t["exit_px"] == later[15]["c"] and t["date"] == later[15]["d"]
    assert math.isclose(t["net_bps"], math.log(later[15]["c"] / later[13]["o"]) * 1e4 - 4, abs_tol=0.01)


def test_night_holds_from_the_close_to_the_next_open():
    night = daily_swarm.Night("down")
    bars = daily([100.0, 99.0])
    state = {}
    daily_swarm.step({"SPY": bars}, state, bars[-1]["d"], [night])
    later = daily([100.0, 99.0, 99.5])
    closed = daily_swarm.step({"SPY": later}, state, later[-1]["d"], [night])
    assert closed[0]["entry_px"] == 99.0 and closed[0]["exit_px"] == later[2]["o"]


def test_drev3_skips_a_trade_that_goes_with_the_3_day_run():
    plain, skip = daily_swarm.Rev(2, 2, 1), daily_swarm.Rev(2, 2, 1, skip_runs=True)
    closes = [90.0, 99.0, 98.0, 96.0]      # down 3% over 2 days (buy), but up 6.7% over 3 days: skip
    assert plain.signal(closes) == 1 and skip.signal(closes) == 0


def test_no_new_daily_positions_without_a_bar_for_today():
    bars = daily([100.0] * 130)
    state = {}
    daily_swarm.step({"AMD": bars}, state, "2099-01-01", [daily_swarm.Mom(20, "both", 5)])
    assert state["open"] == []


# ---------- the allocator ----------

def rets_for(days: int, winner_bps: float, n: int = 4):
    start = datetime(2026, 11, 18)
    out, d = [], start
    while len(out) < days:
        if d.weekday() < 5:
            out.append((d.date().isoformat(), [winner_bps] + [0.0] * (n - 1)))
        d += timedelta(days=1)
    return out


def test_the_allocator_moves_weight_to_the_bot_that_wins_live():
    b = swarm.books(rets_for(30, 10.0), 4)
    assert b["weights"][0] > 0.25 and b["weights"][0] <= swarm.CAP + 1e-9
    assert sum(b["weights"]) == 1 or math.isclose(sum(b["weights"]), 1)
    assert sum(b["alloc"]) > sum(b["equal"])      # it earned more than equal weight once it shifted


def test_random_weights_repeat_for_the_same_week():
    assert swarm._random_weights("2026-11-23", 5) == swarm._random_weights("2026-11-23", 5)
    assert swarm._random_weights("2026-11-23", 5) != swarm._random_weights("2026-11-30", 5)


def test_verdict_needs_60_days_and_the_bar():
    b = swarm.books(rets_for(30, 10.0), 4)
    assert swarm.verdict(b, 2.89)[0] == "RUNNING"
    flat = swarm.books(rets_for(130, 0.0), 4)
    assert swarm.verdict(flat, 2.89)[0] == "KILL"


def test_report_reads_the_trade_files(tmp_path):
    names = [swarm_bots.ALL[0].NAME, daily_swarm.ALL[0].NAME]
    with open(tmp_path / "swarm_virtual.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["strategy", "date", "net_bps"])
        w.writeheader()
        w.writerow({"strategy": names[0], "date": "2026-11-18", "net_bps": 5.0})
        w.writerow({"strategy": names[0], "date": "2026-11-10", "net_bps": 50.0})   # before START: ignored
    text = swarm.report(str(tmp_path), names)
    assert "2 bots, 1 with trades" in text and "day 1 of 60+" in text and "RUNNING" in text


def test_the_manifest_lists_every_bot_in_the_reports_order(tmp_path):
    import json
    bench.write_swarm_manifest(str(tmp_path / "m.json"))
    m = json.load(open(tmp_path / "m.json"))
    assert len(m["names"]) == 153 and m["names"] == sorted(m["names"]) and m["test"] == "swarm-v1"
    assert m["bar"] > 2.8 and m["start"] == swarm.START
