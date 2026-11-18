"""STRATTON swarm, intraday population (docs/swarm-plan.md section 2, fixed 2026-11-17).

Simple rule bots, each family run across a fixed grid of round settings. Every grid point runs;
none was chosen because it worked. They plug into the bench engine like any strategy (on_bar on
each completed 1-minute IEX bar), but they are inputs to the allocator test `swarm-v1`: never in
the ladder, never judged one by one, never placing paper orders. Their virtual trades go to
STATE_DIR/swarm_virtual.csv.

Each bot gives at most one signal per symbol per day, and stops signalling early enough that its
hold ends before 15:55 ET.
"""
import spike_pod

OPEN = 9 * 60 + 30
END = 15 * 60 + 52          # the last minute a hold may end on


class _Day:
    """Per-symbol running figures for today (filled by features()), shared by every bot."""

    def __init__(self):
        self.day = None
        self.sym = {}


DAY = _Day()


def _hm_of(bar: dict, ctx) -> int:
    et = ctx.et(bar["t"])
    return et.hour * 60 + et.minute


def features(sym: str, bars: list, hm: int, ctx) -> dict:
    """Day open, VWAP so far, and closes by minute, for `sym`, shared by every bot this bar."""
    f = DAY.sym.get(sym)
    if f is None or f["n"] > len(bars):
        f = DAY.sym[sym] = {"n": 0, "open": bars[0]["o"], "pv": 0.0, "v": 0.0, "by_hm": {}}
    for b in bars[f["n"]:]:
        m = _hm_of(b, ctx)
        f["pv"] += (b["h"] + b["l"] + b["c"]) / 3 * b["v"]
        f["v"] += b["v"]
        f["by_hm"][m] = b
    f["n"] = len(bars)
    f["vwap"] = f["pv"] / f["v"] if f["v"] else bars[-1]["c"]
    return f


def close_at_or_before(f: dict, hm: int):
    for m in range(hm, hm - 15, -1):
        b = f["by_hm"].get(m)
        if b:
            return b["c"]
    return None


class Bot:
    NAME = ""
    STAGE = "watch"
    COST_BPS = 2.0
    BACKTEST_NET_BPS = 0.0
    UNIVERSE = spike_pod.SHADOW
    FIRST = OPEN + 5
    SWARM = True

    def __init__(self, hold: int):
        self.HOLD_MIN = hold
        self.LAST = END - hold - 3
        self.done = set()

    def new_day(self):
        self.done = set()
        if DAY.day is not self:          # the first bot to see a new day resets the shared figures
            DAY.sym = {}
            DAY.day = self

    def on_bar(self, sym, bars, hm, ctx) -> int:
        f = features(sym, bars, hm, ctx)      # every bar, so cross-symbol bots see the whole universe
        if sym in self.done or not (self.FIRST <= hm <= self.LAST):
            return 0
        side = self.signal(sym, bars, hm, f, ctx)
        if side:
            self.done.add(sym)
        return side

    def signal(self, sym, bars, hm, f, ctx) -> int:
        return 0

    def tags(self, sym, bars, ctx):
        return {}


def _sign(x: float) -> int:
    return 1 if x > 0 else -1 if x < 0 else 0


class Gap(Bot):
    """Gap from the prior close larger than g: fade it (back toward the close) or go with it, at t."""

    def __init__(self, g: float, mode: str, at: int, hold: int):
        super().__init__(hold)
        self.g, self.mode, self.at = g, mode, at
        self.FIRST = at
        self.NAME = f"sw-gap-{mode}-{g:g}-{at // 60:02d}{at % 60:02d}-h{hold}"

    def signal(self, sym, bars, hm, f, ctx):
        if hm > self.at + 10:
            self.done.add(sym)       # too late today
            return 0
        prior = ctx.prior_close(sym)
        if not prior:
            return 0
        gap = f["open"] / prior - 1
        if abs(gap) * 100 <= self.g:
            self.done.add(sym)
            return 0
        return -_sign(gap) if self.mode == "fade" else _sign(gap)


