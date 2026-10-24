import time
from datetime import datetime, timedelta

import pytz

from alpaca_client import get_client

ET = pytz.timezone("America/New_York")

# Max % of portfolio to risk per trade
MAX_RISK_PCT = 0.02

# Cap on notional per entry, as a fraction of portfolio value. Risk-based sizing
# alone (risk% / stop%) can exceed 100% of equity when stops are tight — a
# single 1.2%-stop AMZN entry once took 125% of the account on margin.
MAX_POSITION_PCT = 0.25

# Cap on total open ORB notional. The per-entry cap alone let one afternoon
# (2026-09-21) stack four 25% entries in correlated names — ~98% of equity.
MAX_ORB_EXPOSURE_PCT = 0.50

# Names that move together count as one bet: same-direction ORB notional in a
# bucket is capped at MAX_BUCKET_PCT. On 2026-10-23 SPY + QQQ were bought
# together (~$50k, the whole 50% total cap on what is one index position) and
# both lost, -$209 of the week's -$201.
CORRELATION_BUCKETS = [{"SPY", "QQQ"}, {"AAPL", "MSFT", "AMZN", "NVDA"}]
MAX_BUCKET_PCT = 0.25

# Skip entries smaller than this. Once two 25% positions fill the total cap the
# leftover room bought 1-3 shares ($264-$779): 4 of the 9 round trips in the
# week of 2026-10-19 were dust that said nothing about the strategy but counted
# toward the 100-trade go-live scorecard.
MIN_ENTRY_NOTIONAL = 2000.0

# client_order_id prefix so intraday ORB entries can be told apart from the
# long-term sleeve (position_manager.py) sharing the same Alpaca account.
ORB_TAG = "orb"

# How far back open_orb_trades looks for still-held entries (catches trades
# orphaned on an earlier day, e.g. by a failed flatten).
ORB_LOOKBACK_DAYS = 7
OPEN_LEG_STATUSES = ("new", "accepted", "held", "partially_filled", "pending_cancel")

# Daily loss limit — bot stops trading if unrealized PnL drops below this
# For a $100k paper account, -$1,500 = -1.5% drawdown limit
DAILY_LOSS_LIMIT = -1500.0

# Downside breakouts open paper shorts (bracket: stop above, target below)
# instead of being ignored. Enabled 2026-10-17: the long-only bot sat out a
# whole falling week (81 SELL signals, 0 trades), starving the go-live sample.
ALLOW_SHORTS = True


# ── Account helpers ───────────────────────────────────────────────────────────

def get_open_positions() -> dict:
    api = get_client()
    return {p.symbol: p for p in api.list_positions()}


def orb_trade_in(api, symbol: str) -> dict | None:
    # Only ORB shares count — the long-term sleeve's holdings (AAPL, MSFT,
    # QQQ...) used to block every ORB entry in those symbols.
    return next((t for t in open_orb_trades(api) if t["symbol"] == symbol), None)


def open_orb_trades(api) -> list[dict]:
    """
    ORB entries (last ORB_LOOKBACK_DAYS), long or short, still held: filled,
    no bracket leg filled, and no filled ORB exit for that symbol since. Legs
    may be working *or* already cancelled — a failed flatten used to cancel the
    legs, reject the sell, and orphan a naked position this function then
    ignored. Only orders tagged ORB_TAG are considered, so the long-term
    sleeve's positions are never touched; qty is capped at the live position
    (shares held for a long entry, shares short for a short entry).
    """
    start = datetime.now(ET) - timedelta(days=ORB_LOOKBACK_DAYS)
    orders = api.list_orders(status="all", limit=500, nested=True, after=start.isoformat())
    positions = {p.symbol: int(float(p.qty)) for p in api.list_positions()}
    exits = {}        # symbol -> latest filled ORB exit time
    rearmed = {}      # symbol -> working re-armed stops (tagged ORB_TAG + "x-stop-")
    for o in orders:
        cid = o.client_order_id or ""
        if cid.startswith(f"{ORB_TAG}x-") and o.status == "filled":
            exits[o.symbol] = max(exits.get(o.symbol, o.filled_at), o.filled_at)
        elif cid.startswith(f"{ORB_TAG}x-stop-") and o.status in OPEN_LEG_STATUSES:
            rearmed.setdefault(o.symbol, []).append(o)
    trades = []
    for o in orders:
        if not (o.client_order_id or "").startswith(f"{ORB_TAG}-"):
            continue
        if o.status != "filled":
            continue
        side = "long" if o.side == "buy" else "short"
        legs = getattr(o, "legs", None) or []
        if any(l.status == "filled" for l in legs):
            continue
        if o.symbol in exits and exits[o.symbol] > o.filled_at:
            continue
        held = positions.get(o.symbol, 0)  # negative when short
        qty = min(int(float(o.filled_qty)), held if side == "long" else -held)
        if qty < 1:
            continue
        open_legs = [l for l in legs if l.status in OPEN_LEG_STATUSES] + rearmed.get(o.symbol, [])
        stop = next((float(l.stop_price) for l in legs if l.stop_price), None)
        trades.append({"symbol": o.symbol, "qty": qty, "side": side, "legs": open_legs, "stop": stop})
    return trades


