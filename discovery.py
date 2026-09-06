"""Find live matches and pair each player with their tournament-winner leg.

Adapted from ../kalshi-tennis/collector_ws.Discovery, which does this for
CAPTURE -- it subscribes to every leg family (set winner, exact score, game
total, game spread, aces) and, for tournament legs, deliberately grabs every
event a player code appears in, leaving the analysis to sort out which one
belonged to the match. That is the right call for a recorder and the wrong one
for a bot: this needs exactly two books per player, the right ones, now.

Pairing needs BOTH of the following, and the second one cost real money to
learn:

1. The EVENT's `product_metadata.competition` string. A player code identifies
   the PLAYER, so someone in a live Cincinnati match carries their US Open
   winner leg in the same breath; pairing on the code alone shorts the wrong
   tournament. build_tourn_map.py reached this offline, TOURNAMENT_WINNER.md
   before it.

2. The player's NAME. Competition is necessary and NOT sufficient, because
   Kalshi labels US Open QUALIFYING matches with the same competition string
   as the main draw, and three-letter codes are only unique within an event.
   Measured live 2026-08-26:

       KXATPMATCH-26AUG24MEJDRA-DRA   "Liam Draxl"     (qualifier)
       KXATP-26USO-DRA                "Jack Draper"

   Both are "US Open Men Singles" and both are `-DRA`. Joining on code plus
   competition pairs a qualifier's collapsing match to a top seed's winner
   leg, and the strategy's entire trigger is a match collapsing. That is a
   trade that sells Jack Draper because Liam Draxl is losing.

So the code is used to find CANDIDATE legs and the name decides. A code whose
names disagree is dropped and logged loudly -- it is the exact shape of the
bug above, and it should never pass silently.

Competition strings and names are read live rather than from tourn_map.json,
so a US Open match listed after the map was built still pairs correctly.
tourn_map stays what it always was: an offline artefact for replaying capture.
"""
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from kalshi import get, get_v1, paginate

# Three rules for "is this match on" have been tried and discarded here, all
# three failing silently and against exactly the matches worth trading. They
# are kept below because each one looks reasonable until it is measured.
#
# 1. `expected_expiration_time` is a nominal per-day placeholder, not a
#    scheduled end: every US Open match on a given day carries 18:00Z that day.
#    The 2026 draw ran days behind it. Measured 2026-09-04:
#
#      KXATPMATCH-26SEP02AUGKHA  expected 2026-09-02T18:00Z,
#                                actually played 2026-09-03 17:00-18:30Z (+24.5h)
#      KXWTAMATCH-26AUG30KEYKOR  expected 2026-08-30T18:00Z,
#                                actually settled 2026-09-01 17:25Z     (+47.4h)
#
#    A window of expected-7h..expected+12h therefore dropped both matches from
#    the candidate list BEFORE they were played. Felix Auger-Aliassime's loss
#    and both of Madison Keys' completed matches were never watched at all.
#
# 2. The candle gate -- 3 of the last 10 one-minute candles moving >=1c -- is
#    unreachable for most match markets, because the candlestick endpoint does
#    not emit a candle per minute. Replaying the exact 10-minute call that
#    action_score used to make, across the Auger-Aliassime match: 25 of 27
#    windows returned fewer than FOUR priced candles, and 3 moves needs 4
#    candles. The gate was arithmetically impossible however violently the
#    match swung. It measured candle density, not play. That market traded
#    5,455,427 contracts; its candles accounted for 35,878 of them.
#
# Between them those two rules let 60 of the ~250 US Open singles matches be
# watched, and none of the misses said anything in the log.
#
# 3. Traded volume has neither problem, and `volume_fp` is already carried in
#    the market LIST that open_markets() fetches -- so it costs no extra HTTP
#    call, which retired the per-candidate candle call that produced 2,094 HTTP
#    429s in a day and five scans that threw outright. It is still the fallback
#    below. It is no longer the rule, because it cannot tell a match being
#    PLAYED from a market being repriced; see the note on IN_PLAY_VOL.
#
# The separation is not marginal. Contracts traded in one 60s window, by time
# before the match ended, over five US Open matches (measured 2026-09-04):
#
#      T-8h       0      0      0      7     19
#      T-4h       0      0      4     81    514
#      T-1h       2   1178   3680   4813  68973
#      T-30m  13339  65765  79889  83603 135969
#
# A match being played is three orders of magnitude clear of one merely listed.
# 300 contracts a minute sits in the empty band between them, and erring low is
# right: subscribing early costs one websocket subscription, while subscribing
# late costs the fair value itself, because R exists only while the match is
# still competitive.
#
# This also retires the zombie guard as a separate rule. Kalshi leaves finished
# matches in the open list -- 38 of the 39 candidates in a scan on 2026-09-01
# had ended 18.6h to 43.6h earlier, status "active", result "", close_time two
# weeks out -- and every one of them fails this gate for free, because a match
# that is over is not trading.
IN_PLAY_VOL = 300.0         # contracts traded per 60s -- the FALLBACK only
IN_PLAY_RATE = IN_PLAY_VOL / 60.0        # ...as a rate, so scan jitter is safe
EVENT_TTL = 600             # competition maps change only when a draw is listed

