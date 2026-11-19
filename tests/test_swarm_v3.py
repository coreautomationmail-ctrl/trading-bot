"""STRATTON swarm-v3 core (docs/swarm-plan.md section 9): the evolving economy and its control."""
import csv
import os
import random
import sys
from datetime import datetime

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "pods", "spike"))
sys.path.insert(0, os.path.join(ROOT, "pods", "bench"))

import spike_pod  # noqa: E402
import swarm_bots  # noqa: E402
import swarm_v3 as v3  # noqa: E402


def test_seed_copies_v1_intraday_genomes_into_both_economies():
    s = v3.seed()
    assert len(s["pop"]["e"]) == len(s["pop"]["c"]) == len(swarm_bots.ALL) == 98
    # the rebuilt bots signal exactly like the originals: same class and settings
    for i, b in enumerate(swarm_bots.ALL):
        g = v3.genome_of(b)
        nb = v3.make(g, v3.name_of(g, "e", i))
        assert type(nb) is type(b) and nb.HOLD_MIN == b.HOLD_MIN and nb.FIRST == b.FIRST and nb.LAST == b.LAST
        assert nb.NAME.startswith("sw3-" + b.NAME.split("-")[1] + "-" + b.mode)
        assert v3.hold_of(nb.NAME) == b.HOLD_MIN


def test_mutation_always_differs_and_stays_buildable():
    rng = random.Random(1)
    for b in swarm_bots.ALL:
        g = v3.genome_of(b)
        for _ in range(20):
            c = v3.mutate(g, rng)
            assert c != g and c["fam"] == g["fam"] and c["mode"] in v3.MODES[g["fam"]]
            bot = v3.make(c, v3.name_of(c, "e", 999))
            assert bot.FIRST <= bot.LAST, c          # it can still signal before its hold must end
            g = c


def test_honest_cost_charges_the_open_more():
    assert v3.recost(10.0, "09:35", 30) == 10.0 - 5.0 - 2.5      # in 09:37 (5), out 10:06 (2.5)
    assert v3.side_cost(9 * 60 + 37) == 5.0 and v3.side_cost(10 * 60 + 6) == 2.5


def _rows(names, day, net):
    return {n: [net(n)] * 50 for n in names}


def test_step_kills_losers_breeds_winners_and_control_matches_counts():
    s = v3.seed()
    e_names = sorted(s["pop"]["e"])
    c_names = sorted(s["pop"]["c"])
    winners = set(e_names[:5])
    losers = set(e_names[5:15])
    # 50 trades a day each; winners +40 bps mean, losers -60, the rest 0; control all 0
    day = "2026-11-19"
    rows = {**_rows(e_names, day, lambda n: 40.0 if n in winners else -60.0 if n in losers else 0.0),
            **_rows(c_names, day, lambda n: 0.0)}
    hist = []
    for i in range(40):
        d = f"2027-01-{i + 1:02d}" if i < 31 else f"2027-02-{i - 30:02d}"
        hist.append(v3.step(s, d, rows, {}))
    e, c = s["pop"]["e"], s["pop"]["c"]
    assert not (losers & set(e)), "losers died"
    assert s["births"]["e"] > 0 and any(p["gen"] > 0 for p in e.values())
    kids = [p for p in e.values() if p["gen"] > 0]
    assert all(p["parent"] in winners or p["gen"] > 1 for p in kids)
    # the control made the same number of deaths each day (it had enough eligible bots)
    assert [h["deaths_c"] for h in hist] == [h["deaths_e"] for h in hist]
    assert s["deaths"]["c"] == s["deaths"]["e"]
    # day 1's eco book: capital-weighted mean with equal capital = (5*40 - 10*60) / 98
    assert abs(hist[0]["e"] - (5 * 40 - 10 * 60) / 98) < 1e-3 and hist[0]["c"] == 0.0


def test_children_start_with_no_record_and_pay_from_parent():
    s = v3.seed()
    n = sorted(s["pop"]["e"])[0]
    s["pop"]["e"][n].update(cap=1.5, trades=100)
    v3.step(s, "2026-11-19", {}, {})
    kids = [k for k, p in s["pop"]["e"].items() if p["parent"] == n and p["gen"] == 1]
    assert len(kids) == 1
    k = s["pop"]["e"][kids[0]]
    assert k["trades"] == 0 and k["cap"] == v3.STAKE and k["born"] == "2026-11-19"
    assert abs(s["pop"]["e"][n]["cap"] - (1.5 - v3.RENT - v3.STAKE)) < 1e-9


def test_sync_swaps_bots_in_the_engine_list_in_place():
    s = v3.seed()
    strats = list(swarm_bots.ALL) + v3.bots(s)
    ref = strats
    victim = sorted(s["pop"]["e"])[0]
    del s["pop"]["e"][victim]
    g = v3.genome_of(swarm_bots.ALL[0])
    s["pop"]["e"]["sw3-gap-go-e500-h30"] = {"g": {**g, "mode": "go"}, "cap": 0.3, "born": "x", "trades": 0,
                                            "days": 0, "parent": "p", "gen": 1}
    v3.sync(strats, s)
    names = [b.NAME for b in strats]
    assert strats is ref and victim not in names and "sw3-gap-go-e500-h30" in names
    assert sum(1 for b in strats if not v3.is_v3(b.NAME)) == 98


def test_route_recosts_only_v3_rows():
    rows = [{"strategy": "sw3-orb-go-e3-h30", "et": "09:40", "gross_bps": 20.0, "net_bps": 16.0},
            {"strategy": "sw-orb-go-5-h30", "et": "09:40", "gross_bps": 20.0, "net_bps": 16.0}]
    out = v3.route(rows)
    assert len(out) == 1 and out[0]["net_bps"] == 20.0 - 5.0 - 2.5


def test_maybe_step_once_per_trading_day_and_frozen_book(tmp_path, monkeypatch):
    monkeypatch.setattr(v3, "TRADES_CSV", str(tmp_path / "v3.csv"))
    monkeypatch.setattr(v3, "V1_CSV", str(tmp_path / "v1.csv"))
    monkeypatch.setattr(v3, "SUMMARY", str(tmp_path / "sum.json"))
    path = str(tmp_path / "state.json")
    v1name = sorted(swarm_bots.NAMES)[0]
    with open(tmp_path / "v1.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["strategy", "date", "et", "gross_bps", "net_bps"])
        w.writeheader()
        w.writerow({"strategy": v1name, "date": "2026-11-19", "et": "11:00", "gross_bps": 98.0, "net_bps": 94.0})
    now = datetime(2026, 11, 19, 16, 30, tzinfo=spike_pod.ET)
    strats = []
    h = v3.maybe_step(now, strats, path)
    assert h is not None and v3.maybe_step(now, strats, path) is None
    assert abs(h["f"] - (98.0 - 4.0) / 98) < 1e-3            # re-costed at 2+2 midday, equal weight over 98
    assert len(strats) == 196
    assert v3.maybe_step(datetime(2026, 11, 20, 16, 30, tzinfo=spike_pod.ET), strats, path) is None  # no trades
    assert v3.load(path)["checked"] == "2026-11-20"
    assert "economy" in v3.report(str(tmp_path)) or "no day" in v3.report(str(tmp_path))
