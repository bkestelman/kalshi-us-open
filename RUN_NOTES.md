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

## 2026-09-07 08:00–08:04 UTC — bounded scheduled review

Read RUN_NOTES.md and READINESS.md first; initial worktree clean at 29938b5.
Paper/read-only review only: no trading-key access, real orders, agents,
service restarts, strategy/configuration changes, scheduler edits or new jobs.
Detailed machine evidence: data/reviews/20260907T080000Z-audit.json.

All six paper/score/related/legacy capture services active, NRestarts=0.
Primary PID 23503 since 02:22:31, confirmed 23560 since 02:23:12,
scores 6664 since Sep 6 21:13:37, related 25696 since 02:58:57,
legacy REST/WS 670/672 since Sep 6 20:25:26. At 08:00:37 paper_report.py
returned no warnings: primary heartbeat age 4.5s, score cache 1.2s,
discovery 33.4s; both paper accounts feed_ready, zero watched matches/legs,
zero locked balances/positions. Disk 16G/48G used, 32G free (33%).
S3 sync finished successfully at 08:00:20, 0 new files uploaded, 124 objects,
19,459,128,930 bytes reported; object integrity/current-file backup not verified.

Continuity: streamed 56,041 complete primary records (8 snapshots, 56,033
deltas), epochs 1788747770.4179873–1788753040.1977906; zero per-connection/sid
sequence gaps, maximum record interval 1070.488s. No new raw messages since
03:50:40, matching heartbeat counters and zero-match idle. Annual stream has
30 catalogs/subscriptions, 810 snapshots and 1126 deltas through epoch
1788768008.7740374; zero sequence gaps, maximum interval 600.486s, all 27
subscriptions ready. Active gzip members ended with EOF; complete records
were audited, not treated as closed finalized archives. Legacy WS streamed
5,409,814 records (5,362,594 deltas, 47,041 trades, 179 snapshots), epochs
1788739200.026–1788751910.349, unchanged since previous review. Its five-minute
zero-match heartbeats continue through 08:00:31. Legacy sequence replay not
repeated. REST book/trade files 1,561,796/1,496,105 bytes with mtime ages
5.4/99.9s at audit; REST still labels future Sep 8 TIAMIC/SHEALC listings
as live. No dead-feed conclusion from this overnight quiet period.

No new action of any kind since 04:00 in BOTH live and confirmed. Baseline
analysis remains 7 speculative fills / 741 contracts, 3 REST-price-absent
misses; short realized $5.530265, qualifier $5.81. Confirmed has one 47-contract
Navarro fill and $0.43 realized; absent never-used short account agrees with
zero takes. Navarro speculative 21 @99c cost $20.81 versus confirmed 47 @99c
cost $46.57; fee increments $0.02/$0.04 agree with ceil(0.07*n*p*(1-p)*100)/100.
Confirmed decision had ended 2–0 sets and matching player identity; speculative
entry was ~74s earlier at 6–4, 5–2, 30–0. Settlements occurred at epochs
1788753094.147/1788753074.452 (~57 minutes after first ended score). Independent
counterfactual accounts must not have their overlapping liquidity summed.
Earlier Michelsen/Pegula entries remain speculative, not post-match proof.
No new false positive or missed match found; eight Sep 6 closed matches all
appear in primary logs. This is not an independent full tournament census.

All eight Sep 6 score rows freshly received 13.4–53.2s before audit. Some older
closed rows are 1–5 hours old because discovery selects a rolling 36-hour
start window; global cache timestamp does not imply every retained score is
fresh. Source code confirms finished matches poll each minute while selected.
Fresh discovery has zero started/groups and 12 future main-tour matches;
next scheduled main-tour start epoch 1788793200 (15:00 UTC). No future wait.
Primary/confirmed/score/legacy logs since 04:00 contain no error, traceback,
reconnect or HTTP 429. An initial loose search matched 429 in timestamps;
rechecking message bodies found zero errors. Annual records have no error rows.

