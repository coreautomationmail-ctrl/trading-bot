# STRATTON research journal

One entry per idea: pre-registered rules and gate first, results appended
after. Never edit a gate after seeing results. Costs per research/agent.md.

## Before this journal (2026-09 .. 2026-10, see git history and scripts/)

| Idea | Script | Verdict |
|---|---|---|
| Fixed-list ORB (live since 9/14) | backtest_sip.py | no edge at real cost (few bps gross) |
| Stocks-in-play ORB | backtest_sip.py | no edge at real cost |
| Gap-and-go, incl. SSR-filtered gap-down shorts | backtest_gap.py | best +0.13R at 5 bps, 3/5 windows; not adopted |
| Index intraday momentum | backtest_intraday_mom.py | no edge at real cost |
| Event drift (any >=5% move on >=3x volume) | backtest_swing.py | no excess vs pool |
| RSI2 dip-buying | backtest_swing.py | +6 bps excess < 10 bps cost: beta |
| spike_fade, vwap_fade | backtest_scalp.py | negative |
| **spike_mom** (vol >=8x, move >=5 sigma, hold 60) | backtest_scalp.py, spike_buckets.py | **PASS** afternoon, top-25 dollar volume: +15.8 bps, t 2.7 (2022-26); live paper pod since 2026-10-30 |
| Kronos-small standalone forecasts | kronos/backtest_kronos.py | best +3.1 bps gross, below bar |
| Nautilus cross-check of spike_mom fills | nautilus_spike.py | harness reproduced (236 vs 235 trades) |
| skfolio allocation (spike vs sleeve) | allocator_skfolio.py | inverse-vol best: Sharpe 1.01, maxDD -6.9% |

## 2026-11-02 pead-real-dates: post-earnings drift on SEC 8-K item 2.02 dates

**Pre-registered.** Events: 8-K filings with item 2.02 for the 1,000-name
pool (45,218 filings, 925 names); acceptance time after 16:00 ET -> next
session reacts. EAR = reaction-session return minus the pool's. Trade in
EAR's direction when |EAR| >= 4% (also 8%), enter next open, hold
5/10/20/40/60 sessions, 5 bps/side, scored as excess vs the pool. PASS per
side/hold: excess >= 15 bps, t >= 2.5, >= 7/10 years positive, same sign
2016-20 and 2021-26. Script: scripts/backtest_pead.py.

**Result: FAIL.** 16,411 events at 4%, 6,724 at 8%. Every side x hold has
excess between -29 and +13 bps with |t| <= 2.3 (the only |t| > 2 values are
negative: longs underperform). Raw long returns look good (+106 bps at 20
days) but that is the market's rise; shorts lose money outright. Matches the
literature that drift has largely disappeared in large, liquid names since
the 2000s. Survivor-only pool, so if anything this flatters longs.
Logs: scripts/.cache/pead_ear4.log, pead_ear8.log.

**Lesson:** event drifts in liquid US names are priced in within a session.
Earnings timing may still matter BEFORE the release (idea 1, pre-earnings
run-up), which uses the same EDGAR cache.

## 2026-11-02 kronos-spike-filter: Kronos-small as a filter on spike_mom

**Pre-registered.** For every spike_mom trade 2025-26 (all day, 1-bar
delay), forecast the next 12 five-minute bars with Kronos-small from the
400 bars up to the signal. "Agree" = forecast return points the trade's
way. PASS: agree minus disagree >= 0 with t >= 2 in the afternoon (13:00+)
AND before 13:00. Script: scripts/kronos/kronos_spike_filter.py.

**Result: FAIL.** n=1,755 (8 early-2025 trades lacked 400 bars of history).

| window | agree | disagree | diff (t) |
|---|---|---|---|
| afternoon | +11.0 bps (n 313) | +22.2 bps (n 638) | -11.2 (-1.55) |
| before 13:00 | -7.8 bps (n 283) | +2.7 bps (n 521) | -10.5 (-1.81) |
| all day | +2.1 | +13.4 | -11.4 (-2.39) |

Kronos's agreement doesn't help; if anything the trades it disagrees with
do better (same sign in both windows, neither t >= 2; rank IC -0.008, so
no graded relationship). Likely mechanism: Kronos forecasts reversal after
big jumps, so "disagree" mostly marks the largest spikes, and larger
spikes continue more. That is testable without Kronos (next idea).

**Lesson:** a generic price-forecasting model adds nothing to a
volume-shock signal; don't spend more CPU on Kronos filters.

## 2026-11-02 low-cost-rerun: do near-misses pass with limit-order costs?

**Pre-registered.** Re-run the closest past failures at the cost a
patient limit-order entry might achieve: intraday 0.5-1 bps/side (half the
default), swing 2 bps/side (vs 5). Same gate as research/agent.md otherwise
(n >= 300, net >= 3 bps intraday / >= 15 bps swing excess, t >= 2.5, >= 70%
of years positive, both halves same sign). A flip is only a CANDIDATE: it
becomes a pod only if paper fills prove the lower cost (limit orders also
miss fills, mostly on the trades that would have won).
Runs: rsi2 at 2 bps (swing); intraday index momentum at 0.5 bps; gap-and-go
short-only with SSR at 1 bps. Kronos standalone needs no run: +3.1 bps gross
minus 2 x 1 bps = +1.1 bps < 3.
