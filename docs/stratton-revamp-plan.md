# STRATTON revamp plan

Approved 2026-11-05. Paper only, $0 budget. Phases 0 and 1 are live; see the build order for the rest.

Update 2026-11-07 (owner): Phase 2 two of three failed; the desk now tests new intraday
signals first (ideas.md D-H), then self-retraining. Phase 3 started early: the shadow
bench and ladder run on the VPS (pods/bench), with spike-news and its no-news twin as
watch-only. The live spike pod stays on its own script until a replay match. Decisions:
auto-promotion on paper YES (ladder.py rules); crypto and options IN scope, traded only
if they pass; thesis pod waits for the engine.

## The goal

Turn STRATTON from one rule that waits for a signal into a fund that each day
notices what is happening, remembers what it has learned, comes up with its own
questions, tests them, and moves paper money based on the evidence.

"Conscious" here means that loop, written down and checkable: every belief has
evidence behind it, every decision has a stated reason, and every reason gets
scored later. The bot won't be aware in any real sense, but you will be able to
ask why it did something and get a true answer.

## Where it stands today

| What I checked | Finding | What it means |
|---|---|---|
| Money at work | 3 trades, 60 min each, about $5k, over 4 sessions: about 0.6% of the account's capital-time | This is the "inactive" you feel |
| Signal rate | 7 signals across all 62 watched names in 4 sessions (replay of the saved live bars) | The rule is rare even on a wide list. The "10x more evidence" I claimed for the shadow log is really about 1.5 signals a day |
| Other money | ORB paused 11/02 (no edge). Sleeve runs weekly | The main account is close to idle |
| Learning in the pod | Only pause or grow. Growing needs about +18 bps a trade at 100 trades, the backtest is +8 | Nothing changes what it trades, and it will probably never grow |
| Research desk | 10 of 10 journal tests failed. One idea a day, nearly all price patterns | Honest, but looking in one place |
| Path to trading | A PASS stops at "candidate", then waits for a person | Even a success changes nothing by itself |
| Biggest movers | 27 of the 30 biggest movers 11/3-11/5 had no signal at all (hindsight list) | The rule can't see most of what moves. A source of questions, not proof |
| What it reads | Prices and earnings dates | It never sees news, filings, flows or the calendar |

Shadow log check: the replay found 7 signals and the log has 5. One came before
the container started on 11/03. The other, BIDU 14:58 on 11/04, is unexplained.
So it is mostly working, not the big bug I suspected.

## Principles

1. **Lots of curiosity, strict evidence.** Ideas can come from anywhere, but they
   all pass the same pre-registered tests. Activity alone is not the goal.
2. **Never tune on recent P&L.** Rules change only through pre-registered tests.
   Money moves only by fixed formulas that start from the backtest.
3. **Forward evidence beats backtests.** Live paper results and shadow logs can't
   have hindsight in them.
4. **LLMs suggest, code executes.** Claude never places an order. It writes a
   structured decision, and a plain deterministic program checks and runs it.
5. **Failures are assets.** Each one records why it failed and what would justify
   reopening it.

## The design: six parts

### 1. Senses: a daily record of the world

A small recorder on the VPS saves what was known at each moment, so later tests
can't cheat. This data can't be backfilled, so it's worth starting early.

Free sources, all tested with our paper key or a plain request on 11/05:

| Source | Status | Use |
|---|---|---|
| Alpaca Benzinga news | Works, including March 2016 | Spike with or without news; Claude's reading |
| Alpaca movers and most-actives | Works | What is hot outside the list, live |
| Alpaca options contracts and indicative snapshots | Works | Implied volatility and skew. History depth not checked |
| Alpaca crypto bars | Works | Markets that run nights and weekends |
| FINRA daily short volume | Works | Flow signal |
| SEC EDGAR Form 4 and 8-K | Works | Insider buying, filing text |
| Our own IEX archive and shadow log | Live | Live vs backtest |

Computed daily: market trend, realized volatility, breadth, dispersion, calendar
(FOMC, CPI, earnings, opex). Not available for free: SIP data live and in the
last 15 minutes.

### 2. Memory: a belief ledger

Replace "read the whole journal" with a structured `research/beliefs.json` that
the journal feeds. Each entry has the claim, status, evidence with links,
confidence, what would change its mind, and when to look again.

