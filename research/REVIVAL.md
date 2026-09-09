# Match-book revival: counterfactual recovery study

Research scope: September 5 Zheng/Keys and Anisimova/Potapova; September 7–8
book-inferred broad-paper entries and all five filled live-pilot orders, including
Tiafoe/Michelsen. This study does not submit orders or change live strategy/caps.

## Results — September 9, 2026

The 10-cent / 5-second rule is a **paper candidate**, not a proven live safeguard.
It caught Zheng's comeback and left all covered winning entries untouched. Low
thresholds did not: real winning trades had substantial opposing-bid revivals.

The recent cohort contains 18 book-inferred broad-paper fills and all 5 live fills,
covering **9 distinct matches**. Every inference was ultimately correct by mapped
final score. Pegula's earlier entry predates the capture and is excluded. Navarro
is included using the NO side of her own match book even though the opponent's
separate market was not captured. Live/paper duplicates and multiple fills in a
match are not independent successes.

| Opposing bid sustained 5 seconds | Winning matches that would trigger | Zheng delay after first false signal | Cheapest match hedge at +100 ms |
|---|---:|---:|---:|
| 1 cent | 4 | 129.561 seconds | 2 cents |
| 2 cents | 3 | 324.683 seconds | 3 cents |
| 3 cents | 2 | 445.167 seconds | 4 cents |
| 5 cents | 1 | 474.122 seconds | 6 cents |
| 10 cents | 0 | 902.201 seconds | 14 cents |
| 15 cents | 0 | 927.880 seconds | 16 cents |

Five-cent revival still catches the profitable Swiatek/Zheng September 7 trades:
Swiatek's recovery bid reached 8 cents before she lost. One-/two-cent rules also
catch Andreeva and Rybakina; one cent additionally catches Navarro. A 1–2-cent
rule plus a ten-second delay does not solve those false alarms. Anisimova's and
Keys's September 5 true losing signals had no positive-bid revival in the replay.

**Zheng, September 5:** three false-signal clusters, with the first split by a
feed reset (four validated one-cent segments). No actual related fill from this
strategy occurred during them. The earlier no-liquidity finding concerns resting
YES bids to short Zheng; it is not an exhaustive claim about every possible Keys
qualification entry. Recovery numbers here assume a hypothetical **100 tournament
NO contracts bought at 99 cents**, costing $99.07 with conservative entry fees.

* At 1 cent sustained 5 seconds (17:06:45.597 UTC), selling those NOs into recorded
  99-cent NO bids returns $98.93 after estimated exit fees: **14 cents total loss**.
  Alternatively 100 match YES at 2 cents costs $2.14 with fees, bounding the combined
  normal-settlement loss at **$1.21**. Both books support 100 at +100 and +500 ms.
* At 10 cents sustained 5 seconds (17:19:38.237 UTC), the same 99-cent direct exit
  remains available: again **14 cents hypothetical loss**. The match YES hedge
  instead costs $14.85 including fees, bounding loss at **$13.92**; the opponent's
  match NO is slightly dearer. A 3-cent hedge limit would fill nothing here.
* The 10-cent rule also catches the later false-signal segments. This is one
  comeback match, not multiple independent confirmations of the threshold.

**Rybakina, September 7:** our actual five-contract 99-cent qualification YES
ultimately earned approximately $0.05. At a 1-cent / 5-second revival, a direct
market sweep could sell only at **1 cent per contract**: roughly a $4.91 loss.
A match hedge costs approximately $0.11 instead, changing the small winner to a
small loser. At the 2-cent / 5-second trigger the hedge costs approximately $0.17.
Her final-score confirmation arrives about 455 seconds after entry, too late to
veto these early recovery signals. Never infer a liquid exit from a liquid entry.

**Tiafoe/Michelsen, September 8:** the minimum valid two-sided midpoint for Tiafoe
was **2.5%**. His book never became no-YES-bid / at-most-1-cent ask, so the strict
entry strategy would not have entered against him. His actual winning entry is
untouched by the sustained 10-cent recovery rule.

The 10-cent boundary was selected after seeing these outcomes. Nine winning
matches and one comeback cannot establish an out-of-sample error rate. A price-
bounded direct exit is preferable where it cheaply removes the risk; a hedge is
an alternative when it offers better protection per dollar. Neither a resting
limit nor an unbounded market order is an assured, cheap escape. New observation
must test this policy before enabling live recovery.

