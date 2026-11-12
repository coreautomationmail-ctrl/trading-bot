"""STRATTON's evidence ladder: moves bench strategies between stages by fixed rules.

Stages: watch -> (backtest PASS, a code edit) -> shadow -> incubation -> pod, or retired.
Run weekly (Saturday after 16:00 UTC, market closed) by bench.py. At most one move per
strategy per run, every move is logged to STATE_DIR/ladder.json and sent to Telegram.

Rules, fixed 2026-11-07 before any strategy had forward data, never tuned on results.
z is (forward mean net bps - BACKTEST_NET_BPS) / its standard error, using per-trade
net bps; day t is the t-stat of summed daily net P&L (trades on one day aren't
independent).
  watch       moves only by a fresh pre-registered backtest PASS (a STAGE edit), or by a forward test
              written in the journal before the strategy had 30 forward trades (spike-news:
              journal 2026-11-12); otherwise reported only.
  shadow      -> incubation: n >= 60 virtual trades, mean net > 0, z >= -1, day t > 0
              -> retired:    n >= 60 and z <= -2
  incubation  -> pod:        n >= 100 paper trades, mean net > 0, z >= -1, day t > 0,
                             mean slippage <= 2 x the strategy's per-side cost
              -> retired:    n >= 30 and z <= -2, or paper drawdown > INCUB_MAX_DD_USD
  pod         -> retired:    n >= 30 and z <= -2 on paper trades since promotion
Promotion only ever raises size from the fixed table in bench.py; it can't change a
rule, a limit or the risk checks. Only the owner revives a retired strategy.
"""
import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone

STAGES = ["watch", "shadow", "incubation", "pod", "retired"]
SHADOW_MIN_N, INCUB_MIN_N, JUDGE_MIN_N = 60, 100, 30
INCUB_MAX_DD_USD = 1500.0


def stats(rows: list[dict], backtest: float) -> dict:
    """Per-trade and per-day statistics of `rows` (dicts with date, net_bps)."""
    x = [float(r["net_bps"]) for r in rows]
    n = len(x)
    out = {"n": n, "mean": None, "z": None, "day_t": None}
    if n < 2:
        out["mean"] = round(x[0], 2) if x else None
        return out
    mean = sum(x) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in x) / (n - 1))
    se = sd / math.sqrt(n) if sd > 0 else None
    days = defaultdict(float)
    for r in rows:
        days[r["date"]] += float(r["net_bps"])
    d = list(days.values())
    day_t = None
    if len(d) >= 2:
        dm = sum(d) / len(d)
        dsd = math.sqrt(sum((v - dm) ** 2 for v in d) / (len(d) - 1))
        day_t = round(dm / (dsd / math.sqrt(len(d))), 2) if dsd > 0 else (math.copysign(99.0, dm) if dm else None)
    out.update(mean=round(mean, 2), z=round((mean - backtest) / se, 2) if se else None, day_t=day_t)
    return out


def drawdown_usd(rows: list[dict]) -> float:
    peak = cum = worst = 0.0
    for r in rows:
        cum += float(r.get("pnl") or 0)
        peak = max(peak, cum)
        worst = max(worst, peak - cum)
    return worst


def decide(stage: str, s: dict, backtest_cost: float, slip: float = None, dd: float = 0.0):
    """(new stage, reason) or (stage, None) when nothing moves."""
    n, mean, z, day_t = s["n"], s["mean"], s["z"], s["day_t"]
    good = mean is not None and mean > 0 and z is not None and z >= -1 and (day_t or 0) > 0
    bad = z is not None and z <= -2
    if stage == "shadow" and n >= SHADOW_MIN_N:
        if bad:
            return "retired", f"forward mean {mean:+.1f} bps is {z} SE under the backtest"
        if good:
            return "incubation", f"forward mean {mean:+.1f} bps over {n} virtual trades fits the backtest"
    if stage == "incubation":
        if dd > INCUB_MAX_DD_USD:
            return "retired", f"paper drawdown ${dd:,.0f} over the ${INCUB_MAX_DD_USD:,.0f} limit"
        if n >= JUDGE_MIN_N and bad:
            return "retired", f"paper mean {mean:+.1f} bps is {z} SE under the backtest"
        if n >= INCUB_MIN_N and good and slip is not None and slip <= 2 * backtest_cost:
            return "pod", f"paper mean {mean:+.1f} bps over {n} trades, slippage {slip:.1f} bps"
    if stage == "pod" and n >= JUDGE_MIN_N and bad:
        return "retired", f"paper mean {mean:+.1f} bps is {z} SE under the backtest"
    return stage, None


def read_rows(path: str, name: str, since: str = "") -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path, newline="") as f:
        return [r for r in csv.DictReader(f) if r["strategy"] == name and r["date"] >= since]


def load(path: str) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save(path: str, ladder: dict):
    with open(path + ".tmp", "w") as f:
        json.dump(ladder, f, indent=1)
    os.replace(path + ".tmp", path)


def sync(ladder: dict, strategies: list) -> dict:
    """Adopt each strategy's code STAGE when it is new or that STAGE was edited."""
    today = datetime.now(timezone.utc).date().isoformat()
    for s in strategies:
        e = ladder.get(s.NAME)
        if e is None or e.get("code_stage") != s.STAGE:
            ladder[s.NAME] = {"stage": s.STAGE, "code_stage": s.STAGE, "since": today,
                              "history": (e or {}).get("history", []) + [[today, s.STAGE, "set in code"]]}
    return ladder


def stage_of(ladder: dict, name: str) -> str:
    return (ladder.get(name) or {}).get("stage", "watch")


def review(ladder: dict, strategies: list, virtual_csv: str, paper_csv: str) -> list[str]:
    """Apply the rules once; return one report line per strategy."""
    today = datetime.now(timezone.utc).date().isoformat()
    lines = []
    for s in strategies:
        e = ladder[s.NAME]
        stage = e["stage"]
        if stage == "retired":
            continue
        if stage in ("watch", "shadow"):
            rows = read_rows(virtual_csv, s.NAME)
            st = stats(rows, s.BACKTEST_NET_BPS)
            new, why = decide(stage, st, s.COST_BPS)
        else:
            rows = read_rows(paper_csv, s.NAME, e["since"])
            st = stats(rows, s.BACKTEST_NET_BPS)
            slips = [float(r["slip_bps"]) for r in rows if r.get("slip_bps") not in ("", None)]
            new, why = decide(stage, st, s.COST_BPS, sum(slips) / len(slips) if slips else None,
                              drawdown_usd(rows))
        mean = "n/a" if st["mean"] is None else f"{st['mean']:+.1f}"
        line = f"{s.NAME} [{stage}]: n {st['n']}, mean net {mean} bps (backtest {s.BACKTEST_NET_BPS:+.1f})"
        if why:
            e.update(stage=new, since=today)
            e["history"].append([today, new, why])
            line += f"\n  MOVED to {new}: {why}"
        lines.append(line)
    return lines
