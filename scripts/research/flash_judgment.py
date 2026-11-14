"""
flash-judgment (research/journal.md, 2026-11-14 PRE-REGISTRATION): Claude's call on the flash-replay flags dated
after its training data, against the obvious read.

  build  -> .cache/flash_judgment/items.jsonl   (what the desk sees at the flag minute; no outcome)
  ask    -> .cache/flash_judgment/answers.jsonl (claude -p, Opus 5.5, no tools, 16 flags a call; resumable)
  score  -> research/flash_judgment.log

Usage: python scripts/research/flash_judgment.py build|ask|score
"""
import json
import os
import re
import subprocess
import sys

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from flash_replay import CACHE, ET, load, single_name_headlines  # noqa: E402

FROM, MODEL, BATCH = "2026-07-01", "claude-opus-5-5", 16
OUT = os.path.join(CACHE, "flash_judgment")
ITEMS, ANSWERS = os.path.join(OUT, "items.jsonl"), os.path.join(OUT, "answers.jsonl")
LOG = os.path.join(HERE, "..", "..", "research", "flash_judgment.log")

PROMPT = """You are the flash desk of a paper-trading fund. Each item below is a live alert raised the minute it
happened: a single-name headline, and the stock's tape reacting (a 1-minute bar with >= 3x its recent volume and
>= 0.3% move from the last close before the headline). You see only what was known at the flag minute.

For each item, estimate p = the probability that, entering 5 minutes after the flag and holding 60 minutes, the
stock beats SPY IN THE DIRECTION OF ITS MOVE SINCE THE HEADLINE ("follow"). p < 0.5 means you expect it to fade.
Costs are 4 bps round trip either way. Use the whole range: 0.5 = no view. Judge the news (is it new, material,
already priced, a routine analyst note?) and the tape. Don't search; answer from the item.

Bars: "HH:MM pct vol" = minute, close as % from the pre-headline close, volume. "spy" = SPY % moves.

Reply with ONLY a JSON array, one object per item, in order:
[{"id": "...", "p": 0.0-1.0, "category": "one word (earnings, analyst, guidance, deal, legal, product, macro, other)", "why": "under 15 words"}]

ITEMS:
"""


def build() -> None:
    g = pd.read_csv(os.path.join(CACHE, "flash_replay_flags.csv"))
    g = g[(g["date"] >= FROM) & g["follow60"].notna()].reset_index(drop=True)
    news = single_name_headlines()
    spy = load("SPY")
    items = []
    for sym, sub in g.groupby("sym"):
        df = load(sym)
        for _, f in sub.iterrows():
            flagged = pd.Timestamp(f["flagged"]).tz_convert(ET)
            day = flagged.normalize()
            today = [(t, h) for t, h in news.get(sym, []) if day <= t < flagged]
            hl = max(t for t, h in today if h[:200] == f["headline"]) if today else flagged
            start = hl.floor("min")
            bars = df[(df.index >= day) & (df.index < flagged)]
            pre = bars[bars.index < start]
            ref = pre["close"].iloc[-1]
            show = bars[bars.index >= min(start - pd.Timedelta(minutes=30), day + pd.Timedelta(hours=9, minutes=30))]
            show = show.iloc[-60:]
            prev = df[df.index < day]["close"]
            sp = spy[(spy.index >= day) & (spy.index < flagged)]
            sprev = spy[spy.index < day]["close"].iloc[-1]
            spre = sp[sp.index < start]["close"]
            items.append({
                "id": f"{f['date']}-{sym}-{flagged:%H%M}", "symbol": sym, "flag_time": f"{flagged:%Y-%m-%d %H:%M} ET",
                "headline": f["headline"], "headline_time": f"{hl:%H:%M}",
                "earlier_headlines_today": [f"{t:%H:%M} {h[:140]}" for t, h in today if t < hl][-5:],
                "volume_x": f["volume_x"], "move_since_headline_pct": f["move_pct"],
                "day_change_pct": round((bars["close"].iloc[-1] / prev.iloc[-1] - 1) * 100, 2) if len(prev) else None,
                "spy": {"day_pct": round((sp["close"].iloc[-1] / sprev - 1) * 100, 2),
                        "since_headline_pct": round((sp["close"].iloc[-1] / spre.iloc[-1] - 1) * 100, 2) if len(spre) else None},
                "bars": [f"{t:%H:%M} {(r['close'] / ref - 1) * 100:+.2f} {int(r['volume'])}" for t, r in show.iterrows()]})
    items.sort(key=lambda x: x["id"][:10] + x["flag_time"])
    os.makedirs(OUT, exist_ok=True)
    with open(ITEMS, "w", encoding="utf-8") as fh:
        fh.writelines(json.dumps(i) + "\n" for i in items)
    print(f"{len(items)} items -> {ITEMS}")


