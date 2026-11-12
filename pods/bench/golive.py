"""STRATTON's go-live case: the bar a strategy must clear before real money.

Owner, 2026-11-07: the main goal is great data and a reason to put STRATTON in
the real market, with the trustworthiness of a fund. These checks were written
that day, before any strategy had the data to pass them, and are never loosened.
Passing makes the case; going live stays the owner's decision and account.

Evidence is forward only: signals scored as they happened after a strategy's
registration date. Virtual trades (the shadow log, the bench) and paper trades
both count toward n, because neither can contain hindsight; paper trades alone
prove the fills.

  1 evidence    n >= 300 forward trades; mean net > 0; t of summed daily net >= 2;
                >= 3 calendar months, net positive in >= 2/3 of them
  2 fills       >= 50 paper trades; mean slippage <= the cost the backtest assumed (per side)
  3 risk        worst drawdown of the forward curve at $5,000 a trade <= $2,500;
                no single day and no single symbol > 25% of net P&L
  4 operations  the last 20 sessions in ops.csv with no errors in the pod or the bench

Also reported, fund style: net return per trade, Sharpe of daily P&L (annualized,
252 days), win rate, max drawdown, trades a day.
"""
import csv
import math
import os
from collections import defaultdict

import registry

MIN_N, MIN_T, MIN_MONTHS, MONTH_SHARE = 300, 2.0, 3, 2 / 3
MIN_PAPER, TRADE_USD, MAX_DD_USD, MAX_SHARE = 50, 5000.0, 2500.0, 0.25
MONTH_MIN_TRADES = 30   # a month counts toward the 3 months only with this many trades (revamp review G5)
OPS_SESSIONS = 20


def need_t() -> float:
    """MIN_T, raised for the number of forward tests registered (registry.py, revamp review G1).
    Only ever higher than the 2.0 fixed on 11/07: the bar is never loosened."""
    return max(MIN_T, registry.bar())


