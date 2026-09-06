"""Offline tests for the entry gates, the caps and the ranking.

Everything here runs against hand-built books with no network and no key. The
parts worth testing are the ones that decide how much money moves: a cap that
silently fails open, or a fair value computed from too few samples, does not
announce itself in a log -- it just trades.

Run: KALSHI_DATA=$(mktemp -d) python3 test_winner_taker.py
"""
import asyncio
import os
import sys
import tempfile

os.environ.setdefault("KALSHI_DATA", tempfile.mkdtemp(prefix="wt-test-"))

import winner_taker as W
from kalshi import Book, fee

FAILED = []


def check(name, cond, detail=""):
    if cond:
        print(f"  ok    {name}")
    else:
        print(f"  FAIL  {name} {detail}")
        FAILED.append(name)


def book(yes=None, no=None, t=1000.0):
    b = Book()
    for p, q in (yes or []):
        b.yes[round(p, 4)] = q
        b.since[round(p, 4)] = t
    for p, q in (no or []):
        b.no[round(p, 4)] = q
    return b


def bot(**cfg):
    b = W.WinnerTaker(live=False)
    b.cfg.v.update(cfg)
    return b


def candidates(b):
    """Every watched leg that passes, best-ROC-first.

    The bot itself no longer collects candidates -- alloc_study measured
    ranking as an exact no-op and any delay as a loss, so entries fire from
    on_book() one leg at a time. The gates are still per-leg and still worth
    testing as a set, so the test builds the list the bot deliberately does
    not.
    """
    out = [c for c, _r in (b.evaluate(l) for l in b.legs.values()) if c]
    out.sort(key=lambda c: -c["roc"])
    return out


def add_leg(b, code="XXX", m_bid=0.04, m_ask=0.06, w_bid=0.08, w_size=500,
            w_ask=0.12, rsamp=None, comp="US Open Men Singles",
            win_event=None, win_tk=None, name=""):
    """Wire one player: a match book, a winner book, and R samples.

    Defaults put the match at mid 0.05 (dying) and the winner leg bid at 8c,
    which is the shape the strategy exists for.
    """
    win = win_tk or f"KXATP-26USO-{code}"
    match = f"KXATPMATCH-26AUG31AAA{code}-{code}"
    leg = W.Leg("ATP", f"26AUG31AAA{code}", comp, code, match, win,
                win_event or "KXATP-26USO", name)
    for r in (rsamp if rsamp is not None else [0.20] * W.MIN_R):
        leg.observe_r(r)
    b.legs[win] = leg
    b.books[match] = book(yes=[(m_bid, 9000)], no=[(round(1 - m_ask, 4), 9000)])
    b.books[win] = book(yes=[(w_bid, w_size)],
                        no=[(round(1 - w_ask, 4), 300)] if w_ask else [])
    # The match has begun. Every gate below DYING assumes a ball has been
    # struck; a leg whose match has not started is a longshot, not a collapse,
    # and is declined before any of this is reached.
    b.disc.started.add((leg.tour, leg.key))
    return leg


print("R and fair value")
b = bot()
leg = add_leg(b, rsamp=[0.20] * (W.MIN_R - 1))
check("fair is None below MIN_R samples", leg.fair(0.05) is None,
      f"got {leg.fair(0.05)}")
leg.observe_r(0.20)
check("fair = M * median(R) at MIN_R", abs(leg.fair(0.05) - 0.01) < 1e-9,
      f"got {leg.fair(0.05)}")
# mean 0.40, median 0.20: a handful of wild W/M readings while the book is
# thin must not move fair, which is why this is a median in the first place
leg.rsamp, leg.rn, leg._rkeys = {}, 0, []
for _r in [0.10] * 25 + [0.20] + [0.90] * 25:
    leg.observe_r(_r)
check("fair uses the MEDIAN, not the mean", abs(leg.fair(0.10) - 0.02) < 1e-9,
      f"got {leg.fair(0.10)}")

print("\nentry gates")
b = bot()
add_leg(b)
c = candidates(b)
check("a dying leg with a rich bid is a candidate", len(c) == 1)
if c:
    exp = 0.08 - 0.05 * 0.20 - fee(0.08)
    check("edge = bid - M*R - fee", abs(c[0]["edge"] - exp) < 1e-9,
          f"got {c[0]['edge']:.5f} want {exp:.5f}")
    check("roc = edge / (1 - bid)",
          abs(c[0]["roc"] - exp / 0.92) < 1e-9)

b = bot()
add_leg(b, m_bid=0.34, m_ask=0.36)
check("competitive match is not a candidate", candidates(b) == [])

b = bot()
add_leg(b, m_bid=0.14, m_ask=0.16)
check("sliding match (0.15) is not a candidate", candidates(b) == [])

b = bot()
add_leg(b, rsamp=[0.20] * (W.MIN_R - 1))
check("no R means no trade, never a guessed fair", candidates(b) == [])

b = bot()
add_leg(b, w_bid=0.02)
_c, _r = b.evaluate(next(iter(b.legs.values())))
check("a 2c bid with no edge is rejected ON EDGE, not on price",
      _c is None and _r.startswith("below-edge"), f"got {_r}")

# The Muchova case, 2026-09-04: a 2c bid against a 0.66c fair. It declined on
# min_price for being cheap, then on min_roc for a holding period the exchange
# does not charge. Both gates are gone; it is a trade.
b = bot()
add_leg(b, w_bid=0.02, rsamp=[0.05] * W.MIN_R)
check("a cheap bid with real edge is now a candidate",
      [c["tk"] for c in candidates(b)] == ["KXATP-26USO-XXX"],
      f"got {[(c['tk'], round(c['roc'], 4)) for c in candidates(b)]}")

b = bot()
add_leg(b, w_bid=0.20, w_ask=0.24)
check("bid above max_price is rejected", candidates(b) == [])

