"""
flash-chart (research/journal.md, 2026-11-14 PRE-REGISTRATION): can Claude read charts? Forced ranking of the
flash-judgment flags in batches of 16, three arms: N (numbers), C (chart image), NC (both).

  render -> .cache/flash_judgment/charts/<id>.png  (session from 09:30 to the flag minute; nothing later)
  ask N|C|NC -> .cache/flash_judgment/rank_<arm>.jsonl  (one line per batch; resumable)
  score  -> research/flash_chart.log

Usage: python scripts/research/flash_chart.py render | ask N | ask C | ask NC | score
"""
import json
import os
import re
import shutil
import subprocess
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from flash_replay import CACHE, ET, load  # noqa: E402
from flash_judgment import FROM, ITEMS, MODEL, OUT  # noqa: E402

BATCH = 16
CHARTS = os.path.join(OUT, "charts")
LOG = os.path.join(HERE, "..", "..", "research", "flash_chart.log")

PROMPT = """You are the flash desk of a paper-trading fund. Each item below is a live alert raised the minute it
happened: a single-name headline, and the stock's tape reacting (a 1-minute bar with >= 3x its recent volume and
>= 0.3% move from the last close before the headline). You see only what was known at the flag minute.

RANK all {n} items from 1 to {n}: 1 = most likely that, entering 5 minutes after the flag and holding 60 minutes,
the stock beats SPY IN THE DIRECTION OF ITS MOVE SINCE THE HEADLINE ("follow"); {n} = most likely to fade (reverse
against SPY). The top half will be traded with the move, the bottom half against it. Every rank 1..{n} used exactly
once. Costs are 4 bps round trip. Judge the news (new, material, already priced, a routine analyst note?) and the
tape. Don't search the web; answer from the items.
{extra}
Reply with ONLY a JSON array, one object per item:
[{{"id": "...", "rank": 1-{n}, "why": "under 15 words"}}]

ITEMS:
"""
EXTRA = {
    "N": 'Bars: "HH:MM pct vol" = minute, close as % from the pre-headline close, volume. "spy" = SPY % moves.',
    "C": ('Each item has a "chart" file in the current folder: open every one with the Read tool before ranking. '
          "Chart: 1-minute candles from the 09:30 open to the flag minute, y = % from the last close before the "
          "headline, grey line = SPY % over the same window, dashed line = headline minute, lower panel = volume. "
          '"spy" = SPY % moves.'),
}
EXTRA["NC"] = EXTRA["C"] + ' Bars: "HH:MM pct vol" = minute, close as % from the pre-headline close, volume.'


def items() -> list:
    return [json.loads(x) for x in open(ITEMS, encoding="utf-8")]


def render() -> None:
    os.makedirs(CHARTS, exist_ok=True)
    spy = load("SPY")
    its = items()
    for sym in sorted({i["symbol"] for i in its}):
        df = load(sym)
        for it in (i for i in its if i["symbol"] == sym):
            flagged = pd.Timestamp(it["flag_time"][:16]).tz_localize(ET)
            day = flagged.normalize()
            hl = day + pd.Timedelta(hours=int(it["headline_time"][:2]), minutes=int(it["headline_time"][3:]))
            bars = df[(df.index >= day + pd.Timedelta(hours=9, minutes=30)) & (df.index < flagged)]
            pre = df[(df.index >= day) & (df.index < hl)]
            ref = pre["close"].iloc[-1]
            sp = spy[(spy.index >= bars.index[0]) & (spy.index < flagged)]
            spref = spy[spy.index < max(hl, bars.index[0])]["close"].iloc[-1]
            pct = (bars[["open", "high", "low", "close"]] / ref - 1) * 100
            x = np.arange(len(bars))
            fig, (ax, av) = plt.subplots(2, 1, figsize=(10, 6), sharex=True, gridspec_kw={"height_ratios": [3, 1]})
            up = pct["close"] >= pct["open"]
            ax.vlines(x, pct["low"], pct["high"], color=np.where(up, "#1a9850", "#d73027"), linewidth=0.8)
            ax.bar(x, (pct["close"] - pct["open"]).abs().clip(lower=0.005), bottom=pct[["open", "close"]].min(axis=1),
                   color=np.where(up, "#1a9850", "#d73027"), width=0.7)
            sx = bars.index.get_indexer(sp.index, method="nearest")
            ax.plot(sx, (sp["close"] / spref - 1) * 100, color="grey", linewidth=1, label="SPY %")
            ax.axhline(0, color="black", linewidth=0.5)
            h = bars.index.searchsorted(hl)
            for a in (ax, av):
                a.axvline(h, color="#4575b4", linestyle="--", linewidth=1)
            av.bar(x, bars["volume"], color=np.where(up, "#1a9850", "#d73027"), width=0.7)
            ticks = [i for i, t in enumerate(bars.index) if t.minute % 30 == 0]
            av.set_xticks(ticks, [f"{bars.index[i]:%H:%M}" for i in ticks])
            ax.set_ylabel("% from pre-headline close")
            av.set_ylabel("volume")
            ax.set_title(f"{sym}  {flagged:%Y-%m-%d}  to {flagged:%H:%M} ET  (dashed = headline {hl:%H:%M})")
            ax.legend(loc="upper left")
            fig.tight_layout()
            fig.savefig(os.path.join(CHARTS, it["id"] + ".png"), dpi=90)
            plt.close(fig)
    print(f"{len(os.listdir(CHARTS))} charts -> {CHARTS}")


