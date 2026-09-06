"""Phase 2: what should a $150 account actually spend on?

`winner_take.py` measured the rule unconstrained -- $111,907 of collateral for
$4,233. The bot in WINNER_TAKER.md has two orders of magnitude less than that,
so the rule's sign is no longer the question. Two things are:

  1. WHICH opportunities. The doc says "prioritise by ROC, not edge", but
     ranking needs more than one candidate visible at once, which means firing
     on a tick rather than on every book update. FINDINGS.md killed persistence
     gates (0s beat 15s beat 60s beat 600s, monotonically) -- but persistence
     and staleness are different costs. A persistence gate REQUIRES the bid to
     survive N seconds, which is a selection filter and threw away a third of
     the edge. A tick only means we may fire up to N seconds late, and an IoC
     that arrives after the bid left simply does not fill. This measures the
     tick, not the filter.

  2. HOW MUCH into a merely-decent opportunity. With capital locked for days,
     taking a 1% ROC bid may cost a 6% one tomorrow. The floor that maximises
     realised dollars is an empirical question, not a preference: sweep it.

Collateral is released when the TOURNAMENT settles, which is why this cannot
be sharded by day and why event close times are fetched. A leg whose close
time is unknown never releases -- the conservative direction.

Run:  python3 alloc_study.py                 # the standard sweep
      python3 alloc_study.py --cap 500        # at a different account size
"""
import argparse
import csv
import glob
import json
import os
import time
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone

from iolib import LIVE

CAND_DIR = os.path.join(LIVE, "..", "cand")
CLOSE_CACHE = os.path.join(LIVE, "event_close.json")
BASE = "https://api.elections.kalshi.com/trade-api/v2"

fee = lambda p: 0.07 * p * (1 - p)


def load_rows():
    rows = []
    for fn in sorted(glob.glob(os.path.join(CAND_DIR, "cand_*.csv"))):
        with open(fn) as f:
            for r in csv.DictReader(f):
                rows.append((float(r["t"]), r["tk"], r["event"],
                             float(r["bid"]), float(r["size"]),
                             float(r["since"]), float(r["edge"]),
                             float(r["roc"]), r["result"]))
    rows.sort(key=lambda x: x[0])
    return rows


def event_close(events):
    """Unix close time per winner event, cached.

    Collateral on a short winner leg is locked until the event settles, so
    when it comes back is the difference between $150 funding one trade and
    funding thirty. An event whose close is unknown is treated as never
    closing, which understates capacity rather than inventing it.
    """
    cache = {}
    if os.path.exists(CLOSE_CACHE):
        cache = json.load(open(CLOSE_CACHE))
    missing = [e for e in events if e not in cache]
    for ev in missing:
        try:
            url = f"{BASE}/markets?event_ticker={ev}&limit=200"
            with urllib.request.urlopen(url, timeout=30) as r:
                d = json.load(r)
            ts = []
            for m in d.get("markets", []):
                for k in ("close_time", "expiration_time",
                          "expected_expiration_time"):
                    v = m.get(k)
                    if v:
                        ts.append(datetime.fromisoformat(
                            v.replace("Z", "+00:00")).timestamp())
                        break
            cache[ev] = max(ts) if ts else None
        except Exception as e:
            print(f"  close time for {ev} unavailable ({e}); treating as never")
            cache[ev] = None
        time.sleep(0.2)
    if missing:
        json.dump(cache, open(CLOSE_CACHE, "w"))
    return cache


