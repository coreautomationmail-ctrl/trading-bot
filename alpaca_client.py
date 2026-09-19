import alpaca_trade_api as tradeapi
from datetime import datetime, timedelta, timezone
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


# Calendar days of history to request per timeframe so that `limit` bars are
# always available (weekends/holidays included). Without an explicit `start`
# Alpaca defaults to today's 04:00 ET and returns the *first* N bars of the
# day, so the bot only ever saw data up to ~12:15 ET and 1 daily bar.
_LOOKBACK_DAYS = {"1Min": 3, "5Min": 7, "15Min": 14, "1Hour": 30, "1Day": 400}


def get_bars(symbol: str, timeframe: str = "5Min", limit: int = 100):
    """Return the most recent `limit` bars (ascending)."""
    api = get_client()
    days = _LOOKBACK_DAYS.get(timeframe, 400)
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    bars = api.get_bars(symbol, timeframe, start=start).df.tail(limit)
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
