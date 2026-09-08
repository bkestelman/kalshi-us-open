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

## 2026-09-07 16:00–16:04 UTC — bounded paper review

Read RUN_NOTES.md and READINESS.md first; initial worktree clean at a93e661.
No agents, trading-key access, real orders, restarts, configuration changes,
scheduler changes or new jobs. Only review evidence/documentation written.
Evidence: data/reviews/20260907T160000Z-audit.json and timestamped public
Potapova/Alcaraz market responses and QUAR catalog. Initial large output was
truncated; remaining run-note sections and relevant code were read separately.

All six services active, NRestarts=0: primary PID 23503 since 02:22:31,
confirmed 23560 since 02:23:12, scores 6664 since Sep 6 21:13:37, annual
25696 since 02:58:57, legacy REST/WS 670/672 since Sep 6 20:25:26.
Disk 16G/48G used, 32G free (33%); 2903 MiB memory available, swap unused.
S3 sync succeeded 16:00:22, total size 19,459,130,582 bytes; remote integrity
and current-file backup completeness not checked. Unrelated control service
still auto-restarting; outside this repo, unchanged.

Read and executed paper_report.py: no warnings; audit score age 0.5s,
discovery 27.8s. Both modes now feed_ready=true, two matches/13 legs, zero
positions/locked balances. Automatic recovery after overnight idle VERIFIED:
primary watched ANDPOT 14:15:28.270, subscribed seven tickers 14:15:28.340;
confirmed watched/subscribed 14:16:14.169/.229. Primary added SWIZHE
14:45:25.659, subscribed 17 at .954; confirmed 14:46:14.244/.488.
Raw primary connection 3 has seven snapshots starting 1788790528.3546972;
connection 4 has 17 starting 1788792325.9810877. No restart needed.

Streamed primary 321,049 complete records (32 snapshots, 321,017 deltas),
through 1788796869.6451004; zero connection/sid sequence gaps. Full-day max
interval 37,488.157s is overnight idle. A later resumed-only pass counted
269,496 records with maximum interval 12.205s. Annual 78 catalogs/subscriptions,
2,106 snapshots, 2,986 deltas through 1788796821.285499, zero sequence gaps,
max interval 600.486s; only historical 08:17 capture_error, current 27 ready.
Legacy 6,194,709 complete records (6,130,657 deltas, 63,816 trades, 236
snapshots) through 1788796905.830; max interval 42,096.490s includes idle.
Legacy sequence validation NOT performed. All gzip streams end at open-member
EOF after complete records, not finalized archives. Legacy two-match heartbeat
continues through 16:00:33. REST book/trade files 2,446,991/2,058,746 bytes,
ages 1.80/0.08s. No primary/confirmed/score/REST errors since 14:00; legacy
WS has one ticker-set reconnect at 15:48:50.436. Capture mtime ages initially
used audit-start time and became negative as files grew; corrected with fresh
sample timestamps. An attempted audit read before its process finished failed
FileNotFoundError; successful completed audit was subsequently read.

Both current matches have live scores and no winner: at receipt
1788796908.5231605 Potapova led Andreeva 7-5, 0-0; Swiatek led Zheng 3-0
in set one. Both are mapped/watched. Eight earlier closed matches are present
in primary logs, received 2.9–42.8s before audit. CERBLO not_started at 17:00
is outside 45-minute preroll at this review, not a missed match. Score receipt
freshness is not source latency. Investigated unusual KXWTA-26-POT: public GET
confirms USO event KXWTA-26USO, Anastasia Potapova and UUID
23554e73-dcff-4dd7-b90f-176cd7761733 matching score. Public QUAR catalog
returns 24 markets, empty cursor, no Potapova; no evidence of dropped QUAR
listing. Broader independent tournament census remains unverified. Zheng has
zero R samples in log, consistent with known one-sided-book limitation;
no new signal/false positive identified.

BOTH live and confirmed have zero new actions since 14:00, hence no new
completion/fill/settlement comparison. Baseline remains seven speculative
fills/741 contracts, three REST-price-absent misses, short realized $5.530265,
qualifier $5.81; confirmed one 47-contract fill, realized $0.43. Durable JSON
accounts parse and are flat. Recomputed fill-versus-first-ended timing:
Michelsen/Pegula precede receipt 658.049/1385.082s; Navarro broad precedes
73.740s, confirmed follows 0.471s. These are not independently timed last
points. Counterfactual accounts cannot sum overlapping liquidity. No new
settlement; prior published-result versus actual cash-release lag remains.

Read depth/REST/fee/persistence paths: delayed active/unresolved exact-price
REST quantity plus current WS and remaining ledger minimum, conservative
resnapshots. Recomputed all five qualifier fees/costs: $0.09/$0.07/$0.27/$0.02
broad and $0.04 confirmed, all match. Short fees remain unrounded. Qualifier
saves before logging; short logs before save. No parent-directory fsync,
account/log transaction or continuously durable intervening depth. No observed
corruption; no crash injection. No narrow production fix justified. Tests:
13 support unittests and full winner_taker script passed; existing unclosed
config ResourceWarning remains. git diff --check performed before commit.

Annual capture-only catalog remains 27 contracts, 15 UUIDs match score cache,
12 prospective identities unverified. Reopened official sources linked in
04:00/12:00 notes: 2026 AO Alcaraz/Rybakina, RG Zverev/Andreeva, Wimbledon
Sinner/Noskova. These are current-year titles before USO, not career or prior
calendar-year totals. Only ongoing USO remains. Andreeva already won RG;
a loss today must not imply zero annual titles. Read both archived rule PDFs:
TENNISMAJOR counts singles after issuance; annual NO may retain collateral to
year-end/latest Jan 7 2027 plus settlement/review. Specific Alcaraz calendar-year
terms are distinct from that after-issuance condition. Public GET confirms
KXGRANDSLAM-CALC26-2 active/unresolved, 59/60c, at least two in 2026, UUID
527915ea-e368-4c7f-a203-c83ebb6f6572 matching score identity. Already AO
champion; USO needed for second under normal completion. Expected expiry
Sep 15 14:00Z, close/latest Sep 29 14:00Z, timer 300s do not guarantee cash
release. NEWACHIEVEMENT elimination, cancellation/fractional payouts,
postponement up to two years and review clauses remain promotion-gate work.
No annual promotion or fill; active listing never proves absence of prior titles.

