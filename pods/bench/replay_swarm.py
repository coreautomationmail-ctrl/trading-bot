"""swarm-replay-v1 harness (pods/bench/replay_registry.json). Feeds cached SIP sessions to the live
bench Engine and the unchanged swarm_bots / swarm / swarm_v3 code; changes no parameter.

A bot's trades are a pure function of its genome and the day's bars, so each distinct genome runs
once per day (in parallel) and its trades go to every bot that carries it. Then:
  B  swarm-v1's allocator over the 98 intraday bots (flat cost, as live): swarm.books;
  A  swarm-v3's two economies, stepped day by day with swarm_v3.step (honest cost), vs frozen.

Usage: python replay_swarm.py DATA_DIR OUT_JSON
"""
import gzip
import csv
import json
import math
import os
import sys
import tempfile
from collections import Counter
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..", "spike")]
os.environ.setdefault("STATE_DIR", tempfile.mkdtemp(prefix="replay_state_"))

import bench  # noqa: E402
import swarm  # noqa: E402
import swarm_bots  # noqa: E402
import swarm_v3  # noqa: E402

REPLAY_ID = "swarm-replay-v1"
BAR = 2.94
_W = {"dir": None, "day": None, "bars": None, "prior": None}


class SeenList(list):
    """Engine.state['seen'] with O(1) membership; same behaviour as the live list."""

    def __init__(self):
        super().__init__()
        self._s = set()

    def append(self, x):
        self._s.add(x)
        super().append(x)

    def __contains__(self, x):
        return x in self._s


def gkey(g: dict) -> str:
    return json.dumps(g, sort_keys=True)


def _prior_map(data_dir: str) -> dict:
    """{day: {sym: last daily close before day}} from daily.json."""
    daily = json.load(open(os.path.join(data_dir, "daily.json")))
    days = sorted(f[:10] for f in os.listdir(data_dir) if f.endswith(".csv.gz"))
    by_sym = {s: [(_et_day(b["t"]), b["c"]) for b in bars] for s, bars in daily.items()}
    out = {}
    for s, xs in by_sym.items():
        xs.sort()
        i = 0
        for d in days:
            while i < len(xs) and xs[i][0] < d:
                i += 1
            if i:
                out.setdefault(d, {})[s] = xs[i - 1][1]
    return out


def _et_day(t: str) -> str:
    return bench.utc(t).astimezone(bench.ET).date().isoformat()


def _load_day(data_dir: str, day: str):
    if _W["day"] != day:
        with gzip.open(os.path.join(data_dir, f"{day}.csv.gz"), "rt", newline="") as f:
            bars = [{**r, **{k: float(r[k]) for k in ("o", "h", "l", "c", "v")}} for r in csv.DictReader(f)]
        bars.sort(key=lambda r: (r["t"], r["symbol"]))
        _W["day"], _W["bars"] = day, bars
    return _W["bars"]


def _init(data_dir: str, prior: dict):
    _W["dir"], _W["prior"] = data_dir, prior


def run_day(job) -> tuple:
    """(day, {genome key: [(et, gross_bps, flat net_bps), ...]}) for the given genomes."""
    day, genomes = job
    bars = _load_day(_W["dir"], day)
    bots = [swarm_v3.make(g, k) for k, g in genomes]
    prior = _W["prior"].get(day, {})
    engine = bench.Engine(bots, bench.Ctx(lambda *a: [], lambda d: prior), {"seen": [], "pending": []})
    engine.new_day(day)
    engine.state["seen"] = SeenList()
    for b in bars:
        engine.feed(b["symbol"], b)
    out = {k: [] for k, _ in genomes}
    for r in engine.settle(final=True):
        out[r["strategy"]].append((r["et"], r["gross_bps"], r["net_bps"]))
    return day, out