b = bot()
add_leg(b, w_bid=0.03)          # fair 0.01, fee 0.002 -> edge 0.018 but tiny
b.cfg.v["min_edge"] = 0.02
check("edge below min_edge is rejected", candidates(b) == [])

# Vacherot's quarterfinal-qualifier leg, live 2026-09-05: bid 1c x 1,000 while
# the match book put him at 11.5c. Selling a round-qualifier for a tenth of
# what the match says it is worth is the trade min_edge exists to refuse, and
# it is the one an ADVANCE leg can offer that a winner leg rarely does.
b = bot()
add_leg(b, m_bid=0.08, m_ask=0.10, w_bid=0.01, rsamp=[1.0] * W.MIN_R)
_c, _r = b.evaluate(next(iter(b.legs.values())))
check("a bid far below fair is rejected on edge", _c is None
      and _r.startswith("below-edge"), f"got {_r}")

print("\nranking")
b = bot()
add_leg(b, code="AAA", w_bid=0.06)          # fair .01 -> edge ~.046, roc ~.049
add_leg(b, code="BBB", w_bid=0.12, w_ask=0.15)   # edge ~.102, roc ~.116
add_leg(b, code="CCC", w_bid=0.09, w_ask=0.13)   # edge ~.074, roc ~.081
c = candidates(b)
check("all three qualify", len(c) == 3, f"got {len(c)}")
check("sorted by ROC descending",
      [x["tk"].split("-")[-1] for x in c] == ["BBB", "CCC", "AAA"],
      f"got {[x['tk'].split('-')[-1] for x in c]}")
# Ranking by ROC and ranking by edge only disagree narrowly inside this price
# band -- (1-p) runs 0.85 to 0.97, so it can reorder a near-tie and nothing
# more. Construct one of those near-ties explicitly, because it is the only
# case where "prioritise by ROC, not edge" changes any decision at all.
b = bot()
add_leg(b, code="DDD", w_bid=0.14, w_ask=0.17, rsamp=[0.40] * W.MIN_R)
add_leg(b, code="EEE", w_bid=0.15, w_ask=0.18, rsamp=[0.60] * W.MIN_R)
c2 = candidates(b)
by_edge = [x["tk"].split("-")[-1] for x in sorted(c2, key=lambda x: -x["edge"])]
by_roc = [x["tk"].split("-")[-1] for x in c2]
check("this pair ranks differently by edge and by ROC", by_edge != by_roc,
      f"edge {by_edge} roc {by_roc}")
check("candidates() uses the ROC order", by_roc == ["EEE", "DDD"],
      f"got {by_roc}")

print("\ncaps")


def take(b, c):
    # asyncio.run(), not get_event_loop(): on the VPS's 3.14 there is no
    # implicit loop to get and this raises RuntimeError. The bot hit the same
    # thing in main(); the tests must run on the deployed version to catch it.
    asyncio.run(b.take(c))


b = bot(hard_cap=10.0, per_leg_cap=10.0, per_event_cap=10.0, per_day_cap=10.0)
add_leg(b, w_bid=0.08, w_size=10000)
take(b, candidates(b)[0])
check("hard_cap clamps the take", abs(b.locked() - 10.0) < 0.92,
      f"locked ${b.locked():.2f}")
check("size is floor(room / (1-price))",
      b.pos["KXATP-26USO-XXX"]["count"] == int(10.0 / 0.92),
      f"got {b.pos['KXATP-26USO-XXX']['count']}")
check("a full account takes nothing more", candidates(b) == [])

b = bot(hard_cap=1000.0, per_leg_cap=5.0)
add_leg(b, w_bid=0.08, w_size=10000)
take(b, candidates(b)[0])
check("per_leg_cap binds below hard_cap", b.locked() <= 5.0,
      f"locked ${b.locked():.2f}")

b = bot(hard_cap=1000.0, per_leg_cap=1000.0, per_event_cap=6.0)
add_leg(b, code="AAA", w_bid=0.08, w_size=10000)
add_leg(b, code="BBB", w_bid=0.08, w_size=10000)
for c in candidates(b):
    take(b, c)
check("per_event_cap binds across legs of one draw", b.locked() <= 6.0,
      f"locked ${b.locked():.2f}")
check("both legs are in the same event",
      len({p["event"] for p in b.pos.values()}) == 1)

# Kalshi carries two market-ticker shapes inside ONE event -- measured
# 2026-09-04, KXATP-26USO-ZVE and KXATP-26-SWE are both event KXATP-26USO.
# Slicing the event off the market ticker called them different events and
# gave one draw two per_event_cap buckets.
b = bot(hard_cap=1000.0, per_leg_cap=1000.0, per_event_cap=6.0)
add_leg(b, code="AAA", w_bid=0.08, w_size=10000,
        win_tk="KXATP-26USO-AAA", win_event="KXATP-26USO")
add_leg(b, code="SWE", w_bid=0.08, w_size=10000,
        win_tk="KXATP-26-SWE", win_event="KXATP-26USO")
for c in candidates(b):
    take(b, c)
check("two ticker shapes in one event share ONE per_event_cap bucket",
      b.locked() <= 6.0, f"locked ${b.locked():.2f}")
check("...and the short shape is not filed as its own event",
      {p["event"] for p in b.pos.values()} == {"KXATP-26USO"},
      f"got {sorted({p['event'] for p in b.pos.values()})}")

b = bot(hard_cap=1000.0, per_leg_cap=1000.0, per_event_cap=1000.0,
        per_day_cap=7.0)
add_leg(b, code="AAA", w_bid=0.08, w_size=10000)
add_leg(b, code="BBB", w_bid=0.08, w_size=10000)
for c in candidates(b):
    take(b, c)
check("per_day_cap binds", b.locked() <= 7.0, f"locked ${b.locked():.2f}")
check("day budget is spent, not just locked",
      abs(b.by_day[W.now_day()] - b.locked()) < 1e-6)

