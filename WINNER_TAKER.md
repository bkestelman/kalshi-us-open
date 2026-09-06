# `winner_taker.py` — design

Sells tournament-winner legs of players whose current match is nearly lost,
when the winner book's resting bid is above what the match book implies.

Measured in `FINDINGS.md` (2026-08-24): **+3.59c/contract over 25 days, 117,797
contracts, ~51 legs, 0 of 425 takes settling YES.** Every other match state
loses badly under the same rule, so the dying condition is the strategy.

## The trade

One player, two books:

    M(t)  = their match-winner price, 1c wide, millions of contracts
    W(t)  = their tournament-winner leg, 3-8c wide, thin

Winning the tournament requires winning this match *and* everything after, so

    fair(t) = M(t) * R        R = P(win the rest | win this match)

`R` needs no model. Both books are quoted before a ball is struck, so `R` is
observed directly as the time-weighted median of `W/M` taken **before the
match starts**, and frozen there. When `M` collapses, `fair` collapses with
it; if the winner book has not followed, its resting bid is above fair and we
sell into it.

`R` is deliberately NOT sampled during play. The winner book does not mark
down as fast as the match book, so `W/M` climbs as the player collapses --
measured 2026-09-05 across four days of observations, on all five legs whose
match ran from competitive to lost, by 3x to 14x. Muchova read 0.061 while
competitive and 0.833 while dying; the second number prices her leg at a fair
of 2.5c against the 2c bid that was actually resting there, and declines the
trade. Sampling stops at the first ball, which the exchange now states
(`discovery.match_phases`).

We are always the **taker**, always the **seller**, and we hold to settlement.

### Once the player is out

When the last point is played, the loser's match book goes one-sided -- no
bid, an ask at a cent -- so `M` has no mid at all. That is not missing data,
it is the exchange saying the player is out, and it arrives well before the
market formally settles: Anisimova's book went one-sided at 16:48:07 on
2026-09-05 and the market did not close until 16:55:11.

From that point `fair = 0` exactly, with no `M` and no `R` in it, and
`min_edge` does not apply -- it exists to buffer error in `R`, and there is no
`R` here. What remains is the fee. This matters more than it sounds: the most
a 1c contract can ever earn is 0.93c, so a full cent of required edge makes
every 1c bid unreachable at any fair value whatsoever. The elimination rule
and the edge floor had to change together or neither would have fired.

The **comeback caps do not apply.** `per_leg_cap`, `per_event_cap` and
`per_day_cap` all price the same event -- the player wins after all -- and an
eliminated player has no comeback left to price. Pricing it anyway is not
free: on 2026-09-05, 937 contracts rested at 1c across Anisimova's two legs
and `per_leg_cap` (`hard_cap/4` = $37.50, binding on the PLAYER) took 37 of
them, because her winner leg spent the whole budget before her FIN leg was
scored. Eliminated collateral is netted out of the risk caps too, or it would
re-impose that cap on the next player.

`hard_cap`, `elim_cap` and `cash_reserve` still bind. Those are the money
existing rather than the risk being priced, and no reading of a book can
conjure collateral we do not have.

**This is not free money, and `elim_cap` is not a formality.** "The player is
out" is *inferred* from a book shape, never known, and the inference is
sometimes wrong. Over 2026-09-04..06, 46 of 94 match-winner markets showed the
shape and one -- Zheng, `KEYZHE` on 09-05 -- went on to win the match. That
false read stood for 181.5s across three episodes and cost nothing only
because no leg of hers had a resting bid to hit (`no-bid` on all 1,003
evaluations). That is market behaviour, not a guarantee: a maker who leaves a
stale bid on a forward leg through a match-point scare is the loss case, and
at 1c the payoff is 99:1 against. The naive false-signal rate, 1/45 = 2.2%,
is above the 0.93% that break-even allows; what keeps the trade positive is
that a loss needs the player to reach the round we sold, not merely to win
this match (`q = P(false) x R`, ~0.33% at `R` = 0.15). That margin is thinner
than the certainty language above suggests, and it rests on one observed false
positive -- which has not itself settled.

## Entry

Fire when **all** hold:

| gate | value | why |
|---|---|---|
| `M < 0.10` | match mid, that player's leg | the only state where this rule is positive; +3.59c vs −17.24c competitive |
| `R` known | >= 50 samples of `W/M` at `M > 0.30`, **same match** | an `R` guessed from elsewhere is the circularity that sank the earlier version |
| `bid > fair + fee(bid)` | `fee(p) = 0.07*p*(1-p)` | fee is charged on the sale and is ~1c at these prices |
| `bid >= MIN_PRICE` (0.03) | | a 1c leg pays at most 1c and locks 99c |
| `edge >= MIN_EDGE` (0.01) | `bid - fair - fee` | the tick is 1c; smaller "edges" are rounding |
| `roc >= MIN_ROC` (0.03) | `edge / (1 - bid)` | collateral is locked for days; this is the real currency |
| caps not breached | see Risk | |

**No persistence wait.** The cutoff sweep is monotone against it — 0s beats
15s beats 60s beats 600s on both edge and volume. Waiting removes opportunity
rather than selecting quality. The risk it was meant to address (the bid
disappears before we reach it) is handled by IoC, which is free.

**Order:** IoC sell-YES at the bid price, size `min(resting, per-leg room,
capital room)`. A moving book fills less or not at all, never worse. Nothing
rests, so nothing needs cancelling or hedging.

## Sizing and risk

Collateral is `(1 - price)` per contract, locked until the **tournament**
settles — days, not the match. That is the binding constraint: the 25-day
measurement needed $111,907 of collateral to earn $4,233.