class Orb(Bot):
    """The first close outside the first r minutes' high-low range: go with the break or fade it."""

    def __init__(self, r: int, mode: str, hold: int):
        super().__init__(hold)
        self.r, self.mode = r, mode
        self.FIRST = OPEN + r
        self.NAME = f"sw-orb-{mode}-{r}-h{hold}"

    def signal(self, sym, bars, hm, f, ctx):
        rng = [b for m, b in f["by_hm"].items() if m < OPEN + self.r]
        if not rng:
            self.done.add(sym)
            return 0
        hi, lo = max(b["h"] for b in rng), min(b["l"] for b in rng)
        c = bars[-1]["c"]
        side = 1 if c > hi else -1 if c < lo else 0
        return side if self.mode == "go" else -side


class Vwap(Bot):
    """After 10:30, price more than d from the day's VWAP: revert toward it or follow away from it."""

    def __init__(self, d: float, mode: str, hold: int):
        super().__init__(hold)
        self.d, self.mode = d, mode
        self.FIRST = 10 * 60 + 30
        self.NAME = f"sw-vwap-{mode}-{d:g}-h{hold}"

    def signal(self, sym, bars, hm, f, ctx):
        dev = bars[-1]["c"] / f["vwap"] - 1
        if abs(dev) * 100 <= self.d:
            return 0
        return -_sign(dev) if self.mode == "revert" else _sign(dev)


class Trend(Bot):
    """Return over the last L minutes above x, on the same side of VWAP: follow it or fade it."""

    def __init__(self, lookback: int, x: float, mode: str, hold: int):
        super().__init__(hold)
        self.lookback, self.x, self.mode = lookback, x, mode
        self.FIRST = OPEN + lookback
        self.NAME = f"sw-trend-{mode}-{lookback}-{x:g}-h{hold}"

    def signal(self, sym, bars, hm, f, ctx):
        then = close_at_or_before(f, hm - self.lookback)
        if not then:
            return 0
        c = bars[-1]["c"]
        ret = c / then - 1
        if abs(ret) * 100 <= self.x or _sign(c - f["vwap"]) != _sign(ret):
            return 0
        return _sign(ret) if self.mode == "follow" else -_sign(ret)


class Late(Bot):
    """At time t, the universe's biggest mover from its open: fade it or follow it. Decided from the
    first bar 5+ minutes after t, when every symbol's bar at t has been fed."""

    def __init__(self, at: int, mode: str, hold: int):
        super().__init__(hold)
        self.at, self.mode = at, mode
        self.FIRST = at + 5
        self.LAST = min(self.LAST, at + 20)
        self.NAME = f"sw-late-{mode}-{at // 60:02d}{at % 60:02d}-h{hold}"
        self.pick = None

    def new_day(self):
        super().new_day()
        self.pick = None

    def signal(self, sym, bars, hm, f, ctx):
        if self.pick is None:
            moves = {}
            for s, g in DAY.sym.items():
                c = close_at_or_before(g, self.at)
                if c:
                    moves[s] = c / g["open"] - 1
            self.pick = max(moves.items(), key=lambda kv: abs(kv[1])) if moves else ("", 0.0)
        name, move = self.pick
        if sym != name or not move:
            return 0
        return -_sign(move) if self.mode == "fade" else _sign(move)


def build() -> list:
    bots = []
    for g in (0.5, 1, 2):
        for mode in ("fade", "go"):
            for at in (9 * 60 + 35, 9 * 60 + 45):
                for hold in (30, 60, 120):
                    bots.append(Gap(g, mode, at, hold))
    for r in (5, 15, 30):
        for mode in ("go", "fade"):
            for hold in (30, 60, 120):
                bots.append(Orb(r, mode, hold))
    for d in (1, 1.5, 2.5):
        for mode in ("revert", "follow"):
            for hold in (30, 60):
                bots.append(Vwap(d, mode, hold))
    for lookback in (30, 60):
        for x in (0.5, 1):
            for mode in ("follow", "fade"):
                for hold in (30, 60, 120):
                    bots.append(Trend(lookback, x, mode, hold))
    for at in (14 * 60, 14 * 60 + 30):
        for mode in ("fade", "follow"):
            for hold in (30, 60):
                bots.append(Late(at, mode, hold))
    return bots


ALL = build()
NAMES = {b.NAME for b in ALL}
