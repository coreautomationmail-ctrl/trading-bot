"""STRATTON swarm-v2 (docs/swarm-plan.md section 8): cross-asset trend, cross-sectional ranking, ridge."""
import math
import os
import random
import sys
from datetime import datetime, timedelta

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "pods", "spike"))
sys.path.insert(0, os.path.join(ROOT, "pods", "bench"))

import daily_swarm  # noqa: E402
import swarm_bots  # noqa: E402
import swarm_v2  # noqa: E402


def days(n, start="2025-06-02"):
    out, d = [], datetime.fromisoformat(start)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.date().isoformat())
        d += timedelta(days=1)
    return out


def bars(closes):
    return [{"d": d, "o": c * 0.999, "c": c} for d, c in zip(days(len(closes)), closes)]


def test_the_v2_population_matches_the_plan_and_is_separate_from_v1():
    assert len(swarm_v2.ALL) == 56 and len(swarm_v2.NAMES) == 56
    assert not swarm_v2.NAMES & (swarm_bots.NAMES | daily_swarm.NAMES)


def test_xtrend_follows_each_etf_and_only_sees_etfs():
    up = [100 + i * 0.1 for i in range(300)]
    down = [130 - i * 0.1 for i in range(300)]
    both = swarm_v2.XTrend(63, "ma", "both", 5)
    long_only = swarm_v2.XTrend(63, "ret", "long", 5)
    assert both.signal(up) == 1 and both.signal(down) == -1
    assert long_only.signal(down) == 0
    daily = {"GLD": bars(up), "AMD": bars(up)}            # AMD is a stock: not an xtrend symbol
    state = {}
    daily_swarm.step(daily, state, daily["GLD"][-1]["d"], [both])
    assert [p["symbol"] for p in state["open"]] == ["GLD"]


def test_xsec_momentum_and_reversal_take_opposite_sides():
    closes = {f"S{i:02d}": [100.0] * 30 + [100.0 + i] for i in range(12)}   # S11 rose most, S00 least
    mom = swarm_v2.XSec("mom", 20, 5, 5).signals(closes)
    rev = swarm_v2.XSec("rev", 20, 5, 5).signals(closes)
    assert mom["S11"] == 1 and mom["S00"] == -1 and len(mom) == 10
    assert rev == {s: -v for s, v in mom.items()}
    assert swarm_v2.XSec("mom", 20, 10, 5).signals(closes) == {}          # 12 names can't fill 10 + 10


def test_ridge_recovers_a_known_relationship():
    rng = random.Random(1)
    X = [[rng.gauss(0, 1), rng.gauss(0, 1)] for _ in range(400)]
    y = [0.5 + 2.0 * a - 1.0 * b + rng.gauss(0, 0.01) for a, b in X]
    w, mu, sd = swarm_v2.ridge(X, y, lam=0.0)
    pred = w[0] + w[1] * (1.0 - mu[0]) / sd[0] + w[2] * (0.0 - mu[1]) / sd[1]
    assert abs(pred - 2.5) < 0.05


def test_the_ml_bot_never_trains_on_outcomes_it_could_not_have_known(monkeypatch):
    seen = []
    real = swarm_v2.features

    def spy(c, t):
        seen.append((len(c), t))
        return real(c, t)

    monkeypatch.setattr(swarm_v2, "features", spy)
    rng = random.Random(2)
    closes = {f"S{i:02d}": [100 * math.exp(sum(rng.gauss(0, 0.01) for _ in range(j))) for j in range(1, 320)]
              for i in range(12)}
    bot = swarm_v2.MLRidge(5, 5, 126)
    sides = bot.signals(closes, "2026-W47")
    train = [(n, t) for n, t in seen if t != n - 1]          # the last call per symbol is today's prediction
    assert train and all(t + bot.h <= n - 1 for n, t in train)   # every label t+h is on or before today
    assert len(sides) == 10
    before = bot.model
    bot.signals(closes, "2026-W47")
    assert bot.model is before                                    # same week: no retraining
    bot.signals(closes, "2026-W48")
    assert bot.model is not before


def test_v2_runs_once_after_the_close_with_its_own_files(tmp_path, monkeypatch):
    monkeypatch.setattr(swarm_v2, "STATE_FILE", str(tmp_path / "state.json"))
    monkeypatch.setattr(swarm_v2, "TRADES_CSV", str(tmp_path / "trades.csv"))
    closes = [100 + i * 0.1 for i in range(300)]
    data = {"GLD": bars(closes)}
    today = data["GLD"][-1]["d"]
    written = []
    fetch = lambda syms, end, n: data                          # noqa: E731
    now = datetime.fromisoformat(today + "T17:30:00")
    assert swarm_v2.maybe_run(now.replace(hour=15), lambda *a: written.append(a), fetch) is None   # too early
    assert swarm_v2.maybe_run(now, lambda *a: written.append(a), fetch, [swarm_v2.XTrend(63, "ma", "both", 5)]) == 0
    assert swarm_v2.maybe_run(now, lambda *a: written.append(a), fetch) is None                    # once a day
    assert written[0][0] == str(tmp_path / "trades.csv")


def test_the_manifest_lists_v2_bots(tmp_path):
    import json
    swarm_v2.write_manifest(str(tmp_path / "m.json"))
    m = json.load(open(tmp_path / "m.json"))
    assert m["test"] == "swarm-v2" and len(m["names"]) == 56 and m["files"] == ["swarm_v2_daily.csv"]
