# STRATTON swarm: plan (2026-11-17, approved by the owner the same day)

A forward-only paper test of a population of simple rule bots, plus one allocator that decides
how much each bot trades. No backtests, no AI agents per trade, zero Claude plan usage once built.
This reopens STRATTON research (closed 2026-11-14) at the owner's request, as paper only.

Where the ideas come from (owner request: social media, 2026-11-17):
- Algovibes, "I Made 1,000,000 Trading Bots Fight to the Death": on an unseen year the single
  champion lost money and the population didn't. So back the population, not a winner.
- AI Pathways, "Claude Tested 100,000 Price Action Strategies": far more variants made money on
  higher timeframes than on 1-minute charts. Filters that held up in their unseen years: New
  York session only, skipping trades that go with a 3-day run, waiting for a confirmation candle.
- Both judged on huge backtests. Here nothing is judged on history: every bot starts at zero
  forward trades.

## 1. The question, fixed before any data

Does an allocator that shifts paper money toward whichever bots are winning *live* beat
(a) the same bots with equal money, and (b) a random allocation, after costs?

That is the only thing judged (registry entry `swarm-v1`). The bots are its inputs. They are
recorded and reported, never passed or failed one by one, and never placed in the bench's
ladder. This means no bot ever places a paper order on its own.

Why this shape: the registry holds 12 tests (bar z 2.87). Judging bots one by one would push it
past 3. One registered test moves it to 13 (bar 2.89). That bar is high on purpose: STRATTON has
had 12 tries, and the guard against a lucky pass doesn't reset.

## 2. The population (v1)

Each family is a simple rule, run across a fixed grid of round settings. No setting is chosen
because it worked: every grid point runs. All bots trade the bench's `SHADOW` universe (62 large,
liquid US names) and pay the bench's cost of 2 bps a side.

**Intraday** (`pods/bench/swarm_bots.py`, live 1-minute IEX bars, inside the bench loop; at most
one signal per bot per symbol per day, flat before 15:55):

| Family | Rule | Grid |
|---|---|---|
| gap | gap from the prior close > g; fade it or go with it at time t | g 0.5/1/2% x fade/go x t 09:35/09:45 x hold 30/60/120 min = 36 |
| orb | first break of the first r-minute range; go with it or fade it | r 5/15/30 x go/fade x hold 30/60/120 = 18 |
| vwap | after 10:30, price more than d from the day's VWAP; revert or follow | d 1/1.5/2.5% x revert/follow x hold 30/60 = 12 |
| trend | return over the last L minutes > x and on the same side of VWAP; follow or fade | L 30/60 x x 0.5/1% x follow/fade x hold 30/60/120 = 24 |
| late | at time t, the day's biggest mover (largest absolute move from the open) in the universe; fade or follow | t 14:00/14:30 x fade/follow x hold 30/60 = 8 |

98 intraday bots. The two spike strategies already on the bench (spike-quiet, spike-news) join
as inputs from `bench_virtual.csv`, which makes 100.

**Daily** (`pods/bench/daily_swarm.py`, daily IEX bars, run once after each close; enter at the
next open, exit at the close `hold` trading days later):

| Family | Rule | Grid |
|---|---|---|
| dmom | N-day return > 0: long; < 0: short | N 20/60/120 x long-only/both x hold 5/20 = 12 |
| drev | N-day return below -x: long; above +x: short (both sides) | N 2/3/5 x x 2/4% x hold 1/3/5 = 18 |
| dbrk | close at an N-day high: long; at an N-day low: short; go with it or fade it | N 20/55 x go/fade x hold 5/10/20 = 12 |
| drev3 | drev, but skipping a trade that goes with a 3-day run (the AI Pathways filter) | N 2/3 x x 2/4% x hold 1/3 = 8 |
| night | buy at the close, sell at the next open: every day / after a down day / after an up day | 3 |

53 daily bots. 153 in total.

Not in v1: 4-hour bars (the IEX bar API makes them from 1-minute data; possible later), the
evolutionary version (the worst bots die, the best breed), and new families from more research.
Each later addition is a new registry entry, `swarm-v2`, never a change to v1.

## 3. The allocator (`pods/bench/swarm.py`), fixed now

- **A bot's daily return** is the mean net bps of the trades it closed that day, or 0 with none.
  A book's daily return is the sum of weight times bot return.
- **Weights:** all equal at the start. Each Friday after the close:
  `w <- w * exp(0.01 * sum of that bot's daily returns over the last 20 trading days)`. Then each
  weight is floored at 0.2/N and capped at 0.30, and the weights are renormalised to sum to 1.
- **The equal book** uses 1/N throughout.
- **The random book** uses Dirichlet(1) weights, redrawn each Friday from a seed fixed by the date.
- A bot with no trades yet keeps its weight. A bot that stops trading drifts toward the floor
  only through its zero returns.

## 4. Pass bar (registered as `swarm-v1` before the first scored trade)

- **Primary:** daily return of the allocator minus the equal book. Day t >= bar(K) (2.89 at
  K = 13), over at least 60 trading days.
- **Also required:** the allocator's mean daily return > 0, it beats random on day t > 0, and both
  halves of the window are positive.
- **KILL:** after 120 trading days, day t < 1, or the allocator's mean daily return <= 0.
- **Anything else is INCONCLUSIVE.** It runs on as a report, with no new allocator variants for a
  quarter.
- **On PASS:** the allocator's top bots move to the incubation paper account at the bench's fixed
  sizes, through the owner. Real money remains the owner's decision and is out of scope.

## 5. Reporting

One Telegram message each Saturday, with the ladder: allocator vs equal vs random so far, days
counted, the day t and its distance to the bar, the 5 heaviest bots, and the count of bots with
trades.

## 6. What it costs

- **Money:** $0, paper only.
- **Claude plan usage:** the build session only. Nothing runs on Claude afterwards.
- **Time to an answer:** 60 trading days (about 3 months) is the minimum. Reaching z 2.89 needs a
  large, steady edge, so a real but modest one may take 6+ months or end INCONCLUSIVE.
- **The likely outcome:** INCONCLUSIVE or KILL. An allocator can't create an edge its bots don't
  have. A PASS would show that switching between styles beats holding all of them.
