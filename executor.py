from alpaca_client import get_client

MAX_RISK = 0.02
STOP_LOSS_PCT = 0.01

def submit_order(symbol, signal, price):
    api = get_client()
    account = api.get_account()
    portfolio = float(account.portfolio_value)
    shares = max(1, int((portfolio * MAX_RISK) / (price * STOP_LOSS_PCT)))

    if signal == "BUY":
        api.submit_order(
            symbol=symbol,
            qty=shares,
            side="buy",
            type="market",
            time_in_force="day"
        )
        print(f"BUY {shares} shares of {symbol} at ~{price}")

    elif signal == "SELL":
        try:
            api.close_position(symbol)
            print(f"CLOSED position in {symbol}")
        except Exception as e:
            print(f"No open position to close: {e}")
