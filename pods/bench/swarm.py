"""STRATTON swarm allocator and its verdict (docs/swarm-plan.md sections 3-4, fixed 2026-11-17).

Recomputed from the trade files every time, so there is no allocator state to drift or lose:
  - a bot's daily return is the mean net bps of the trades it closed that day (0 with none);
  - three books share those returns: the allocator (multiplicative weights, updated each Friday),
    equal weight, and random Dirichlet(1) weights redrawn each Friday from a seed fixed by the date;
  - the test is registry entry `swarm-v1`: day t of (allocator - equal) against bar(K).
"""
import csv
import math
import os
import random
from datetime import date

import registry

TEST_ID = "swarm-v1"
START = "2026-11-18"          # the first trading day counted (the registry date)
ETA = 0.01                    # weight multiplier per bps of 20-day summed return
LOOKBACK = 20                 # trading days
FLOOR, CAP = 0.2, 0.30        # floor is FLOOR / N
MIN_DAYS, KILL_DAYS = 60, 120
SPIKE_INPUTS = ("spike-quiet", "spike-news")   # bench strategies the swarm also takes as inputs


def read_trades(paths: list, names: set, start: str = START) -> dict:
    """{date: {bot: [net_bps, ...]}} for trades of `names` dated from `start` on."""
    out = {}
    for path in paths:
        if not os.path.exists(path):
            continue
        with open(path, newline="") as f:
            for r in csv.DictReader(f):
                if r.get("strategy") in names and r.get("date", "") >= start and r.get("net_bps") not in ("", None):
                    out.setdefault(r["date"], {}).setdefault(r["strategy"], []).append(float(r["net_bps"]))
    return out


def daily_returns(trades: dict, bots: list) -> list[tuple[str, list]]:
    """[(date, [return per bot, in `bots` order]), ...] oldest first."""
    return [(d, [sum(trades[d].get(b, [])) / len(trades[d][b]) if trades[d].get(b) else 0.0 for b in bots])
            for d in sorted(trades)]


def _normalise(w: list) -> list:
    n = len(w)
    for _ in range(20):            # floor and cap, then renormalise, until both hold
        s = sum(w)
        w = [max(FLOOR / n, min(CAP, x / s)) for x in w]
        if abs(sum(w) - 1) < 1e-9:
            break
    s = sum(w)
    return [x / s for x in w]


def _is_week_end(d: str, nxt: str | None) -> bool:
    """The last counted day of its ISO week (Friday, or earlier if the week ends on a holiday)."""
    if nxt is None:
        return False
    a, b = date.fromisoformat(d).isocalendar()[:2], date.fromisoformat(nxt).isocalendar()[:2]
    return a != b


def books(rets: list, n: int, start: str = START, test: str = TEST_ID) -> dict:
    """Daily returns of the three books: {"alloc": [...], "equal": [...], "random": [...], "weights": [...]}."""
    alloc = [1 / n] * n
    rnd = _random_weights(start, n, test)
    out = {"alloc": [], "equal": [], "random": [], "dates": [], "weights": alloc}
    for i, (d, r) in enumerate(rets):
        out["dates"].append(d)
        out["alloc"].append(sum(w * x for w, x in zip(alloc, r)))
        out["equal"].append(sum(r) / n)
        out["random"].append(sum(w * x for w, x in zip(rnd, r)))
        nxt = rets[i + 1][0] if i + 1 < len(rets) else None
        if _is_week_end(d, nxt):
            window = rets[max(0, i + 1 - LOOKBACK):i + 1]
            summed = [sum(day[1][j] for day in window) for j in range(n)]
            alloc = _normalise([w * math.exp(ETA * s) for w, s in zip(alloc, summed)])
            rnd = _random_weights(nxt, n, test)
    out["weights"] = alloc
    return out


def _random_weights(seed_day: str, n: int, test: str = TEST_ID) -> list:
    rng = random.Random(f"{test}-{seed_day}")
    g = [rng.gammavariate(1.0, 1.0) for _ in range(n)]
    s = sum(g)
    return [x / s for x in g]


def day_t(xs: list) -> float | None:
    n = len(xs)
    if n < 2:
        return None
    m = sum(xs) / n
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return m / sd * math.sqrt(n) if sd else None


def verdict(b: dict, bar: float) -> tuple[str, dict]:
    diff = [a - e for a, e in zip(b["alloc"], b["equal"])]
    n = len(diff)
    t = day_t(diff)
    t_rand = day_t([a - r for a, r in zip(b["alloc"], b["random"])])
    mean_alloc = sum(b["alloc"]) / n if n else 0.0
    half = n // 2
    halves = [sum(diff[:half]), sum(diff[half:])] if n >= 2 else [0.0, 0.0]
    facts = {"days": n, "t": t, "t_random": t_rand, "mean_alloc": mean_alloc, "halves": halves, "bar": bar}
    if n >= MIN_DAYS and t is not None and t >= bar and mean_alloc > 0 and (t_rand or 0) > 0 and min(halves) > 0:
        return "PASS", facts
    if n >= KILL_DAYS and (t is None or t < 1 or mean_alloc <= 0):
        return "KILL", facts
    return ("INCONCLUSIVE" if n >= KILL_DAYS else "RUNNING"), facts


def report(state_dir: str, bots: list, test: str = TEST_ID, start: str = START,
           files: tuple = ("swarm_virtual.csv", "swarm_daily.csv", "bench_virtual.csv")) -> str:
    """The weekly Telegram text, for swarm-v1 or (with test, start and files) another population."""
    names = list(bots)
    trades = read_trades([os.path.join(state_dir, f) for f in files], set(names), start)
    rets = daily_returns(trades, names)
    label = "swarm" if test == TEST_ID else test
    if not rets:
        return f"STRATTON {label} ({len(names)} bots): no closed trades yet since {start}."
    b = books(rets, len(names), start, test)
    k = sum(1 for _ in registry.load())
    status, f = verdict(b, registry.bar(k))
    traded = sum(1 for j in range(len(names)) if any(day[1][j] for day in rets))
    top = sorted(zip(b["weights"], names), reverse=True)[:5]
    fmt = lambda x: "n/a" if x is None else f"{x:+.2f}"   # noqa: E731
    return (f"STRATTON {label} ({len(names)} bots, {traded} with trades), day {f['days']} of {MIN_DAYS}+: {status}\n"
            f"Daily mean bps: allocator {sum(b['alloc']) / f['days']:+.2f}, equal {sum(b['equal']) / f['days']:+.2f}, "
            f"random {sum(b['random']) / f['days']:+.2f}\n"
            f"Allocator minus equal: day t {fmt(f['t'])} (bar {f['bar']:.2f}); minus random: day t {fmt(f['t_random'])}\n"
            "Heaviest: " + ", ".join(f"{n} {w:.1%}" for w, n in top))