# Volume is no longer the primary rule. It could not tell a match being played
# from a market merely being repriced, and it was wrong far more often than it
# was right. Measured 2026-09-05 at 15:35Z, of the 24 matches it had called in
# play, exactly THREE had a book that moved at all in the previous hour; the
# other 21 were frozen to the tick. Pegula vs Cirstea was called in play on a
# market that had traded 4,288 contracts in its entire life -- one ordinary
# order clears 300 in a minute.
#
# The exchange states both facts outright, so ask it instead:
#
# 1. WHEN a match is scheduled -- /milestones, documented, and one call covers
#    every tennis match on the calendar. `start_date` is a real per-match time
#    that is maintained as the order of play slips, not the 18:00Z placeholder
#    that expected_expiration_time carries. Checked against the three matches
#    that started on 2026-09-05: 15:00 vs 15:00:56, 15:00 vs 15:00:56, 15:30 vs
#    15:30:53. Its sibling `details.status` is USELESS -- it read "not_started"
#    for all 24 open events including the three that were live.
#
# 2. WHETHER it has started -- the v1 events API, undocumented, carrying the
#    live label the Kalshi app itself shows. See match_phases().
#
# Watching starts PREROLL before the scheduled time, because R only exists if
# we were already there: R is sampled while the match is competitive, and a bot
# that arrives mid-collapse has nothing to price with. 45 minutes is far more
# than the 50 samples MIN_R needs and costs only a websocket subscription.
PREROLL = 45 * 60           # subscribe this long before the scheduled start
SCHED_TTL = 300             # the order of play slips through the day
SCHED_LOOKBACK_H = 18       # keep matches that began before the last restart
PHASE_TTL = 60              # the live label is the thing we want promptly


def norm_name(s):
    return " ".join((s or "").split()).casefold()


def name_key(s):
    """(surname, first initial) -- the relaxed form, used only as a fallback.

    The two books do not always spell a player the same way. Measured across
    the 25 legs the allocation study traded:

        'Catherine McNally'  vs  'Caty McNally'    same person
        'Daniil Medvedev'    vs  'Hamad Medjedovic'  NOT
        'Botic Van de Zandschulp' vs 'Luca Van Assche'  NOT
        'Peyton Stearns'     vs  'Sloane Stephens'   NOT

    Surname plus first initial separates all four correctly, where exact
    matching would decline the McNally pair and lose a real trade. It is only
    ever consulted when the exact match fails, and only when it identifies
    exactly ONE candidate -- two players sharing a surname and an initial in
    one draw is rare but is not something to guess at.
    """
    parts = norm_name(s).split()
    if not parts:
        return None
    return (parts[-1], parts[0][0])


