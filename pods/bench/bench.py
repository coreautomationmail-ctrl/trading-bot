"""STRATTON shadow bench: many strategies on the live IEX feed, side by side (paper).

Phase 3 of docs/stratton-revamp-plan.md. Every strategy in strategies.ALL sees each
completed 1-min bar of its universe. A signal becomes a virtual trade scored exactly
as the backtests and the spike shadow log score it (enter the open of the bar two
minutes after the signal bar, exit the close HOLD_MIN + 1 minutes after it, minus
COST_BPS a side), appended to STATE_DIR/bench_virtual.csv. Virtual trades are a pure
function of the bars, so a restart replays the day and loses nothing.

Strategies the ladder (ladder.py) has promoted to incubation or pod also place paper
orders, in the incubation paper account (INCUB_ALPACA_* keys; the main account, idle
since ORB paused), only on signals fresh enough to act on. Sizes and limits below are
fixed and no promotion changes them. A file STATE_DIR/KILL stops all new orders.

The ladder runs Saturdays after 16:00 UTC and reports to Telegram.

Usage: python bench.py                 run live
       python bench.py replay DIR OUT  replay archived IEX days (DIR/<date>.csv.gz) to OUT
"""
import csv
import gzip
import json
import math
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import requests

import ladder
import spike_pod
import strategies
from spike_pod import ET, PAPER_URL, fetch_bars, send_telegram

STATE_DIR = spike_pod.STATE_DIR
STATE_FILE = os.path.join(STATE_DIR, "bench_state.json")
VIRTUAL_CSV = os.path.join(STATE_DIR, "bench_virtual.csv")
PAPER_CSV = os.path.join(STATE_DIR, "bench_paper.csv")
LADDER_FILE = os.path.join(STATE_DIR, "ladder.json")
KILL_FILE = os.path.join(STATE_DIR, "KILL")
VIRTUAL_FIELDS = ["strategy", "date", "et", "symbol", "side", "signal_bar", "entry_px", "exit_px",
                  "gross_bps", "net_bps", "tags"]
PAPER_FIELDS = ["strategy", "date", "symbol", "side", "qty", "entry_time", "entry_px", "exit_time",
                "exit_px", "pnl", "net_bps", "signal_px", "slip_bps"]
SIZE_USD = {"incubation": 1500.0, "pod": 5000.0}
MAX_OPEN_PER_STRATEGY, MAX_OPEN_TOTAL = 2, 6
DAILY_LOSS_STOP_USD = 300.0
FLATTEN = 15 * 60 + 55
NEWS_URL = spike_pod.DATA_URL.rsplit("/v2/", 1)[0] + "/v1beta1/news"
SINGLE_NAME_MAX = 4      # a headline tagged to 5+ names is a recap (scripts/research/spike_news.py)


def utc(t: str) -> datetime:
    return datetime.fromisoformat(t.replace("Z", "+00:00"))


def key(t: datetime) -> str:
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:00Z")


class Ctx:
    """What a strategy may look up besides bars. Live: Alpaca news, cached per symbol-hour."""

    def __init__(self, news_source=None):
        self.news_source = news_source or self._fetch_news
        self.cache = {}

    @staticmethod
    def et(t: str) -> datetime:
        return utc(t).astimezone(ET)

    def _fetch_news(self, sym: str, start: datetime, end: datetime) -> list:
        r = requests.get(NEWS_URL, headers=spike_pod._headers(), timeout=20,
                         params={"symbols": sym, "start": start.isoformat(), "end": end.isoformat(),
                                 "limit": 50, "sort": "desc"})
        r.raise_for_status()
        return [utc(n["created_at"]) for n in r.json().get("news") or []
                if len(n.get("symbols") or []) <= SINGLE_NAME_MAX]

    def has_news(self, sym: str, t: str, minutes: int) -> bool:
        """A single-name headline created in the `minutes` before bar `t` opened."""
        end = utc(t)
        k = (sym, t, minutes)
        if k not in self.cache:
            self.cache[k] = any(end - timedelta(minutes=minutes) <= c < end
                                for c in self.news_source(sym, end - timedelta(minutes=minutes), end))
        return self.cache[k]