b = bot(hard_cap=1000.0)
add_leg(b, w_bid=0.08, w_size=7)
take(b, candidates(b)[0])
check("never takes more than the resting size",
      b.pos["KXATP-26USO-XXX"]["count"] == 7)

b = bot(hard_cap=1000.0, max_take=3)
add_leg(b, w_bid=0.08, w_size=9000)
take(b, candidates(b)[0])
check("max_take clamps one order",
      b.pos["KXATP-26USO-XXX"]["count"] == 3)

print("\nthe gates name the binding reason")
# A player is not dying before a ball is struck. Bucsa opened at a match mid of
# 0.055 on 2026-09-05 and sat under DYING for fifteen hours without playing;
# only the accident that R needs competitive play kept that off the tape.
b = bot()
leg = add_leg(b, code="NST", w_bid=0.08)
b.disc.started.discard((leg.tour, leg.key))
c, why = b.evaluate(leg)
check("a longshot whose match has not started is declined",
      c is None and why == "not-started", f"got {why}")
b.disc.started.add((leg.tour, leg.key))
c, why = b.evaluate(leg)
check("...and is a candidate the moment it has", c is not None, f"got {why}")

# no-bid is checked BEFORE no-R. A leg with neither used to report no-R, which
# reads as "we arrived too late to price this" when the truth is "there is
# nothing here to sell" -- and on 2026-09-05 that mis-attribution covered every
# decline of the day.
b = bot()
leg = add_leg(b, code="NBD", w_bid=0.0, w_size=0, rsamp=[])
c, why = b.evaluate(leg)
check("a leg with neither a bid nor R blames the bid, not R",
      c is None and why == "no-bid", f"got {why}")
b = bot()
leg = add_leg(b, code="NOR", w_bid=0.08, rsamp=[0.2] * (W.MIN_R - 1))
c, why = b.evaluate(leg)
check("a leg with a real bid and too little R still blames R",
      c is None and why.startswith("no-R"), f"got {why}")

print("\nan eliminated player is priced at zero, not skipped")
# When the last point is played the loser's match book goes one-sided -- no
# bid, an ask at a cent -- mid() returns None, and every leg used to die at
# no-match-mid. That is the moment of MAXIMUM information: the player is out,
# so the leg is worth exactly zero. Measured on Anisimova 2026-09-05: her book
# went one-sided at 16:48:07 and 157 contracts were still resting at 1c.
b = bot()
leg = add_leg(b, code="OUT", w_bid=0.01, w_size=157, w_ask=0.18)
b.books[leg.match_tk] = book(yes=[], no=[(0.99, 5000)])   # no bid, ask 1c
c, why = b.evaluate(leg)
check("an eliminated player's leg is a candidate, not no-match-mid",
      c is not None, f"got {why}")
check("...priced at fair zero", c and c["fair"] == 0.0, f"got {c}")
check("...earning the bid less the fee",
      c and abs(c["edge"] - (0.01 - W.fee(0.01))) < 1e-9, f"got {c}")
# min_edge would have rejected this: 0.0093 is the MOST a 1c contract can
# earn, so a full cent of required edge makes 1c unreachable at any fair.
check("...which a full cent of min_edge would have refused",
      c and c["edge"] < b.cfg["min_edge"], f"got {c}")

# The same book shape before the first ball is a longshot, not a corpse.
b = bot()
leg = add_leg(b, code="PRE", w_bid=0.01, w_size=157, w_ask=0.18)
b.books[leg.match_tk] = book(yes=[], no=[(0.99, 5000)])
b.disc.started.discard((leg.tour, leg.key))
c, why = b.evaluate(leg)
check("...but the same book before the match starts is NOT elimination",
      c is None and why == "no-match-mid", f"got {why}")

# A one-sided book at a price that is merely low is not a loss.
b = bot()
leg = add_leg(b, code="LOW", w_bid=0.01, w_size=157, w_ask=0.18)
b.books[leg.match_tk] = book(yes=[], no=[(0.90, 5000)])   # ask 10c
c, why = b.evaluate(leg)
check("an ask above ELIMINATED is not an elimination",
      c is None and why == "no-match-mid", f"got {why}")

# The caps still bind: certainty about fair is not certainty about capital.
b = bot(hard_cap=2.0, per_leg_cap=2.0, per_event_cap=2.0, per_day_cap=2.0)
leg = add_leg(b, code="CAP", w_bid=0.01, w_size=5000, w_ask=0.18)
b.books[leg.match_tk] = book(yes=[], no=[(0.99, 5000)])
c, _ = b.evaluate(leg)
take(b, c)
check("an eliminated leg still respects the cap", b.locked() <= 2.0,
      f"locked ${b.locked():.2f}")


print("\nthe comeback caps do not apply once there is no comeback")
# per_leg/per_event/per_day all price ONE thing: the player wins after all.
# When the match is over that risk is settled, and on 2026-09-05 pricing it
# anyway cost 900 of the 937 contracts resting at 1c across Anisimova's legs.
# What still binds is capital, which no reading of a book can create.
def elim_leg(b, code="ELM", size=5000, **kw):
    leg = add_leg(b, code=code, w_bid=0.01, w_size=size, w_ask=0.18, **kw)
    b.books[leg.match_tk] = book(yes=[], no=[(0.99, 5000)])
    return leg

b = bot(hard_cap=1000.0, per_leg_cap=5.0, per_event_cap=5.0, per_day_cap=5.0)
leg = elim_leg(b)
c, why = b.evaluate(leg)
take(b, c)
check("per_leg_cap does not bind an eliminated take", b.locked() > 5.0,
      f"locked ${b.locked():.2f}")

b = bot(hard_cap=20.0, per_leg_cap=5.0, elim_cap=1000.0)
take(b, b.evaluate(elim_leg(b))[0])
check("...but hard_cap still does", 19.0 <= b.locked() <= 20.0,
      f"locked ${b.locked():.2f}")