For example, "spike momentum works in the afternoon on top-dollar-volume names"
keeps its live score next to the backtest. Failed ideas record why they failed,
so the desk can't quietly retest them, but can reopen one if new data appears.

### 3. Imagination: five idea sources instead of one

| Source | How it works |
|---|---|
| Scout | Exists: papers and practitioner research |
| Anomaly miner | New: a daily script finds what's odd today and asks what came before it. Hypotheses only |
| Red team | New: Claude attacks our own beliefs and looks at inversions, e.g. Kronos *disagreeing* did about 11 bps better than agreeing |
| Signal grammar | New: an overnight batch tries hundreds of rule combinations, with pass marks raised for the number of tries and a held-out slice |
| Cross-domain | New: a monthly prompt borrows from other fields and markets, looking for a mechanism first and a pattern second |

First questions for the desk, in order:

1. **News vs no news:** do spikes with a headline run further than spikes without?
   Chan (2003) found drift after news and reversal without. It needs only news
   and bars we can already get, and could lift the live pod directly.
2. **Is the spike edge mostly event days?** Past notes wondered about FOMC at
   14:00, and it was never tested. Top 1% of trades make 45-63% of P&L.
3. **Self-retraining filter:** retrain monthly on all earlier years and decide
   which signals to take. Scored only on later data.
4. **Catching steady movers** without hindsight, since those are most of the misses.
5. **Insider cluster buying:** several insiders buying within days (Lakonishok &
   Lee 2001). No after-cost evidence found, which is what we would test.
6. **Market-neutral pairs and sector-relative moves:** a different kind of edge,
   not in the journal.
7. **Options signals:** implied-volatility moves ahead of stock moves.
8. **Crypto pod:** active nights and weekends. Fees may kill it, so check them first.
9. **FINRA short volume:** low priority, since the evidence is mixed.

### 4. Judgment: an evidence ladder

Every idea climbs the same steps, automatically on paper:

| Stage | Entry test | Money | Leaves when |
|---|---|---|---|
| Idea | Has a reason it might work | None | Tested or retired |
| Backtest | Pre-registered, today's gate | None | Pass or fail |
| Shadow | Passed, or a strong near-miss, and logged | None, virtual trades | Forward results match, or it clearly doesn't |
| Incubation | Forward results fit the backtest | $1-2k a trade | Enough trades to judge |
| Pod | Passed the live checks | Allocator decides | Scorecard retires it |

Rules for each stage are fixed before a candidate enters, and the existing gate
never loosens. Near-misses can enter Shadow because watching costs nothing, but
they cannot skip a step.

This needs one engine running many pods as plugins, replacing a script built
around one rule. The live spike pod moves to it only after it matches today's
behavior in a side-by-side replay.

The growth rule also gets fixed: judge against the backtest using the combined
live and shadow evidence, so that it can actually fire if the edge is real.

### 5. Thesis pod: Claude as a portfolio manager (the out-of-the-box bet)

This is the closest thing to what you are describing. Each morning Claude reads
overnight news, filings, the movers and the belief ledger, then writes timestamped
theses: what, which direction, why, how long, what would prove it wrong.

- **Virtual book first.** A program scores every thesis, net of costs, against SPY
  and against a random-picks baseline.
- **Forward only.** Backtests can't be trusted here because the model may remember
  history. Published replications found that claimed LLM advantages shrink over
  longer and broader tests, with regime-aware risk control mattering more.
- **Its best output may be ideas.** A thesis that repeatedly works can be turned
  into a rule and sent up the ladder.
- **Clear exit.** After 300 scored theses it either beats both baselines with t >= 2,
  or stops trading virtually and stays on as an idea source.

### 6. Rhythm: making it proactive

| When | What | Reaches you |
|---|---|---|
| 08:45 ET | Pre-market brief: regime, events, what's being watched, theses | Telegram, short |
| Market hours | Pods run; alerts only for real anomalies | Rarely |
| After close | Review: every trade and notable miss, beliefs changed | Telegram, short |
| Overnight | Signal batch and desk test on the PC | Verdict in the morning |
| Saturday | CIO memo: what we learned, what's climbing, what was retired, what I'd change | Telegram plus file |

STRATTON can also ask you questions, batched into the Saturday memo so they fit
your weekends.

### Allocator

