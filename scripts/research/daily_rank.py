"""
daily-rank (research/journal.md, 2026-11-14 PRE-REGISTRATION; the stop rule for Claude-as-trader): each day Claude
ranks every name with overnight single-name news by expected return vs SPY from 09:35 to the close.

  build -> .cache/daily_rank/days.jsonl     (one line per day: the names and what was known at 09:34; no outcome)
  ask   -> .cache/daily_rank/ranks.jsonl    (claude -p, Opus 5.5, no tools, one call per day; resumable)
  score -> research/daily_rank.log

Usage: python scripts/research/daily_rank.py build|ask|score   (STRATTON_CACHE points it at another universe)
"""
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from flash_replay import CACHE, ET, load, single_name_headlines  # noqa: E402

FROM, TO, MODEL = "2026-07-01", "2026-10-26", "claude-opus-5-5"
OUT = os.path.join(CACHE, "daily_rank")
DAYS, RANKS = os.path.join(OUT, "days.jsonl"), os.path.join(OUT, "ranks.jsonl")
LOG = os.path.join(HERE, "..", "..", "research", "daily_rank.log")

PROMPT = """You are the morning desk of a paper-trading fund. It is 09:35 ET on {day}. Below is every stock in our
universe that had company news since yesterday's close, with what is known right now: up to 3 latest headlines,
yesterday's return, today's opening gap, and the first 5 minutes of trading. SPY: gap {sgap:+.2f}%, first 5 min
{s5:+.2f}%.

RANK all {n} stocks from 1 to {n}: 1 = best expected return RELATIVE TO SPY from the 09:35 open to today's close,
{n} = worst. The top fifth will be bought and the bottom fifth shorted, held to the close. Every rank used exactly
once. Judge the news (new, material, surprising, already priced in the gap?) and the tape. Don't search the web.

Fields: prev_day_pct = yesterday's return; gap_pct = 09:30 open vs yesterday's close; first5_pct = 09:30 open to
09:34 close.

Reply with ONLY a JSON array: [{{"id": "SYMBOL", "rank": 1-{n}}}, ...]

STOCKS:
"""


def name_rows(args):
    sym, heads, sessions = args
    df = load(sym)
    if df is None:
        return []
    out = []
    days = df.index.normalize()
    closes = df.groupby(days)["close"].last()
    for day in sessions:
        prev = closes[closes.index < day]
        if len(prev) < 2:
            continue
        lo = prev.index[-1] + pd.Timedelta(hours=16)
        hs = [(t, h) for t, h in heads if lo <= t < day + pd.Timedelta(hours=9, minutes=25)]
        if not hs:
            continue
        bars = df[days == day]
        first = bars[bars.index < day + pd.Timedelta(hours=9, minutes=35)]
        rest = bars[(bars.index >= day + pd.Timedelta(hours=9, minutes=35))]
        if len(first) < 3 or len(rest) < 300:
            continue
        o = first["open"].iloc[0]
        out.append({"day": f"{day:%Y-%m-%d}", "id": sym,
                    "headlines": [f"{t:%m-%d %H:%M} {h[:160]}" for t, h in hs[-3:]],
                    "prev_day_pct": round((prev.iloc[-1] / prev.iloc[-2] - 1) * 100, 2),
                    "gap_pct": round((o / prev.iloc[-1] - 1) * 100, 2),
                    "first5_pct": round((first["close"].iloc[-1] / o - 1) * 100, 2),
                    "_r": (rest["close"].iloc[-1] / rest["open"].iloc[0] - 1) * 1e4})
    return out


def build() -> None:
    spy = load("SPY")
    sd = spy.index.normalize()
    sessions = [d for d in sd.unique() if FROM <= f"{d:%Y-%m-%d}" <= TO]
    news = single_name_headlines()
    with ProcessPoolExecutor() as ex:
        rows = [r for rs in ex.map(name_rows, [(s, h, sessions) for s, h in news.items() if s != "SPY"]) for r in rs]
    d = pd.DataFrame(rows)
    os.makedirs(OUT, exist_ok=True)
    with open(DAYS, "w", encoding="utf-8") as fh:
        for day in sessions:
            k = f"{day:%Y-%m-%d}"
            bars = spy[sd == day]
            first = bars[bars.index < day + pd.Timedelta(hours=9, minutes=35)]
            rest = bars[bars.index >= day + pd.Timedelta(hours=9, minutes=35)]
            pc = spy[sd < day]["close"].iloc[-1]
            spy_r = (rest["close"].iloc[-1] / rest["open"].iloc[0] - 1) * 1e4
            sub = d[d["day"] == k]
            if sub.empty:
                continue
            fh.write(json.dumps({"day": k, "spy_gap": round((first["open"].iloc[0] / pc - 1) * 100, 2),
                                 "spy_first5": round((first["close"].iloc[-1] / first["open"].iloc[0] - 1) * 100, 2),
                                 "names": sub.drop(columns=["day", "_r"]).to_dict("records"),
                                 "_outcome": dict(zip(sub["id"], (sub["_r"] - spy_r).round(2)))}) + "\n")
    print(f"{len(sessions)} days, {len(d)} name-days -> {DAYS}")