b = bot(hard_cap=1000.0, per_leg_cap=5.0, elim_cap=30.0)
take(b, b.evaluate(elim_leg(b))[0])
check("...and so does elim_cap", 29.0 <= b.locked() <= 30.0,
      f"locked ${b.locked():.2f}")

check("elim_cap defaults to the whole book",
      bot(hard_cap=400.0).cfg["elim_cap"] == 400.0)

# The regression this was built for: BOTH of a player's legs, which share one
# `risk` key and therefore one per_leg_cap. The winner leg used to spend the
# entire $37.50 before the FIN leg was ever sized, and the FIN leg died on
# no-room -- silently.
b = bot(hard_cap=1000.0, per_leg_cap=37.5, max_take=1000)
win = elim_leg(b, code="ANI", size=780)
fin = elim_leg(b, code="ANI", size=157,
               win_tk="KXATPADVANCE-26USOFIN-ANI",
               win_event="KXATPADVANCE-26USOFIN")
for l in (win, fin):
    c, why = b.evaluate(l)
    check(f"both legs of one eliminated player are takeable ({l.win_tk[-3:]})",
          c is not None, f"got {why}")
    take(b, c)
check("...and both actually fill", len(b.pos) == 2, f"got {sorted(b.pos)}")
check("...for the full resting size of each",
      sum(p["count"] for p in b.pos.values()) == 937,
      f"got {sum(p['count'] for p in b.pos.values())}")

# Eliminated collateral is netted out of the risk caps, or it would re-impose
# the very cap this path skips -- on the NEXT player.
b = bot(hard_cap=1000.0, per_leg_cap=10.0, per_event_cap=10.0, per_day_cap=10.0)
take(b, b.evaluate(elim_leg(b, code="DED", size=5000))[0])
locked_elim = b.locked()
live = add_leg(b, code="LIV", w_bid=0.08, w_size=500)
c, why = b.evaluate(live)
check("eliminated collateral does not consume per_leg_cap for another player",
      c is not None, f"got {why}")
take(b, c)
check("...nor per_day_cap", b.locked() > locked_elim,
      f"locked ${b.locked():.2f} vs ${locked_elim:.2f}")
check("...and it is tracked separately from risk collateral",
      abs(b.locked_elim() - locked_elim) < 1e-9,
      f"elim ${b.locked_elim():.2f} of ${b.locked():.2f}")

# A restart rebuilds positions from exchange fills, which do not say which
# gate produced them. Without the checkpoint the caps stop composing.
b = bot()
take(b, b.evaluate(elim_leg(b, code="SAV"))[0])
check("an eliminated take is checkpointed", b.elim_tks, f"got {b.elim_tks}")
b2 = bot()
check("...and reloaded", b2.load_elim() == b.elim_tks,
      f"got {b2.load_elim()} want {b.elim_tks}")

# The decline that happens AFTER the gates said take used to log nothing.
b = bot(hard_cap=1000.0, per_leg_cap=37.5)
seen = []
b.jlog = seen.append
w = elim_leg(b, code="TWO", size=780)
f2 = elim_leg(b, code="TWO", size=157,
              win_tk="KXATPADVANCE-26USOFIN-TWO",
              win_event="KXATPADVANCE-26USOFIN")
b.cfg.v["elim_cap"] = 5.0
# Both legs are SCORED before either reserves anything, because on_book hands
# takes to ensure_future and the loop body does not await. That is the only
# sequence in which take() reaches its own room check with the room gone --
# and it is the sequence that happened on 2026-09-05.
cands = [b.evaluate(l)[0] for l in (w, f2)]
check("both legs clear the gates before either reserves",
      all(c is not None for c in cands), f"got {cands}")
for c in cands:
    take(b, c)
check("a take declined on room is journalled, not silent",
      any(j.get("why") == "no_room" for j in seen),
      f"got {[j.get('a') or j.get('why') for j in seen]}")

print("\nno ROC floor")
check("min_roc and rise_to are gone from the config",
      "min_roc" not in W.DEFAULTS and "rise_to" not in W.DEFAULTS,
      f"got {sorted(W.DEFAULTS)}")
check("the bot has no floor() left", not hasattr(W.WinnerTaker, "floor"))
# A 2c bid against a 0.5c fair: ROC 1.4%, which the old 0.03 floor declined
# and which is now a trade. This is the Muchova shape.
b = bot()
add_leg(b, m_bid=0.04, m_ask=0.06, w_bid=0.02, w_size=10000,
        rsamp=[0.10] * W.MIN_R)
c = candidates(b)
check("a sub-3% ROC is taken now that the floor is gone",
      len(c) == 1 and c[0]["roc"] < 0.03,
      f"got {[(x['tk'], round(x['roc'], 4)) for x in c]}")

print("\nconfig hot reload")
b = bot()
p = b.cfg.path
import json
import time as _t
check("defaults applied", b.cfg["hard_cap"] == 150.0)
check("per_leg_cap defaults to hard_cap/4", b.cfg["per_leg_cap"] == 37.5)
check("per_event_cap defaults to hard_cap/2", b.cfg["per_event_cap"] == 75.0)
json.dump({"hard_cap": 900.0}, open(p, "w"))
os.utime(p, (_t.time() + 5, _t.time() + 5))
b.cfg.reload()
check("raising hard_cap on disk takes effect", b.cfg["hard_cap"] == 900.0)
check("derived caps follow the new hard_cap", b.cfg["per_leg_cap"] == 225.0)
open(p, "w").write("{not json")
os.utime(p, (_t.time() + 10, _t.time() + 10))
b.cfg.reload()
check("a broken config keeps the previous values, does not crash",
      b.cfg["hard_cap"] == 900.0)

