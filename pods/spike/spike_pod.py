"""
STRATTON pod #2: volume-spike momentum (paper), v2: top-dollar-volume names, afternoon only.

Backtest (scripts/backtest_scalp.py): a 1-min bar with volume >= VOL_X x its
trailing 30-bar mean and |log(close/open)| >= K x the trailing 30-bar std of
that return keeps running in the same direction for the next hour. Detection
here uses IEX bars (the free live feed); the IEX check kept about half the
SIP edge (+7..10 bps/trade gross, 2025-26).

Loop, once a minute (a few seconds after the minute closes):
  1. poll the latest IEX 1-min bars for the universe (REST, so no 30-symbol
     websocket cap)
  2. update each symbol's trailing 30-bar state; a fresh spike bar -> market
     order in the bar's direction, sized POS_USD, if the symbol has no
     position in the account (ORB, sleeve or this pod) and the pod has room
  3. close each pod trade HOLD_MIN minutes after entry, and everything at
     15:55 ET

Shadow log (no orders): the same spike rule on all 62 backtested names, all
day (09:35-14:58), each signal's backtest-style outcome (enter the open two
bars after the signal bar, exit the close 60 bars later) appended to
STATE_DIR/spike_shadow.csv. ~10x the live evidence of the pod's own trades,
so live-vs-backtest is judged in weeks, not months.

After each session the day's IEX 1-min bars for the 62 names are saved to
STATE_DIR/iex/<date>.csv.gz: our own record of exactly what the live feed
showed, to measure how far it differs from the SIP data the backtests use.

Each trade also records slippage: fill vs the signal bar's close, in bps
against the trade (positive = paid more than the signal price).

Paper only: refuses to start unless ALPACA_BASE_URL is the paper endpoint.
State (open trades) lives in STATE_DIR so a container restart resumes.
Closed trades are appended to STATE_DIR/spike_trades.csv for the scorecard.
"""
import csv
import gzip
import json
import math
import os
import time
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
PAPER_URL = "https://paper-api.alpaca.markets"
DATA_URL = "https://data.alpaca.markets/v2/stocks/bars"
TAG = "spk"

# Universe (v2, 2026-10-30): the top 25 of the 62 backtested names by 2025
# average daily dollar volume (scripts/spike_buckets.py: the edge rises with
# prior-year dollar volume), minus every symbol the ORB bot (bot.SYMBOLS) or
# the long-term sleeve (position_manager.WATCHLIST) trades. Since 2026-10-30 the
# pod has its own paper account; adding those 8 big names back was tested and
# rejected (afternoon 2022-26: ~-1 bps on their own). v1 traded all 53
# non-overlapping names and failed the wide-universe test.
UNIVERSE = [
    "META", "IWM", "AMD", "GOOGL", "AVGO", "ORCL", "INTC", "V", "BAC",
    "WMT", "BA", "XOM", "GS", "JNJ", "PFE", "HD", "QCOM",
]
VOL_X = float(os.getenv("SPIKE_VOL_X", "8"))
K = float(os.getenv("SPIKE_K", "5"))
HOLD_MIN = int(os.getenv("SPIKE_HOLD_MIN", "60"))
POS_USD = float(os.getenv("SPIKE_POS_USD", "5000"))
MAX_OPEN = int(os.getenv("SPIKE_MAX_OPEN", "4"))
DAILY_LOSS_STOP = float(os.getenv("SPIKE_DAILY_LOSS_STOP", "500"))
ENABLED = os.getenv("SPIKE_ENABLED", "true").lower() == "true"
STATE_DIR = os.getenv("STATE_DIR", "/data")
WINDOW = 30
MIN_BARS = 20
# v2: afternoon signals only -- 13:00-14:58 held up in core, wide and held-out
# 2017-22 (spike_buckets.py); 14:54 so the 60-min hold exits before FLATTEN.
FIRST_ENTRY, LAST_ENTRY, FLATTEN = (13 * 60), (14 * 60 + 54), (15 * 60 + 55)

