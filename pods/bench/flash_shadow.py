"""Shadow flags for the flash triage test (revamp review G6.2; research/journal.md 2026-11-13, T1/T2).

The screener writes looser shadow flags to /spike/world/flags/shadow/<date>.jsonl (pods/spike/flash_screener.py);
after the close Vision labels a random sample with both triage prompts (jarvis ops/stratton_shadow.py) into
/vision/shadow/<date>.json. This scores each labelled flag over the same minutes as the live flags (entry
FLAG_LAG_MIN after the flag, 60 minutes): its absolute move against SPY (T1, T2) and the follow/fade
counterfactuals net of costs (T3), into data/flash_shadow.csv and data/flash_shadow_summary.json.

Usage: python flash_shadow.py   (after intraday_book.py score_flags). Paper data only: no orders.
"""
import csv
import json
import os
import sys
from datetime import datetime, timedelta

import thesis
from intraday_book import FLAG_LAG_MIN, FLAGS_DIR, LEDGER_DIR, _mean, _read_jsonl, minute_bars, trade, utc_z
from spike_pod import ET

SHADOW_FLAGS = os.path.join(FLAGS_DIR, "shadow")
SHADOW_LABELS = os.path.join(LEDGER_DIR, "shadow")
SHADOW_CSV = os.path.join(thesis.STATE_DIR, "flash_shadow.csv")
SHADOW_SUMMARY = os.path.join(thesis.STATE_DIR, "flash_shadow_summary.json")
SHADOW_FIELDS = ["date", "id", "symbol", "flagged_at", "move_pct", "volume_x", "v1", "v2", "category", "size",
                 "abs60", "follow60", "fade60"]
T1_N, T1_T = 1500, 2.5


def score_shadow_day(date: str, flags: list, labels: dict, bars: dict) -> list:
    """One row per labelled shadow flag: both prompts' calls, and the 60-minute move against SPY from the
    same entry as the live flags (FLAG_LAG_MIN after the flag): absolute (T1, T2), followed and faded (T3)."""
    rows = []
    for f in flags:
        lab = labels.get(f["id"])
        if not lab:
            continue
        at = datetime.fromisoformat(f["flagged_at"]).astimezone(ET)
        entry_at = (at + timedelta(minutes=FLAG_LAG_MIN, seconds=59)).replace(second=0, microsecond=0)
        exit_at = entry_at + timedelta(minutes=60)
        own = trade(bars.get(f["sym"], []), 1, entry_at, exit_at)
        spy = trade(bars.get("SPY", []), 1, entry_at, exit_at)
        rel = own[0] - spy[0] if own and spy else None            # both net of the same costs: gross difference
        up = 1 if f.get("since_headline_pct", 0) >= 0 else -1
        rows.append({"date": date, "id": f["id"], "symbol": f["sym"], "flagged_at": at.strftime("%H:%M"),
                     "move_pct": f.get("since_headline_pct"), "volume_x": f.get("volume_x"),
                     "v1": lab.get("v1", ""), "v2": lab.get("v2", ""), "category": lab.get("category", ""),
                     "size": lab.get("size", ""),
                     "abs60": "" if rel is None else round(abs(rel), 1),
                     "follow60": "" if rel is None else round(up * rel - 2 * thesis.COST_BPS, 1),
                     "fade60": "" if rel is None else round(-up * rel - 2 * thesis.COST_BPS, 1)})
    return rows


def welch(a: list, b: list):
    """(mean a - mean b, Welch t), or (None, None) under 2 in either group."""
    if len(a) < 2 or len(b) < 2:
        return None, None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = (va / len(a) + vb / len(b)) ** 0.5
    return round(ma - mb, 1), (round((ma - mb) / se, 2) if se else None)


def shadow_summary(rows: list) -> dict:
    """T1: on the live prompt's (v2) labels, LOOK minus SKIP absolute 60-minute move vs SPY, Welch t >= 2.5 at
    1,500 scored flags. T2: the prompt with the larger gap is kept (v1 = the 11/11 text)."""
    scored = [r for r in rows if r["abs60"] not in ("", None)]
    out = {"labelled": len(rows), "scored": len(scored)}
    for v in ("v1", "v2"):
        look = [float(r["abs60"]) for r in scored if r[v] == "look"]
        skip = [float(r["abs60"]) for r in scored if r[v] == "skip"]
        gap, t = welch(look, skip)
        out[v] = {"look": len(look), "skip": len(skip), "look_abs60": _mean(look), "skip_abs60": _mean(skip),
                  "gap": gap, "t": t}
    if len(scored) >= T1_N:
        t = out["v2"]["t"]
        out["T1"] = "pass" if t is not None and t >= T1_T else "fail"
        g1, g2 = out["v1"]["gap"], out["v2"]["gap"]
        out["T2"] = "keep v2" if g1 is None or (g2 is not None and g2 > g1) else "return to v1"
    else:
        out["T1"] = out["T2"] = f"running ({len(scored)} of {T1_N})"
    cats: dict[str, list] = {}
    for r in scored:
        if r["category"]:
            cats.setdefault(r["category"], []).append(r)
    out["by_category"] = {c: {"n": len(rs), "follow60": _mean([r["follow60"] for r in rs]),
                              "fade60": _mean([r["fade60"] for r in rs])} for c, rs in sorted(cats.items())}
    return out


def score_shadow(now: datetime) -> str:
    if not os.path.isdir(SHADOW_FLAGS):
        return f"shadow: no {SHADOW_FLAGS}"
    done = {r["date"] for r in csv.DictReader(open(SHADOW_CSV))} if os.path.exists(SHADOW_CSV) else set()
    new = []
    for name in sorted(os.listdir(SHADOW_FLAGS)):
        d = name[:10]
        path = os.path.join(SHADOW_LABELS, f"{d}.json")
        if not name.endswith(".jsonl") or name.endswith(".state.jsonl") or d in done or not os.path.exists(path):
            continue
        body = json.load(open(path))
        if not body.get("complete"):          # Vision is still labelling this day
            continue
        labels = {e["id"]: e for e in body.get("labels", [])}
        flags = [f for f in _read_jsonl(os.path.join(SHADOW_FLAGS, name)) if f.get("id") in labels]
        if not flags:
            continue
        end = min(now - timedelta(minutes=20), datetime.fromisoformat(f"{d}T23:00:00+00:00"))
        bars = minute_bars(sorted({f["sym"] for f in flags} | {"SPY"}), d, utc_z(end))
        new += score_shadow_day(d, flags, labels, bars)
    thesis.write_rows(SHADOW_CSV, SHADOW_FIELDS, new)
    rows = list(csv.DictReader(open(SHADOW_CSV))) if os.path.exists(SHADOW_CSV) else []
    summary = shadow_summary(rows)
    with open(SHADOW_SUMMARY, "w") as f:
        json.dump({"at": now.isoformat(), **summary}, f, indent=1)
    return f"shadow: {len(new)} new, {len(rows)} so far: T1 {summary['T1']}, T2 {summary['T2']}"


if __name__ == "__main__":
    print(score_shadow(datetime.now(ET)), flush=True)
    sys.exit(0)
