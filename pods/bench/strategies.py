"""The shadow bench's strategies: one class per idea, each a plain rule on 1-min bars.

A strategy says when to trade; the engine (bench.py) does everything else: the
virtual fill at the open two minutes after the signal bar (the backtests' one-bar
delay), the exit HOLD_MIN minutes later, costs, logging and, for strategies the
ladder has promoted, paper orders.

STAGE is where the strategy starts on the evidence ladder (ladder.py):
  watch       a near-miss: logged forward, never promoted by forward results.
              Only a fresh pre-registered backtest PASS moves it, by editing STAGE.
  shadow      passed its backtest: virtual trades, promoted by the ladder's rules
  incubation  small paper orders; pod: full paper size; retired: not run
Changing STAGE here is the only manual move. BACKTEST_NET_BPS is the backtest's
net result per trade, copied from the journal and never changed afterwards.

Adding a strategy: subclass Strategy, append it to ALL, and give it a journal
entry. Rules see only bars up to and including the one just completed.
"""
import json
import math
import os

import spike_pod

VOL_X, K = spike_pod.VOL_X, spike_pod.K
# The 277 names with cached 1-min history (top-200 dollar volume lists): what research tests on,
# so a strategy that passed there is watched on all of them. IEX serves them in one request.
WIDE = json.load(open(os.path.join(os.path.dirname(os.path.abspath(spike_pod.__file__)), "missed_universe.json")))


class Strategy:
    NAME = ""
    STAGE = "watch"
    HOLD_MIN = 60
    COST_BPS = 2.0                  # per side, the desk's intraday gate
    BACKTEST_NET_BPS = 0.0
    FIRST, LAST = 9 * 60 + 35, 14 * 60 + 54   # signal window, ET minutes
    UNIVERSE = spike_pod.SHADOW
    JOURNAL = ""

    def new_day(self):
        """Called before a session's first bar."""

    def on_bar(self, sym: str, bars: list, hm: int, ctx) -> int:
        """`bars` is today's completed bars for sym, oldest first; return +1 / -1 to trade
        in that direction from the next bar, 0 to pass. `ctx` gives news lookups."""
        return 0

    def tags(self, sym: str, bars: list, ctx) -> dict:
        """Extra columns saved with each signal."""
        return {}


class SpikeNews(Strategy):
    """Spike momentum on a bar with a single-name headline in the prior 60 minutes.
    Near-miss 2026-11-06: news +15.9 net vs no news +6.5 on rank 1-25, gap t 1.54 < 2.
    Reopens if forward signals show the same gap (beliefs.json spike-news), which is what
    this and SpikeQuiet record together."""
    NAME = "spike-news"
    BACKTEST_NET_BPS = 15.9
    FIRST, LAST = 9 * 60 + 35, 14 * 60 + 58   # the spike shadow log's window
    JOURNAL = "2026-11-06 spike-news"
    WANT_NEWS = True

    def new_day(self):
        self.state = {}

    def on_bar(self, sym, bars, hm, ctx):
        st = self.state.setdefault(sym, spike_pod.SymState(self.FIRST, self.LAST))
        b = bars[-1]
        side = st.update(ctx.et(b["t"]), b["o"], b["c"], b["v"])
        if not side:
            return 0
        self.last_sigma = abs(math.log(b["c"] / b["o"])) / st.last_std if st.last_std else None
        return side if ctx.has_news(sym, b["t"], 60) == self.WANT_NEWS else 0

    def tags(self, sym, bars, ctx):
        return {"sigma": round(self.last_sigma, 2) if self.last_sigma else ""}


class SpikeQuiet(SpikeNews):
    """The same spikes without a headline: the comparison group for spike-news."""
    NAME = "spike-quiet"
    BACKTEST_NET_BPS = 6.5
    WANT_NEWS = False


ALL = [SpikeNews(), SpikeQuiet()]
