import pandas as pd
import numpy as np
from datetime import datetime
import pytz

ET = pytz.timezone("America/New_York")

# ── Per-symbol configuration ──────────────────────────────────────────────────
# stop_pct:  how far price can move against you before stop triggers
# take_pct:  take-profit target (should be >= 2x stop_pct for positive expectancy)
# vol_mult:  require this multiple of avg session volume to confirm breakout
# gap_limit: skip if pre-market gap exceeds this % (e.g. 0.03 = 3%)

SYMBOL_CONFIG = {
    "SPY":  {"stop_pct": 0.005, "take_pct": 0.010, "vol_mult": 0.8, "gap_limit": 0.02},
    "QQQ":  {"stop_pct": 0.006, "take_pct": 0.012, "vol_mult": 0.8, "gap_limit": 0.02},
    "AAPL": {"stop_pct": 0.010, "take_pct": 0.020, "vol_mult": 0.8, "gap_limit": 0.03},
    "MSFT": {"stop_pct": 0.010, "take_pct": 0.020, "vol_mult": 0.8, "gap_limit": 0.03},
    "NVDA": {"stop_pct": 0.018, "take_pct": 0.036, "vol_mult": 0.9, "gap_limit": 0.04},
    "TSLA": {"stop_pct": 0.018, "take_pct": 0.036, "vol_mult": 0.9, "gap_limit": 0.04},
    "AMZN": {"stop_pct": 0.012, "take_pct": 0.024, "vol_mult": 0.8, "gap_limit": 0.03},
}

DEFAULT_CONFIG = {"stop_pct": 0.012, "take_pct": 0.024, "vol_mult": 0.8, "gap_limit": 0.03}

# ── Time-of-day sizing multipliers ────────────────────────────────────────────
# Larger size during high-probability windows, smaller during chop
TIME_SIZE_WINDOWS = [
    # (start_hour, start_min, end_hour, end_min, multiplier, label)
    (9,  45, 11,  0, 1.25, "morning momentum window"),
    (11,  0, 14,  0, 0.75, "midday chop — reduced size"),
    (14,  0, 15, 45, 1.10, "afternoon trend window"),
    (15, 45, 16,  0, 0.50, "near close — minimal size"),
]
DEFAULT_SIZE_MULT = 0.75


def get_symbol_config(symbol: str) -> dict:
    return SYMBOL_CONFIG.get(symbol.upper(), DEFAULT_CONFIG)


# ── ATR-normalized stop/take (opt-in alternative to the fixed-% config above) ──
# k = 10% of 14-day ATR, matching the stop rule in Zarattini/Barbon/Aziz (2024),
# "Trading the Opening Range Breakouts" — the strongest public backtest evidence
# found for ORB stop calibration. Keeps the same 2:1 take:stop ratio used by
# SYMBOL_CONFIG so this is a like-for-like comparison of stop *sizing*, not a
# change to the reward:risk shape.
ATR_STOP_MULT = 0.10
ATR_STOP_TAKE_RATIO = 2.0


def get_atr_stop_take_pct(daily_bars: pd.DataFrame, price: float, fallback_cfg: dict) -> tuple:
    """Returns (stop_pct, take_pct) derived from 14-day daily ATR, expressed as a
    fraction of price so callers don't need to change. Falls back to the fixed
    per-symbol config if daily history or price is insufficient."""
    if daily_bars is None or len(daily_bars) < 15 or price <= 0:
        return fallback_cfg["stop_pct"], fallback_cfg["take_pct"]
    atr_val = get_atr(daily_bars, period=14).iloc[-1]
    if pd.isna(atr_val):
        return fallback_cfg["stop_pct"], fallback_cfg["take_pct"]
    stop_pct = (ATR_STOP_MULT * float(atr_val)) / price
    return stop_pct, stop_pct * ATR_STOP_TAKE_RATIO


