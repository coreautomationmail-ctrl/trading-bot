"""STRATTON swarm-v2: a second population, judged on its own (registry `swarm-v2`, plan
docs/swarm-plan.md section 8, owner 2026-11-18). Daily bots only, run after each close beside
swarm-v1's daily runner, sharing its rules for positions (daily_swarm.step):

  xtrend  trend following across asset classes, on 15 ETFs (the managed-futures idea)
  xsec    cross-sectional ranking of the 62 SHADOW stocks: momentum, and short-term reversal
  ml      a ridge regression, retrained each week on past data only, ranking the same stocks

Every grid point runs; no setting was chosen because it worked. v1 is untouched: its bots, files
and test stay as registered.
"""
import json
import math
import os

import daily_swarm
import spike_pod
import swarm

TEST_ID = "swarm-v2"
START = "2026-11-19"
STATE_FILE = os.path.join(spike_pod.STATE_DIR, "swarm_v2_state.json")
TRADES_CSV = os.path.join(spike_pod.STATE_DIR, "swarm_v2_daily.csv")
MANIFEST = os.path.join(spike_pod.STATE_DIR, "swarm_v2_manifest.json")
HISTORY_DAYS = 560          # calendar days: a 252-day lookback, a 252-day training window and features

ETFS = ["SPY", "QQQ", "IWM", "EFA", "EEM", "TLT", "IEF", "LQD", "HYG", "GLD", "SLV", "DBC", "USO", "UUP", "VNQ"]
STOCKS = spike_pod.SHADOW


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


class XTrend(daily_swarm.DailyBot):
    """Each ETF: long when its trend is up; short (or flat) when down. Trend = the L-day return, or
    the price against its L-day average."""
    UNIVERSE = set(ETFS)

    def __init__(self, lookback: int, rule: str, sides: str, hold: int):
        self.lookback, self.rule, self.sides, self.HOLD = lookback, rule, sides, hold
        self.NAME = f"sw2-xtrend-{rule}-{sides}-{lookback}-h{hold}"

    def signal(self, closes):
        if len(closes) <= self.lookback:
            return 0
        if self.rule == "ret":
            side = _sign(closes[-1] / closes[-1 - self.lookback] - 1)
        else:
            side = _sign(closes[-1] - sum(closes[-self.lookback:]) / self.lookback)
        return side if self.sides == "both" or side > 0 else 0


def _rank_sides(scores: dict, k: int) -> dict:
    """Long the k highest scores, short the k lowest. Ties broken by symbol, so it's repeatable."""
    if len(scores) < 2 * k:
        return {}
    order = sorted(scores, key=lambda s: (scores[s], s))
    return {**{s: -1 for s in order[:k]}, **{s: 1 for s in order[-k:]}}


class XSec(daily_swarm.DailyBot):
    """Rank the stocks by their L-day return. Momentum buys the strongest k and shorts the weakest
    k; reversal does the opposite."""
    CROSS = True
    UNIVERSE = set(STOCKS)

    def __init__(self, kind: str, lookback: int, k: int, hold: int):
        self.kind, self.lookback, self.k, self.HOLD = kind, lookback, k, hold
        self.NAME = f"sw2-xsec-{kind}-{lookback}-k{k}-h{hold}"

    def signals(self, closes: dict) -> dict:
        scores = {s: c[-1] / c[-1 - self.lookback] - 1 for s, c in closes.items() if len(c) > self.lookback}
        sides = _rank_sides(scores, self.k)
        return sides if self.kind == "mom" else {s: -v for s, v in sides.items()}


# ---------- the ridge model ----------

def features(c: list, t: int) -> list | None:
    """Features of one stock on day index t, from closes up to t only."""
    if t < 50:
        return None
    lr = lambda a, b: math.log(c[a] / c[b])   # noqa: E731
    rets = [lr(i, i - 1) for i in range(t - 19, t + 1)]
    m = sum(rets) / 20
    vol = math.sqrt(sum((r - m) ** 2 for r in rets) / 19)
    return [lr(t, t - 1), lr(t, t - 3), lr(t, t - 5), lr(t, t - 10), lr(t, t - 20), vol,
            c[t] / (sum(c[t - 49:t + 1]) / 50) - 1]


def ridge(X: list, y: list, lam: float = 1.0) -> tuple[list, list, list]:
    """Standardised ridge regression with an intercept: (coefficients, feature means, feature sds)."""
    n, p = len(X), len(X[0])
    mu = [sum(r[j] for r in X) / n for j in range(p)]
    sd = [math.sqrt(sum((r[j] - mu[j]) ** 2 for r in X) / max(1, n - 1)) or 1.0 for j in range(p)]
    Z = [[1.0] + [(r[j] - mu[j]) / sd[j] for j in range(p)] for r in X]
    A = [[sum(z[a] * z[b] for z in Z) + (lam if a == b and a > 0 else 0.0) for b in range(p + 1)] for a in range(p + 1)]
    v = [sum(z[a] * yi for z, yi in zip(Z, y)) for a in range(p + 1)]
    # Gaussian elimination with partial pivoting
    m = len(A)
    for i in range(m):
        piv = max(range(i, m), key=lambda r: abs(A[r][i]))
        A[i], A[piv], v[i], v[piv] = A[piv], A[i], v[piv], v[i]
        for r in range(i + 1, m):
            f = A[r][i] / A[i][i] if A[i][i] else 0.0
            for col in range(i, m):
                A[r][col] -= f * A[i][col]
            v[r] -= f * v[i]
    w = [0.0] * m
    for i in range(m - 1, -1, -1):
        w[i] = (v[i] - sum(A[i][j] * w[j] for j in range(i + 1, m))) / A[i][i] if A[i][i] else 0.0
    return w, mu, sd


