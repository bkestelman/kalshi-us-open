# September 9 live pilot review

Reviewed September 9 UTC entries through approximately 21:40 UTC. Signed GETs
for fills and settlements are saved in `data/research/live_review_20260909.json`.
Pilot attribution uses exact exchange order IDs, not whole-account settlement
totals. All four filled positions have winning settlement outcomes.

| Pilot position | Filled shares | Cost excluding fees | Actual fees | Net settled profit |
| --- | ---: | ---: | ---: | ---: |
| Shelton semifinal YES | 21 | $17.6572 | $0.1963 | $3.1465 |
| Rybakina semifinal YES | 122 | $120.7800 | $0.0846 | $1.1354 |
| Zheng semifinal NO | 17 | $16.8300 | $0.0118 | $0.1582 |
| Gauff semifinal YES | 6.31 | $6.1207 | $0.0129 | $0.1764 |

Total $4.6165 net profit on $161.6804 principal plus $0.3056 fees.
Seven attempted orders today: four filled, two zero-fill (Pegula semifinal YES,
Alcaraz semifinal NO), and one insufficient-balance rejection (Alcaraz tournament
NO), subsequently resolved absent. The cash-sizing correction was deployed earlier
today. Health at review: no unresolved orders/errors; $299.76 cumulative allocation
against $1000 total/$200 per match. Allocation is retained reservation, not spending.

## Gauff depth

Order created at 20:36:57.876572 UTC: 36 requested at 97c; exchange execution at
20:36:57.935371: 6.31 filled at 97c. The independent primary capture was replayed
with connection/sequence resets invalidating books until fresh snapshots.
Depth evidence: `data/research/gauff_depth_20260909.jsonl.gz`.

At 20:36:57.866371, offers were 36.31 at 97c and 3.07 at 99c, none at 98c.
The 97c level fell to 21.31 at .879185, 15 at .895129, 6.31 at .918329,
and disappeared at .947313. The 99c level remained 3.07 throughout this window.
The ledger's observed 36.31 agrees with the independent capture before submission.
A 99c IOC sized across depth could potentially have captured the additional 3.07;
that is $0.0307 gross profit, roughly $0.0286 after formula fees, conditional on
unchanged executable liquidity at arrival. This is a counterfactual, not a fill.
The principal shortfall was competing execution/cancellation at 97c during latency.

The account's Gauff settlement contains 30.38 shares, including trades outside the
pilot order. Its full settlement profit must not be attributed to this trader.

## Execution finding

`pilot_strategy.executable_quote` returns the first eligible whole-contract level.
`Ledger.prepare` sizes only that depth. `Pilot` blocks a market after any fill,
including a partial fill. Together these prevent both an initial sweep to worse
eligible prices and subsequent completion after partial execution.

Recommended behavior: aggregate eligible depth in price priority, select quantity
and deepest necessary limit together under existing cash and cumulative caps, and
send one price-bounded IOC. Reserve conservatively at its worst execution price.
Shadow verification must validate and consume multiple levels too. Any later
retry after a partial fill needs separate reconciled-exposure and fresh-depth
handling; widening the first IOC does not require removing that safeguard.
This review changed no strategy code or running service.
