from alpaca_client import get_client

MAX_RISK = 0.02
STOP_LOSS_PCT = 0.01

def get_open_positions():
    api = get_client()
    positions = api.list_positions()
    return {p.symbol: p for p in positions}

def already_have_position(symbol):
    positions = get_open_positions()
    return symbol in positions

def submit_order(symbol, signal, price):
    api = get_client()

    if signal == "BUY":
        # Duplicate order prevention
        if already_have_position(symbol):
            print(f"Already have position in {symbol}, skipping")
            return None

        account = api.get_account()
        portfolio = float(account.portfolio_value)
        shares = max(1, int((portfolio * MAX_RISK) / (price * STOP_LOSS_PCT)))

        try:
            order = api.submit_order(
                symbol=symbol,
                qty=shares,
                side="buy",
                type="market",
                time_in_force="day"
            )
            print(f"BUY {shares} shares of {symbol} at ~{price}")
            return order
        except Exception as e:
            print(f"Order failed for {symbol}: {e}")
            return None

    elif signal == "SELL":
        if not already_have_position(symbol):
            print(f"No position in {symbol} to close, skipping")
            return None
        try:
            api.close_position(symbol)
            print(f"CLOSED position in {symbol}")
        except Exception as e:
            print(f"Failed to close {symbol}: {e}")
        return None
