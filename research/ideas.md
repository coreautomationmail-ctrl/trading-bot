# STRATTON idea queue

Top = next. Each line: slug, the idea, why it might work, data needed.
Already tested (see research/journal.md and git history): fixed-list ORB,
stocks-in-play, gap-and-go (incl. SSR-filtered shorts), index intraday
momentum, spike_fade, vwap_fade, event drift on any big move, RSI2, Kronos
standalone, spike_mom (PASS, live as the spike pod), PEAD on real earnings
dates and pre-earnings run-up (2026-11-02, see journal).

Revamp priorities (docs/stratton-revamp-plan.md), tested before anything below:

A. DONE, FAIL 2026-11-06, see journal. spike-down-no-news: spike-news (2026-11-06) failed its gate, but its gap came
   entirely from down spikes: down with no headline -3.7 bps, down with a headline
   +16.1, up spikes no gap. That was found by looking, so rank 1-25 can't confirm
   it. Pre-register on rank 26-100 only, 2017-26, with the 60-minute single-name
   rule from scripts/research/spike_news.py (.cache/spike_news_scored.csv has
   n60 and side per trade): does skipping down + no-news improve the group by at
   least +3 bps, t >= 2, same sign in 2017-21 and 2022-26? News-tagged down spikes
   there are few, so report n and say if it can't settle the question.
B. spike-events: is the spike edge concentrated on event days (FOMC decision
   days, top-1% tails)? Report the edge with and without them. Score it on daily
   P&L too: per-trade t 3.34 is only 1.33 by day, and the biggest days look macro.
   Define event days from a fixed calendar, never from which days had many signals.
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