def read(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def _t(x: list[float]):
    if len(x) < 2:
        return None
    m = sum(x) / len(x)
    sd = math.sqrt(sum((v - m) ** 2 for v in x) / (len(x) - 1))
    return m / (sd / math.sqrt(len(x))) if sd > 0 else (math.copysign(math.inf, m) if m else None)


def evaluate(trades: list[dict], paper: list[dict], cost_bps: float, ops: list[dict]) -> dict:
    """trades: forward rows {date, symbol, net_bps}; paper: rows with slip_bps; ops: {date, errors}."""
    x = [float(t["net_bps"]) for t in trades]
    n = len(x)
    days, months, syms = defaultdict(float), defaultdict(float), defaultdict(float)
    for t in trades:
        v = float(t["net_bps"])
        days[t["date"]] += v
        months[t["date"][:7]] += v
        syms[t["symbol"]] += v
    total = sum(x)
    day_vals = [days[d] for d in sorted(days)]
    curve = peak = dd = 0.0
    for v in day_vals:
        curve += v * TRADE_USD / 1e4
        peak = max(peak, curve)
        dd = max(dd, peak - curve)
    day_t = _t(day_vals)
    month_n = defaultdict(int)
    for t in trades:
        month_n[t["date"][:7]] += 1
    months = {m: v for m, v in months.items() if month_n[m] >= MONTH_MIN_TRADES}   # 2 days in a month isn't a month
    pos_months = sum(v > 0 for v in months.values())
    slips = [float(p["slip_bps"]) for p in paper if p.get("slip_bps") not in ("", None)]
    recent = ops[-OPS_SESSIONS:]
    checks = {
        "evidence": n >= MIN_N and total > 0 and (day_t or 0) >= need_t() and len(months) >= MIN_MONTHS
                    and pos_months >= MONTH_SHARE * len(months),
        "fills": len(paper) >= MIN_PAPER and bool(slips) and sum(slips) / len(slips) <= cost_bps,
        "risk": n > 0 and total > 0 and dd <= MAX_DD_USD and max(day_vals) <= MAX_SHARE * total
                and max(syms.values()) <= MAX_SHARE * total,
        "operations": len(recent) >= OPS_SESSIONS and all(int(o["errors"]) == 0 for o in recent),
    }
    sd_day = (math.sqrt(sum((v - total / len(day_vals)) ** 2 for v in day_vals) / (len(day_vals) - 1))
              if len(day_vals) > 1 else 0)
    return {
        "checks": checks, "ready": all(checks.values()), "n": n, "paper_n": len(paper),
        "mean_bps": total / n if n else None, "day_t": day_t, "months": len(months), "pos_months": pos_months,
        "slip_bps": sum(slips) / len(slips) if slips else None, "dd_usd": dd,
        "sharpe": (total / len(day_vals)) / sd_day * math.sqrt(252) if sd_day and len(day_vals) >= 20 else None,
        "win": sum(v > 0 for v in x) / n if n else None, "per_day": n / len(days) if days else 0,
        # The spike edge is tail-driven (top 1% of trades made 45-63% of P&L in the backtest), so the
        # report shows what the typical trade does and what's left without the best 1%.
        "median_bps": sorted(x)[n // 2] if n else None,
        "mean_ex_top1_bps": (sum(sorted(x)[:n - max(1, n // 100)]) / (n - max(1, n // 100))) if n > 1 else None,
        "clean_sessions": next((i for i, o in enumerate(reversed(ops)) if int(o["errors"])), len(ops)),
    }


def fmt(name: str, r: dict, cost_bps: float) -> str:
    def f(v, spec, none="n/a"):
        return none if v is None else format(v, spec)
    mark = {True: "PASS", False: "not yet"}
    c = r["checks"]
    return "\n".join([
        f"{name}: {'READY for the owner to decide on real money' if r['ready'] else 'building the case'}",
        f"  1 evidence {mark[c['evidence']]}: {r['n']}/{MIN_N} trades, mean {f(r['mean_bps'], '+.1f')} bps, "
        f"day t {f(r['day_t'], '.2f')}/{need_t()}, months {r['pos_months']}/{r['months']} positive (need {MIN_MONTHS}+)",
        f"  2 fills {mark[c['fills']]}: {r['paper_n']}/{MIN_PAPER} paper trades, slippage {f(r['slip_bps'], '.1f')} "
        f"bps vs {cost_bps:g} assumed",
        f"  3 risk {mark[c['risk']]}: drawdown ${r['dd_usd']:,.0f}/{MAX_DD_USD:,.0f} at ${TRADE_USD:,.0f} a trade",
        f"  4 operations {mark[c['operations']]}: {r['clean_sessions']}/{OPS_SESSIONS} clean sessions in a row",
        f"  fund view: Sharpe {f(r['sharpe'], '.2f')}, win rate {f(r['win'], '.0%')}, "
        f"{r['per_day']:.1f} trades a day; median trade {f(r['median_bps'], '+.1f')} bps, "
        f"mean without the best 1% {f(r['mean_ex_top1_bps'], '+.1f')} bps",
    ])


# ---------- the strategies' evidence ----------

SPIKE_REGISTERED = "2026-11-02"     # v2 rule live 10/30; its shadow log starts 11/02


def spike_pod(spike_dir: str, universe: list[str]) -> tuple[list[dict], list[dict]]:
    """The live pod's rule: shadow signals on its own names and window (net of 2 bps a side),
    plus its paper trades (fill to fill, so slippage is already in)."""
    virtual = [{"date": r["date"], "symbol": r["symbol"], "net_bps": float(r["bps"]) - 4}
               for r in read(os.path.join(spike_dir, "spike_shadow.csv"))
               if r["symbol"] in universe and "13:00" <= r["et"] <= "14:54" and r["date"] >= SPIKE_REGISTERED]
    paper = [{"date": r["entry_time"][:10], "symbol": r["symbol"], "net_bps": float(r["bps"]),
              "slip_bps": r.get("slip_bps", "")} for r in read(os.path.join(spike_dir, "spike_trades.csv"))]
    return virtual, paper


def report(spike_dir: str, bench_dir: str, strategies: list, universe: list[str], stages: dict) -> str:
    ops = read(os.path.join(bench_dir, "ops.csv"))
    blocks = []
    virtual, paper = spike_pod(spike_dir, universe)
    # The pod's paper trades are signals too; the shadow log already holds most of them, so
    # only its own rows count toward n and the paper rows prove fills.
    blocks.append(fmt("Spike pod", evaluate(virtual, paper, 2.0, ops), 2.0))
    vrows, prows = read(os.path.join(bench_dir, "bench_virtual.csv")), read(os.path.join(bench_dir, "bench_paper.csv"))
    for s in strategies:
        if stages.get(s.NAME, "watch") in ("watch", "retired"):
            continue      # near-misses and retired strategies can't make a go-live case
        v = [r for r in vrows if r["strategy"] == s.NAME]
        p = [r for r in prows if r["strategy"] == s.NAME]
        # Each signal once (revamp review G5): the bench logs a virtual row for every signal and, once a
        # strategy trades paper, a paper row too. The paper row (real fills) wins.
        papered = {(r["symbol"], r.get("signal_bar")) for r in p if r.get("signal_bar")}
        v = [r for r in v if (r["symbol"], r.get("signal_bar")) not in papered]
        blocks.append(fmt(s.NAME, evaluate(v + p, p, s.COST_BPS, ops), s.COST_BPS))
    return "STRATTON go-live case (real money only when all four pass; your call):\n" + "\n".join(blocks)