# Shadow universe: backtest_scalp.SYMBOLS + WIDE (the 62 the edge was found on).
SHADOW = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "TSLA", "META", "AMD", "GOOGL", "JPM",
          "BAC", "XOM", "GE", "PFE", "GILD", "C", "WFC", "T", "CVX", "JNJ", "MCD", "VZ", "DIS", "PG",
          "BHC", "BKNG", "V", "INTC", "CSCO", "GS", "WMT", "BA", "HD", "IBM", "CMG", "CMCSA", "KO",
          "SLB", "AVGO", "QCOM", "RTX", "ORCL", "NKE", "SBUX", "AMGN", "MRK", "COP", "BIDU", "AIG",
          "F", "PEP", "MDLZ", "BIIB", "ABBV", "CVS", "UNP", "AXP", "BMY", "CAT", "PM"]
SHADOW_FIRST, SHADOW_LAST = 9 * 60 + 35, 14 * 60 + 58


# ---------- spike detection (mirrors backtest_scalp.features/signals) ----------

class SymState:
    def __init__(self, first: int = FIRST_ENTRY, last: int = LAST_ENTRY):
        self.first, self.last = first, last
        self.day = None
        self.vols = deque(maxlen=WINDOW)
        self.rets = deque(maxlen=WINDOW)
        self.last_ts = None
        self.last_std = self.last_vol_mean = None

    def update(self, ts: datetime, o: float, c: float, v: float):
        """Feed one completed bar; return +1/-1 if it is a spike bar, else 0."""
        if ts.date() != self.day:
            self.day, self.last_ts = ts.date(), None
            self.vols.clear()
            self.rets.clear()
        if self.last_ts is not None and ts <= self.last_ts:
            return 0
        self.last_ts = ts
        r = math.log(c / o) if o > 0 and c > 0 else 0.0
        signal = 0
        self.last_std = self.last_vol_mean = None
        if len(self.vols) >= MIN_BARS:
            vol_mean = sum(self.vols) / len(self.vols)
            mu = sum(self.rets) / len(self.rets)
            std = math.sqrt(sum((x - mu) ** 2 for x in self.rets) / (len(self.rets) - 1))
            self.last_std, self.last_vol_mean = std, vol_mean
            hm = ts.hour * 60 + ts.minute
            if (self.first <= hm <= self.last and std > 0 and v >= VOL_X * vol_mean
                    and abs(r) >= K * std):
                signal = 1 if r > 0 else -1
        self.vols.append(v)
        self.rets.append(r)
        return signal


# ---------- Alpaca REST ----------

def _headers():
    return {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
            "APCA-API-SECRET-KEY": os.environ["ALPACA_SECRET_KEY"]}


def api(method: str, path: str, **kw):
    r = requests.request(method, f"{PAPER_URL}{path}", headers=_headers(), timeout=15, **kw)
    if r.status_code >= 400:
        raise RuntimeError(f"{method} {path} -> {r.status_code} {r.text[:200]}")
    return r.json() if r.text else None


def fetch_bars(start: datetime, symbols: list = None, end: datetime = None) -> dict:
    """IEX 1-min bars since `start`: {sym: [bar, ...]} (default: the traded universe)."""
    out, token = {}, None
    while True:
        params = {"symbols": ",".join(symbols or UNIVERSE), "timeframe": "1Min", "feed": "iex",
                  "start": start.isoformat(), "limit": 10000, "sort": "asc"}
        if end:
            params["end"] = end.isoformat()
        if token:
            params["page_token"] = token
        r = requests.get(DATA_URL, headers=_headers(), params=params, timeout=20)
        r.raise_for_status()
        body = r.json()
        for sym, bars in (body.get("bars") or {}).items():
            out.setdefault(sym, []).extend(bars)
        token = body.get("next_page_token")
        if not token:
            return out


def send_telegram(text: str):
    token, chat = os.getenv("TELEGRAM_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    print(text, flush=True)
    if token and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text}, timeout=10)
        except Exception as e:
            print(f"[telegram] {e}", flush=True)


# ---------- pod state ----------

STATE_FILE = os.path.join(STATE_DIR, "spike_state.json")
TRADES_CSV = os.path.join(STATE_DIR, "spike_trades.csv")
CONTROL_FILE = os.path.join(STATE_DIR, "spike_control.json")  # written by the weekly scorecard
SHADOW_CSV = os.path.join(STATE_DIR, "spike_shadow.csv")
SHADOW_FIELDS = ["date", "et", "symbol", "signal_bar", "side", "sigma", "volx", "entry_px", "exit_px", "bps"]


