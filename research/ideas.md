# STRATTON idea queue

Top = next. Each line: slug, the idea, why it might work, data needed.
Already tested (see research/journal.md and git history): fixed-list ORB,
stocks-in-play, gap-and-go (incl. SSR-filtered shorts), index intraday
momentum, spike_fade, vwap_fade, event drift on any big move, RSI2, Kronos
standalone, spike_mom (PASS, live as the spike pod), PEAD on real earnings
dates and pre-earnings run-up (2026-11-02, see journal).

**NEXT (owner's revamp review, 2026-11-12): U, a new universe, tested before anything below.** Every
stock test so far ran on the 62 or 277 most liquid names, and the trigger replay (journal 2026-11-10)
found nothing left there three minutes after a move. The likeliest place for an edge the desk can still
catch is less-watched names. Run U1-U3 as ONE pre-registered family under one combined bar (as the
headline family: all three registered in one journal entry before any result, PASS needs t >= 2.9 for
the test that passes), one desk session each:
- Universe, fixed before any return: US common stocks, market cap $300M-$5B, price > $5, median daily
  dollar volume > $5M over the PRIOR 60 days (no hindsight), from the daily pool and SIP history (free
  once 15 minutes old). If the cache lacks them, the first session is the download and is BLOCKED
  until it lands; say exactly what to fetch.
- Costs: measure half-spreads from 1-min high-low on quiet minutes for a sample of these names and
  pre-register a per-side cost of at least 10 bps (never less than measured).
- U1. Single-name news (Benzinga, created_at before entry), follow the first 15 minutes' direction
  from minute 20 to the close.
- U2. The same signal, entered at the next day's open, held one day.
- U3. The spike rule (backtest_scalp: volume >= 8x, move >= 5 sigma) on this universe, 60-minute hold.
- Held-out slice for any pass: the years not used while designing. Intraday only; no long holds.

Revamp priorities (docs/stratton-revamp-plan.md), tested before anything below:

A. DONE, FAIL 2026-11-06, see journal. spike-down-no-news: spike-news (2026-11-06) failed its gate, but its gap came
   entirely from down spikes: down with no headline -3.7 bps, down with a headline
   +16.1, up spikes no gap. That was found by looking, so rank 1-25 can't confirm
   it. Pre-register on rank 26-100 only, 2017-26, with the 60-minute single-name
   rule from scripts/research/spike_news.py (.cache/spike_news_scored.csv has
   n60 and side per trade): does skipping down + no-news improve the group by at
   least +3 bps, t >= 2, same sign in 2017-21 and 2022-26? News-tagged down spikes
   there are few, so report n and say if it can't settle the question.
B. DONE, FAIL 2026-11-07, see journal. spike-events: is the spike edge concentrated on event days (FOMC decision
   days, top-1% tails)? Report the edge with and without them. Score it on daily
   P&L too: per-trade t 3.34 is only 1.33 by day, and the biggest days look macro.
   Define event days from a fixed calendar, never from which days had many signals.
Owner, 2026-11-07: the fund's aim is active intraday trading, so new intraday signals
come first now, then the last Phase 2 question (C). Crypto and options are in scope
if they can make money. Near-misses go to the shadow bench (pods/bench) as watch-only;
a fresh PASS here is what puts a rule on it with real ladder stages.

D. S1 DONE, all 3 FAIL 2026-11-08, see journal; S2 is Vision's step. headline-family: PRE-REGISTERED in research/journal.md (2026-11-07 PRE-REGISTRATION
   headline-family). Run T1, T2 and T3 with scorer S1 (the cached word list) exactly as written there,
   and append the results to that entry. Don't add tests to the family. S2 (Claude labels) only under
   that entry's rule, never by the desk.