- `HARD_CAP` (dollars of locked collateral, default **$150**) clamps
  everything, checked against live positions at startup so a restart cannot
  double-commit. Hot-reloaded from `data/live/winner_taker.json` on a 1s
  cadence, so the account can be topped up without stopping the bot.
- `PER_LEG_CAP` and `PER_EVENT_CAP` — one comeback should not be able to take
  the account. Default `HARD_CAP/4` and `HARD_CAP/2`. `PER_EVENT_CAP` at
  `HARD_CAP/2` measured better than no event cap at all: filling the account
  on one draw crowds out the next. `PER_LEG_CAP` at `HARD_CAP/2` measured
  better than `/4` — but across 27 legs of which zero settled YES, which is
  exactly the tail the cap exists for, so `/4` stays the default.
- `PER_DAY_CAP` (default off, `= HARD_CAP`) paces new commitment. Measured a
  pure cost; it is there for when the loss tail stops being hypothetical.
- ~~**Prioritise by ROC, not edge.**~~ **Measured 2026-08-25: a no-op, and
  the tick it needs costs a third of the P&L.** Qualifying legs essentially
  never coexist (27 legs over 21 days), so there is never a queue to sort;
  `fifo`, ROC-first and edge-first return identical dollars at every tick
  length, while firing on a 0.5s tick instead of the book update drops
  $27.88 to $18.45. `MIN_ROC` stays, as a plain floor rather than a reserve
  price: a floor that rises as the account fills also measured worse than
  flat. See FINDINGS.md, "Allocation, measured".
- `MAX_P` (0.15): never sell a leg priced above this. The catastrophic buckets
  are all expensive legs, and a leg that rich is a contender.

### Position management: hold, and log

Default is **hold to settlement**, because +3.59c is a hold-to-settlement
number. If the player wins the match, `fair` jumps and the position is marked
badly — but covering means crossing an 8c book, and converting a rare large
loss into a frequent medium one is not supported by anything measured.

`COVER_ON_RECOVER` (default off) will buy back if the match mid climbs back
above `0.30`. It exists so the question can be settled from live fills rather
than argued: every position logs its mark at match resolution regardless.

## What it needs to run

- Websocket feed of match books + winner legs — `collector_ws.Discovery`
  already discovers exactly this pair and has since before this strategy
  existed.
- `tourn_map.json` (`build_tourn_map.py`) to join a match key to the right
  winner event via `product_metadata.competition`. A player code identifies
  the *player*, so a live match carries winner legs for every tournament they
  are entered in; pairing on the code alone shorts the wrong tournament.
- Trading key wherever the bot runs. This used to say "laptop only,
  never on the capture box"; that was retired 2026-08-26, since the bot is
  deployed to the VPS and the trading key was already on that box.

## Failure modes this design takes seriously

- **A match first seen already under way has no `R`.** There is no pre-match
  window left to sample, so the bot must **skip that match**, not guess.
  Losing a trade is cheaper than inventing a fair value. A RESTART does not
  hit this: `R` is checkpointed every 30s, keyed to the match it came from
  and expiring at `R_MAX_AGE`, so it is reloaded rather than resampled.
- **Stale books.** Every price that reaches a trade decision comes from the
  live orderbook, never a list endpoint. This repo has been burned twice
  (`TENNIS_DEADLEG.md`); the 20-31c "arbs" were settlement-freeze phantoms.
- **A snapshot resync** says nothing about order age. Irrelevant now that
  there is no persistence gate, but the same trap will return if one is added.
- **Bo5.** The men's US Open draw is best-of-five. Nothing here models sets —
  `M` is observed, not derived — so the rule transfers unchanged. This is the
  one strategy in the project that is *not* blocked on the Bo5 rederivation.

## Open questions, in the order they should be answered

**Answered 2026-08-25 (see FINDINGS.md):** how to allocate scarce capital.
Take on the book update, never on a timer; do not rank; keep a flat 0.03 ROC
floor and turn *that*, not `HARD_CAP`, when the capital is wanted elsewhere.
A $150 account earns ~$1.33/day on the 15-day sample, not the $0.64 that
question 1 below assumes — collateral turns over as tournaments settle.

1. ~~**What does MECNET actually net?**~~ **ANSWERED 2026-08-26: nothing, at
   entry.** Four 1-contract sells, two of them in the same mutually-exclusive
   event, each cost `(1 - p) + fee(p)` — ratio 1.00 against naive on every row.
   Collateral is `sum(1 - p)`. So `HARD_CAP` buys exactly what it says.
   The "$16 per 25 days" ceiling in this question was wrong on its own terms
   (it ignored that collateral turns over as tournaments settle); the measured
   figure is $27.88 per 21 days on a $150 cap, and it was already computed
   under per-leg collateral, so nothing needs re-deriving. The four probe legs
   are left open so the balance can be re-read as they settle, in case excess
   is returned on a schedule rather than at entry.
2. **Does the loss tail exist?** 0/425 is not 0 probability. The US Open adds
   ~120 more legs across two draws; one comeback run costs ~95c/contract.
3. **Does the edge survive a deeper book?** The measurement is tour-level
   events. The US Open winner books are ~10x deeper — more size available, and
   more competition for the same stale bids.

## Deliberately out of scope

- The `cheap` + competitive bucket (+5.71c, $42,224). Larger, but it is the
  longshot bias with 3 realised YES legs — higher variance, different trade.
- Dead legs, in every form. Dropped 2026-08-23.
- Any use of `M x R` while the match is competitive. It loses six figures per
  25 days in the measurement; it is the trap this whole line of work walked
  into twice.