# ── Opening relative volume (opt-in additional entry filter) ───────────────────
# Time-of-day-adjusted RVOL: today's opening-window volume vs the trailing
# same-window average over prior sessions. This is distinct from the existing
# volume check above (which compares the latest bar to today's own running
# session average) — RVOL compares across days, which is what the "stocks in
# play" ORB research (Zarattini/Barbon/Aziz 2024) found predictive.
RVOL_WINDOW_MINUTES = 15
RVOL_LOOKBACK_DAYS = 20


def compute_opening_rvol_series(bars: pd.DataFrame, window_minutes: int = RVOL_WINDOW_MINUTES,
                                 lookback_days: int = RVOL_LOOKBACK_DAYS) -> pd.Series:
    """
    Per-session-date series: today's opening-window volume / trailing
    `lookback_days`-session average opening-window volume (today excluded via
    shift(1), so no look-ahead). Requires multi-day intraday history in `bars`.
    """
    bars = _ensure_et_index(bars)
    end_minutes = 9 * 60 + 30 + window_minutes
    end_str = f"{end_minutes // 60:02d}:{end_minutes % 60:02d}"
    opening = bars.between_time("09:30", end_str)
    if opening.empty or "volume" not in opening.columns:
        return pd.Series(dtype=float)
    daily_open_vol = opening.groupby(opening.index.date)["volume"].sum()
    daily_open_vol.index = pd.to_datetime(daily_open_vol.index)
    min_periods = max(5, lookback_days // 2)
    trailing_avg = daily_open_vol.rolling(lookback_days, min_periods=min_periods).mean().shift(1)
    return daily_open_vol / trailing_avg


def get_time_size_multiplier(now: datetime = None) -> tuple:
    if now is None:
        now = datetime.now(ET)
    current = now.hour * 60 + now.minute
    for sh, sm, eh, em, mult, label in TIME_SIZE_WINDOWS:
        if sh * 60 + sm <= current < eh * 60 + em:
            return mult, label
    return DEFAULT_SIZE_MULT, "outside prime window"


# ── Indicators ────────────────────────────────────────────────────────────────

def get_atr(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    high = bars["high"]
    low = bars["low"]
    prev_close = bars["close"].shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(period).mean()


def get_rsi(bars: pd.DataFrame, period: int = 14) -> pd.Series:
    delta = bars["close"].diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def get_ema(bars: pd.DataFrame, period: int = 20) -> pd.Series:
    return bars["close"].ewm(span=period, adjust=False).mean()


# ── Filters ───────────────────────────────────────────────────────────────────

def check_volatility_kill_switch(bars: pd.DataFrame, multiplier: float = 2.5) -> bool:
    atr = get_atr(bars)
    if atr.isna().all():
        return False
    recent_atr = atr.iloc[-1]
    avg_atr = atr.iloc[-20:-1].mean()
    if pd.isna(recent_atr) or pd.isna(avg_atr):
        return False
    if recent_atr > avg_atr * multiplier:
        print(f"[strategy] Volatility kill switch: ATR {recent_atr:.4f} vs avg {avg_atr:.4f}")
        return True
    return False


def check_time_filter(now: datetime = None) -> bool:
    if now is None:
        now = datetime.now(ET)
    h, m = now.hour, now.minute
    if h < 9 or h >= 16:
        print(f"[strategy] Outside market hours ({now.strftime('%H:%M')} ET)")
        return False
    if h == 9 and m < 45:
        print(f"[strategy] Too early ({now.strftime('%H:%M')} ET)")
        return False
    if h == 15 and m >= 45:
        print(f"[strategy] Too close to close ({now.strftime('%H:%M')} ET)")
        return False
    return True


def check_premarket_gap(bars: pd.DataFrame, gap_limit: float) -> bool:
    """Returns True (skip this symbol) if the open gap exceeds gap_limit."""
    try:
        prev_close_bars = bars.between_time("15:55", "16:00")
        open_bars = bars.between_time("09:30", "09:31")
        if prev_close_bars.empty or open_bars.empty:
            return False
        prev_close = float(prev_close_bars["close"].iloc[-1])
        today_open = float(open_bars["open"].iloc[0])
        gap = abs((today_open - prev_close) / prev_close)
        if gap > gap_limit:
            print(f"[strategy] Gap filter: {gap:.2%} > limit {gap_limit:.2%}, skipping")
            return True
    except Exception as e:
        print(f"[strategy] Gap check error: {e}")
    return False


def check_rsi_ema(bars: pd.DataFrame, signal: str) -> bool:
    """
    BUY  requires RSI > 50 AND close > 20 EMA (trending up with momentum)
    SELL requires RSI < 50 AND close < 20 EMA (trending down with momentum)
    """
    try:
        rsi = get_rsi(bars)
        ema = get_ema(bars)
        if rsi.isna().all() or ema.isna().all():
            print("[strategy] RSI/EMA: insufficient data, passing through")
            return True
        r = float(rsi.iloc[-1])
        e = float(ema.iloc[-1])
        c = float(bars["close"].iloc[-1])
        if signal == "BUY":
            ok = r > 50 and c > e
            if not ok:
                print(f"[strategy] RSI/EMA rejected BUY: RSI={r:.1f}, close={c:.2f}, EMA={e:.2f}")
            return ok
        elif signal == "SELL":
            ok = r < 50 and c < e
            if not ok:
                print(f"[strategy] RSI/EMA rejected SELL: RSI={r:.1f}, close={c:.2f}, EMA={e:.2f}")
            return ok
    except Exception as e:
        print(f"[strategy] RSI/EMA error: {e}")
    return True


LONG_TERM_TREND_PERIOD = 200  # daily bars


def check_long_term_trend(daily_bars: pd.DataFrame) -> bool:
    """
    Regime filter found via the 2022 bear-market backtest: nearly all ORB
    losses in a grinding downtrend came from BUY signals taken while price
    was below its 200-day SMA (MSFT: 97% of the loss; NVDA: 100% of trades
    lost, since it never traded above its 200-day SMA in that window).
    Blocks new BUY entries outside an established uptrend; SELL/exit logic
    is untouched so an open position can always be closed.

    Fails closed (blocks BUY) if daily history is missing or too short —
    unlike the RSI/EMA filter's pass-through default, this filter exists
    specifically to keep the bot out of bad regimes, so an unknown regime
    should not be treated as a green light.
    """
    if daily_bars is None or len(daily_bars) < LONG_TERM_TREND_PERIOD:
        print("[strategy] Long-term trend filter: insufficient daily history, blocking BUY")
        return False
    closes = daily_bars["close"].astype(float)
    sma = closes.rolling(LONG_TERM_TREND_PERIOD).mean().iloc[-1]
    price = closes.iloc[-1]
    if pd.isna(sma):
        print("[strategy] Long-term trend filter: SMA not yet computable, blocking BUY")
        return False
    return price > sma


def _ensure_et_index(bars: pd.DataFrame) -> pd.DataFrame:
    if not isinstance(bars.index, pd.DatetimeIndex):
        raise ValueError("bars.index must be a DatetimeIndex.")
    if bars.index.tz is None:
        bars = bars.copy()
        bars.index = bars.index.tz_localize("UTC").tz_convert("America/New_York")
    elif str(bars.index.tz) != "America/New_York":
        bars = bars.copy()
        bars.index = bars.index.tz_convert("America/New_York")
    return bars


# ── Main signal ───────────────────────────────────────────────────────────────

def generate_signal(bars: pd.DataFrame, symbol: str = None, now: datetime = None,
                     daily_bars: pd.DataFrame = None, stop_style: str = "fixed",
                     opening_rvol: float = None, min_rvol: float = 1.0) -> dict:
    """
    Opening-range breakout with full filter stack.

    daily_bars: daily-timeframe history for `symbol`, used to gate BUY
        entries with the 200-day SMA regime filter (see check_long_term_trend)
        and, when stop_style="atr", to size the stop/take distance.
        Not needed for SELL/exit evaluation.
    stop_style: "fixed" (default, unchanged live behavior) uses SYMBOL_CONFIG's
        per-symbol stop_pct/take_pct. "atr" uses get_atr_stop_take_pct instead —
        opt-in only, for backtest comparison.
    opening_rvol: precomputed value from compute_opening_rvol_series for
        `now`'s session date. None (default) disables the filter — unchanged
        live behavior. When provided, entries are rejected if opening_rvol
        < min_rvol.

    Returns:
        {
            "signal":    "BUY" | "SELL" | "HOLD",
            "size_mult": float,   # multiply base position size by this
            "stop_pct":  float,   # symbol-specific stop distance
            "take_pct":  float,   # symbol-specific take-profit distance
            "reason":    str,
        }
    """
    cfg = get_symbol_config(symbol) if symbol else DEFAULT_CONFIG
    hold = {
        "signal": "HOLD",
        "size_mult": 1.0,
        "stop_pct": cfg["stop_pct"],
        "take_pct": cfg["take_pct"],
        "reason": "",
    }

    if len(bars) < 30:
        hold["reason"] = "not enough bars"
        return hold

    if not check_time_filter(now):
        hold["reason"] = "time filter"
        return hold

    try:
        bars = _ensure_et_index(bars)
    except ValueError as e:
        hold["reason"] = f"index error: {e}"
        return hold

    if check_volatility_kill_switch(bars):
        hold["reason"] = "volatility kill switch"
        return hold

    if check_premarket_gap(bars, cfg["gap_limit"]):
        hold["reason"] = "pre-market gap too large"
        return hold

    # Opening range 9:30–10:00
    opening = bars.between_time("09:30", "10:00")
    if opening.empty:
        hold["reason"] = "opening range empty"
        return hold

    range_high = float(opening["high"].max())
    range_low  = float(opening["low"].min())
    latest_close = float(bars["close"].iloc[-1])

    # Volume confirmation — session bars only
    session = bars.between_time("09:30", "16:00")
    if not session.empty and "volume" in session.columns:
        avg_vol = session["volume"].mean()
        latest_vol = float(bars["volume"].iloc[-1])
        if latest_vol < avg_vol * cfg["vol_mult"]:
            hold["reason"] = f"volume too low ({latest_vol:.0f} < {avg_vol * cfg['vol_mult']:.0f})"
            return hold

    # Opening relative volume — opt-in, disabled unless caller passes opening_rvol
    if opening_rvol is not None and not pd.isna(opening_rvol) and opening_rvol < min_rvol:
        hold["reason"] = f"opening RVOL too low ({opening_rvol:.2f} < {min_rvol})"
        return hold

    # Breakout direction
    if latest_close > range_high:
        raw = "BUY"
    elif latest_close < range_low:
        raw = "SELL"
    else:
        hold["reason"] = f"within range [{range_low:.2f}–{range_high:.2f}]"
        return hold

    # RSI + EMA confirmation
    if not check_rsi_ema(bars, raw):
        hold["reason"] = f"{raw} rejected by RSI/EMA"
        return hold

    # Long-term trend filter — only gates new BUY entries, not exits
    if raw == "BUY" and not check_long_term_trend(daily_bars):
        hold["reason"] = "BUY rejected: below 200-day SMA (bear regime)"
        return hold

    # Time-of-day size multiplier
    size_mult, window = get_time_size_multiplier(now or datetime.now(ET))

    if stop_style == "atr":
        stop_pct, take_pct = get_atr_stop_take_pct(daily_bars, latest_close, cfg)
    else:
        stop_pct, take_pct = cfg["stop_pct"], cfg["take_pct"]

    print(f"[strategy] ✅ {raw} | symbol={symbol} | size_mult={size_mult} | {window}")

    return {
        "signal":    raw,
        "size_mult": size_mult,
        "stop_pct":  stop_pct,
        "take_pct":  take_pct,
        "reason":    f"{raw} breakout confirmed | {window}",
    }