Read paper_report.py, depth ledger, atomic_json and qualifier fee path.
Remaining-depth consumption and conservative resnapshot semantics remain;
account files parse with zero positions and totals matching history. Existing
limitations persist: short fees unrounded versus qualifier cents; no parent
directory fsync, account/log not transactional, unsaved intervening liquidity
not crash durable. No crash injection. Validation: 13 test_paper_support tests
pass and full test_winner_taker.py passes. Existing temporary config unclosed
file ResourceWarning remains. No production code fix justified this interval.

Annual audit remains capture-only. Current 27-market catalog: 15 player IDs
match score-cache IDs, 12 prospective joins unverified. Re-read archived
TENNISMAJOR and TENNISMILESTONES PDFs with existing /tmp pypdf; no installation.
TENNISMAJOR is singles after issuance, with qualifying YES early expiration;
NO collateral can remain until year end/latest Jan 7 2027 and outcome review.
Alcaraz KXGRANDSLAM-CALC26-2 remains active/unresolved, explicit at-least-two
in 2026, ID 527915ea-e368-4c7f-a203-c83ebb6f6572 matches scores, expected
expiration Sep 15 14:00Z, close Sep 29 14:00Z. Rechecked AO Alcaraz/Rybakina,
RG Zverev/Andreeva and Wimbledon Sinner/Noskova via the official sources
linked in the 04:00 review. ATP direct page returned 403; official Wimbledon
search result https://www.wimbledon.com/en_GB/gallery/jannik_sinner_champion
and ATP final report search result confirmed Sinner. Alcaraz already has AO;
only USO remains, implying he needs USO for two under normal completion.
Generic NEWACHIEVEMENT contingencies versus specific year wording still need
reconciliation before promotion. Active listing never establishes zero prior
titles; no annual fill/promotion made. Historical titles are current-year
pre-USO singles titles, not career totals or last year's titles.

Operational failures: 06:00 scheduled execution log ends with Codex usage-limit
errors, so that review was incomplete and did not produce a completed handoff.
Current paper_report reports review_result=success while this invocation is
running: it reads current systemd Result, not durable previous-review success.
This monitoring blind spot remains; scheduler explicitly left untouched.
Unrelated /home/ubuntu/kalshi control panel is in a crash loop (NRestarts=12768
at first check): RuntimeError tennis-tournwinner --obs-every has no kind in
FLAGS. Outside this paper repo; left unchanged for interactive owner.

Handoff: maintain running experiment. Current uninterrupted primary capture
is ~5h40m, not >=24h. Earliest full uninterrupted primary/confirmed checkpoint
remains Sep 8 ~02:22/02:23 UTC; annual ~02:59. Next scheduled reviewer should
check new matches, confirmation timing/depth, settlement lag and the operational
failures above. Only this report and run notes changed; commit/push outcome
is recorded by the review execution and final response.
# 2026-09-07 10:00–10:04 UTC bounded paper review

Initial worktree clean. Read RUN_NOTES.md and READINESS.md first. No agents,
trading keys, real orders, restarts, configuration changes or scheduler changes.
Evidence: 20260907T100000Z-audit.json. Only review documentation changed.

All six paper/score/annual/legacy services active, NRestarts=0. Primary PID
23503 since 02:22:31, confirmed 23560 since 02:23:12, scores 6664 since
Sep 6 21:13:37, annual 25696 since 02:58:57, legacy REST/WS 670/672 since
Sep 6 20:25:26. Disk 16G/48G used, 32G available (33%). S3 sync succeeded
10:00:20, 124 objects/19,459,128,930 bytes; object integrity not checked.

paper_report.py at 10:00:27 returned no warnings, score age 0.1s, discovery
6.5s, primary heartbeat 113.9s. Both accounts have zero matches/legs, positions
and locked balances. Both feed_ready flags are now false: primary disconnected
08:17:33.604, confirmed 08:56:05.916, each with no close frame. Inspected
winner_taker.py ws_loop: when tickers are empty it waits for discovery/30s
recheck; discovery sets dirty on changes, and a new subscription requires all
snapshots before entries. Idle disconnect is not proof of lost active-market
capture. Reconnection with future tickers has not yet been observed; next
review must verify it. No restart justified.

