# US Open 2026 — strategy scoping

Measured 2026-08-23 against the live Kalshi API and 1-minute candle history.
Prior work: `../kalshi-tennis` (maker, settled-P&L study), `../kalshi`
(takers, `TOURNAMENT_WINNER.md`, `TENNIS_DEADLEG.md`).

## What is different about the US Open (all verified today)

1. **The winner book is now real.** `KXATP-26USO`: 34 legs, 6.21M contracts
   traded, 5.41M open interest, **1c spread on all 17 priced legs**, with
   9.6k @ 0.33 bid / 243k @ 0.34 ask on Alcaraz and 23.5k @ 0.09 on Djokovic.
   `KXWTA-26USO` is ~10x thinner (649k volume) but still 1c wide.
   The premise of `TOURNAMENT_WINNER.md` — a 10-27c-wide sparse winner book
   lagging a fast match book — is **dead**. Sum of yes-bids is 0.990 and sum
   of yes-asks 1.330, i.e. exactly 34 ticks of overround: as tight as the grid
   allows. There is no multi-leg arb and no fat lagging book to pick off.
2. **`collateral_return_type: MECNET`** on both winner events. Mutually
   exclusive collateral netting. This is the direct answer to the objection
   that killed the dead-leg winner trade ("selling a winner leg at 5c posts
   95c for *days*"). Unverified how much it actually nets — see D1.
3. **Zero maker fees where it matters.** `KXATP`/`KXWTA` (winner),
   `KXATPSETWINNER`, `KXATPEXACTMATCH`, `KXATPGTOTAL`, `KXATPGSPREAD`,
   `KXATPADVANCE`, `KXATPACES` are all `fee_type: quadratic` = taker-only fees.
   Only `KXATPMATCH`/`KXWTAMATCH` carry maker fees. Note `KXUSOMENSINGLES`
   *does* carry maker fees but is not the series being used — the US Open
   winner is listed under `KXATP-26USO`.
4. **Slam legs carry ~20x the volume of the ATP 250s the bot was trading.**
   Wimbledon SF Sinner-Djokovic: match 15.2M, set-winner 833k/776k/806k per
   set, exact-score 779k, game-total 636k, game-spread 508k. The Jul-Aug
   capture that produced `FINDINGS_SELECTIVITY.md` came from set legs doing
   20-45k. Deep queues everywhere.
5. **Men are best-of-5.** Exact-score legs become X30/X31/X32 (6 legs);
   set-winner extends to 5 sets. Every identity in
   `FINDINGS_SELECTIVITY.md` §4 and all of `TENNIS_DEADLEG.md`'s arithmetic
   must be rederived. `sim_maker.leg_code`/`classify` and `dead_leg_taker`
   assume best-of-3. **The women's draw stays best-of-3** and the existing
   code applies unmodified.
6. **New leg families never studied:** `KXATPGTOTAL` (total games),
   `KXATPGSPREAD` (game spread) — now listed on every tour match, several
   hundred k volume at a slam, 8-20c spreads live, zero maker fee.
   `KXATPACES` (49 Wimbledon events). `KXATPADVANCE` (reach SF / reach final).

## The best new idea: the ADVANCE book is the same claim as the match book

`KXATPADVANCE-26WIMSEMI-X` ("X reaches the semifinals") is, *while X is playing
their quarterfinal*, exactly `KXATPMATCH-<that QF>-X`. Same for
`-26WIMFIN-X` during the semifinal, and for the winner leg during the final.
No R-factor, no model — an exact identity between a 15M-volume 1c book and a
13k-volume 3-8c book.

Measured on 1-minute candles over all 12 Wimbledon men's QF/SF pairs, scoring
both directions and charging the Kalshi taker fee `0.07*p*(1-p)` on **both**
legs:

| pair | minutes | gross-crossable | **net-crossable** | best net |
|---|---|---|---|---|
| QF Djokovic | 298 | 110 | **55** | **+18.7c** |
| QF Struff | 161 | 48 | **48** | +2.5c |
| QF Auger-Aliassime | 300 | 10 | 5 | +13.9c |
| SF Djokovic | 286 | 37 | 13 | +0.8c |
| QF Fritz | 300 | 42 | 3 | +1.7c |
| SF Zverev | 274 | 30 | 3 | +1.8c |
| SF Sinner | 261 | 43 | 2 | +0.7c |
| QF Zverev | 260 | 6 | 2 | +1.3c |
| SF Ferrer | 295 | 1 | 1 | +1.8c |
| QF Sinner / QF Cobolli | 186 / 279 | 0 / 4 | 0 / 0 | — |

11 of 12 legs cross gross; 9 of 12 survive fees; two legs (Djokovic QF,
Struff QF) offer 48-55 separate net-positive minutes. Fees are the binding
constraint — `0.07*p*(1-p)` is 1.12c per side at p=0.20, so a 2c gross
crossing does not survive, and the surviving edge concentrates where the
ADVANCE book sat 3-18c stale for tens of minutes at a time.

Per slam this identity exists 12 times per draw (8 QF + 4 SF) plus 2 at the
final against the winner book, i.e. ~28 exact pairs across both US Open draws,
concentrated in the second week.

**Caveat, and it is the same one that has burned this repo twice:** candles
carry **no size**. Every number above is a price crossing, not a fill. The
live snapshot is discouraging on this point — Wimbledon ADVANCE legs traded
only 4-800 contracts per minute, and `TENNIS_DEADLEG.md`'s rule stands:
list/candle prices discover opportunities, live orderbooks decide trades.

## Ranking

**A. Run the existing set/exact maker on the US Open, women's draw first.**
Only strategy with a settled, gated, sim-validated spec plus two live
shakeouts. Two reasons to expect it to do better here than in Winston-Salem:
the min_join result (2026-08-18/19 live, 628 fills — placements joining <=10
resting lots ran -11.4c/ct and were negative in 8/8 matches; gating at T=10
turned those days from -$2.53 to +$9.92) says the bot earns where queues are
deep, and a slam has deep queues everywhere; and volume is ~20x. Women's draw
is best-of-3 so `CLOSE_MIN=0.30`, the sibling gate and `leg_code` all hold
unmodified. Men's draw needs the Bo5 rederivation first — do not point the
current code at it.

**B. Cross-book pair against the ADVANCE / winner books.** New, model-free,
measurable before the tournament starts. Second week only. Blocked on depth.

**C. ~~Bo5 dead legs~~ — DROPPED 2026-08-23 (user decision).** Not pursued:
the measurement never found anything there. Kept below only so it is not
re-derived from first principles a third time.

~~Bo5 dead legs.~~ Best-of-5 creates strictly more provably-dead exact
legs than Bo3 (after a 1-1 split both X30 legs die; after a player loses two
sets their X30 and X31 both die), on books ~20x deeper than the ones that
produced `TENNIS_DEADLEG.md`'s ~$485/day of best-snapshot premium. Model-free.
Same depth/latency unknowns.

**D. New leg families (game total / game spread / aces) as maker venues.**
Wide spread + zero maker fee is the same shape as the exact legs, which were
the best capture leg (+4.0c) and the worst holding (-4.1c). Unknown adverse
selection; cheap to capture, expensive to guess at.

**Refuted, do not re-raise:** taker lag-chasing between match and set books;
identity arbitrage in the match/exact complex; the R-scaled winner-tracking
trade. On the last one I re-measured Djokovic's Wimbledon SF today: the
winner leg tracked the match book to within a tick or two all match, and the
implied ratio W/M wandered 0.47-0.82 — the fair band is still wider than the
lag, exactly as `TOURNAMENT_WINNER.md` concluded, even now that the book is
1c wide.

## Data that would verify this

**D1. What MECNET actually nets** (minutes, decides B and C's sizing).
Sell 1 contract on two different `KXATP-26USO` legs and read the balance
delta. If collateral is the netted worst case rather than $0.99/contract, the
dead-leg and pair trades change by an order of magnitude in ROC.

**D2. Depth and persistence on the ADVANCE/winner books** (the blocker for B).
Candles cannot answer it. Point an orderbook logger at `KXATP-26USO`,
`KXWTA-26USO` and — once listed — `KXATPADVANCE`/`KXWTAADVANCE` legs, at
`identity_logger.py` resolution. Needed *before* the second week.

**D3. Extend `collector_ws.py` before Aug 31.** Add `KXATPGTOTAL`,
`KXATPGSPREAD`, `KXATPACES`, `KXATPADVANCE`, `KXATP-26USO`/`KXWTA-26USO` to
discovery, and confirm it handles Bo5 set tickers (`-4-`, `-5-`) and the 6-leg
exact complex. Zero risk, zero capital, and the only way to answer whether
slam microstructure differs. Run it through US Open qualifying (from ~Aug 25)
so the pipeline is warm on day 1.

**D4. Finish the candle study while it is free.** History reaches back to at
least 2026-07-02, i.e. all of Wimbledon and the French Open. Extend the 12
pairs above to Wimbledon women, French Open both draws, and the winner-leg-
vs-final pairs — ~48 exact pairs. This is the only way to size B on more than
one tournament before the US Open starts, and it costs nothing but API calls.

**D5. Re-run the existing pipeline on slam capture.** `pop_settled.py` →
`slice_settled.py` → `sim_maker.py select`/`minjoin` on US Open week 1, as
out-of-sample for `CLOSE_MIN=0.30` and the min_join gate. `power.py` says the
current standard error (1.47c/ct) still exceeds the edge being argued about;
a slam's ~100 matches/day closes that gap far faster than 21/day did.

## Decisions taken 2026-08-23

- **Dead legs are dropped entirely**, in every form — the winner-book variant,
  the Bo5 exact variant, and `../kalshi/dead_leg_taker.py`. The premium is
  gone by the time it is reachable; `TOURNAMENT_WINNER.md` measured $0.00
  harvestable across ten eliminated legs, the Wimbledon winner-book trace
  showed Djokovic's leg already at 0.03/0.04 forty-five minutes before his
  semifinal ended, and the collateral was never worth it. Do not re-raise.
- **Women's draw only to start.** The WTA draw is best-of-3, so `leg_code`,
  `classify`, the sibling identities and `CLOSE_MIN=0.30` all hold unmodified.
  The men's draw needs the Bo5 rederivation before any code points at it.
- **Capture box is fine** — already t3.micro with 50 GB (14 G used, 34 G free),
  which absorbs the +60% the new legs cost. The enhanced collector is deployed
  and live: 52 tickers across 3 matches, against ~30 before.

## Schedule

`KXATP-26USO` expected expiration 2026-09-14T05:00Z (final Sep 13), so the
main draw starts ~Aug 31 — about 8 days. `KXATPADVANCE`/`KXWTAADVANCE` legs
are not listed yet; `KXATPNATSTAGE-26{QF,SF,FIN}` already are.

---

# The two complement books (2026-08-23)

A Kalshi match event lists **both** players as separate markets, and the
exchange does **not** cross-match them. "SHI wins" is SHI-YES and it is
equally KEC-NO, resting in two books with two queues. Same for every set-
winner pair. What the existing code does with that:

- `maker_bot.market_key()` nets the pair for **capital** (correctly: it
  refuses to net across set-2 and exact, which can both pay).
- `maker_bot.Book` consolidates yes/no **within one ticker** (correctly:
  `best_ask = 1 - max(no)`).
- **Nothing reads the sibling leg's book when deciding a price.** Every quote
  and every reference price comes from a single leg.

`../kalshi-tennis/complement_books.py` measures what that costs, on a
2-minute live capture (2 ATP matches, 32,701 updates with both legs live):

| | better SELL on complement | better BUY on complement | crossed net of fees |
|---|---|---|---|
| set legs | **66.1%** of updates, avg 2.82c, median 1,244 lots | 4.1%, avg 1.78c, median 50 lots | **0** |
| match legs | 5.7%, avg 1.00c, median 45 lots | 4.0%, avg 1.01c, median 1,154 lots | **0** |

Typical case, `KXATPSETWINNER-26AUG23SHIKEC-2`:

    SHI book   0.24 / 0.26
    KEC book   0.73 / 0.75   ->  SHI is 0.25 / 0.27 in SHI's terms
    synthetic  0.25 / 0.26   ->  a 2c book and a 2c book make a 1c book

## Three conclusions, in order of how much they are worth

**1. There is no arbitrage here.** Zero of 32,701 updates crossed net of both
legs' taker fees, and the fee math says why: `0.07*p*(1-p)` is 1.75c per
contract at p=0.5, so a two-leg take needs >3.5c of gross disagreement at the
money and the books never disagree by more than a tick. The tell is that
*both* legs report a better sell at the same instant — syn_bid(SHI) +
syn_bid(KEC) = 0.25 + 0.74 = 0.99. They sum to less than 1, not more. Any
"free money" reading of the 66% number is the same phantom
`TENNIS_DEADLEG.md` documented; my first pass at this had buy and sell
inverted and printed a 1,325-observation arbitrage that does not exist.

**2. Taker routing is real and free.** Every take should price both books and
hit the better one. Worth ~1c, available on two thirds of updates in the set
legs with four figures of size behind it. This matters most for the pieces
that already cross the spread — `dead_leg_taker`, the flatten path in
`maker_bot`, and any of the US Open taker ideas — and it is pure improvement
with no new risk. Cheapest real win on the list.

**3. Quoting the sibling instead is NOT a queue-position play — but the
join/improve rule should be redefined against the synthetic book.** At the
level equivalent to our own best bid, the complement book is empty ~98% of
the time, so there is no shorter queue to join over there; and resting alone
at a level is exactly the case the min_join study found toxic (fills joining
<=10 resting lots ran -11.4c/ct, negative in 8/8 matches). So the direct
answer to "quote yes on both legs instead of both sides of one" is no.

The real consequence is subtler and probably bigger. `sim_maker`'s
`improve` arm (+$33.36 against join's +$43.79) and the bot's `improve=False`
rule are both defined against a **single** book. In the example above,
resting a 0.25 bid in SHI looks like *improving* SHI's 0.24 book, but 0.25 is
already the market's bid via KEC's no@0.25 x800 — it is *joining* the
synthetic touch. Conversely, resting at SHI's own 0.24 "best bid" is quoting
a tick behind the real market, which is why it fills less and, when it does,
fills better. Two thirds of set-leg updates are in this state, and the whole
edge being argued about is ~2.6c of capture, so a systematic 1c
misclassification of every join is not a rounding error.

**Next step for this:** add a `synthetic` arm to `sim_maker.py` that computes
best bid/ask across both complement legs and re-runs the join/improve
comparison against it. That is a sim question, answerable on the 12 days of
capture already on disk — no new data needed.

---

# Results, 2026-08-24 (25 days of capture, 336M messages)

Eight `c7i.large` boxes over `ws_20260730` → `ws_20260822`, via
`vps/run_analysis.sh`. Bucket is `kalshi-tennis-411691564448` (the README's
`kalshi-tennis-data` is a placeholder). Total spend well under $1.

## 1. The synthetic book is a null — and the maker config is negative

| arm | PnL $ | contracts | c/ct |
|---|---|---|---|
| join | −251.02 | 101,119 | −0.25c |
| **synthetic** | **−273.30** | 98,059 | **−0.28c** |
| improve | −236.12 | 124,199 | −0.19c |
| synth_ms1 | −527.45 | 148,631 | −0.35c |
| join_ms1 | −529.26 | 148,625 | −0.36c |
| synth+c30 | −102.80 | 36,341 | −0.28c |
| **join+c30** | **−58.62** | 37,395 | **−0.16c** |

Pricing against the consolidated touch does not help. The clean read is the
ms1 pair, which strips the MINSPREAD confound: −0.35c vs −0.36c. **The
hypothesis was wrong** — the 66% "better price on the complement" is a real
measurement that does not convert into maker P&L. Taker routing still stands
on its own and is untested here.

**The more important result is that nothing in this table is positive.**
`join+c30` — the live config plus the gate `FINDINGS_SELECTIVITY.md` §7 called
"the only net-positive arm" — runs −$58.62 / −0.16c over 25 days, positive in
1 of 4 chunks. `close30` was chosen on the same three days it was scored on,
and out-of-sample it does not survive. That document's own caveat predicted
this. Caveat on the caveat: these arms carry no `outlay_cap`, so peak capital
is ~$3,000 against a $424 account, and the day mix differs from the original
three days.

**This removes the last demonstrated-positive arm from strategy A.** Ranking
A first was predicated on `close30`; that predicate is gone.

## 2. winner_lag: real, small, capital-hungry, and possibly not a lag at all

93 episodes, all settled, 80/93 positive at the most conservative R.

| | episodes | expected @ p90 R | realised | collateral | ROC |
|---|---|---|---|---|---|
| all | 93 | +$3,494 | +$4,978 | $167,057 | 2.98% |
| **dwell ≥ 1 effective minute** | **52** | **+$871** | **+$1,254** | $86,613 | 1.45% |

**75% of the expected edge was sub-minute** — books caught mid-collapse, not
standing bids. The Aug 10-14 chunk went from $1,932 to $170 after filtering:
91% artifact. Peak-ever size cannot tell a bid that stood for 45 minutes from
a screenshot, which is why `winner_lag` carries a dwell column.

Two things stop this being a strategy yet:

- **Capital.** $86,613 of collateral over 25 days against a $424 account. The
  MECNET probe (D1) is now the binding question, not a nicety.
- **The realised column is near-deterministic.** A player whose match is under
  10% almost always loses, so the legs almost always settle at 0. Only the
  expected-at-p90-R column is an ex-ante number.

### The overround confound

A winner book's legs sum to ~0.99 bid / 1.33 ask. Selling *any* leg at *any*
time may simply collect that overround, in which case the sub-10% trigger is
decoration and there is no lag trade. `winner_control.py` separates them with
no fair-value model: sample every winner leg's bid on a cadence, tag it with
its holder's match state, settle against the real result.

On 2026-08-20 alone, and within the same <15c price band:

| match state | c/ct |
|---|---|
| dying <0.10 | **3.05c** |
| sliding .10-.30 | 3.47c |
| competitive >.30 | **6.24c** |
| no live match | 6.29c |

Selling was profitable in every bucket and **least** profitable while the
match was dying — the overround signature, not the lag one. That day had
`legs_yes = 0` (no leg settled YES, so nothing paid the ~95c cost), which
makes it unreadable as an expectation; the 25-day run is in flight.

**If the full control repeats this, winner_lag is not a lag trade** and the
honest statement is that selling winner legs collects an overround, which is a
different (and much more capital-hungry) business than the one proposed.

## Revised ranking

1. **B — cross-book pair against ADVANCE/winner books.** Now the only
   candidate with an exact identity and no demonstrated-negative result.
   Still blocked on depth (D2).
2. **D — new leg families as maker venues.** Untested; the capture now
   collects them.
3. **A — the set/exact maker.** Demoted. No positive arm on 25 days.
4. **winner_lag.** On hold pending the control.

---

# The winner-leg taking rule, resolved (2026-08-24)

Three scans, each correcting the last. Order matters, because two of them were
misleading on their own.

1. `winner_lag.py` — scored episodes it was pointed at. Found +$871 expected
   over 52 episodes, but 75% of the raw edge was books caught mid-collapse
   (dwell < 1 min), and it could not say whether the sub-10% trigger mattered.
2. `winner_control.py` — sampled every winner-leg bid on a cadence and settled
   it. Found selling indiscriminately is **−2.75c/ct** (the overround does not
   pay: eventual winners cost ~90c each), and that within cheap legs the dying
   bucket *under*performed. **This was the wrong comparison** and I reported it
   as if it settled the question. It never required the bid to be above fair,
   which is the entire rule.
3. `winner_take.py` — the ex-ante rule: sell when the best bid has already
   rested >= T seconds *and* is above fair + fee, fair = M x R, R the running
   median of W/M from earlier in that same match. Match state is a dimension.

## Result

`rich` rule (bid > fair + fee), 25 days, settled, by state:

| T | dying <0.10 | sliding .10-.30 | competitive >.30 |
|---|---|---|---|
| 0s | **+3.59c / +$4,233** | −21.78c / −$113,130 | −17.24c / −$216,097 |
| 60s | **+2.38c / +$1,590** | −7.69c / −$6,377 | −15.38c / −$30,749 |
| 600s | **+2.14c / +$941** | +3.37c / +$1,135 | −4.15c / −$1,550 |

**The sub-10% trigger is the strategy, not decoration.** Requiring a bid above
M x R while the match is competitive selects players whose winner leg is rich
because they are genuinely strong — you end up short real contenders, and 6 of
those legs settled YES. The dying condition is what makes the rich rule
survivable, and it is positive at every cutoff.

## Opportunity, and the cutoff sweep

| T | contracts | c/ct | total $ | collateral | ROC | $/day |
|---|---|---|---|---|---|---|
| **0s** | **117,797** | **3.59c** | **$4,233** | $111,907 | 3.78% | $169 |
| 15s | 90,820 | 3.18c | $2,889 | $86,279 | 3.35% | $116 |
| 60s | 66,854 | 2.38c | $1,590 | $63,511 | 2.50% | $64 |
| 600s | 43,945 | 2.14c | $941 | $41,748 | 2.25% | $38 |

25 days, ~51 distinct legs. **Persistence filtering is strictly harmful** —
it removes edge and volume monotonically, so it throttles rather than selects.
60s was not merely arbitrary, it was costly. Take immediately; handle the
"bid vanishes" risk with IoC, not with waiting.

## What is still unknown, and it is the important part

- **0 of 425 takes settled YES.** Structurally sensible (a player under 10%
  must win this match *and* every round after) but it means the loss tail was
  never observed. At M=0.09, R=0.18 the implied per-leg risk is ~1.6%; ~7 YES
  legs would be expected across 425 independent takes, and these are 425 takes
  over 51 legs. Zero is a small-sample outcome, not proof.
- **Capital is the binding constraint, not edge.** $111,907 of collateral for
  $4,233. On a $424 account that is ~0.4% of the opportunity, ~$16 per 25 days.
  **MECNET is now the highest-value open question in the whole project** — if
  shorts across one mutually-exclusive event net, this changes by an order of
  magnitude.
- `cheap` + competitive shows +5.71c on $42,224 — bigger than anything here,
  and it is the longshot bias `winner_control` found, with 3 realised YES legs.
  A different and higher-variance business. Not this bot.

---

# Allocation, measured (2026-08-25)

`WINNER_TAKER.md` specified the entry rule but left two questions to judgement:
with far more opportunity than capital, **which** takes get the money, and is
ranking worth the tick it needs to see more than one candidate at a time. Both
are answerable from capture already on disk, so neither should have been
argued.

`alloc_extract.py` replays the 15 local capture days (`ws_20260730`..
`ws_20260811`, `ws_20260820`; 21.0 days of span) and emits every change to a
watched leg's top of book once its match is dying and R is known — including
the bid falling out of the band and the bid **disappearing**, which is the only
way a simulated delay can be made to cost anything. 924 rows, 27 legs,
7 events. `alloc_study.py` then runs a capital-constrained allocator over that
stream: collateral is `(1-p)` per contract and is released when the event
closes, so the sim cannot be sharded by day.

Baseline is $150 with the doc's caps (per-leg $38, per-event $75).

## 1. Every delay is a straight loss

| tick | takes | contracts | c/ct | pnl $ | peak $ | %/$-day |
|---|---|---|---|---|---|---|
| **immediate** | **33** | **367** | **7.60c** | **$27.88** | $114 | 6.12% |
| 0.5s | 29 | 271 | 6.81c | $18.45 | $101 | 5.16% |
| 1s | 28 | 266 | 6.85c | $18.22 | $97 | 5.22% |
| 5s | 19 | 250 | 6.77c | $16.93 | $84 | 5.35% |
| 15s | 13 | 231 | 6.74c | $15.56 | $74 | 5.78% |

**Half a second costs a third of the P&L**, and it is monotone from there.
Same shape as the persistence sweep above, for a different reason: a
persistence gate *selects* (it requires the bid to survive), while a tick just
*misses*. Both lose. Take on the book update.

## 2. Ranking is an exact no-op

At every tick length, `fifo`, best-ROC-first and best-edge-first return
**identical** dollars, contracts and takes. Qualifying legs essentially never
coexist — 27 legs over 21 days, and a dying match with a stale bid is a rare
state — so there is never a queue to sort. The doc's "prioritise by ROC, not
edge" is solving a problem that does not occur at this account size, and the
tick it would need is the expensive part.

Note also that inside the 3-15c band the two orderings barely differ anyway:
`(1-p)` runs 0.85 to 0.97, so ROC can only reorder a near-tie.

## 3. The ROC floor is a rate/dollars dial, and 0.03 is the knee

| floor | takes | pnl $ | peak $ | ROC | %/$-day |
|---|---|---|---|---|---|
| 0.00 | 44 | $28.82 | $149 | 19.3% | 3.80% |
| 0.02 | 37 | $28.61 | $149 | 19.2% | 3.99% |
| **0.03** | **33** | **$27.88** | **$114** | **24.4%** | **6.12%** |
| 0.05 | 21 | $15.15 | $74 | 20.4% | 7.51% |
| 0.08 | 3 | $8.27 | $37 | 22.1% | 10.74% |

0.03 keeps 97% of the dollars while using 77% of the capital. Below it you buy
the last 3% of dollars with a third more collateral; above it dollars fall off
a cliff while the rate keeps climbing. **This is the dial to turn when the
capital is wanted elsewhere**, not `hard_cap`.

A floor that RISES as the account fills (the reserve-price idea — refuse a 1%
bid early so a 6% bid later can be funded) measured worse than flat at every
schedule tried: 0.03→0.10 gives $17.61, 0.02→0.08 gives $22.12, against flat
0.03's $27.88. Same reason as §2: the better opportunity it is saving for does
not arrive while the capital is still held.

## 4. Caps

| per-leg (event cap off) | pnl $ | | per-event | pnl $ | | per-day | pnl $ |
|---|---|---|---|---|---|---|---|
| $15 | $13.87 | | $38 | $20.52 | | $38 | $19.63 |
| $38 (cap/4) | $29.72 | | **$75 (cap/2)** | **$39.36** | | $75 | $24.11 |
| $75 (cap/2) | $38.61 | | $150 (off) | $35.61 | | **$150 (off)** | **$27.88** |
| $150 (off) | $35.61 | | | | | | |

Both concentration caps pay for themselves at cap/2 and cost money tighter
than that — and, notably, an event cap of cap/2 beats no event cap at all,
because filling the account on one draw crowds out the next one. Per-day
pacing is a pure cost in this sample.

**The per-leg number is not a free lunch and should not be read as one.** It
says concentration earned more across 27 legs of which **zero settled YES**.
The cap exists for the tail that this sample, like the 0/425 above, never
observed. cap/4 stays the default.

## 5. What more capital buys

| cap | takes | pnl $ | peak $ | %/$-day |
|---|---|---|---|---|
| $150 | 33 | $27.88 | $114 | 6.12% |
| $300 | 36 | $50.29 | $227 | 6.25% |
| $500 | 37 | $79.01 | $377 | 6.38% |
| $1,000 | 41 | $133.96 | $752 | 5.91% |
| $2,000 | 60 | $221.44 | $1,502 | 5.11% |
| $5,000 | 81 | $314.73 | $2,584 | 4.45% |

Near-linear to $500 and sublinear past $1,000.

**This corrects the "$16 per 25 days" ceiling asserted above.** That number
scaled the unconstrained $111,907 of collateral down linearly, which assumes
capital is committed once and never comes back. It does come back: these
positions are opened late in a tournament and released when it settles, so
$150 turns over repeatedly and earns $27.88 over 21 days — $1.33/day, not
$0.64. MECNET (D1) is still the question that would change this by an order of
magnitude, but the strategy is not a paper exercise without it.

## Caveats, and they are the same two as always

- **0 of 27 legs settled YES.** Every dollar above is "nothing came back".
  The loss tail remains unobserved, and one comeback costs ~95c/contract.
- **The sample is 15 days and 33 takes.** Directional, not precise.
- **These are tour-level books.** The live US Open winner book is 1c wide with
  11,108 contracts resting at 0.09 on Djokovic (checked 2026-08-25). The
  premise of the trade is a stale, wide winner book, and a 1c book is the
  opposite of that. Whether the rule still fires at a slam is open question 3,
  and the bot's observation log is what will answer it.

---

# D1 answered: MECNET does not net at entry (2026-08-26)

`../kalshi-tennis/mecnet_probe.py --live`, four 1-contract sells, balance read
between each:

| ticker | event | price | cash delta | naive 1-p | ratio |
|---|---|---|---|---|---|
| KXATP-26USO-DJO | KXATP-26USO | 0.09 | $0.9158 | $0.9100 | **1.01** |
| KXATP-26USO-SHE | KXATP-26USO | 0.07 | $0.9346 | $0.9300 | **1.00** |
| KXWTA-26USO-RYB | KXWTA-26USO (control) | 0.07 | $0.9346 | $0.9300 | 1.00 |
| KXATP-26USO-FIL | KXATP-26USO | 0.06 | $0.9440 | $0.9400 | **1.00** |

Rows 2 and 4 are the test — a second and third short inside the *same*
mutually-exclusive event, where netting would have to show up. They cost the
same as the first. **Collateral is `sum(1 - p)`, not `1 - sum(p)`.**

The deltas sit slightly *above* naive `1-p`, and the excess is exactly the
taker fee: `0.07*0.09*0.91 = 0.00573`, and `0.9100 + 0.00573 = 0.9158`. Same on
every row. So entry costs `(1 - p) + fee(p)` and the fee is charged at entry,
not at settlement.

`collateral_return_type: MECNET` therefore does not mean margin is netted when
the position is opened. It may still mean excess is *returned* once the
exclusivity is provable; the four legs are deliberately left open, so re-read
the balance as `KXATP-26USO` legs settle to check. Nothing in the strategy
depends on that being true.

**This does not change any number in the allocation study above**, which
already charged `(1-p)` per contract. The "$16 per 25 days" ceiling that D1 was
raised against was already wrong for a different reason (it ignored that
collateral turns over), and the corrected figure — $27.88 per 21 days on a $150
cap — was computed under exactly the regime just confirmed.

What it does close: the hope that a small account could carry an order of
magnitude more position inside one draw. It cannot. `hard_cap`, `per_leg_cap`
and `per_event_cap` all mean what they say.

---

# The player-code collision (2026-08-26)

Found in the paper bot's own log, an hour after it started, while checking a
schedule detail that turned out to be the tell: the main draw had not started
and every "US Open" match on the board was a QUALIFIER.

    KXATPMATCH-26AUG24MEJDRA-DRA   "Liam Draxl"      (qualifier)
    KXATP-26USO-DRA                "Jack Draper"

Both carry `product_metadata.competition = "US Open Men Singles"`. Kalshi
labels qualifying with the same competition string as the main draw, and a
three-letter player code is unique only WITHIN an event. So the join this repo
has prescribed since `TOURNAMENT_WINNER.md` — competition plus code — pairs two
different people.

`build_tourn_map.py` is not wrong; it is incomplete. Its rule solves "same
code, different tournament", which is real. It does not solve "same code,
different person, same competition", which is also real and is worse: this
strategy fires when a match COLLAPSES, so the pairing above is a rule that
sells Jack Draper's tournament-winner leg because Liam Draxl is losing a
qualifying match. It was caught in paper. Live it would have been a real short
of a top seed on an unrelated player's result.

Not a one-off. The same check over the 25 legs the allocation study traded:

| code | match book | winner leg | |
|---|---|---|---|
| MED | Hamad Medjedovic | Daniil Medvedev | different |
| VAN | Luca Van Assche | Botic Van de Zandschulp | different |
| STE | Sloane Stephens | Peyton Stearns | different |
| BER | Matteo Berrettini | Zizou Bergs | different |
| CER | Juan Manuel Cerundolo | Francisco Cerundolo | different (brothers) |
| MCC | Caty McNally | Catherine McNally | **same person** |

## The fix, and why it is not just "compare names"

Both books carry the player's full name, which is an exact identity, so the
code is now only a lookup key and the name decides. But MCC shows strict
equality is not enough either — the two books spell the same player
differently, and declining that pair loses a real trade.

Matching is therefore: exact normalised name, else surname + first initial,
and the fallback is used ONLY when it identifies exactly one candidate. That
separates all six rows above correctly. Two players sharing a surname and an
initial in one draw is rare, and a rare decline is the cheap failure — the
expensive one is trading the wrong player.

A code whose names disagree is logged as `CODE COLLISION ... not paired`,
never dropped silently.

## What it cost the measurement: almost nothing

Re-extracted with name validation and re-run. **1 of 175 qualifying rows and 2
contracts were contaminated**; VAN, STE, BER and CER never produced a
qualifying row at all. Every headline number is unchanged:

| | before | after |
|---|---|---|
| immediate | $27.88 | **$27.88** |
| 0.5s tick | $18.45 | **$18.45** |
| ranking (fifo/roc/edge) | identical | **identical** |
| floor 0.03 | $27.88 | **$27.88** |
| floor 0.00 | $28.82 | $29.91 |

Only floors below 0.03 moved, because the contaminated rows sat under the
floor the bot actually runs. The conclusions stand, now on clean data.

Medvedev's leg survives the fix and still trades — it is simply no longer
also driven by Medjedovic's match. That is the shape of a correct fix here:
it removes wrong inputs, not legs.

## The general lesson, which is the third of its kind in this repo

`TENNIS_DEADLEG.md` says list prices discover opportunities and live
orderbooks decide trades. `build_tourn_map.py` says a player code is not a
tournament. This adds: **a player code is not a player.** Every join in this
project has now failed at least once by being one identifier short, and each
time the failure was silent and directional. When an identity is used to pair
two books, verify it on something the exchange states outright — a name, a
result — not on a key that is only locally unique.
