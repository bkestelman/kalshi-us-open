"""Sell the future-round legs of players whose current match is nearly lost.

The spec is WINNER_TAKER.md; the measurement behind it is FINDINGS.md
(2026-08-24): +3.59c/contract over 25 days, 117,797 contracts, ~51 legs,
0 of 425 takes settling YES. Every other match state loses badly under the
same rule, so the dying condition IS the strategy, not a filter on it.

One player, several books. M is their match-winner price -- 1c wide, millions
of contracts. W is a leg that pays only if they keep winning: the tournament
winner (3-8c wide, thin) or a round qualifier -- "will X qualify for the
Quarterfinals / Semifinals / Final", which Kalshi lists under KX{tour}ADVANCE.
Every one of them requires winning this match and some amount of what follows,
so

    fair = M * R        R = P(win the rest | win this match)

R is per-leg and observed, so it absorbs the difference between them without
a new rule: the quarterfinal qualifier of a player in the round of 16 has
R near 1, the tournament winner has R near zero, and the same arithmetic
prices both.

R needs no model. Both books are live before the match turns, so R is observed
directly as the running median of W/M from while the match was still
competitive, in THIS match. When M collapses, fair collapses with it; if the
resting bid has not followed, we sell into it. R is only sampled from a book
narrow enough for its mid to mean something -- see MAX_R_SPREAD, which the
qualifier books made necessary.

Always the taker, always the seller, IoC, held to settlement.

WINNER_TAKER.md left two things open -- how to rank when capital is scarce,
and whether ranking is worth the tick it needs. alloc_study.py measured both
against 15 days of capture under a $150 cap, and the answer to both was no:

  * Ranking is a NO-OP. fifo, best-ROC-first and best-edge-first return
    identical dollars at every tick length, because qualifying legs almost
    never coexist -- 27 legs over 21 days, and a dying match is a rare state.
    There is nothing to rank.
  * The tick it would need costs 34% of the P&L at half a second ($27.88
    immediate against $18.45), monotonically worse from there. Same shape as
    the persistence sweep in FINDINGS.md, for a different reason: delay does
    not select, it just misses.

So the bot evaluates on every book update and takes immediately. There is no
ROC floor at all any more: the %/dollar-day arithmetic it was tuned on assumed
collateral locked for days, and the exchange settles a leg minutes after the
player goes out. `min_edge` is the gate that survives, because it prices the
bid against fair rather than against a holding period.

Caps are read from data/live/winner_taker.json on every tick, so raising the
account's commitment mid-session takes effect within a second and is logged.
Committed capital is rebuilt from EXCHANGE fills at startup, not from our own
log, so a restart cannot double-commit.

Run:  python3 winner_taker.py paper       # decides and logs, places nothing
      python3 winner_taker.py live        # places real IoC sells
"""
import argparse
import asyncio
import bisect
import csv
import itertools
import json
import os
import signal
import sys
import time
import uuid
from collections import defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import websockets

from discovery import Discovery
from iolib import LIVE as OUT
from kalshi import (API, WS_HOST, WS_PATH, Book, fee, get, signed, ws_headers)

# websockets renamed connect()'s header argument in v14 (extra_headers ->
# additional_headers), and passing the wrong one is a TypeError on every
# connect: an infinite reconnect loop that still looks alive to systemd.
try:
    import inspect
    _HDR = ("additional_headers"
            if "additional_headers" in inspect.signature(websockets.connect).parameters
            else "extra_headers")
except (TypeError, ValueError):
    _HDR = ("additional_headers"
            if int(websockets.__version__.split(".")[0]) >= 14 else "extra_headers")

OID_PREFIX = "wtk-"         # so committed() counts THIS strategy and no other
CONFIG_PATH = os.path.join(OUT, "winner_taker.json")
DISCOVERY_INTERVAL = 60
SETTLE_POLL = 60.0
MARK_POLL = 30.0
STATUS_EVERY = 120.0
BALANCE_POLL = 30.0

# Entry gates that are not risk limits. These are the measured strategy, not
# preferences, so they live in code where a config edit cannot quietly move
# them off the thing that was measured.
DYING = 0.10        # match mid must be below this
# The loser's match market quotes no bid and an ask at a cent or two once the
# last point is played, which is the exchange saying the player is out well
# before it formally settles -- Anisimova's book went one-sided at 16:48:07 on
# 2026-09-05 and the market did not close until 16:55:11.
ELIMINATED = 0.02   # started, no bid, ask at or under this: the player is out
# Now that R is frozen at the first ball, this no longer excludes a collapse
# -- nothing is sampled during one. What it still excludes is a player whose
# PRE-MATCH line is under 0.30, so a heavy underdog accrues no R and cannot be
# sold. That is the conservative reading and it is where 5 of the 7 unpriceable
# players of 2026-09-05 sat (Bucsa 0.055, Merida 0.015, Navone 0.015), though
# all five also had no bid on the leg, so none was sellable regardless.
COMPET = 0.30       # pre-match line above this is worth sampling; also recovery
MIN_R = 50          # samples of W/M required before fair is trusted
# A mid is only worth sampling if the book is narrow enough for it to mean
# something. Measured 2026-09-05 over the open US Open men's legs, median
# spread: winner 1c, FIN 5c, SEMI 7c, QUAR 34c. Vacherot's quarterfinal
# qualifier quoted 1c bid / 97c ask against a match that priced him at 11.5c
# -- a mid of 49c, and an R sample of 4.3 that would have poisoned the median
# for every other leg of his. 0.10 admits the winner, FIN and SEMI books and
# rejects that one. The obs log records wbid and wask on every row, so this is
# a dial that can be settled from the capture rather than argued.
MAX_R_SPREAD = 0.10
R_CAP = 500         # distinct ratios kept; repeats cost a counter, not a slot
R_MAX_AGE = 12 * 3600   # older checkpointed samples belong to a different match
STATE_PATH = os.path.join(OUT, "winner_taker_state.json")
# Which tickers hold collateral taken on the eliminated path. restore() reads
# fills back from the exchange, and a fill does not say which gate produced it
# -- without this, every eliminated position would be counted as comeback risk
# after a restart and would starve the risk caps it was deliberately exempt
# from.
ELIM_PATH = os.path.join(OUT, "winner_taker_elim.json")
STATE_EVERY = 30.0