Actual raw audit: primary 56,041 complete book records, epochs
1788747770.4179873–1788753040.1977906, unchanged; zero connection/sid sequence
gaps, maximum interval 1070.488s. Annual 43 catalogs/subscriptions, 1,161
snapshots, 1,480 deltas through 1788775176.440021; zero sequence gaps,
maximum interval 600.486s. One annual capture_error at 1788769026.6295974
(08:17:06.630), connection 32; connection 33 subscribed at 1788769036.824193,
10.195s later. Current health shows all 27 snapshots ready. Disconnect data
loss cannot be ruled out, but reconstruction recovered. Legacy streamed all
5,409,814 records, last epoch 1788751910.349, maximum interval 113.464s;
sequence validation NOT performed for its different schema. All three active
gzip streams end in EOF after complete records; not finalized archives.
Legacy zero-match heartbeats continue through 10:00:32; REST still captures
future Sep 8 TIAMIC/SHEALC. REST files grew to 1,687,021/1,502,645 bytes at
audit. No new errors in score or legacy file logs since 08:00. Systemd journal
for these collectors returned no entries; file logs supplied evidence.

No new actions, fills or settlements since 08:00 in BOTH directories.
Baseline remains 7 speculative fills/741 contracts, three REST-price-absent
misses; short realized $5.530265, qualifier $5.81. Confirmed one 47-contract
Navarro fill, $0.43 realized. Recomputed analysis against score completion:
all broad entries inferred; Michelsen/Pegula were substantially before ended
scores. Navarro broad 21 @99c cost $20.81 vs confirmed 47 @99c cost $46.57,
fees $0.02/$0.04 agree with conservative cent ceiling; confirmed decision
followed ended score, broad entry preceded it ~74s. Independent accounts cannot
sum overlapping liquidity. All eight Sep 6 completed score rows are closed
and present in primary logs, refreshed 0.3–56.3s before final sample. Fresh
discovery has zero started/groups and 12 future main-tour matches, earliest
15:00 UTC. No new false positive or missed match identified; independent full
tournament census and new active-match recovery remain unverified.

Read paper_report.py, liquidity/atomic persistence and qualifier fee path;
durable account JSON parses and matches totals, zero positions. Conservative
resnapshot depth consumption persists. Known limitations unchanged: short fee
unrounded versus qualifier cents, no directory fsync, account/log not one
transaction, intervening unsaved depth not crash durable. No crash injection.
Tests: 13 support unittests and full winner_taker test script passed. Existing
unclosed temporary config ResourceWarning remains. No production fix justified.

Annual markets remain capture-only. Current catalog 27 contracts, 15 IDs join
score cache, 12 prospective joins unverified. Reopened official sources linked
in the 04:00 notes: AO Alcaraz/Rybakina, RG Zverev/Andreeva, Wimbledon
Sinner/Noskova. These are current-year pre-USO singles titles. Only USO remains;
active listing never implies no prior title. Read both archived contract PDFs:
TENNISMAJOR requires singles wins after issuance; annual NO may retain
collateral to year end/Jan 7 2027 plus settlement/review. Alcaraz
KXGRANDSLAM-CALC26-2 is active, at least two in 2026, ID
527915ea-e368-4c7f-a203-c83ebb6f6572 matches scores. Already AO champion;
under normal completion USO is needed for his second title. Specific annual
wording and NEWACHIEVEMENT terms differ from TENNISMAJOR's after-issuance
condition; do not transfer that condition between products. Expected expiry
Sep 15 14:00Z, close/latest Sep 29 14:00Z, settlement timer 300s are not an
actual release guarantee. Elimination, cancellation/fractional payout,
postponement and outcome-review contingencies still require a complete
promotion gate. No annual paper promotion or fill.

Operational limits: paper_report's review_result=success is current systemd
Result, not durable proof of prior successful review; known 06:00 usage-limit
failure remains recorded. Scheduler untouched. Guessed data/scores,
data/related and qualifier_taker.py paths were absent; resolved actual paths.
Audit initially measured late-read freshness against its start timestamp;
corrected those fields using fresh reads/time before saving final evidence.
No credentials accessed or printed. Commit/push result follows in execution log.