Off until 3 pods are live. Then inverse-volatility weights, which won the earlier
skfolio test, with a slow tilt based on evidence that starts from the backtest and
has hard caps per pod. It moves money between pods but never changes their rules.

## Safety rails

- Paper only: the paper-URL check stays, and the go-live lock is unchanged.
- A small risk program, which no LLM can edit, enforces position limits, per-pod
  and fund-wide daily loss stops, and a kill switch.
- The desk keeps its current limits: research files only, no keys, no orders.
- Auto-promotion can't raise limits or change the risk program, and every move is
  logged and sent to you.
- News and web text are treated as data, since a headline could try to steer Claude.

## Build order

| Phase | When | Build | Done when | Stop or change if |
|---|---|---|---|---|
| 0 | Days 1-2 | Explain the BIDU miss, fix the growth rule, write baseline metrics, set up the recorder | Recorder has run a full session | |
| 1 | Week 1 | Daily brief and review, belief ledger seeded from the journal | First week of briefs read well | You find them noise: cut them back |
| 2 | Weeks 1-2 | Questions 1-3 above | Pre-registered verdicts recorded | All three fail: drop self-retraining and lean on new data |
| 3 | Weeks 2-4 | Multi-pod engine, ladder, shadow bench | Spike matches in replay, 3+ candidates in shadow | Replay differs: spike stays on the old script |
| 4 | Weeks 3-5 | Signal grammar and anomaly miner, questions 4-9 | 20+ ideas tested a week | False passes pile up: raise the bar |
| 5 | Weeks 4-8 | Thesis pod, virtual | 300 theses scored, probably 3-4 months | Fails: keep it as an idea source only |
| 6 | When 3 pods are live | Allocator | Weights running | |

## How we'll judge it

Measured weekly from Phase 0:

- Ideas tested, candidates at each stage, and share of decisions with a recorded reason
- Live results vs backtest for each pod, and correlation between pods
- Capital-time in use, tracked but deliberately **not** a target
- Pass rate on held-out data: if it falls well below the design-data pass rate, we're overfitting

At 90 days, success is several independent candidates in forward testing and a
ledger that has visibly changed its mind. P&L won't settle it: the backtest's
per-trade spread implies roughly 500 trades to confirm an 8 bps edge.

## What could go wrong

- **Nothing new passes.** Quite possible given the 10 straight fails. New data is
  the best counter, and the shadow bench still gives honest evidence.
- **Lucky results from mass testing.** Handled by raised pass marks, held-out data
  and forward shadow before any money.
- **Claude usage limits.** Scripts do the data work and Claude only does judgment.
  If limits bite, the order to cut is thesis pod, then the post-close write-up.
- **Compute.** The VPS has about 1 GB free and 2 cores, enough for the recorder and
  engine. Batches stay on the PC at 2 workers, and the RAM issue means they must
  resume cleanly after a crash.
- **PC-dependent jobs.** The desk's report sync needs the scheduled task that's still
  unregistered. Without it the desk works from stale reports.
- **Too many messages.** Two short messages a day and one memo, then cut if it's noise.

## Decisions for you

| Decision | My recommendation | What would change it |
|---|---|---|
| Auto-promote on paper without asking? | Yes, with fixed limits and a notification | If you'd rather approve each one (slower) |
| Thesis pod: virtual or real paper orders? | Virtual first | Real paper orders mean a third account, which you'd have to create |
| Crypto and options in scope? | Test both, trade only if they pass | Keep to stocks if you want it simpler |
| Claude usage | Desk daily, brief and review daily, thesis pod in week 4 | Your usage limits |

## Sources

- FINSABER: [Can LLM-based Financial Investing Strategies Outperform the Market in Long Run?](https://arxiv.org/abs/2505.07078)
- [Alpaca historical news docs](https://docs.alpaca.markets/us/docs/historical-news-data)
- Insider clusters, Lakonishok & Lee (2001), via [QuantInsti's Form 4 event study](https://blog.quantinsti.com/sec-form-4-insider-trading-python-event-study/) and [this secondary summary](https://www.heygotrade.com/en/blog/insider-buying-bullish-signal-how-to-track-without-bloomberg/)
- Short volume: [Diether, Lee & Werner (2005)](https://users.nber.org/~confer/2005/mmf05/werner.pdf)
- Chan (2003), "Stock price reaction to news and no-news", is cited from memory: check it before the test is pre-registered.
