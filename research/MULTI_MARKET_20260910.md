# Multi-market entry review — September 10, 2026

Evidence: current `data/pilot_live/pilot_ledger.json`, shadow ledger, independent
broad-paper actions September 7–10, and the signed account reconciliation saved
at `data/reviews/20260910T120005Z-reconciliation.json`. Reproduce the compact
comparison with `python3 analyze_multi_market.py`; this only reads local files.
Saved output: `data/research/multi_market_20260910.json`.

## Findings

Live has 15 intents: 11 filled, 2 zero-fill, 2 absent after reconciliation.
Sabalenka/Noskova and Zheng/Rybakina each have two filled markets. There is no
one-market-per-match restriction. There WAS a global busy gate, a single-worker
order executor, a return after the first candidate, and an unresolved-order gate.
Each subsequent market waited for the preceding response and another evaluation.
Partial fills retain full requested allocation; caps previously changed, so the
current $200 cap must not be assumed for older entries.

Several entries have genuine scarcity: the loser one-cent snapshots for
Michelsen, Navarro and Andreeva show no bids in tournament/final/semifinal markets.
A quarterfinal winner only secures the semifinal qualifier; its final qualifier
and tournament YES are not eligible under the existing strategy just because
it wins this match. Broad paper also makes model-edge trades and repeated fills;
those are not equivalent pilot opportunities.

A clear multi-market snapshot exists for Alcaraz at September 9 07:33:31.946 UTC:
tournament bid 8c/110 shares and final-qualifier bid 3c/122 shares. Live prepared
Shelton semifinal YES at .534094, then Alcaraz semifinal NO at .671385 (zero fill),
then tournament NO at .735250 (insufficient shard cash, subsequently absent).
The unresolved tournament order blocked further entries, including the final.
This proves available quotes during a live block, not that a parallel final order
would certainly have filled. The earlier shard-cash fix already addresses that
rejection. Tien's September 8 one-cent snapshots also show concurrent tournament
and semifinal bids and broad-paper fills, but live had already used $4.91 of its
then-$5 match allocation. Increasing concurrency alone would not fix that cap.

## Fill estimates and allocation choice

| Measurement | Live | Identical-strategy shadow |
| --- | ---: | ---: |
| Filled orders | 11 | 8 |
| Fully filled among filled orders | 10 | 6 |
| Filled/requested contracts, terminal orders only | 84.33% | 94.37% |
| Median preparation-to-resolution latency | 69 ms | 154 ms |
| Retained cumulative allocation | $409.74/$1000 | $499.55/$500 |

Excluded from live ratio/latency: both absent-order inferences, which are not
exchange-terminal fill observations. Shadow stopped generating useful new
comparisons after nearly exhausting its cap September 8. Samples differ in time,
size, caps and execution model; ratios are descriptive, not a calibrated forecast.
Gauff filled only 6.31/36; most live fills were complete. A fill-rate discount
could overrun the match cap when all markets fill together. Delayed paper REST
verification can miss liquidity that live captures and observes books affected
by actual live trades; it cannot provide additive executable liquidity.

Use current eligible depth to bound potential fills, share available dollars
approximately equally across markets, redistribute unused allocation from small
books, and reserve 100% of every requested IOC at its worst price plus fees.
This avoids a discovery-order bias where one deep market consumes the whole cap.
When a worse price is needed, reprice the entire order reservation. No fill-rate
overbooking, reservation recycling, cap increase, or strategy expansion.

## Implementation and validation

Live and shadow gather all related markets for both players of one match event.
Live refreshes market metadata in parallel with one shard-cash response, rechecks
signals, and subtracts reservations from each shared shard budget. Up to eight
orders are durably prepared together and submitted on eight workers; batches
start at least one second apart and remain gated until every response completes.
Individual errors keep their full reservation and prevent later batches.

Batch logs record candidates, limits, counts, allocation policy and batch IDs,
allowing future comparisons of simultaneous candidates against actual fills.
Original ledgers remain compatible; staged recovery keeps its sequential path
and position/recovery checks. Recovery is not enabled by this change.

Tests cover simultaneous full fills, shared/opposing-player match limits, total
and shard limits, shallow-book redistribution, multilevel and fractional sizing,
restart with multiple unresolved intents, partial/zero fills, durability failure
before any POST, overlapping real worker calls with a three-party barrier, a
fast response while a sibling remains pending, post-read signal rechecks, and
100 randomized budget/shard cases. Exchange calls are mocked.

API references: [V2 orders](https://docs.kalshi.com/api-reference/orders/create-order-v2)
and [rate limits](https://docs.kalshi.com/getting_started/rate_limits).
Independent concurrent single-order requests retain existing per-order response
attribution; batch endpoints also charge per order.

Activated live and shadow September 10 at 12:59:18 UTC. All 107 relevant tests
passed; `/tmp/pilot-batch-tests.log`. Ledger backups:
`/tmp/pilot-batch-backup-1789045158`. All prior order fields and allocations were
preserved. Both services are account-ready, with zero unresolved/errors and no
active match legs. Fresh processes correctly wait for subscriptions before
reporting feed-ready. `paper_report.py` reports no warnings. No post-change live
trade has occurred yet; concurrency is verified with mocked exchange barriers.
