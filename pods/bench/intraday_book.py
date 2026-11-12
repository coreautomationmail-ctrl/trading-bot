"""Intraday book, virtual: Claude's check-in trades (research/theses/intraday.md), sealed the moment
they reach GitHub and scored after the close against SPY and random picks over the same minutes.

At each check-in (10:30, 12:00, 13:30 ET) a PC session commits research/theses/<date>-<HHMM>.json:
  {"theses": [{"id": "<date>-<HHMM>-1", "symbol": "AMD", "direction": "long", "exit_by": "15:55",
               "stop_pct": 1.5, "target_pct": 3.0, "confidence": 0.55, "why": ..., "wrong_if": ...}],
   "close": ["<earlier intraday id>"]}
  python intraday_book.py seal    every 2 min through the session: a check-in's file is copied into
                                  data/theses/intraday/ the first time it is seen (up to 90 min after
                                  the check-in). sealed_at is that moment, so nothing is scored on
                                  prices from before the file existed.
  python intraday_book.py seal_flash   every minute: flash-desk files (<date>-F<HHMM>-<SYM>.json, written
                                  after a screener flag, research/theses/flash.md), sealed when first seen
                                  in a git fetch; at most MAX_FLASH trades each
  python intraday_book.py score   after the close: data/theses_intraday_scores.csv and
                                  data/theses_intraday_summary.json
  python intraday_book.py score_flags   after score: every screener flag, passed or not, as if
                                  followed and as if faded (entry FLAG_LAG_MIN after the flag, the
                                  time a session's file takes to be sealed; out at +30 and +60 min, net
                                  of costs, minus SPY), joined with Vision's ledger (Haiku's call; the
                                  session's status, minutes and API-rate cost estimate) and Claude's
                                  own scored result: data/flash_ledger.csv, data/flash_ledger_summary.json

A trade enters at the open of the first SIP 1-minute bar starting at or after sealed_at (rounded up to
the minute). It exits at the first of: its stop (fill at the stop, or at a bar's open past it), its
target (same), a later check-in's close (open of the first bar after that file was sealed), exit_by
(open of that minute's bar). A bar touching both stop and target counts as the stop. COST_BPS a side.
Baselines over the same minutes, same direction, no stop: SPY, and the mean of RANDOM_DRAWS random
names from the 277 (seeded by the trade id). Exit test as the morning book: 300 trades, beat both with
day t >= 2. Paper data only: no orders, no account endpoints.
"""
import csv
import json
import os
import random
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import requests

import spike_pod
import thesis
from spike_pod import ET

CHECKINS = ["1030", "1200", "1330"]
SEAL_WINDOW_MIN = 90
MAX_NEW, MAX_FLASH = 3, 2
REPO_URL = "https://github.com/coreautomationmail-ctrl/trading-bot.git"
REPO_DIR = os.path.join(thesis.STATE_DIR, "repo")
DIR = os.path.join(thesis.STATE_DIR, "theses", "intraday")
SCORES_CSV = os.path.join(thesis.STATE_DIR, "theses_intraday_scores.csv")
SUMMARY_JSON = os.path.join(thesis.STATE_DIR, "theses_intraday_summary.json")
FLAGS_DIR = os.getenv("FLAGS_DIR", "/spike/world/flags")
LEDGER_DIR = os.getenv("LEDGER_DIR", "/vision")
FLAG_CSV = os.path.join(thesis.STATE_DIR, "flash_ledger.csv")
FLAG_SUMMARY = os.path.join(thesis.STATE_DIR, "flash_ledger_summary.json")
FLAG_LAG_MIN, FLAG_HOLDS = 4, (30, 60)
FLAG_FIELDS = ["date", "id", "symbol", "flagged_at", "move_pct", "volume_x", "headline", "triage", "triage_why",
               "session", "minutes", "cost_usd_est", "claude_trades", "claude_net_bps", "claude_vs_random",
               "follow30", "fade30", "follow60", "fade60"]
FIELDS = ["date", "id", "symbol", "direction", "confidence", "entry_at", "exit_at", "exit_why", "net_bps",
          "spy_bps", "random_bps", "vs_spy", "vs_random", "why", "version", "net_5bps"]


def minutes(hhmm: str) -> int:
    return int(hhmm[:2]) * 60 + int(hhmm[-2:])


def start_min(code: str) -> int:
    """A file's start: a check-in "1030", or a flash flag "F1032-AMD" (research/theses/flash.md)."""
    return minutes(code[1:5] if code.startswith("F") else code)