Handoff: preserve processes and experiment. Current primary process window is
~7h40m, NOT >=24h; earliest process/capture-window checkpoint Sep 8 02:22/02:23,
annual 02:59. This is not an uninterrupted socket claim: idle disconnects above
are recorded. Verify fresh subscriptions at next matches, confirmed timing,
missed depth, settlement lag, annual eligibility and monitoring blind spot.
One bounded review completed; no future wait or additional jobs.

## 2026-09-07 12:00–12:04 UTC — bounded review

Read both required documents first; initial git status empty. Paper/read-only
scope maintained: no agents, trading keys, real orders, restarts, configuration
or scheduler changes. Only documentation/evidence written. Evidence files:
`data/reviews/20260907T120000Z-{review.md,audit.json,account-check.json}`.

All six services active, NRestarts=0: primary PID 23503 since 02:22:31,
confirmed 23560 since 02:23:12, scores 6664 since Sep 6 21:13:37,
annual 25696 since 02:58:57, legacy REST/WS 670/672 since Sep 6 20:25:26.
Disk 16G/48G used, 32G free (33%). S3 journal confirms success 12:00:20,
124 objects/19,459,128,930 bytes; remote object integrity not checked.
Read paper_report.py and executed it at 12:00:24: no warnings, score age
1.6s, discovery 42.5s, heartbeat 111.4s. Both accounts zero watched matches,
legs, positions and locked cash. Both feed_ready=false after previously
recorded idle disconnects; no new reconnect/errors since 10:00 in primary,
confirmed, scores or legacy REST/WS file logs. Future subscription recovery
still unobserved. Quiet overnight books do not establish a dead feed.

Streamed primary 56,041 complete raw records (8 snapshots/56,033 deltas),
epochs 1788747770.4179873–1788753040.1977906, zero connection/sid sequence gaps,
max interval 1070.488s. Annual: 55 catalogs/subscriptions, 1,485 snapshots,
2,061 deltas through 1788782346.1373408, zero sequence gaps; max interval
600.486s. One historical capture_error remains (08:17, documented previously),
no additional error count; current 27/27 snapshots ready. Legacy streamed
5,409,814 records through 1788751910.349; max gap 113.464s; sequence validation
not performed for legacy schema. All active gzip streams end in EOF after
complete records, not finalized archives. Legacy WS idle heartbeats continue
through 12:00:32; REST labels Sep 8 TIAMIC/SHEALC as live, not current play.
REST orderbook/trades files grew to 1,800,351/1,513,661 bytes, ages 2.9/32.2s.
Late audit discovery age 6.1s, zero started/groups, 12 future main-tour matches,
next 15:00 UTC. All eight Sep 6 score rows closed, individually received
3.4–55.4s earlier and present in primary logs. Older retained scores may be
stale outside polling selection; global freshness is not source-event latency.
No newly missed match/false positive identified; no independent full draw census.

Both directories have zero new actions of any kind since 10:00. Recomputed
baseline analysis: primary 7 inferred fills/741 contracts, three REST-price-absent
misses, short $5.530265 and qualifier $5.81; confirmed one 47-contract fill,
$0.43. All durable account JSON parses, positions empty and totals consistent.
Michelsen/Pegula entries precede ended receipt by 658.049/1385.082s; Navarro
broad precedes by 73.740s, confirmed follows by 0.471s. These are score-receipt
comparisons, not independently measured last-point timestamps. Separate accounts
cannot sum overlapping liquidity. No new settlement to compare; previous
Navarro published-result recognition versus exchange cash settlement remains
an unresolved simulator limitation, not newly rechecked against public REST.
Recomputed five qualifier costs with cent-ceiling quadratic fees: all match
($0.09/$0.07/$0.27/$0.02 broad, $0.04 confirmed). Read both fill paths and
PaperLiquidity: delayed active/unresolved REST exact-price depth, current WS
and remaining-depth minimum, conservative resnapshots, save before action log.
Short fees still unrounded; parent directory not fsynced, account/log not one
transaction, intervening depth not continuously durable. No crash injection.
13 support unittests and full test_winner_taker.py passed; existing unclosed
config ResourceWarning persists. No narrow production fix justified this interval.