Handoff: preserve experiment, primary process only ~13h40m, not >=24h.
Earliest current primary/confirmed process checkpoints Sep 8 02:22/02:23,
annual 02:59; documented idle sockets are not uninterrupted capture claims.
Next timer should compare current matches' eventual completion against fills,
check future preroll/related-leg additions, missed depth and settlement lag.
paper_report review_result=success remains current systemd Result, not durable
proof previous scheduled review completed (known 06:00 usage failure).
No execution/auth failure beyond the premature local audit read above; push
outcome follows in execution/final response. One bounded review; exit without
waiting for matches or changing scheduler.

## 2026-09-07 17:07 UTC — user-facing review summaries

Verified timer ran 04/06/08/10/12/14/16 UTC; latest service success ended
16:03:48. 06:00 was incomplete due to usage limit, as documented by 08:00.
User requested a brief update for every review. Prompt now requests a one/two
sentence final response; wrapper appends it to data/reviews/updates.md and
records explicit failure on nonzero exit. Backfilled concise prior summaries.
No supported delivery connection exists from the VPS CLI to this original chat;
explained that limitation instead of promising automatic chat posts. Shell syntax
checked. No simulator or collector restart. Abrupt SIGKILL/power loss cannot run
the EXIT trap; systemd status and missing update remain necessary checks.

## 2026-09-07 — automatic review delivery to original conversation

Corrected earlier unsupported claim that VPS reviews cannot reach the original
chat. Installed Codex CLI exposes `codex queue --thread UUID --message TEXT`.
The current CODEX_THREAD_ID is 01a0786a-ed8c-71c1-be7d-927c35478e4d; a direct
test returned a queued-message ID. Added review_notify.py: durable pending/sent
outbox, serialized delivery, retries on CLI errors/timeouts, and duplicate
suppression. Review EXIT handler now queues each final result or failure to
this exact conversation. Minute monitor ExecStartPost evaluates warnings and
retries pending deliveries. New/changed health warnings queue immediately on
that minute's check; unchanged warnings remind at 30 minutes; recovery queues
one followup. Messages request immediate investigation/fixes within paper-only
scope and brief chat updates. Scheduled review editing must be coordinated.

Validation: three focused tests cover failed delivery and retry, duplicate
suppression, timeout retention, warning deduplication/reminders/recovery. Shell
syntax and diff checks pass. Replayed actual 16:00 review through delivery:
outbox has zero pending, one accepted review, no error. Deployed monitor unit
and executed it successfully. Simulator and collectors unchanged. A separate
queue test is also waiting for this thread to consume it. CLI acceptance proves
queueing, not yet consumption by the current conversation. Automatic responses
still depend on the session/runtime being available and model usage capacity;
queue delivery cannot remove those limits. Crash between successful queueing
and durable acknowledgment can duplicate a message (at-least-once delivery).

## 2026-09-07 17:43 UTC — automatic snapshot-wait alert investigated

Alert was delivered to the original thread without user prompting. Related
collector was already recovered: active, PID25696, connection89, all27
snapshots, feed_ready true, fresh heartbeat. No review worker active and no
restart needed. Minute report caught routine ten-minute subscription refresh.
Added 120-second persistence requirement ONLY for related snapshot-wait chat
alerts; raw monitor warning remains recorded, and other warnings are immediate.
Five notification tests pass, including transient refresh versus real stall,
recovery and immediate disk warning despite simultaneous snapshot wait.
No trading/collector changes. Queue delivery and automatic investigation now
observed end to end.
# 2026-09-07 18:00–18:04 UTC bounded review

Initial worktree clean at d42efa7. Read RUN_NOTES.md and READINESS.md first;
truncated combined output was followed by targeted reads. No agents, trading
keys, real orders, restarts, strategy/configuration/scheduler changes or new jobs.
Evidence: `20260907T180000Z-{audit,completion,account-check,final-health}.json`.

All six paper/score/related/legacy services active, NRestarts=0. Primary PID
23503 since 02:22:31, confirmed 23560 since 02:23:12, score 6664 since Sep 6
21:13:37, annual 25696 since 02:58:57, REST/WS 670/672 since Sep 6 20:25:26.
Disk 16G/48G, 32G free (33%). S3 completed 18:00:21, 124 objects,
19,459,131,452 bytes; remote integrity/current-file completeness unverified.
Unrelated control service remains auto-restarting; outside scope, unchanged.
Read and executed paper_report.py: no warnings, final score/discovery ages
1.0/1.4s. Its review_result still reflects current systemd state, not durable
previous-review success; known 06:00 usage failure remains historical.

Streamed 1,398,708 primary records (112 snapshots, 1,398,596 deltas), through
1788804065.7385483; zero per-connection/sid sequence gaps. Since 16:00 maximum
record interval 6.205s; full-day 37,488s gap is documented overnight idle.
Annual: 90 catalogs/subscriptions, 2,430 snapshots, 4,651 deltas, through
1788804010.1241343, no sequence gaps, max interval 600.486s, only historical
08:17 capture_error. Current 27/27 snapshots ready. Legacy WS: 9,469,373 deltas,
136,258 trades, 322 snapshots through 1788804122.978; legacy sequence validation
not performed. Open gzip EOF follows complete records, not finalized archives.
REST book/trade sizes 3,552,946/3,648,961 bytes, ages 1.0/17.6s. No new primary,
confirmed, score or REST errors since 16:00; legacy ticker-set reconnect
17:25:43.725 only. No new sequence/API error observed.

