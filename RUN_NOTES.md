# Paper run — September 6, 2026 onward

User objective: exploit stale bids on a player's tournament winner and future
round qualification markets as their match becomes decided. Remain paper-only.
Use the book for speed; investigate asynchronous score context for false-signal
protection. Current cash is not the strategy's scaling limit.

## Starting state

- Existing systemd paper service was active from 20:25 UTC, receiving updates,
  with a $500 hard cap. Two separate tennis collectors were also running.
- This server was a deployment, with no Git history. Imported its source into
  the user-provided GitHub repository, main branch, commit `b871853`.
- Legacy September 6 action log: Eala FIN, Mensik WIN, Kostyuk WIN each sold
  38 at 2 cents and recorded roughly $0.71 profit after a NO result. These
  used instantaneous assumed full fills and are not execution validation.
- Baseline tests passed with network enabled. Fixed the FakeScan fixture to
  seed empty ADVANCE markets, eliminating accidental real API calls. They now
  pass offline as advertised.

## Findings

The existing elimination trigger means started match + no YES bid + YES ask
at most 2 cents. It is an inference, not confirmation. Replayed September 5
collector data for Zheng against Keys: three false episodes lasting 124.561,
24.663, and 31.532 seconds. **Each reached an ask of 1 cent**, so tightening
the threshold alone does not solve the known false positive. Zheng later won.
The raw episode output is in `data/research/false_signal_episodes.json`.
The replay used the legacy collector's reconstructed books; further sequence
validation is needed before treating every reconstructed state as ground truth.

Kalshi public market rules were checked for Kostyuk's match and FIN leg:
match winner is conditional on a ball being played; qualification settles YES
even if the player qualifies but does not subsequently compete. A loss must
therefore only price *future* rounds, never an already achieved qualification.
Public v1 event metadata supplies round and a started label, but the inspected
response had no live score. Milestones contain competitor IDs and source IDs;
their `status` can still say `not_started`, so it is not yet a trusted outcome.

The user reports match markets usually remain available long enough after the
last point. Keeping related books after closure is secondary to signal quality.

## First changes

- Paper orders wait 100 ms by default, then fill at most the quantity still
  displayed at their limit. No price improvement is assumed.
- A separate remaining-depth ledger prevents taking the same simulated resting
  orders repeatedly. Positive YES deltas replenish it; cancellations reduce it;
  snapshots cannot replenish an already observed price level. This deliberately
  underestimates fills when cancellations concern orders already simulated filled.
- Paper positions, cap usage, realized P&L and consumed depth persist in an
  atomically replaced, fsynced account file. Corrupt state stops the run.
  Legacy fills are excluded from this new account; the previous run held none
  at the time of inspection.
- Websocket reconnect clears old books. Entries and R sampling wait for all
  subscriptions' snapshots. Missing/out-of-order sequence numbers force resync.
- Settled legs cannot be re-entered.
- Signal transitions log 1-cent/2-cent states, recovery, related bids and
  decisions. Takes record signal type, fill model and elapsed time.
- Original suite plus five focused paper tests pass on Python 3.14.

## Still required before judging readiness

Collect at least one day with the improved simulation. Measure false signals,
missed opportunities, partial/zero fills, score-feed delay, and settlement P&L.
Audit market rules/round joins, feed reconstruction, fee rounding and live order
accounting. The current inferred-elimination path still uses fair=0 and bypasses
comeback caps; paper results on that path are **not** evidence of zero risk.
No live transition is authorized by this run.

## Score feed and manual fills (21:15 UTC)

Found documented `/live_data/milestone/{id}` and `/live_data/batch` endpoints.
The batch API requires repeated `milestone_ids` parameters; a comma-separated
string silently returns null. Started independent `tennis-scores.service` at
two-second polling for unfinished matches and one-minute rechecks for finished
matches. Full score changes, minus bulky statistics, are journalled with request
and receive times. Source latency is unknown; receive time does not establish
score freshness. Match market `custom_strike.tennis_competitor` IDs match the
score IDs, including Zheng, Kostyuk, Medvedev and Tiafoe.

The bot reads this local cache. A mapped, recently fetched explicit ended/closed
winner can confirm loss without R, or veto shorting a known winner. Qualification
rounds already achieved are excluded using score milestone round metadata.
Unfinished scores are recorded as context, not used as a timing veto yet.

Read-only portfolio inspection confirmed Anisimova FIN NO at 99 cents,
156.99 contracts, September 5 at 16:48:28.520 UTC. **The Kostyuk-match fills
were on Noskova QUAR YES**, 206 contracts at 99 cents, September 6 at
19:15:54.963–19:15:55.092 UTC. This exposes a missing strategy direction:
buy the winner's newly secured qualification, as well as short the loser's
future qualifications. The original bot cannot express that direction.

## Winner-side paper account

Added a paper-only YES taker sharing the same websocket and local score cache.
It targets QUAR after Round Of 16, SEMI after Quarterfinals, FIN after
Semifinals, and WIN after the Final. It accepts an explicit mapped score win,
or a started match with bid >=99 cents and no ask. Inference is still risky.
Its separate research account has $500 total and $125 per-player inferred
exposure. These are independent of the original short-side $500 account, not
a claim that combined exposure is $500. Live mode never instantiates it.
Fees are rounded up to cents per winner-side simulated order. Positions and
remaining ask quantities are durable. Nine focused tests now cover latency,
depth consumption, restart, score identity, round eligibility and YES fills.