Annual catalog remains 27 contracts; 15 player UUIDs match score cache, 12
prospective joins unverified. Reopened official AO Alcaraz/Rybakina, RG
Zverev/Andreeva and WTA Wimbledon Noskova sources linked in 04:00 notes.
ATP Wimbledon page returned a tool internal error; official dated July 12
[Wimbledon Sinner champion gallery](https://www.wimbledon.com/en_GB/gallery/jannik_sinner_champion)
provided replacement evidence. Six distinct current-year pre-USO singles
champions, only USO remaining: annual active status never implies zero titles.
Read both archived PDFs fully again. TENNISMAJOR counts singles after issuance;
NO may retain capital to year-end/latest Jan 7 2027 plus settlement/review.
NEWACHIEVEMENT includes elimination/withdrawal, cancellation fractional payout,
postponement/suspension up to two years and review contingencies. Do not transfer
after-issuance condition into the distinct Alcaraz product's explicit year terms.
KXGRANDSLAM-CALC26-2 remains active/unresolved, at least two in 2026, UUID
527915ea-e368-4c7f-a203-c83ebb6f6572 matches scores, bid/ask 57/59c. Already
AO champion; USO needed for second under normal completion. Expected expiry
Sep 15 14:00Z, close/latest Sep 29 14:00Z, 300s timer do not guarantee release.
No annual paper promotion or fill; complete per-contract promotion gate remains.

Operational limitations: review_result=success still reflects current systemd
Result, not durable proof previous review completed; known 06:00 usage failure
retained. Scheduler untouched. Broad /tmp filename search produced permission
denials for systemd-private directories; relevant existing audit/PDF resources
were found and read. No credentials printed/accessed. Commit/push outcome will
be recorded by execution and final response.

Handoff: maintain experiment, current primary process only ~9h40m, not >=24h.
Earliest current primary/confirmed process checkpoints Sep 8 02:22/02:23 UTC,
annual 02:59. Idle socket disconnects remain documented; verify new subscriptions
and snapshots when matches resume, then completion timing, missed depth and
settlement/cash lag. One bounded review; no sleep for matches or additional jobs.

## 2026-09-07 14:00–14:03 UTC — bounded paper review

Read RUN_NOTES.md and READINESS.md first; initial worktree clean. No agents,
trading keys, real orders, restarts, configuration/scheduler changes or new jobs.
Evidence: data/reviews/20260907T140000Z-audit.json; only review documentation changed.

All six services active, NRestarts=0: primary PID 23503 since 02:22:31,
confirmed 23560 since 02:23:12, score 6664 since Sep 6 21:13:37, annual
25696 since 02:58:57, legacy REST/WS 670/672 since Sep 6 20:25:26.
Disk 16G/48G used, 32G free (33%). S3 sync succeeded 14:00:20 with 124
objects/19,459,128,930 bytes; remote integrity not verified.

Read and ran paper_report.py. At 14:00:26 annual feed_ready=false produced
an awaiting-snapshots warning during routine refresh; raw connection 67 contains
27 fresh snapshots through epoch 1788789624.2400837, and subsequent health
is ready. At 14:01:49 report warnings empty, score age 1.6s, discovery 54.1s,
primary heartbeat 75.7s. Both paper modes remain zero matches/legs and
feed_ready=false after the previously documented idle disconnects. ws_loop
waits for discovery when no tickers and requires fresh snapshots on reconnect.
Actual future subscription recovery remains unobserved; no restart justified.

Streamed complete records in all three active gzip captures (each ends at open
stream EOF, not a finalized archive). Primary unchanged: 56,041 records,
8 snapshots/56,033 deltas, epochs 1788747770.4179873–1788753040.1977906,
zero connection/sid sequence gaps, max record interval 1070.488s. Annual:
67 catalogs/subscriptions, 1,809 snapshots/2,450 deltas, last epoch
1788789624.2400837, zero sequence gaps, max interval 600.486s. One historical
08:17 capture_error only; no new errors. Legacy 5,409,814 records through
1788751910.349, max interval 113.464s; legacy sequence validation NOT performed.
Legacy WS zero-match heartbeats continue through 14:00:33. REST files grew to
1,913,796/1,532,752 bytes, ages 4.3/23.9s; its two live labels concern Sep 8
TIAMIC/SHEALC. No new error/reconnect/sequence/API-429 lines since 12:00 in
primary, confirmed, score or legacy file logs. Quiet capture is consistent
with idle markets, not established dead feeds; historical disconnect loss
cannot be excluded.

Eight Sep 6 completed score rows remain closed, refreshed 0.9–56.9s earlier,
all present in primary logs. New score row ANDPOT is not_started, scheduled
15:00 UTC, not yet watched at 14:01: this is outside discovery's 45-minute
preroll (14:15), not evidence of a miss. Fresh discovery has zero groups/started
and 12 future main-tour matches. No new completion, false positive or missed
market identified; independent full tournament census remains unverified.

BOTH data/live and data/confirmed have zero new actions of any kind since
12:00. Recomputed baseline analysis: primary seven inferred fills/741 contracts,
three REST-price-absent misses, short realized $5.530265 and qualifier $5.81;
confirmed one 47-contract fill, $0.43. All durable accounts parse, flat with
zero locked cash. Michelsen/Pegula fills precede first ended receipt by
658.049/1385.082s; Navarro broad precedes by 73.740s, confirmed follows by
0.471s. Score receipt is not an independently timed final point. Independent
accounts cannot sum overlapping liquidity. No new settlements to reconcile;
previous result-recognition versus actual cash-settlement lag remains unresolved.

Recomputed five qualifier fees/costs: all match, fees $0.09/$0.07/$0.27/$0.02
broad and $0.04 confirmed. Read both fill paths and PaperLiquidity: delayed
active/unresolved REST exact-price depth, current WS and remaining-depth
minimum; conservative resnapshot consumption and settled ticker persistence.
Correction to previous notes: qualifier saves before action logging, but SHORT
fills log the take before save_paper_account (winner_taker.py:1153). Neither
path makes account/log atomic. File fsync without parent directory fsync,
unsaved intervening depth and unrounded short fees remain limitations. No
observed corruption or crash injection. No production fix justified this idle
interval. Tests: 13 support unittests and full winner_taker script passed;
existing unclosed temporary config ResourceWarning persists.

Annual catalog remains capture-only, 27 contracts, 15 UUIDs join score cache,
12 prospective identities unverified. Reopened official sources linked in the
04:00 notes: AO Alcaraz/Rybakina, RG Zverev/Andreeva, WTA Wimbledon Noskova,
and the official dated July 12 Wimbledon Sinner champion gallery linked at
12:00. Verified six distinct 2026 pre-USO singles champions; only USO remains.
Active annual status never proves absence of a prior title. Read both archived
PDFs fully: TENNISMAJOR singles titles count after issuance; annual NO can
retain collateral to year-end/Jan 7 2027 plus settlement/review. Specific
Alcaraz calendar-year wording differs from that after-issuance condition.
KXGRANDSLAM-CALC26-2 remains active/unresolved, 57/59c, at least two in 2026,
UUID 527915ea-e368-4c7f-a203-c83ebb6f6572 matches score identity. Already AO
champion; USO needed for his second under normal completion. Expected expiry
Sep 15 14:00Z, close/latest Sep 29 14:00Z, timer 300s do not guarantee release.
NEWACHIEVEMENT cancellation/fractional payouts, elimination, postponement up to
two years and review clauses must be resolved per contract before promotion.
No annual promotion or fill.

Operational limits: no execution/auth failure in checks this review; git push
outcome recorded in execution/final response. The monitor's current systemd
review_result=success does not prove prior scheduled completion; known 06:00
usage failure remains. Initial combined document output was truncated; relevant
remaining review sections were subsequently read. No credentials accessed or
printed. Handoff: maintain running experiment (~11h40m current primary process,
NOT >=24h). Earliest process-window checkpoint Sep 8 02:22/02:23 UTC, annual
02:59; not a claim of uninterrupted sockets. Next review should verify resumed
subscriptions/snapshots from 14:15 preroll, completion timing, missed depth and
settlement cash lag. One bounded review; exit without waiting for matches.