class Discovery:
    """Blocking REST logic; run it in a thread pool."""

    def __init__(self, tours=("ATP", "WTA"), log=print):
        self.tours = tours
        self.log = log
        self._mkts = {}          # series -> (fetched_at, markets)
        self._events = {}        # series -> (fetched_at, {event: competition})
        self._vol = {}           # (tour,key) -> (sampled_at, cumulative volume)
        self._nolegs = set()     # in play but unpairable; log those once, not per scan
        self._sched = None       # (fetched_at, {event_ticker: start epoch})
        self._phase = None       # (fetched_at, {event tickers that have begun})
        self._phase_warned = False
        self.closed = set()
        self.started = set()     # (tour,key) the exchange says has begun
        self.note = ""

    def _rate(self, gk, volume, sampled_at):
        """Contracts per second since the last DIFFERENT sample of this group.

        Returns None for "this scan learned nothing" -- the first sighting of a
        match, or a scan that re-read a cached market list already counted --
        which is not the same as "not trading" and must never drop a live
        match. The cost is that a match already in play when the bot starts is
        found one scan late; the alternative is guessing from a book that looks
        identical before the first ball.
        """
        prev = self._vol.get(gk)
        if prev is not None and sampled_at <= prev[0]:
            return None
        self._vol[gk] = (sampled_at, volume)
        if prev is None:
            return None
        dt = sampled_at - prev[0]
        if dt <= 0:
            return None
        return max(0.0, volume - prev[1]) / dt

    @staticmethod
    def _ts(v):
        """ISO-8601 -> epoch, or None. Kalshi mixes 'Z' and '+00:00'."""
        if not v:
            return None
        try:
            return datetime.fromisoformat(
                v.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None

    def schedule(self, ttl=SCHED_TTL):
        """event_ticker -> scheduled start, as an epoch.

        ONE documented call covers every tennis match on the calendar, so this
        is cheaper than the per-candidate candle call it ultimately replaces.

        `minimum_start_date` is the filter that works. `start_date_min` is
        accepted and SILENTLY IGNORED -- it returns 2025 rows, which would look
        like a schedule and schedule nothing.

        A failure here returns the previous answer rather than an empty one:
        an empty schedule reads as "nothing is due", which would unsubscribe
        the whole day.
        """
        c = self._sched
        if c and time.time() - c[0] < ttl:
            return c[1]
        since = (datetime.now(timezone.utc)
                 - timedelta(hours=SCHED_LOOKBACK_H))
        try:
            ms = paginate("/milestones", "milestones", limit=200,
                          type="tennis_tournament_singles",
                          minimum_start_date=since.strftime(
                              "%Y-%m-%dT%H:%M:%SZ"))
        except Exception as e:
            self.log(f"schedule: {type(e).__name__} {e} -- keeping last")
            return c[1] if c else {}
        out = {}
        for m in ms:
            ev = (m.get("details") or {}).get("main_game_event_ticker")
            t = self._ts(m.get("start_date"))
            if ev and t:
                out[ev] = t
        if not out:
            self.log("schedule: milestones returned no usable start times "
                     "-- keeping last")
            return c[1] if c else {}
        self._sched = (time.time(), out)
        return out

    def match_phases(self, ttl=PHASE_TTL):
        """Event tickers the exchange says have actually STARTED.

        `product_metadata.sports_paging_actual_start.phase` on the v1 events
        API is the live label the Kalshi app shows, and the only place the
        exchange admits a match is under way. The key is ABSENT until the match
        starts, then appears and is never cleared, so this means "has begun",
        not "is on court now" -- which is what this strategy wants, because the
        legs are sold AFTER the player is beaten, not during.

        Undocumented, so it is never load-bearing on its own: an empty answer
        just leaves the schedule in charge, and a page of events carrying no
        such key at all says so once and loudly, because that is what a shape
        change looks like.
        """
        c = self._phase
        if c and time.time() - c[0] < ttl:
            return c[1]
        out, events, keyed = set(), 0, 0
        for tour in self.tours:
            try:
                d = get_v1("/events", series_ticker=f"KX{tour}MATCH",
                           status="open", limit=200) or {}
            except Exception as e:
                self.log(f"phase {tour}: {type(e).__name__} {e}")
                continue
            for e in d.get("events") or []:
                events += 1
                sp = ((e.get("product_metadata") or {})
                      .get("sports_paging_actual_start")) or {}
                if sp:
                    keyed += 1
                if sp.get("phase") == "live":
                    out.add(e.get("ticker"))
        if events and not keyed and not self._phase_warned:
            self._phase_warned = True
            self.log(f"phase: {events} open events and NOT ONE carries "
                     f"sports_paging_actual_start -- the v1 shape has "
                     f"changed; falling back to the schedule alone")
        self._phase = (time.time(), out)
        return out

    def open_markets(self, series, ttl=60):
        c = self._mkts.get(series)
        if c and time.time() - c[0] < ttl:
            return c[1]
        m = paginate("/markets", "markets", series_ticker=series,
                     status="open", limit=1000)
        self._mkts[series] = (time.time(), m)
        return m

    def competitions(self, series):
        """event_ticker -> product_metadata.competition, for one series."""
        c = self._events.get(series)
        if c and time.time() - c[0] < EVENT_TTL:
            return c[1]
        out = {}
        for status in ("open", None):
            p = dict(series_ticker=series, limit=200)
            if status:
                p["status"] = status
            for e in paginate("/events", "events", **p):
                comp = (e.get("product_metadata") or {}).get("competition")
                if comp:
                    out[e["event_ticker"]] = comp
        self._events[series] = (time.time(), out)
        return out

    def sellable_legs(self, tour, comp, players):
        """Every leg of each player in THIS competition that we can sell.

        `players` is {code: name} from the match markets. Returns
        {code: [(ticker, event_ticker), ...]}. A player with no open leg is
        omitted rather than guessed -- a qualifier who is not in the winner
        market simply has no trade here, which is the common case and not an
        error.

        TWO families, not one. Alongside the tournament winner, Kalshi lists
        round qualifiers -- "will X qualify for the Quarterfinals / Semifinals
        / Final" -- under KX{tour}ADVANCE, and they carry the same competition
        string, so they pair on exactly the machinery below. They are where
        the liquidity is. Measured 2026-09-05 across the open US Open men's
        legs, books quoting BOTH sides, and the median spread:

            winner   11 of 30 two-sided, median  1c
            QUAR     15 of 16 two-sided, median 34c
            SEMI     11 of 15 two-sided, median  7c
            FIN      11 of 15 two-sided, median  5c

        The winner book is the tight one and the empty one; the qualifiers
        quote three times as often and far wider. Both halves of that matter:
        the quoting is the opportunity, and the width is why `winner_taker`
        guards R sampling with `MAX_R_SPREAD` -- a 34c-wide book has a mid,
        and its mid means nothing.

        The EVENT ticker is returned rather than left to be sliced off the
        market ticker, because Kalshi uses two market-ticker shapes inside one
        event and the slice gets a different answer for each. Measured
        2026-09-04, both of these are event KXATP-26USO:

            KXATP-26USO-ZVE   "Will Alexander Zverev win the US Open Men's..."
            KXATP-26-SWE      "Will Dane Sweeny win the US Open Men Singles..."

        `rsplit("-", 1)[0]` calls those two different events, which split one
        draw into two per_event_cap buckets. The exchange states the event
        outright in the market list; use what it says.

        The name is the authority. Matching on the code alone paired Liam
        Draxl's qualifying match to Jack Draper's winner leg (see the module
        docstring); a code collision now drops the pair and says so.
        """
        exact = {norm_name(n): c for c, n in players.items() if n}
        loose = defaultdict(list)
        for c, n in players.items():
            k = name_key(n)
            if k:
                loose[k].append(c)
        legs = defaultdict(list)
        for series in (f"KX{tour}", f"KX{tour}ADVANCE"):
            events = {ev for ev, c in self.competitions(series).items()
                      if c == comp}
            if not events:
                continue
            self._pair_series(series, events, comp, players, exact, loose, legs)
        return dict(legs)

    def _pair_series(self, series, events, comp, players, exact, loose, legs):
        """Pair one series' open markets into `legs`. Name is the authority."""
        for m in self.open_markets(series):
            if m["event_ticker"] not in events:
                continue
            tk = m["ticker"]
            code = tk.rsplit("-", 1)[-1]
            name = m.get("yes_sub_title") or m.get("subtitle") or ""
            c = exact.get(norm_name(name))
            if c is None:
                # Fall back to surname + initial, and only when it is
                # unambiguous. Two candidates means we do not know, and not
                # knowing is a decline.
                cands = loose.get(name_key(name) or ("", ""), [])
                if len(cands) == 1:
                    c = cands[0]
                    self.log(f"paired {tk} {name!r} to match {c} "
                             f"{players[c]!r} on surname+initial")
            if c is not None:
                legs[c].append((tk, m["event_ticker"]))
            elif code in players:
                # Same code, different person. This is the Draxl/Draper case
                # and it is the one failure here that trades in the wrong
                # direction, so it is never silent.
                self.log(f"CODE COLLISION in {comp}: match {code} is "
                         f"{players[code]!r} but {tk} is {name!r} -- not paired")

    def scan(self, live_keys):
        """-> (newly_live, still_alive). Sets self.closed.

        newly_live: [(tour, key, comp,
                      {code: {"match": tk, "name": str,
                              "legs": [(ticker, event_ticker), ...]}})]
        still_alive: {(tour, key)} that should stay subscribed
        closed: live groups whose match market left the open list, which is
                the exchange saying the match is OVER. Absence from `alive`
                cannot carry that -- a match drops out of `alive` merely by
                trading quietly for one scan.
        """
        newly, alive = [], set()
        open_gks, confirmed = set(), set()
        scanned = live = 0
        now = time.time()
        sched, begun = self.schedule(), self.match_phases()
        for tour in self.tours:
            series = f"KX{tour}MATCH"
            mkts = self.open_markets(series)
            # The list's OWN fetch time, not now(): open_markets caches for 60s
            # and the discovery interval is 60s, so a scan can be handed a list
            # it has already counted. Measuring against the fetch time makes a
            # repeat read a no-op instead of a phantom zero-volume minute.
            sampled_at = self._mkts[series][0]
            by_key, meta, name_of = defaultdict(list), {}, {}
            volume = defaultdict(float)
            for m in mkts:
                key = m["event_ticker"].split("-")[1]
                by_key[key].append(m["ticker"])
                meta[key] = m
                name_of[m["ticker"]] = (m.get("yes_sub_title")
                                        or m.get("subtitle") or "")
                # Both sides of a match trade; summing them keeps the gate from
                # depending on which player's market the exchange lists first.
                volume[key] += float(m.get("volume_fp") or 0)
            if by_key:
                confirmed.add(tour)
            open_gks.update((tour, k) for k in by_key)
            comps = self.competitions(series)
            scanned += len(by_key)
            for key, tickers in by_key.items():
                gk = (tour, key)
                ev = meta[key]["event_ticker"]
                rate = self._rate(gk, volume[key], sampled_at)
                hot = rate is not None and rate >= IN_PLAY_RATE
                start = sched.get(ev)
                # Three sources, in order of how much they are trusted. The
                # schedule says when to arrive, the live label says whether it
                # actually happened, and volume is the fallback for a match
                # neither one knows about -- a new series, or a milestone feed
                # that stopped answering.
                if ev in begun:
                    self.started.add(gk)
                elif start is not None and hot and now >= start:
                    # Scheduled, past its time, and trading hard: the v1 label
                    # is late or gone, but this match is plainly under way.
                    self.started.add(gk)
                if gk in self.started:
                    live += 1
                due = start is not None and now >= start - PREROLL
                # No schedule for this match at all -- fall back to the old
                # rule rather than never watching it.
                watch = due or gk in self.started or (start is None and hot)
                if gk in live_keys:
                    alive.add(gk)
                    continue
                if not watch:
                    continue
                comp = comps.get(ev)
                if not comp:
                    if gk not in self._nolegs:
                        self._nolegs.add(gk)
                        self.log(f"{key} due but no competition string; "
                                 f"not watching")
                    continue
                codes = {t.rsplit("-", 1)[-1]: t for t in tickers}
                names = {t.rsplit("-", 1)[-1]:
                         (name_of.get(t) or "") for t in tickers}
                legs = self.sellable_legs(tour, comp, names)
                if not legs:
                    # Common and not an error -- two qualifiers have no winner
                    # leg between them. Said ONCE per match all the same: this
                    # branch was silent, and a silent decline here is
                    # indistinguishable from a match that was never seen.
                    if gk not in self._nolegs:
                        self._nolegs.add(gk)
                        self.log(f"{key} ({comp}) due but neither player "
                                 f"has an open leg to sell; not watching")
                    continue
                players = {c: {"match": codes[c], "legs": ls,
                               "name": names.get(c, "")}
                           for c, ls in legs.items()}
                newly.append((tour, key, comp, players))
        self.closed = {gk for gk in live_keys
                       if gk[0] in confirmed and gk not in open_gks}
        # Bound the baselines by the open list. `confirmed` guards the case
        # that matters: a throttled series returns an empty page, and dropping
        # every baseline for it would restart every match's volume clock.
        gone = {gk for gk in set(self._vol) | self._nolegs
                if gk[0] in confirmed and gk not in open_gks}
        for gk in gone:
            self._vol.pop(gk, None)
        self._nolegs -= gone
        self.started -= gone
        self.note = (f"{scanned} open, {live} started, "
                     f"{len(newly)} newly watched")
        return newly, alive