Restarted onto `6c18c80` at 21:19:46 UTC. At 21:21:46: feed synchronized,
3 watched matches / 17 related legs, 3,744 messages since restart, zero new
fills on either research account. `paper-monitor.timer` now records health and
durable totals each minute. The first monitor sample preceded the bot's first
two-minute heartbeat and correctly reported missing heartbeat; the following
sample was healthy. Score collector snapshot age was below two seconds.

## Sequence-validated historical replay and discovery refresh

Replayed 31,286,068 September 5 book messages with per-subscription sequence
checks. Reset reconstructed books across 34 reconnects/gaps and require fresh
snapshots. Zheng's 1-cent false signal survives this check, including a fresh
snapshot mid-episode. It was not merely a missed-delta artifact.

Anisimova's valid no-bid/1-cent state began at epoch 1788626888.918
(16:48:08.918 UTC), **19.602 seconds before the user's manual FIN NO fill**.
That is an observed executable opportunity after the market signal, not a
claim that every size was executable at the first timestamp. Raw audit output:
`data/research/validated_signal_episodes.json`; reproduce with `replay_signals.py`.

Discovery skipped matching related legs for matches already being watched.
Added a
refresh path that adds newly available legs while preserving existing R and
match identity. Existing matches are not re-announced as newly discovered.
Correction after reviewing the full logs: the earlier claim that Pegula SEMI
was absent was a reading error; it was already subscribed. The refresh fixes
a real missing code path, but Pegula is not evidence of a realized omission.

## Execution verification

Added public REST checks after the configured paper delay, in parallel for
market status and orderbook. A paper fill requires an active, unresolved
market and quantity at the exact limit in both the current websocket book and
the independently fetched REST book, limited by remaining simulated depth.
Errors or absent depth produce no fill and a one-second retry cooldown.
This is deliberately conservative: the extra REST request is **paper
validation latency**, not a proposed synchronous check on the live order path.
Action records carry its request/receive timestamps and quantity.

Series endpoints currently report quadratic fees with multiplier 1 for ATP,
WTA and both ADVANCE series. Official fee rounding documentation distinguishes
direct-member $0.0001 balance precision from non-direct-member cents. The
manual 200-contract Noskova fill paid $0.138600; its subsequent 5- and
1-contract fills paid zero (do not assume all manual fills are taker fills).
Winner-side paper currently rounds fees conservatively to whole cents;
short-side uses the original unrounded model. Harmonizing this remains before
live readiness, though the monetary discrepancy is below a cent per order.

## Overnight review — September 7, 02:17 UTC

Service PID 9306 stayed running from 21:28:44 on September 6. Both collectors
and the minute monitor are healthy; no ongoing feed or score errors. The new
paper window is **under five hours**, not a completed full-day validation.

Six REST-verified fills, 720 contracts, five markets, three matches:

| Trade | Contracts | YES price | Settled paper profit |
|---|---:|---:|---:|
| Buy Michelsen QUAR YES | 126 | 99c | $1.17 |
| Sell Medvedev QUAR YES | 33 + 335 | 7c, 1c | $5.28 |
| Sell Medvedev SEMI YES | 3 | 9c | $0.25 |
| Buy Tiafoe QUAR YES | 95 | 99c | $0.88 |
| Buy Pegula QUAR YES | 128 | 97c | $3.57 |

Short account: $5.530265; winner account: $5.62. All positions settled.
These remain independent research accounts, not a shared-$500 return.
Median decision-to-paper-fill time was 152.8 ms including the 100 ms delay
and REST validation. Three other attempts returned no fill because the exact
price was absent from REST; the Medvedev 7c attempt partially filled 33 of
100 displayed contracts. This shows the simulation is now exercising misses
and partial fills rather than blindly counting every displayed quote.

**All six entry signals were book-inferred.** Michelsen was taken 658 seconds
before the first ended score, Pegula 1,385 seconds before. Their recorded
scores were 2 sets to 0 / 4–3 and 1 set to 0 / 4–1, respectively: these are
speculative entries, not proven post-match opportunities. Medvedev/Tiafoe fills
were 10–11 seconds before the first ended score, much closer to the desired
last-point window. Winning outcomes do not prove the inference safe.

Found and fixed confirmation latency: the feed first reports `status=ended`,
an explicit winner and complete set score, then reports `status=closed` about
118–130 seconds later. We wrongly required closed. The early ended result now
qualifies when winner ID and completed best-of-three/five set totals agree.
Closed results retain support for retirement outcomes.

The delayed confirmation mattered: Pegula QUAR observations still showed an
ask of 99c 7 and 67 seconds after the first ended result, and no ask by the
time the old closed-only gate fired. Ask quantity was not in the old observation
schema, so these observations establish a missed candidate, not a fill size.

Starting a separate `winner-taker-confirmed-paper.service` in `data/confirmed`
with `require_score_confirmation=true` and the same local score cache. It will
compare strictly confirmed entries against the existing broad book-driven
research account without rewriting historical results or borrowing liquidity.
Both strategies remain paper-only. Before any live deployment, inferred trades
must have explicit probability/edge justification and risk caps; the legacy
short-side inferred path's fair=0 and cap exemption are not validated.

Reproduce the review with:
`python3 analyze_paper_run.py --since 2026-09-06T21:28:44Z`.
