"""
u-family (research/journal.md, 2026-11-12 PRE-REGISTRATION, run unchanged on the stage-3 outputs): U1-U3 on the
less-watched names ($300M-$5B), 10 bps/side, design 2016-2022, held out 2023-26, combined bar t 2.9.

Inputs (.cache/u): m1/day/<SYM>.csv (o0930, c0944, o_entry 09:50, c1559 per universe day), m1/u3/<SYM>.csv (spike
trades, gross_bps), news/<year>.csv.gz, universe_days.csv.gz. Output: research/u_family.log
"""
import glob
import math
import os

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
U = os.path.join(HERE, "..", ".cache", "u")
LOG = os.path.join(HERE, "..", "..", "research", "u_family.log")
COST, BAR, DESIGN_END = 10.0, 2.9, 2022
ET = "America/New_York"
lines: list = []


def say(s=""):
    print(s, flush=True)
    lines.append(s)


def t(x):
    x = pd.Series(x).dropna()
    return x.mean() / x.std() * math.sqrt(len(x)) if len(x) > 2 and x.std() > 0 else float("nan")


def headline_days(syms: set) -> set:
    """(symbol, session date) with a single-name headline from the prior 16:00 to 09:49:59 ET that day."""
    sess = pd.DatetimeIndex(sorted(pd.read_csv(os.path.join(U, "universe_days.csv.gz"), usecols=["date"])["date"]
                                   .unique()))
    loc = sess.tz_localize(ET)
    cut = loc + pd.Timedelta(hours=9, minutes=49, seconds=59)
    prev_close = pd.DatetimeIndex([loc[0] - pd.Timedelta(hours=8)]).append(loc[:-1] + pd.Timedelta(hours=16))
    out = set()
    for p in sorted(glob.glob(os.path.join(U, "news", "*.csv.gz"))):
        n = pd.read_csv(p)
        n["syms"] = n["symbols"].fillna("").str.split()
        n = n[n["syms"].str.len().between(1, 3)]
        ts = pd.to_datetime(n["created_at"], utc=True).dt.tz_convert(ET)
        j = cut.searchsorted(ts)                     # first session whose 09:49:59 is at or after ts
        ok = (j < len(sess))
        j_ok = np.clip(j, 0, len(sess) - 1)
        ok &= ts.to_numpy() >= prev_close[j_ok].to_numpy()
        for k, ss in zip(np.where(ok)[0], n["syms"].to_numpy()[ok]):
            d = sess[j_ok[k]]
            for s in ss:
                if s in syms:
                    out.add((s, d))
    return out


def gate(name: str, tr: pd.DataFrame) -> bool:
    """tr: date, sym, net (bps). Scored on design years; held out must match sign with day t >= 2."""
    tr = tr.dropna(subset=["net"])
    des, held = tr[tr["date"].dt.year <= DESIGN_END], tr[tr["date"].dt.year > DESIGN_END]
    day = des.groupby("date")["net"].sum()
    yrs = des.groupby(des["date"].dt.year)["net"].mean()
    a, b = des[des["date"].dt.year <= 2020]["net"].mean(), des[des["date"].dt.year >= 2021]["net"].mean()
    pos = des.groupby("sym")["net"].sum()
    top_share = pos.max() / pos[pos > 0].sum() if (pos > 0).any() else float("nan")
    hday = held.groupby("date")["net"].sum()
    s = des["net"]
    checks = {
        "n >= 300": len(s) >= 300, "mean net >= +3": s.mean() >= 3, f"trade t >= {BAR}": t(s) >= BAR,
        f"day t >= {BAR}": t(day) >= BAR, ">= 70% years +": (yrs > 0).mean() >= 0.7,
        "same sign 16-20 / 21-22": np.sign(a) == np.sign(b), "no symbol > 25%": top_share <= 0.25,
    }
    passed = all(checks.values())
    say(f"{name}: design n {len(s)}, net {s.mean():+.2f} bps (t {t(s):+.2f}, day t {t(day):+.2f}), win "
        f"{(s > 0).mean():.0%}, median {s.median():+.1f}, years + {int((yrs > 0).sum())}/{len(yrs)}, 2016-20 {a:+.1f} "
        f"/ 2021-22 {b:+.1f}, top symbol {top_share:.0%}, top-1% share "
        f"{s.nlargest(max(1, len(s) // 100)).sum() / s.sum() if s.sum() > 0 else float('nan'):.0%}")
    say(f"  held out 2023-26: n {len(held)}, net {held['net'].mean():+.2f} (day t {t(hday):+.2f})")
    say("  gate: " + "; ".join(f"{k} {'ok' if v else 'FAIL'}" for k, v in checks.items())
        + f" -> {'PASS' if passed else 'FAIL'}")
    if passed:
        ok = np.sign(held["net"].mean()) == np.sign(s.mean()) and t(hday) >= 2
        say(f"  held-out check: {'PASS' if ok else 'FAIL'}")
        passed = ok
    return passed