class MLRidge(daily_swarm.DailyBot):
    """Predict each stock's next-h-day return from its own recent returns, volatility and distance
    from the 50-day average, with a ridge model trained on the last `window` days whose outcomes
    are already known. Retrained once a week; long the top k predictions, short the bottom k."""
    CROSS = True
    UNIVERSE = set(STOCKS)

    def __init__(self, h: int, k: int, window: int):
        self.h, self.k, self.window, self.HOLD = h, k, window, h
        self.NAME = f"sw2-ml-ridge-h{h}-k{k}-w{window}"
        self.model, self.trained_on = None, None

    def train(self, closes: dict):
        X, y = [], []
        for c in closes.values():
            last = len(c) - 1 - self.h                  # the newest day whose h-day outcome is known
            for t in range(max(50, last - self.window + 1), last + 1):
                f = features(c, t)
                if f:
                    X.append(f)
                    y.append(math.log(c[t + self.h] / c[t]))
        self.model = ridge(X, y) if len(X) > 50 else None

    def signals(self, closes: dict, week: str | None = None) -> dict:
        if self.model is None or week is None or week != self.trained_on:
            self.train(closes)
            self.trained_on = week
        if self.model is None:
            return {}
        w, mu, sd = self.model
        scores = {}
        for s, c in closes.items():
            f = features(c, len(c) - 1)
            if f:
                scores[s] = w[0] + sum(wj * (fj - m) / d for wj, fj, m, d in zip(w[1:], f, mu, sd))
        return _rank_sides(scores, self.k)


def build() -> list:
    bots = []
    for lookback in (63, 126, 252):
        for rule in ("ret", "ma"):
            for sides in ("long", "both"):
                for hold in (5, 21):
                    bots.append(XTrend(lookback, rule, sides, hold))
    for lookback in (20, 60, 120):
        for k in (5, 10):
            for hold in (5, 20):
                bots.append(XSec("mom", lookback, k, hold))
    for lookback in (1, 3, 5):
        for k in (5, 10):
            for hold in (1, 3):
                bots.append(XSec("rev", lookback, k, hold))
    for h in (1, 5):
        for k in (5, 10):
            for window in (126, 252):
                bots.append(MLRidge(h, k, window))
    return bots


ALL = build()
NAMES = {b.NAME for b in ALL}


class _Weekly:
    """Hands each ML bot this week's key, so it retrains once a week (and again after a restart)."""

    def __init__(self, bot, week):
        self.bot, self.week = bot, week
        self.NAME, self.HOLD, self.NIGHT, self.CROSS, self.UNIVERSE = bot.NAME, bot.HOLD, False, True, bot.UNIVERSE

    def signals(self, closes):
        return self.bot.signals(closes, self.week)


def maybe_run(now_et, append_csv, fetch=daily_swarm.fetch_daily, bots: list = None) -> int | None:
    """As daily_swarm.maybe_run, for swarm-v2's population, files and longer history."""
    from datetime import timezone

    bots = bots or ALL
    today = now_et.date().isoformat()
    if now_et.hour * 60 + now_et.minute < daily_swarm.RUN_AFTER_ET:
        return None
    state = daily_swarm.load_state(STATE_FILE)
    if state.get("last_day") == today or state.get("checked") == today:
        return None
    daily = fetch(sorted(set(ETFS) | set(STOCKS)), now_et.astimezone(timezone.utc), HISTORY_DAYS)
    if not any(bars and bars[-1]["d"] == today for bars in daily.values()):
        state["checked"] = today
        daily_swarm.save_state(state, STATE_FILE)
        return None
    week = "%d-W%02d" % now_et.isocalendar()[:2]
    runners = [_Weekly(b, week) if isinstance(b, MLRidge) else b for b in bots]
    closed = daily_swarm.step(daily, state, today, runners)
    append_csv(TRADES_CSV, daily_swarm.FIELDS, closed)
    daily_swarm.save_state(state, STATE_FILE)
    return len(closed)


def write_manifest(path: str = None):
    import registry

    names = sorted(NAMES)
    k = len(registry.load())
    path = path or MANIFEST
    with open(path + ".tmp", "w") as f:
        json.dump({"test": TEST_ID, "start": START, "bar": registry.bar(k), "k": k, "eta": swarm.ETA,
                   "lookback": swarm.LOOKBACK, "floor": swarm.FLOOR, "cap": swarm.CAP, "min_days": swarm.MIN_DAYS,
                   "kill_days": swarm.KILL_DAYS, "files": [os.path.basename(TRADES_CSV)], "names": names}, f)
    os.replace(path + ".tmp", path)


def report(state_dir: str) -> str:
    return swarm.report(state_dir, sorted(NAMES), TEST_ID, START, (os.path.basename(TRADES_CSV),))
