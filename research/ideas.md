# STRATTON idea queue

Top = next. Each line: slug, the idea, why it might work, data needed.
Already tested (see research/journal.md and git history): fixed-list ORB,
stocks-in-play, gap-and-go (incl. SSR-filtered shorts), index intraday
momentum, spike_fade, vwap_fade, event drift on any big move, RSI2, Kronos
standalone, spike_mom (PASS, live as the spike pod), PEAD on real earnings
dates (2026-11-02, see journal).

1. pre-earnings-runup: buy the pool names 5 sessions before a scheduled
   earnings release, sell the day before it (Barber et al. 2013, "earnings
   announcement premium"). Daily + EDGAR. Release dates must be known in
   advance: use the previous year's same-quarter date + 364 days as the
   forecast, never the actual date.
2. short-term-reversal: each week buy the pool's 5% worst 5-day performers
   and short the 5% best, hold 5 sessions (Lehmann 1990). Daily. Excess vs
   pool.
3. overnight-drift: hold the 62 liquid names close-to-open only vs
   open-to-close; is the overnight share of returns tradeable with
   MOC/MOO costs (2 bps/side)? 1-min.
4. turn-of-month: SPY/QQQ long from the last session of the month through
   the 3rd session of the next (Lakonishok & Smidt 1988). Daily (pool has
   no ETFs: use the 1-min cache for SPY/QQQ, resampled).
5. spike-afternoon-wide-iex: does spike_mom's afternoon edge hold on IEX
   detection for the 12 core names in 2025-26 (the live pod's feed)?
6. 52wk-high-momentum: buy pool names within 2% of their 52-week high,
   hold 20 sessions (George & Hwang 2004). Daily, excess vs pool.
7. last-half-hour-continuation: SPY/QQQ, if the first 30 minutes and the
   13:30-15:30 return agree, ride the last 30 minutes (Gao, Han, Li & Zhou
   2018 variant using the afternoon). 1-min.
8. gap-fade-largecaps: the 62 names, fade opening gaps of 1-3% that have
   no earnings release (EDGAR) in the prior 24 h, exit at 11:00. 1-min +
   EDGAR.
