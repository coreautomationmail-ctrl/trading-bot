from datetime import datetime

import pytz

from alpaca_client import get_client

ET = pytz.timezone("America/New_York")

# Max % of portfolio to risk per trade
MAX_RISK_PCT = 0.02

# Cap on notional per entry, as a fraction of portfolio value. Risk-based sizing
# alone (risk% / stop%) can exceed 100% of equity when stops are tight — a
# single 1.2%-stop AMZN entry once took 125% of the account on margin.
MAX_POSITION_PCT = 0.25

# client_order_id prefix so intraday ORB entries can be told apart from the
# long-term sleeve (position_manager.py) sharing the same Alpaca account.
ORB_TAG = "orb"

# Daily loss limit — bot stops trading if unrealized PnL drops below this
# For a $100k paper account, -$1,500 = -1.5% drawdown limit
DAILY_LOSS_LIMIT = -1500.0


# ── Account helpers ───────────────────────────────────────────────────────────

def get_open_positions() -> dict:
    api = get_client()
    return {p.symbol: p for p in api.list_positions()}


def already_have_position(symbol: str) -> bool:
    return symbol in get_open_positions()


def open_orb_trades(api) -> list[dict]:
    """
    Today's filled ORB entries whose bracket legs are still working — i.e. the
    shares are still held. Entries whose stop or take-profit already filled are
    excluded. Only looks at orders tagged ORB_TAG, so the long-term sleeve's
    positions are never touched.
    """
    start = datetime.now(ET).replace(hour=0, minute=0, second=0, microsecond=0)
    orders = api.list_orders(status="closed", limit=500, nested=True, after=start.isoformat())
    trades = []
    for o in orders:
        if not (o.client_order_id or "").startswith(f"{ORB_TAG}-"):
            continue
        if o.side != "buy" or o.status != "filled":
            continue
        legs = getattr(o, "legs", None) or []
        open_legs = [l for l in legs if l.status in ("new", "accepted", "held", "partially_filled")]
        if open_legs:
            trades.append({"symbol": o.symbol, "qty": int(float(o.filled_qty)), "legs": open_legs})
    return trades


def exit_orb_trade(api, trade: dict, price: float) -> dict | None:
    """Cancel the bracket legs, then market-sell exactly the ORB qty."""
    for leg in trade["legs"]:
        try:
            api.cancel_order(leg.id)
        except Exception as e:
            print(f"[executor] cancel leg {leg.id} failed (non-fatal): {e}")
    try:
        api.submit_order(symbol=trade["symbol"], qty=trade["qty"], side="sell",
                         type="market", time_in_force="day")
        print(f"[executor] ✅ EXIT {trade['symbol']} | {trade['qty']} shares @ ~${price:.2f}")
        return {"symbol": trade["symbol"], "side": "SELL", "price": price, "qty": trade["qty"]}
    except Exception as e:
        print(f"[executor] exit {trade['symbol']} failed: {e}")
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
    Submit a BUY (GTC bracket: hard stop + take profit) or SELL (exit today's
    ORB trade in that symbol) order.

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

    if signal == "BUY":
        if already_have_position(symbol):
            print(f"[executor] Already in {symbol}, skipping BUY")
            return None

        account  = api.get_account()
        portfolio = float(account.portfolio_value)
        cash = float(account.cash)

        # Risk-based sizing adjusted by time-of-day multiplier
        # shares = (portfolio * risk%) / (price * stop%) * size_mult
        risk_dollars = portfolio * MAX_RISK_PCT * size_mult
        shares = int(risk_dollars / (price * stop_pct))

        # Never exceed the per-position cap or spend cash we don't have (no margin)
        max_notional = min(portfolio * MAX_POSITION_PCT, cash)
        shares = min(shares, int(max_notional / price))
        if shares < 1:
            print(f"[executor] {symbol}: sized to 0 shares (cash=${cash:,.0f}, cap=${max_notional:,.0f}), skipping")
            return None

        stop_price  = round(price * (1 - stop_pct), 2)
        limit_price = round(price * (1 + take_pct), 2)

        try:
            # Bracket order with hard stop + take profit. GTC so the protective
            # legs survive the close if flatten_intraday somehow doesn't run —
            # with "day" they were cancelled at 4pm, leaving a naked position.
            api.submit_order(
                symbol=symbol,
                qty=shares,
                side="buy",
                type="market",
                time_in_force="gtc",
                order_class="bracket",
                stop_loss={"stop_price": stop_price},
                take_profit={"limit_price": limit_price},
                client_order_id=f"{ORB_TAG}-{symbol}-{datetime.now(ET):%Y%m%d-%H%M%S}",
            )
            print(
                f"[executor] ✅ BUY {shares}x {symbol} @ ~${price:.2f} | "
                f"SL=${stop_price} TP=${limit_price} | "
                f"size_mult={size_mult:.2f} risk=${risk_dollars:.0f} notional=${shares * price:,.0f}"
            )
            return {"symbol": symbol, "side": "BUY", "price": price, "qty": shares}

        except Exception as e:
            print(f"[executor] BUY order failed for {symbol}: {e}")
            return None

    elif signal == "SELL":
        # Only exit ORB trades opened today — never the long-term sleeve's shares
        trades = [t for t in open_orb_trades(api) if t["symbol"] == symbol]
        if not trades:
            print(f"[executor] No open ORB trade in {symbol} to close")
            return None
        return exit_orb_trade(api, trades[0], price)

    return None