print("\nsettlement")
b = bot()
b.pos["t"] = {"count": 100.0, "collateral": 92.0, "cash": 7.44, "event": "e",
              "match_tk": None, "prices": [(0.08, 100.0)]}
check("collateral is locked while open", abs(b.locked() - 92.0) < 1e-9)
# a leg that settles NO keeps the premium; one that settles YES costs $1 each
b.realized += b.pos["t"]["cash"] - 0.0
b.pos.pop("t")
check("settling NO realizes the premium", abs(b.realized - 7.44) < 1e-9)
check("collateral is released", b.locked() == 0.0)

print("\nR checkpointing")
b = bot()
leg = add_leg(b, code="ZZZ", rsamp=[0.2] * 80)
b.save_state()
b2 = bot()
st = b2.load_state()
e = st.get("KXATP-26USO-ZZZ", {})
check("R survives a restart", e.get("rsamp") == [[0.2, 80]],
      f"got {e.get('rsamp')}")
check("...as one counted ratio, not 80 copies of it", e.get("rn") == 80,
      f"got {e.get('rn')}")
check("checkpoint records the match it came from",
      e["match_tk"] == leg.match_tk)
# ...and the leg rebuilt from it prices identically.
b3 = bot()
b3.rstate = st
leg3 = b3._make_leg("ATP", leg.key, leg.comp, "ZZZ",
                    {"match": leg.match_tk, "name": ""},
                    leg.win_tk, leg.win_event)
check("a leg rebuilt from the checkpoint gives the same fair",
      leg3.fair(0.05) == leg.fair(0.05),
      f"{leg3.fair(0.05)} vs {leg.fair(0.05)}")

# A checkpoint written before R was counted is a flat list of repeats. It must
# still load: the alternative is every recovered leg throwing on the first
# restart after the change, which is the one moment R matters most.

_json = json
_old = {"KXATP-26USO-OLD": {"match_tk": "KXATPMATCH-26AUG31AAAOLD-OLD",
                            "key": "26AUG31AAAOLD", "comp": "c", "code": "OLD",
                            "tour": "ATP", "t": round(_t.time()),
                            "rsamp": [0.10] * 30 + [0.20] * 70}}
open(W.STATE_PATH, "w").write(_json.dumps(_old))
mig = bot().load_state()["KXATP-26USO-OLD"]
check("a pre-count checkpoint is migrated, not dropped",
      mig["rsamp"] == [[0.10, 30], [0.20, 70]], f"got {mig['rsamp']}")
check("...and keeps its observation count", mig["rn"] == 100,
      f"got {mig['rn']}")

# Watching starts PREROLL before the first ball, so a match later today is
# deliberately not held yet. Writing only the held legs dropped R for every
# one of them -- silently, and for exactly the matches still to come.
b4 = bot()
b4.rstate = {"KXWTA-26USO-LATER": {"match_tk": "m", "t": round(_t.time()),
                                   "rsamp": [[0.3, 40]], "rn": 40}}
add_leg(b4, code="NOW", rsamp=[0.2] * 60)
b4.save_state()
kept = bot().load_state()
check("R for a match not yet subscribed survives the checkpoint",
      "KXWTA-26USO-LATER" in kept, f"got {sorted(kept)}")
check("...alongside the leg being watched now", "KXATP-26USO-NOW" in kept)
_stale = {"KXWTA-26USO-STALE": {"match_tk": "m", "rsamp": [[0.3, 40]],
                                "rn": 40, "t": round(_t.time()) - 2 * W.R_MAX_AGE}}
b5 = bot()
b5.rstate = dict(_stale)
add_leg(b5, code="FRESH", rsamp=[0.2] * 60)
b5.save_state()
check("...but a carried-forward leg still expires at R_MAX_AGE",
      "KXWTA-26USO-STALE" not in bot().load_state())

print("\nbook-driven entry")


def pump(b, tk):
    """Deliver one book update and let the take it schedules run.

    on_book() schedules with ensure_future, so it needs a RUNNING loop -- the
    pump has to be inside a coroutine, not around one.
    """
    async def go():
        b.on_book(tk)
        pending = [t for t in asyncio.all_tasks()
                   if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending)
    asyncio.run(go())


b = bot(hard_cap=1000.0)
leg = add_leg(b, w_bid=0.08, w_size=40)
b.legs_by_match[leg.match_tk].append(leg)
pump(b, leg.win_tk)
check("an update on the WINNER book fires the entry",
      b.pos.get(leg.win_tk, {}).get("count") == 40)

b = bot(hard_cap=1000.0)
leg = add_leg(b, w_bid=0.08, w_size=40)
b.legs_by_match[leg.match_tk].append(leg)
pump(b, leg.match_tk)
check("an update on the MATCH book fires it too (fair moved with M)",
      b.pos.get(leg.win_tk, {}).get("count") == 40)

b = bot(hard_cap=1000.0, enabled=False)
leg = add_leg(b, w_bid=0.08, w_size=40)
pump(b, leg.win_tk)
check("enabled=false stops entries", b.pos == {})
# R exists only BEFORE the first ball, which is precisely the window a paused
# bot would otherwise sit out and never get back.
b2 = bot(enabled=False)
leg2 = add_leg(b2, m_bid=0.49, m_ask=0.51, w_bid=0.08, w_ask=0.12)
b2.disc.started.discard((leg2.tour, leg2.key))      # not yet under way
n0 = leg2.rn
b2.sample_r()
check("...but R keeps accruing while disabled", leg2.rn == n0 + 1,
      f"{n0} -> {leg2.rn}")

b = bot(hard_cap=1000.0)
leg = add_leg(b, w_bid=0.08, w_size=40)
b.inflight.add(leg.win_tk)
pump(b, leg.win_tk)
check("a leg with an order already out is not double-fired", b.pos == {})