## Method

`research_revival.py` reconstructs books only after valid snapshots. It checks
sequence numbers even for unselected markets, invalidates books on gaps or
connection changes, and preserves full positive depth around the entry/revival pricing windows
in a reusable compressed tape. Outside those windows the September 7–8 tape
keeps match top quotes for trigger and Tiafoe-low analysis; related depth is not
priced there. The metadata sidecar explicitly bounds executable-depth queries. `analyze_revival.py` tests returned losing-player bids at 1, 2, 3, 5, 10 and 15 cents,
with 0, 1, 3, 5, 10 and 30 seconds of uninterrupted persistence. For qualification YES positions it watches the NO side of the exact match
market that triggered entry, not a substituted opponent ticker. The separate
opponent market can disagree and is only an alternative hedge venue. An empty
book is not a revival. A gap breaks persistence rather than proving a comeback or a finish.

For actual entries, monitoring runs until a favorable final score arrives or
one hour after the fill, whichever comes first. Final-score receipt vetoes a
recovery action without delaying entry. The entire fill-to-confirmation interval
is covered for all 23 included entries; later gaps/removal are not treated as
healthy observed time. Zheng false episodes have a one-hour maximum lookahead;
the 10-cent trigger fell just beyond 15 minutes, so a 15-minute cutoff would
incorrectly exclude it. Coverage seconds and excluded entries are reported.
Repeated fills in one match are correlated observations, not independent trials.
Synthetic test run IDs in `paper_action_exclusions.json` are excluded.

At trigger +100 ms, the analysis prices:

* Sell the held related-market YES into YES bids, or sell the held NO into NO bids.
* Buy the recovering player's match YES using its NO-bid ladder.
* Alternatively buy the opponent's match NO using that market's YES-bid ladder.

Each route is separate: these are alternatives, not additive hypothetical profits.
Hedges are also checked at a maximum 3-cent purchase price. Full sweeps expose
slippage; insufficient depth remains partial and books invalidated by gaps remain
unknown. Zheng also has a +500 ms sensitivity check. These are displayed-depth
counterfactuals, not exchange-confirmed fills: competition, cancels, network delay
and our own market impact can change execution. Passive limit orders are not
credited with fills because queue position cannot be established by book depth.

