"""
Cross-check the spike_mom backtest with NautilusTrader's engine (run with .nautilus_venv).

Same detector as the live pod (pods/spike/spike_pod.SymState), 1-min SIP
bars from the backtest_scalp cache, 12 core names, 2025-2026 (out-of-sample
for the parameter choice). Signal on bar i -> market order on bar i+1 (fills
at bar i+1's close ~= bar i+2's open, i.e. the harness's delay=1) -> market
exit HOLD bars after the signal. Nautilus adds: real order/position
lifecycle, one-tick slippage on PROB_SLIPPAGE of market fills.

Compare with: backtest_scalp sweep row 8 / 5 / 60 / delay 1 on the same names+years.

Usage: python scripts/nautilus_spike.py [prob_slippage]
"""
import os

import sys
from datetime import datetime, timezone

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "pods", "spike"))

import numpy as np
import pandas as pd
from nautilus_trader.backtest.engine import BacktestEngine, BacktestEngineConfig
from nautilus_trader.backtest.models import FillModel
from nautilus_trader.config import LoggingConfig, StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.objects import Money
from nautilus_trader.persistence.wranglers import BarDataWrangler
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy

import spike_pod as pod

CACHE = os.path.join(ROOT, "scripts", ".cache")
SYMBOLS = ["SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMZN", "TSLA",
           "META", "AMD", "GOOGL", "JPM"]
YEARS = (2025, 2026)
HOLD = 60
POS_USD = 5_000
LAST_SIGNAL = 15 * 60 + 59 - (HOLD + 1)   # harness drops trades whose exit bar crosses 15:59
VENUE = Venue("XNAS")


class SpikeConfig(StrategyConfig, frozen=True):
    bar_types: list


class Spike(Strategy):
    def __init__(self, config: SpikeConfig):
        super().__init__(config)
        self.sym_state = {}       # instrument_id -> SymState
        self.n = {}           # bars seen per instrument
        self.pending = {}     # instrument_id -> side to enter on the next bar
        self.exit_at = {}     # instrument_id -> bar count at which to exit
        self.entry_px = {}
        self.trades = []
        self.sigs = []

    def on_start(self):
        for bt in self.config.bar_types:
            self.subscribe_bars(BarType.from_str(bt))

    def on_bar(self, bar: Bar):
        iid = bar.bar_type.instrument_id
        st = self.sym_state.setdefault(iid, pod.SymState())
        n = self.n[iid] = self.n.get(iid, -1) + 1
        ts = datetime.fromtimestamp(bar.ts_event / 1e9, tz=timezone.utc).astimezone(pod.ET)
        sig = st.update(ts, float(bar.open), float(bar.close), float(bar.volume))
        inst = self.cache.instrument(iid)
        if iid in self.pending:                       # enter one bar after the signal
            side = self.pending.pop(iid)
            qty = inst.make_qty(max(1, int(POS_USD / float(bar.close))))
            self.submit_order(self.order_factory.market(iid, OrderSide.BUY if side > 0 else OrderSide.SELL, qty))
            return
        if iid in self.exit_at and n >= self.exit_at[iid]:
            self.exit_at.pop(iid)
            self.close_all_positions(iid)
            return
        if sig:
            self.sigs.append((str(iid.symbol), ts.isoformat()))
        if sig and iid not in self.exit_at and ts.hour * 60 + ts.minute <= LAST_SIGNAL:
            self.pending[iid] = sig
            self.exit_at[iid] = n + HOLD + 1

    def on_order_filled(self, event):
        iid = event.instrument_id
        px = float(event.last_px)
        side = 1 if event.order_side == OrderSide.BUY else -1
        if iid not in self.entry_px:
            self.entry_px[iid] = (side, px)
        else:
            s, e = self.entry_px.pop(iid)
            self.trades.append((str(iid.symbol), s * np.log(px / e) * 1e4))


def load(sym):
    # nt_*.npz: the m1 pickles re-saved as plain arrays (the pickles need pandas 3,
    # nautilus pins an older pandas)
    frames = []
    for y in YEARS:
        z = np.load(os.path.join(CACHE, f"nt_{sym}_{y}.npz"))
        frames.append(pd.DataFrame(z["v"], columns=["open", "high", "low", "close", "volume"],
                                   index=pd.to_datetime(z["ts"], utc=True)))
    return pd.concat(frames)


def main():
    prob_slip = float(sys.argv[1]) if len(sys.argv) > 1 else 0.5
    engine = BacktestEngine(BacktestEngineConfig(logging=LoggingConfig(log_level="ERROR")))
    engine.add_venue(venue=VENUE, oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
                     base_currency=USD, starting_balances=[Money(10_000_000, USD)],
                     fill_model=FillModel(prob_slippage=prob_slip, random_seed=42),
                     bar_execution=True)
    bar_types = []
    for sym in SYMBOLS:
        inst = TestInstrumentProvider.equity(symbol=sym, venue="XNAS")
        engine.add_instrument(inst)
        bt = BarType.from_str(f"{sym}.XNAS-1-MINUTE-LAST-EXTERNAL")
        engine.add_data(BarDataWrangler(bt, inst).process(load(sym)))
        bar_types.append(str(bt))
        print(f"loaded {sym}", flush=True)
    strat = Spike(SpikeConfig(bar_types=bar_types))
    engine.add_strategy(strat)
    engine.run()
    pd.DataFrame(strat.sigs, columns=["sym", "ts"]).to_csv(os.path.join(CACHE, "nt_sigs.csv"), index=False)
    t = pd.DataFrame(strat.trades, columns=["sym", "bps"])
    print(f"\nNautilus  prob_slippage={prob_slip}: {len(t)} trades, gross {t['bps'].mean():+.2f} bps/trade, "
          f"median {t['bps'].median():+.2f}, t {t['bps'].mean() / t['bps'].std() * np.sqrt(len(t)):.2f}")
    print(t.groupby("sym")["bps"].agg(["count", "mean"]).round(1).to_string())


if __name__ == "__main__":
    main()
