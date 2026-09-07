# winner_taker — readiness

**Current status, 2026-09-07: paper only; not ready for live trading.**
The dated [RUN_NOTES.md](RUN_NOTES.md) supersede the historical checklist below.
The first roughly five hours of the revised run produced six REST-verified
paper fills (720 contracts) across three matches, with $5.53 in the short
account and $5.62 in the separate winner-qualification account. All entries
were book-inferred; two were well before the last point. This is evidence of
tradable candidates and working partial/no-fill simulation, not zero risk.

Current checks still required:

- At least a full day of the revised paper feed and outcome review.
- Compare fast inferred entries with the new confirmation-only paper service.
  Final `ended` scores now confirm outcomes without the previous extra
  two-minute wait for `closed`.
- Give inferred shorts explicit probability/edge justification and risk caps
  before live use. The legacy fair=0 and comeback-cap exemption are unproven.
- Reconcile account fees, live order responses and ambiguous order submission
  outcomes; neither paper fills nor a positive small sample establish these.
- Verify coverage and execution using the new full related-market book capture.

Both paper accounts persist across restarts. The score collector and minute
health monitor run independently on the VPS. The confirmation-only variant
shares discovered match/market mappings and maintains its own books/accounts.

---

Status as of 2026-09-04. Paper is running on `kalshi-vps`; nothing has traded
with real money.

The one-line summary: **the code is ready, and the trade is not in the market
we are watching.** The rule fires and paper-settled 9 for 9 at a tour stop.
Discovery is now fixed and the US Open has been watched properly for a full
day — and on that honest sample the tournament-winner book kept a bid on
**1 of 23** dying slam legs. The strategy is not disproven; it is pointed at
the wrong book. The liquidity at a slam is in the ADVANCE (round-qualifier)
markets, which this bot does not watch at all. See the two new P0 items.

---

## P0 — before any live capital

- [x] **Confirm the rule fires at all.** It does, but only at tour level: 9
      takes, 351 contracts, all in ATP Winston-Salem 2026-08-26..28, all nine
      settled NO for **+$16.38**. Zero takes at the US Open.
- [x] **Re-answer the US Open question on data that is not broken.**
      **Answered 2026-09-04, and the old conclusion survives.** With discovery
      fixed the day watched 41 matches / 65 legs / 55 players (against 13/15/15
      the day before), 26,901 observation rows against 1,418. Across every obs
      day, legs that reached `m < 0.10`, and whether the winner book showed
      *any* bid there:

          US Open   ATP:  0/13 legs
          US Open   WTA:  1/10 legs      <- Muchova, 2c x 227
          tour stop ATP:  9/ 9 legs
          tour stop WTA:  2/10 legs

      So **1 of 23 slam legs kept a bid**, on honest coverage — the n=5
      finding was right after all. The dominant split is slam vs tour stop,
      NOT ATP vs WTA: the 9-for-9 takes were tour-stop ATP, which is 9/9 here.
      The winner book is where this trade does not exist at a slam.

      The one exception is worth reading: Muchova, 2026-09-04T20:52:17Z, bid
      2c x 227 against a fair of 0.66c, edge +1.21c. It was declined by
      `min_price`, which has since been removed — but at ROC 1.23% it would
      still decline on `min_roc` 0.03.

      *For the record, what the broken sample looked like before the fix.* The
      first attempt was measured on data discovery had quietly gutted, and at
      the time the conclusion drawn from it was judged unsafe. It was in fact
      correct; it just could not be believed on that sample:

      * only **60** of the ~250 US Open singles matches were ever watched, none
        at all on Aug 28-29;
      * **12** legs in the whole tournament reached the `dying` gate, and 7 of
        those had no R (see the next item), leaving **5** legs -- de Minaur,
        Fils, Krejcikova, Nakashima, Djokovic -- as the entire evidence base.
        All five did show `no-bid`, and n=5 is not a verdict on anything;
      * Madison Keys was never watched once, and Felix Auger-Aliassime was
        watched only in the match he WON.

      Both causes were fixed in 8e9a513/4af82d7 -- see the header of
      `discovery.py`. Read `winner_taker_obs_*.csv`, not the action log: the
      declines are the answer, and `no-bid` vs `below-edge` are completely
      different findings. Sanity-check coverage FIRST
      (`grep -c "IN PLAY" winner_taker.log` against the day's actual order of
      play) before believing any conclusion drawn from the declines. Since the
      schedule landed, `IN PLAY` means the exchange says a ball was struck and
      `WATCHING` means only that a match is due, so the two lines answer
      different questions: coverage is `WATCHING`, reality is `IN PLAY`.
