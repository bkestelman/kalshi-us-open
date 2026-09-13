# Recovery release review — September 9, 2026

**Status: execution candidate implemented and tested, recovery NOT enabled.**
The 5c/60s candidate passes the covered actual-fill regression, but has NOT
established zero interference across the broader historical population. Under
an absolute non-interference requirement, the policy is not approved for live
use. Do not interpret the disabled candidate configuration as authorization.
A user clarification about occasional bounded-cost exits remains pending.

## Balance incident — fixed and live

The authenticated balance read returned **$879.7488 aggregate**, comprising
**$99.8211 on exchange index0** and **$779.9277 on index3**. Alcaraz's tournament
winner market is on0; Shelton's qualification market is on3. The rejected
124-contract Alcaraz NO order needed **$113.56** including estimated fees.
The old entry allocator checked cumulative pilot limits but not the target
exchange's cash. No balance snapshot was saved at rejection time; today's
balance split corroborates the diagnosis rather than proving that exact
historical cash figure. No resting orders or configured target allocations
were returned during the initial read-only investigation.

`pilot.py` now reads market identity/status and signed cash before reservation,
uses only the matching `balance_breakdown` dollar amount, then rechecks the
entry signal and feed/discovery freshness. Missing, stale, duplicate or invalid
cash information prevents submission. `Ledger.prepare(cash_limit=...)` applies
the minimum of shard cash, remaining cumulative total and remaining match cap.
The reproduction sizes124 contracts down to109, reserving99.82. Cash-read errors
are included in the minute health monitor. No transfers or target-allocation
changes are made, and the old rejected order is not retried.

Live restarted **12:28:53 UTC**, PID150357. All ten existing order statuses and
reservations survived; allocation127.04 was retained. At21:30 UTC the same PID
was healthy, had13 orders/9 fills and299.76 cumulative allocation, no unresolved
orders or cash errors, and active market data. The authorized limits remain
1000 total/200 per match. Recovery paper and other services were not restarted.