def _chunks(xs: list, n: int) -> list:
    k = max(1, math.ceil(len(xs) / n))
    return [xs[i:i + k] for i in range(0, len(xs), k)]


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _adjust(book: list, spy: list) -> list:
    """Book daily bps minus beta x SPY open-to-close bps, beta from the previous 60 sessions."""
    out = []
    for i, x in enumerate(book):
        beta = 0.0
        if i >= 60:
            b, s = book[i - 60:i], spy[i - 60:i]
            mb, ms = _mean(b), _mean(s)
            var = sum((v - ms) ** 2 for v in s)
            beta = sum((u - mb) * (v - ms) for u, v in zip(b, s)) / var if var else 0.0
        out.append(x - beta * spy[i])
    return out


def _stats(diff: list) -> dict:
    n, half = len(diff), len(diff) // 2
    return {"n": n, "mean": _mean(diff), "t": swarm.day_t(diff), "halves": [sum(diff[:half]), sum(diff[half:])]}


def _by_year(dates: list, diff: list) -> dict:
    ys = {}
    for d, x in zip(dates, diff):
        ys.setdefault(d[:4], []).append(x)
    return {y: {"n": len(v), "mean": _mean(v), "t": swarm.day_t(v)} for y, v in ys.items()}


def _passes(a: dict, b: dict, mean: float) -> bool:
    return (a["t"] is not None and b["t"] is not None and a["t"] >= BAR and b["t"] >= BAR and mean > 0
            and min(a["halves"]) > 0 and min(b["halves"]) > 0)