def load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"open": [], "day": None, "realized_today": 0.0}


def save_state(s: dict):
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f, indent=1, default=str)
    os.replace(tmp, STATE_FILE)


def log_trade(t: dict):
    new = not os.path.exists(TRADES_CSV)
    with open(TRADES_CSV, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "side", "qty", "entry_time", "entry_px",
                                          "exit_time", "exit_px", "pnl", "bps", "signal_bar",
                                          "signal_px", "slip_bps"])
        if new:
            w.writeheader()
        w.writerow({k: t.get(k) for k in w.fieldnames})


def order(symbol: str, qty: int, side: str, cid: str) -> dict:
    return api("POST", "/v2/orders", json={"symbol": symbol, "qty": str(qty), "side": side,
                                           "type": "market", "time_in_force": "day",
                                           "client_order_id": cid})


def filled_price(order_id: str):
    o = api("GET", f"/v2/orders/{order_id}")
    return float(o["filled_avg_price"]) if o.get("status") == "filled" else None


def enter(state: dict, sym: str, side: int, bar: dict, now: datetime):
    import spike_scorecard
    control = spike_scorecard.load_control(CONTROL_FILE)
    if not ENABLED or control["paused"] or len(state["open"]) >= MAX_OPEN:
        return
    if state["realized_today"] <= -DAILY_LOSS_STOP:
        return
    held = {p["symbol"] for p in api("GET", "/v2/positions")}
    if sym in held or any(t["symbol"] == sym for t in state["open"]):
        return
    if side < 0:
        asset = api("GET", f"/v2/assets/{sym}")
        if not (asset.get("shortable") and asset.get("easy_to_borrow")):
            return
    qty = int(POS_USD * control["size_mult"] // float(bar["c"]))
    if qty < 1:
        return
    cid = f"{TAG}-{sym}-{now:%Y%m%d-%H%M%S}"
    o = order(sym, qty, "buy" if side > 0 else "sell", cid)
    state["open"].append({"symbol": sym, "side": side, "qty": qty, "entry_order": o["id"],
                          "entry_time": now.isoformat(), "signal_bar": bar["t"], "signal_px": float(bar["c"]),
                          "exit_due": (now + timedelta(minutes=HOLD_MIN)).isoformat()})
    send_telegram(f"STRATTON spike: {'BUY' if side > 0 else 'SHORT'} {qty} {sym} "
                  f"(bar {bar['t'][11:16]}Z, vol x{VOL_X:g}, move >= {K:g} sd) [paper]")


def manage_exits(state: dict, now: datetime):
    hm = now.hour * 60 + now.minute
    for t in list(state["open"]):
        if "exit_order" in t:
            px = filled_price(t["exit_order"])
            if px is None:
                continue
            entry_px = t.get("entry_px") or filled_price(t["entry_order"]) or px
            pnl = t["side"] * (px - entry_px) * t["qty"]
            sig_px = t.get("signal_px")
            t.update(exit_px=px, entry_px=entry_px, exit_time=now.isoformat(), pnl=round(pnl, 2),
                     bps=round(t["side"] * math.log(px / entry_px) * 1e4, 2),
                     slip_bps=round(t["side"] * math.log(entry_px / sig_px) * 1e4, 2) if sig_px else "",
                     side="long" if t["side"] > 0 else "short")
            log_trade(t)
            state["realized_today"] += pnl
            state["open"].remove(t)
            send_telegram(f"STRATTON spike: closed {t['symbol']} {t['bps']:+.1f} bps (${pnl:+.0f}) [paper]")
            continue
        if "entry_px" not in t:
            t["entry_px"] = filled_price(t["entry_order"])
        if now >= datetime.fromisoformat(t["exit_due"]) or hm >= FLATTEN:
            if t["entry_px"] is None:          # entry never filled: cancel and drop
                try:
                    api("DELETE", f"/v2/orders/{t['entry_order']}")
                except RuntimeError:
                    pass
                state["open"].remove(t)
                continue
            cid = f"{TAG}x-{t['symbol']}-{now:%Y%m%d-%H%M%S}"
            o = order(t["symbol"], t["qty"], "sell" if t["side"] > 0 else "buy", cid)
            t["exit_order"] = o["id"]


# ---------- shadow log ----------

def shadow_signal(state: dict, sym: str, side: int, st: SymState, b: dict, ts: datetime):
    """Remember a shadow signal; its outcome is filled in as later bars arrive."""
    ret_std = st.last_std or 0.0
    state.setdefault("shadow", []).append({
        "symbol": sym, "signal_bar": b["t"], "et": ts.strftime("%H:%M"), "side": side,
        "sigma": round(abs(math.log(b["c"] / b["o"])) / ret_std, 2) if ret_std else "",
        "volx": round(b["v"] / st.last_vol_mean, 1) if st.last_vol_mean else ""})


def shadow_settle(state: dict, recent: dict):
    """Fill pending shadow outcomes from `recent` {sym: {bar_time: bar}}; log the finished ones.
    Entry = open of the bar 2 minutes after the signal bar (1-bar delay, as the backtest);
    exit = close of the bar 61 minutes after it. A bar IEX never printed (no trade that minute)
    is skipped for the next one that exists, up to 3 minutes later."""
    def bar_at(sym, t0, minutes):
        for extra in range(4):
            key = (t0 + timedelta(minutes=minutes + extra)).strftime("%Y-%m-%dT%H:%M:00Z")
            if key in recent.get(sym, {}):
                return recent[sym][key]
        return None

    keep = []
    for s in state.get("shadow", []):
        t0 = datetime.fromisoformat(s["signal_bar"].replace("Z", "+00:00"))
        if "entry_px" not in s:
            b = bar_at(s["symbol"], t0, 2)
            if b:
                s["entry_px"] = b["o"]
        if "entry_px" in s:
            b = bar_at(s["symbol"], t0, 61)
            if b:
                s.update(exit_px=b["c"], date=s["signal_bar"][:10],
                         bps=round(s["side"] * math.log(b["c"] / s["entry_px"]) * 1e4, 2))
                new = not os.path.exists(SHADOW_CSV)
                with open(SHADOW_CSV, "a", newline="") as f:
                    w = csv.DictWriter(f, fieldnames=SHADOW_FIELDS)
                    if new:
                        w.writeheader()
                    w.writerow({k: s.get(k) for k in SHADOW_FIELDS})
                continue
        if datetime.now(ZoneInfo("UTC")) - t0 < timedelta(hours=3):   # give up on stale ones
            keep.append(s)
    state["shadow"] = keep


def shadow_finish(state: dict, recent: dict, now: datetime):
    """A 14:58 signal's exit bar completes just as the market closes, after the open-market
    loop's last fetch, so pending signals get settled from here. Never stops the pod."""
    pending = state.get("shadow")
    if not pending:
        return
    try:
        for sym, bars in fetch_bars(now - timedelta(minutes=10), sorted({s["symbol"] for s in pending})).items():
            for b in bars:
                recent.setdefault(sym, {})[b["t"]] = b
        shadow_settle(state, recent)
    except Exception as e:
        print(f"shadow: {e}", flush=True)


# ---------- daily IEX archive ----------

ARCHIVE_DIR = os.path.join(STATE_DIR, "iex")
ARCHIVE_FIELDS = ["symbol", "t", "o", "h", "l", "c", "v", "n", "vw"]


def maybe_archive(state: dict, now: datetime):
    """Once per session, after 16:05 ET: save that day's regular-hours IEX bars for SHADOW."""
    day = state.get("day")
    if not day or state.get("archived") == day:
        return
    if str(now.date()) == day and now.hour * 60 + now.minute < 16 * 60 + 5:
        return
    d = datetime.fromisoformat(day).replace(tzinfo=ET)
    bars = fetch_bars(d.replace(hour=9, minute=30), SHADOW, d.replace(hour=16, minute=0))
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    path = os.path.join(ARCHIVE_DIR, f"{day}.csv.gz")
    with gzip.open(path + ".tmp", "wt", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ARCHIVE_FIELDS, extrasaction="ignore")
        w.writeheader()
        for sym in sorted(bars):
            for b in bars[sym]:
                w.writerow({"symbol": sym, **b})
    os.replace(path + ".tmp", path)
    state["archived"] = day
    print(f"archived {sum(len(v) for v in bars.values())} IEX bars to {path}", flush=True)


def maybe_send_scorecard(state: dict):
    """Once per ISO week, Saturdays from 16:00 UTC (same slot as the ORB scorecard)."""
    import spike_scorecard
    now = datetime.now(ZoneInfo("UTC"))
    week = now.strftime("%G-W%V")
    if now.weekday() != 5 or now.hour < 16 or state.get("scorecard_week") == week:
        return
    control, note = spike_scorecard.decide(spike_scorecard.load(TRADES_CSV),
                                           spike_scorecard.load_control(CONTROL_FILE), now.date(),
                                           spike_scorecard.load(SHADOW_CSV))
    spike_scorecard.save_control(CONTROL_FILE, control)
    send_telegram(spike_scorecard.build(TRADES_CSV, now.strftime("%b %d %Y"), note)
                  + "\n\n" + spike_scorecard.shadow_summary(SHADOW_CSV))
    state["scorecard_week"] = week


def main():
    if os.getenv("ALPACA_BASE_URL", "").rstrip("/") != PAPER_URL:
        raise SystemExit("spike pod is paper-only: ALPACA_BASE_URL must be the paper endpoint")
    os.makedirs(STATE_DIR, exist_ok=True)
    state = load_state()
    syms = {s: SymState() for s in UNIVERSE}
    shadow = {s: SymState(SHADOW_FIRST, SHADOW_LAST) for s in SHADOW}
    recent = {}   # {sym: {bar_time: bar}}, today's shadow bars for settling outcomes
    warmed_day = None
    last_err = ["", 0.0]
    send_telegram(f"STRATTON spike pod up: {len(UNIVERSE)} symbols, vol x{VOL_X:g}, "
                  f"k {K:g}, hold {HOLD_MIN}m, ${POS_USD:,.0f}/trade, max {MAX_OPEN} open [paper]")
    while True:
        try:
            clock = api("GET", "/v2/clock")
            now = datetime.now(ET)
            if not clock["is_open"]:
                shadow_finish(state, recent, now)
                maybe_archive(state, datetime.now(ET))
                maybe_send_scorecard(state)
                save_state(state)
                time.sleep(60)
                continue
            if state.get("day") != str(now.date()):
                state.update(day=str(now.date()), realized_today=0.0)
            # warm up from today's open once per day (e.g. after a restart), no trading on it
            if warmed_day != now.date():
                start = now.replace(hour=9, minute=30, second=0, microsecond=0)
                for sym, bars in fetch_bars(start).items():
                    for b in bars[:-1]:
                        syms[sym].update(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET),
                                         b["o"], b["c"], b["v"])
                recent = {}
                for sym, bars in fetch_bars(start, SHADOW).items():
                    for b in bars[:-1]:
                        shadow[sym].update(datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET),
                                           b["o"], b["c"], b["v"])
                        recent.setdefault(sym, {})[b["t"]] = b
                warmed_day = now.date()
            else:
                cutoff = now.replace(second=0, microsecond=0)   # bars labelled < this minute are complete
                for sym, bars in fetch_bars(now - timedelta(minutes=4)).items():
                    for b in bars:
                        ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
                        if ts >= cutoff:
                            continue
                        sig = syms[sym].update(ts, b["o"], b["c"], b["v"])
                        if sig and now - ts <= timedelta(minutes=2, seconds=30):
                            enter(state, sym, sig, b, now)
                try:
                    for sym, bars in fetch_bars(now - timedelta(minutes=4), SHADOW).items():
                        for b in bars:
                            ts = datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET)
                            if ts >= cutoff:
                                continue
                            recent.setdefault(sym, {})[b["t"]] = b
                            sig = shadow[sym].update(ts, b["o"], b["c"], b["v"])
                            if sig:
                                shadow_signal(state, sym, sig, shadow[sym], b, ts)
                    shadow_settle(state, recent)
                except Exception as e:   # the shadow log must never stop the pod trading
                    print(f"shadow: {e}", flush=True)
            manage_exits(state, now)
            save_state(state)
        except Exception as e:
            msg = str(e)[:300]
            # alert on a new error, or a repeat at most every 30 min
            if msg != last_err[0] or time.time() - last_err[1] > 1800:
                send_telegram(f"STRATTON spike pod error: {msg}")
                last_err[:] = [msg, time.time()]
            else:
                print(f"error (repeat): {msg}", flush=True)
        # wake 4 s after the next minute boundary
        time.sleep(60 - time.time() % 60 + 4)


if __name__ == "__main__":
    main()