New broad results since 16:00: seven REST-verified speculative fills, 303
contracts, median decision-to-fill 145.001ms; two REST-price-absent misses.
- Andreeva QUAR YES 26 @99c, 17:32:37.952, 233.058s before first ended receipt.
- Swiatek WIN NO 91 @YES 3c and FIN NO 10 @6c at 17:35:20; QUAR NO
  10 @10c at 17:35:22, 19 @8c at 17:36:29. These four are model-edge signals,
  262–331s before ended, not inferred-elimination signals.
- Zheng QUAR YES 26 @99c at 17:38:22.468, 148.564s before ended.
- Swiatek QUAR NO 121 @YES 2c at 17:38:23.503, 147.529s before ended.
The other three entries are book-inferred. No new confirmed fills or settlement
in either directory. Andreeva won 5-7, 6-4, 6-3 (first ended 1788802591.0103912);
Zheng won 7-5, 6-3 (1788802851.032236). IDs match decision score context.
Receipt comparisons are not independently measured last-point latency. No new
losing filled signal; successful outcomes do not validate early entries as safe.

Reconstructed six relevant books across snapshots/connections: immediately before
and for five minutes after ended receipt, AND/ZHE QUAR had no asks; POT WIN and
SWI WIN/FIN/QUAR had no YES bids. This supports confirmed zero fills for these
inspected legs, not a claim of exhaustive market coverage. Public unauthenticated
GETs of all six plus Alcaraz succeeded; all active/unresolved. Held positions
correctly remain pending, not booked as new profit. Total broad now 14 fills /
1,044 contracts; realized unchanged $5.530265 short and $5.81 qualifier.
Three short positions lock $242.73; two qualifier positions cost $51.52.
Confirmed remains flat with one historical fill / $0.43. Counterfactual accounts
must not sum overlapping liquidity.

Recomputed each open short count/cash/collateral from actions: all match durable
JSON. Short cash: WIN $2.544633, FIN $0.56052, QUAR $4.6131. All seven historical
and new qualifier costs match cent-ceiling quadratic fees; new fees $0.02 each,
cost $25.76 each. Read fill/settlement/persistence paths: delayed exact-price
public REST + current WS + remaining ledger minimum, consumed levels retained
across snapshots. Known limitations remain: short fee unrounded; file fsync but
no parent-directory fsync; account/log not transactional (short logs before save,
qualifier saves before log); intervening depth not continuously durable; published
result recognition can release paper cash before actual exchange settlement.
No corruption observed or crash injected. No production fix justified this run.
13 support tests and full winner_taker test script pass; existing unclosed config
ResourceWarning persists. diff whitespace check performed before commit.

Coverage: all ten closed Sep6-key score events in primary logs; individual ages
2.1–46.4s. CERBLO watched 16:15:03, marked IN PLAY 17:50:30, but score source
still says not_started on fresh (~1s) receipt at final check. This source-status
lag is unresolved and can delay confirmed entries; global cache freshness is
not proof of actual score freshness. OSARYB preroll watched/subscribed primary
18:00:48, confirmed 18:01:14/15; heartbeat count lag reflects two-minute reporting.
No discovered eligible match omission established; independent draw census and
missing Blockx qualifier listing verification remain outside this bounded audit.

Annual remains capture-only, 27 contracts, 15 exact UUID joins, 12 unverified.
Reopened official sources linked in the 04:00 notes: 2026 AO Alcaraz/Rybakina,
RG Zverev/Andreeva, Wimbledon Sinner/Noskova. These are current-year pre-USO
singles titles, not prior-calendar-year/career totals. Only USO remains; active
annual listings never imply no prior major. Read both archived PDFs fully:
TENNISMAJOR counts singles after issuance and annual NO may retain capital until
year-end/latest Jan7 2027 plus review. Alcaraz KXGRANDSLAM-CALC26-2 public rules
say at least two in 2026, UUID 527915ea-e368-4c7f-a203-c83ebb6f6572 matches scores;
already AO champion, thus needs USO under normal completion. Its specific annual
terms differ from TENNISMAJOR after-issuance terms. Expected Sep15/close Sep29
14:00Z, 300s timer are not cash-release guarantees. NEWACHIEVEMENT elimination,
withdrawal, fractional cancellation, up-to-two-year postponement and review
contingencies still require complete promotion review. No annual promotion/fill.

Operational failure: one local output formatter assumed discovery groups were
objects and raised AttributeError after displaying audit sections; corrected by
printing actual nested-list schema. Audit itself completed successfully. No
execution/auth failure in public checks/tests; commit/push outcome follows.
Handoff: maintain experiment (~15h40m primary process, not >=24h). Earliest current
primary/confirmed process checkpoints Sep8 02:22/02:23, annual 02:59, not claims
of uninterrupted sockets. Next review: pending five positions, CERBLO source
score lag, new preroll matches, and confirmed executable depth. Exit now without
waiting for matches or changing scheduler.

### 18:04 UTC automatic follow-up on CERBLO score lag

Original conversation received scheduled review automatically. Checked fresh
collector cache and independently GET /live_data/milestone/ccf0ac5b-92a8-47b6-9c2d-36af74664963:
both return not_started with empty round scores and matching player UUIDs.
Thus the missing score is present at the upstream individual endpoint too,
not merely a stale local cache or batch-only parsing failure. This does not
independently establish the actual first-ball time. Current health warnings
empty; review worker exited; no collector restart justified. Book-driven
paper path remains available; confirmation-only path must wait for valid score
evidence. Seven new speculative fills remain pending per 18:00 review.

## 2026-09-07 20:00–20:04 UTC bounded review

Initial worktree clean at c663083. Read RUN_NOTES.md and READINESS.md first,
then targeted notes after combined output truncation. Paper/public-read-only
scope maintained: no keys, orders, agents, restarts, strategy/config/scheduler
changes or new jobs. No production fix justified. Evidence saved locally under
`data/reviews/20260907T200000Z-{audit,public,account-check,quar-catalog,final-health}.json`.