def main(data_dir: str, out_path: str, workers: int = 11):
    days = sorted(f[:10] for f in os.listdir(data_dir) if f.endswith(".csv.gz"))
    prior = _prior_map(data_dir)
    seed_g = [(b.NAME, swarm_v3.genome_of(b)) for b in swarm_bots.ALL]
    v1_of = {gkey(g): n for n, g in seed_g}
    seed_jobs = [(gkey(g), g) for _, g in seed_g]
    trades = {}                                  # (day, key) -> [(et, gross, net)]
    with Pool(workers, initializer=_init, initargs=(data_dir, prior)) as pool:
        for i, (day, res) in enumerate(pool.imap(run_day, [(d, seed_jobs) for d in days], chunksize=4)):
            for k, rows in res.items():
                trades[(day, k)] = rows
            if i % 100 == 0:
                print(f"seed genomes: {i + 1}/{len(days)} days", flush=True)

        # B: swarm-v1 allocator, flat cost, as live (98 intraday bots)
        names = sorted(swarm_bots.NAMES)
        key_of = {n: gkey(g) for n, g in seed_g}
        rets = []
        for d in days:
            day_r = [_mean([x[2] for x in trades[(d, key_of[n])]]) if trades[(d, key_of[n])] else 0.0
                     for n in names]
            if any(trades[(d, key_of[n])] for n in names):
                rets.append((d, day_r))
        bk = swarm.books(rets, len(names), rets[0][0])

        # A: swarm-v3 economies, honest cost
        state = swarm_v3.seed()
        for i, d in enumerate(days):
            need = {}
            for eco in swarm_v3.ECOS:
                for p in state["pop"][eco].values():
                    k = gkey(p["g"])
                    if (d, k) not in trades:
                        need[k] = p["g"]
            if need:
                for day, res in pool.map(run_day, [(d, c) for c in _chunks(list(need.items()), workers)]):
                    for k, rows in res.items():
                        trades[(day, k)] = rows
            honest = lambda k, hold: [swarm_v3.recost(x[1], x[0], hold) for x in trades[(d, k)]]  # noqa: E731
            v3_rows = {}
            for eco in swarm_v3.ECOS:
                for n, p in state["pop"][eco].items():
                    xs = honest(gkey(p["g"]), p["g"]["hold"])
                    if xs:
                        v3_rows[n] = xs
            v1_rows = {v1_of[k]: honest(k, g["hold"]) for k, g in seed_jobs if trades[(d, k)]}
            swarm_v3.step(state, d, v3_rows, v1_rows)
            if i % 100 == 0:
                h = state["history"][-1]
                print(f"economy {d}: n_e {h['n_e']} n_c {h['n_c']}, {len(need)} new genomes", flush=True)

    # SPY open-to-close bps per session, from the minute bars
    spy = {}
    for d in days:
        sb = [b for b in _load_day(data_dir, d) if b["symbol"] == "SPY"]
        spy[d] = math.log(sb[-1]["c"] / sb[0]["o"]) * 1e4 if sb else 0.0

    hist = state["history"]
    hd = [h["date"] for h in hist]
    e, c, f = [h["e"] for h in hist], [h["c"] for h in hist], [h["f"] for h in hist]
    a_ec, a_ef = _stats([x - y for x, y in zip(e, c)]), _stats([x - y for x, y in zip(e, f)])
    s3 = [spy[d] for d in hd]
    ea, ca, fa = _adjust(e, s3), _adjust(c, s3), _adjust(f, s3)
    bd = bk["dates"]
    al, eq, rn = bk["alloc"], bk["equal"], bk["random"]
    b_ae, b_ar = _stats([x - y for x, y in zip(al, eq)]), _stats([x - y for x, y in zip(al, rn)])
    s1 = [spy[d] for d in bd]
    ala, eqa, rna = _adjust(al, s1), _adjust(eq, s1), _adjust(rn, s1)

    yearly_events = {}
    for h in hist:
        y = yearly_events.setdefault(h["date"][:4], Counter())
        for k in ("deaths_e", "births_e", "deaths_c", "births_c"):
            y[k] += h[k]
    mix = {eco: dict(Counter(p["g"]["fam"] for p in state["pop"][eco].values())) for eco in swarm_v3.ECOS}

    res = {
        "id": REPLAY_ID, "bar": BAR, "sessions": len(days), "first": days[0], "last": days[-1],
        "data": "Alpaca SIP 1-min, split-adjusted, 09:30-16:00 ET",
        "A_v3_economy": {"pass": _passes(a_ec, a_ef, _mean(e)), "mean_e": _mean(e), "mean_c": _mean(c),
                         "mean_f": _mean(f), "eco_minus_control": a_ec, "eco_minus_frozen": a_ef},
        "B_v1_allocator": {"pass": _passes(b_ae, b_ar, _mean(al)), "mean_alloc": _mean(al),
                           "mean_equal": _mean(eq), "mean_random": _mean(rn),
                           "alloc_minus_equal": b_ae, "alloc_minus_random": b_ar,
                           "final_top_weights": sorted(zip(bk["weights"], names), reverse=True)[:10]},
        "reported": {
            "A_by_year": {"eco_minus_control": _by_year(hd, [x - y for x, y in zip(e, c)]),
                          "eco_minus_frozen": _by_year(hd, [x - y for x, y in zip(e, f)])},
            "B_by_year": {"alloc_minus_equal": _by_year(bd, [x - y for x, y in zip(al, eq)]),
                          "alloc_minus_random": _by_year(bd, [x - y for x, y in zip(al, rn)])},
            "market_adjusted": {
                "A": {"mean_e": _mean(ea), "mean_c": _mean(ca), "mean_f": _mean(fa),
                      "eco_minus_control": _stats([x - y for x, y in zip(ea, ca)]),
                      "eco_minus_frozen": _stats([x - y for x, y in zip(ea, fa)])},
                "B": {"mean_alloc": _mean(ala), "mean_equal": _mean(eqa), "mean_random": _mean(rna),
                      "alloc_minus_equal": _stats([x - y for x, y in zip(ala, eqa)]),
                      "alloc_minus_random": _stats([x - y for x, y in zip(ala, rna)])}},
            "deaths_births_by_year": {y: dict(v) for y, v in yearly_events.items()},
            "final_family_mix": mix, "final_pop": {eco: len(state["pop"][eco]) for eco in swarm_v3.ECOS},
        },
        "history_v3": hist,
        "books_v1": {"dates": bd, "alloc": al, "equal": eq, "random": rn},
    }
    with open(out_path, "w") as fh:
        json.dump(res, fh, indent=1)
    print(json.dumps({k: res[k] for k in ("A_v3_economy", "B_v1_allocator")}, indent=1, default=str))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], *(int(x) for x in sys.argv[3:4]))