class Engine:
    def __init__(self, strats: list, ctx: Ctx, state: dict):
        self.strats, self.ctx, self.state = strats, ctx, state
        self.bars, self.by_t, self.day = {}, {}, None

    def new_day(self, day: str):
        self.day, self.bars, self.by_t = day, {}, {}
        self.ctx.cache = {}
        for s in self.strats:
            s.new_day()
        if self.state.get("day") != day:
            self.state.update(day=day, seen=[], pending=[], realized_today=0.0)

    def feed(self, sym: str, b: dict) -> list:
        """One completed bar; returns the new signals as dicts."""
        if b["t"] in self.by_t.setdefault(sym, {}):
            return []
        self.by_t[sym][b["t"]] = b
        bars = self.bars.setdefault(sym, [])
        bars.append(b)
        et = self.ctx.et(b["t"])
        hm = et.hour * 60 + et.minute
        out = []
        for s in self.strats:
            if sym not in s.UNIVERSE:
                continue
            side = s.on_bar(sym, bars, hm, self.ctx)
            if not side or not (s.FIRST <= hm <= s.LAST):
                continue
            k = f"{s.NAME}|{sym}|{b['t']}"
            if k in self.state["seen"]:
                continue
            self.state["seen"].append(k)
            sig = {"strategy": s.NAME, "symbol": sym, "side": side, "signal_bar": b["t"],
                   "signal_px": b["c"], "et": et.strftime("%H:%M"), "hold": s.HOLD_MIN,
                   "cost": s.COST_BPS, "tags": json.dumps(s.tags(sym, bars, self.ctx))}
            self.state["pending"].append(sig)
            out.append(sig)
        return out

    def _bar_at(self, sym: str, t0: datetime, minutes: int):
        for extra in range(4):
            b = self.by_t.get(sym, {}).get(key(t0 + timedelta(minutes=minutes + extra)))
            if b:
                return b
        return None

    def settle(self, final: bool = False) -> list:
        """Score pending signals whose exit bar has printed; `final` drops the rest."""
        done, keep = [], []
        for s in self.state["pending"]:
            t0 = utc(s["signal_bar"])
            entry = self._bar_at(s["symbol"], t0, 2)
            exit_ = entry and self._bar_at(s["symbol"], t0, s["hold"] + 1)
            if entry and exit_:
                gross = s["side"] * math.log(exit_["c"] / entry["o"]) * 1e4
                done.append({"strategy": s["strategy"], "date": s["signal_bar"][:10], "et": s["et"],
                             "symbol": s["symbol"], "side": s["side"], "signal_bar": s["signal_bar"],
                             "entry_px": entry["o"], "exit_px": exit_["c"], "gross_bps": round(gross, 2),
                             "net_bps": round(gross - 2 * s["cost"], 2), "tags": s["tags"]})
            elif not final:
                keep.append(s)
        self.state["pending"] = keep
        return done