class Sim:
    """One capital-constrained run over the candidate stream.

    `window` is the tick: 0 fires on every book update (the doc's literal
    "take immediately"), W>0 collects everything seen in the last W seconds and
    spends best-ROC-first. Firing on a leg uses its LATEST row inside the
    window, so a bid that shrank while we waited is taken at the smaller size
    -- that shrinkage is exactly the cost of ranking, and it is what the sweep
    is measuring.
    """

    def __init__(self, cap, window, rank, floor, rise_to, leg_cap, event_cap,
                 day_cap, closes, min_price=0.0, max_price=0.15,
                 min_edge=0.01):
        self.cap, self.window, self.rank = cap, window, rank
        self.floor, self.rise_to = floor, rise_to
        self.leg_cap, self.event_cap, self.day_cap = leg_cap, event_cap, day_cap
        self.closes = closes
        self.min_price, self.max_price = min_price, max_price
        self.min_edge = min_edge
        self.locked = 0.0
        self.by_leg = defaultdict(float)
        self.by_event = defaultdict(float)
        self.by_day = defaultdict(float)
        self.open_pos = []          # (release_ts, tk, event, collateral)
        self.sold = defaultdict(float)   # (tk, price, since) -> contracts sold
        self.pnl = 0.0
        self.contracts = 0.0
        self.takes = 0
        self.peak = 0.0
        self.coll_days = 0.0        # dollar-days, for a rate of return
        self.last_t = None
        self.skipped_room = 0

    def release(self, now):
        keep = []
        for rel, tk, ev, coll in self.open_pos:
            if rel is not None and rel <= now:
                self.locked -= coll
                self.by_leg[tk] -= coll
                self.by_event[ev] -= coll
            else:
                keep.append((rel, tk, ev, coll))
        self.open_pos = keep

    def accrue(self, now):
        if self.last_t is not None:
            self.coll_days += self.locked * (now - self.last_t) / 86400.0
        self.last_t = now

    def cur_floor(self):
        """ROC required right now.

        A fixed floor spends on whatever arrives first. A rising floor asks
        more of each dollar as the account fills up, which is the reserve-price
        answer to 'take 1% now or wait for 6% tomorrow' -- but the schedule is
        judgement, so it is swept against the fixed one rather than assumed.
        """
        if self.rise_to is None:
            return self.floor
        u = min(1.0, self.locked / self.cap) if self.cap else 1.0
        return self.floor + (self.rise_to - self.floor) * u

    def qualifies(self, c, floor):
        _t, _tk, _ev, p, size, _since, edge, roc, _res = c
        return (size >= 1 and self.min_price <= p <= self.max_price
                and edge >= self.min_edge and roc >= floor)

    def fire(self, now, cands):
        self.release(now)
        self.accrue(now)
        floor = self.cur_floor()
        elig = [c for c in cands if self.qualifies(c, floor)]
        if self.rank == "roc":
            elig.sort(key=lambda c: -c[7])
        elif self.rank == "edge":
            elig.sort(key=lambda c: -c[6])
        # "fifo" leaves them in arrival order
        day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d")
        for t, tk, ev, p, size, since, edge, roc, res in elig:
            unit = 1 - p
            key = (tk, p, since)
            remaining = size - self.sold[key]
            if remaining < 1:
                continue
            room = min(self.cap - self.locked,
                       self.leg_cap - self.by_leg[tk],
                       self.event_cap - self.by_event[ev],
                       self.day_cap - self.by_day[day])
            if room < unit:
                self.skipped_room += 1
                continue
            n = min(remaining, int(room / unit))
            if n < 1:
                continue
            coll = n * unit
            self.sold[key] += n
            self.locked += coll
            self.by_leg[tk] += coll
            self.by_event[ev] += coll
            self.by_day[day] += coll
            self.peak = max(self.peak, self.locked)
            rel = self.closes.get(ev)
            self.open_pos.append((rel, tk, ev, coll))
            per = (p - fee(p)) if res == "no" else (p - 1 - fee(p))
            self.pnl += per * n
            self.contracts += n
            self.takes += 1

    def run(self, rows):
        if self.window <= 0:
            for r in rows:
                self.fire(r[0], [r])
            return self
        # Per-leg state PERSISTS between ticks rather than being rebuilt from
        # the rows that happened to arrive inside one window. A bid that was
        # posted and then sat untouched emits no further rows, and a windowed
        # rule that only looked at this window's arrivals would never see it --
        # which would make ranking look worse than it is for a reason that has
        # nothing to do with ranking. The extractor emits a bid=0 row when a
        # level dies, so a vanished bid is visible here as state, and STALE
        # bounds how long a silent leg is believed.
        STALE = 300.0
        state = {}
        batch_end = None
        for r in rows:
            t = r[0]
            if batch_end is None:
                batch_end = t + self.window
            if t > batch_end:
                self.fire(batch_end, [c for c in state.values()
                                      if batch_end - c[0] <= STALE])
                batch_end = t + self.window
            state[r[1]] = r
        if batch_end is not None:
            self.fire(batch_end, [c for c in state.values()
                                  if batch_end - c[0] <= STALE])
        return self

    def report(self, name):
        roc = (self.pnl / self.peak * 100) if self.peak else 0.0
        # Return per dollar-DAY of locked collateral. The plain ROC number
        # flatters a strategy that holds for a week and understates one that
        # turns capital over; these positions are opened late in a tournament
        # and settle when it ends, so the hold is days, not weeks, and the
        # rate is what says whether the lockup was worth it.
        rate = (100 * self.pnl / self.coll_days) if self.coll_days else 0.0
        return (f"{name:26} {self.takes:7,} {self.contracts:10,.0f} "
                f"{100 * self.pnl / self.contracts if self.contracts else 0:7.2f}c "
                f"{self.pnl:9,.2f} {self.peak:9,.2f} {roc:7.1f}% {rate:8.2f}%")