E. DONE, FAIL 2026-11-09, see journal. steady-movers: plan question 4. Most missed movers rise steadily, never spiking.
   At 12:00 ET (primary; report 11:00 and 13:00), among the 277 names: return since
   the 09:30 open in the top or bottom decile AND efficiency ratio (|net move since
   the open| / sum of |1-min moves|) in the top decile. Go with the move, exit 15:55.
   Idea: order-splitting by institutions keeps pushing a quiet trend (an intraday
   cousin of Da, Gurun & Warachka's "frog in the pan", 2014). 2 bps/side. Never pick
   names with the movers list, which is hindsight.
F. DONE, FAIL 2026-11-10, see journal. intraday-pairs: plan question 6, market-neutral. Each month, the 50 pairs among the
   277 with the highest correlation of 30-min returns over the prior 60 sessions.
   After 10:30 ET, when a pair's log price ratio moves more than 3 sds (of its prior
   20 sessions' 30-min changes) from today's 10:00 value and neither name had a
   single-name headline in the prior 60 minutes, fade it: long the laggard, short the
   leader, equal dollars; exit when it is back to the 10:00 value or at 15:55.
   2 bps/side per leg (8 bps round trip per pair trade); the gate's 3 bps is per pair.
G. DONE, FAIL 2026-11-11, see journal. crypto-fee-check: plan question 8. Data: .cache/crypto/m1_<BASE>_<YEAR>.pkl
   (BTC, ETH, SOL, DOGE, LTC, AVAX, LINK; 2021-26; filled by
   scripts/fill_crypto_cache.py; UTC, all hours, no sessions). Alpaca's tier-1 fees
   are 0.25% taker / 0.15% maker a side (docs, read 2026-11-07), so costs are 25
   bps/side (market orders); report 15 too. First the break-even question: on the
   spike rule (vol x8, |move| >= 5 sd, trailing 30 bars, rolling through midnight,
   hold 60), what is the mean gross move, and how many trades beat 50 bps round trip?
   Then one rule with a reason to clear the fee, e.g. 4-hour time-series momentum
   (Liu & Tsyvinski 2021, "Risks and returns of cryptocurrency", RFS). Gate as
   intraday but with these costs; "years" are 2021-26 with same sign in 2021-23 and
   2024-26 instead of the 2016-20 split.
H. options-iv-lead: plan question 7. BLOCKED on history: Alpaca's option bars start
   2024 and need keys; world_recorder.py has saved SPY/QQQ chains twice a day since
   2026-11-05. Revisit when 60 sessions are recorded (about February 2027).

C. spike-regime-filter: retrain monthly on all earlier data, take or skip each
   signal using market trend, realized volatility, time of day, sector. Scored
   only on later months. Must beat the fixed rule by a pre-registered margin.

1. short-term-reversal: each week buy the pool's 5% worst 5-day performers
   and short the 5% best, hold 5 sessions (Lehmann 1990). Daily. Excess vs
   pool.
2. overnight-drift: hold the 62 liquid names close-to-open only vs
   open-to-close; is the overnight share of returns tradeable with
   MOC/MOO costs (2 bps/side)? 1-min.
3. turn-of-month: SPY/QQQ long from the last session of the month through
   the 3rd session of the next (Lakonishok & Smidt 1988). Daily (pool has
   no ETFs: use the 1-min cache for SPY/QQQ, resampled).
4. spike-afternoon-wide-iex: does spike_mom's afternoon edge hold on IEX
   detection for the 12 core names in 2025-26 (the live pod's feed)?
5. 52wk-high-momentum: buy pool names within 2% of their 52-week high,
   hold 20 sessions (George & Hwang 2004). Daily, excess vs pool.
6. last-half-hour-continuation: SPY/QQQ, if the first 30 minutes and the
   13:30-15:30 return agree, ride the last 30 minutes (Gao, Han, Li & Zhou
   2018 variant using the afternoon). 1-min.
7. gap-fade-largecaps: the 62 names, fade opening gaps of 1-3% that have
   no earnings release (EDGAR) in the prior 24 h, exit at 11:00. 1-min +
   EDGAR.
8. eleven-am-effect: recent NYSE small-cap study finds 30 s-5 min returns
   efficient but 45-60 min horizons show persistent patterns incl. an
   11:00 effect. Test on the 62 names: sign of 10:00-11:00 return vs
   11:00-12:00, and time-of-day return profile. 1-min.
9. reversal-near-52wk-high: Quantpedia's #3 of 2021 combines 52-week-high
   proximity with short-term reversal; run as an interaction of ideas 1
   and 5 (losers that are near their 52wk high). Daily, excess vs pool.
10. overnight-intraday-reversal: in the pool, names with the most extreme
    overnight (close-to-open) return reverse during the next session's
    day part; trade at the open, exit at the close. "Does Overnight News
    Explain Overnight Returns?" (Glasserman, Krstovski, Laliberte &
    Mamaysky, arXiv 2507.04481, 2025) and Lou, Polk & Skouras "A tug of war"
    (JFE 2019). Daily open/close; 2-5 bps/side is the problem.
11. reversal-by-volatility: Wei & Yang (SSRN) report large, low-volatility
    stocks reverse while large, high-volatility stocks continue over a
    week; run as a split of idea 1 by trailing 60-day volatility. Daily,
    excess vs pool.
12. half-hour-seasonality: a stock's return in a given half-hour of the
    day predicts its return in the same half-hour on later days (continuation
    at multiples of one day, lasting weeks). Heston, Korajczyk & Sadka,
    "Intraday Patterns in the Cross-section of Stock Returns" (J. Finance
    2010). Long/short the 62 (+215) names in each half-hour by their mean
    same-half-hour return over the prior 20 days. 1-min; 2 bps/side vs
    half-hour returns is the hurdle.
13. end-of-day-reversal: a stock's return from the open to 15:30 predicts
    a reversal in its last half hour (cross-sectional, opposite to index
    intraday momentum); the paper notes it vanishes once the next open is
    included. Baltussen, Da et al., "End-of-day reversal" working paper
    (Notre Dame, ~2024). 1-min, the 62 (+215) names, exit at 15:59.
14. factor-zoo-market-timing: lagged high-frequency cross-sectional returns
    predict the market's next intraday return, with ETF trading profits after
    costs reported. Aleti, Bollerslev & Siggaard, "Intraday Market Return
    Predictability Culled from the Factor Zoo" (Management Science 2025). Crude
    version: the 277 names' last-30-min returns, fit on past data only, predict
    SPY's next 30 min. 1-min; we have no factor data, so stock returns stand in.
15. etf-jump-news-split: in intraday ETF data, jumps with no macro news reverse for
    at least 60 minutes, jumps on scheduled news also reverse and jumps on
    unscheduled news continue. Xiao, Yin & Zhao, "Jumps, News, and Subsequent Return
    Dynamics: An Intraday Study" (J. Financial Research, 2020); seen in a search
    summary only, read the paper first, and it reports no after-cost figures. Test on
    SPY/QQQ: spike_mom's signal, faded and followed, split by a fixed macro calendar
    (shares that calendar with idea B). 1-min; few signals, so n >= 300 may fail.
16. anomalies-on-news-days: anomaly returns are reported as about 2x higher on corporate news days and 7x on earnings
    days. "Anomalies and News" (Engelberg, McLean & Pontiff, 2015 working paper; seen in an Alpha Architect recap,
    read the paper first; returns reported are gross, long-horizon sample 1979-2013). Test: short-term reversal and
    momentum ranks on the 277 names, entered only after a headline day from .cache/news. Daily, excess vs pool; 5
    bps/side is the hurdle.
17. spy-noise-boundary: SPY goes long (short) when price leaves a "noise area" above (below) the open, the band
    being the average absolute move from the open at that time of day over the prior 14 sessions; checked every
    30 minutes, trailing stop at the band or VWAP, flat at the close. Reported net of commissions and slippage,
    2007-2024, Sharpe 1.33. Zarattini, Aziz & Barbon, "Beat the Market: An Effective Intraday Momentum Strategy
    for S&P500 ETF (SPY)" (SSRN / Swiss Finance Institute RP 24-97, 2024-25); seen in search summaries only, read
    the paper first. Differs from the rejected first-half-hour rule: the trigger is a time-of-day volatility band.
    SPY/QQQ 1-min 2016-26; one trade a day at most, so n >= 300 needs both ETFs or all years.
18. short-term-momentum-high-turnover: last month's return reverses on average, but among the highest-turnover
    stocks it continues ("short-term momentum"), the reversal being confined to low-turnover names. Medhat &
    Schmeling, "Short-term Momentum" (Review of Financial Studies 2022); Alpha Architect's recaps say the effect
    sits in liquid, cheaper-to-trade stocks. Test: pool names, monthly, double sort on prior 21-day return and
    21-day share turnover (needs shares outstanding, not cached; the stated proxy is 21-day volume over the
    prior 252-day average volume, a weaker stand-in, say so), long top-turnover
    winners and short top-turnover losers, hold 21 sessions. Daily, excess vs pool, 5 bps/side. Differs from
    idea 1 (weekly, no turnover split).
19. crypto-half-hour-tsmom: Bitcoin's first half hour of the (UTC) day predicts its last half hour, attributed
    to liquidity provision; reported economic gains, costs unclear. Shen, Urquhart & Wang, "Bitcoin intraday
    time-series momentum" (Financial Review, 2022); seen in a search summary, read the paper first. Test on the
    7 cached coins, long-only (Alpaca has no crypto shorts), at 25 bps/side: likely too small, so report gross first.
20. peer-overnight-spillover: economically linked peers' overnight returns lift a stock's overnight return, and
    that gain reverses during the day. Wang, "Decoding Spillover" (JFQA, accepted 2025); seen in a search summary,
    read the paper first. Test: pool names, peers = the 5 highest-correlation names over the prior 60 sessions
    (no industry data cached), fade the focal name's open-to-close when peers' overnight return is extreme.
    Daily open/close, 5 bps/side.