All six paper/score/annual/legacy services active with NRestarts=0: primary
23503 since 02:22:31, confirmed 23560 since 02:23:12, scores 6664 since Sep6
21:13:37, annual 25696 since 02:58:57, legacy REST/WS 670/672 since Sep6
20:25:26. Disk 16G/48G, 32G free (34%). S3 sync completed successfully
20:00:21 with listed total 19,459,132,558 bytes; remote integrity and open-file
completeness not verified. Unrelated control service still auto-restarting,
unchanged. Read and executed paper_report.py: warnings empty, primary and
confirmed 4 matches/23 legs and feed_ready=true; initial score/discovery ages
0.1/13.1s. review_result=success is current unit state, not historical proof
all scheduled reviews succeeded (06:00 usage failure remains documented).

Actual raw continuity: streamed primary 1,882,975 records (170 snapshots,
1,882,805 deltas), epochs 1788747770.4179873–1788811268.9575064, zero
per-connection/sid sequence gaps; max interval since18:00 4.762s. Full-day
37,488.157s gap is documented overnight idle, not new capture failure.
Annual 102 catalogs/subscriptions, 2,754 snapshots, 4,858 deltas; latest epoch
1788811106.697125, zero sequence gaps; max recent interval 600.429s agrees
with ten-minute refresh. Sole capture_error is the historical 08:17 incident;
27/27 snapshots ready. Legacy streamed 12,887,998 deltas, 184,040 trades,
445 snapshots through 1788811354.622, max recent gap 5.917s; legacy sequence
validation not performed. Open gzip EOF followed complete records, not sealed
archives. REST orderbook/trade files 4,705,729/4,682,564 bytes, ages 1.4/9.7s.
No new primary/confirmed/score/REST error, sequence or API failure in logs since
18:00; legacy ticker-set reconnects 18:11:09.859,18:12:10.180,18:15:12.482.
Relevant warning-level journals empty. No restart needed.

Both action directories inspected: no new fills or misses since18:00. Five
primary positions settled at18:11:41.168–.333: Andreeva and Zheng QUAR YES,
26 each, +$0.24 each; Swiatek WIN/FIN/QUAR NO, 91/10/150 contracts,
exact profits $2.544633/$0.560520/$4.613100 (action log rounds cents).
Incremental short +$7.718253 and separate qualifier +$0.48. Public GETs
confirm all five finalized with matching outcomes/UUIDs and qualification
rules. Exchange settlement timestamps 18:15:45.482–.493 are ~244 seconds
after paper release, repeating the known cash-availability limitation.
All positions now closed. Broad total14 fills/1,044 contracts: short realized
$13.248518, qualifier $6.29; confirmed remains one47-contract fill/$0.43.
Alternative accounts must not sum overlapping liquidity as executable volume.

Recomputed all8 short fill proceeds using existing unrounded quadratic fee,
all6 broad and1 confirmed qualifier costs using cent-ceiling quadratic fee;
all durable realized totals, take counts and empty positions agree. Every
fill has verified REST status and quantity >= filled count. Reviewed delayed
REST/current WS/remaining-depth minimum and durable consumed-level paths.
Known limitations unchanged: fee conventions differ; file fsync lacks parent
fsync; account/log not transactional; intervening depth not continuously durable;
paper result recognition precedes actual exchange cash release. No corruption
observed, no crash/restart injected. 13 support tests and full winner_taker
script pass; existing temporary-config ResourceWarning persists. No new test
or strategy change. Diff whitespace check before commit.

Completion comparison: no newly completed score event since18:00; all10 closed
Sep6-key events occur in primary logs. Andreeva/Potapova 5-7,6-4,6-3 and
Swiatek/Zheng 5-7,3-6 agree with settlements; prior seven entries remain
speculative (233s/149s early YES and147–331s early shorts), not confirmed
post-last-point execution. No new losing filled signal observed; historical
Zheng false signals and early profitable entries still preclude safety claims.
Prior18:00 reconstruction found no executable bid/ask in the six inspected
legs at confirmation; no new completion requires a new such replay this run.

CERBLO source now recovered: public individual endpoint and local cache agree
live third set (Cerundolo3-6,6-4,1-2 Blockx at check). Score transitions show
live/match_about_to_start1788803409.070, back to not_started1788803479.066,
then live1788804585.150 (18:09:45.150). Thus source briefly regressed, rather
than never having emitted live; actual first-ball time remains unverified.
OSARYB marked IN PLAY20:00:40.675 while fresh score still not_started/0-0
at20:01:43; monitor does not detect semantic score lag. Handoff: watch that
transition. GEAVAN and KHATIE preroll added18:45:09.638/19:20:13.149;
both services have four matches/23 legs. Public QUAR event catalog returned
31 markets, empty cursor, no Blockx/Arthur Gea/Van de Zandschulp listings:
missing qualifier joins for these names are consistent with missing listings,
not a demonstrated discovery bug. Independent complete draw census not done.

Annual remains capture-only:27 contracts,15 exact score-cache UUID joins,
12 unmatched identities still require validation before any promotion. Reread
both complete archived TENNISMAJOR and TENNISMILESTONES/NEWACHIEVEMENT PDFs.
Current-year pre-USO titles rechecked on official pages linked in04:00 notes:
AO Alcaraz/Rybakina, RG Zverev/Andreeva, Wimbledon Noskova. ATP Sinner source
failed twice (Internal Error then403); Sinner title remains prior verified
04:00/18:00 evidence, not newly reverified this run. Only USO remains under
normal2026 schedule. Existing active listings never establish zero titles.
Public Alcaraz KXGRANDSLAM-CALC26-2 is active/unresolved, at least2 in2026,
UUID527915ea-e368-4c7f-a203-c83ebb6f6572 matches scores; AO already won,
so USO supplies the second under previously verified Wimbledon history.
Calendar-year wording differs from generic TENNISMAJOR after-issuance terms.
ExpectedSep15/closeSep29 14:00Z are not cash-release guarantees. Annual NO
can retain collateral through year-end/latestJan7 plus review; achievement
terms include elimination, withdrawal, fractional cancellation and up-to-two-
year postponement. No annual promotion or paper fill.

