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
only for a future round/tournament win. No model-edge entries. Cached valid
score identity and round are required; final-score confirmation is not. Cached
confirmed contradictory outcomes veto. Annual markets remain capture-only.

Live limits are cumulative $25 total and $5 per MATCH EVENT, including estimated
rounded fees and both players/all legs. Filled orders retain their full requested
allocation even on partial fills or settlement. The pilot does not automatically
recycle budget. One filled order per market. Shadow has $500/$125 limits; broad
paper remains available for further depth/opportunity research. Live and shadow
fills are counterfactual alternatives, not additive executable liquidity.

Before any network submission, persist and fsync an intent, client-order ID,
and worst-case allocation, including fsync of the parent directory. Only one
submission is in flight. POST is attempted once, IOC only, taker_at_cross avoids
canceling other resting orders. Missing/malformed/non-success responses remain
UNRESOLVED with full allocation held; ALL further entries pause. Reconcile by
client-order ID through fully paginated exchange orders. Only a terminal order
with a valid fill count resolves the intent. Absence is NOT rejection and does
not release risk. No automatic POST retry. Definitive failures not appearing in
order history deliberately require investigation rather than automatic retries.
A restart loads the ledger and checks exchange pilot orders for missing local
intents. Corruption or unknown historical pilot exposure stops startup.

Accepted V2 IOC responses include actual fill counts; raw exchange responses
are saved (no credentials). Actual execution price/fee fields are retained and
must be reconciled for P&L, never replaced by paper estimates. This first pilot
reports allocation, not invented realized profit. Fee schedules verified
quadratic/multiplier 1 for all four series at startup. Partial fills retain
full allocation. No stale 2c elimination exception, no comeback-cap exemption.

Services: tennis-pilot-live (no automatic restart), tennis-pilot-paper.
State: data/pilot_live and data/pilot_paper. STOP file in either directory disables
new entries; unresolved reconciliation continues. `systemctl stop
tennis-pilot-live` stops the process. Never delete/reset the ledger to restore
budget. Minute health monitor queues errors/unresolved orders into original chat.
Model usage exhaustion can delay human/agent investigation; entry safety and
caps are enforced in Python and don't depend on model availability.

Validation includes persisted timeout/restart, missing order, partial-fill
reconciliation, malformed response, both-side/match/total caps, save failure,
single POST attempt, strict 1c versus 2c trigger, one-sided winner, score veto,
and initial-feed/not-started guards. Existing paper suites also pass. Signed
account/balance/orders and fee preflight uses only GET, not a test trade.
