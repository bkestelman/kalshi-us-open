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
At 21:23, Pegula SEMI was listed and correctly matched by a fresh discovery
instance but was absent from the running bot's three Pegula legs. Added a
refresh path that adds newly available legs while preserving existing R and
match identity. Existing matches are not re-announced as newly discovered.
