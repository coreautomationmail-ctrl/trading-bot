# Shift gears after swarm-replay-v1 (2026-11-21, overnight research)

## Where we are

swarm-replay-v1 FAILED both registered tests (8ba9b2b): over 1,222 sessions (2022-01 .. 2026-11) the
evolving economy did no better than random churn (eco - control t -0.22) and every book lost money
after costs (about -2 to -4 bps/day). That is the 8th approach to fail: PEAD on 8-K dates, Kronos filter,
headline tone S1 and S2, spike-down without news, ORB, turn-of-month SPY, and the swarm.

The common cause: every one of them looked for a repeating price shape in large caps, where the other side
is the fastest, best-informed traders in the world, using free data everyone has. None started from a
reason someone else pays us. Chen & Velikov ("Zeroing In on the Expected Returns of Anomalies", JFQA 2023):
across 204 published anomalies, expected returns become negligible after spreads, publication decay and
the modern market. Our failures fit that pattern.

New rule for any STRATTON test: before data, name WHO is forced or constrained to trade, WHEN, and WHY
that leaves money for a small, unconstrained trader. No counterparty sentence, no test.

## Options, ranked

### 1. Index-deletion auction reversal (paper-testable, recommended if STRATTON continues)

- Who pays: index funds must sell deleted names AT the closing auction on the effective date (they are
  measured against the index's closing price). Concentrated selling pushes the auction price down; the
  price recovers next morning when the pressure is gone. We are free of the tracking constraint.
- Evidence (checked): Hendrix, Liu, Roberts, "Measuring the Costs of Index Reconstitution: A Global
  Perspective" (Dimensional, 2025), US intraday data 2019-2023: deletions -30 bps from the 4 pm continuous
  close into the auction, then +63 bps from the auction to the next open, relative to non-rebalanced stocks;
  additions +9 then -13. Industry research, relative moves, not a net strategy.
- Decay (checked): Greenwood & Sammon, "The Disappearing Index Effect" (JF 80(2), 2025): the broad
  announcement-to-effective index effect shrank to <1% for additions, ~-0.6% for deletions (2010-2020).
  That is a different, longer window than the overnight auction reversal; the auction effect can survive
  while the announcement effect has gone.
- Data (tested tonight): Alpaca's free historical auctions endpoint (/v2/stocks/auctions, feed=sip) returns
  official opening and closing auction prints back to at least 2019 on our key (AAPL 2019-03-01: open O
  174.23, close 6 174.97). Daily bar open/close are NOT auction prices; use this endpoint. Delisted names'
  coverage not yet checked.
- Events: S&P DJI press releases (free, name the effective date and the reason for each change). S&P's own
  study: ~21 S&P 500 deletions a year, ~10 "clean" (2011-2021), almost all migrations to the 400/600.
  Adding the 400 and 600 should give several dozen usable events a year, a few hundred since 2019:
  enough to measure a 30-60 bps effect, too few for a fine-grained search. Russell has no complete free
  2019-2026 event list; start with S&P only.
- Classify, don't just exclude: drop acquisitions, bankruptcies, spin-offs, share-class swaps and events
  announced after the decision time; keep migrations (500->400, 400->600) as their own category, since a
  migration is selling by one index family and buying by another.
- Fit with the owner's day-trader goal: it is a close-to-open hold around a known event, executed with
  market-on-close and market-on-open orders. Fully paper-testable.
- Odds: moderate that the relative effect is still there; unknown that it survives market risk on a
  long-only overnight hold. Small sample is the main risk (inconclusive rather than a clear fail).

### 2. Odd-lot tenders and exchange offers (real money only; owner decision)

- Who pays: an issuer making an oversubscribed tender or exchange offer accepts ALL shares from holders of
  fewer than 100 shares, while large holders are prorated. Being small is the edge itself.
- Evidence (checked): Lennar's Millrose exchange offer, final results 2025-11-26: proration 8.604228% for
  ordinary holders, odd-lot tenders accepted in full (778,625 shares). Offer was ~$106.38 of Millrose per
  $100 of Lennar, subject to a cap.
- Catch: it can't be paper-traded (a paper account can't tender), so it needs a real brokerage account,
  which STRATTON has never had (owner rule: paper only). Episodic (a handful of offers a year), price risk
  between buying and the exchange, and each deal needs its terms read. Small but real money per event.
- This is the only option on the list that pays because we are small rather than because we are clever.

### 3. Not recommended

Leveraged-ETF close flows (Lenkey 2024 survey: effects economically insignificant), Rule 201 short-sale
bounce (only 2011-2013 evidence), unconditional overnight holding (Glasserman et al. 2025: "falls short of
being a viable trading strategy" because of turnover), more intraday price-rule bots in large caps.

### 4. Stop active research

Keep the paper log running (spike pod, bench, swarm until its futility stop), spend no more sessions on
STRATTON, put the time into the channels. Reasonable given 8 failures and the profit-from-content goal.

## Recommendation

Option 1, capped: one pre-registered test, built and run on a Saturday daytime, no Claude per trade, no
new money. If it fails, go to option 4 for good. Option 2 only if the owner wants to open a small real
account; it is the cleanest edge but breaks the paper-only rule.

## Draft pre-registration (NOT in replay_registry.json until the owner says go)

- id: index-deletion-auction-v1
- events: S&P 500/400/600 deletions and migrations from official S&P DJI releases, effective dates
  2019-01-01 .. 2026-11-13; classified as exit / migration-down / migration-up; excluded: acquisition,
  bankruptcy, delisting, spin-off, share-class change, effective within 1 session of announcement.
- trade: buy at the closing auction on the last session before the effective date, sell at the next
  opening auction (Alpaca auctions endpoint, SIP). Cost: 5 bps a side (smaller names), and a 10 bps
  variant reported.
- benchmark: the same close-to-open hold on SPY and on 20 seeded random S&P 1500 names that night.
- pass: exits+migration-down pooled: mean net > 0 AND t >= 2.5 vs SPY AND vs random, both halves positive
  (split at the middle event). Migration-up reported, not gating. ONE run.
- data check first (no results looked at): coverage report of events -> resolved symbol -> both auctions
  present. If coverage < 80%, stop and report before testing.