def _wait_legs_done(api, legs, timeout: float = 15.0) -> bool:
    """Poll until every cancelled leg reaches a final state. Returns False if a
    leg filled (the bracket already exited) — the caller must not sell again.
    Selling while legs are still pending_cancel is rejected by Alpaca because
    the legs still reserve the shares."""
    deadline = time.monotonic() + timeout
    pending = {l.id for l in legs}
    while pending and time.monotonic() < deadline:
        for leg_id in list(pending):
            status = api.get_order(leg_id).status
            if status == "filled":
                return False
            if status in ("canceled", "expired", "rejected", "replaced"):
                pending.discard(leg_id)
        if pending:
            time.sleep(0.5)
    if pending:
        print(f"[executor] legs still pending after {timeout:.0f}s: {sorted(pending)}")
    return True


def exit_orb_trade(api, trade: dict, price: float) -> dict | None:
    """Cancel the bracket legs, wait for the cancels to land, then close exactly
    the ORB qty (sell a long, buy back a short). If that fails, re-arm a GTC
    stop so the position is never left unprotected."""
    close_side = "sell" if trade.get("side", "long") == "long" else "buy"
    for leg in trade["legs"]:
        try:
            api.cancel_order(leg.id)
        except Exception as e:
            print(f"[executor] cancel leg {leg.id} failed (non-fatal): {e}")
    if not _wait_legs_done(api, trade["legs"]):
        print(f"[executor] {trade['symbol']}: bracket leg already filled, nothing to exit")
        return None
    try:
        api.submit_order(symbol=trade["symbol"], qty=trade["qty"], side=close_side,
                         type="market", time_in_force="day",
                         client_order_id=f"{ORB_TAG}x-{trade['symbol']}-{datetime.now(ET):%Y%m%d-%H%M%S}")
        print(f"[executor] ✅ EXIT {trade['symbol']} | {trade['qty']} shares @ ~${price:.2f}")
        return {"symbol": trade["symbol"], "side": close_side.upper(), "price": price, "qty": trade["qty"]}
    except Exception as e:
        print(f"[executor] exit {trade['symbol']} failed: {e}")
        if trade.get("stop"):
            try:
                api.submit_order(symbol=trade["symbol"], qty=trade["qty"], side=close_side,
                                 type="stop", stop_price=trade["stop"], time_in_force="gtc",
                                 client_order_id=f"{ORB_TAG}x-stop-{trade['symbol']}-{datetime.now(ET):%Y%m%d-%H%M%S}")
                print(f"[executor] re-armed GTC stop {trade['symbol']} @ ${trade['stop']}")
            except Exception as e2:
                print(f"[executor] ⚠️ {trade['symbol']} UNPROTECTED — re-arm stop failed: {e2}")
        return None


def flatten_intraday() -> list[dict]:
    """Close every still-open ORB trade from today. Called on the last run
    before the close so nothing is held overnight."""
    api = get_client()
    closed = []
    for trade in open_orb_trades(api):
        try:
            price = float(api.get_latest_trade(trade["symbol"]).price)
        except Exception:
            price = 0.0
        result = exit_orb_trade(api, trade, price)
        if result:
            closed.append(result)
    return closed


def check_daily_loss_limit() -> bool:
    """
    Returns True if we've hit the daily loss limit and should stop trading.
    Compares total unrealized PnL across all open positions.
    """
    try:
        api = get_client()
        positions = api.list_positions()
        total_unrealized = sum(float(getattr(p, "unrealized_pl", 0)) for p in positions)
        if total_unrealized <= DAILY_LOSS_LIMIT:
            print(
                f"[executor] ⛔ Daily loss limit hit: "
                f"${total_unrealized:.2f} <= ${DAILY_LOSS_LIMIT:.2f}"
            )
            return True
    except Exception as e:
        print(f"[executor] Daily loss check failed: {e}")
    return False


# ── Order submission ──────────────────────────────────────────────────────────

