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

Launching two full discovery scans concurrently caused one HTTP 429 at
02:18:03; the primary recovered at 02:19:22. The comparison service now follows
the primary's exported match/market mappings instead of duplicating REST scans.
It retains its own websocket books, depth ledger and accounts. Stale discovery
snapshots are rejected rather than interpreted as an empty schedule.

The primary now records its own compressed websocket feed, including qualifier
ask sizes, in `winner_taker_ws_YYYYMMDD.jsonl.gz`. This closes the evidence gap
in the Pegula retrospective and enables replay of both trade directions under
different confirmation delays. Action records carry a run ID and comparison
mode, and startup records capture the effective configuration.

Deployed code `f1d81a4`: primary restarted 02:22:32 UTC and recovered its
three short fills / $5.53 and three winner fills / $5.62 with no open positions.
Primary full-book capture began 02:22:50. Confirmation-only follower started
02:23:12, subscribed to the same five Navarro-related/match tickers immediately,
and logged `require_score_confirmation=true`. Both modes remain paper-only.
Original offline suite and 13 focused paper/score/discovery tests pass.
The first full 24-hour revised-run checkpoint is September 7 around 21:29 UTC;
the confirmation-only comparison necessarily has a later starting point.

## 2026-09-07 03:00 UTC — recurring reviews and annual-market capture

User requested checks every couple of hours and identified annual Grand Slam markets.
Added a systemd two-hour UTC timer (`paper-review.timer`, first 04:00 UTC) invoking
local authenticated Codex noninteractive mode. Each bounded (45-minute maximum)
review reads current notes, checks both paper accounts/feed/captures, investigates
issues, tests fixes, and commits/pushes. Reports and execution logs live under
`data/reviews/`. `flock` prevents overlapping scheduled reviews; dirty worktrees
instruct the reviewer to report only. Authentication status says logged in using
ChatGPT; the first scheduled model execution is still pending. This is separate
from the existing minute health monitor. Timer failure is visible in systemd and
execution logs, not a guaranteed notification in the original chat.

New `related-markets.service` continuously captures raw sequenced orderbook
snapshots/deltas for KXATPGRANDSLAM, KXWTAGRANDSLAM and KXGRANDSLAM, currently
27 open contracts. Catalog/rules/player IDs refreshed every ten minutes and
archived alongside raw books in `related_ws_YYYYMMDD.jsonl.gz`. Gaps force fresh
subscriptions; errors and local timestamps retained. Independent collector keeps
new coverage off the entry path and does not restart existing experiments.
Annual contracts are capture-only pending verified year-to-date major winners
and remaining-major dependencies. Annual NO contracts can remain open to year
end; include collateral duration when evaluating them. Alcaraz's exact open
contract is KXGRANDSLAM-CALC26-2: at least two majors in 2026, not an exact-two
bucket. API close time September 29, 14:00 UTC. Annual Anisimova YES had 1-cent
bids in catalog: possible stale opportunity, not yet an executable/eligible fill.

Tonight's Navarro result supplied a first confirmation-only fill: at
02:54:15.235 UTC, 47 YES KXWTAADVANCE-26USOQUAR-NAV at 99c, total cost $46.57
including rounded fee, after ended/final-sets score at 02:54:15.076 decision.
REST verified 47 contracts after the 100ms delay; decision-to-fill 157.7ms.
The broad account independently bought 21 at 99c roughly 74 seconds earlier
with score still 6-4, 5-2, 30-0. These are alternative counterfactual accounts:
do NOT sum their overlapping liquidity as simultaneously executable profit.
Settlement was still pending at inspection. Confirmation-only fill is useful
positive evidence, not proof of guaranteed live execution or overall readiness.

Health monitor now checks raw capture freshness, related collector snapshot
readiness, and missing confirmation-only heartbeat. Validation: 13 support tests
and full winner-taker test script pass; Python compile, shell syntax, systemd
unit validation and calendar parsing pass (unrelated distro CPUAccounting
warnings). Continuous primary/confirmed paper processes left running.

## 2026-09-07 04:00–04:05 UTC — bounded scheduled review

Initial `git status --short` was empty at ac66379. Read RUN_NOTES.md and
READINESS.md first. Paper/read-only scope maintained; no trading key access,
orders, agents, service restarts, scheduler edits or additional jobs. Both
paper services, score service, annual collector and legacy REST/WS collectors
were active with NRestarts=0. Primary PID 23503 since 02:22:31; confirmed
23560 since 02:23:12; scores 6664 since Sep 6 21:13:37; annual 25696 since
02:58:57; legacy REST/WS 670/672 since Sep 6 20:25:26. S3 sync was activating;
backup completion was not verified. Disk 16G/48G used, 32G free (33%); memory
858 MiB used, 2973 MiB available, swap unused. Historical hardware notes are stale.

