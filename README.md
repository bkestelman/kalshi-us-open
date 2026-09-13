# kalshi-us-open

A user-authorized **live pilot** now runs alongside the paper experiments. See
[PILOT.md](PILOT.md) for shared live/shadow strategy, locally configured
outstanding-capital limits, settlement recycling, and the ambiguity cutoff. Minute
health checks continue; unchanged alert reminders are limited to once per12h.

Current supervised paper run: [RUN_NOTES.md](RUN_NOTES.md). As of September 6,
the simulator models order delay and consumed depth, checkpoints paper accounts,
and uses an independent score collector. A second, **paper-only** account buys
the winner's newly secured qualification (e.g. Noskova QUAR YES), alongside the
original loser shorts. Each research account has its own budget; their results
must not be combined as though they share one capital cap.

Run `python3 paper_report.py` for current health, order-attributed settled profit,
fees, remaining allocation, and budget-exhaustion warnings. Live uses exchange
fills/fees; shadow results use estimated fees and remain separate.

Each pilot reads `data/pilot_live/pilot_config.json` or
`data/pilot_paper/pilot_config.json` at startup. These local, gitignored files
contain `total_cap`, `per_match_cap`, and boolean `recycle_on_settlement`.
There are no implicit production limits: missing/invalid configuration prevents
startup. Keep authorized current values there, not in this README. Restart after
an authorized config change. Settlements release reservations without deleting
order history or increasing caps; unresolved orders retain their allocation.
`tennis-scores.service` collects score changes; `paper-monitor.timer` records
health once a minute. `winner-taker-confirmed-paper.service` runs the
confirmation-only comparison in `data/confirmed/`, sharing the primary's
discovery mappings and score cache. The primary's full book capture is in
`data/live/winner_taker_ws_YYYYMMDD.jsonl.gz`. Run both `python3 test_winner_taker.py` and
`python3 test_paper_support.py` for offline verification. Historical strategy
notes below include superseded assumptions; the dated run notes take precedence.

`winner_taker` — sells the tournament-winner leg and the round-qualifier legs
("will X qualify for the Quarterfinals / Semifinals / Final") of players whose
current match is nearly lost, when the resting bid is still above what the
match book implies.

Spec: [WINNER_TAKER.md](WINNER_TAKER.md). Measurements: [FINDINGS.md](FINDINGS.md).
What is left before live: [READINESS.md](READINESS.md).
Prior work lives in `../kalshi-tennis` (capture, maker, settled-P&L study) and
`../kalshi` (takers).

## Layout

| file | what it is |
|---|---|
| `winner_taker.py` | the bot. `paper` or `live` |
| `discovery.py` | live matches paired to that player's winner and ADVANCE legs |
| `kalshi.py` | signing, kept-alive REST, pagination, `Book`, fee |
| `iolib.py` | paths, capture IO (copied from `../kalshi-tennis`) |
| `alloc_extract.py` | phase 1 of the allocation study: capture → candidate stream |
| `alloc_study.py` | phase 2: capital-constrained allocator, sweeps |
| `build_tourn_map.py` | offline match→winner-event map, for replaying capture |
| `test_winner_taker.py` | 84 offline tests: gates, caps, ranking, reservations |
| `requirements.txt` | pinned to the VPS's versions |
| `vps/deploy.sh` | ship tracked files + keys, install units |

## Setup

