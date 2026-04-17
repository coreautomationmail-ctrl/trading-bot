import pandas as pd
import numpy as np
from datetime import datetime
import pytz

ET = pytz.timezone("America/New_York")

def get_atr(bars, period=14):
    high = bars["high"]
    low = bars["low"]
    close = bars["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - close).abs(),
        (low - close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def check_volatility_kill_switch(bars, multiplier=2.5):
    atr = get_atr(bars)
    if atr.isna().all():
        return False
    recent_atr = atr.iloc[-1]
    avg_atr = atr.iloc[-20:-1].mean()
    if pd.isna(recent_atr) or pd.isna(avg_atr):
        return False
    if recent_atr > avg_atr * multiplier:
        print(f"Volatility kill switch triggered: ATR {recent_atr:.2f} vs avg {avg_atr:.2f}")
        return True
    return False

def check_time_filter(now=None):
    if now is None:
        now = datetime.now(ET)
    # Skip first 15 minutes (9:30-9:45 ET) - too chaotic
    if now.hour == 9 and now.minute < 45:
        print("Time filter: too early, skipping")
        return False
    # Skip last 15 minutes (3:45-4:00 ET) - low liquidity
    if now.hour == 15 and now.minute >= 45:
        print("Time filter: too close to close, skipping")
        return False
    # Skip outside market hours
    if now.hour < 9 or now.hour >= 16:
        print("Time filter: outside market hours")
        return False
    return True

def generate_signal(bars, now=None):
    if len(bars) < 20:
        return "HOLD"

    # Time filter
    if not check_time_filter(now):
        return "HOLD"

    # Volatility kill switch
    if check_volatility_kill_switch(bars):
        return "HOLD"

    opening = bars.between_time("09:30", "10:00")
    if opening.empty:
        return "HOLD"

    range_high = opening["high"].max()
    range_low = opening["low"].min()
    latest_close = bars["close"].iloc[-1]
    avg_volume = bars["volume"].mean()
    latest_volume = bars["volume"].iloc[-1]

    # Require above average volume to confirm breakout
    if latest_volume < avg_volume * 0.8:
        return "HOLD"

    if latest_close > range_high:
        return "BUY"
    elif latest_close < range_low:
        return "SELL"
    return "HOLD"