def submit_order(symbol: str, signal: str, price: float, size_mult: float = 1.0,
                 stop_pct: float = None, take_pct: float = None) -> dict | None:
    """
    Act on a breakout signal. BUY covers an open ORB short, otherwise opens a
    long; SELL exits an open ORB long, otherwise opens a short (ALLOW_SHORTS).
    Entries are GTC brackets with a hard stop and take profit.

    Args:
        symbol:     ticker
        signal:     "BUY" or "SELL"
        price:      current price (used for sizing)
        size_mult:  time-of-day multiplier from strategy (default 1.0)
        stop_pct:   override stop distance (uses symbol default if None)
        take_pct:   override take-profit distance (uses symbol default if None)

    Returns dict {"symbol", "side", "price", "qty"} on success, else None.
    """
    from strategy import get_symbol_config
    cfg = get_symbol_config(symbol)
    stop_pct  = stop_pct  or cfg["stop_pct"]
    take_pct  = take_pct  or cfg["take_pct"]

    api = get_client()

    # ── Daily loss guard ──
    if check_daily_loss_limit():
        return None

    if signal not in ("BUY", "SELL"):
        return None
    long_entry = signal == "BUY"

    # Opposite-side ORB trade open → this signal is its exit. Same side → skip.
    # Only tagged ORB trades are ever exited — never the long-term sleeve's shares.
    trade = orb_trade_in(api, symbol)
    if trade:
        if (trade["side"] == "long") != long_entry:
            return exit_orb_trade(api, trade, price)
        print(f"[executor] Already {trade['side']} {symbol}, skipping {signal}")
        return None

    positions = {p.symbol: p for p in api.list_positions()}
    if not long_entry:
        if not ALLOW_SHORTS:
            print(f"[executor] No open ORB trade in {symbol} to close")
            return None
        # Alpaca won't flip a long to a short in one order, and an ORB entry
        # must never sell the sleeve's shares.
        if symbol in positions and float(positions[symbol].qty) > 0:
            print(f"[executor] {symbol}: long-term sleeve holds shares, skipping short")
            return None
        asset = api.get_asset(symbol)
        if not (asset.shortable and asset.easy_to_borrow):
            print(f"[executor] {symbol}: not easy to borrow, skipping short")
            return None

    account  = api.get_account()
    portfolio = float(account.portfolio_value)
    cash = float(account.cash)

    # Risk-based sizing adjusted by time-of-day multiplier
    # shares = (portfolio * risk%) / (price * stop%) * size_mult
    risk_dollars = portfolio * MAX_RISK_PCT * size_mult
    shares = int(risk_dollars / (price * stop_pct))

    # Never exceed the per-position cap, the total ORB cap (longs + shorts),
    # or the cash on hand (no margin; applied to shorts too so exposure
    # stays comparable to the long-only record)
    open_trades = [t for t in open_orb_trades(api) if t["symbol"] in positions]
    orb_exposure = sum(t["qty"] * abs(float(positions[t["symbol"]].current_price)) for t in open_trades)
    bucket = next((b for b in CORRELATION_BUCKETS if symbol in b), {symbol})
    bucket_exposure = sum(t["qty"] * abs(float(positions[t["symbol"]].current_price))
                          for t in open_trades
                          if t["symbol"] in bucket and (t["side"] == "long") == long_entry)
    max_notional = min(portfolio * MAX_POSITION_PCT, cash,
                       portfolio * MAX_ORB_EXPOSURE_PCT - orb_exposure,
                       portfolio * MAX_BUCKET_PCT - bucket_exposure)
    shares = min(shares, int(max_notional / price))
    if 0 < shares and shares * price < MIN_ENTRY_NOTIONAL:
        print(f"[executor] {symbol}: {shares} shares = ${shares * price:,.0f} is below the "
              f"${MIN_ENTRY_NOTIONAL:,.0f} minimum entry, skipping")
        return None
    if shares < 1:
        print(f"[executor] {symbol}: sized to 0 shares (cash=${cash:,.0f}, orb_exposure=${orb_exposure:,.0f}, "
              f"bucket_exposure=${bucket_exposure:,.0f}, cap=${max_notional:,.0f}), skipping")
        return None

    if long_entry:
        stop_price  = round(price * (1 - stop_pct), 2)
        limit_price = round(price * (1 + take_pct), 2)
    else:
        stop_price  = round(price * (1 + stop_pct), 2)
        limit_price = round(price * (1 - take_pct), 2)
    label = "BUY" if long_entry else "SHORT"

    try:
        # Bracket order with hard stop + take profit. GTC so the protective
        # legs survive the close if flatten_intraday somehow doesn't run —
        # with "day" they were cancelled at 4pm, leaving a naked position.
        api.submit_order(
            symbol=symbol,
            qty=shares,
            side="buy" if long_entry else "sell",
            type="market",
            time_in_force="gtc",
            order_class="bracket",
            stop_loss={"stop_price": stop_price},
            take_profit={"limit_price": limit_price},
            client_order_id=f"{ORB_TAG}-{symbol}-{datetime.now(ET):%Y%m%d-%H%M%S}",
        )
        print(
            f"[executor] ✅ {label} {shares}x {symbol} @ ~${price:.2f} | "
            f"SL=${stop_price} TP=${limit_price} | "
            f"size_mult={size_mult:.2f} risk=${risk_dollars:.0f} notional=${shares * price:,.0f}"
        )
        return {"symbol": symbol, "side": label, "price": price, "qty": shares}

    except Exception as e:
        print(f"[executor] {label} order failed for {symbol}: {e}")
        return None