DEFAULTS = {
    "enabled": True,
    # Dollars of collateral locked at once by THIS strategy. Collateral is
    # (1 - price) per contract and stays locked until the TOURNAMENT settles --
    # days, not the match. That is the binding constraint: the 25-day
    # measurement needed $111,907 of collateral to earn $4,233.
    "hard_cap": 150.0,
    "per_leg_cap": None,        # default hard_cap/4: one comeback, one leg
    "per_event_cap": None,      # default hard_cap/2: one draw
    "per_day_cap": None,        # default hard_cap: paces new commitment
    # Collateral for takes on the ELIMINATED path, which the three caps above
    # do not apply to. Those three all price one thing -- the comeback -- and
    # an eliminated player has no comeback left to price: the match is over and
    # every remaining leg settles NO. Measured 2026-09-05 on Anisimova: 937
    # contracts rested at 1c across her two legs and per_leg_cap ($37.50, or
    # hard_cap/4) took 37 of them, because `risk` binds on the PLAYER and her
    # winner leg spent the whole budget before her FIN leg was scored.
    #
    # This is NOT free money and the cap is not a formality. "The player is
    # out" is inferred from a book shape, not known, and the inference is
    # wrong sometimes: over 2026-09-04..06, 46 of 94 match-winner markets
    # showed the shape and one of them -- Zheng, KEYZHE 09-05 -- went on to
    # WIN. That one cost nothing only because no leg of hers had a resting bid
    # to hit (no-bid on all 1,003 evaluations across 181.5s of false reading),
    # which is market behaviour nobody is obliged to repeat. At 1c the payoff
    # is 99:1 against, so this stays a number someone chose.
    "elim_cap": None,           # default hard_cap: the whole book, once
    "cash_reserve": 0.0,        # never spend the account below this
    # There is no min_roc, and no rising floor. Both are gone as of
    # 2026-09-05. The 0.03 knee was measured in %/dollar-DAY against a study
    # that believed collateral sat locked until the tournament settled; the
    # exchange says a leg settles when the PLAYER goes out, 9-30 minutes after
    # their match (README). Against a 20-minute hold, 0.03 was not a knee, it
    # was a wall: it is what declined Muchova at 2c on 2026-09-04, the one US
    # Open leg in 23 whose book had a bid at all.
    #
    # What still stops a bad trade is min_edge, and it is the gate that
    # matters -- it compares the bid to FAIR, so it scales with the round
    # being sold. Live check 2026-09-05, Vacherot's quarterfinal-qualifier
    # leg: bid 1c x 1,000 against a fair of ~11.5c. min_roc never had to see
    # that one; min_edge rejects it on its own, at -10.6c.
    "min_edge": 0.01,           # one tick; below this is rounding
    "max_price": 0.15,          # richer than this is a contender, not a longshot
    "max_take": 500,            # contracts per order
    # Housekeeping only -- R sampling, config reload, the observation log.
    # Entries are NOT on a timer: they fire on the book update that creates
    # them, because alloc_study measured every delay as a straight loss.
    "sample_every": 1.0,
    "cover_on_recover": False,  # buy back if the match mid climbs back over .30
    "obs_every": 60.0,          # observation log cadence per watched leg
}


def now_day():
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat()} {msg}"
    print(line, flush=True)
    with open(os.path.join(OUT, "winner_taker.log"), "a") as f:
        f.write(line + "\n")


class Config:
    """Caps read from disk on every tick.

    Editing the JSON raises or lowers the commitment within one tick, with no
    restart and no dropped state -- which is the point: the account will grow,
    and stopping the bot to change a number is how you miss the day you were
    waiting for. Every change is logged with its before/after so the action log
    explains a sizing change months later.
    """

    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.v = dict(DEFAULTS)
        self.mtime = 0.0
        if not os.path.exists(path):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                json.dump(DEFAULTS, f, indent=2)
            log(f"wrote default config {path}")
        self.reload(quiet=True)

    def reload(self, quiet=False):
        try:
            m = os.path.getmtime(self.path)
        except OSError:
            return
        if m <= self.mtime:
            return
        try:
            d = json.load(open(self.path))
        except ValueError as e:
            log(f"config: {self.path} is not valid JSON ({e}); keeping previous")
            self.mtime = m
            return
        old, new = dict(self.v), dict(DEFAULTS)
        new.update({k: v for k, v in d.items() if k in DEFAULTS})
        unknown = [k for k in d if k not in DEFAULTS]
        self.v = new
        self.mtime = m
        if not quiet:
            diff = {k: (old.get(k), new[k]) for k in new if old.get(k) != new[k]}
            if diff:
                log(f"config reloaded: " + ", ".join(
                    f"{k} {a!r}->{b!r}" for k, (a, b) in diff.items()))
        if unknown:
            log(f"config: ignoring unknown keys {unknown}")

    def __getitem__(self, k):
        v = self.v.get(k)
        if v is None and k in ("per_leg_cap", "per_event_cap", "per_day_cap",
                               "elim_cap"):
            hc = self.v["hard_cap"]
            return hc / 4 if k == "per_leg_cap" else (
                hc / 2 if k == "per_event_cap" else hc)
        return v


class Leg:
    """One sellable leg of one player, plus the match that prices it.

    The leg is either their tournament-winner market or one of the round
    qualifiers; a player in play has one Leg per open market, all sharing a
    match, a name, and a per_leg_cap bucket.
    """
    __slots__ = ("tour", "key", "comp", "code", "name", "match_tk", "win_tk",
                 "win_event", "rsamp", "rn", "_rkeys", "joined", "last_obs")

    def __init__(self, tour, key, comp, code, match_tk, win_tk, win_event,
                 name=""):
        self.tour, self.key, self.comp, self.code = tour, key, comp, code
        # The player's full name, which is what the match book and the winner
        # book were actually joined on -- the code is only a lookup key and is
        # NOT unique across a draw and its qualifying rounds (discovery.py).
        self.name = name
        self.match_tk, self.win_tk = match_tk, win_tk
        # As the EXCHANGE states it, never sliced off win_tk: one event carries
        # two market-ticker shapes (KXATP-26USO-ZVE and KXATP-26-SWE are both
        # event KXATP-26USO), and slicing splits one draw into two cap buckets.
        self.win_event = win_event
        # A MULTISET, not a list: {ratio: how many times it was seen}. A
        # frozen book quoted the same ratio 20,000 times and stored 20,000
        # copies of one number -- Keys' whole R was a single quote wearing
        # 20,000 samples as a disguise, and the checkpoint it wrote was 1.97 MB
        # for fifteen legs. Counting repeats keeps the weighting (a ratio
        # quoted for an hour should outweigh one quoted for a second) at a
        # thousandth of the size.
        self.rsamp = {}          # W/M seen while this match was competitive
        self.rn = 0              # total observations, repeats included
        self._rkeys = []         # the distinct ratios, sorted
        self.joined = time.time()
        self.last_obs = 0.0

    @property
    def event(self):
        return self.win_event

    @property
    def draw(self):
        """The tournament, for per_event_cap. NOT the Kalshi event.

        One draw is spread over four Kalshi events -- KXATP-26USO plus the
        QUAR/SEMI/FIN qualifiers -- and capping them separately would put four
        times the intended money into one tournament.
        """
        return f"{self.tour}|{self.comp}"

    @property
    def risk(self):
        """The unit a single comeback destroys, for per_leg_cap.

        A player's winner leg and their three round-qualifier legs do not fail
        independently: they all turn on the match being watched right now. One
        comeback takes every one of them, so per_leg_cap -- which exists to
        survive exactly one comeback -- has to bind on the PLAYER, not on the
        market ticker.
        """
        return f"{self.tour}|{self.comp}|{self.name or self.code}"

    def fair(self, m):
        """M * R, or None while R is unknown.

        A bot started mid-collapse has no competitive-period samples and must
        SKIP the match rather than borrow an R from elsewhere -- that
        circularity is what sank the earlier version of this trade. Losing a
        trade is cheaper than inventing a fair value.

        The median is TIME-WEIGHTED: a ratio quoted for an hour counts for the
        hour, not for one observation. `_rkeys` is kept sorted as ratios arrive
        (see observe_r), so this walks at most R_CAP distinct values and never
        sorts. It is called on every book update, where alloc_study priced
        delay at a third of the P&L per half second.
        """
        if self.rn < MIN_R:
            return None
        half, seen = self.rn / 2.0, 0
        for v in self._rkeys:
            seen += self.rsamp[v]
            if seen >= half:
                return m * v
        return m * self._rkeys[-1]      # unreachable while rn == sum(counts)

    def observe_r(self, r):
        """Record one W/M observation. Repeats are counted, not appended."""
        if r not in self.rsamp:
            if len(self._rkeys) >= R_CAP:
                return
            bisect.insort(self._rkeys, r)
            self.rsamp[r] = 0
        self.rsamp[r] += 1
        self.rn += 1


