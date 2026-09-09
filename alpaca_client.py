import alpaca_trade_api as tradeapi
from dotenv import load_dotenv
import os

load_dotenv()


def get_client() -> tradeapi.REST:
    return tradeapi.REST(
        os.getenv("ALPACA_API_KEY"),
        os.getenv("ALPACA_SECRET_KEY"),
        os.getenv("ALPACA_BASE_URL"),
        api_version="v2"
    )


def get_bars(symbol: str, timeframe: str = "5Min", limit: int = 100):
    api = get_client()
    bars = api.get_bars(symbol, timeframe, limit=limit).df
    if bars.index.tz is None:
        bars.index = bars.index.tz_localize("UTC")
    bars.index = bars.index.tz_convert("America/New_York")
    return bars


def get_account():
    """Return the Alpaca account object."""
    api = get_client()
    return api.get_account()


def get_historical_bars(symbol: str, timeframe: str, start: str, end: str):
    """
    Pull the full bar range [start, end) for backtesting, e.g. a historical
    shock window. Dates as 'YYYY-MM-DD'; no `limit` is passed so the SDK
    paginates through the whole range instead of capping at one page.
    """
    api = get_client()
    bars = api.get_bars(symbol, timeframe, start=start, end=end).df
    if bars.index.tz is None:
        bars.index = bars.index.tz_localize("UTC")
    bars.index = bars.index.tz_convert("America/New_York")
    return bars


def get_fills(symbol: str = None):
    """
    Return a list of filled orders.
    Optionally filter by symbol.
    """
    api = get_client()
    orders = api.list_orders(status="filled", limit=100)
    if symbol:
        orders = [o for o in orders if o.symbol == symbol]
    return orders