def main():
    days = []
    for p in glob.glob(os.path.join(U, "m1", "day", "*.csv")):
        d = pd.read_csv(p)
        d["sym"] = os.path.basename(p)[:-4]
        days.append(d)
    day = pd.concat(days, ignore_index=True)
    day["date"] = pd.to_datetime(day["date"])
    day = day.sort_values(["sym", "date"])
    news = headline_days(set(day["sym"]))
    day["news"] = [(s, d) in news for s, d in zip(day["sym"], day["date"])]
    day["dir"] = np.sign(day["c0944"] - day["o0930"])
    sig = day[day["news"] & (day["dir"] != 0)].copy()
    say(f"universe days {len(day)}, names {day['sym'].nunique()}, news days {int(day['news'].sum())}, "
        f"signals {len(sig)}")
    # U1: enter 09:50 open, exit 15:59 close
    sig["net"] = sig["dir"] * (sig["c1559"] / sig["o_entry"] - 1) * 1e4 - 2 * COST
    r1 = gate("U1 (news + first-15-min direction, 09:50 -> close)", sig[["date", "sym", "net"]])
    # U2: same signal, next session's open -> close (the next row must be the next session)
    nxt = day.groupby("sym")[["date", "o0930", "c1559"]].shift(-1)
    sess = pd.DatetimeIndex(sorted(day["date"].unique()))
    pos = sess.get_indexer(day["date"])
    nxt_ok = nxt["date"].to_numpy() == sess[np.clip(pos + 1, 0, len(sess) - 1)].to_numpy()
    day["n_o"], day["n_c"] = np.where(nxt_ok, nxt["o0930"], np.nan), np.where(nxt_ok, nxt["c1559"], np.nan)
    s2 = day.loc[sig.index].copy()
    s2["net"] = s2["dir"] * (s2["n_c"] / s2["n_o"] - 1) * 1e4 - 2 * COST
    say(f"U2 next-session rows missing (not in universe next day or gap): {int(s2['net'].isna().sum())}")
    r2 = gate("U2 (same signal, next session open -> close)", s2[["date", "sym", "net"]])
    # U3: spike rule trades
    u3 = []
    for p in glob.glob(os.path.join(U, "m1", "u3", "*.csv")):
        d = pd.read_csv(p)
        d["sym"] = os.path.basename(p)[:-4]
        u3.append(d)
    u3 = pd.concat(u3, ignore_index=True)
    u3["date"] = pd.to_datetime(u3["date"])
    u3["net"] = u3["gross_bps"] - 2 * COST
    say(f"U3 gross mean {u3['gross_bps'].mean():+.2f} bps over {len(u3)} trades")
    r3 = gate("U3 (spike rule, followed, 60 min)", u3[["date", "sym", "net"]])
    say(f"u-family verdict: U1 {'PASS' if r1 else 'FAIL'}, U2 {'PASS' if r2 else 'FAIL'}, U3 {'PASS' if r3 else 'FAIL'}")
    with open(LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