HDR = (f"{'rule':26} {'takes':>7} {'contracts':>10} {'c/ct':>8} "
       f"{'pnl $':>9} {'peak $':>9} {'ROC':>8} {'%/$-day':>9}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=150.0)
    ap.add_argument("--leg-cap", type=float, default=None,
                    help="default cap/4, per WINNER_TAKER.md")
    ap.add_argument("--event-cap", type=float, default=None,
                    help="default cap/2")
    ap.add_argument("--day-cap", type=float, default=None,
                    help="default cap (i.e. off)")
    args = ap.parse_args()
    leg = args.leg_cap if args.leg_cap is not None else args.cap / 4
    evc = args.event_cap if args.event_cap is not None else args.cap / 2
    dayc = args.day_cap if args.day_cap is not None else args.cap

    rows = load_rows()
    if not rows:
        raise SystemExit(f"no candidate rows in {CAND_DIR}; run alloc_extract.py")
    events = sorted({r[2] for r in rows})
    closes = event_close(events)
    span = (rows[-1][0] - rows[0][0]) / 86400.0
    known = sum(1 for e in events if closes.get(e))
    print(f"{len(rows):,} candidate rows, {len({r[1] for r in rows})} legs, "
          f"{len(events)} events ({known} with known close), "
          f"{span:.1f} days of span")
    print(f"cap ${args.cap:,.0f}  per-leg ${leg:,.0f}  per-event ${evc:,.0f}  "
          f"per-day ${dayc:,.0f}\n")

    def sim(**kw):
        kw.setdefault("cap", args.cap)
        kw.setdefault("leg_cap", leg)
        kw.setdefault("event_cap", evc)
        kw.setdefault("day_cap", dayc)
        kw.setdefault("closes", closes)
        kw.setdefault("rise_to", None)
        return Sim(**kw).run(rows)

    print("=== 1. the tick, holding the rule fixed (fifo, floor 0.03)")
    print("    isolates what DELAY costs, with no ranking involved")
    print(HDR)
    for w in (0, 0.5, 1, 2, 5, 15):
        print(sim(window=w, rank="fifo", floor=0.03)
              .report("immediate" if not w else f"{w}s tick, no ranking"))

    print("\n=== 2. ranking, holding the tick fixed")
    print("    same delay, spending order the only difference")
    print(HDR)
    for w in (0.5, 1, 5):
        for r in ("fifo", "roc", "edge"):
            print(sim(window=w, rank=r, floor=0.03).report(f"{w}s tick, {r}"))

    print("\n=== 3. what ROC floor maximises dollars? (immediate)")
    print(HDR)
    for f in (0.0, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.12):
        print(sim(window=0, rank="fifo", floor=f).report(f"floor {f:.2f}"))

    print("\n=== 4. rising floor: ask more of each dollar as the account fills")
    print(HDR)
    for lo, hi in ((0.03, 0.03), (0.01, 0.05), (0.02, 0.08), (0.03, 0.10),
                   (0.03, 0.20)):
        print(sim(window=0, rank="fifo", floor=lo, rise_to=hi)
              .report(f"floor {lo:.2f} -> {hi:.2f}"))

    print("\n=== 5. per-leg cap: how much on one bet")
    print("    the risk this buys is the one 0/425 never tested")
    print(HDR)
    for lc in (args.cap / 10, args.cap / 4, args.cap / 3, args.cap / 2,
               args.cap):
        print(sim(window=0, rank="fifo", floor=0.03, leg_cap=lc,
                  event_cap=args.cap).report(f"per-leg ${lc:,.0f}"))

    print("\n=== 6. per-event cap: both draws are one event each")
    print(HDR)
    for ec in (args.cap / 4, args.cap / 2, args.cap):
        print(sim(window=0, rank="fifo", floor=0.03, event_cap=ec,
                  leg_cap=args.cap).report(f"per-event ${ec:,.0f}"))

    print("\n=== 7. account size: what does more capital buy")
    print(HDR)
    for c in (150, 300, 500, 1000, 2000, 5000):
        print(sim(cap=c, window=0, rank="fifo", floor=0.03, leg_cap=c / 4,
                  event_cap=c / 2, day_cap=c).report(f"cap ${c:,}"))

    print("\n=== 8. per-day cap: pacing new commitment")
    print(HDR)
    for dc in (args.cap / 4, args.cap / 2, args.cap):
        print(sim(window=0, rank="fifo", floor=0.03, day_cap=dc)
              .report(f"per-day ${dc:,.0f}"))


if __name__ == "__main__":
    main()