def valid(t: dict, date: str, checkin: str) -> str:
    """'' if the trade can be scored, else why not."""
    if t.get("symbol") not in thesis.UNIVERSE:
        return f"{t.get('symbol')} isn't in the 277-name universe"
    if t.get("direction") not in ("long", "short"):
        return "direction must be long or short"
    if not str(t.get("id", "")).startswith(f"{date}-{checkin}"):
        return "id must start with the date and check-in"
    stop, target = t.get("stop_pct"), t.get("target_pct")
    if not (isinstance(stop, (int, float)) and 0.3 <= stop <= 10):
        return "stop_pct is required, 0.3-10"
    if target is not None and not (isinstance(target, (int, float)) and stop <= target <= 20):
        return "target_pct must be null or between stop_pct and 20"
    by = str(t.get("exit_by", ""))
    if len(by) != 5 or by[2] != ":" or not by.replace(":", "").isdigit() or \
            not start_min(checkin) + 20 <= minutes(by) <= 15 * 60 + 55:
        return "exit_by must be HH:MM, 20+ minutes after the check-in or flag, no later than 15:55"
    return ""


def seal(now: datetime) -> str:
    et = now.astimezone(ET)
    date, m = et.date().isoformat(), et.hour * 60 + et.minute
    if et.weekday() > 4:
        return "weekend"
    os.makedirs(DIR, exist_ok=True)
    done = []
    for c in CHECKINS:
        path = os.path.join(DIR, f"{date}-{c}.json")
        if os.path.exists(path) or not minutes(c) - 10 <= m <= minutes(c) + SEAL_WINDOW_MIN:
            continue
        if m > minutes(c) + 30 and m % 10 >= 2:
            continue  # every 2 minutes for the first half hour, then every 10: GitHub allows 60 unsigned calls an hour
        r = requests.get(thesis.REPO_FILE.format(date=f"{date}-{c}"),
                         headers={"Accept": "application/vnd.github.raw+json"}, timeout=20)
        if r.status_code == 404:
            continue
        r.raise_for_status()
        done.append(_seal_one(json.loads(r.text), path, date, c, now, MAX_NEW))
    return "; ".join(done)


def _seal_one(body: dict, path: str, date: str, code: str, now: datetime, max_new: int) -> str:
    theses = (body.get("theses") or [])[:max_new]
    earlier = {t["id"] for f in sorted(os.listdir(DIR)) if f.startswith(date) and f.endswith(".json")
               for t in json.load(open(os.path.join(DIR, f)))["theses"]}   # everything sealed before now
    with open(path, "w") as f:
        json.dump({"sealed_at": now.isoformat(), "checkin": code, "read": str(body.get("read", ""))[:300],
                   "prompt_version": int(body.get("prompt_version") or 1),
                   "theses": [t for t in theses if not valid(t, date, code)],
                   "close": [i for i in body.get("close") or [] if i in earlier],
                   "rejected": [{"id": t.get("id"), "why": valid(t, date, code)} for t in theses if valid(t, date, code)]},
                  f, indent=1)
    return f"sealed {code}: {sum(not valid(t, date, code) for t in theses)} of {len(theses)} trades"


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", REPO_DIR, *args], capture_output=True, text=True, check=True, timeout=60).stdout


def seal_flash(now: datetime) -> str:
    """Flash files (research/theses/<date>-F<HHMM>-<SYM>.json) are sealed the minute they're seen. Their
    times are unknown in advance, so instead of the contents API (60 unsigned calls an hour) a sparse
    shallow clone is fetched: ls-remote first, a fetch only when master moved."""
    et = now.astimezone(ET)
    date = et.date().isoformat()
    if et.weekday() > 4 or not 9 * 60 + 35 <= et.hour * 60 + et.minute <= 15 * 60 + 55:
        return ""
    os.makedirs(DIR, exist_ok=True)
    if not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        subprocess.run(["git", "clone", "--quiet", "--depth", "1", "--filter=blob:none", "--sparse", REPO_URL, REPO_DIR],
                       check=True, timeout=120)
        git("sparse-checkout", "set", "research/theses")
    head = subprocess.run(["git", "ls-remote", REPO_URL, "refs/heads/master"], capture_output=True, text=True,
                          check=True, timeout=30).stdout.split()[0]
    if head != git("rev-parse", "HEAD").strip():
        git("fetch", "--quiet", "--depth", "1", "origin", "master")
        git("reset", "--quiet", "--hard", "FETCH_HEAD")
    done = []
    theses_dir = os.path.join(REPO_DIR, "research", "theses")
    for name in sorted(os.listdir(theses_dir)):
        if not (name.startswith(f"{date}-F") and name.endswith(".json")) or os.path.exists(os.path.join(DIR, name)):
            continue
        code = name[len(date) + 1:-len(".json")]
        try:
            body = json.load(open(os.path.join(theses_dir, name), encoding="utf-8"))
        except ValueError:
            body = {"read": "unparseable file", "theses": []}
        done.append(_seal_one(body, os.path.join(DIR, name), date, code, now, MAX_FLASH))
    return "; ".join(done)