print("\nconcurrent takes cannot breach a cap")
# Two legs firing at once, each of which would fit on its own. Without a
# reservation both size against the same free capital and the pair overshoots.
b = bot(hard_cap=10.0, per_leg_cap=10.0, per_event_cap=10.0, per_day_cap=10.0)
la = add_leg(b, code="AAA", w_bid=0.08, w_size=10000)
lb = add_leg(b, code="BBB", w_bid=0.08, w_size=10000)
ca, _ = b.evaluate(la)
cb, _ = b.evaluate(lb)


async def both():
    await asyncio.gather(b.take(ca), b.take(cb))


asyncio.run(both())
check("two simultaneous takes stay inside hard_cap", b.locked() <= 10.0,
      f"locked ${b.locked():.2f}")
check("the reservation is released afterwards", b.pending == {},
      f"pending {b.pending}")

b = bot(hard_cap=1000.0)
leg = add_leg(b, w_bid=0.08, w_size=100)
c, _ = b.evaluate(leg)
before = b.room(leg)
b.pending[999] = {"tk": leg.win_tk, "risk": leg.risk, "draw": leg.draw,
                  "day": W.now_day(), "coll": 500.0}
check("a pending reservation reduces room",
      abs(b.room(leg) - (before - 500.0)) < 1e-9)
b.pending.pop(999)

print("\nADVANCE legs are correlated, not independent")
# One player, four sellable legs: the winner and three round qualifiers. They
# all turn on the same match, so one comeback takes all four.
def vacherot(b):
    for tk, ev in [("KXATP-26USO-VAC", "KXATP-26USO"),
                   ("KXATPADVANCE-26USOQUAR-VAC", "KXATPADVANCE-26USOQUAR"),
                   ("KXATPADVANCE-26USOSEMI-VAC", "KXATPADVANCE-26USOSEMI"),
                   ("KXATPADVANCE-26USOFIN-VAC", "KXATPADVANCE-26USOFIN")]:
        add_leg(b, code="VAC", w_bid=0.08, w_size=10000, win_tk=tk,
                win_event=ev, name="Valentin Vacherot")

b = bot(hard_cap=1000.0, per_leg_cap=6.0, per_event_cap=1000.0)
vacherot(b)
check("four legs are watched", len(b.legs) == 4, f"got {len(b.legs)}")
for c in candidates(b):
    take(b, c)
check("a player's four legs share ONE per_leg_cap bucket", b.locked() <= 6.0,
      f"locked ${b.locked():.2f} across {len(b.pos)} legs")

b = bot(hard_cap=1000.0, per_leg_cap=1000.0, per_event_cap=6.0)
vacherot(b)
for c in candidates(b):
    take(b, c)
check("...and all four count against ONE draw", b.locked() <= 6.0,
      f"locked ${b.locked():.2f}")
check("the draw is the tournament, not the Kalshi event",
      {p["draw"] for p in b.pos.values()} == {"ATP|US Open Men Singles"},
      f"got {sorted({p['draw'] for p in b.pos.values()})}")
b = bot(hard_cap=100000.0, per_leg_cap=100000.0, per_event_cap=100000.0,
        per_day_cap=100000.0)
vacherot(b)
for c in candidates(b):
    take(b, c)
check("...while the Kalshi events stay distinct and true",
      len({p["event"] for p in b.pos.values()}) == 4,
      f"got {sorted({p['event'] for p in b.pos.values()})}")

print("\nR is not sampled from a junk-wide book")
# Vacherot's quarterfinal qualifier, live 2026-09-05: 1c bid / 97c ask, a mid
# of 49c, on a match that priced him at 11.5c.
b = bot()
leg = add_leg(b, m_bid=0.40, m_ask=0.42, w_bid=0.01, w_ask=0.97, rsamp=[])
b.disc.started.discard((leg.tour, leg.key))
b.sample_r()
check("a 96c-wide book contributes no R sample", leg.rn == 0,
      f"got {leg.rsamp}")
b = bot()
leg = add_leg(b, m_bid=0.40, m_ask=0.42, w_bid=0.08, w_ask=0.12, rsamp=[])
b.disc.started.discard((leg.tour, leg.key))
b.sample_r()
check("a 4c-wide book still does", leg.rn == 1, f"got {leg.rsamp}")

print("\nR is frozen at the first ball")
# Measured across four days: on all five legs whose match ran from competitive
# to lost, W/M ROSE as the player collapsed -- 0.061 to 0.833 for Muchova --
# because the winner book does not mark down as fast as the match book. R
# sampled during play is stale W over fresh M, and it biases fair UP, which
# declines the trade that was actually there.
b = bot()
leg = add_leg(b, m_bid=0.49, m_ask=0.51, w_bid=0.08, w_ask=0.12, rsamp=[])
b.disc.started.discard((leg.tour, leg.key))
for _ in range(5):
    b.sample_r()
check("R accrues before the match starts", leg.rn == 5, f"got {leg.rn}")
before = leg.fair(0.05)
b.disc.started.add((leg.tour, leg.key))             # first ball
for _ in range(5):
    b.sample_r()
check("...and not one sample after it", leg.rn == 5, f"got {leg.rn}")
# Now move the winner book the way a stale one moves and confirm fair is deaf
# to it -- this is the bias the freeze exists to remove.
b.books[leg.win_tk] = book(yes=[(0.40, 500)], no=[(0.35, 300)])
for _ in range(5):
    b.sample_r()
check("...so fair is unchanged by a book that drifts during play",
      leg.fair(0.05) == before, f"{leg.fair(0.05)} vs {before}")

# A match already under way when we first see it has no pre-match window, so
# it gets no R and is skipped. That is WINNER_TAKER.md's rule, and the
# checkpoint is what stops it costing us a match across a restart.
b = bot()
leg = add_leg(b, rsamp=[])
for _ in range(60):
    b.sample_r()
check("a match found already under way accrues no R at all", leg.rn == 0,
      f"got {leg.rn}")