The VPS runs **Python 3.14.4**, and this bot is deployed there, so develop
against the same interpreter — the alternative is finding out about a language
change from a live trading process, which is exactly how `asyncio.get_event_loop()`
was caught (fine on the laptop's 3.8, a `RuntimeError` on 3.14).

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh     # once
uv venv --python 3.14.4 .venv
uv pip install --python .venv/bin/python -r requirements.txt
.venv/bin/python test_winner_taker.py               # 50 tests
```

`uv` fetches a prebuilt CPython, so this needs no sudo and no compiler. The
code still runs on 3.8 and the tests pass there, but **3.14 is the version that
counts**.

## Running it

```sh
.venv/bin/python winner_taker.py paper          # decides and logs, places nothing
.venv/bin/python winner_taker.py live           # places real IoC sells
.venv/bin/python winner_taker.py live --cap 400 # ...and write $400 into the config
```

Paper makes no authenticated call at all, so it runs on a box with no trading
key — `vps/deploy.sh` ships only the read-only key unless you pass `--live`.
Live needs `trade_api_id.txt` and `trade_api_key.rsa` on whichever box runs it.

Everything writes to `data/live/`:

| file | what |
|---|---|
| `winner_taker.json` | **the caps.** Edit while running; picked up within a second |
| `winner_taker.log` | human log: takes, marks, settlements, config changes |
| `winner_taker_actions_YYYYMMDD.jsonl` | machine log, one line per decision |
| `winner_taker_obs_YYYYMMDD.csv` | every watched leg on a cadence, **including declines** |
| `winner_taker_state.json` | R samples, checkpointed so a restart is not blinded |

The observation log is the one to read first. The action log only records what
cleared the gates, which makes a quiet day unreadable — "no bid at all" and "a
bid a tick below fair" are completely different findings about whether this
trade still exists on a slam-sized book.

## Changing how much it commits

Edit `data/live/winner_taker.json`. No restart, and every change is logged with
its before/after.

```json
{
  "hard_cap": 150.0,      // total collateral locked at once, dollars
  "per_leg_cap": null,    // null = hard_cap/4 — one bet
  "per_event_cap": null,  // null = hard_cap/2 — one draw
  "per_day_cap": null,    // null = hard_cap  — off
  "elim_cap": null,       // null = hard_cap  — eliminated takes only
  "cash_reserve": 0.0,    // never spend the account below this
  "min_edge": 0.01,       // required net premium over fair, per contract
  "enabled": true         // false pauses ENTRIES; R keeps being collected
}
```

`per_leg_cap` binds on the **player**, not the market: a player's winner leg
and their three qualifier legs all turn on the same match, so one comeback
takes all four. `per_event_cap` binds on the **tournament**, which is spread
over four Kalshi events.

Those three caps price **one comeback**, so they do not apply once the player
is out — see [WINNER_TAKER.md](WINNER_TAKER.md#once-the-player-is-out).
Eliminated takes bind on `elim_cap` and `hard_cap` instead, which are capital
rather than risk, and their collateral is netted out of the risk caps so it
cannot re-impose them on the next player. `elim_cap` is the knob that says how
much to put behind "this player is out" being *inferred from a book shape* —
an inference measured wrong once in 45 over 2026-09-04..06.

Collateral is `(1 - price)` per contract and stays locked until the player is
**eliminated**, which is minutes after their match — not until the tournament
ends. Measured 2026-09-04 from `close_time` on the exchange:

| player | match closed | winner leg closed | held |
|---|---|---|---|
| Muchova | 21:00:25Z | 21:09:36Z | **9m 11s** |
| Paolini | 16:30:17Z | 17:00:01Z | **29m 44s** |

The legs' `expiration_time` says 2026-09-27, and that is the nominal
placeholder, not the settlement — the same trap `discovery.py` documents for
`expected_expiration_time` on match markets. This mattered because the study
priced that collateral as locked for days, and set a ROC floor accordingly. `hard_cap` is a cap on what is locked *at once*; it frees as
players go out and is spent again, many times a day.

Two things worth knowing before turning a dial:

- **There is no `min_roc` any more, and no `min_price`.** Both were removed on
  2026-09-05. `min_roc` 0.03 was a knee measured in %/dollar-**day** against a
  study that believed collateral sat locked until the tournament settled; the
  table above says the hold is ~20 minutes, so it was pricing a risk the
  exchange does not charge. Between them the two gates declined Muchova at 2c
  on 2026-09-04 — the one US Open leg in 23 whose book had a bid at all.
  `min_edge` is what remains, and it is the right one: it compares the bid to
  fair, so it scales with the round being sold rather than with a holding
  period.
- **`enabled: false` does not stop R.** The W/M samples that define fair value
  exist only before the match starts, so a bot that stopped sampling while
  paused would have to skip every match it sat out.
- **R is frozen at the first ball, and stored counted.** A frozen book quoted
  the same ratio thousands of times and the checkpoint stored every copy --
  2.04 MB for fifteen legs, against 4 KB once repeats became a counter. The
  median is time-weighted, so a ratio quoted for an hour still counts for the
  hour and the number `fair` returns is unchanged.

## Restarts

Restarting mid-tournament is the normal case — positions live for days.

- Committed collateral is rebuilt from **exchange fills** (client order ids
  prefixed `wtk-`), not from our own log, so a restart cannot double-commit. If
  that read fails, the bot refuses to trade rather than guess.
- R samples are checkpointed every 30s and reloaded if they are under 12 hours
  old and from the same match. A match with no samples is **skipped**, never
  traded off a borrowed R.

## Reproducing the allocation study

Needs capture. Point `KALSHI_DATA` at it or symlink the days into `data/live/`:

```sh
ls data/live/ws_*.jsonl.gz | sed 's|.*/||;s|\.gz$||' \
    | xargs -P 8 -n 1 .venv/bin/python alloc_extract.py   # ~15 min on 15 days
.venv/bin/python alloc_study.py --cap 150
```

Phase 1 is the expensive pass and shards by day; phase 2 is seconds and is the
one to re-run when sweeping.
