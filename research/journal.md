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

**Result: FAIL, nothing flips.**
- rsi2 at 2 bps: excess +6.1 bps (t 4.0, n 60,197), well short of the 15
  bps swing bar; the raw +20.7 bps net is mostly market beta.
- intraday index momentum at 0.5 bps: -0.41 bps (t -1.3), 3/11 years
  positive; worse since 2024.
- gap-and-go short-only + SSR at 1 bps: +0.11R on n=244 (< 300), one of
  five windows negative.
- Kronos standalone: +1.1 bps by arithmetic.
Logs: scripts/.cache/lowcost_*.log.

**Lesson:** costs were not what sank these; the signals are weak. Cheaper
execution is still worth having for spike_mom, but it won't revive old ideas.

## 2026-11-02 spike-size-bucket: do bigger spikes run further?

**Pre-registered.** spike_mom as live (vol >= 8x, move >= 5 sigma, signal
13:00-14:54 ET, 1-bar delay, hold 60), SIP 1-min, names in the top 25 of
the 62 by PRIOR-year dollar volume, 2017-2026. Size = move in sigmas
(|ret| / trailing std); also report by volume multiple. Tercile cutpoints
set on 2017-2021 only, applied unchanged to 2022-2026 (held out).
PASS (a reason to size up on big spikes): in 2022-26, top-minus-bottom
tercile >= +5 bps with t >= 2, AND the same sign in 2017-21. Costs cancel in
the difference; levels reported net of 2 bps/side.
Script: scripts/research/spike_size.py.

**Result: FAIL.** n=1485 trades, all +7.1 bps net (t 3.08); years
2017..2026: -2 -7 +12 +19 +10 -1 +2 -8 +24 +11.
Sigma terciles (cuts 6.50 / 9.73): design big-small +17.3 bps (t 3.29),
but held out -1.2 bps (t -0.11): q0/q1/q2 = +7.1 / +6.5 / +5.9 net. The
2017-21 size effect did not survive. Volume multiple (cuts 9.92 / 14.19):
design -2.0 (t -0.37), held out -15.1 (t -1.42); the biggest-volume third
is the worst held out (-2.9 net).
Verdict: no size-up on big spikes; keep flat sizing. Side finding: the
live pod's afternoon signal itself holds up (+7 bps net, t 3.1, positive
in both halves), consistent with the scorecard's EXPECTED_BPS=8.
Top symbols' share of gross bps: IWM 9%, GOOGL 9%, META 9%.

## 2026-11-02 pre-earnings-runup: buy the week before a forecast earnings date

**Pre-registered.** Hypothesis: stocks earn a premium in the days before a
scheduled earnings release (Barber, De George, Lehavy & Trueman 2013,
"The earnings announcement premium around the globe"; Frazzini & Lamont
2007). Events: every 8-K item 2.02 filing P for the pool (EDGAR cache).
Forecast date = P's date + 364 days (never the actual next date). Forecast
reaction session f = first session on/after that date (next session if P
was accepted after 16:00 ET). Rule: long at the open of session f-5, exit
at the close of f-1 (5 sessions; if the real release comes early, so be
it). One trade per symbol per f. Price at entry-1 close >= $5. Data: pool
daily SIP bars 2016-01..2026-10-02, forecasts from 2015+ filings. Costs 5
bps/side; scored as excess vs the equal-weight pool over the same 5
sessions (`pool_benchmark`, hold 5).
Design slice: pool ranks 1-500 (Q1-2016 dollar volume). Held-out: ranks
501-1000, run only with the same rules and reported regardless.
PASS (design slice) needs ALL: n >= 300; mean excess >= 15 bps; t >= 2.5;
positive in >= 70% of years 2016-2026; same sign 2016-20 and 2021-26; no
symbol > 25% of P&L. Also reported (not gated): win rate, median, top-1%
share of P&L, and a variant exiting at the close of f (holds through the
forecast release). Spike-pod correlation: skipped (pod P&L not here).
Script: scripts/research/pre_earnings_runup.py.

**Result: FAIL.** 45,218 filings -> 20,483 trades (design) / 19,359
(held-out), 2016-2026.

| slice | n | excess (net) | t | years+ | 16-20 / 21-26 | win | median |
|---|---|---|---|---|---|---|---|
| design 1-500, f-5 -> f-1 | 20,483 | -14.3 bps | -4.7 | 1/11 | -15 / -14 | 48% | -13.8 |
| held-out 501-1000, f-5 -> f-1 | 19,359 | -13.6 bps | -3.7 | 0/11 | -14 / -13 | 49% | -9.8 |
| variant thru f, design | 20,483 | -11.4 bps | -2.7 | 3/11 | -10 / -13 | 49% | -13.3 |
| variant thru f, held-out | 19,359 | -8.6 bps | -1.6 | 7/11 | +4 / -19 | 49% | -13.3 |

No symbol > 3% of P&L, so it is broad, not one name. Gross of the 10 bps
round trip the pre-release excess is about -4 bps: no premium at all,
before or through the release. Raw long returns (+18 bps net) are just
the pool's drift. Log: research/pre_earnings_runup.log.

**Lesson:** with PEAD also dead, earnings timing carries no daily-bar
edge in these liquid names, before, on, or after the release. Drop
earnings-calendar ideas unless they use intraday data around the print.

## 2026-11-03 spike-exit-rl: can a learned exit beat the fixed 60-min hold?

**Pre-registered.** Same trades as spike-size (spike_mom as live, top 25 of
the 62 by prior-year dollar volume, 2017-2026, enter next open + 1-bar
delay). Each trade's path is followed up to 120 min (or 15:59 ET).
Policy: batch RL (fitted value iteration / optimal stopping) with decisions
every 5 min. State = minutes held, unrealized P&L bucket (bps: <-50, -50..-20,
-20..0, 0..20, 20..50, >50), last-5-min move (down <-5 / flat / up >5 bps).
Actions hold/exit. Q(hold) = mean of the next 5 min's P&L plus next-state
value, from 2017-21 trades only; states with n < 30 fall back to the live
rule (hold to 60). Applied unchanged to 2022-26.
PASS (adopt the learned exit) needs ALL: held-out mean (policy - fixed 60)
>= +3 bps with paired t >= 2, and the same sign in 2017-21. Costs are the
same one round trip on both sides, so they cancel. Also reported (not
gated): fixed 30/90/120-min holds, average minutes held.
Script: scripts/research/spike_exit_rl.py.