c, why = b.evaluate(leg)
check("...so it is declined, not guessed at",
      c is None and why.startswith("no-R"), f"got {why}")

check("the ladder prints nearest-round first, so a descent is visible",
      W.RUNGS == ("QUAR", "SEMI", "FIN", "WIN"))
check("the ladder rungs are named for the log",
      (W.rung("KXATPADVANCE-26USOQUAR-FRI"), W.rung("KXATPADVANCE-26USOSEMI-FRI"),
       W.rung("KXATPADVANCE-26USOFIN-FRI"), W.rung("KXATP-26USO-FRI"))
      == ("QUAR", "SEMI", "FIN", "WIN"))

print("\nplayer name matching (the Draxl/Draper class of bug)")
from discovery import norm_name, name_key


def pairs(a, b):
    return norm_name(a) == norm_name(b) or name_key(a) == name_key(b)


# Every one of these is real, seen in live markets or in the capture the
# allocation study ran on. A three-letter code is unique only within an event,
# and Kalshi labels US Open qualifying with the same competition string as the
# main draw, so the code alone pairs different people.
check("Draxl is not Draper", not pairs("Jack Draper", "Liam Draxl"))
check("Medvedev is not Medjedovic",
      not pairs("Daniil Medvedev", "Hamad Medjedovic"))
check("Van de Zandschulp is not Van Assche",
      not pairs("Botic Van de Zandschulp", "Luca Van Assche"))
check("Stearns is not Stephens", not pairs("Peyton Stearns", "Sloane Stephens"))
# ...and the books do not always spell the same person the same way, so strict
# equality alone would decline a real pair.
check("Caty McNally IS Catherine McNally",
      pairs("Catherine McNally", "Caty McNally"))
check("a name matches itself", pairs("Novak Djokovic", "Novak Djokovic"))
check("spacing and case do not matter",
      pairs("  novak   DJOKOVIC ", "Novak Djokovic"))
check("an empty name matches nothing", not pairs("", "Novak Djokovic"))

print("\nthe in-play gate (a match is live when it is TRADING)")
import time as _time
import discovery as D


class FakeScan:
    """Drives the real scan() over a sequence of market-list snapshots.

    Seeds the Discovery caches rather than mocking them, so this exercises the
    actual gate, the actual baseline bookkeeping, the actual pairing and the
    actual pruning -- no network. Each step() supplies the cumulative volume
    per match and how many seconds have passed since the previous list fetch.
    """

    COMP = "US Open Men Singles"

    def __init__(self, tours=("ATP",), winners=("AAA", "BBB"),
                 starts=None, begun=()):
        self.d = D.Discovery(tours=tours, log=lambda *a: None)
        # real clock: open_markets/competitions cache on time.time()
        self.t = _t.time()
        self.live = set()
        self.winners = winners
        # Seed the two exchange answers rather than mocking the calls, so the
        # real precedence in scan() is what gets exercised. A None schedule
        # means "the milestone feed knows nothing about this match", which is
        # the case that must still fall back to volume.
        self.d._sched = (self.t, {"KXATPMATCH-%s" % k: self.t + off
                                  for k, off in (starts or {}).items()})
        self.d._phase = (self.t, {"KXATPMATCH-%s" % k for k in begun})

    def _seed_winner_series(self):
        mkts = [{"ticker": "KXATP-26USO-%s" % c,
                 "event_ticker": "KXATP-26USO",
                 "yes_sub_title": "Player %s" % c} for c in self.winners]
        self.d._mkts["KXATP"] = (self.t, mkts)
        self.d._events["KXATP"] = (self.t, {"KXATP-26USO": self.COMP})
        # Discovery also queries qualifier markets. Keep this synthetic scan
        # isolated from the network and from real exchange listings.
        self.d._mkts["KXATPADVANCE"] = (self.t, [])
        self.d._events["KXATPADVANCE"] = (self.t, {})

    def step(self, volumes, dt=60.0, paired=True):
        self.t += dt
        mkts = []
        for key, vol in volumes.items():
            for code in ("AAA", "BBB"):
                mkts.append({"ticker": "KXATPMATCH-%s-%s" % (key, code),
                             "event_ticker": "KXATPMATCH-%s" % key,
                             "yes_sub_title": "Player %s" % code,
                             # both sides carry half, so the gate must sum them
                             "volume_fp": str(vol / 2.0)})
        self.d._mkts["KXATPMATCH"] = (self.t, mkts)
        # scan() reads these through their own TTL against the real clock;
        # re-stamp them so a multi-step test does not silently refetch.
        self.d._sched = (_t.time(), self.d._sched[1])
        self.d._phase = (_t.time(), self.d._phase[1])
        self.d._events["KXATPMATCH"] = (
            self.t, {"KXATPMATCH-%s" % k: self.COMP for k in volumes}
            if paired else {})
        self._seed_winner_series()
        newly, alive = self.d.scan(self.live)
        self.live |= {("ATP", k) for _, k, _, _ in newly}
        return {k for _, k, _, _ in newly}, alive


def went_live(volume_per_minute, dt=60.0):
    """Does a match trading at this rate get picked up on the second scan?"""
    s = FakeScan()
    s.step({"26XXX00": 10_000.0}, dt=dt)          # first sight: no baseline
    newly, _ = s.step({"26XXX00": 10_000.0 + volume_per_minute * (dt / 60.0)},
                      dt=dt)
    return "26XXX00" in newly