def append_csv(path: str, fields: list, rows: list):
    if not rows:
        return
    new = not os.path.exists(path)
    with open(path, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if new:
            w.writeheader()
        w.writerows(rows)


# ---------- paper orders for promoted strategies ----------

class Paper:
    """Market orders in the incubation account, same pattern as the spike pod."""

    def __init__(self):
        self.headers = {"APCA-API-KEY-ID": os.environ.get("INCUB_ALPACA_API_KEY", ""),
                        "APCA-API-SECRET-KEY": os.environ.get("INCUB_ALPACA_SECRET_KEY", "")}

    def api(self, method: str, path: str, **kw):
        r = requests.request(method, f"{PAPER_URL}{path}", headers=self.headers, timeout=15, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {r.status_code} {r.text[:200]}")
        return r.json() if r.text else None

    def order(self, sym: str, qty: int, side: str, cid: str) -> dict:
        return self.api("POST", "/v2/orders", json={"symbol": sym, "qty": str(qty), "side": side,
                                                    "type": "market", "time_in_force": "day",
                                                    "client_order_id": cid})

    def filled(self, order_id: str):
        o = self.api("GET", f"/v2/orders/{order_id}")
        return float(o["filled_avg_price"]) if o.get("status") == "filled" else None


def may_enter(state: dict, stage: str, strategy: str, sym: str, held: set) -> bool:
    if stage not in SIZE_USD or os.path.exists(KILL_FILE):
        return False
    if not os.environ.get("INCUB_ALPACA_API_KEY"):
        return False
    if state.get("realized_today", 0.0) <= -DAILY_LOSS_STOP_USD:
        return False
    open_ = state.setdefault("open", [])
    if len(open_) >= MAX_OPEN_TOTAL or sum(t["strategy"] == strategy for t in open_) >= MAX_OPEN_PER_STRATEGY:
        return False
    return sym not in held and all(t["symbol"] != sym for t in open_)


def paper_enter(paper: Paper, state: dict, lad: dict, sig: dict, now: datetime):
    stage = ladder.stage_of(lad, sig["strategy"])
    if stage not in SIZE_USD or now - utc(sig["signal_bar"]) > timedelta(minutes=2, seconds=30):
        return
    held = {p["symbol"] for p in paper.api("GET", "/v2/positions")}
    if not may_enter(state, stage, sig["strategy"], sig["symbol"], held):
        return
    if sig["side"] < 0:
        a = paper.api("GET", f"/v2/assets/{sig['symbol']}")
        if not (a.get("shortable") and a.get("easy_to_borrow")):
            return
    qty = int(SIZE_USD[stage] // float(sig["signal_px"]))
    if qty < 1:
        return
    cid = f"bn-{sig['strategy'][:12]}-{sig['symbol']}-{now:%Y%m%d-%H%M%S}"
    o = paper.order(sig["symbol"], qty, "buy" if sig["side"] > 0 else "sell", cid)
    state["open"].append({"strategy": sig["strategy"], "symbol": sig["symbol"], "side": sig["side"],
                          "qty": qty, "entry_order": o["id"], "entry_time": now.isoformat(),
                          "signal_px": float(sig["signal_px"]),
                          "exit_due": (now + timedelta(minutes=sig["hold"])).isoformat()})
    send_telegram(f"STRATTON bench: {sig['strategy']} {'BUY' if sig['side'] > 0 else 'SHORT'} "
                  f"{qty} {sig['symbol']} [{stage}, paper]")


def paper_exits(paper: Paper, state: dict, now: datetime, cost: dict):
    hm = now.hour * 60 + now.minute
    for t in list(state.get("open", [])):
        if "exit_order" in t:
            px = paper.filled(t["exit_order"])
            if px is None:
                continue
            entry = t.get("entry_px") or paper.filled(t["entry_order"]) or px
            pnl = t["side"] * (px - entry) * t["qty"]
            gross = t["side"] * math.log(px / entry) * 1e4
            append_csv(PAPER_CSV, PAPER_FIELDS, [{
                **t, "date": t["entry_time"][:10], "entry_px": entry, "exit_px": px,
                "exit_time": now.isoformat(), "pnl": round(pnl, 2),
                "net_bps": round(gross - 2 * cost.get(t["strategy"], 2.0), 2),
                "slip_bps": round(t["side"] * math.log(entry / t["signal_px"]) * 1e4, 2)}])
            state["realized_today"] = state.get("realized_today", 0.0) + pnl
            state["open"].remove(t)
            send_telegram(f"STRATTON bench: closed {t['strategy']} {t['symbol']} {gross:+.1f} bps "
                          f"(${pnl:+.0f}) [paper]")
            continue
        if "entry_px" not in t:
            t["entry_px"] = paper.filled(t["entry_order"])
        if now >= datetime.fromisoformat(t["exit_due"]) or hm >= FLATTEN:
            if t["entry_px"] is None:
                try:
                    paper.api("DELETE", f"/v2/orders/{t['entry_order']}")
                except RuntimeError:
                    pass
                state["open"].remove(t)
                continue
            o = paper.order(t["symbol"], t["qty"], "sell" if t["side"] > 0 else "buy",
                            f"bnx-{t['symbol']}-{now:%Y%m%d-%H%M%S}")
            t["exit_order"] = o["id"]


# ---------- live loop ----------

def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(s: dict):
    with open(STATE_FILE + ".tmp", "w") as f:
        json.dump(s, f, indent=1, default=str)
    os.replace(STATE_FILE + ".tmp", STATE_FILE)


def maybe_review(state: dict, lad: dict, strats: list):
    now = datetime.now(timezone.utc)
    week = now.strftime("%G-W%V")
    if now.weekday() != 5 or now.hour < 16 or state.get("ladder_week") == week:
        return
    lines = ladder.review(lad, strats, VIRTUAL_CSV, PAPER_CSV)
    ladder.save(LADDER_FILE, lad)
    send_telegram("STRATTON ladder, weekly:\n" + ("\n".join(lines) or "no strategies"))
    state["ladder_week"] = week


def main():
    if os.getenv("ALPACA_BASE_URL", "").rstrip("/") != PAPER_URL:
        raise SystemExit("bench is paper-only: ALPACA_BASE_URL must be the paper endpoint")
    os.makedirs(STATE_DIR, exist_ok=True)
    strats = list(strategies.ALL)
    lad = ladder.sync(ladder.load(LADDER_FILE), strats)
    ladder.save(LADDER_FILE, lad)
    strats = [s for s in strats if ladder.stage_of(lad, s.NAME) != "retired"]
    cost = {s.NAME: s.COST_BPS for s in strats}
    universe = sorted({u for s in strats for u in s.UNIVERSE})
    state = load_state()
    state.setdefault("seen", [])
    state.setdefault("pending", [])
    engine, paper, last_err = Engine(strats, Ctx(), state), Paper(), ["", 0.0]
    send_telegram("STRATTON bench up: " + ", ".join(f"{s.NAME} [{ladder.stage_of(lad, s.NAME)}]"
                                                    for s in strats) + " [paper]")
    while True:
        try:
            clock = spike_pod.api("GET", "/v2/clock")
            now = datetime.now(ET)
            if not clock["is_open"]:
                if engine.day:
                    for sym, bars in fetch_bars(now - timedelta(minutes=15), universe).items():
                        for b in bars:
                            engine.feed(sym, b)
                    append_csv(VIRTUAL_CSV, VIRTUAL_FIELDS, engine.settle(final=True))
                    engine.day = None
                maybe_review(state, lad, strats)
            else:
                cutoff = now.replace(second=0, microsecond=0)
                if engine.day != str(now.date()):
                    engine.new_day(str(now.date()))
                    since = now.replace(hour=9, minute=30, second=0, microsecond=0)
                else:
                    since = now - timedelta(minutes=4)
                for sym, bars in fetch_bars(since, universe).items():
                    for b in bars:
                        if utc(b["t"]) >= cutoff:
                            continue
                        for sig in engine.feed(sym, b):
                            try:
                                paper_enter(paper, state, lad, sig, now)
                            except Exception as e:
                                send_telegram(f"STRATTON bench order error: {str(e)[:200]}")
                append_csv(VIRTUAL_CSV, VIRTUAL_FIELDS, engine.settle())
                paper_exits(paper, state, now, cost)
            save_state(state)
        except Exception as e:
            msg = str(e)[:300]
            if msg != last_err[0] or time.time() - last_err[1] > 1800:
                send_telegram(f"STRATTON bench error: {msg}")
                last_err[:] = [msg, time.time()]
            else:
                print(f"error (repeat): {msg}", flush=True)
        time.sleep(60 - time.time() % 60 + 6)


# ---------- replay ----------

def replay(day_files: list, out: str, news_source=None, strats: list = None) -> list:
    """Run the strategies over archived IEX days; append virtual trades to `out`."""
    strats = strats or strategies.ALL
    state = {"seen": [], "pending": []}
    engine, rows = Engine(strats, Ctx(news_source or (lambda *a: [])), state), []
    for path in sorted(day_files):
        with gzip.open(path, "rt", newline="") as f:
            bars = [{**r, **{k: float(r[k]) for k in ("o", "h", "l", "c", "v")}} for r in csv.DictReader(f)]
        bars.sort(key=lambda r: (r["t"], r["symbol"]))
        engine.new_day(os.path.basename(path)[:10])
        for b in bars:
            engine.feed(b["symbol"], b)
        rows += engine.settle(final=True)
    append_csv(out, VIRTUAL_FIELDS, rows)
    return rows


if __name__ == "__main__":
    if sys.argv[1:2] == ["replay"]:
        import glob
        n = len(replay(glob.glob(os.path.join(sys.argv[2], "*.csv.gz")), sys.argv[3]))
        print(f"{n} virtual trades -> {sys.argv[3]}")
    else:
        main()
