"""
Headline family, scorer S2 (research/journal.md, 2026-11-07 PRE-REGISTRATION headline-family, Claude-label
rule): Haiku labels single-name headlines from 2025-07-01 on as good / bad / neutral (Vision does the
labelling: jarvis ops/stratton_labels.py, site stratton_news). S1 and S2 are then compared on T1 trades from
2026-07-01 on. The full labelling and S2's T1-T3 runs happen only if S2 beats S1 by >= 3 bps per trade.

  export   write .cache/s2/s2_headlines.csv (id,headline; one row per distinct headline text) for Vision
  compare  read .cache/s2/s2_labels.csv (id,label; copied back from Vision) and print the comparison

Usage: python scripts/research/headline_s2.py export|compare
"""
import csv
import glob
import gzip
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import numpy as np
import pandas as pd

import headline_family as hf

OUT = os.path.join(hf.CACHE, "s2")
LABEL_FROM, COMPARE_FROM = "2025-07-01", "2026-07-01"
GAIN = 3.0


def hid(headline: str) -> str:
    return hashlib.sha1(headline.encode("utf-8")).hexdigest()[:16]


def single_name() -> dict:
    """{sym: [(created_at, headline)]} for single-name headlines, as hf.scored_news picks them."""
    import collections
    rows, tags = {}, collections.Counter()
    for path in glob.glob(os.path.join(hf.CACHE, "news", "*.csv.gz")):
        sym = os.path.basename(path)[:-len(".csv.gz")]
        with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
            rows[sym] = [(r["created_at"], r["headline"] or "") for r in csv.DictReader(f)]
        tags.update(set(rows[sym]))
    return {s: [(c, h) for c, h in it if tags[(c, h)] < hf.RECAP_NAMES] for s, it in rows.items()}


def export():
    os.makedirs(OUT, exist_ok=True)
    seen = {}
    for items in single_name().values():
        for c, h in items:
            if c >= LABEL_FROM and h.strip():
                seen.setdefault(hid(h), h)
    with open(os.path.join(OUT, "s2_headlines.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "headline"])
        w.writerows(seen.items())
    print(f"{len(seen)} distinct headlines from {LABEL_FROM} -> {OUT}/s2_headlines.csv")


def scored(items: dict, score) -> dict:
    out = {}
    for sym, its in items.items():
        keep = [(c, score(h)) for c, h in its if c >= LABEL_FROM]
        if not keep:
            out[sym] = (np.zeros(0, "int64"), np.zeros(0))
            continue
        s = pd.Series([v for _, v in keep], index=pd.to_datetime([c for c, _ in keep], utc=True).as_unit("ns").asi8)
        s = s.groupby(level=0).sum()
        out[sym] = (s.index.to_numpy("int64"), s.to_numpy(float))
    return out


def t1(news: dict) -> pd.DataFrame:
    hf.YEARS = range(2026, 2027)
    rows = []
    empty = (np.zeros(0, "int64"), np.zeros(0))
    for sym in hf.names():
        rows += [r for r in hf.run_name((sym, *news.get(sym, empty), None))[1] if r[0] == "T1"]
    t = pd.DataFrame(rows, columns=["test", "sym", "year", "date", "side", "bps", "entry_ns"])
    return t[pd.to_datetime(t["date"]) >= pd.Timestamp(COMPARE_FROM)].reset_index(drop=True)


def stats(t: pd.DataFrame) -> str:
    x = t["bps"] - hf.COST
    day = x.groupby(t["date"]).sum()
    dt = day.mean() / day.std(ddof=1) * np.sqrt(len(day)) if len(day) > 1 else float("nan")
    return f"n={len(x)}, net {x.mean():+.2f} bps/trade, day t {dt:+.2f}, win {(x > 0).mean():.0%}"


def compare():
    lab = pd.read_csv(os.path.join(OUT, "s2_labels.csv"), dtype=str).drop_duplicates("id", keep="last")
    labels = dict(zip(lab["id"], lab["label"]))
    items = single_name()
    want = {hid(h) for its in items.values() for c, h in its if c >= LABEL_FROM and h.strip()}
    print(f"labels {len(labels)} of {len(want)} headlines; G {(lab['label'] == 'G').mean():.0%}, "
          f"B {(lab['label'] == 'B').mean():.0%}, N {(lab['label'] == 'N').mean():.0%}")
    pos, neg = hf.lexicon()

    def s1(h):
        w = hf.TOKEN.findall(h.upper())
        return sum(x in pos for x in w) - sum(x in neg for x in w)

    s2 = lambda h: {"G": 1, "B": -1}.get(labels.get(hid(h)), 0)  # noqa: E731
    a, b = t1(scored(items, s1)), t1(scored(items, s2))
    print(f"T1 from {COMPARE_FROM} (data to {max(a['date'].max(), b['date'].max())}), 2 bps/side:")
    print(f"  S1 word list: {stats(a)}")
    print(f"  S2 Haiku:     {stats(b)}")
    m = a.merge(b, on=["sym", "entry_ns"], suffixes=("_1", "_2"))
    same = m[m["side_1"] == m["side_2"]]
    print(f"  both took {len(m)} trades, same side on {len(same)}; on the {len(m) - len(same)} opposite-side ones "
          f"S2 net {(m.loc[m['side_1'] != m['side_2'], 'bps_2'] - hf.COST).mean():+.2f}")
    gain = (b["bps"] - hf.COST).mean() - (a["bps"] - hf.COST).mean()
    print(f"S2 - S1 = {gain:+.2f} bps/trade (needs >= {GAIN:+.1f}): "
          f"{'QUALIFIES: full labelling and S2 T1-T3 runs next' if gain >= GAIN else 'does not qualify: stop rule applies'}")


if __name__ == "__main__":
    {"export": export, "compare": compare}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
