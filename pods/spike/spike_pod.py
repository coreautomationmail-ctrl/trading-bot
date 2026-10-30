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

Paper only: refuses to start unless ALPACA_BASE_URL is the paper endpoint.
State (open trades) lives in STATE_DIR so a container restart resumes.
Closed trades are appended to STATE_DIR/spike_trades.csv for the scorecard.
"""
import csv
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
# the long-term sleeve (position_manager.WATCHLIST) trades, so the bots never
# share a position. v1 traded all 53 non-overlapping names and failed the
# wide-universe test.
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


# ---------- spike detection (mirrors backtest_scalp.features/signals) ----------

class SymState:
    def __init__(self):
        self.day = None
        self.vols = deque(maxlen=WINDOW)
        self.rets = deque(maxlen=WINDOW)
        self.last_ts = None

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
        if len(self.vols) >= MIN_BARS:
            vol_mean = sum(self.vols) / len(self.vols)
            mu = sum(self.rets) / len(self.rets)
            std = math.sqrt(sum((x - mu) ** 2 for x in self.rets) / (len(self.rets) - 1))
            hm = ts.hour * 60 + ts.minute
            if (FIRST_ENTRY <= hm <= LAST_ENTRY and std > 0 and v >= VOL_X * vol_mean
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


def fetch_bars(start: datetime) -> dict:
    """IEX 1-min bars since `start` for the whole universe: {sym: [bar, ...]}."""
    out, token = {}, None
    while True:
        params = {"symbols": ",".join(UNIVERSE), "timeframe": "1Min", "feed": "iex",
                  "start": start.isoformat(), "limit": 10000, "sort": "asc"}
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
                                          "exit_time", "exit_px", "pnl", "bps", "signal_bar"])
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
    if not ENABLED or len(state["open"]) >= MAX_OPEN:
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
    qty = int(POS_USD // float(bar["c"]))
    if qty < 1:
        return
    cid = f"{TAG}-{sym}-{now:%Y%m%d-%H%M%S}"
    o = order(sym, qty, "buy" if side > 0 else "sell", cid)
    state["open"].append({"symbol": sym, "side": side, "qty": qty, "entry_order": o["id"],
                          "entry_time": now.isoformat(), "signal_bar": bar["t"],
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
            t.update(exit_px=px, entry_px=entry_px, exit_time=now.isoformat(), pnl=round(pnl, 2),
                     bps=round(t["side"] * math.log(px / entry_px) * 1e4, 2),
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


def maybe_send_scorecard(state: dict):
    """Once per ISO week, Saturdays from 16:00 UTC (same slot as the ORB scorecard)."""
    import spike_scorecard
    now = datetime.now(ZoneInfo("UTC"))
    week = now.strftime("%G-W%V")
    if now.weekday() != 5 or now.hour < 16 or state.get("scorecard_week") == week:
        return
    send_telegram(spike_scorecard.build(TRADES_CSV, now.strftime("%b %d %Y")))
    state["scorecard_week"] = week


def main():
    if os.getenv("ALPACA_BASE_URL", "").rstrip("/") != PAPER_URL:
        raise SystemExit("spike pod is paper-only: ALPACA_BASE_URL must be the paper endpoint")
    os.makedirs(STATE_DIR, exist_ok=True)
    state = load_state()
    syms = {s: SymState() for s in UNIVERSE}
    warmed_day = None
    last_err = ["", 0.0]
    send_telegram(f"STRATTON spike pod up: {len(UNIVERSE)} symbols, vol x{VOL_X:g}, "
                  f"k {K:g}, hold {HOLD_MIN}m, ${POS_USD:,.0f}/trade, max {MAX_OPEN} open [paper]")
    while True:
        try:
            clock = api("GET", "/v2/clock")
            now = datetime.now(ET)
            if not clock["is_open"]:
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