Operational failures: attempted reading audit JSON before background scan
finished raised FileNotFoundError; subsequently waited for exit0 and read the
complete artifact. ATP source fetch failure recorded above. No public API or
test auth failure. Notes/report only are committed; raw JSON remains ignored
local evidence. Push outcome recorded in final response/execution log.
Handoff: preserve continuous experiment (~17h40m current primary process,
not yet>=24h). Earliest current primary/confirmed24h checkpoints Sep8
02:22/02:23, annual02:59; historical baseline Sep6 21:28 is not uninterrupted
capture. Next review: CERBLO/OSARYB completion, source regressions, new confirmed
executable depth and preroll coverage. Exit without waiting or scheduling.

## 2026-09-07 20:12 UTC — user-authorized live pilot implementation

User explicitly authorized live launch after blockers, strict 99/1 signal,
$25 total/$5 per match, and supplied ~/trade-key{,-id}. Explained ambiguous
submission: exchange acceptance can precede lost response, so releasing budget
or retrying can duplicate risk. Added shared pilot.py live/paper strategy and
durable pilot_execution ledger. POST attempted once, intent fsynced before
submission; uncertainty pauses all entries and retains allocation across
restarts until terminal exchange reconciliation. Missing from order history
is not rejection. Both sides/all related legs share match event and total cap;
fees included. Pilot allocation is cumulative and not recycled on settlement.
Broader paper accounts unchanged; pilot shadow uses $500/$125. See PILOT.md.

Legacy winner_taker.py live CLI now routes to new pilot. Old signed helper also
no longer retries POSTs. Winner-YES is now executable through the same pilot
strategy as shadow NO/YES, instead of paper-only module. Annual capture-only and
model-edge speculative branch excluded from pilot. Key files kept outside repo,
permissions tightened to600, no secret output. Signed GET balance/order history
and four fee schedules passed preflight; actual order schema executed/fp counts
verified from read-only existing account order. No preflight test orders.

11 pilot tests, five notification tests,13 support tests and full existing
winner script pass; compile/shell/diff validation pass. Scheduled review scope
and chat alert instructions updated for user-authorized live pilot; no cap
increase or broader live strategy authorized. Minute report tracks both pilot
health, errors and unresolved reservations. Pending deployment follows.

### Live/shadow deployment verified, 20:12 UTC

Committed/pushed f47e716 before deployment. Shadow started20:11:35 UTC, live
started20:11:54 UTC after shadow snapshots were ready. Live PID85944 and shadow
PID85822 each received all31 subscribed books for23 related legs; feed_ready
true, account_ready true, zero orders/allocated/unresolved/errors. Minute report
no warnings. Live preflight again verified signed account reads before enabling
entries. Existing broad and confirmed paper services were not restarted. No
real order was forced to test connectivity; first eligible live opportunity
will provide actual execution evidence. Live service has Restart=no.

The literal ~/trade-key paths are read by the isolated single-attempt pilot
transport; keys were not copied into repo or shared with paper collectors.
No cap expansion or model-edge/annual live entry enabled.

## 2026-09-07 21:27 UTC — first live fill and ambiguous second submission

Automatic alert reached original conversation. Live ledger contains a confirmed
5-contract Rybakina QUAR YES fill at99c (21:14:00.329844 UTC). Signed exchange
orders/fills both confirm order01a07db8-85c0-7e24-9519-c2abbe2bf4b7 executed5;
fill fee_cost0.003500 (raw exchange trade-fee field; do not infer final rounded
cash movement from that alone). Pilot reservation remains4.96.

Second intent: Cerundolo FIN sellYES5 at1c, client ID
tennis-pilot-00356a62-278e-4269-abd6-630424c09599, returned RemoteDisconnected.
Full4.96 reservation retained, global entry paused; total allocation9.92.
Direct signed ticker-filtered orders and fills both200 with empty lists/cursor
for CER FIN. Absence does not prove rejection, so did not clear intent or retry.
No review worker active; local ledger unchanged by investigation.

Likely stale HTTP keepalive after roughly12-minute quiet interval between
submissions. Added pre-send idle socket discard after15s inactivity on the
specific transport thread. Fresh connection created before POST; no retry added.
Regression test verifies discard precedes exactly one send. All12 pilot tests
pass. This fixes an avoidable transport failure mode but cannot retroactively
establish the uncertain order outcome. Reconciliation and conservative pause
remain in force, pending terminal exchange evidence. No cap/strategy change.

## 2026-09-08 00:35 UTC — failed review recovery and test isolation repair

Automatically handled queued22:00/00:00 review failures and repeated alerts.
Both failed with Codex usage exhaustion, reset text00:07 UTC. 22:00 consumed
84,086 reported tokens and stopped mid-repair after /tmp/repair2200.py failed
ModuleNotFoundError paper_support; 00:00 failed before producing a report.
Do not describe those reviews as successful. Their logs remain intact.

22:00 reviewer left STOP on live pilot and uncommitted test_pilot.py isolation
change. More serious: combined test import order cached iolib.LIVE pointing to
production before test_winner_taker selected temporary storage. Five synthetic
rows from PID93034 were appended and the broad short account checkpoint was
overwritten with fake tickerT/count100. Running PID23503 retained correct state
(takes11, realized16.620032, locked0), and live ledger was separate/unaffected.
The earlier repair script never ran past its import; no assumed repair success.

Rebuilt durable broad short account from genuine REST-verified actions since
Sep6 21:28:44, checked all11 takes have settled tickers, recomputed exact
realized16.620032 and by-day exposure, matched running health. No restart.
Preserved contaminated checkpoint and five excluded rows under
 data/reviews/20260908T003400Z-*.
Raw action journal unchanged. Explicit data/live/paper_action_exclusions.json
lists the synthetic run IDs; paper_report and analyze_paper_run honor it.
Unknown saved liquidity conservatively zeroed across known ticker cent levels
in recovered checkpoint; running process has original in-memory depth and its
next account save will supersede this conservative fallback.