- [ ] **Decide whether R should sample a one-sided winner book.** Of the 40 US
      Open legs that were watched, **12 never took a single R sample**, so they
      were untradeable no matter what their book did later. `sample_r` needs
      `wb.mid()`, and `kalshi.py:224` returns None unless the book has BOTH a
      bid and an ask; a deep longshot's winner book is routinely ask-only. The
      correlation is exact -- every zero-R leg had zero bids while competitive,
      every leg with a bid accrued R:

          Tsitsipas   250 competitive rows,    0 with a bid,  rsamp 0
          Vacherot    307 competitive rows,    0 with a bid,  rsamp 0
          Navarro   1,406 competitive rows,    0 with a bid,  rsamp 0
          Djokovic    248 competitive rows,  248 with a bid,  rsamp 14,876

      This is deliberately NOT fixed here. Every repair changes what R MEANS --
      using the ask overstates W, overstates fair and understates edge, so it
      would likely convert `no-R` into `below-edge` rather than into a trade --
      and R is the pricing model alloc_study validated. Settle it from the
      capture, not from judgement: the obs log already records `wask` on every
      one-sided row, so the data to decide is accruing now.
- [x] **Watch the ADVANCE markets — this is now the main question.**
      **Done 2026-09-05, untested against a live collapse.** `discovery`
      pairs both `KX{tour}` and `KX{tour}ADVANCE` on the same competition +
      name join, so a player carries up to four sellable legs. Three things
      came out of building it that are worth knowing before it trades:

      * **The caps had to change meaning.** A player's four legs all turn on
        the same match, so `per_leg_cap` now binds on the PLAYER and
        `per_event_cap` on the TOURNAMENT (which spans four Kalshi events).
        Capping per ticker would have put 4x the intended money behind one
        comeback.
      * **The qualifier books are wide.** Median spread, open US Open men's
        legs, 2026-09-05: winner 1c, FIN 5c, SEMI 7c, QUAR **34c**. R is
        sampled from `mid()`, so `MAX_R_SPREAD` (0.10) now refuses to sample a
        book too wide for its mid to mean anything. Vacherot's QUAR leg quoted
        1c/97c — a mid of 49c and an R sample of 4.3 against a true ratio near
        1.0.
      * **The near round is not the cheap round.** QUAR tracks the current
        match almost exactly, so its fair value is high and its bid is not a
        gift. `min_edge` handles this without a new rule, because fair scales
        with R — but it means "the ADVANCE books have more bids" is NOT the
        same as "more trades", and the obs log is again the place to find out.

      Not yet answered: **subscription count.** A match went from 2 watched
      legs to as many as 8. The box has ~57 MB free (P1, below). Watch RSS on
      the first full day.
- [ ] ~~Watch the ADVANCE markets~~ — original note kept for the reasoning: Kalshi
      lists round-qualifier events alongside the tournament winner, and they
      carry the same shape of trade with far better liquidity at a slam:

          KXATPADVANCE-26USOFIN / -26USOSEMI / -26USOQUAR   (and WTA)

      Their `product_metadata.competition` is the same string discovery
      already pairs on ("US Open Men Singles"), so the pairing machinery needs
      no new concepts — but `discovery.sellable_legs` (then
      `winner_legs`) hardcoded `f"KX{tour}"` and so had never seen them. Measured by hand on 2026-09-04: 500
      contracts filled at 1c-equivalent on `KXWTAADVANCE-26USOFIN-PAO` at a
      moment when Paolini's *winner* book had never shown a single bid in 60
      competitive rows. The trade existed; we were looking at the wrong book.

      Caps need thought before this ships: a player's winner leg and their
      FIN/SEMI/QUAR legs are correlated, not independent, so they should
      probably share one `per_event_cap` bucket per player-tournament rather
      than getting one each.