Official API references checked September9:
[balance and exchange indexes](https://docs.kalshi.com/api-reference/portfolio/get-balance),
[market exchange index](https://docs.kalshi.com/api-reference/market/get-market),
[V2 IOC/reduce-only orders](https://docs.kalshi.com/api-reference/orders/create-order-v2).
Read-only evidence is in `data/research/recovery-readonly-preflight.json`.

## Historical evidence and overfitting

The configured S3 bucket is `s3://kalshi-tennis-411691564448/live/`, not the
example bucket name in the README. It contains42 WS objects dating toJuly30
(including one overlapping early-day object). Eight recent daily captures are
also local. The study extracted **485,534,143 book messages across26 dates**:
Aug6–23 andSep2–9. Source/tape hashes and exact endpoints are recorded in
`research/recovery_cohort_manifest.json`. The Sep9 broad cohort capture ends
08:04 UTC; a separate primary replay through21:31 covers later actual fills.

The extraction checks every book-message sequence, including unrelated
instruments. Gaps, new connections and >30s capture silence invalidate books;
new snapshots are required before applying deltas. Long silence invalidates at
last-message+30s, not at the next received message. Each day starts without
borrowed books. Open current-day gzip tails are explicitly marked and only
complete decoded records are used. Closed-day truncation fails the extraction.

All started-match strict one-sided99c/1c episodes are considered, on both sides
of each exact match book. Results come from cached exchange market results.
Repeated episodes and complementary tickers are deduplicated by match in the
summary. Historical windows run to recorded final-score receipt when available,
market close, or one hour, whichever occurs first. Censoring and gaps are
reported; absence of a trigger in an incomplete window is not proof of safety.

Development dates were Aug6–19. Previously examined Sep5/Sep7–8 are reported
separately. The untouched validation dates were Aug20–23 andSep2–4/Sep6/Sep9.
The grid was fixed at2/3/5/7/10c and5/10/30/60 seconds. The5c/60s candidate was
recorded before viewing the untouched validation results, using the known
Swiatek failure at shorter persistence as a regression constraint. It remains
a selected candidate, not a randomized or fully independent production trial.

| Cohort | Matches with successful hypothetical signals observed | Eventual-winning matches flagged by5c/60s | Flagged by10c/5s | Wrong-signal matches detected by either |
|---|---:|---:|---:|---:|
| Development |295|24|17|3|
| Untouched validation |152|8|7|2|
| Previously studied dates |24|0|0|1|

These are **quote-only hypothetical signal windows**, not actual related-market
fills or measured P&L. In particular, older final-score receipt timestamps are
unavailable: a live score veto could suppress some observed revivals. We cannot
count that hypothetical veto as verified. Nor can we assume an executable
related-market entry or exit existed on those older matches. The table is a
stress test of the claim that bid revival cleanly distinguishes incorrect
signals, not an estimate of the real strategy's loss rate.

Of the untouched validation cohort,147 matches have at least one fully covered
successful-signal window. The5c/60s rule flags two fully covered successful
windows and six additional cases in partly covered windows. A measured trigger
before a later gap remains valid positive evidence; partial coverage only limits
claims about missing triggers. All six wrong-signal matches have some gaps in
their full evaluation windows, but their detected persistence intervals are
sequence-valid. They must not be described as six fully observed hour-long cases.

Wrong-signal examples, delay from first strict signal to valid trigger:

| Match |5c/60s|10c/5s|
|---|---:|---:|
| Aug6 Norrie–de Minaur |919.9s|927.2s|
| Aug7 Van de Zandschulp–Hurkacz |188.8s|173.1s|
| Aug15 Jodar–Shapovalov |735.7s|722.6s|
| Aug23 Timofeeva–Rakhimova |1124.2s|1373.3s|
| Sep2 Sakkari–Starodubtseva |592.6s|586.0s|
| Sep5 Keys–Zheng |529.4s|902.5s|

Tickers, rather than these display-name expansions, are authoritative in
`data/research/cohort_results.json`. These are six matches, not independent
counts for every signal cluster or both market listings. No actual related fill
is asserted for these historical false signals.

## Actual successful-trade regression

The separate actual-fill replay includes broad book-inferred paper entries,
the live pilot and the shadow pilot, honors synthetic-run exclusions, and stops
at recorded favorable final-score receipt. It uses the original primarySep7–8
replay, standaloneSep6 capture and primarySep9 capture. Results:

- 48 entries located; **47 fully covered entries across14 matches**.
- All9 live fills, all8 shadow fills and30 of31 broad-paper entries covered.
- The earlier broad-paper Pegula entry remains excluded because its full
entry-to-final interval is outside the selected valid capture.
- All covered entries' match inferences were eventually correct.
- **5c/60s: zero covered successful matches interrupted.**
- 10c/5s: also zero.
- 5c/30s: interrupts Swiatek/Zheng;5c/5s additionally interrupts Michelsen/Etcheverry.

Multiple fills from one match and live/shadow alternatives are correlated;
47 entries are not47 independent tests. These actual fills contain no executed
false-signal entry, so this is an interference regression, not a measured net
recovery benefit. See `data/research/actual_recovery_results.json`.

For hypothetical100 Zheng tournament NO bought at99c,5c/60s fires about
**6m13s earlier** than10c/5s. At+100ms,+500ms and+1s, the historical tape supports
selling100 NO at99c, for a14c total estimated round-trip loss. A same-match hedge
costs7c plus fees at the earlier trigger versus14c at the old trigger. These are
displayed-depth counterfactuals; there was no actual strategy fill. Full numbers
are in `data/research/zheng_5c60_execution.json`.

## Staged execution candidate

`staged_recovery/runner.py` subclasses the entry pilot but is not imported by
its deployed entrypoint. No systemd service points to it. The shipped config is
`enabled:false`,5c/60s, and permits exact-match hedges if later enabled.
CLI startup refuses disabled configuration and requires the existing pilot_live
data directory and the existing exclusive pilot lock. It cannot run alongside
the original live pilot on that ledger.

The staged runner provides:

- One fsynced journal and cumulative budget for entries, exits and hedges.
  Existing positions and allocations carry forward; neither exit proceeds nor
  settlement recycle allocation. Recovery fees/hedge costs remain charged.
- Same-book continuous persistence, fresh feed and reconnect checks, favorable
  score veto, adverse-final fallback, and a final signal recheck after reads.
- Durable blocking of subsequent entries in the affected match on revival,
  with recovery taking priority over new entries after asynchronous cash reads.
- Price-bounded direct exits, no more than2c gross below the original unit-cost
  limit, using **reduce_only**. Hedges buy only the adverse outcome of the exact
  original match, at no more than20c. Opponent ticker guesses are excluded.
- Route comparison by greatest protected quantity, then greatest minimum
  ordinary-settlement proceeds after fees. REST and current WS depth are both
  checked, with per-route shard cash, fresh account watermark and exact expected
  net position. New entries do not mix with pre-existing same-market exposure.
- Fractional counts down to0.01, protection bounded by remaining owned quantity,
  at most three attempts, and at least five seconds between attempts.
- One POST attempt per durable client-order ID. Missing/malformed/non-success
  responses retain their reservations and block all further entry/recovery
  orders until reconciliation. Actual raw responses are retained.
- Restart reconciliation by client-order ID. An ambiguous exit cannot use
  entry-only absence logic to discard its reservation while source exposure
  exists. An absent hedge that satisfies the existing bounded clean-read policy
  is tombstoned and is not retried. Late evidence halts further entries.
- Dedicated heartbeat/error/unprotected-exposure monitoring in the minute report.

A fully allocated match may have no remaining hedge/exit-fee budget. The candidate
reports that explicitly; it does not manufacture protection or reduce all entry
sizes to reserve a hedge allowance. The20c hedge cost is an ordinary budgeted
expense, not a promise of no sacrifice on successful trades. Match and related
rules were read for the sampled markets: match contracts require a ball played;
qualification pays on securing the round even if the player later withdraws.
Ordinary complementary outcomes support the hedge reasoning; exceptional/void
settlement is not assumed to guarantee a dollar payout.

## Validation and deployment boundary

At the final review, **95 unittest tests passed**, plus **134 standalone winner
engine assertions**. These include live-cash regression,100 seeded randomized
recovery budget/fraction scenarios, NO/YES exits, reduce-only request mapping,
partial fills, corrupt ledgers, cap exhaustion, interrupted POST/restart,
matching-CID reconciliation, source-exposure checks, score veto, feed replacement,
STOP handling, disabled CLI startup, extraction gaps and open-tail handling.
The raw test logs are preserved under `/tmp/recovery-final-*.log` and the
additional staged/cohort logs referenced in RUN_NOTES.

A separate GET-only preflight against a temporary copy of the real ledger
verified startup/account reads, conservation of allocations and settlement
adoption. It submitted zero orders and changed no production ledger.
Evidence: `data/research/staged-readonly-check.json`.

The remaining gate is **policy acceptance, not waiting for another rare false
positive**. Historical data already demonstrates the tradeoff.5c/60s is a better
supported lower-threshold candidate than5c/5s for the covered actual entries,
but it is not established as harmless across all historical signals. Under a
zero-observed-interference requirement, keep it disabled. If occasional bounded
exits/hedges are acceptable, that is a different deployment criterion and should
be made explicit before activating this candidate. No recovery deployment,
cash transfer, cap increase or test trade was performed.

A future approved activation would change only the live service entrypoint to
`python3 -m staged_recovery.runner --config <approved config>`, retaining its
existing data directory, lock, credentials and caps, after stopping the old
process and backing up the ledger. Rollback after any recovery intent must keep
the recovery-aware reconciler: the old entry-only ledger validator deliberately
rejects recovery exit reservations rather than silently ignoring their costs.

Reproduction commands:

```sh
python3 analyze_recovery_cohort.py data/research/cohort data/research/cohort_results.json
python3 analyze_actual_recovery.py
python3 -m unittest discover
python3 test_winner_taker.py
```

Use the preserved compact tapes and manifest instead of unnecessarily rereading
half a billion raw messages. `research_recovery_cohort.py` documents single-source
extraction when rebuilding a tape is needed.
