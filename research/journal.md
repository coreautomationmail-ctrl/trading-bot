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

**Result: FAIL.** n=1485 (806 design / 679 held out); 228 states had
n >= 30 and 71 of them learned "exit".
Policy minus fixed 60 (gross; costs cancel): design +2.2 bps (paired t 2.89),
held out +1.9 bps (t 1.35); same sign, but below +3 / t 2. Policy averages
~43 min held in both periods. Held-out diff by year: 2022 +11.2, 2023 +4.0,
2024 -3.4, 2025 +1.7, 2026 -3.0.
Fixed holds (gross bps), design / held out: 30 min +6.1 / +14.2, 60 min +11.6 /
+10.4, 90 min +4.5 / +12.1, 120 min +3.6 / +12.6. No fixed length beats 60
in both periods, so the held-out edge of 30 min is noise, not a rule.
Verdict: keep the fixed 60-min hold. The learned exit is a small, real-looking
gain (+2 bps both periods), too weak to adopt; revisit once the 215 extra
names (~4x trades) are cached; same rules, then a fresh held-out run.

## 2026-11-04 spike-exit-rl-wide: the learned exit on the wider universe

**Pre-registered** (revisit planned in spike-exit-rl). Identical rules, state
grid, MIN_N, design/held-out split and PASS bar as spike-exit-rl; only the
universe widens: the 62 + the 215 extra names (top-200 dollar volume in
2017/2021/2025), top 100 by prior-year dollar volume (vs top 25), so ~4x trades.
Policy learned on 2017-21 only, applied unchanged to 2022-26.
The 2022-26 trades of the original top-25 run were already seen, so the
held-out set is also reported without them ("fresh": a (name, year) not in
that run's top 25).
PASS needs ALL: held-out mean (policy - fixed 60) >= +3 bps with paired
t >= 2; the same sign in 2017-21; the same sign on the fresh held-out trades.
Script: scripts/research/spike_exit_rl.py --wide.

**Result: FAIL.** The top-25 rerun reproduces spike-exit-rl exactly (+2.2 / +1.9).
Wide: n=5498 (2832 design / 2666 held out), 395 states with n >= 30, 98 learned "exit".
Policy minus fixed 60 (gross): design +1.6 bps (t 3.00), held out -1.2 (t -1.52),
fresh held out (n=1987) -0.9 (t -1.05). Sign flips out of sample.
Held-out diff by year: 2022 -5.7, 2023 +1.6, 2024 +2.5, 2025 -3.5, 2026 -0.5.
Fixed holds (gross bps), design / held out: 30 min +3.1 / +10.3, 60 +6.6 / +8.7,
90 +1.7 / +9.9, 120 +1.3 / +10.8.
Verdict: the learned exit is in-sample fitting; keep the fixed 60-min hold and
close this line. Not gated, noted: the fixed-60 spike trade stays positive gross
on the top 100 (+6.6 / +8.7 bps), weaker than on the top 25 (+11.6 / +10.4).

## 2026-11-03 short-term-reversal: buy last week's losers, short its winners

**Pre-registered.** Hypothesis: 1-week returns reverse (Lehmann 1990;
Jegadeesh 1990), driven by liquidity provision and overreaction; recent
work says the effect survives costs in large caps (De Groot, Huij & Zhou,
JBF 2012). Data: pool daily SIP bars 2016-01..2026-10-02.
Rules: formation every 5th session t (non-overlapping weeks). Eligible:
close[t] >= $5, close[t-5], open[t+1], close[t+5] present. r5 =
close[t]/close[t-5] - 1, information known at the close of t. Within the
slice, long the bottom 5% by r5, short the top 5%. Enter at the open of
t+1, exit at the close of t+5 (5 sessions). Each name-week is one trade.
Costs 5 bps/side. Score: long excess = ret - pool5, short excess = pool5 -
ret, minus 10 bps (pool5 = `pool_benchmark` hold 5 on the same entry day).
Design slice: pool ranks 1-500 (Q1-2016 dollar volume); held-out: ranks
501-1000, same rules, ranked within the held-out slice, reported
regardless. PASS (design, longs+shorts pooled) needs ALL: n >= 300; mean
excess >= 15 bps; t >= 2.5; positive in >= 70% of years 2016-2026; same
sign 2016-20 and 2021-26; no symbol > 25% of P&L. Not gated, reported:
long and short legs separately; split by trailing 60-day volatility
(above/below the slice median at t; idea 11); win rate, median, top-1%
share. t is per trade (trades in a week are correlated, so it flatters);
also report t on weekly means. Spike-pod correlation: skipped (pod P&L not
here). Caveat: survivor-only pool flatters the long (loser) leg.
Script: scripts/research/short_term_reversal.py.

**Result: FAIL.** 528 weeks, 2016-2026; 25,372 trades (design) / 24,850
(held-out), half long, half short.

| slice / leg | n | excess (net) | t (weekly t) | years+ | 16-20 / 21-26 |
|---|---|---|---|---|---|
| design, long+short | 25,372 | -4.9 bps | -0.99 (-0.56) | 4/11 | -7 / -3 |
| design, long losers | 12,686 | -0.7 | -0.13 | 6/11 | -7 / +4 |
| design, short winners | 12,686 | -9.2 | -1.09 | 4/11 | -7 / -11 |
| held-out, long+short | 24,850 | +4.0 bps | +0.89 (+0.54) | 6/11 | +9 / 0 |
| held-out, long losers | 12,425 | +1.7 | +0.26 | 5/11 | +17 / -11 |
| held-out, short winners | 12,425 | +6.3 | +1.00 | 6/11 | 0 / +11 |

Design win rate 49%, median -4.2 bps; top symbol 2% of P&L (broad);
top-1% share meaningless with a negative total. Gross of the 10 bps round
trip the design slice earns about +5 bps a week: the reversal exists but
is far below cost, and the year signs flip (2021 -41, 2022 +17).
Volatility split (idea 11, not gated): design low-vol half +9.8 bps (t 1.66,
n 4,752) vs high-vol -8.3, which matches Wei & Yang's direction, but the
held-out slice shows no such gap (low +2.5, high +4.3). Not a lead.
Log: research/short_term_reversal.log.

**Lesson:** weekly reversal in liquid US names is a few bps gross, well
under swing costs. Idea 9 (reversal near 52-week high) and idea 11 build on
the same weak base; skip them unless a cheaper-cost variant is pre-registered.

## 2026-11-04 spike-wide-universe: does the live signal work on more names?

**Pre-registered.** spike_mom as live (vol >= 8x, move >= 5 sigma, signal
13:00-14:54 ET, 1-bar delay, hold 60), SIP 1-min, on the 62 + the 215 extra
names, 2017-2026. Test group: rank 26-100 by PRIOR-year dollar volume (rank
1-25, the pod's pool, is reference only). Costs 2 bps/side.
PASS (a case for adding names to the paper pod) needs ALL, on 2022-26 rank
26-100: n >= 300; mean >= +5 bps net; t >= 2.5; positive in >= 4 of 5 years;
top symbol <= 25% of positive gross bps; AND 2017-21 mean > 0.
Reported, not gated: 4 bps/side, the pod's 4-position cap on rank 1-100.
Not a clean first look: spike-exit-rl-wide's averages imply this group earned
about +0.6 net in 2017-21 and +4 net in 2022-26, so the +5 bar was set after
seeing those. Caveats: the extra names were picked by top-200 dollar volume in
2017/2021/2025 (selection uses future lists); live IEX detection has about
halved the edge; both flatter this test.
Script: scripts/research/spike_wide.py.

**Result: FAIL.** Rank 26-100 held out: n=2019, +3.3 bps net (t 1.28), positive 2 of 5 years
(2022 -3.6, 2023 -3.8, 2024 -13.1, 2025 +23.2, 2026 +4.1); -0.7 at 4 bps/side. Design: n=2035, +0.4 (t 0.22).
Reference rank 1-25: design +8.2 (t 3.45), held out +9.2 (t 1.85).
With the pod's 4-position cap, pooling rank 1-100 takes 2035 of 2666 held-out trades at +0.1 net:
total +193 net bps vs +5958 for top 25 alone, because the weaker names crowd out better ones.
Verdict: keep the pod's 17 names. More names add trades, not edge. Log: research/spike_wide.log.
## 2026-11-04 overnight-drift: hold the liquid names close-to-open only

**Pre-registered.** Hypothesis: most US equity returns accrue overnight
(close to next open) while the trading day earns about nothing (Lou, Polk &
Skouras, "A tug of war", JFE 2019; Cliff, Cooper & Gulen 2008). Question: is
the overnight share large enough to trade with MOC-in / MOO-out costs?
Data: 1-min SIP, regular session, all 11 years 2016-2026 cached for the 62
(`SYMBOLS + WIDE`) and the 215 extra names (checked: 277 files per year).
Rules: every session d and name: buy at the close of the 15:59 bar of d
(MOC proxy), sell at the open of the first bar (09:30) of the name's next
session (MOO proxy). One trade per name-night, weekends/holidays included.
Net = ln(open/close) - 2 x 2 bps. Only full sessions on both sides (a
session with a 15:59 bar; the next session's first bar at 09:30).
Design slice: the 62 names. Held-out: the 215 extra names, each year only
the top 100 of them by PRIOR-year dollar volume (so held-out starts in
2017; no 2015 data cached), same rules, reported regardless.
PASS (design) needs ALL: n >= 300; mean net >= 3 bps; t >= 2.5 where t is
on the nightly equal-weight means (names on one night are correlated, so a
per-trade t would flatter; per-trade t also reported); positive in >= 70% of
years; same sign 2016-20 and 2021-26; no symbol > 25% of P&L. Added
(stricter, set now): the SPY-hedged version (stock overnight minus SPY's
same-night overnight return, costs on the stock leg only) must also have
mean > 0 with nightly t >= 2, so a pass isn't just overnight market beta.
Not gated, reported: open-to-close (09:30 open -> 15:59 close) on the same
names and costs; win rate, median, top-1% share; by year. Spike-pod
correlation: skipped (pod P&L not here). Caveats: the first-bar open and
15:59 close are trade prices, not the official auction prints; overnight
holding also carries gap risk the gate doesn't price.
Script: scripts/research/overnight_drift.py.

**Result: FAIL.** Design: 62 names, 2,715 nights, 165,758 name-nights.
Held-out: 180 distinct extra names (top 100 per year), 2017-2026, 239,339.

| slice / version | n | mean | nightly t | years+ | 16-20 / 21-26 |
|---|---|---|---|---|---|
| design, overnight long, net (gated) | 165,758 | -2.35 bps | -1.49 | 4/11 | -0.5 / -4.0 |
| design, overnight long, gross | 165,758 | +1.65 | +1.14 | 9/11 | +3.6 / 0.0 |
| design, SPY-hedged, net | 165,758 | -5.39 | -8.59 | 0/11 | -3.7 / -6.8 |
| design, day part long, net | 165,889 | -3.19 | -2.05 | 2/11 | -4.0 / -2.5 |
| held-out, overnight long, net | 239,339 | -1.86 | -1.04 | 4/10 | +1.0 / -3.8 |
| held-out, overnight long, gross | 239,339 | +2.14 | +1.28 | 9/10 | +5.0 / +0.2 |
| held-out, SPY-hedged, net | 239,339 | -5.44 | -10.15 | 0/10 | -3.6 / -6.7 |

Design win rate 50%, median +0.5 bps; AMD alone is 43% of the (negative)
P&L, stocks-only is the same (-2.5 bps). The overnight share is real but
tiny: about +2 bps gross a night, below the 4 bps round trip, and it
vanished after 2020 (+0.0 / +0.2 gross in 2021-26, 2022 -11). Hedged
against SPY's own overnight move it is negative every year, so the
overnight gross is market beta, not a stock-level effect. The day part also
loses net. Spike-pod correlation: skipped (pod P&L not here).
Log: research/overnight_drift.log.

**Lesson:** the "all returns come overnight" pattern in liquid names is a
couple of bps a night and has faded since 2021; any once-a-day round trip at
2 bps/side kills it. Overnight ideas need a cross-sectional signal (idea 10)
that earns well over 4 bps, not the unconditional drift.

## 2026-11-05 turn-of-month: long from the last session of the month through the 3rd

**Pre-registered.** Hypothesis: stocks earn most of their return in a four-day
window around the month turn (Lakonishok & Smidt 1988; McConnell & Xu, "Equity
returns at the turn of the month", FAJ 2008). Recent work disputes it: Han, Han &
Tian (Finance Research Letters 2025) say it vanished after 2001. Proposed reasons
(month-end flows, liquidity needs, settlement) are contested.
Data: 1-min SIP, all 11 years 2016-2026 cached for the 62 and the 215 extra names.
Rules: calendar = SPY's sessions (known in advance). Each month with a full next
month start: buy the 09:30 bar's open of the month's last session, sell the last
bar's close of the 3rd session of the next month (4 sessions, half-days allowed).
A name needs a 09:30 bar on entry and a bar on exit. Prices are unadjusted (as
the cache holds them); SPY/QQQ/IWM ex-dividend days fall outside this window.
Costs 5 bps/side (swing).

The queue says SPY/QQQ, but 2 ETFs over ~128 months can't meet n >= 300 or
"no symbol > 25%", so the gated slice is the 62 names (which include SPY, QQQ,
IWM). SPY and QQQ are still reported on their own.

**Official gate (research/agent.md, unchanged), the 62, score = ret - pool's
same-window return (`pool_benchmark` hold 4, ends 2026-10-02) - 10 bps:** n >= 300;
mean >= 15 bps; t >= 2.5; positive in >= 70% of years; same sign 2016-20 and
2021-26; no symbol > 25% of P&L. t is on monthly means (names in a month are
correlated; per-trade t also shown). The verdict comes from this gate.
Known limit, stated now: the effect is market-wide, so subtracting the pool's
same-window return removes most of it by construction. This score mainly asks
whether the 62 beat the pool at the turn.

**Second score, same thresholds, NOT the official gate:** timing = ret - 10 bps
- the same name's same-year mean 4-session open-to-close return over windows
that don't touch a turn-of-month session (gross: holding through needs no trade).
This asks the actual question, whether the window beats ordinary days. If it clears
the thresholds while the official gate fails, the verdict stays FAIL and it is
flagged for the owner to decide, not treated as a pass.

Held-out: the 215 extra names, each year the top 100 by PRIOR-year dollar volume
(starts 2017), same rules and both scores, reported regardless.
Not gated, reported: SPY and QQQ alone (raw net, both scores, also at 2 bps/side);
a 7-session window (3rd-last session through the 3rd); win rate, median, top-1%
share, by year. Spike-pod correlation: skipped (pod P&L not here). Caveats:
survivor-biased names flatter longs; about 128 months is only 128 independent bets
whatever n says, which is why t is monthly.
Script: scripts/research/turn_of_month.py.

**Correction after the first run (gates unchanged).** The first run failed the
official gate at -0.3 bps with GE 53% of P&L, which exposed a data error: the
1-min cache is unadjusted, so GE's 1:8 reverse split (2021-08-02) scored +682% and
BKNG's 25:1 (2026-04-06) -96%. "Prices are unadjusted" above was wrong to accept.
Fix: drop any window (traded or baseline) containing an overnight gap, open /
previous close, above 1.45x or below 0.68x. These bounds target split ratios and
were chosen after seeing the list of gaps, not the corrected results, but the first
run's numbers were seen. Side effect: real gaps that large are dropped too, in both
directions. Smaller splits and spin-offs can get through. Also reported, not gated:
excess vs a pool benchmark with the same filter, because `pool_benchmark` is also
on raw daily bars (it clips, but splits still reach it). The official score keeps
`pool_benchmark` as written.

**Result: FAIL (official gate), and the second score fails too.** 129 month turns,
2016-01..2026-09; the excess scores lose the last one (pool data ends 2026-10-02).
4-session window, after the split filter, all net of 10 bps; t is monthly:

| slice / score | n | mean | t | years+ | 16-20 / 21-26 | win | median |
|---|---|---|---|---|---|---|---|
| the 62, excess vs pool (OFFICIAL) | 7,857 | -7.2 bps | -1.04 | 4/11 | -6.0 / -8.3 | 48% | -13.1 |
| the 62, excess vs filtered pool | 7,857 | -5.4 | -0.77 | 4/11 | -6.2 / -4.6 | 48% | -12.0 |
| the 62, timing (second score) | 7,919 | +8.3 | +0.46 | 5/11 | +19.7 / -1.7 | 50% | +2.6 |
| the 62, raw long | 7,919 | +22.4 | +1.27 | 8/11 | +28.8 / +16.8 | 53% | +20.4 |
| SPY raw long | 129 | +22.5 | +1.29 | 9/11 | +27.8 / +17.8 | 58% | +39.0 |
| SPY timing | 129 | +10.3 | +0.58 | 4/11 | +19.9 / +2.0 | 53% | +21.9 |
| QQQ raw long | 129 | +27.4 | +1.22 | 7/11 | +36.6 / +19.4 | 56% | +41.4 |
| QQQ timing | 129 | +7.4 | +0.33 | 3/11 | +16.4 / -0.5 | 55% | +23.8 |
| held-out, excess vs pool | 11,358 | -4.2 | -0.55 | 6/10 | +1.1 / -7.9 | 48% | -13.1 |
| held-out, timing | 11,458 | +13.9 | +0.62 | 5/10 | +26.7 / +5.2 | 51% | +8.8 |

Official gate: top symbol 12% (AMGN), so concentration is no longer the problem;
top-1% share -188% (meaningless with a negative total). The timing score fails on
mean, t, years and same-sign, and is driven by 2020 (+182, the COVID rebound). At
2 bps/side SPY timing is still only +16.3 (t 0.92). The 7-session window is not
gated: the 62's timing +24.2 (t 1.10, 7/11 years) but +49.6 in 2016-20 vs +2.2 in
2021-26; SPY raw long +45.1 (t 2.15) still includes the market's own rise and
SPY timing is +32.8 (t 1.49). No sign of a window that holds up.
The per-trade t values look better (timing +1.88 design, +3.15 held-out), but they
count correlated names in one month as separate bets; the monthly t is the honest one.
The first run's numbers (official -0.3, timing +16.6) were inflated by the GE split.
Spike-pod correlation: skipped (pod P&L not here). Log: research/turn_of_month.log.

**Lesson:** the turn of the month adds a few bps over ordinary days at most, with
nothing like a significant t in 129 months, and looks weaker since 2021, in line with
Han, Han & Tian. Not worth a pod.

**Data warning for earlier work:** the 1-min cache is unadjusted, so any test holding
across an overnight sees splits. overnight-drift (2026-11-04) did not filter them.
Summing the 15 split-like gaps in the 62 gives about -1.4 bps per name-night, so its
design result is understated: roughly +3.1 gross / -0.9 net instead of +1.65 / -2.35.
That is arithmetic, not a rerun, and it stays under the +3 bar, so that FAIL stands.
Future overnight or multi-day tests on 1-min bars must filter splits. Tests that enter
and exit within a session don't hold across the split; whether a split distorts their
signal features was not checked here.


## 2026-11-06 spike-news: do spikes with a headline run further than spikes without?

**Pre-registered, before any news was joined to any trade.** spike_mom as live
(vol >= 8x, move >= 5 sigma, signal 13:00-14:54 ET, 1-bar delay, hold 60), SIP
1-min, 2017-2026, rank 1-25 by PRIOR-year dollar volume. Costs 2 bps/side.
News = at least one Benzinga headline on that name with created_at in the 60
minutes before the signal bar opens. A headline carried by 5+ of the 277 names is
a market recap and doesn't count.
PASS (a case for a news filter on the paper pod, which still has to go through
shadow) needs ALL, on the news group: n >= 300; mean >= +3 net; t >= 2.5; positive
in >= 70% of years; same sign in 2017-21 and 2022-26; top symbol <= 25% of P&L;
AND news minus no-news >= +3 bps, t >= 2, same sign in both periods.
Reported, not gated: 15-minute and since-prior-close windows, recaps included,
headlines during the signal bar, up vs down spikes, rank 26-100, a same-name
same-year comparison, by-day t.
Citation checked: Chan (JFE 70(2), 2003) is about monthly returns, finds strong
drift after bad news and reversal after no-news moves, and says both are mainly in
smaller, illiquid stocks. So this test stretches it to 60 minutes in the most
liquid names, and a null result would not contradict the paper.
Not a clean first look: the pooled rank 1-25 result is known (+8.2 design, +9.2
held out, spike-wide-universe), so only the split is new. Headline counts double
from 2016 to 2025 and mega-caps are nearly always in the news, so the share with
news is reported by year and name. If no-news beats news, that is a new idea to
pre-register, not a pass.
Script: scripts/research/spike_news.py.

**Result: FAIL, on one gate.** Rank 1-25, 2017-26. News n=321, +15.9 net (t 2.98), positive 9 of 10
years, +10.7 / +20.1 by period, top symbol GOOG 16%, top-1% share 25%. No news n=1123, +6.5 (t 2.22),
5 of 10 years, top-1% share 84%. Gap +9.4 bps, t 1.54 (needed 2), +3.1 / +15.1 by period. The news group
meets every other check, but the filter's reason to exist is the gap. Not loosened.
Log: research/spike_news.log. The first run scored 0% news because of a timestamp unit bug (seconds vs
nanoseconds); fixed before any gated number was read, and the rules did not change.

Why I don't trust the +9.4 yet:
- Windows disagree. 15 minutes: gap -0.6 (t -0.10). Since the prior close: -5.4 (t -0.97), and in 2022-26
  no-news is the better group (+31.2 vs +3.5). A real effect shouldn't flip with a wider window.
- SPY is 99 of the 321 and 83% of its signals have "news", so that tag says little for it. Stocks only
  (not gated): gap +9.5, t 1.14.
- Same-name same-year gap is also +9.4, so it isn't simply that newsier names do better.

**Lead, seen after the fact (not evidence):** the whole gap comes from down spikes. Down + news +16.1
(n=145); down + no news -3.7 (n=550), and -15.9 (t -2.66) in 2022-26. Gap +19.7, t 2.74. Up spikes show no
gap (-0.6). This is the shape Chan describes (drift after bad news, reversal without), but one cut of
several, so it needs its own pre-registered test on other data. Queued as spike-down-no-news.

**Bigger finding: the pod's t is overstated.** Signals cluster on the same days (730 days for 1,444 trades,
up to 22 in a day), so they aren't independent. Rank 1-25 +8.6 net: t 3.34 per trade, 1.33 on summed daily
P&L, 0.34 if each day counts equally. The largest days are macro: best 2025-04-09 (+5561 summed bps), 2022-11-30
(+4247), 2019-08-01, 2021-03-17; worst 2022-11-02 (-3218), 2024-09-18 (-2122), 2022-09-21 (-1739). Several of
these are Fed days. The usual day looks close to zero, which makes spike-events the key test. These sums
ignore the pod's 4-position cap, so they are not the pod's own P&L. The earlier t 3.08 has the same
weakness. Spike-pod correlation: skipped.

## 2026-11-06 spike-down-no-news: does skipping down spikes with no headline help on other names?

**Pre-registered, before any outcome on this cut was read.** Hypothesis (Chan 2003 shape, seen after the
fact on rank 1-25 in spike-news): a down spike with no headline doesn't continue, so skipping it improves
the group. Rank 1-25 found the lead and can't confirm it, so it is not tested here.
Rules: spike_mom as live (vol >= 8x, move >= 5 sigma, signal 13:00-14:54 ET, 1-bar delay, hold 60), SIP
1-min, 2017-2026, rank 26-100 by PRIOR-year dollar volume, trades and news tags taken unchanged from
.cache/spike_news_scored.csv (n=4054). No news = n60 == 0: no single-name Benzinga headline in the 60
minutes before the signal bar opens, 5+ name recaps not counted. Skipped = side -1 and no news. Kept =
everything else. Costs 2 bps/side.
PASS needs ALL of:
- Filter: mean(kept) minus mean(all) >= +3 bps; Welch t of kept vs skipped >= 2 (the improvement is that
  gap times the skipped share, so it has the same t); improvement positive in 2017-21 and 2022-26, and
  in 2017-20 and 2021-26 (the standard gate's split, with no 2016 because rank needs a prior year).
- Standard gate on kept: n >= 300; mean net >= +3; t >= 2.5; positive in >= 70% of years; same sign in
  2017-20 and 2021-26; top symbol <= 25% of P&L.
If PASS: same rules on the held-out rank 101+ (ranked names only), reported alongside. Still only a
candidate for the paper pod's shadow log, not deployed.
Reported, not gated: t by summed daily P&L; the gap's t with same-day trades clustered; win rate, median,
top-1% share; each of the four cells (up/down, news/no news); down + news minus down + no news; the rule
"skip ALL down spikes" for comparison; 4 bps/side; the 4-position cap; correlation of daily P&L with rank
1-25 (not the pod's own P&L, which is on the VPS).
Can't settle the news half: checked counts only, no returns. Headlines are rare here, 3-11% of signals
by year, so down + news is n=116 and skipped is 94% of down spikes (1785 of 1901). This test is close to
"are down spikes worse than up spikes here". If the skip-all rule does as well as the news rule, a pass
says nothing about news, and the journal entry must say so.
Not a clean first look: the pooled group is known (+0.4 net 2017-21, +3.3 net 2022-26, spike-wide-universe),
and spike_news.log has a rank 26-100 news vs no-news line that I did not open; that line has no down split.
Caveats that flatter the test: the extra names were picked with lists from 2017/2021/2025, and live IEX
detection about halved the edge. Shorting costs and borrow are not modelled.
Script: scripts/research/spike_down_no_news.py.

**Result: FAIL, 7 of 10 checks.** Rank 26-100, 2017-26. All n=4054, +1.8 net (t 1.18, by day 0.37). Kept
n=2269, +1.8 (t 0.76, by day 0.21), positive 6 of 10 years, win 45%, median -4.0, top symbol C 6%. Skipped
n=1785, +1.9 (t 1.02, by day 0.64). Improvement -0.03 bps (needed +3); kept minus skipped -0.06, Welch t -0.02,
clustered by day -0.01 (needed 2). Improvement by period: -2.99 in 2017-21, +2.55 in 2022-26, -0.62 in 2017-20,
+0.36 in 2021-26. Not loosened. Rank 101+ not scored, as pre-registered. Log: research/spike_down_no_news.log.
The pooled +1.8 matches the +0.4 and +3.3 from spike-wide-universe weighted by n.

- The lead doesn't replicate. Down + no news was -3.7 on rank 1-25 and is +1.9 here, so no sign of reversal. The
  gap between down + news and down + no news was +19.7 (t 2.74) there and is -0.9 (t -0.11, n=116 vs 1785) here.
- The news half stays unsettled, as flagged: 116 news-tagged down spikes give a wide range. Skipping ALL down
  spikes does the same as the news rule (+0.01), so nothing here separates them.
- Top-1% shares of 243-401% mean a few trades are the whole total; the groups are near zero and those shares
  are noisy. At 4 bps/side both all and kept are -2.2.
- With the 4-position cap, kept is worse than all: n=1849, -2.7, sum -5077, vs n=3290, -0.2, sum -635. Not gated.
- Daily P&L correlation with rank 1-25: all +0.87, kept +0.82. So this group is largely the same market days,
  and adds little diversification.

Limit on the conclusion: rank 1-25 can still be different from these names, and the pod trades rank 1-25. The
test says the pattern doesn't carry to other names, not that it is false in the pod's own names. Only forward
shadow signals can check that.

## 2026-11-07 spike-events: is the spike edge concentrated on FOMC decision days?

**Pre-registered, before any return was split by event day.** Hypothesis: the rank 1-25 edge comes mostly from
scheduled Fed decision days, when a 14:00 ET statement lands inside the signal window, and the usual day is near zero.
Rules: spike_mom as live (vol >= 8x, move >= 5 sigma, signal 13:00-14:54 ET, 1-bar delay, hold 60), SIP 1-min,
2017-2026 (data ends 2026-10-27), rank 1-25 by PRIOR-year dollar volume, trades unchanged from
.cache/spike_news_scored.csv. Costs 2 bps/side. Event day = date in a fixed list of 77 scheduled decision days,
2017-02-01 to 2026-09-16, in the script. Emergency moves (2020-03-03, 2020-03-15) are not on it and count as ordinary.
Never defined from which days had signals.
Calendar check, no returns read: written from memory of the Fed's calendars; search results confirmed only 2023, 2025,
2026 and most of 2022, and the rest could not be confirmed online. SPY volume in 14:00-14:04 vs 13:30-13:59 has median
6.3x on the 77 days vs 1.23x on all days, lowest 1.42x (2020-11-05). A wrong date would look like an ordinary day, and
none does, though this can't catch a date that happens to be a minutes day.
PASS (a case for restricting or sizing the pod by the calendar, through shadow first) needs ALL, on the event group:
- n >= 300; mean net >= +3; t >= 2.5; positive in >= 70% of years; same sign in 2017-20 and 2021-26 (no 2016, since
  rank needs a prior year); top symbol <= 25% of P&L.
- t on summed daily P&L >= 2 (added because per-trade t is known to overstate here; stricter than the standard gate).
- Event minus ordinary >= +3 bps; t with same-day trades clustered >= 2; same sign in 2017-21 and 2022-26.
Reported whatever the verdict, not gated: rank 26-100 with the same split, as the replication; the ordinary group's own
numbers and t by day (the "without them" half of the question); top-1% trades removed from each group; signals before
vs from 14:00 on event days; up vs down; SPY/QQQ vs stocks; FOMC minutes days (decision + 21 calendar days) and the day
after a decision; 4 bps/side; the 4-position cap; share of P&L from the 5 best days; win rate, median, top-1% share.
Not a clean first look: pooled rank 1-25 is known, and spike-news listed some big days by date, a few of them Fed days
of both signs. Counts were checked, no returns: event group n=308 on 55 days of 1,444 trades on 730 days, so n >= 300
passes by only 8; rank 26-100 n=519 on 68 days. FOMC days are the only events tested. Big unscheduled macro days
(2025-04-09, 2022-11-30) stay in ordinary, so "ordinary" doesn't mean quiet.
Caveats that flatter the event group: 2 bps/side at 14:00 on a Fed day is optimistic, and the pod could not take many
simultaneous signals. Shorting costs are not modelled. Spike-pod correlation can't be computed from here (the live log
is on the VPS); correlation with rank 1-25 is the stand-in.
Script: scripts/research/spike_events.py.

**Result: FAIL, 8 of 10 checks.** Rank 1-25, 2017-26. Event n=308, -3.0 net (t -0.52, by day -0.18), positive 6 of 10
years, win 52%, median +1.5, top symbol BAC 21%. Ordinary n=1136, +11.8 (t 4.09, by day 1.70), 6 of 10 years, win 47%,
median -2.6, top-1% share 48%. Gap -14.8, clustered t -0.83; 2017-21 +17.0, 2022-26 -45.1. The hypothesis is wrong:
FOMC days add nothing to the pooled total, which goes from +12,463 summed bps to +13,388 without them. Not loosened.
Held-out step not run, since it applies only to a PASS; rank 26-100 was reported anyway.
Log: research/spike_events.log. A first version of the added diagnostics had a syntax error, so nothing printed; gates
and calendar did not change.

- Rank 26-100 agrees on the headline: event n=519 -12.7 (by day -0.76), ordinary n=3535 +4.0 (by day 0.78), gap -16.6,
  clustered t -1.01. Its daily P&L correlates +0.87 with rank 1-25, so it is partly the same market days, not fully
  independent evidence.
- FOMC days flip sign: +22.1 in 2017-21, -24.2 in 2022-26 (t -2.64, by day -0.85), and 2025-26 is positive again. Too
  unstable to use as a skip rule, and the 4-position cap reverses it: capped event n=167 +8.3 (t 1.00), capped ordinary
  n=970 +5.3 (t 1.91, by day 1.42).
- At 4 bps/side: event -7.0, ordinary +7.8.

**Bigger finding, after the fact (not pre-registered, not evidence of a rule):** the total rests on very few days.
Three non-FOMC days hold +12,139 of the +12,463 summed bps, on 41 of 1,444 trades: 2025-04-09 (+5561, 11 up trades, all
entered 13:21), 2022-11-30 (+4247, 20 up, all 13:32), 2019-08-01 (+2331, 10 down, 13:28-14:28). Without them: n=1403,
+0.2 (t 0.11, by day 0.06). With the cap: those days are 13 trades and +4,266 of +6,516; the rest +2.0 (t 0.86, by day
0.66). The news behind them is my recollection, not checked here: tariff pause, Powell's speech, tariff tweet. All three
look like an unscheduled shock landing just before or in the window.
That trim flatters the bad reading, because dropping any strategy's best days hurts it. The 3 worst days, all FOMC
(2022-11-02, 2024-09-18, 2022-09-21), total -7,079. Dropping best 3 and worst 3 leaves n=1347, +5.5 (t 2.76, by day
1.84), 6 of 10 years. Rank 26-100 on the same trim is -1.1 (t -0.84), so that residue doesn't carry over.
So about 6 days in ten years decide the sign. Nothing here identifies them in advance, and live, the pod's outcome will
mostly depend on whether such a day occurs while it's running.
Reported cuts that looked strong were single days: minutes days +63.5 falls to -9.1 without 2025-04-09 (a minutes day by
coincidence), and day-after +44.5 falls to +8.2 without 2019-08-01.
Untested lead, a story and not a result: the hold from a 14:00 signal spans the 14:30 press conference, which could
explain FOMC whipsaws. Not queued, because it would need FOMC-only trades, about 300 in ten years.
Not done: CPI and jobs days (no fixed calendar in the cache). Spike-pod correlation: not computable here.

## 2026-11-07 grammar-zmove: 432 intraday move rules at once (check-in, not the desk)

Pre-registered in scripts/research/grammar.py before the run: every combination of a 5/15/30-minute
move (raw or minus SPY) at |z| >= 3 or 4 of the trailing 1-min sd, optional volume >= 3x, follow or
fade, three windows (09:45-10:59, 11:00-13:29, 13:30-15:00), hold 15/30/60. All 277 names, 2 bps/side.
Design 2016-20 needed n >= 1000, mean >= 3 bps, day t >= 3.5 (for 432 tries); held-out 2021-26 the
same sign, mean >= 2, day t >= 2, 4 of 6 years positive.

Result: FAIL, 0 of 432. No rule reached day t 3.5 in design (best 2.23: fade a market-relative 15-min
|z| >= 4 move on 3x volume at the open, +10.7 bps, n 885), and that one lost in held-out (-4.7, t -1.23).
Only 1 rule cleared t 2 in design; none cleared t 2 held out. The strongest-looking cells are all
open-window fades on volume, and every one reversed after 2020. Full table: research/grammar_zmove.csv.

Lesson: plain price-and-volume triggers on liquid names, at 1-60 minute holds, carry no edge after
2 bps/side, consistent with the 11 earlier price-pattern FAILs. The next families should add
information prices don't have (headline direction, filings, the calendar), not more price shapes.

## 2026-11-07 PRE-REGISTRATION headline-family: 3 tests under one combined bar (check-in; the desk runs it)

Why now: a council review of the plan for the new API budget. With 15/15 desk FAILs and 0/432 grammar
rules, more tests mostly add false-pass risk. So this family is fixed in advance and is the last paid
trading-signal step unless something in it passes.

Scorers:
- S1 word list (free): Loughran-McDonald Master Dictionary 1993-2025, cached at
  .cache/lexicon/lm_master_1993-2025.csv (free for non-commercial research; a lexicon rule going to real
  money would need a license or a different scorer). Headline score = positive minus negative words
  (tokens = runs of A-Z after uppercasing). Above 0 good, below 0 bad, 0 neutral and skipped.
- S2 Claude (paid, only under the rule below): Haiku 5.5 labels good / bad / neutral from the headline
  text alone.

Headlines: single-name (tagged to fewer than 5 of the 277 names, as scripts/research/spike_news.py),
created_at strictly before the signal bar opens.

Tests (all 277 names, 2016-2026 1-min SIP, 2 bps a side):
- T1 news-direction: headline created 09:45-14:50 ET. Enter at the open of the 2nd bar that starts after
  created_at, in the headline's direction; exit 60 bars later, same day. First headline per name per 60
  minutes; no overlapping trades per name.
- T2 spike-agreement: spike_mom signals (volume >= 8x, |move| >= 5 sd, 09:35-14:58, backtest_scalp's
  1-bar delay, hold 60) with a scored single-name headline in the prior 60 minutes; trade only when the
  spike's direction matches the headline's sign. Report the disagreeing group too (not a test).
- T3 earnings-open: 8-K item 2.02 filed between 16:00 the previous day and 09:25 ET
  (backtest_pead.earnings_filings). Sign = sum of that name's headline scores from 1 hour before the
  filing to 09:25. Enter at the 10:00 open in that direction, exit at 15:55.

Combined bar: at most 6 tests (3 x 2 scorers), so each needs t >= 2.9 on summed daily net P&L
(Bonferroni, 0.05/6 two-sided), plus the desk gate: n >= 300, mean net >= 3 bps, >= 70% of years
positive, same sign in 2016-20 and 2021-26, no symbol over 25% of P&L. Held out: names ranked 101-277 by
prior-year dollar volume, reported separately; a pass also needs the same sign there with t >= 2.

Claude-label rule (decided before any result): after S1's results, Vision labels headlines from
2025-07-01 on with Haiku (about $0.60). S1 and S2 are compared on T1 trades from 2026-07-01 on, after the
model's training data (taken as ending June 2026), so S2 can't know what happened next. The full labelling
(about $5) and S2's runs of T1-T3 happen only if S2 beats S1 there by >= 3 bps per trade on the same
trades. The bar stays t >= 2.9 either way.

Stop rule: if nothing passes, no more API money goes to trading signals. The desk keeps testing free
ideas on the subscription, and STRATTON's real-money decision waits for a pass on free data.

Results: (the desk appends them here, S1 first.)

**Desk run plan, 2026-11-08, written before any T1-T3 outcome was computed.** Scorer S1 only. The rules
above stand; these are the readings of points they leave open, fixed now:
- Data: all 277 names, every year 2016-2026 is cached (data ends 2026-10-27). Headlines from .cache/news.
  8-K filings from .cache/edgar only (no SEC calls; a name with no cached CIK is left out of T3 and listed).
- Single-name = the (created_at, headline) pair appears in fewer than 5 of the 277 files. Headlines with the
  same name and timestamp are summed into one score.
- T1: "first headline per 60 minutes" is among non-neutral headlines: a scored headline triggers only if no
  scored headline on that name was taken in the prior 60 minutes, and only if its entry is after the name's
  previous exit. Entry = open of the 2nd bar whose start is after created_at; exit = close of the 60th bar
  (entry bar included), same day, by bar count.
- T2: spike rule as scripts/research/spike_wide.py but window 09:35-14:58; score = sum of single-name
  headline scores with created_at in [signal bar open - 60 min, signal bar open). Sum 0 = no trade.
- T3: "previous day" = previous trading day in the name's bars. One trade per name-day. Entry = 10:00 bar
  open, exit = 15:55 bar open. Score window [first filing in the window - 1 h, 09:25 ET of the trade day).
- Day t = per-test daily sum of net bps across names. Top-symbol share = biggest symbol's net sum over the sum
  of positive symbol sums. Years: 2016-26 (11). Held out = trades in names ranked 101-277 by prior-year dollar
  volume within the 277, so 2017-26 only; it is a subset of the main sample, as written above.
- Reported, not gated: per-trade t, win rate, median, top-1% share, the T2 disagreeing group, daily P&L
  correlation with spike_mom rank 1-25 trades from .cache/spike_wide_trades.csv (a stand-in for the pod).
Script: scripts/research/headline_family.py.

**S1 results, 2026-11-08: all three FAIL.** 277 names, 2016-26, 2 bps/side, day t = summed daily net. Log:
research/headline_family.log; trades in .cache/headline_family_s1_trades.csv. Of 690,145 single-name headlines,
S1 scores 11% good, 17% bad, 73% neutral (skipped). No T3 filings for 12 names (ETFs and foreign filers, listed in the log).
- T1 news-direction: n=56,217, -3.41 net (gross +0.6), day t -7.51, 1 of 11 years positive; held out rank 101-277
  -2.17, day t -1.56. FAIL 4/8. The word-list direction carries about nothing; the cost is the whole loss.
- T2 spike-agreement: n=301, +12.3 net, day t 1.53 (needed 2.9), per-trade t 1.69, 8 of 11 years, +8.0 / +15.5 by
  period, top symbol NVAX 10%; held out n=86, +20.6, day t 1.56. FAIL 6/8, on both t checks.
- T3 earnings-open: n=1,956, -4.85 net, day t -0.79, 5 of 11 years; held out -2.72, day t -0.27. FAIL 4/8.
- Reported only: T2 disagreeing (spike against the headline) n=278, +21.0 net, day t 2.44, so agreement does
  not help; it is worse. Together that is the spike-news result again: spikes with any headline run, and the
  word list's sign adds nothing. T3 split (not evidence): good-scored names long -11.8 (n=1,328), bad-scored short
  +9.9 (n=628), i.e. the open-to-close move went against the headline tone after 10:00.
- Daily P&L correlation with spike_mom rank 1-25: T1 -0.13, T2 +0.34, T3 +0.02.
Verdict: no S1 test passes. Not loosened. Under the pre-registered rule, the S2 (Claude label) comparison is
Vision's step, not the desk's, and was not run here. If S2 doesn't qualify, the stop rule applies: no more API
money on trading signals.

**S2 run plan, 2026-11-10, written before any S2 label exists** (owner OK to spend on it). Readings of the
Claude-label rule, fixed now:
- Labeller: Haiku 5.5 (claude-haiku-5-5) through Vision (jarvis ops/stratton_labels.py, site stratton_news),
  200 headlines a request, text alone, G/B/N. 89,927 distinct single-name headline texts from 2025-07-01
  (same single-name rule as S1); one label per text. G = +1, B = -1, N or no label = 0 (skipped), summed per
  name and timestamp as S1.
- Comparison: T1 exactly as the S1 run, each scorer on its own trades, entry date 2026-07-01 to the end of the
  cached bars (2026-10-27). "Beats S1 by >= 3 bps per trade" = S2's mean net per trade minus S1's >= 3.0.
  S1 there: n=2,466, -4.47 net, day t -2.76 (reproduced from headline_family_s1_trades.csv). The overlap and
  the opposite-side trades are reported, not gated.
- If it qualifies: full labelling (~$5, owner asked first) and S2 T1-T3 under the t >= 2.9 bar. If not: stop rule.
Script: scripts/research/headline_s2.py.

## 2026-11-09 steady-movers: ride intraday moves that are big AND smooth (ideas.md E)

**Pre-registered, before any return was computed.** Hypothesis: a name whose move since the open is both
among the day's largest and unusually smooth (many small same-direction steps, few jumps) is being pushed by
order-splitting, and keeps going into the close. An intraday cousin of Da, Gurun & Warachka, "Frog in the Pan"
(RFS 2014), which is monthly. Lead from .cache/missed_summary.md ("moved steadily, no signal" is the biggest
bucket, 22 of 40 movers over 4 sessions); those movers are hindsight and are not used anywhere in the rule.
Rules:
- Universe: the 277 names (backtest_scalp.SYMBOLS + WIDE + m1_extra_symbols.json), SIP 1-min, regular hours.
  Years 2016-2026 are all cached (277 files each; data ends 2026-10-27). A name-day is eligible only if it has
  the 09:30 bar, at least 90% of the bars from 09:30 to the decision bar, the entry bar and the 15:55 bar.
- Decision time T = 12:00 ET (primary, gated). 11:00 and 13:00 reported, not gated.
- Using only bars that close before T (09:30 .. T-1 min): R = log(close at T-1 / open at 09:30);
  ER = |R| / sum over those bars of |log step| (first step = log(close/open) of the 09:30 bar, then
  close-to-close).
- Each day, across eligible names: R in the top or bottom decile of that day's R AND ER in the top decile of
  that day's ER. Side = sign(R). Entry = open of the bar at T+1 min (the 1-bar delay convention), exit = open
  of the 15:55 bar. One trade per name-day.
- Costs 2 bps/side (4 round trip). Gross is log return in bps. No overnight hold, so splits don't matter.
Gate (desk standard, plus day t because trades cluster on the same days): n >= 300; mean net >= +3 bps;
per-trade t >= 2.5 AND t on summed daily net P&L >= 2.5; >= 70% of years positive (2016-26, 11 years); same
sign in 2016-20 and 2021-26; top symbol <= 25% of positive-symbol P&L.
Replication for a PASS (not a true holdout, all 277 names are in the sample; the cross-section needs them all):
the same trades split into names ranked 1-100 and 101-277 by PRIOR-year dollar volume (2017-26); a PASS needs
both halves the same sign with day t >= 2.
Reported, not gated: win rate, median, top-1% share, long vs short, 11:00 and 13:00, the R-decile-only rule
without the ER filter (is smoothness adding anything?), 4 bps/side, daily P&L correlation with spike_mom rank
1-25 (.cache/spike_wide_trades.csv, stand-in for the pod).
Caveats: 2 bps/side on the smaller of the 277 names may be optimistic; shorting costs are not modelled. The
missed-movers lead came from 4 sessions only, under the 10-session bar in agent.md; the idea was queued by the
owner's check-in (ideas.md E), so it runs as queued.
Script: scripts/research/steady_movers.py.

**Result: FAIL, 3 of 9 checks.** 12:00, all 277 names, 2016-26 (data ends 2026-10-27), 2 bps/side. Log:
research/steady_movers.log; trades in .cache/steady_movers_trades.csv. 647,028 eligible name-days, median 240
names a day, so the two deciles pick about 18 names a day.
- n=48,796 on 2,709 days, -5.76 net (gross -1.76), per-trade t -9.91, day t -5.59, win 46%, median -7.1. Positive
  in 1 of 11 years (2025 +3.0). 2016-20 -6.47, 2021-26 -5.16. Long -5.3, short -6.3. At 4 bps/side -9.76.
- Replication halves agree on the loss: rank 1-100 -4.05 (day t -3.00), rank 101-277 -6.40 (day t -5.08).
- Reported only: 11:00 -5.33 net (gross -1.33, day t -4.13); 13:00 -5.11 (gross -1.11, day t -5.75). Without the
  smoothness filter (R decile only): 12:00 -4.67 net, gross -0.67; 11:00 gross +0.30; 13:00 gross -0.56.
- Daily P&L correlation with spike_mom rank 1-25: +0.05 on 728 shared days.
Verdict: FAIL. Not loosened. Big midday movers in these names don't continue into the close, and selecting the
smooth ones makes it slightly worse (gross -1.8 vs -0.7 bps), not better: no intraday frog-in-the-pan here. The
gross move is about zero, so no cost assumption rescues it, and the faded side (+1.8 gross) is under the 4 bps
cost too. Held-out step not run, since it applies only to a PASS.
Lesson: the missed-movers bucket "moved steadily, no signal" describes movers picked after the fact; at noon,
the smooth movers among 277 names are not the ones that keep going. Another price-only FAIL.

## 2026-11-10 intraday-pairs: fade 3-sd intraday splits in highly correlated pairs (ideas.md F)

**Pre-registered, before any return was computed.** Hypothesis: two names that have moved together every
half hour for three months share the same order flow; when one pulls away from the other intraday with no
single-name headline to explain it, the gap is temporary liquidity pressure and closes the same day.
Market-neutral, so it should not depend on the day's direction (unlike spike_mom). Scout (2026-11-10) found
no new after-cost evidence for intraday pairs; a 2025 study of 288 intraday momentum/contrarian rules on 28
US blue chips (Investment Management and Financial Innovations) found none survive costs, a reason to expect FAIL.
Rules:
- Universe: the 277 names (backtest_scalp.SYMBOLS + WIDE + m1_extra_symbols.json), SIP 1-min, 09:30-15:59.
  All 2016-2026 cached (data ends 2026-10-27). A name-day is eligible if it has the 09:30 bar and >= 90% of the
  390 bars; missing bars take the previous close (open and close).
- Pairs: on each month's first trading day, using the prior 60 sessions, each eligible-all-60-days name's 13
  half-hour log returns a day (09:30 open, then closes of the 09:59 .. 15:59 bars); the 50 pairs with the highest
  correlation are that month's pairs. First month with 60 prior sessions is used; earlier 2016 is skipped.
- Each day, pair (a, b), both eligible: L = log(close a) - log(close b); ref = L at the 09:59 bar close (the
  10:00 value); sd = std of the pair's 13 half-hour changes of L a day over the prior 20 sessions (days where
  both were eligible, need >= 15).
- Trigger: the first bar starting 10:30..15:53 whose close has |L - ref| > 3 sd and neither name has a
  single-name headline (in fewer than 5 of the 277 news files) with created_at in [bar start - 60 min, bar start).
  One trade per pair per day.
- Entry at the next bar's open: long the laggard, short the leader, equal dollars. Exit at the open of the bar
  after the first bar (entry bar on) whose close is back at or across ref, else at the 15:55 bar open.
- P&L per pair trade = long leg log return minus short leg log return, bps of one leg's notional; costs
  2 bps/side per leg = 8 bps round trip. No overnight hold, so splits don't matter.
Gate (desk standard; day t gated too, as in steady-movers): n >= 300; mean net >= +3 bps per pair; per-trade
t >= 2.5 and t on summed daily net >= 2.5; >= 70% of years positive (2016-26); same sign 2016-20 and 2021-26;
no symbol > 25% of positive-symbol P&L (each trade's net split half to each leg).
Held out (needed for a PASS, run and reported regardless): the same procedure with pairs formed only among names
ranked 101-277 by PRIOR-year dollar volume within the 277 (2017-26); same sign with day t >= 2.
Reported, not gated: win rate, median, top-1% share, top pair share, median hold, share exited on reversion,
the rule without the headline filter, daily P&L correlation with spike_mom rank 1-25 (.cache/spike_wide_trades.csv).
Caveats: shorting costs and borrow not modelled; share-class twins (if both are in the 277) will rank top and are
kept, as written.
Script: scripts/research/intraday_pairs.py.

**Reading fix before any return was seen:** the first run (stopped at 2018, only progress lines printed) formed no
pairs from mid-November to February each year, because on half-day sessions every name has under 90% of 390 bars,
so no name is "eligible all 60 days". The formation and sd windows now count the prior 60 / 20 FULL sessions,
skipping days where fewer than half of the names with data are eligible (1-3 a year). No rule changed otherwise.

**Result: FAIL, 3 of 8 checks.** All 277 names, 2016-26 (data ends 2026-10-27; pairs from April 2016), 8 bps a pair
trade. Log: research/intraday_pairs.log; trades in .cache/intraday_pairs_trades.csv.
- n=57,073 on 2,637 days, -3.95 net per pair (gross +4.05), per-trade t -13.82, day t -8.98, win 46%, median -3.9.
  Negative in 11 of 11 years; 2016-20 -2.92, 2021-26 -4.79. Top symbol GPN 17%, top pair GPN/V 5%.
- Median hold 195 min; only 13% of trades get back to the 10:00 value before 15:55; median entry z 3.09.
- Held out (pairs formed among rank 101-277 by prior-year dollar volume, 2017-26): n=56,067, -5.15 net (gross
  +2.85), day t -7.62, all 10 years negative.
- Reported only: without the headline filter -4.14 net (gross +3.86, n=61,394), so the filter adds +0.2 bps. At
  1 bps/side per leg (4 a trade) +0.05. Most traded: AAL/UAL, BAC/JPM, DAL/UAL, COP/EOG, MSFT/QQQ. Daily P&L
  correlation with spike_mom rank 1-25: -0.06 on 725 shared days.
Verdict: FAIL. Not loosened. The split does close a little (gross +4 bps, positive every year in both universes),
but four legs of 2 bps cost twice that. Even at 1 bps a side it is zero. Held-out step reported above; it agrees.
Lesson: intraday pair divergences in these liquid names are mostly real repricing (87% never close the same day);
the small reversion is a market-maker's spread-sized edge, not a taker's. Another price-only FAIL, the 18th.