- [x] **Re-decide `min_roc` — the holding period was wrong by ~3 orders of
      magnitude.** Removed entirely on 2026-09-05, along with `rise_to` and
      the `floor()` machinery. `min_edge` is the surviving gate.

      The measurement behind it: collateral does NOT stay locked until the
      tournament settles. A leg settles when the player is eliminated, minutes
      after their match. Measured 2026-09-04 from `close_time`:

          Muchova   match closed 21:00:25Z, winner leg closed 21:09:36Z   9m11s
          Paolini   match closed 16:30:17Z, winner leg closed 17:00:01Z  29m44s

      (The legs' `expiration_time` says 2026-09-27. That is the nominal
      placeholder, not the settlement — the same trap `discovery.py`
      documents for `expected_expiration_time`, now the FOURTH time a nominal
      Kalshi timestamp has misled this project.)

      `min_roc` 0.03 was set against dollar-day figures that assumed a
      multi-day hold. Against a 20-minute hold it was a far higher bar than
      intended, and it was what still declined Muchova at 2c (ROC 1.23%).
- [ ] **Decide `hard_cap` and `cash_reserve` for live.** Account is **$356.43
      cash / $596.67 portfolio** (2026-08-26). Default `hard_cap` is $150 with
      `cash_reserve` 0.0, i.e. the bot may spend to the last dollar and starve
      the maker and `tourn_taker`, which draw on the same balance. Pick a
      reserve deliberately.
- [ ] **Confirm the loss tail is acceptable at the chosen cap.** 0 of 425 takes
      in the 25-day study and 0 of 27 legs in the 15-day one settled YES. One
      comeback costs ~95c/contract. `per_leg_cap` at `hard_cap/4` is the
      default *despite* `hard_cap/2` measuring better, precisely because that
      tail is unobserved.
- [ ] **Nothing tells you if the bot dies.** The live unit is `Restart=no` by
      design, so a crash is silent until someone looks. Decide how you find out
      — a cron that greps `systemctl is-active`, a push, anything.

Already done, not blocking:

- [x] Order path proven against the real exchange — the MECNET probe placed
      four real IoC sells (status 201, correct fills). The order schema,
      signing and fill parsing are not first-run risks.
- [x] Committed collateral rebuilt from exchange fills at startup; refuses to
      trade if that read fails.
- [x] R checkpointed every 30s, restored only for the same match, expires at
      12h.
- [x] 84 offline tests, passing on 3.8 and on the box's 3.14.4.

---

## P1 — speed

alloc_study priced delay at **a third of the P&L per half second**, so this is
a first-class concern rather than hygiene. Where the time actually goes,
measured rather than assumed:

| stage | measured | verdict |
|---|---|---|
| VPS → Kalshi TCP connect | **2.0 ms** | us-east-1d, already colocated |
| TLS handshake | 14 ms | avoided *only* on a warm socket |
| full signed GET | 22-37 ms | the floor for an order round trip |
| `evaluate()` per book update | **2,442 µs → 9.7 µs** | fixed, see below |

### Done

- [x] **`fair()` no longer sorts the R samples on every book update.** It
      sorted an unsorted 20,000-element list per call: **2,442 µs**. At 400
      book messages a second that is ~98% of a core — the bot would have been
      CPU-bound on a shared t3.micro at exactly the moment a match collapses.
      `rsamp` is now kept sorted with `bisect.insort` at sample time (once a
      second), so the median is an index. **~250x on the hot path.**
- [x] **Orders get their own thread, kept warm.** `kalshi.signed` holds its
      HTTPS connection in thread-local storage, so sharing the 4-worker pool
      meant an order could land on a cold thread and pay the 14 ms handshake.
      Orders now use a dedicated single-thread pool, and the balance poll runs
      on that same thread every 30 s as a keepalive. A dying match is often the
      first order in an hour, and that is the one that must not handshake.
- [x] **Discovery makes no per-candidate call at all.** It used to spend one
      candlestick call per candidate per scan (~0.2 s), parallelised across 8
      threads to stay inside the 60 s interval, and still produced up to 10,204
      HTTP 429s in a day. The gate then read `volume_fp` out of the market
      list, and now reads the exchange's own schedule -- one `/milestones`
      call for every tennis match on the calendar, plus one v1 events call per
      tour for the live label. A scan is four list calls regardless of draw
      size, and the 429s stay gone. Arriving late costs the *fair value*, not
      just trades: R only exists while the match is competitive, which is why
      watching now starts `PREROLL` BEFORE the first ball rather than after
      enough volume has accumulated to prove one was struck.

