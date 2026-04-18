import pandas as pd
import numpy as np
from datetime import datetime
import pytz

ET = pytz.timezone("America/New_York")

# ── ATR helpers ──────────────────────────────────────────────────────────────

def get_atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    high = bars["high"]
    low = bars["low"]
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs()
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def check_volatility_kill_switch(bars: pd.DataFrame, multiplier: float = 2.5) -> bool:
    atr = get_atr(bars)
    if atr.isna().all():
        return False
    recent_atr = atr.iloc[-1]
    avg_atr = atr.iloc[-20:-1].mean()
    if pd.isna(recent_atr) or pd.isna(avg_atr):
        return False
    if recent_atr > avg_atr * multiplier:
        print(f"Volatility kill switch triggered: ATR {recent_atr:.4f} vs avg {avg_atr:.4f}")
        return True
    return False


# ── Time filter ───────────────────────────────────────────────────────────────

def check_time_filter(now: datetime = None) -> bool:
    if now is None:
        now = datetime.now(ET)
    hour, minute = now.hour, now.minute

    # Outside market hours entirely
    if hour < 9 or hour >= 16:
        print(f"Time filter: outside market hours ({now.strftime('%H:%M')} ET)")
        return False

    # First 15 min (9:30–9:45) — opening chaos
    if hour == 9 and minute < 45:
        print(f"Time filter: too early ({now.strftime('%H:%M')} ET), skipping")
        return False

    # Last 15 min (15:45–16:00) — low liquidity
    if hour == 15 and minute >= 45:
        print(f"Time filter: too close to close ({now.strftime('%H:%M')} ET), skipping")
        return False

    return True


# ── Index validation ──────────────────────────────────────────────────────────

def _ensure_et_index(bars: pd.DataFrame) -> pd.DataFrame:
    """
    Guarantee bars has a timezone-aware DatetimeIndex in ET.
    Raises ValueError if the index is not a DatetimeIndex so callers
    catch misconfigured data early rather than silently returning HOLD.
    """
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError(
            "bars.index must be a DatetimeIndex. "
            "Check that alpaca_client.get_bars() converts the index to ET."
        )
    if bars.index.tz is None:
        bars = bars.copy()
        bars.index = bars.index.tz_localize("UTC").tz_convert("America/New_York")
    elif str(bars.index.tz) != "America/New_York":
        bars = bars.copy()
        bars.index = bars.index.tz_convert("America/New_York")
    return bars


# ── Signal generation ─────────────────────────────────────────────────────────

def generate_signal(bars: pd.DataFrame, now: datetime = None) -> str:
    """
    Opening-range breakout strategy.

    Returns one of: "BUY" | "SELL" | "HOLD"
    """
    if len(bars) < 20:
        print("Signal: not enough bars")
        return "HOLD"

    if not check_time_filter(now):
        return "HOLD"

    try:
        bars = _ensure_et_index(bars)
    except ValueError as e:
        print(f"Signal: index error — {e}")
        return "HOLD"

    if check_volatility_kill_switch(bars):
        return "HOLD"

    # Opening range: 9:30–10:00 ET
    opening = bars.between_time("09:30", "10:00")
    if opening.empty:
        print("Signal: opening range empty — no data in 9:30–10:00 window")
        return "HOLD"

    range_high = opening["high"].max()
    range_low = opening["low"].min()
    latest_close = float(bars["close"].iloc[-1])

    # Volume filter — use only the regular session (9:30 onwards) to avoid
    # pre-market volume inflating the average and making the threshold too hard to clear
    session_bars = bars.between_time("09:30", "16:00")
    if session_bars.empty or "volume" not in session_bars.columns:
        print("Signal: no session volume data, skipping volume filter")
    else:
        avg_volume = session_bars["volume"].mean()
        latest_volume = float(bars["volume"].iloc[-1])
        if latest_volume < avg_volume * 0.8:
            print(f"Signal: volume too low ({latest_volume:.0f} < {avg_volume * 0.8:.0f}), HOLD")
            return "HOLD"

    if latest_close > range_high:
        print(f"Signal: BUY breakout above range high {range_high:.2f}")
        return "BUY"
    elif latest_close < range_low:
        print(f"Signal: SELL breakdown below range low {range_low:.2f}")
        return "SELL"

    print(f"Signal: HOLD — price {latest_close:.2f} within range [{range_low:.2f}, {range_high:.2f}]")
    return "HOLD"
