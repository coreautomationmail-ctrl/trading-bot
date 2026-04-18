from alpaca_client import get_client

# Risk 2% of portfolio per trade
MAX_RISK_PCT = 0.02

# Stop-loss distance: 1% below entry
STOP_LOSS_PCT = 0.01

# Take-profit distance: 2% above entry (2:1 reward/risk)
TAKE_PROFIT_PCT = 0.02


def get_open_positions() -> dict:
    api = get_client()
    positions = api.list_positions()
    return {p.symbol: p for p in positions}


def already_have_position(symbol: str) -> bool:
    return symbol in get_open_positions()


def submit_order(symbol: str, signal: str, price: float) -> dict | None:
    """
    Submit a BUY or SELL order.

    Returns a dict {"symbol", "side", "price", "qty"} on success so the
    caller can log it properly, or None if no order was placed.
    """
    api = get_client()

    if signal == "BUY":
        if already_have_position(symbol):
            print(f"[executor] Already have position in {symbol}, skipping BUY")
            return None

        account = api.get_account()
        portfolio = float(account.portfolio_value)

        # Correct sizing: risk MAX_RISK_PCT of portfolio
        # i.e. if price drops STOP_LOSS_PCT, the loss equals MAX_RISK_PCT * portfolio
        # shares = (portfolio * MAX_RISK_PCT) / (price * STOP_LOSS_PCT)
        # Example: $10k account, $100 stock → (10000 * 0.02) / (100 * 0.01) = 20 shares ($2k notional)
        risk_dollars = portfolio * MAX_RISK_PCT
        shares = max(1, int(risk_dollars / (price * STOP_LOSS_PCT)))

        stop_price = round(price * (1 - STOP_LOSS_PCT), 2)
        limit_price = round(price * (1 + TAKE_PROFIT_PCT), 2)

        try:
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
                f"[executor] BUY {shares} shares of {symbol} @ ~${price:.2f} | "
                f"SL=${stop_price} TP=${limit_price}"
            )
            return {"symbol": symbol, "side": "BUY", "price": price, "qty": shares}
        except Exception as e:
            print(f"[executor] BUY order failed for {symbol}: {e}")
            return None

    elif signal == "SELL":
        positions = get_open_positions()
        if symbol not in positions:
            print(f"[executor] No position in {symbol} to close, skipping SELL")
            return None

        pos = positions[symbol]
        qty = abs(int(float(pos.qty)))
        entry_price = float(pos.avg_entry_price)

        try:
            api.close_position(symbol)
            print(f"[executor] CLOSED position in {symbol} ({qty} shares, entry ${entry_price:.2f})")
            return {"symbol": symbol, "side": "SELL", "price": price, "qty": qty}
        except Exception as e:
            print(f"[executor] Failed to close {symbol}: {e}")
            return None

    return None