### Worth doing, in order

- [ ] **Instrument end-to-end take latency.** Log `book message received →
      order sent` per take. Everything above is a component measurement; none
      of it is the number that matters, and after day 1 there will be real
      takes to measure. Do this before optimising anything further.
- [ ] **`uvloop`.** Drop-in `asyncio` replacement, typically ~2x on event-loop
      overhead. Cheap, low risk, but currently unmeasured against our actual
      profile — do it after the instrumentation, not before.
- [ ] **Faster JSON.** `json.loads` runs on every websocket message. Only worth
      it if the instrumentation says parse time is material; stdlib json on
      3.14 is not obviously the bottleneck at our message rates.
- [ ] **Early-exit `on_book`.** A match-book update re-scores both legs even
      when the touch did not move. Skipping unchanged touches is easy and
      removes most evaluate calls.

### Hardware

- [x] Colocation is already right: us-east-1d, 2 ms to the exchange. No win
      available.
- [ ] **CPU credits are unverified.** `kalshi-vps` is a **t3.micro** (2 vCPU,
      908 MB) already running two collectors. t3 is burstable; if credits drain
      the box throttles to baseline and latency goes with it. The `claude-kalshi`
      IAM user cannot read CloudWatch (`cloudwatch:GetMetricStatistics` and
      `ec2:DescribeInstanceCreditSpecifications` both denied), so **check
      `CPUCreditBalance` in the console during a busy slam day**, or grant those
      two read permissions.
- [ ] **Memory is the tighter constraint.** 908 MB total, **74.6 MB free** with
      collector_ws at 113 MB and winner_taker at 48 MB — and both grow with the
      number of live matches. A slam day is ~120 matches against today's 1.
      Watch it, and if it bites, prefer moving `winner_taker` to its own small
      instance over shrinking capture: it isolates trading from the box whose
      job is not to lose data, and t3.small is ~$15/mo.

---

## P2 — should we make a GitHub repo?

**Yes, private.** Reasons, in order:

1. **There is no off-box backup.** The work exists on the laptop and as an
   rsync'd copy on the VPS. `kalshi-tennis` and `kalshi` are both on GitHub;
   this is the odd one out.
2. **It makes the box auditable without trusting the deploy script.** Today
   `vps/deploy.sh` ships `git ls-files` over rsync and writes `DEPLOYED_SHA`,
   which gives the same guarantee as `git pull` — only committed work, never
   untracked files — but only because the script says so. With a remote, the
   box pulls and `git log` on the box is the authority.
3. It costs nothing and the history is already local.

Keys are gitignored and must stay that way; `deploy.sh` ships them over ssh,
never through git. If you create it, switch `deploy.sh` to the `kalshi-tennis`
model (`git push` here, `git pull` there).

Deployment works either way today, so this is not blocking.

---

## P3 — open questions the paper run will not answer

- **Does the loss tail exist?** 0 of 425 and 0 of 27 is not zero probability.
  At M=0.09, R=0.18 the implied per-leg risk is ~1.6%. Only live settlement
  answers it, and only slowly.
- **Does MECNET return collateral on a schedule?** It does not net at entry —
  measured, ratio 1.00 on same-event shorts. The four probe legs are
  deliberately left open; **re-read the balance as `KXATP-26USO` legs settle**
  to see whether excess comes back later. Nothing depends on it.
- **Does the edge survive a deeper book?** See P0 item 1. This is the question.

---

## Standing hazards

Three joins in this project have now failed by being one identifier short, each
time silently and directionally:

1. list/candle prices used as tradeable (`TENNIS_DEADLEG.md`) → live orderbooks
   only.
2. player code as a tournament (`build_tourn_map.py`) → join on competition.
3. player code as a *player* (2026-08-26) → join on the name.

The third was found in the paper bot's own log while checking an unrelated
schedule detail, one hour after it started. It would have shorted Jack Draper
because Liam Draxl was losing a qualifying match. **When an identity pairs two
books, verify it on something the exchange states outright.**