At 04:00:26 `paper_report.py` returned no warnings, primary/confirmed zero
matches/legs, feed_ready=true, zero locked balances and open positions. Score
cache age 1.5s; discovery age 18.8s at 04:00:58, empty groups/started but a
populated schedule. Eight Sep 6 score-cache matches were closed, individually
re-fetched 5.3–47.1s earlier (finished matches have one-minute polling). All
eight have primary WATCHING/IN PLAY evidence: KOSNOS, PAUALC, MICETC, PEGCIR,
MEDTIA, SABTOW, SHETSI, KALNAV. No newly missed match identified; this is not
an exhaustive independent tournament census. Legacy REST's two "live" matches
TIAMIC/SHEALC are Sep 8 listings, not proof of current play.

Actual capture check: streamed all 56,041 complete primary raw book records
(8 snapshots, 56,033 deltas), epochs 1788747770.4179873–1788753040.1977906;
zero per-connection/sid sequence gaps. Last record 03:50:40.198 agrees with
heartbeat last_message_at; 56,043 heartbeat messages include non-book messages.
Largest record gap 1070.488s occurred in the overnight tail. Annual capture:
7 catalogs/connections, 189 snapshots, 249 deltas at initial scan; zero sequence
gaps/errors, all 27 subscriptions ready; epochs 1788749937.6214266–1788753608.0452516,
maximum quiet interval 540.381s, consistent with ten-minute refresh. Streamed
5,409,814 legacy WS records (5,362,594 deltas, 47,041 trades, 179 snapshots),
epochs 1788739200.026–1788751910.349. Five-minute bins after 02:55 contained
381/421/119/194/1781/63/47/36 records. Legacy dropped final Navarro match at
03:31:37 and changed ticker set at 03:31:50, then continued five-minute
zero-match heartbeats through 04:00:31; old capture mtime is expected idle,
not a dead feed. Legacy full-day sequence reconstruction was not repeated.
REST orderbook/trade capture sizes 1,330,267/1,473,457 bytes with mtime ages
1.2/15.7s. No error/gap/traceback in primary, confirmed, score or legacy REST
logs since 02:55; sole legacy WS reconnect was that ticker-set change. Earlier
02:18:03 discovery 429 remains historical, with no repeat observed.