# In ladder order, nearest round first. Printing them alphabetically defeats
# the point of printing them: the whole check is that the rungs DESCEND.
RUNGS = ("QUAR", "SEMI", "FIN", "WIN")


def rung(tk):
    """Which rung of the ladder a leg is: QUAR, SEMI, FIN or WIN."""
    for r in RUNGS[:-1]:
        if r in tk:
            return r
    return "WIN"


class WinnerTaker:
    def __init__(self, live):
        self.live = live
        self.cfg = Config()
        self.disc = Discovery(log=log)
        self.pool = ThreadPoolExecutor(max_workers=4)
        # Orders go through a pool of ONE, always the same thread. kalshi.signed
        # keeps its HTTPS connection in thread-local storage, so sharing the
        # general pool meant an order could land on a thread whose socket was
        # cold and pay a fresh TLS handshake -- measured at 14 ms against 2 ms
        # for the TCP connect alone, on the critical path. One dedicated thread
        # plus the balance poll below (which runs on it) keeps that socket hot.
        self.order_pool = ThreadPoolExecutor(max_workers=1)
        self.books = defaultdict(Book)
        self.legs = {}                    # winner ticker -> Leg
        self.legs_by_match = defaultdict(list)   # match ticker -> [Leg]
        self.inflight = set()             # legs with an order outstanding
        self.pending = {}                 # reservation id -> collateral held
        self._rid = itertools.count()
        self.groups = {}                  # (tour,key) -> {"legs": [...], "last_alive"}
        self.announced = set()            # groups already logged as IN PLAY
        self.pos = {}                     # winner ticker -> position dict
        self._comp_cache = {}             # event ticker -> competition string
        self.by_day = defaultdict(float)  # UTC day -> new collateral committed
        self.settled_tks = set()
        self.elim_tks = self.load_elim()
        self.cash_avail = None            # spendable cash, refreshed in background
        self.rstate = self.load_state()
        self.dirty = None
        self.stop = False
        self.msgs = 0
        self.takes = 0
        self.realized = 0.0
        self.recent = deque(maxlen=20)

    # ------------------------------------------------------------- logging
    def jlog(self, obj):
        obj["t"] = round(time.time(), 3)
        obj.setdefault("mode", "live" if self.live else "paper")
        tag = "" if self.live else "paper_"
        p = os.path.join(OUT, f"winner_taker_actions_{tag}{now_day()}.jsonl")
        with open(p, "a") as f:
            f.write(json.dumps(obj, separators=(",", ":")) + "\n")

    def obs(self, row):
        """Every watched leg, on a cadence, whether or not it traded.

        The takes log only records what CLEARED the gates, which makes "no
        trades today" unreadable -- a book with no bid and a book whose bid sat
        1c below fair are completely different findings about this strategy,
        and open question 3 (does the edge survive a deeper book?) can only be
        answered from the declines.
        """
        p = os.path.join(OUT, f"winner_taker_obs_{now_day()}.csv")
        new = not os.path.exists(p)
        with open(p, "a", newline="") as f:
            w = csv.DictWriter(f, list(row), extrasaction="ignore")
            if new:
                w.writeheader()
            w.writerow(row)

    # ------------------------------------------------------------- capital
    def locked(self):
        return sum(p["collateral"] for p in self.pos.values())

    def locked_elim(self):
        """Collateral committed on the eliminated path only."""
        return sum(p.get("elim_coll", 0.0) for p in self.pos.values())

    def room(self, leg, elim=False):
        """Dollars of collateral still spendable on this leg, caps applied.

        Reserved-but-unconfirmed collateral counts against every cap. Entries
        fire on book updates and an order is a round trip, so two legs can
        easily be in flight at once; without the reservation both would size
        against the same free capital and the pair could breach the cap. The
        reservation is released when the fill count is known.

        `elim` swaps which caps apply, and does not remove all of them.
        per_leg/per_event/per_day exist to survive one comeback and an
        eliminated player has none, so they are skipped; hard_cap and elim_cap
        still bind, because those are the money existing, not the risk being
        priced, and no read of the book can conjure collateral we do not have.
        """
        risk, draw = leg.risk, leg.draw
        pend_all = sum(r["coll"] for r in self.pending.values())
        free = self.cfg["hard_cap"] - self.locked() - pend_all
        if elim:
            pend_elim = sum(r["coll"] for r in self.pending.values()
                            if r.get("elim"))
            return min(free,
                       self.cfg["elim_cap"] - self.locked_elim() - pend_elim)
        pend_leg = sum(r["coll"] for r in self.pending.values()
                       if r["risk"] == risk and not r.get("elim"))
        pend_ev = sum(r["coll"] for r in self.pending.values()
                      if r["draw"] == draw and not r.get("elim"))
        day = now_day()
        pend_day = sum(r["coll"] for r in self.pending.values()
                       if r["day"] == day and not r.get("elim"))
        # Eliminated collateral is netted out of every RISK cap. It is not
        # exposure to a comeback -- there is nothing left to come back from --
        # so letting it consume the comeback budget would quietly re-impose
        # the cap this path exists to skip, on the next player.
        by_leg = sum(p["collateral"] - p.get("elim_coll", 0.0)
                     for p in self.pos.values() if p["risk"] == risk)
        by_event = sum(p["collateral"] - p.get("elim_coll", 0.0)
                       for p in self.pos.values() if p["draw"] == draw)
        return min(free,
                   self.cfg["per_leg_cap"] - by_leg - pend_leg,
                   self.cfg["per_event_cap"] - by_event - pend_ev,
                   self.cfg["per_day_cap"] - self.by_day[day] - pend_day)

    def _comp_of_event(self, ev):
        """product_metadata.competition for one event, cached.

        Restored positions have to land in the same draw bucket as live ones
        or the caps do not compose across a restart, and the competition
        string is the only thing that spans KXATP-26USO and its ADVANCE
        siblings.
        """
        if ev not in self._comp_cache:
            d = get(f"/events/{ev}") or {}
            e = d.get("event") or {}
            self._comp_cache[ev] = (e.get("product_metadata") or {}).get(
                "competition")
        return self._comp_cache[ev]

    def restore(self):
        """Rebuild committed collateral from EXCHANGE fills, not our own log.

        A restart that resumed from zero would let the bot commit the cap a
        second time against positions it still holds -- and these positions
        live for days, so a restart mid-tournament is the normal case, not the
        edge case. Filtering on our own client_order_id prefix keeps other
        strategies' collateral out of THIS cap while still being authoritative
        about ours.
        """
        if not self.live:
            log("restore: paper mode, starting flat")
            return
        orders, cursor = [], None
        for _ in range(100):
            path = f"{API}/portfolio/orders?limit=200"
            if cursor:
                path += f"&cursor={cursor}"
            st, d = signed("GET", path)
            if st != 200 or not isinstance(d, dict):
                log(f"restore: /portfolio/orders failed ({st} {str(d)[:120]}); "
                    "REFUSING to trade with unknown exposure")
                self.stop = True
                return
            orders.extend(d.get("orders") or [])
            cursor = d.get("cursor")
            if not cursor:
                break
        n = 0
        for o in orders:
            if not (o.get("client_order_id") or "").startswith(OID_PREFIX):
                continue
            fc = float(o.get("fill_count_fp") or 0)
            if fc <= 0 or o.get("book_side") != "ask":
                continue
            tk = o["ticker"]
            py = (float(o["yes_price_dollars"]) if o.get("yes_price_dollars")
                  else 1 - float(o["no_price_dollars"]))
            m = (get(f"/markets/{tk}") or {}).get("market", {})
            if m.get("result") in ("yes", "no") or m.get("status") == "settled":
                self.settled_tks.add(tk)
                self.elim_tks.discard(tk)       # collateral returned; forget it
                continue
            ev = m.get("event_ticker")
            comp = self._comp_of_event(ev) if ev else None
            # Every winner and ADVANCE market carries the player in all three
            # of these; take whichever is there, because the alternative here
            # is refusing to trade at startup.
            name = (m.get("yes_sub_title") or m.get("no_sub_title")
                    or m.get("subtitle") or "").strip()
            if not ev or not comp or not name:
                # Same contract as a failed orders read: an open position we
                # cannot place in a draw and against a player cannot be capped,
                # and both caps now bind on those, not on the ticker.
                log(f"restore: /markets/{tk} left event/competition/name "
                    f"unknown (ev={ev!r} comp={comp!r} name={name!r}); "
                    "REFUSING to trade with uncappable exposure")
                self.stop = True
                return
            tour = "ATP" if tk.startswith("KXATP") else "WTA"
            p = self.pos.setdefault(tk, {"count": 0.0, "collateral": 0.0,
                                         "cash": 0.0, "event": ev,
                                         "risk": f"{tour}|{comp}|{name}",
                                         "draw": f"{tour}|{comp}",
                                         "match_tk": None, "prices": []})
            p["count"] += fc
            p["collateral"] += fc * (1 - py)
            p["cash"] += fc * (py - fee(py))
            p["prices"].append((py, fc))
            if tk in self.elim_tks:
                # The whole position, not just the tranche that was taken after
                # elimination: once the player is out, collateral committed
                # before that is no longer exposed to a comeback either.
                p["elim_coll"] = p["collateral"]
            n += 1
        log(f"restore: {n} prior fills across {len(self.pos)} open legs, "
            f"${self.locked():,.2f} of collateral already locked")

    # -------------------------------------------------------------- R state
    def save_state(self):
        """Checkpoint the R samples.

        WINNER_TAKER.md says a bot restarted mid-match must SKIP that match
        rather than guess an R, and that stands -- but reloading samples this
        process collected itself, from this same match, is not guessing. It is
        the same observation, and the failure mode the doc is protecting
        against (borrowing an R from somewhere else) is prevented by keying on
        the match ticker and expiring at R_MAX_AGE. Without this a restart
        during the US Open's second week -- when the legs worth trading are
        exactly the ones in play -- blinds the bot for the rest of the day.
        """
        try:
            now = round(time.time())
            d = {tk: {"match_tk": l.match_tk, "key": l.key, "comp": l.comp,
                      "code": l.code, "tour": l.tour, "t": now,
                      # [ratio, count] pairs, already sorted. JSON turns float
                      # KEYS into strings and reads them back as strings, so
                      # never write the dict itself.
                      "rsamp": [[v, l.rsamp[v]] for v in l._rkeys],
                      "rn": l.rn}
                 for tk, l in self.legs.items() if l.rn}
            # Carry forward legs we are not holding right now. Watching starts
            # PREROLL before the first ball, so a match later in the day is
            # deliberately NOT subscribed yet -- and writing only the held legs
            # dropped R for every one of them. Their own `t` is preserved, so
            # R_MAX_AGE still expires them on schedule.
            for tk, v in self.rstate.items():
                if tk not in d and now - v.get("t", 0) < R_MAX_AGE:
                    d[tk] = v
            self.rstate = d
            tmp = STATE_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(d, f, separators=(",", ":"))
            os.replace(tmp, STATE_PATH)     # atomic: a torn file loses R
        except Exception as e:
            log(f"save_state: {type(e).__name__} {e}")

    def load_state(self):
        if not os.path.exists(STATE_PATH):
            return {}
        try:
            d = json.load(open(STATE_PATH))
        except ValueError as e:
            log(f"load_state: unreadable ({e}); starting with no R")
            return {}
        now = time.time()
        out = {k: v for k, v in d.items() if now - v.get("t", 0) < R_MAX_AGE}
        # Migrate the flat-list format written before R was counted. Those
        # files are a list of repeated floats; collapse them so the rest of
        # this process only ever sees [ratio, count] pairs.
        for v in out.values():
            samp = v.get("rsamp") or []
            if samp and not isinstance(samp[0], (list, tuple)):
                counts = {}
                for r in samp:
                    counts[r] = counts.get(r, 0) + 1
                v["rsamp"] = [[r, counts[r]] for r in sorted(counts)]
                v["rn"] = len(samp)
        if out:
            log(f"load_state: R samples recovered for {len(out)} legs")
        return out

    def load_elim(self):
        """Tickers whose collateral came from the eliminated path.

        A restart rebuilds positions from exchange fills (restore), and a fill
        does not record which gate produced it. Without this the caps do not
        compose across a restart: collateral that was exempt from the risk caps
        before the restart would start consuming them after it.
        """
        try:
            if os.path.exists(ELIM_PATH):
                return set(json.load(open(ELIM_PATH)))
        except (ValueError, TypeError) as e:
            log(f"load_elim: unreadable ({e}); eliminated positions will be "
                "counted as risk until they settle")
        return set()

    def save_elim(self):
        try:
            tmp = ELIM_PATH + ".tmp"
            with open(tmp, "w") as f:
                json.dump(sorted(self.elim_tks), f)
            os.replace(tmp, ELIM_PATH)
        except OSError as e:
            log(f"save_elim: {type(e).__name__} {e}")

    async def state_loop(self):
        while not self.stop:
            await asyncio.sleep(STATE_EVERY)
            self.save_state()

    def balance(self):
        st, d = signed("GET", f"{API}/portfolio/balance")
        if st != 200 or not isinstance(d, dict):
            return None
        try:
            return float(d["balance_dollars"])
        except (KeyError, TypeError, ValueError):
            return float(d.get("balance", 0)) / 100

    async def balance_loop(self):
        """Keep spendable cash fresh in the background.

        `cash_reserve` used to be checked by calling /portfolio/balance inline
        in take(), which put a full REST round trip in front of every order --
        on the one path alloc_study measured as costing a third of the P&L per
        half second of delay. The reserve is a floor on the whole account, not
        a per-order quantity, so a value up to BALANCE_POLL seconds old is
        exactly as good and free.
        """
        loop = asyncio.get_running_loop()
        while not self.stop:
            if self.live:
                # Deliberately on the ORDER thread: this call doubles as the
                # keepalive that stops that thread's TLS session going cold
                # between takes. A dying match can be the first order in an
                # hour, and that is the order that must not pay a handshake.
                b = await loop.run_in_executor(self.order_pool, self.balance)
                if b is not None:
                    self.cash_avail = b
            await asyncio.sleep(BALANCE_POLL)

    # ---------------------------------------------------------- decisions
    def sample_r(self):
        """Collect W/M BEFORE the first ball, then freeze it.

        R is measured pre-match and never updated again, because during play it
        is not measuring what it claims to. The winner book does not mark down
        as fast as the match book, so W/M climbs as the player collapses --
        measured across four days of observations, on every one of the five
        legs whose match ran from competitive to lost:

            leg          M .60-1.0  M .45-.60  M .30-.45 | M .15-.30  M .05-.15
            KXWTA-...MUC     0.061      0.047      0.058 |     0.100      0.233
            KXWTA-...SVI     0.027      0.034      0.040 |     0.073      0.130
            KXATP-...PAU     0.021      0.027      0.035 |     0.087
            KXWTA-...ANI     0.042      0.052      0.065 |     0.084      0.125
            KXATP-...NAK     0.035      0.052      0.060 |     0.078

        COMPET excluded everything right of the bar, but the drift starts well
        inside the sampled region -- 1.5x to 1.7x by the 0.30-0.45 bucket -- so
        the median came out above the clean value. It biases `fair` UP, which
        declines the trade: Muchova's dying-period R of 0.833 prices her leg at
        a fair of 2.5c against the 2c bid that was actually there, where her
        competitive R of 0.061 prices it at 0.18c.

        Freezing is only possible because the exchange now tells us when the
        match starts (discovery.match_phases). A match already under way when
        we first see it gets no samples at all and is skipped -- WINNER_TAKER.md
        is explicit that a bot which missed the window must skip rather than
        guess, and R survives a restart through the checkpoint, keyed to the
        match it came from and expiring at R_MAX_AGE.

        Deliberately OUTSIDE the entry path and outside the `enabled` switch: a
        bot that stopped sampling because trading was paused would have to skip
        every match it sat out. Sampling on the tick rather than per book update
        makes R a median over SECONDS of quoting, where per-update sampling
        could reach MIN_R in a single burst.
        """
        for tk, leg in self.legs.items():
            # Frozen. Not "stop when the mid gets low" -- stop when the match
            # begins, which is a fact about the world rather than a threshold.
            if (leg.tour, leg.key) in self.disc.started:
                continue
            wb, mb = self.books.get(tk), self.books.get(leg.match_tk)
            if wb is None or mb is None:
                continue
            m, w = mb.mid(), wb.mid()
            wb_bid, wb_ask = wb.bid(), wb.ask()
            wide = (wb_bid is None or wb_ask is None
                    or wb_ask - wb_bid > MAX_R_SPREAD)
            if m is not None and m > COMPET and w is not None and not wide \
                    and len(leg._rkeys) < R_CAP:
                # Insert in order. This runs once a second per leg, where
                # fair() runs on every book update, so the cost belongs here.
                leg.observe_r(round(w / m, 5))

    def evaluate(self, leg):
        """Score one leg against every gate but the caps.

        Returns (candidate, reason). Exactly one is None. The reason is kept
        even when it is boring, because the observation log is written from it
        and "no bid at all" and "a bid a tick below fair" are entirely
        different findings about whether this strategy still exists.
        """
        tk = leg.win_tk
        wb, mb = self.books.get(tk), self.books.get(leg.match_tk)
        if wb is None or mb is None:
            return None, "no-book"
        m = mb.mid()
        started = (leg.tour, leg.key) in self.disc.started
        # ELIMINATED. When a match ends, the loser's match book goes one-sided
        # -- no bid, an ask at a cent -- so mid() returns None and every leg
        # used to die at `no-match-mid`. That was backwards: it is the moment
        # of MAXIMUM information, not of missing data. The player is out, so
        # every future-round leg is worth exactly zero, and fair needs neither
        # M nor R. Measured on Anisimova, 2026-09-05: the match book went
        # one-sided at 16:48:07 and the bot was blind from 16:49 through the
        # window where 157 contracts were still resting at 1c on her FIN leg.
        #
        # Only meaningful once the match has STARTED -- a pre-match longshot
        # quotes the same shape and has lost nothing.
        m_bid, m_ask = mb.bid(), mb.ask()
        out = (started and m_bid is None
               and m_ask is not None and m_ask <= ELIMINATED)
        if m is None and not out:
            return None, "no-match-mid"
        p, q = wb.bid(), wb.bid_size()
        snap = {"m": m, "p": p, "q": q, "ask": wb.ask(), "fair": None,
                "edge": None, "roc": None}
        if out:
            # No estimate, so nothing for min_edge to buffer. min_edge exists
            # to protect against error in R; there is no R here. What remains
            # is the fee, and a 1c bid clears it by 0.93c -- which min_edge at
            # a full cent would have rejected, because the most a 1c contract
            # can ever earn is 0.93c. The two gates had to change together or
            # neither would have fired.
            if p is None or q <= 0:
                return None, "no-bid"
            if p > self.cfg["max_price"]:
                return None, f"above-max-price {p:.2f}"
            edge = p - fee(p)
            snap["fair"], snap["edge"] = 0.0, edge
            snap["roc"] = edge / (1 - p)
            if edge <= 0:
                return None, f"below-fee {edge:+.4f}"
            if self.room(leg, True) < (1 - p):
                return None, "no-room"
            return ({"leg": leg, "tk": tk, "p": p, "q": q, "fair": 0.0,
                     "m": m if m is not None else 0.0, "edge": edge,
                     "roc": snap["roc"], "eliminated": True}, None)
        # A player is not "dying" before a ball has been struck. Without this
        # a first-round longshot quoted at 3c looks exactly like a collapse:
        # the mid is under DYING all day, and only the accident that R needs
        # competitive play stood between that and a sale. Selling a longshot at
        # its own fair price earns nothing and holds the collateral until they
        # are actually beaten, days later.
        if not started:
            return None, "not-started"
        if m >= DYING:
            return None, f"not-dying {m:.3f}"
        # no-bid BEFORE no-R. A leg with neither reported no-R, and that read
        # as "we arrived too late to price this" when the truth was "there is
        # nothing here to sell". On 2026-09-05 that mis-attribution covered
        # every one of the day's declines: 7 players crossed DYING with rn=0,
        # and the two of them that HAD been competitive -- Potapova at a match
        # mid of 0.500, Shapovalov at 0.355 -- had no bid on their winner leg
        # at all, ask 1c, one-sided. Order the gates so the log names the
        # binding one.
        if p is None or q <= 0:
            return None, "no-bid"
        fair = leg.fair(m)
        if fair is None:
            return None, f"no-R {leg.rn}/{MIN_R}"
        snap["fair"] = fair
        # There is no min_price. It was 0.03, and it was the only thing between
        # this bot and the first US Open leg where the trade actually existed:
        # 2026-09-04, Muchova at 2c x 227 against a fair of 0.66c, declined for
        # being cheap rather than for being wrong. A 1c leg is not a bad trade
        # because 1c is a small number -- min_edge already prices the premium
        # against fair, in the units that matter. Cheapness is not a separate
        # risk.
        if p > self.cfg["max_price"]:
            return None, f"above-max-price {p:.2f}"
        edge = p - fair - fee(p)
        roc = edge / (1 - p)
        snap["edge"], snap["roc"] = edge, roc
        if edge < self.cfg["min_edge"]:
            return None, f"below-edge {edge:+.4f}"
        if self.room(leg) < (1 - p):
            return None, "no-room"
        return ({"leg": leg, "tk": tk, "p": p, "q": q, "fair": fair, "m": m,
                 "edge": edge, "roc": roc}, None)

    def on_book(self, tk):
        """React to one book update. This is the entry path.

        Not a timer. alloc_study measured every delay as a straight loss --
        $27.88 taking immediately against $18.45 on a half-second tick,
        monotone from there -- and measured ranking as an exact no-op, because
        qualifying legs essentially never coexist. So there is nothing to
        collect and nothing to sort: score the legs this update could have
        moved, and fire.

        A MATCH book update moves fair for both players in it, so it re-scores
        their legs too; that is the update that usually matters, since fair
        collapses when M does.
        """
        if not self.cfg["enabled"] or self.stop:
            return
        legs = []
        if tk in self.legs:
            legs.append(self.legs[tk])
        legs.extend(self.legs_by_match.get(tk, ()))
        for leg in legs:
            if leg.win_tk in self.inflight:
                continue
            c, _reason = self.evaluate(leg)
            if c:
                self.inflight.add(leg.win_tk)
                asyncio.ensure_future(self._take_and_clear(c))

    async def _take_and_clear(self, c):
        try:
            await self.take(c)
        except Exception as e:
            log(f"take error {c['tk']}: {type(e).__name__} {e}")
        finally:
            self.inflight.discard(c["tk"])

    async def housekeeping_loop(self):
        """R sampling, config reload and the observation log.

        Everything here is deliberately NOT on the entry path: none of it
        should be able to delay a take, and R sampling in particular has to
        keep running when trading is switched off.
        """
        while not self.stop:
            await asyncio.sleep(self.cfg["sample_every"])
            try:
                self.cfg.reload()
                self.sample_r()
                now = time.time()
                for leg in list(self.legs.values()):
                    if now - leg.last_obs < self.cfg["obs_every"]:
                        continue
                    leg.last_obs = now
                    c, reason = self.evaluate(leg)
                    wb = self.books.get(leg.win_tk)
                    mb = self.books.get(leg.match_tk)
                    m = mb.mid() if mb else None
                    p = wb.bid() if wb else None
                    fair = leg.fair(m) if m is not None else None
                    edge = (p - fair - fee(p)) if (p and fair is not None) else None
                    self.obs({
                        "ts": datetime.now(timezone.utc).isoformat(),
                        "comp": leg.comp, "player": leg.code,
                        "name": leg.name,
                        "win_tk": leg.win_tk, "match_tk": leg.match_tk,
                        "mmid": "" if m is None else f"{m:.4f}",
                        "wbid": "" if p is None else f"{p:.4f}",
                        "wask": "" if not wb or wb.ask() is None else f"{wb.ask():.4f}",
                        "wsize": f"{wb.bid_size():.0f}" if wb else "",
                        "rn": leg.rn,
                        "fair": "" if fair is None else f"{fair:.4f}",
                        "edge": "" if edge is None else f"{edge:+.4f}",
                        "roc": "" if edge is None else f"{edge / (1 - p):.4f}",
                        "decision": "TAKE" if c else reason,
                        "locked": f"{self.locked():.2f}"})
            except Exception as e:
                log(f"housekeeping error: {type(e).__name__} {e}")

    async def take(self, c):
        """One IoC sell-YES at the resting bid.

        Nothing rests, so nothing needs cancelling or hedging, and a book that
        moved between the tick and the order fills less or not at all -- never
        worse. That is what replaces the persistence gate the cutoff sweep
        rejected.
        """
        leg, tk, p, q = c["leg"], c["tk"], c["p"], c["q"]
        elim = bool(c.get("eliminated"))
        unit = 1 - p
        room = self.room(leg, elim)
        if room < unit:
            # Journalled, because this is the one decline that happens AFTER
            # the gates said take. On 2026-09-05 both of Anisimova's legs
            # cleared every gate in the same millisecond and the second one
            # died here, silently, on a cap the first had already spent.
            self.jlog({"a": "skip", "tk": tk, "why": "no_room", "price": p,
                       "offered": q, "room": round(room, 2),
                       "need": round(unit, 2), "elim": elim})
            return
        n = int(min(q, self.cfg["max_take"], room / unit))
        if n < 1:
            return
        # NOTE: nothing between room() above and the reservation below may
        # await. Two takes can be in flight at once, and it is the absence of
        # a yield here that makes size-then-reserve atomic.
        reserve = self.cfg["cash_reserve"]
        if self.live and reserve > 0:
            bal = self.cash_avail
            if bal is None:
                self.jlog({"a": "skip", "tk": tk, "why": "balance_unknown"})
                return
            if bal - n * unit < reserve:
                self.jlog({"a": "skip", "tk": tk, "why": "cash_reserve",
                           "balance": bal, "need": n * unit})
                return
        body = {"ticker": tk, "client_order_id": f"{OID_PREFIX}{uuid.uuid4()}",
                "side": "ask", "count": f"{n}.00", "price": f"{p:.4f}",
                "time_in_force": "immediate_or_cancel",
                "self_trade_prevention_type": "maker", "post_only": False}
        # Reserve before the round trip, release after. Between these two
        # lines this order's worst case is charged against every cap, so a
        # second leg firing concurrently sizes against what is really left.
        rid = next(self._rid)
        self.pending[rid] = {"tk": tk, "risk": leg.risk, "draw": leg.draw,
                             "day": now_day(), "coll": n * unit, "elim": elim}
        try:
            if self.live:
                loop = asyncio.get_running_loop()
                st, resp = await loop.run_in_executor(
                    self.order_pool, signed, "POST",
                    f"{API}/portfolio/events/orders", body)
                if st not in (200, 201) or not isinstance(resp, dict):
                    self.jlog({"a": "order_fail", "tk": tk, "price": p,
                               "count": n, "st": st, "resp": str(resp)[:200]})
                    return
                o = resp.get("order", resp)
                filled = float(o.get("fill_count_fp") or o.get("fill_count") or 0)
            else:
                # Report the full size in paper so the caps bind exactly as
                # they would live; a dry run that "fills" nothing never tests
                # them.
                filled = n
        finally:
            self.pending.pop(rid, None)
        if filled < 1:
            self.jlog({"a": "no_fill", "tk": tk, "price": p, "count": n,
                       "edge": round(c["edge"], 4), "roc": round(c["roc"], 4)})
            return
        coll = filled * unit
        pos = self.pos.setdefault(tk, {"count": 0.0, "collateral": 0.0,
                                       "cash": 0.0, "event": leg.event,
                                       "risk": leg.risk, "draw": leg.draw,
                                       "match_tk": leg.match_tk, "prices": [],
                                       "elim_coll": 0.0})
        pos["count"] += filled
        pos["collateral"] += coll
        pos["cash"] += filled * (p - fee(p))
        pos["prices"].append((p, filled))
        pos["match_tk"] = leg.match_tk
        if elim:
            # Tracked per DOLLAR, not per position: a leg can be sold once on
            # the normal path while the match is merely dying and again once it
            # is over, and only the second tranche is exempt from the risk caps.
            pos["elim_coll"] = pos.get("elim_coll", 0.0) + coll
            self.elim_tks.add(tk)
            self.save_elim()
        else:
            # per_day_cap paces new RISK. Eliminated collateral is not risk.
            self.by_day[now_day()] += coll
        self.takes += 1
        self.recent.append((tk, p, filled, c["roc"]))
        self.jlog({"a": "take", "tk": tk, "match_tk": leg.match_tk,
                   "comp": leg.comp, "player": leg.code, "name": leg.name,
                   "price": p,
                   "count": filled, "offered": q, "fair": round(c["fair"], 4),
                   "mmid": round(c["m"], 4), "edge": round(c["edge"], 4),
                   "roc": round(c["roc"], 4), "collateral": round(coll, 2),
                   "r_n": leg.rn, "locked": round(self.locked(), 2),
                   "elim": elim})
        log(f"TAKE {leg.comp} {leg.code} SELL {filled:.0f} {tk} @{p:.2f} "
            f"(fair {c['fair']:.3f}, M {c['m']:.3f}, edge {100*c['edge']:.1f}c, "
            f"ROC {100*c['roc']:.1f}%, ${coll:.2f} locked, "
            f"${self.locked():.2f} total)")

    # ------------------------------------------------------------- watching
    async def mark_loop(self):
        """Log every position's mark when its match resolves.

        WINNER_TAKER.md holds to settlement and says so, but the reason to
        hold is a measured +3.59c and the reason NOT to (a comeback marks the
        position badly) has never been observed -- 0 of 425 takes settled YES.
        Marking at match resolution is how that gets settled from live fills
        rather than argued, whether or not cover_on_recover is ever switched on.
        """
        loop = asyncio.get_running_loop()
        while not self.stop:
            await asyncio.sleep(MARK_POLL)
            try:
                for tk, pos in list(self.pos.items()):
                    mt = pos.get("match_tk")
                    if not mt or pos.get("marked"):
                        continue
                    wb = self.books.get(tk)
                    mb = self.books.get(mt)
                    m = mb.mid() if mb else None

                    why = None
                    if m is not None and m > COMPET:
                        why = "match_recovered"
                    else:
                        # The match book stops publishing when it settles, so
                        # its mid freezes rather than going to 0 or 1 -- the
                        # result has to be asked for. This is also the only
                        # path that still fires once the match group has aged
                        # out of discovery and the Leg is gone: the mark is a
                        # property of the POSITION, and the doc's "every
                        # position logs its mark at match resolution" has to
                        # hold for the ones we stopped watching too.
                        d = await loop.run_in_executor(
                            self.pool, get, f"/markets/{mt}")
                        mm = (d or {}).get("market", {})
                        if mm.get("result") in ("yes", "no"):
                            why = f"match_settled_{mm['result']}"
                    if not why:
                        continue
                    pos["marked"] = True
                    avg = (sum(p * n for p, n in pos["prices"])
                           / max(pos["count"], 1))
                    bid, ask = (wb.bid(), wb.ask()) if wb else (None, None)
                    mark = ask if ask is not None else bid
                    self.jlog({"a": "mark", "why": why, "tk": tk,
                               "match_tk": mt,
                               "mmid": None if m is None else round(m, 4),
                               "avg_price": round(avg, 4),
                               "count": pos["count"], "wbid": bid, "wask": ask,
                               "mtm": None if mark is None else
                               round(pos["cash"] - pos["count"] * mark, 2)})
                    log(f"MARK {tk} ({why}): short {pos['count']:.0f} @{avg:.2f}, "
                        f"leg now {bid}/{ask}")
                    if why == "match_recovered" and self.cfg["cover_on_recover"]:
                        await self.cover(tk, pos)
            except Exception as e:
                log(f"mark error: {type(e).__name__} {e}")

    async def cover(self, tk, pos):
        """Buy back at the ask. Off by default: covering means crossing an 8c
        book, and converting a rare large loss into a frequent medium one is
        not supported by anything measured."""
        wb = self.books.get(tk)
        a = wb.ask() if wb else None
        if a is None or pos["count"] < 1:
            return
        n = int(pos["count"])
        body = {"ticker": tk, "client_order_id": f"{OID_PREFIX}{uuid.uuid4()}",
                "side": "bid", "count": f"{n}.00", "price": f"{a:.4f}",
                "time_in_force": "immediate_or_cancel",
                "self_trade_prevention_type": "maker", "post_only": False}
        if not self.live:
            self.jlog({"a": "cover", "tk": tk, "price": a, "count": n})
            self.pos.pop(tk, None)
            return
        loop = asyncio.get_running_loop()
        st, resp = await loop.run_in_executor(
            self.order_pool, signed, "POST", f"{API}/portfolio/events/orders",
            body)
        o = resp.get("order", resp) if isinstance(resp, dict) else {}
        filled = float(o.get("fill_count_fp") or 0)
        self.jlog({"a": "cover", "tk": tk, "price": a, "count": n,
                   "filled": filled, "st": st})
        if filled >= pos["count"] - 1e-6:
            self.pos.pop(tk, None)

    async def settle_loop(self):
        """Release collateral and realize P&L once a winner event settles.

        A settled market stops publishing deltas, so its book freezes; without
        this the position would reserve capital forever against something that
        can no longer move.
        """
        loop = asyncio.get_running_loop()
        while not self.stop:
            await asyncio.sleep(SETTLE_POLL)
            try:
                for tk, pos in list(self.pos.items()):
                    d = await loop.run_in_executor(
                        self.pool, get, f"/markets/{tk}")
                    m = (d or {}).get("market", {})
                    res = m.get("result")
                    if res not in ("yes", "no"):
                        continue
                    # short: we keep the premium if it resolves NO, and pay
                    # $1/contract if it resolves YES
                    realized = pos["cash"] - (pos["count"] if res == "yes" else 0.0)
                    self.realized += realized
                    self.settled_tks.add(tk)
                    self.pos.pop(tk, None)
                    self.jlog({"a": "settle", "tk": tk, "result": res,
                               "count": pos["count"],
                               "realized": round(realized, 2),
                               "released": round(pos["collateral"], 2)})
                    log(f"SETTLE {tk} -> {res}: {realized:+.2f} realized, "
                        f"${pos['collateral']:.2f} collateral released")
            except Exception as e:
                log(f"settle error: {type(e).__name__} {e}")

    # ------------------------------------------------------------ plumbing
    def tickers(self):
        out = set()
        for leg in self.legs.values():
            out.add(leg.win_tk)
            out.add(leg.match_tk)
        # keep held legs subscribed even after their match group ages out, so
        # marks and covers have a live book to read
        out |= set(self.pos)
        return sorted(out)

    def _make_leg(self, tour, key, comp, code, d, win_tk, win_ev):
        leg = Leg(tour, key, comp, code, d["match"], win_tk, win_ev,
                  d.get("name", ""))
        prior = self.rstate.get(leg.win_tk)
        # Only this leg's own samples, from this same match.
        if prior and prior.get("match_tk") == leg.match_tk:
            # sorted(): a checkpoint written before the samples were kept in
            # order would silently give a wrong median, which is a wrong fair
            # value.
            for v, n in prior.get("rsamp") or []:
                if v not in leg.rsamp:
                    bisect.insort(leg._rkeys, v)
                    leg.rsamp[v] = 0
                leg.rsamp[v] += n
            leg.rn = prior.get("rn") or sum(leg.rsamp.values())
            log(f"  {leg.code} {leg.win_tk}: recovered {leg.rn} R "
                f"observations ({len(leg._rkeys)} distinct) from checkpoint")
        self.legs[leg.win_tk] = leg
        self.legs_by_match[leg.match_tk].append(leg)
        return leg

    async def discovery_loop(self):
        loop = asyncio.get_running_loop()
        while not self.stop:
            try:
                newly, alive = await loop.run_in_executor(
                    self.pool, self.disc.scan, set(self.groups))
                now = time.time()
                for gk in alive:
                    if gk in self.groups:
                        self.groups[gk]["last_alive"] = now
                changed = False
                for tour, key, comp, players in newly:
                    legs = []
                    # One player now has several sellable legs -- the
                    # tournament winner and each round qualifier still open.
                    # They share a match, a name and a per_leg_cap bucket, and
                    # differ only in what they pay and when they settle.
                    for code, d in players.items():
                        for win_tk, win_ev in d["legs"]:
                            legs.append(self._make_leg(
                                tour, key, comp, code, d, win_tk, win_ev))
                    self.groups[(tour, key)] = {"legs": legs, "last_alive": now}
                    # One line per match, one clause per player: the legs
                    # of a player share a match ticker and a name, so
                    # repeating those once per leg only buries the tickers.
                    byplayer = {}
                    for l in legs:
                        byplayer.setdefault(l.name or l.code, []).append(l)
                    # WATCHING, not IN PLAY. Subscribing happens PREROLL
                    # before the first ball so that R exists by the time it
                    # matters, so this line is a plan, not an observation.
                    st = self.disc.schedule().get(f"KX{tour}MATCH-{key}")
                    when = (time.strftime("%H:%MZ", time.gmtime(st)) if st
                            else "unscheduled")
                    log(f"WATCHING {comp} {key} (starts {when}): " + ", ".join(
                        f"{who} ({ls[0].match_tk} -> "
                        + " ".join(l.win_tk for l in ls) + ")"
                        for who, ls in byplayer.items()))
                    changed = True
                # ...and IN PLAY when the exchange says a ball has been
                # struck, which is the line that counts matches actually
                # played AND the moment R stops moving. Print what it froze
                # at: this is the whole input to every fair value the match
                # will produce, and it is the last chance to see it before it
                # is used. The rungs should descend -- a player is likelier to
                # reach the quarters than to win the thing -- so a ladder out
                # of order is a junk book, visible at a glance.
                for gk in sorted(self.disc.started - self.announced):
                    g = self.groups.get(gk)
                    if not g:
                        continue
                    self.announced.add(gk)
                    byp = {}
                    for l in g["legs"]:
                        byp.setdefault(l.name or l.code, []).append(l)
                    log(f"IN PLAY {gk[0]} {gk[1]} -- R frozen: " + "; ".join(
                        f"{who} " + " ".join(
                            f"{rung(l.win_tk)}="
                            + (f"{l.fair(1.0):.4f}" if l.rn >= MIN_R else "-")
                            for l in sorted(
                                ls, key=lambda x: RUNGS.index(rung(x.win_tk))))
                        + f" ({max(l.rn for l in ls)} obs)"
                        for who, ls in byp.items()))
                self.announced &= set(self.groups)
                for gk in list(self.disc.closed):
                    g = self.groups.pop(gk, None)
                    if g:
                        for leg in g["legs"]:
                            self.legs.pop(leg.win_tk, None)
                            self.legs_by_match.pop(leg.match_tk, None)
                        log(f"MATCH OVER {gk[0]} {gk[1]}")
                        changed = True
                if changed:
                    self.dirty.set()
            except Exception as e:
                log(f"discovery error: {type(e).__name__} {e}")
            await asyncio.sleep(DISCOVERY_INTERVAL)

    async def ws_loop(self):
        sub = 0
        while not self.stop:
            tks = self.tickers()
            if not tks:
                self.dirty.clear()
                try:
                    await asyncio.wait_for(self.dirty.wait(), timeout=30)
                except asyncio.TimeoutError:
                    pass
                continue
            try:
                async with websockets.connect(
                        WS_HOST + WS_PATH, ping_interval=10, ping_timeout=30,
                        **{_HDR: ws_headers()}) as ws:
                    self.dirty.clear()
                    sub += 1
                    await ws.send(json.dumps({
                        "id": sub, "cmd": "subscribe",
                        "params": {"channels": ["orderbook_delta"],
                                   "market_tickers": tks}}))
                    log(f"subscribed to {len(tks)} tickers")
                    while not self.stop:
                        if self.dirty.is_set():
                            break
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            continue
                        self.msgs += 1
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            continue
                        typ = msg.get("type")
                        if typ not in ("orderbook_snapshot", "orderbook_delta"):
                            continue
                        m = msg["msg"]
                        tk = m.get("market_ticker")
                        if not tk:
                            continue
                        t = time.time()
                        b = self.books[tk]
                        if typ == "orderbook_snapshot":
                            b.snapshot(m, t)
                        else:
                            b.delta(m, t)
                        # The entry path. Not a timer -- see on_book().
                        self.on_book(tk)
            except Exception as e:
                log(f"ws error: {type(e).__name__} {str(e)[:200]}; retry in 5s")
                await asyncio.sleep(5)

    async def status_loop(self):
        while not self.stop:
            await asyncio.sleep(STATUS_EVERY)
            r = sum(1 for l in self.legs.values() if l.rn >= MIN_R)
            # The discovery note matters most when nothing is in play: without
            # it a quiet bot and a broken bot log exactly the same thing.
            log(f"status: [{self.disc.note}] {len(self.groups)} matches, "
                f"{len(self.legs)} legs "
                f"({r} with R), {self.msgs:,} msgs, {self.takes} takes, "
                f"${self.locked():,.2f}/{self.cfg['hard_cap']:,.0f} locked, "
                f"realized ${self.realized:+,.2f}")

    async def run(self):
        log(f"winner_taker starting ({'LIVE' if self.live else 'paper'}), "
            f"cap ${self.cfg['hard_cap']:,.0f}, per-leg "
            f"${self.cfg['per_leg_cap']:,.0f}, per-event "
            f"${self.cfg['per_event_cap']:,.0f}, per-day "
            f"${self.cfg['per_day_cap']:,.0f}, eliminated "
            f"${self.cfg['elim_cap']:,.0f}, min_edge {self.cfg['min_edge']}")
        self.dirty = asyncio.Event()
        await asyncio.get_running_loop().run_in_executor(self.pool, self.restore)
        if self.stop:
            return
        await asyncio.gather(self.discovery_loop(), self.ws_loop(),
                             self.housekeeping_loop(), self.mark_loop(),
                             self.settle_loop(), self.state_loop(),
                             self.balance_loop(), self.status_loop())


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("mode", choices=("paper", "live"))
    ap.add_argument("--cap", type=float, default=None,
                    help="override hard_cap for this session and WRITE it to "
                         "the config, so a later edit starts from what ran")
    args = ap.parse_args()
    bot = WinnerTaker(args.mode == "live")
    if args.cap is not None:
        bot.cfg.v["hard_cap"] = args.cap
        with open(CONFIG_PATH, "w") as f:
            json.dump(bot.cfg.v, f, indent=2)
        bot.cfg.mtime = os.path.getmtime(CONFIG_PATH)

    def bye(*_):
        # Nothing rests, so there is nothing to cancel: an IoC is done by the
        # time we hear about it. Stopping is just stopping.
        bot.save_state()        # R is the one thing a restart cannot rebuild
        log(f"stopped: {bot.takes} takes, ${bot.locked():,.2f} still locked "
            f"across {len(bot.pos)} legs")
        sys.exit(0)

    for s in (signal.SIGTERM, signal.SIGINT):
        signal.signal(s, bye)
    # asyncio.run(), not get_event_loop().run_until_complete(): the VPS is on
    # Python 3.14, where get_event_loop() with no running loop is an error
    # rather than the deprecation warning it was in 3.12. asyncio.run() has
    # been correct since 3.7, so this is right on the laptop's 3.8 too.
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
