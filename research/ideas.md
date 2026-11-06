# STRATTON idea queue

Top = next. Each line: slug, the idea, why it might work, data needed.
Already tested (see research/journal.md and git history): fixed-list ORB,
stocks-in-play, gap-and-go (incl. SSR-filtered shorts), index intraday
momentum, spike_fade, vwap_fade, event drift on any big move, RSI2, Kronos
standalone, spike_mom (PASS, live as the spike pod), PEAD on real earnings
dates and pre-earnings run-up (2026-11-02, see journal).

Revamp priorities (docs/stratton-revamp-plan.md), tested before anything below:

A. spike-news: do spike_mom signals with a headline on that name shortly before
   the signal bar run further than those without (Chan, "Stock price reaction to
   news and no-news", JFE 2003: drift after news, reversal without; check the
   citation)? Needs `.cache/news`; if it isn't filled yet, record BLOCKED and go
   to B. Pre-register the look-back window. Hindsight check: created_at must be
   before the signal bar.
B. spike-events: is the spike edge concentrated on event days (FOMC decision
   days, top-1% tails)? Report the edge with and without them.
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