Added shared _test_environment loaded BEFORE production imports in all four
test modules, rejecting a process which already cached iolib paths. Replaced
conflicting per-file env setups. Combined unittest discovery (30 tests plus
winner script's import-time checks) passes in one process; hashes of production
paper account/config and live ledger unchanged across that reproduction.
Paper report now flags checkpoint-versus-newer-heartbeat count/P&L divergence.

Reduced routine review work to new interval/errors, last100 note lines plus
PILOT.md. No repeat whole-day raw replays/rules fetches or unchanged-code test
runs without a new reason. Model/caps unchanged. Changed identical-warning
reminders30min->2h; new/changed failures still notify immediately, and each
review result/failure still delivered separately. This reduces repeated alerts
without pretending an old unresolved order has been resolved.

Live remains STOP and unresolved Cerundolo full4.96 reservation, total9.92
allocation including verified Rybakina fill. Direct signed orders read200,
empty records/cursor still; no safe evidence to classify as rejected, no POST
retry or ledger clearance. Feed healthy. Scheduled service retains failure
status until a later successful review; no reset to conceal failures.

## 2026-09-08 01:08 UTC — bounded ambiguous-order resolution authorized

User requested live/paper summaries,12-hour health reminders, and a practical
cutoff for unacknowledged submissions. Implemented ten-minute minimum followed
by three clean scans spaced >=30s and spanning>=60s. Fully paginate ticker-
filtered orders without status restriction; query historical orders/fills when
created time crosses /historical/cutoff. Check fills since submission-minus60s,
positions including nonzero fractional quantities/exposure, and settlements.
Require /exchange/user_data_timestamp within60s and after ten-minute deadline.
Any API error, malformed/missing pagination, stale watermark, matching nonterminal
order, any same-market pending order/fill/settlement, or nonzero position blocks
release and resets clean-scan evidence. This is an explicitly authorized bounded
absence inference, not proof a missing response was rejected.

Terminal matching orders still resolve from actual fill count. After clean
cutoff, mark not_found (not rejected), preserve evidence/tombstone, release
allocation and prohibit another attempt in that same market. Keep checking
released intents for late order/fill/position evidence; restore risk allocation
and halt if any appears. No POST retries or cap increase. Live remains paused
with STOP until deployment verifies current Cerundolo intent passes the rule.

Current live audit: Rybakina QUAR5YES at99c is executed and settledYES; exchange
settlement revenue500c, yes_total_cost4.95, fee_cost0.003500, net reported0.0465
(about5c; fractional fee does not independently expose balance rounding).
Cerundolo FIN still has no order/position; it will be checked by the new rule.
Broad paper currently20fills/1411contracts since Sep6 21:28:44; short realized
16.620032 and winner-YES6.60, combined23.220032 across independent500 accounts.
Open:1 Jovic FIN NO and20 Gauff QUAR YES (excluded from realized). Five attempted
fills failed REST depth verification. Confirmed-only2fills/60contracts,0.55
realized (Navarro47 and Rybakina13), flat. Pilot shadow had Rybakina34 and
Cerundolo126 filled, then Jovic1 and Gauff20; not additive to broad/live depth.

Unchanged health reminder interval now12h; new/changed warnings remain immediate
and each scheduled-review result still delivered. Tests add clean cutoff timing,
failed scan reset, stale watermark, fractional position, fills/settlements,
historical coverage, pagination failure and late-fill halt; combined suite run
recorded below. Primary paper processes need no restart for these changes.

### Bounded reconciliation deployed and live resumed

37 combined unittests plus winner script checks passed. Committed/pushed34cca92
before live restart. Actual CER FIN intent then completed three clean signed
checks with fresh watermarks:0 current orders,0 recent fills,0 market positions,
0 settlements; historical cutoffs July9 so September intent remains covered by
current endpoints. Three checks span>=60s and intent age exceeds10m. Ledger now
not_found with evidence retained. Cleared the specific ambiguity STOP after
verifying no unresolved orders/halt_reason; resumed live under unchanged25/5
cumulative caps. Only Rybakina4.96 allocation remains charged. No POST retried.
Settlement evidence for live summary saved in
 data/reviews/20260908T010800Z-live-settlement.json.
Existing paper and shadow processes kept running. First live process restart
loads the same durable ledger; changes do not erase losses or refill used budget.

## 2026-09-08 02:03 UTC — bounded scheduled review

Reviewed interval 01:08 (last completed recovery/deployment audit)–02:03 UTC.
Initial git status clean. Read PILOT.md and last100 notes first; only older
annual-specific paragraph revisited to compare cached Alcaraz terms. No restart,
strategy/cap change, manual order, test-suite replay, or scheduler change.

paper_report.py via python3 returned no warnings. Initial `python` command
failed (not installed); python3 succeeded. Broad/confirmed, score, related,
live/shadow pilot and standalone REST/WS captures active. Broad/confirmed
service starts Sep7 02:22/02:23 with zero restarts: nearly24h continuous at this
review, maintain through next review. Related start Sep7 02:58, zero restarts.
Disk33G free (32% used). Score age1.3s, discovery47.6s at initial report.
Broad WS grew33767878->33866325 bytes during review; related130929->130994,
latest age25.7s. Standalone WS82.76MB, trades1.95MB, books1.20MB freshly
written; WS count39,673,091 at01:30 ->40,520,636 at02:00. No interval errors,
sequence gaps or failed reconnects in inspected app logs; standalone REST14
and WS17 interval log rows, zero error matches. Journals for capture services
had no entries (most output goes to files); file logs checked separately.
Quiet related market intervals are consistent with healthy heartbeat/capture.
No whole-day raw replay; continuity evidence is file growth plus interval
message counts/status, not an exhaustive per-message sequence audit.

Live new Tien tournament NO5 at01:37:09: match bid absent/ask1c, cached valid
identity/round and started fifth set. Target sell-YES price2c is distinct from
strict MATCH1c trigger; no live model-edge entry. Signed fully paginated GETs
at02:01:25: executed order5, matching fill5, position-5, cost4.900000 and
fee0.006900, no settlement. IOC response average fee0.0013 is rounded: use
reconciled0.006900 total, not inferred0.0065. Reservation4.91 covers actual;
Rybakina retained4.96, total9.87/25, each match<=5. Recomputed every live and
shadow reservation with rounded fee formula successfully. Shadow308.91/500,
max match124.83/125; new Tien127 fill. No unresolved/error/halt; STOP absent.
Cerundolo tombstone retained; direct orders/fills/positions/settlements empty,
fresh exchange watermark200. No late evidence, no retry. Live remains enabled.
Signed read evidence saved20260908T020000Z-exchange.json; no POST performed.

Broad interval7 fills/545 contracts: Tien NO518 across tournament/SEMI/QUAR,
Khachanov QUAR YES27. First127 Tien were broad-only model-edge; subsequent
book inference preceded score lost confirmation01:37:24.529 by8–15s.
Confirmed3 fills/532 contracts: Tien QUAR5/tournament500 and Khachanov YES27,
after score confirmation. All10 interval paper fills REST verified with
count<=verified depth; three broad absent-price attempts correctly no_fill.
Independent accounts/counterfactual shadow are not additive liquidity.
Gauff YES20 settled +0.18; Jovic NO1 settled displayed+0.03 (exact short net
change0.027963). Broad realized16.647995+6.78=23.427995; confirmed0.55,
no new confirmed settlements. Durable account/health counts agree: broad18
short/9 YES, confirmed2 short/3 YES. Open Tien/Khachanov fills remain unsettled,
not counted as realized. Excluded synthetic run IDs honored. Observed score
outcomes support Tien loss/Khachanov win; Gea loss/Van win observed01:59:50,
no new fill for that pair. Discovery has that pair and Zverev/Darderi; no new
demonstrated missed-market/false-positive discrepancy. Full draw census and
raw consumed-depth replay not repeated on unchanged code.

Annual27 contracts remain capture-only. Cached Alcaraz KXGRANDSLAM-CALC26-2
active, unchanged updated_time July13, UUID527915ea-e368-4c7f-a203-c83ebb6f6572,
calendar2026 at least2; expectedSep15/closeSep29 14:00Z, timer300s. Prior
verified notes record AO Alcaraz/Rybakina, RG Zverev/Andreeva, Wimbledon
Sinner/Noskova (Sinner previous re-fetch failed); USO remaining. Alcaraz already
has AO, so USO can be second; active does not imply zero prior titles. Retain
prior15 UUID joins/12 unverified, generic after-issuance distinction and
potential year-end/Jan7 cash lock. No unchanged external rules/history
re-fetched, no new identity validation or promotion claimed.

Material unrelated operational issue: kalshi-control.service crash loops in
/home/ubuntu/kalshi/control_server.py:332, RuntimeError tennis-tournwinner:
--obs-every has no kind in FLAGS. Journal evidence02:01:41. External control
repo left untouched; capture services continue independently. Handoff: fix
control-panel flag schema in its own working context; monitor Tien/Khachanov
settlements and confirm24h duration next review. No concrete live execution/cap
fault found, so no safety stop. No production code changes/tests necessary;
read-only assertions verified reservation math and fresh interval fill depth.
Detailed current health/positions saved20260908T020000Z-summary.json.

### 02:04 UTC review delivery follow-up: control panel repaired

02:00 review completed successfully and confirmed5-contract Tien tournament NO
live fill; allocated9.87/25, no unresolved orders, no cap change. Investigated
independent kalshi-control.service crash loop in /home/ubuntu/kalshi repo.
JOBS tennis-tournwinner already declared obs-every/no-obs/default mode all,
while FLAGS omitted both flags and allowed only both/final/dead. Verified
actual tourn_taker argparse supports numeric obs-every, boolean no-obs and
both/final/dead/dom/all. Aligned FLAGS only. Import-time registry validation
and defaults assertions passed, no trading job launched. Committed/pushed
49a2249 to bkestelman/kalshi; unrelated untracked paper_holds file untouched.
Restarted only kalshi-control: active/running, NRestarts0, HTTP200 localhost8080.
Pilot/paper services unchanged. No exposure increase or broader live activation.

## 2026-09-08 04:02 UTC — bounded scheduled review

Completed interval 02:03–04:02 UTC, including 02:04 control repair handoff.
Initial and pre-write git status clean. Read PILOT.md and last100 RUN_NOTES
lines first. No older notes needed, no production edits/restarts, no test orders,
no scheduler changes. Saved 20260908T040000Z-summary.json and -exchange.json.

paper_report.py succeeded with no warnings. Broad/confirmed active since Sep7
02:22:31/02:23:12, zero restarts: >25h continuous experiment achieved. Score,
related, live/shadow pilots, standalone REST/WS and control active, zero
restarts. Control warning journal failures end02:04:41 before repaired process
start02:04:44; no later warning entries. Disk33G free,32% used. Score age1.1s,
discovery39.5s. Broad capture36,008,697 bytes, messages5,083,878; interval logs
show increasing messages through03:50 and MATCH OVER03:52:20, then zero
watched matches/legs. Idle capture age464s is expected, not feed failure.
Related capture254296->254550 bytes during review,27 markets/feed ready.
Standalone WS133,961,454/orderbook2,079,715/trades2,612,687 bytes, ages4/6/3s;
WS count40,520,636 at02:00 (prior review) ->43,741,836 at04:00. Two normal
ticker-set reconnects02:10:40/02:35:42, no failed reconnect/sequence/API errors
in interval file logs. Inspected65 broad/64 confirmed/3538 score/3 each pilot/
26 REST/30 WS interval log rows, plus warning journals. Continuity verified
by capture growth, fresh files and message counts; no whole-archive replay or
exhaustive sequence audit. Standalone still labels Zverev/Darderi in play after
primary completion (different discovery semantics), but continues recording;
no demonstrated lost data or execution dependency on that label.

No new broad, confirmed, live or shadow fills. Broad4 settlements: Khachanov
QUAR YES27 +0.25; Tien tournament NO271 +4.34, SEMI NO114 +10.15, QUAR NO133
+2.48 (display-rounded). Exact durable totals33.619006 short +7.03 YES =
40.649006, interval gain17.221011. Confirmed3 settlements: Khachanov YES27
+0.25, Tien QUAR NO5 +0.09 and tournament NO500 +4.65; exact total5.546640,
interval gain4.996640. All broad/confirmed positions now empty; health and
durable take counts/realized totals agree. Existing fee/depth-verified fills
from preceding review now settled consistently with Tien loss/Khachanov win;
no new fill requires depth replay. Synthetic run exclusions honored.
Both observe Zverev3–0/Darderi0–3 at03:47:51; no new takes. Darderi one-cent
signals have target no-bid/zero depth (confirmed initially awaits score).
Gea target likewise no-bid. No demonstrated missed executable market or
false-positive fill; complete draw census not repeated.

Fresh signed fully paginated GET orders/fills/positions/settlements for all
three live intent markets succeeded; historical cutoff and fresh <60s account
watermark verified, no historical fetch needed for these September intents.
Tien5 NO settled02:45:45.489855: revenue5.00, cost4.90, actual fee0.006900,
net0.093100. Rybakina5 YES settlement revenue5/cost4.95/fee0.003500 remains
consistent (net0.046500). Both exchange positions empty. Actual combined
settled net0.139600 is supported by exchange, not paper fee estimates.
Cerundolo not_found tombstone retained, orders/fills/positions/settlements
all empty; fresh automatic absence recheck and no late evidence. All live
reservations independently recomputed using rounded fee formula; retained
allocation9.87/25, match4.96 and4.91 <=5 despite settlement. Shadow reservations
also recomputed308.91/500, max match124.83/125. No unresolved, halt_reason,
STOP or health error. No safety stop warranted. Live strategy/caps unchanged.
Read-only assertions and GETs ran in a fresh Python process; no Ledger
instantiation/save or production-state mutation. No code change warrants
rerunning unchanged suites.

Annual27 remain capture-only. Inspected cached Alcaraz KXGRANDSLAM-CALC26-2:
active, unchanged July13 update, UUID527915ea-e368-4c7f-a203-c83ebb6f6572,
explicit calendar2026 >=2 majors, expectedSep15/closeSep29 14:00Z, timer300s.
Prior review records Alcaraz already won AO; USO can supply second title.
Prior recorded AO/RG/Wimbledon winners and identity/rule caveats retained;
no unchanged external rules/history refetched or new verification claimed.
12 previously unverified identities and Sinner source re-fetch remain prior
limitations; no annual promotion. Generic after-issuance rules and possible
Jan7 cash lock must still be resolved before promotion.

Handoff: keep all experiments running and live cumulative9.87 allocation
charged; watch next matches and tombstone late-evidence monitor. No concrete
execution/cap fault or new auth failure. Report/notes committed and push
attempt follows; no production code changes.

## 2026-09-08 06:02 UTC — bounded scheduled review

Reviewed only 04:02–06:02 UTC after reading PILOT.md and last100 notes lines; initial/pre-write git status clean. No older notes, raw archive replay, production edits, restarts, tests of unchanged code, orders or scheduler changes. Python command unavailable; reran successfully with python3. Fresh isolated read-only Python assertions and signed GETs passed; no Ledger instantiated or saved.

paper_report.py returned no warnings. Broad/confirmed remain active since Sep7 02:22:31/02:23:12 (>27h continuous), all nine paper/score/related/pilot/REST/WS/control services active with NRestarts0. Warning journals since04:02 empty. Disk32% used/33G available. At06:00:24 score age1.4s/discovery31.5s. Broad/confirmed60 interval log rows each, score3581, standalone REST25/WS26; no sequence/API/error failures. One expected WS ticker-change reconnect04:21:08. Primary8 open/0 started, both paper feeds0 matches/legs; no new completion, actions, fills or settlements in any of four action ledgers (timestamp field verified). Synthetic exclusions retained and honored; no excluded rows in interval. Broad durable realized40.649006, confirmed5.546640; both flat and health agrees. No new fill requiring fee/depth replay or evidence of missed executable markets/false positives; complete draw census not repeated.

Actual capture continuity: related392326 bytes at06:00 versus254550 prior, 12153 messages versus10851, 27 markets/feed ready. Primary unchanged36008697 bytes/5083878 messages is expected idle. Standalone WS133967121 versus133961454 bytes, count43742023 versus43741836; now0 in play after ticker-change reconnect, idle capture age6014s expected. REST orderbook2703124 bytes age3.9s/trades2682422 age34.8s, both larger than prior. REST labels5 scheduled matches live (discovery terminology), WS correctly0 in play. No demonstrated continuity fault; no exhaustive raw sequence replay.

Live/shadow ledgers and health checked: no unresolved/halt/error/STOP, live9.87/25 max match4.96/5; shadow308.91/500 max match124.83/125. Independently recomputed every reservation including rounded fees and retained filled allocation. Signed fully paginated orders/fills/positions/settlements for all3 live markets succeeded; cutoff precedes intents, no historical fetch needed, account watermark <60s. Market evidence exactly equals04:00 saved reconciliation: both filled orders settled with empty positions; actual combined net0.139600 unchanged. Cerundolo not_found tombstone retained, fresh service absence recheck, all4 exchange lists empty. No execution/cap fault, safety stop unwarranted. No auth failure.

Annual27 remain capture-only. Cached Alcaraz KXGRANDSLAM-CALC26-2 inspected: unchanged July13 rules/calendar2026 >=2 majors, UUID527915ea-e368-4c7f-a203-c83ebb6f6572, active, expectedSep15/closeSep29 14:00Z, timer300s. Carry forward prior verified AO title: USO can be second; active status does not imply zero titles. Prior AO/RG/Wimbledon history retained without unchanged external refetch. No new identity/history verification claimed or promotion made. Prior12 unverified identities, Sinner source refetch, generic after-issuance interpretation and possible Jan7 settlement lock remain promotion blockers.

Evidence saved data/reviews/20260908T060000Z-summary.json and -exchange.json. Handoff: keep experiments running, retain cumulative allocation and tombstone monitoring; check next match activity on next timer. No new paper result or code fix. Notes/report commit and push follows.