Fee estimates use the pilot's verified standard quadratic multiplier, summed over
executed levels and conservatively rounded up to cents per order. Actual exchange
fees can differ through fractional fees/rounding rebates. See [Kalshi fee
rounding](https://docs.kalshi.com/getting_started/fee_rounding) and [IOC/limit order
semantics](https://docs.kalshi.com/api-reference/orders/create-order-v2).

## Payoff and implementation constraints

For a player's future-round/tournament NO, buying the same player's current-match
YES creates a minimum combined payout of $1 per matched contract under normal,
consistent settlement: if they lose this match, the future NO pays; if they win,
the match YES pays. Both can eventually pay. For a qualification YES secured by
this match, the losing opponent's match YES supplies the opposite outcome. This
reasoning requires correct round/identity/rules; unrelated annual markets are not
included, and void/exceptional settlement must be checked before deployment.

A hedge limits loss but costs premium plus fees when the original inference was
right. It cannot promise zero sacrifice on unseen true positives. The one-sided
book remains probabilistic. An unbounded market sweep can also buy a hedge much
more expensively than the bid that triggered it.

A prospective implementation should use a price-bounded IOC, handle partial fills,
and reconcile every exit/hedge intent before another action. It must stop new
entries for the affected match on revival and prevent repeated hedging on flicker.
The present live budget is cumulative $250 total/$50 per match. Recovery costs
would need budget reserved at entry or demonstrably available within those caps;
existing positions cannot assume an unlimited hedge allowance. No new live exit
or hedge path is enabled by this research.

## Reproduction

```bash
python3 research_revival.py /home/ubuntu/kalshi-tennis/data/live/ws_20260905.jsonl.gz --target KEYZHE --target POTANI --target USO-ZHE --target USOQUAR-ZHE --target USOSEMI-ZHE --target USOFIN-ZHE --depth-window 1788626800:1788631300 --output data/research/revival_sep05.jsonl.gz
python3 research_revival.py data/live/winner_taker_ws_20260907.jsonl.gz data/live/winner_taker_ws_20260908.jsonl.gz --target KXATPMATCH --target KXWTAMATCH --target KXATP- --target KXWTA- --target KXATPADVANCE --target KXWTAADVANCE --quotes-outside --depth-window 1788749500:1788750600 --depth-window 1788802300:1788803700 --depth-window 1788815580:1788817350 --depth-window 1788828440:1788829500 --depth-window 1788831370:1788832400 --depth-window 1788890570:1788891600 --depth-window 1788908720:1788909800 --output data/research/revival_sep0708.jsonl.gz
python3 analyze_revival.py
python3 -m unittest test_revival
```

Compressed tapes and detailed JSON remain on the VPS under `data/research/`.
Use the saved tapes for parameter comparisons rather than repeatedly scanning
large raw captures. This dated study intentionally uses September 7–8 paper
journals; reusing the code for later studies requires updating that input selection.

## Forward paper recovery implementation

`recovery_policy.py` is transport-independent; `recovery_paper.py` follows new
filled orders from `pilot_live` and `pilot_paper` in a separate process. It never
submits exchange orders or writes to either source ledger. The systemd unit also
hides trading/Git keys and mounts source-data directories read-only. Start time is
durable: this is a forward comparison, not a fabricated replay of old fills.
The broader model-edge paper strategy is not copied into this forward branch.

The rule watches the **exact entry match book**, uses a monotonic five-second
10-cent threshold, resets on invalid feed/new book objects, rejects crossed quotes,
and vetoes recovery once a favorable mapped final score is available. An adverse
confirmed result triggers evaluation immediately. A recovery trigger also marks
subsequent entries in that source/match as hypothetically blocked.

Candidate routes are a direct sale of the held related outcome, a hedge in the
entry match market, and optionally the opposite player's match market. Direct
sales have a floor **two cents below entry cost per contract**; hedges have a
**20-cent maximum purchase price**. These are explicit research limits, not live
orders or optimized parameters. Prefer maximum covered quantity, then the highest
minimum combined proceeds after fees; no unbounded sweep or assumed passive fill.
Fractional contract depth is handled to hundredths.

Each intent is saved before the simulated 100-ms latency. Its original route,
quantity and limit remain fixed. Paper fills require an active unsettled market,
current WebSocket depth AND a subsequent REST depth check, the same valid match
book generation, and remaining budget. Both sides of each order book have durable
consumption accounting; reconnect snapshots cannot refill spent capacity.
Live/shadow comparisons have independent capacity, never additive claimed fills.
Partial/no fills get at most three intents with five-second retry spacing and
consumption checks; missing bounded depth keeps an explicit unprotected state.
A restart with an unfinished intent marks it interrupted and does not resubmit.

The live-following branch respects the existing **$250 total/$50 match** caps;
the shadow-following branch uses its existing $500/$125 caps. Source reservations
(including unresolved orders) remain charged, plus hypothetical exit fees and
hedge spend. Settlements do not recycle allocation. Later baseline entries that
would breach the recovery branch's budget are recorded as hypothetically blocked.
**A fully spent match cap can prevent a full hedge.** A future live design must
reserve recovery capacity at entry or accept incomplete protection; doing so can
reduce initial size on cap-constrained true winners. The paper branch exposes this
tradeoff rather than silently increasing caps.

Settled comparisons use actual market results for the held outcome and every
hedge. Fee estimates are conservative rounded paper fees, so absolute baseline
paper P&L may differ slightly from exchange-reported live P&L. The relevant measure
is recovery-versus-hold difference within each independent source branch.

Files: `data/recovery_paper/recovery_state.json`, `recovery_health.json`, and action
journals. `paper_report.py` reports heartbeat, errors and unprotected/interrupted
cases; existing minute monitoring and twelve-hour reviews cover the new branch.
No live recovery has been enabled. The historical 10-cent separation remains a
small, retrospectively selected sample and needs prospective evidence.

Fee preflight note: live API metadata reports KXATPMATCH/KXWTAMATCH as
`quadratic_with_maker_fees`, multiplier1. Kalshi's [official OpenAPI
schema](https://docs.kalshi.com/openapi.yaml) assigns that category the same general
**taker** table as `quadratic`; maker fees are additional for resting orders. The
recovery path only models IOC taker fills, so its fee numbers are unchanged.
Startup validation accepts those two known categories at multiplier1 and rejects
unknown schedules/rates. The initial overly narrow startup check stopped the new
paper service before any hypothetical fills; it was corrected and regression-tested.