def view(it: dict, arm: str) -> dict:
    v = dict(it)
    if arm == "C":
        v.pop("bars")
    if arm in ("C", "NC"):
        v["chart"] = it["id"] + ".png"
    return v


def ask(arm: str) -> None:
    its = items()
    path = os.path.join(OUT, f"rank_{arm}.jsonl")
    done = {json.loads(x)["batch"] for x in open(path, encoding="utf-8")} if os.path.exists(path) else set()
    for b in range(-(-len(its) // BATCH)):
        if b in done:
            continue
        chunk = its[b * BATCH:(b + 1) * BATCH]
        text = PROMPT.format(n=len(chunk), extra=EXTRA[arm]) + "\n".join(json.dumps(view(i, arm)) for i in chunk)
        cmd = ["claude", "-p", "--safe-mode", "--model", MODEL, "--no-session-persistence"]
        cwd = os.path.join(OUT, "empty")
        if arm == "N":
            cmd += ["--tools", ""]
        else:
            cwd = os.path.join(OUT, "batch_view")
            shutil.rmtree(cwd, ignore_errors=True)
            os.makedirs(cwd)
            for i in chunk:
                shutil.copy(os.path.join(CHARTS, i["id"] + ".png"), cwd)
            cmd += ["--tools", "Read", "--allowedTools", "Read", "--output-format", "stream-json", "--verbose"]
        os.makedirs(cwd, exist_ok=True)
        r = subprocess.run(cmd, input=text, capture_output=True, text=True, encoding="utf-8", cwd=cwd, timeout=1800)
        out, reads = r.stdout, []
        if arm != "N":
            out = ""
            for line in r.stdout.splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                if ev.get("type") == "assistant":
                    for c in ev["message"].get("content", []):
                        if c.get("type") == "tool_use":
                            reads.append(str(c.get("input", {}).get("file_path", c.get("input"))))
                if ev.get("type") == "result":
                    out = ev.get("result", "")
            bad = [p for p in reads if os.path.basename(p) not in {i["id"] + ".png" for i in chunk}
                   or os.path.normcase(os.path.realpath(os.path.join(cwd, os.path.dirname(p))))
                   != os.path.normcase(os.path.realpath(cwd))]
            if bad:
                print(f"batch {b}: VOID, read outside the batch charts: {bad[:3]}", flush=True)
                continue
        m = re.search(r"\[.*\]", out, re.S)
        if not m:
            print(f"batch {b}: no JSON ({r.returncode}) {out[:200]} {r.stderr[:200]}", flush=True)
            continue
        got = {a["id"]: a for a in json.loads(m.group(0)) if isinstance(a, dict) and "id" in a}
        ids = [i["id"] for i in chunk]
        given = [float(got[i]["rank"]) if i in got else 99.0 for i in ids]
        perm = sorted(given) == list(range(1, len(ids) + 1))
        ranks = pd.Series(given).rank(method="first").astype(int).tolist()
        rec = {"batch": b, "arm": arm, "permutation": perm, "missing": len(ids) - len(got),
               "charts_read": len(set(map(os.path.basename, reads))),
               "ranks": dict(zip(ids, ranks)), "why": {i: got.get(i, {}).get("why", "") for i in ids}}
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
        print(f"{arm} batch {b + 1}: {len(got)}/{len(ids)}, permutation {perm}, charts read {rec['charts_read']}",
              flush=True)


def score() -> None:
    g = pd.read_csv(os.path.join(CACHE, "flash_replay_flags.csv"))
    g = g[(g["date"] >= FROM) & g["follow60"].notna()].copy()
    g["id"] = g["date"] + "-" + g["sym"] + "-" + pd.to_datetime(g["flagged"].str[:19]).dt.strftime("%H%M")
    g = g.set_index("id")
    rng = np.random.default_rng(20261114)
    out, per = [], {}
    for arm in ("N", "C", "NC"):
        path = os.path.join(OUT, f"rank_{arm}.jsonl")
        if not os.path.exists(path):
            out.append(f"{arm}: no answers")
            continue
        recs = [json.loads(x) for x in open(path, encoding="utf-8")]
        rows = [(r["batch"], i, k) for r in recs for i, k in r["ranks"].items()]
        d = pd.DataFrame(rows, columns=["batch", "id", "rank"]).join(g[["date", "follow60", "fade60"]], on="id")
        d["s"] = 17 - d["rank"]
        per[arm] = d

        def ic_of(frame, col="s"):
            return frame.groupby("batch").apply(lambda x: x[col].corr(x["follow60"], method="spearman")).mean()

        ic = ic_of(d)
        null = []
        for _ in range(10_000):
            d["q"] = d.groupby("batch")["s"].transform(lambda s: rng.permutation(s.to_numpy()))
            null.append(ic_of(d, "q"))
        p = (np.sum(np.array(null) >= ic) + 1) / (len(null) + 1)
        d["pick"] = np.where(d["rank"] <= 8, d["follow60"], d["fade60"])
        daily = d.groupby("date")["pick"].sum()
        top, bot = d[d["rank"] <= 4]["follow60"].mean(), d[d["rank"] >= 13]["follow60"].mean()
        verdict = "PASS" if ic >= 0.10 and p < 0.05 else "KILL" if ic < 0.02 else "INCONCLUSIVE"
        out += [f"{arm}: batches {len(recs)}, flags {len(d)}, non-permutation batches "
                f"{sum(not r['permutation'] for r in recs)}, charts read {sum(r['charts_read'] for r in recs)}",
                f"  IC {ic:+.3f}, within-batch permutation p {p:.4f} -> {verdict}",
                f"  pick net {d['pick'].mean():+.2f} bps (day t {daily.mean() / daily.std() * np.sqrt(len(daily)):+.2f});"
                f" top-4 follow60 {top:+.1f} vs bottom-4 {bot:+.1f}"]
    if "N" in per:
        for arm in ("C", "NC"):
            if arm not in per:
                continue
            m = per["N"][["batch", "id", "follow60", "s"]].merge(per[arm][["id", "s"]], on="id", suffixes=("_n", "_x"))

            def diff(f, a, b):
                return (f.groupby("batch").apply(lambda x: x[b].corr(x["follow60"], method="spearman")).mean()
                        - f.groupby("batch").apply(lambda x: x[a].corr(x["follow60"], method="spearman")).mean())

            obs = diff(m, "s_n", "s_x")
            null = []
            for _ in range(2_000):
                sw = rng.random(len(m)) < 0.5
                f = m.assign(a=np.where(sw, m["s_x"], m["s_n"]), b=np.where(sw, m["s_n"], m["s_x"]))
                null.append(diff(f, "a", "b"))
            p = (np.sum(np.array(null) >= obs) + 1) / (len(null) + 1)
            out.append(f"{arm} minus N: IC diff {obs:+.3f}, label-swap p {p:.4f}")
    text = "\n".join(out)
    print(text)
    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write(text + "\n")


if __name__ == "__main__":
    {"render": render, "ask": lambda: ask(sys.argv[2]), "score": score}[sys.argv[1]]()