Actions and accounting: since revised baseline Sep 6 21:28:44, primary has
7 fills / 741 contracts, 3 REST-price-absent no-fills; all seven are inferred.
Short realized $5.530265 (3 fills, 2 settled tickers); qualifier $5.81 (4 fills,
4 settled tickers). Confirmed has one 47-contract fill, realized $0.43, no open
positions; absent never-used confirmed short file agrees with zero short takes.
No new entries after the preceding notes; new Navarro settlements are primary
03:51:34.147 (21 shares, +$0.19) and confirmed 03:51:14.452 (47, +$0.43).
Both are alternative simulations; their overlapping 47-contract REST liquidity
must not be added as concurrent executable volume. Confirmed fill at
02:54:15.235 was 470.7ms after first valid ended receipt 02:54:14.764278;
decision at 02:54:15.076702, simulated execution 157.707ms. Primary bought
73.740s before ended receipt with score 6-4, 5-2, 30-0. Official WTA result
[Navarro–Kalinskaya](https://www.wtatennis.com/tournaments/us-open/scores/LS73992588)
confirms 6-4, 6-2. Correct identity is Anna Kalinskaya, not Kalinina.

Public Navarro QUAR GET confirms finalized YES, matching competitor UUID
f17d541e-1f11-44ec-987b-3f2d8a90408b. Close 03:49:41, settlement_ts
03:55:45.483893: simulation recognizes published result about 4.2–4.5 minutes
before exchange settlement. Thus released paper collateral is not evidence
of actual cash availability. No losing filled signal observed; early Michelsen
and Pegula entries and historical Zheng false episodes still invalidate any
zero-risk inference claim. No new post-02:55 score completion or fill candidate.

Recomputed all five YES entry costs using ceil(0.07*n*p*(1-p)*100)/100:
all match durable totals; Navarro fees $0.02/$0.04. Short unrounded-fee model
remains different from conservative cent-rounded YES accounting. Code review
confirms delayed exact-price REST + current WS + remaining-depth minimum,
consumed depth and settled tickers persisted with positions before action logs;
valid JSON accounts agree with observed totals. Atomic save fsyncs the file
then replaces it, but not the parent directory: power-loss durability is not
fully proven. Account/log are not one transaction, and liquidity changes
between account saves are not continuously durable. No crash/restart injected.
These are handoff limitations, not observed account corruption.

Narrow fix: paper_report.py now includes confirmed daily action counts and
confirmed position counts, and reports/warns on primary discovery age >300s.
The monitor previously omitted these comparisons despite healthy-looking
heartbeats. No strategy/configuration change or process restart needed; next
minute monitor loads the updated script. Validation: 13 test_paper_support
unittests pass; full test_winner_taker.py passes; py_compile and git diff --check
pass. Direct report assertions pass for confirmed settlement/zero positions
and injected missing-discovery warning. Existing unittest ResourceWarning for
an unclosed temporary config file remains; no test failure.

Annual capture-only audit: all 27 catalog rules/IDs reviewed; 15 IDs join
exactly to current score-cache player IDs (including Alcaraz, Anisimova,
Navarro), 12 have no current score-cache counterpart and remain unverified
for any prospective match join. Catalog creation dates are Dec 19 2025,
Feb 6/10 and Apr 2 2026, so issuance matters. Independently verified 2026
singles champions before this US Open:
- Australian Open: [Alcaraz](https://ausopen.com/articles/news/major-milestone-alcaraz-completes-slam-set-ao-2026-title)
  and [Rybakina](https://ausopen.com/articles/news/resilient-rybakina-beats-sabalenka-ao-2026-title).
- Roland-Garros: [Zverev](https://www.rolandgarros.com/en-us/article/2026-edition-rg-live-sunday-june-7)
  and [Andreeva](https://www.rolandgarros.com/en-us/article/2026-edition-roland-garros-wrap-sat-june-6).
- Wimbledon: [Sinner](https://www.atptour.com/en/news/wimbledon-2026-results)
  and [Noskova](https://www.wtatennis.com/news/4533700/at-21-linda-noskova-caps-brilliant-fortnight-to-become-youngest-wimbledon-champion-in-15-years-defeats-karolina-muchova).

Only the ongoing US Open remains among the four named 2026 majors. This
six-winner census implies Anisimova has zero 2026 singles majors before USO;
her prior USO elimination is in existing notes, not newly independently
re-audited here. An open annual quote alone proves neither title history nor
executable depth. No annual promotion or annual paper fill was made.

Downloaded and read full [TENNISMAJOR terms](https://assets.kalshi.com/contract_terms/TENNISMAJOR.pdf):
singles only, after issuance, walkover/retirement championship counts; doubles,
mixed, junior and wheelchair wins excluded. Early expiration follows qualifying
YES; latest expiration one week after cutoff and settlement by following day,
subject to review. Anisimova API close Dec 31 04:59Z, expected expiration
Dec 31 15:00Z, latest Jan 7 2027 15:00Z: do not assume immediate annual NO
collateral release after elimination.

Alcaraz KXGRANDSLAM-CALC26-2 is active/unresolved, UUID
527915ea-e368-4c7f-a203-c83ebb6f6572 matching score cache; calendar-year
at-least-two, not exactly-two. Issued Feb 10 after his AO win; its annual
achievement wording differs from TENNISMAJOR's after-issuance rule. One
2026 major already won + USO remaining means YES requires winning USO under
normal completion. API expected expiration Sep 15 14:00Z, close/latest
Sep 29 14:00Z. Linked [TENNISMILESTONES terms](https://assets.kalshi.com/contract_terms/TENNISMILESTONES.pdf)
are headed NEWACHIEVEMENT and include official elimination, postponement,
cancellation and outcome-review contingencies; nominal dates are not a
cash-release guarantee. Reconcile generic achievement terms and specific
contract before promotion. All annual series report quadratic multiplier 1.
Web PDF open failed; unauthenticated urllib downloads succeeded. pdftotext
was absent; installed pypdf only under /tmp/tennis-review-pdf and extracted
both PDFs successfully. No project dependency changes. Initial guessed file
paths were absent; resolved actual sources/paths before checks.

Handoff: keep processes running. The revised account window is ~6h36m, not
24h. Sep 7 21:29 is only 24h elapsed since account baseline; uninterrupted
current primary/full-book capture cannot reach 24h until Sep 8 ~02:22/02:23,
and annual capture until ~02:59. Next timer should review new matches,
confirmed-versus-inferred depth/timing, cash-release lag and unresolved annual
identity/terms details. Reports/PDFs/catalog retained under
`data/reviews/20260907T040000Z-*`; no future wait or scheduler change.
