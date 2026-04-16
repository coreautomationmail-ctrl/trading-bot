def generate_signal(bars):
    if len(bars) < 10:
        return "HOLD"

    opening = bars.between_time("09:30", "10:00")
    if opening.empty:
        return "HOLD"

    range_high = opening["high"].max()
    range_low = opening["low"].min()
    latest_close = bars["close"].iloc[-1]
    avg_volume = bars["volume"].mean()
    latest_volume = bars["volume"].iloc[-1]

    if latest_volume < avg_volume * 0.8:
        return "HOLD"

    if latest_close > range_high:
        return "BUY"
    elif latest_close < range_low:
        return "SELL"
    return "HOLD"