def ask() -> None:
    items = [json.loads(x) for x in open(ITEMS, encoding="utf-8")]
    done = {json.loads(x)["id"] for x in open(ANSWERS, encoding="utf-8")} if os.path.exists(ANSWERS) else set()
    todo = [i for i in items if i["id"] not in done]
    for k in range(0, len(todo), BATCH):
        chunk = todo[k:k + BATCH]
        text = PROMPT + "\n".join(json.dumps(i) for i in chunk)
        r = subprocess.run(["claude", "-p", "--safe-mode", "--tools", "", "--model", MODEL, "--no-session-persistence"],
                           input=text, capture_output=True, text=True, encoding="utf-8", cwd=OUT, timeout=900)
        m = re.search(r"\[.*\]", r.stdout, re.S)
        if not m:
            print(f"batch {k // BATCH}: no JSON ({r.returncode}) {r.stdout[:200]} {r.stderr[:200]}", flush=True)
            continue
        got = {a["id"]: a for a in json.loads(m.group(0)) if isinstance(a, dict) and "id" in a}
        with open(ANSWERS, "a", encoding="utf-8") as fh:
            for i in chunk:
                if i["id"] in got:
                    fh.write(json.dumps(got[i["id"]]) + "\n")
        print(f"batch {k // BATCH + 1}/{-(-len(todo) // BATCH)}: {len(got)} of {len(chunk)}", flush=True)


def score() -> None:
    items = {json.loads(x)["id"]: json.loads(x) for x in open(ITEMS, encoding="utf-8")}
    ans = {}
    for x in open(ANSWERS, encoding="utf-8"):
        a = json.loads(x)
        ans[a["id"]] = a
    g = pd.read_csv(os.path.join(CACHE, "flash_replay_flags.csv"))
    g = g[(g["date"] >= FROM) & g["follow60"].notna()].copy()
    g["id"] = g["date"] + "-" + g["sym"] + "-" + pd.to_datetime(g["flagged"].str[:19]).dt.strftime("%H%M")
    g = g[g["id"].isin(ans)].copy()
    g["p"] = g["id"].map(lambda i: float(ans[i]["p"]))
    g["category"] = g["id"].map(lambda i: ans[i].get("category", ""))
    g["pick"] = np.where(g["p"] >= 0.5, g["follow60"], g["fade60"])
    g["won"] = (g["follow60"] + 4 > 0).astype(float)   # follow beat SPY gross
    ic = g["p"].rank().corr(g["follow60"].rank())
    rng = np.random.default_rng(20261114)
    perm = []
    for _ in range(10_000):
        q = g.groupby("date")["p"].transform(lambda s: s.sample(frac=1, random_state=rng.integers(1 << 31)).to_numpy())
        perm.append(q.rank().corr(g["follow60"].rank()))
    pval = (np.sum(np.array(perm) >= ic) + 1) / (len(perm) + 1)
    daily = g.groupby("date")["pick"].sum()
    brier = ((g["p"] - g["won"]) ** 2).mean()
    base = g["won"].mean()
    out = [f"flags scored {len(g)} of {len(items)} on {g['date'].nunique()} days; answers {len(ans)}",
           f"PRIMARY rank IC(p, follow60) = {ic:+.3f}, within-day permutation p = {pval:.4f}",
           f"  verdict: {'PASS' if ic >= 0.10 and pval < 0.05 else 'KILL' if ic < 0.02 else 'INCONCLUSIVE'}",
           f"pick net {g['pick'].mean():+.2f} bps (day t {daily.mean() / daily.std() * np.sqrt(len(daily)):+.2f}); "
           f"always follow {g['follow60'].mean():+.2f}, always fade {g['fade60'].mean():+.2f}",
           f"picks: follow {int((g['p'] >= 0.5).sum())}, fade {int((g['p'] < 0.5).sum())}; p mean {g['p'].mean():.3f}, sd {g['p'].std():.3f}",
           f"Brier {brier:.4f} vs 0.5 baseline 0.2500 vs base rate ({base:.3f}) {base * (1 - base):.4f}",
           "calibration (p bucket: n, follow won %, mean follow60):"]
    for k, sub in g.groupby(pd.cut(g["p"], [0, 0.35, 0.45, 0.5, 0.55, 0.65, 1.0], include_lowest=True), observed=True):
        out.append(f"  {k}: n={len(sub)}, won {sub['won'].mean():.0%}, follow60 {sub['follow60'].mean():+.1f}")
    out.append("by category (n, pick net, IC):")
    for k, sub in g.groupby("category"):
        c = sub["p"].rank().corr(sub["follow60"].rank()) if len(sub) > 4 else float("nan")
        out.append(f"  {k}: n={len(sub)}, pick {sub['pick'].mean():+.1f}, IC {c:+.2f}")
    text = "\n".join(out)
    print(text)
    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")


if __name__ == "__main__":
    {"build": build, "ask": ask, "score": score}[sys.argv[1]]()