def utc_z(t: datetime) -> str:
    """A time as the API's UTC "...Z" string. Formatting a New York time with a literal Z (the old code)
    sent 16:45 ET as 16:45 UTC = 11:45 ET, so every score after 11:45 ET was cut short or missing."""
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def minute_bars(symbols: list, day: str, end: str) -> dict:
    """SIP 1-minute bars for one session: {sym: [bar, ...]} in time order."""
    out = {}
    for i in range(0, len(symbols), 100):
        token = None
        while True:
            params = {"symbols": ",".join(symbols[i:i + 100]), "timeframe": "1Min", "feed": "sip",
                      "adjustment": "split", "start": f"{day}T13:00:00Z", "end": end, "limit": 10000}
            if token:
                params["page_token"] = token
            r = requests.get(spike_pod.DATA_URL, headers=spike_pod._headers(), params=params, timeout=30)
            r.raise_for_status()
            body = r.json()
            for sym, bars in (body.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            token = body.get("next_page_token")
            if not token:
                break
    return out


def _et(bar: dict) -> datetime:
    return datetime.fromisoformat(bar["t"].replace("Z", "+00:00")).astimezone(ET)


def first_at(bars: list, at: datetime) -> int | None:
    return next((i for i, b in enumerate(bars) if _et(b) >= at), None)


def trade(bars: list, sign: int, entry_at: datetime, exit_at: datetime, stop_pct=None, target_pct=None):
    """(net bps, exit time, why) over one session's bars, or None without an entry bar."""
    i = first_at(bars, entry_at)
    if i is None or _et(bars[i]) >= exit_at:
        return None
    entry = bars[i]["o"]
    stop = entry * (1 - sign * stop_pct / 100) if stop_pct else None
    target = entry * (1 + sign * target_pct / 100) if target_pct else None
    px, when, why = None, None, "exit_by"
    for b in bars[i:]:
        t = _et(b)
        if t >= exit_at:
            px, when = b["o"], t
            break
        if stop is not None:
            if b is not bars[i] and (b["o"] - stop) * sign <= 0:
                px, when, why = b["o"], t, "stop"
                break
            if (b["l"] if sign > 0 else b["h"]) * sign <= stop * sign:
                px, when, why = stop, t, "stop"
                break
        if target is not None:
            if b is not bars[i] and (b["o"] - target) * sign >= 0:
                px, when, why = b["o"], t, "target"
                break
            if (b["h"] if sign > 0 else b["l"]) * sign >= target * sign:
                px, when, why = target, t, "target"
                break
    if px is None:
        px, when = bars[-1]["c"], _et(bars[-1])
    return sign * (px / entry - 1) * 1e4 - 2 * thesis.COST_BPS - (thesis.STOP_SLIP_BPS if why == "stop" else 0), when, why


def score_day(date: str, files: list, bars: dict) -> list:
    closes = {}
    for s in files:
        for i in s["close"]:
            closes.setdefault(i, datetime.fromisoformat(s["sealed_at"]).astimezone(ET))
    rows = []
    for s in files:
        sealed = datetime.fromisoformat(s["sealed_at"]).astimezone(ET)
        entry_at = (sealed + timedelta(seconds=59)).replace(second=0, microsecond=0)
        for t in s["theses"]:
            sign = 1 if t["direction"] == "long" else -1
            planned = datetime.fromisoformat(f"{date}T{t['exit_by']}:00").replace(tzinfo=ET)
            by = planned
            if t["id"] in closes and closes[t["id"]] > sealed:
                by = min(by, (closes[t["id"]] + timedelta(seconds=59)).replace(second=0, microsecond=0))
            own = trade(bars.get(t["symbol"], []), sign, entry_at, by, t["stop_pct"], t.get("target_pct"))
            if own is None:
                continue  # no bar to enter on: unscored
            net, out_at, why = own
            if why == "exit_by" and by < planned:
                why = "closed"
            base = lambda sym: trade(bars.get(sym, []), sign, entry_at, out_at + timedelta(seconds=1))  # noqa: E731
            spy = base("SPY")
            rng = random.Random(t["id"])
            draws = [d[0] for d in (base(x) for x in rng.sample([u for u in thesis.UNIVERSE if u != t["symbol"]],
                                                                 thesis.RANDOM_DRAWS)) if d]
            if spy is None or not draws:
                continue
            rand = sum(draws) / len(draws)
            rows.append({"date": date, "id": t["id"], "symbol": t["symbol"], "direction": t["direction"],
                         "confidence": t.get("confidence", ""), "entry_at": entry_at.strftime("%H:%M"),
                         "exit_at": out_at.strftime("%H:%M"), "exit_why": why, "net_bps": round(net, 1),
                         "spy_bps": round(spy[0], 1), "random_bps": round(rand, 1), "vs_spy": round(net - spy[0], 1),
                         "vs_random": round(net - rand, 1), "why": str(t.get("why", ""))[:200],
                         "version": s.get("prompt_version", 1),
                         "net_5bps": round(net - 2 * (thesis.HIGH_COST_BPS - thesis.COST_BPS), 1)})
    return rows


def score(now: datetime) -> str:
    os.makedirs(DIR, exist_ok=True)
    today = now.astimezone(ET).date().isoformat()
    after_close = now.astimezone(ET).hour >= 17
    days = sorted({f[:10] for f in os.listdir(DIR) if f.endswith(".json")})
    done = {r["date"] for r in csv.DictReader(open(SCORES_CSV))} if os.path.exists(SCORES_CSV) else set()
    new = []
    for d in days:
        if d in done or (d == today and not after_close) or d > today:
            continue
        files = [json.load(open(os.path.join(DIR, f))) for f in sorted(os.listdir(DIR)) if f.startswith(d)]
        if not any(f["theses"] for f in files):
            continue
        end = min(now - timedelta(minutes=20), datetime.fromisoformat(f"{d}T23:00:00+00:00"))
        bars = minute_bars(sorted(set(thesis.UNIVERSE) | {"SPY"}), d, utc_z(end))
        new += score_day(d, files, bars)
    thesis.write_rows(SCORES_CSV, FIELDS, new)
    rows = [{**r, "stopped": int(r["exit_why"] == "stop")}
            for r in (list(csv.DictReader(open(SCORES_CSV))) if os.path.exists(SCORES_CSV) else [])]
    flash = lambda i: "-F" in i  # noqa: E731  flash-desk trade ids carry the flag code
    sealed_files = sorted(f for f in os.listdir(DIR) if f.endswith(".json"))
    checkins = [len(json.load(open(os.path.join(DIR, f)))["theses"]) for f in sealed_files if not flash(f)]
    flash_days: dict[str, int] = {}
    for f in sealed_files:
        if flash(f):
            flash_days[f[:10]] = flash_days.get(f[:10], 0) + len(json.load(open(os.path.join(DIR, f)))["theses"])
    # The flash desk's sessions are trading days since it started (a quiet day with no flag counts).
    flash_sessions = [flash_days.get(d, 0) for d in sorted({f[:10] for f in sealed_files}) if d >= "2026-11-11"]
    summary = {**thesis.summarize([r for r in rows if not flash(r["id"])], "intraday", checkins),
               "flash": thesis.summarize([r for r in rows if flash(r["id"])], "flash", flash_sessions)}
    with open(SUMMARY_JSON, "w") as f:
        json.dump({"at": now.isoformat(), **summary}, f, indent=1)
    return f"intraday: {len(new)} new, {len(rows)} scored so far: {json.dumps(summary)}"


def _read_jsonl(path: str) -> list:
    out = []
    for line in open(path, encoding="utf-8"):
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def score_flag_day(date: str, flags: list, ledger: dict, claude: dict, bars: dict) -> list:
    """One row per flag: Vision's ledger entry, Claude's scored trades, and the follow/fade counterfactuals
    (each net of costs and minus SPY over the same minutes, same direction)."""
    rows = []
    for f in flags:
        at = datetime.fromisoformat(f["flagged_at"]).astimezone(ET)
        entry_at = (at + timedelta(minutes=FLAG_LAG_MIN, seconds=59)).replace(second=0, microsecond=0)
        up = 1 if f.get("since_headline_pct", 0) >= 0 else -1
        led = ledger.get(f["id"], {})
        mine = claude.get(f["id"], [])
        row = {"date": date, "id": f["id"], "symbol": f["sym"], "flagged_at": at.strftime("%H:%M"),
               "move_pct": f.get("since_headline_pct"), "volume_x": f.get("volume_x"),
               "headline": str(f.get("headline", ""))[:160],
               "triage": "" if not led else ("look" if led.get("look") else "skip"),
               "triage_why": str(led.get("why") or "")[:120], "session": led.get("status", ""),
               "minutes": round(led["duration_s"] / 60, 1) if led.get("duration_s") else "",
               "cost_usd_est": led["cost_usd_est"] if led.get("cost_usd_est") is not None else "",
               "claude_trades": len(mine),
               "claude_net_bps": round(sum(float(r["net_bps"]) for r in mine), 1) if mine else "",
               "claude_vs_random": round(sum(float(r["vs_random"]) for r in mine), 1) if mine else ""}
        for hold in FLAG_HOLDS:
            exit_at = entry_at + timedelta(minutes=hold)
            for name, sign in (("follow", up), ("fade", -up)):
                own = trade(bars.get(f["sym"], []), sign, entry_at, exit_at)
                spy = trade(bars.get("SPY", []), sign, entry_at, exit_at)
                # net of the trade's costs, minus SPY's gross move (spy[0] is net too: add its costs back)
                row[f"{name}{hold}"] = round(own[0] - spy[0] - 2 * thesis.COST_BPS, 1) if own and spy else ""
        rows.append(row)
    return rows


def _mean(xs: list):
    xs = [float(x) for x in xs if x not in ("", None)]
    return round(sum(xs) / len(xs), 1) if xs else None


def flag_summary(rows: list) -> dict:
    """By Haiku's call: how many, and the mean follow/fade counterfactual. For the sessions: cost and
    Claude's own result per traded flag."""
    out = {}
    for name in ("look", "skip", ""):
        group = [r for r in rows if r["triage"] == name]
        if group:
            out[name or "no_ledger"] = {"n": len(group), **{k: _mean([r[k] for r in group])
                                                             for k in ("follow30", "fade30", "follow60", "fade60")}}
    traded = [r for r in rows if int(r["claude_trades"] or 0)]
    out["claude"] = {"sessions": sum(1 for r in rows if r["session"]),
                     "cost_usd_est": round(sum(float(r["cost_usd_est"]) for r in rows if r["cost_usd_est"] != ""), 2),
                     "flags_traded": len(traded), "vs_random_per_flag": _mean([r["claude_vs_random"] for r in traded])}
    return out


def score_flags(now: datetime) -> str:
    if not os.path.isdir(FLAGS_DIR):
        return f"flags: no {FLAGS_DIR}"
    today = now.astimezone(ET).date().isoformat()
    after_close = now.astimezone(ET).hour >= 17
    done = {r["date"] for r in csv.DictReader(open(FLAG_CSV))} if os.path.exists(FLAG_CSV) else set()
    scored = list(csv.DictReader(open(SCORES_CSV))) if os.path.exists(SCORES_CSV) else []
    new = []
    for name in sorted(os.listdir(FLAGS_DIR)):
        d = name[:10]
        if not name.endswith(".jsonl") or d in done or d > today or (d == today and not after_close):
            continue
        flags = _read_jsonl(os.path.join(FLAGS_DIR, name))
        if not flags:
            continue
        path = os.path.join(LEDGER_DIR, f"{d}.json")
        ledger = {e["id"]: e for e in json.load(open(path))} if os.path.exists(path) else {}
        claude = {}
        for r in scored:
            if r["date"] == d and "-F" in r["id"]:
                claude.setdefault(r["id"].rsplit("-", 1)[0], []).append(r)
        end = min(now - timedelta(minutes=20), datetime.fromisoformat(f"{d}T23:00:00+00:00"))
        bars = minute_bars(sorted({f["sym"] for f in flags} | {"SPY"}), d, utc_z(end))
        new += score_flag_day(d, flags, ledger, claude, bars)
    if new:
        first = not os.path.exists(FLAG_CSV)
        with open(FLAG_CSV, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=FLAG_FIELDS)
            if first:
                w.writeheader()
            w.writerows(new)
    rows = list(csv.DictReader(open(FLAG_CSV))) if os.path.exists(FLAG_CSV) else []
    summary = flag_summary(rows)
    with open(FLAG_SUMMARY, "w") as f:
        json.dump({"at": now.isoformat(), **summary}, f, indent=1)
    return f"flags: {len(new)} new, {len(rows)} so far: {json.dumps(summary)}"


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    now = datetime.now(ET)
    if cmd == "seal":
        if text := seal(now):
            print(f"{now:%Y-%m-%d %H:%M} {text}", flush=True)
    elif cmd == "seal_flash":
        if text := seal_flash(now):
            print(f"{now:%Y-%m-%d %H:%M} {text}", flush=True)
    elif cmd == "score":
        print(score(now), flush=True)
    elif cmd == "score_flags":
        print(score_flags(now), flush=True)
    else:
        sys.exit(__doc__)