# Measured 2026-09-04 across five US Open matches: a listed, unplayed match
# traded 0-19 contracts a minute eight hours out and 0-514 four hours out,
# while a match in progress traded 1,178-136,969. The gate sits in the gap.
check("a match trading 136,000/min is in play", went_live(136_000))
check("a match trading 4,800/min is in play", went_live(4_800))
check("a match trading 1,178/min is in play", went_live(1_178))
check("a match trading 514/min is in play", went_live(514))
check("a listed match trading 19/min is NOT in play", not went_live(19))
check("a listed match trading 0/min is NOT in play", not went_live(0))
# A zombie -- finished, still in the open list, status "active" -- trades
# nothing, so it needs no separate rule. 38 of 39 candidates in a scan on
# 2026-09-01 were these.
check("a finished match left in the open list is NOT in play", not went_live(0))
# The gate is a RATE, so a scan that arrives late must not read as a burst.
check("a slow scan does not fake a burst", not went_live(19, dt=600.0))
check("a fast scan still catches a live match", went_live(4_800, dt=10.0))

print()
print("the schedule decides when to watch, the live label decides what is on")
# Volume could not tell a match being PLAYED from a market being repriced.
# Measured 2026-09-05 15:35Z: of 24 matches it had called in play, 3 had a book
# that moved at all in the previous hour. Pegula vs Cirstea was called in play
# on a market that had traded 4,288 contracts in its whole life.


def watched(starts=None, begun=(), volume=0.0):
    """Two scans against a fixed schedule -> (watched keys, started keys)."""
    s = FakeScan(starts=starts, begun=begun)
    # Union of both scans: a scheduled match is picked up on the FIRST one,
    # where a volume-detected match cannot be -- the rate has no baseline yet.
    # That one scan of latency was the price of the old rule.
    first, _ = s.step({"26XXX00": 10_000.0})
    second, _ = s.step({"26XXX00": 10_000.0 + volume})
    return first | second, {k for _, k in s.d.started}


w, _ = watched(starts={"26XXX00": 30 * 60}, volume=0.0)
check("a match due in 30 min is watched though it has traded nothing",
      "26XXX00" in w)
w, _ = watched(starts={"26XXX00": 3 * 3600}, volume=0.0)
check("a match due in 3 hours is not watched yet", "26XXX00" not in w)
# Erring early is the safe direction and the schedule slips LATE, never early:
# the order of play runs behind, so PREROLL only ever arrives in time.
w, _ = watched(starts={"26XXX00": 3 * 3600}, volume=500_000.0)
check("a hot market does not override a schedule that says not yet",
      "26XXX00" not in w)
w, _ = watched(volume=500_000.0)
check("an UNSCHEDULED match still falls back to volume", "26XXX00" in w)

# "Watched" is not "started". R is sampled before the first ball; selling is
# not, because a longshot quoted at 3c is under DYING all day.
w, st = watched(starts={"26XXX00": 30 * 60})
check("a match watched before its start has NOT started", not st)
w, st = watched(starts={"26XXX00": 30 * 60}, begun=("26XXX00",))
check("the live label marks a watched match as started", "26XXX00" in st)
# The label is undocumented, so it cannot be the only way to learn this.
w, st = watched(starts={"26XXX00": -600}, volume=500_000.0)
check("past its time and trading hard counts as started without the label",
      "26XXX00" in st)
w, st = watched(starts={"26XXX00": -600}, volume=0.0)
check("...but past its time and dead does not", not st)

# The first sighting has nothing to measure against and must not guess.
s = FakeScan()
newly, _ = s.step({"26XXX00": 5_000_000.0})
check("a match is never called live on its first sighting", newly == set())

# ...and a re-read of the cached list is not a fresh minute of zero volume.
s = FakeScan()
s.step({"26XXX00": 10_000.0})
s.d._mkts["KXATPMATCH"] = (s.t, s.d._mkts["KXATPMATCH"][1])   # same fetch time
before = dict(s.d._vol)
s.d.scan(set())
check("re-reading the cached list does not move the baseline",
      s.d._vol == before)

# Volume only ever goes up; a correction must not read as negative trading.
s = FakeScan()
s.step({"26XXX00": 10_000.0})
newly, _ = s.step({"26XXX00": 9_000.0})
check("a volume that goes backwards is not in play", newly == set())

# The pairing failures that used to be silent must now say so exactly once.
said = []
s = FakeScan(winners=())          # nobody in this match has a winner leg
s.d.log = said.append
s.step({"26XXX00": 10_000.0})
for i in range(3):
    s.step({"26XXX00": 10_000.0 + 100_000.0 * (i + 1)})
check("an unpairable live match is reported, not dropped silently",
      any("has an open leg to sell" in m for m in said), said)
check("...and it is reported once, not every scan",
      sum("has an open leg to sell" in m for m in said) == 1, said)

# Baselines must not grow without bound as a slam works through its draw.
s = FakeScan()
s.step({"26XXX%02d" % i: 1000.0 for i in range(50)})
s.step({"26XXX00": 1000.0})
check("baselines for matches that left the open list are pruned",
      set(s.d._vol) == {("ATP", "26XXX00")}, set(s.d._vol))
# ...but a throttled series returns an empty page, and that must not wipe them.
s = FakeScan()
s.step({"26XXX00": 1000.0})
s.d._mkts["KXATPMATCH"] = (s.t + 60, [])
s.d.scan(set())
check("an empty (throttled) page does not wipe the baselines",
      set(s.d._vol) == {("ATP", "26XXX00")}, set(s.d._vol))

# The window this replaces dropped matches BEFORE they were played, because
# expected_expiration_time is a nominal per-day placeholder: Auger-Aliassime's
# loss was played 24.5h past it and Keys' match settled 47.4h past it. Volume
# is measured when it happens, so a match days late is caught like any other.
s = FakeScan()
s.step({"26SEP02AUGKHA": 100_000.0})
newly, _ = s.step({"26SEP02AUGKHA": 100_000.0 + 68_973.0})
check("a match played a day after its nominal expiry is still caught",
      "26SEP02AUGKHA" in newly)

print()
if FAILED:
    print(f"{len(FAILED)} FAILED: {FAILED}")
    sys.exit(1)
print("all tests passed")
