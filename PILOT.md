# Authorized live pilot — September 7, 2026

User authorized starting real trades after execution blockers are fixed, using
~/trade-key and ~/trade-key-id. Credentials stay outside git and logs.

`pilot.py live` and `pilot.py paper` share `pilot_strategy.candidate` and the
same durable allocation ledger. They reuse primary discovery and score cache
but have independent sequenced websocket books and execution. Existing broad
and confirmed paper services continue; no data collection is gated by live caps.

Strategy: US Open tournament winner and advancement markets only. Match must
have started. Winner YES: match YES bid >=99c, no YES ask; buy qualification
secured by this round. Loser NO: match YES has no bid and ask <=1c; sell YES
only for a future round/tournament win. Aggregate eligible price levels, including
fractional depth, in execution priority. Choose the largest whole-contract order
affordable under cash/outstanding-allocation caps, with the tightest limit reaching that
quantity; submit one IOC and reserve the entire quantity at that worst price.
An unaffordable deeper limit does not reduce quantity available at a better price.
Shadow execution intersects one REST book with fresh websocket depth per price,
then sweeps through the limit and records fractional fills and execution levels.
No model-edge entries. Cached valid
score identity and round are required; final-score confirmation is not. Cached
confirmed contradictory outcomes veto. Annual markets remain capture-only.

Live and shadow limits are local startup configuration in their gitignored
`data/pilot_{live,paper}/pilot_config.json`: `total_cap`, `per_match_cap`, and
`recycle_on_settlement`. Caps include rounded fee reservations and aggregate both
players/all related legs per MATCH EVENT. With settlement recycling enabled,
verified settlement releases the entire original reservation, including unused
partial-fill headroom, from both caps. Until then any fill retains its full
requested allocation. Profits do not increase caps; fresh exchange-shard cash
still bounds each live batch. All order identities, filled-market exclusions,
and absent-order tombstones survive settlement and budget renewal.
Shadow uses finalized market outcomes and estimated execution costs/fees. Live
uses fully paginated fills (including historical when needed), exact order IDs,
exact filled quantities, actual fees and signed settlement outcomes. Failed or
incomplete accounting retains allocation and appears in health warnings.
Live and shadow fills are counterfactual alternatives, not additive liquidity.

Before any network submission, persist and fsync an intent, client-order ID,
and worst-case allocation, including fsync of the parent directory. One match batch is in flight at a time. All available related markets for both
players share the remaining match/total budget by dollars, redistributing unused
shares from shallow books. Each order is sized from displayed eligible depth and
reserved assuming a FULL fill at its worst limit, including rounded fees. All
intents are persisted together before concurrently submitting up to eight IOCs
on independent worker connections. No new batch starts until every response has
finished; any unresolved response blocks subsequent batches. Batches start at
least one second apart. A quick response never releases the batch gate.
The staged recovery runner retains its separate sequential entry checks. POST is attempted once, IOC only, taker_at_cross avoids
canceling other resting orders. Missing/malformed/non-success responses remain
UNRESOLVED with full allocation held; ALL further entries pause. Reconcile by
client-order ID through fully paginated exchange orders. Only a terminal order
with a valid fill count resolves the intent. An absent order is initially ambiguous, not a rejection. After at least10 minutes,
three clean checks at least30 seconds apart (at least60 seconds total) can mark
it not_found and release the allocation. All reads must succeed and paginate
fully: current/historical orders as required by cutoff, recent fills, market
positions and settlements. Exchange account watermark must be within60 seconds
and beyond the ten-minute deadline. Any pending order, same-market fill or
settlement, or nonzero exposure prevents release. Failed/stale reads reset clean
evidence. This is a bounded operational inference, not mathematical proof.
No automatic POST retry; a not_found market is not traded again by the pilot.
Keep durable tombstones and check for late orders/fills/positions. Late evidence
restores allocation and halts new entries for investigation. No cap increase.
A restart loads the ledger and checks exchange pilot orders for missing local
intents. Corruption or unknown historical pilot exposure stops startup.

Accepted V2 IOC responses include actual fill counts; raw exchange responses
are saved (no credentials). Actual execution price/fee fields are retained and
must be reconciled for P&L, never replaced by paper estimates. Health reports settled principal, fees and net profit separately from outstanding
allocation and lifetime reservations. Fee schedules verified
quadratic/multiplier 1 for all four series at startup. Partial fills retain
full allocation. No stale 2c elimination exception, no comeback-cap exemption.

Services: tennis-pilot-live (no automatic restart), tennis-pilot-paper.
State: data/pilot_live and data/pilot_paper. STOP file in either directory disables
new entries; unresolved reconciliation continues. `systemctl stop
tennis-pilot-live` stops the process. Never delete/reset the ledger to restore
budget; reconcile verified settlements instead. Minute health monitor queues errors/unresolved orders into original chat.
Model usage exhaustion can delay human/agent investigation; entry safety and
caps are enforced in Python and don't depend on model availability.

Validation includes persisted timeout/restart, missing order, partial-fill
reconciliation, malformed response, both-side/match/total caps, save failure,
single POST attempt, strict 1c versus 2c trigger, one-sided winner, score veto,
and initial-feed/not-started guards. Existing paper suites also pass. Signed
account/balance/orders and fee preflight uses only GET, not a test trade.

September 9 cash correction: aggregate cash spans exchange indexes. Before each
live batch, read market `exchange_index` metadata in parallel with a fresh
`balance_breakdown`, then recheck all entry signals;
size against that exchange's dollar balance as well as configured pilot caps.
Orders sharing an exchange share its cash budget; each reservation is deducted
before allocating another order. Historical partial-fill rates never justify
submitting more full-fill exposure than the caps allow.
Missing/stale shard cash prevents a submission and appears in health alerts.
No account transfers or allocation-target changes are made automatically.
