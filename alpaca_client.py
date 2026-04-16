import alpaca_trade_api as tradeapi
from dotenv import load_dotenv
import os

load_dotenv()

def get_client():
    return tradeapi.REST(
        os.getenv("ALPACA_API_KEY"),
        os.getenv("ALPACA_SECRET_KEY"),
        os.getenv("ALPACA_BASE_URL"),
        api_version="v2"
    )

def get_bars(symbol, timeframe="5Min", limit=100):
    api = get_client()
    bars = api.get_bars(symbol, timeframe, limit=limit).df
    bars.index = bars.index.tz_convert("America/New_York")
    return bars