def ask() -> None:
    days = [json.loads(x) for x in open(DAYS, encoding="utf-8")]
    done = {json.loads(x)["day"] for x in open(RANKS, encoding="utf-8")} if os.path.exists(RANKS) else set()
    cwd = os.path.join(OUT, "empty")
    os.makedirs(cwd, exist_ok=True)
    for k, d in enumerate(days):
        if d["day"] in done:
            continue
        names = d["names"]   # the outcome stays in this process; only names go to the model
        text = PROMPT.format(day=d["day"], sgap=d["spy_gap"], s5=d["spy_first5"], n=len(names)) + \
            "\n".join(json.dumps(x) for x in names)
        r = subprocess.run(["claude", "-p", "--safe-mode", "--tools", "", "--model", MODEL, "--no-session-persistence"],
                           input=text, capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=1800)
        m = re.search(r"\[.*\]", r.stdout, re.S)
        if not m:
            print(f"{d['day']}: no JSON ({r.returncode}) {r.stdout[:160]} {r.stderr[:160]}", flush=True)
            if "limit" in r.stdout.lower():
                return
            continue
        got = {a["id"]: a for a in json.loads(m.group(0)) if isinstance(a, dict) and "id" in a}
        ids = [x["id"] for x in names]
        given = [float(got[i]["rank"]) if i in got else 9999.0 for i in ids]
        perm = sorted(given) == list(range(1, len(ids) + 1))
        ranks = pd.Series(given).rank(method="first").astype(int).tolist()
        with open(RANKS, "a", encoding="utf-8") as fh:
            fh.write(json.dumps({"day": d["day"], "permutation": perm, "missing": len(ids) - len(got),
                                 "ranks": dict(zip(ids, ranks))}) + "\n")
        print(f"{k + 1}/{len(days)} {d['day']}: {len(got)}/{len(ids)} permutation {perm}", flush=True)


def daily_ic(f: pd.DataFrame, col: str) -> pd.Series:
    return f.groupby("day").apply(lambda x: x[col].corr(x["r"], method="spearman"))


def line(name: str, ic: pd.Series) -> str:
    return f"{name}: IC {ic.mean():+.4f}, t {ic.mean() / ic.std() * np.sqrt(len(ic)):+.2f}, days {len(ic)}"


def score() -> None:
    days = {d["day"]: d for d in map(json.loads, open(DAYS, encoding="utf-8"))}
    recs = [json.loads(x) for x in open(RANKS, encoding="utf-8")]
    rows = []
    for rec in recs:
        d = days[rec["day"]]
        n = len(rec["ranks"])
        for x in d["names"]:
            rows.append({"day": rec["day"], "id": x["id"], "n": n, "rank": rec["ranks"][x["id"]],
                         "gap": x["gap_pct"], "first5": x["first5_pct"], "heads": len(x["headlines"]),
                         "r": d["_outcome"][x["id"]]})
    f = pd.DataFrame(rows)
    f["s"] = f["n"] + 1 - f["rank"]
    f["neg_gap"] = -f["gap"]
    ic = daily_ic(f, "s")
    t = ic.mean() / ic.std() * np.sqrt(len(ic))
    verdict = "PASS" if ic.mean() >= 0.05 and t >= 2.0 else "KILL (stop rule fires)" if ic.mean() < 0.02 else \
        "INCONCLUSIVE (U universe decides)"
    q = f.groupby("day")["rank"].transform(lambda s: pd.qcut(s, 5, labels=False))
    f["ls"] = np.where(q == 0, f["r"] - 4, np.where(q == 4, -f["r"] - 4, np.nan))
    ls = f.dropna(subset=["ls"]).groupby("day")["ls"].mean()
    out = [f"days {len(recs)}, name-days {len(f)}, non-permutation days {sum(not r['permutation'] for r in recs)}, "
           f"names/day {f.groupby('day').size().mean():.0f}",
           "PRIMARY " + line("Claude rank", ic), f"  verdict: {verdict}",
           f"long top fifth / short bottom fifth, net 4 bps/side: {ls.mean():+.2f} bps/day (t "
           f"{ls.mean() / ls.std() * np.sqrt(len(ls)):+.2f})",
           "baselines (same days):", "  " + line("gap reversal (-gap)", daily_ic(f, "neg_gap")),
           "  " + line("first-5-min move", daily_ic(f, "first5")),
           "by gap sign (Claude IC within day, then averaged):"]
    for k, sub in f.groupby(np.sign(f["gap"])):
        out.append("  " + line(f"gap {'up' if k > 0 else 'down' if k < 0 else 'flat'}",
                               daily_ic(sub[sub.groupby("day")["id"].transform("size") >= 5], "s")))
    out.append("by headline count:")
    for k, sub in f.groupby(f["heads"].clip(upper=3)):
        out.append("  " + line(f"{k} headline(s)", daily_ic(sub[sub.groupby("day")["id"].transform("size") >= 5], "s")))
    text = "\n".join(out)
    print(text)
    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")


if __name__ == "__main__":
    {"build": build, "ask": ask, "score": score}[sys.argv[1]]()
