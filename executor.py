import os
from alpaca_client import get_client

# Max % of portfolio to risk per trade
MAX_RISK_PCT = 0.02

# Daily loss limit — bot stops trading if unrealized PnL drops below this
# For a $100k paper account, -$1,500 = -1.5% drawdown limit
DAILY_LOSS_LIMIT = -1500.0

# Trailing stop distance as % of price
# e.g. 0.008 = trail 0.8% below highest price reached
TRAILING_STOP_PCT = {
    "SPY":  0.004,
    "QQQ":  0.005,
    "AAPL": 0.008,
    "MSFT": 0.008,
    "NVDA": 0.015,
    "TSLA": 0.015,
    "AMZN": 0.010,
}
DEFAULT_TRAILING_STOP = 0.010


def get_trailing_stop_pct(symbol: str) -> float:
    return TRAILING_STOP_PCT.get(symbol.upper(), DEFAULT_TRAILING_STOP)


# ── Account helpers ───────────────────────────────────────────────────────────

def get_open_positions() -> dict:
    api = get_client()
    return {p.symbol: p for p in api.list_positions()}


def already_have_position(symbol: str) -> bool:
    return symbol in get_open_positions()


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
    Submit a BUY (bracket + trailing stop) or SELL (close position) order.

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
    trail_pct = get_trailing_stop_pct(symbol)

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

        # Risk-based sizing adjusted by time-of-day multiplier
        # shares = (portfolio * risk%) / (price * stop%) * size_mult
        risk_dollars = portfolio * MAX_RISK_PCT * size_mult
        shares = max(1, int(risk_dollars / (price * stop_pct)))

        stop_price  = round(price * (1 - stop_pct), 2)
        limit_price = round(price * (1 + take_pct), 2)

        # Trail amount in dollars (e.g. 0.8% of $150 = $1.20 trail)
        trail_dollars = round(price * trail_pct, 2)

        try:
            # Primary bracket order with hard stop + take profit
            api.submit_order(
                symbol=symbol,
                qty=shares,
                side="buy",
                type="market",
                time_in_force="day",
                order_class="bracket",
                stop_loss={"stop_price": stop_price},
                take_profit={"limit_price": limit_price},
            )
            print(
                f"[executor] ✅ BUY {shares}x {symbol} @ ~${price:.2f} | "
                f"SL=${stop_price} TP=${limit_price} | "
                f"size_mult={size_mult:.2f} risk=${risk_dollars:.0f}"
            )
            return {"symbol": symbol, "side": "BUY", "price": price, "qty": shares}

        except Exception as e:
            print(f"[executor] BUY order failed for {symbol}: {e}")
            return None

    elif signal == "SELL":
        positions = get_open_positions()
        if symbol not in positions:
            print(f"[executor] No position in {symbol} to close")
            return None

        pos = positions[symbol]
        qty = abs(int(float(pos.qty)))
        entry = float(pos.avg_entry_price)

        try:
            api.close_position(symbol)
            print(f"[executor] ✅ CLOSED {symbol} | {qty} shares | entry=${entry:.2f} | exit=~${price:.2f}")
            return {"symbol": symbol, "side": "SELL", "price": price, "qty": qty}
        except Exception as e:
            print(f"[executor] Failed to close {symbol}: {e}")
            return None

    return None
