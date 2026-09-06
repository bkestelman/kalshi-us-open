r"""Phase 1 of the allocation study: replay capture, emit every qualifying moment.

`winner_take.py` (../kalshi-tennis) answered "is the rule positive?" by
aggregating takes as it went, with no capital constraint at all -- it spent
$111,907 of collateral because nothing stopped it. WINNER_TAKER.md's bot has
$150 and will see far more opportunity than it can fund, so the open questions
are which opportunities to spend it on and whether ranking them is worth the
tick of staleness ranking costs.

Neither is answerable in a single pass, because a capital-constrained
simulation cannot be sharded by day: collateral locks until the TOURNAMENT
settles, days after the take. So this pass does the expensive, parallelisable
half -- decode 40 GB of JSON, rebuild books, track R -- and emits the small
stream of moments where a leg passed the entry gates. alloc_study.py then runs
the constrained allocator over that stream, cheaply, as many times as a sweep
needs.

Emitted rows are *book states*, not takes, and not only the good ones. Once a
leg's match is dying and R is known, every change to its top of book is
emitted -- including the bid falling out of the price band, the edge going
negative, and the bid disappearing entirely (bid=0, size=0).

That last case is the whole reason the stream is not filtered down to
qualifying moments: phase 2 is measuring what a tick of delay costs, and a
delay costs nothing unless the bid can be gone by the time we fire. A stream
of only-qualifying rows would let every simulated tick fill at leisure and
report ranking as free. So the capital-independent gates (price band, min
edge) are applied in phase 2 as well, where they can also be swept.

Days are independent (R is per-match and matches do not span a UTC day), so
the pass fans out one process per day:

  python3 alloc_extract.py ws_20260805.jsonl                 # one day
  ls data/live/ws_*.jsonl.gz | sed 's|.*/||;s|\.gz$||' \
      | xargs -P 8 -n 1 python3 alloc_extract.py             # every local day

Out:  data/cand/cand_YYYYMMDD.csv
"""
import csv
import json
import os
import sys
from collections import defaultdict

from discovery import name_key, norm_name
from iolib import LIVE, capture_files, open_capture

OUT_DIR = os.path.join(LIVE, "..", "cand")

# The two gates that decide whether a leg is WATCHED at all. Everything else
# from WINNER_TAKER.md -- price band, min edge, ROC floor, caps -- is phase 2's,
# so it can be swept and so the stream still shows a watched bid dying.
DYING = 0.10          # match mid below this: the only state the rule survives
COMPET = 0.30         # match mid above this: match is competitive, R samples
MIN_R = 50            # samples of W/M needed before fair is trusted

fee = lambda p: 0.07 * p * (1 - p)

FIELDS = ["t", "tk", "event", "bid", "size", "since", "fair", "mmid",
          "edge", "roc", "result"]


class Book:
    """Yes/no ladders plus when each yes level last became non-empty.

    `since` is what makes a resting order identifiable across updates, which
    is how phase 2 avoids selling the same lot twice. It is NOT a persistence
    gate -- the sweep in FINDINGS.md killed those -- it is just an identity.
    """
    __slots__ = ("yes", "no", "since")

    def __init__(self):
        self.yes, self.no = {}, {}
        self.since = {}

    def snap(self, m, t):
        self.yes = {round(float(p), 4): float(q)
                    for p, q in (m.get("yes_dollars_fp") or [])}
        self.no = {round(float(p), 4): float(q)
                   for p, q in (m.get("no_dollars_fp") or [])}
        # A snapshot is a resync, not new orders: age is unknown, so restart
        # the clock. That understates rest time, which is the safe direction.
        self.since = {p: t for p, q in self.yes.items() if q > 1e-9}

    def delta(self, m, t):
        side = m["side"]
        d = self.yes if side == "yes" else self.no
        p = round(float(m["price_dollars"]), 4)
        d[p] = d.get(p, 0.0) + float(m["delta_fp"])
        if d[p] <= 1e-9:
            d.pop(p, None)
        if side == "yes":
            if d.get(p, 0.0) > 1e-9:
                self.since.setdefault(p, t)
            else:
                self.since.pop(p, None)

    def bid(self):
        y = [p for p, q in self.yes.items() if q > 1e-9]
        return max(y) if y else None

    def ask(self):
        n = [p for p, q in self.no.items() if q > 1e-9]
        return round(1 - max(n), 4) if n else None

    def mid(self):
        b, a = self.bid(), self.ask()
        return (b + a) / 2 if (b is not None and a is not None and b < a) else None


def extract(path, out_path):
    tm = json.load(open(os.path.join(LIVE, "tourn_map.json")))
    comp_of_key = tm["comp_of_key"]
    win_event = tm["win_event"]
    results = tm["results"]
    names = tm.get("names") or {}
    # event -> {code: real market ticker}. See build_tourn_map.py: the winner
    # ticker CANNOT be rebuilt as f"{event}-{code}", because one event carries
    # two ticker shapes and the glued form names a market that does not exist
    # for the second one. Those legs then have no match mid and vanish from
    # the study without a word -- about a third of the US Open draw on
    # 2026-09-04. Refuse rather than silently under-count.
    win_leg = tm.get("win_leg") or {}
    if not win_leg:
        raise SystemExit(
            "tourn_map.json has no `win_leg`: re-run build_tourn_map.py.\n"
            "Gluing event+code drops every leg whose market uses the short "
            "ticker shape (KXATP-26-SWE is in event KXATP-26USO), silently "
            "and in the direction of finding fewer trades than exist.")
    if not names:
        raise SystemExit(
            "tourn_map.json has no `names`: re-run build_tourn_map.py.\n"
            "Pairing on competition+code alone is not safe -- in this very "
            "capture MED is both Medvedev and Medjedovic, VAN both Van de "
            "Zandschulp and Van Assche, STE both Stearns and Stephens.")
    paired, refused = {}, set()

    def same_player(match_tk, win_tk):
        """Is the match book and the winner leg the SAME person?

        Cached, because it is asked on every match-book message. A pair with a
        name missing on either side is refused: this runs offline against a
        map that can simply be rebuilt, so there is no reason to guess.
        """
        k = (match_tk, win_tk)
        if k in paired:
            return paired[k]
        a, b = names.get(match_tk), names.get(win_tk)
        ok = bool(a and b and (norm_name(a) == norm_name(b)
                               or name_key(a) == name_key(b)))
        paired[k] = ok
        if not ok and k not in refused:
            refused.add(k)
        return ok

    books = defaultdict(Book)
    mmid = {}                    # winner ticker -> holder's live match mid
    rsamp = defaultdict(list)    # winner ticker -> W/M seen while competitive
    last_state = {}              # winner ticker -> last emitted (bid, size, since)
    rows = 0

    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", newline="") as fo:
        w = csv.writer(fo)
        w.writerow(FIELDS)
        with open_capture(path) as fh:
            for ln in fh:
                try:
                    o = json.loads(ln)
                except ValueError:
                    continue
                typ = o.get("type")
                if typ not in ("orderbook_snapshot", "orderbook_delta"):
                    continue
                msg = o["msg"]
                tk = msg.get("market_ticker")
                if not tk:
                    continue
                t = o.get("t")
                b = books[tk]
                if typ == "orderbook_snapshot":
                    b.snap(msg, t)
                else:
                    b.delta(msg, t)

                head = tk.split("-")[0]
                if head in ("KXATPMATCH", "KXWTAMATCH"):
                    # Join the match to its winner leg through the EVENT's
                    # competition string. A player code identifies the player,
                    # so a live match carries winner legs for every tournament
                    # they are entered in; pairing on the code alone shorts the
                    # wrong tournament (build_tourn_map.py says the same).
                    key = tk.split("-")[1]
                    code = tk.rsplit("-", 1)[1]
                    comp = comp_of_key.get(key)
                    ev = win_event.get(comp) if comp else None
                    wtk = win_leg.get(ev, {}).get(code) if ev else None
                    if wtk and same_player(tk, wtk):
                        mmid[wtk] = b.mid()
                    continue
                if head not in ("KXATP", "KXWTA"):
                    continue

                res = results.get(tk)
                if res not in ("yes", "no"):
                    continue            # unsettled: cannot be scored
                m = mmid.get(tk)
                wmid = b.mid()
                if m is not None and m > COMPET and wmid is not None:
                    rsamp[tk].append(wmid / m)

                if m is None or m >= DYING:
                    continue
                sam = rsamp[tk]
                if len(sam) < MIN_R:
                    continue            # R unknown for THIS match: skip it

                fair = m * sorted(sam)[len(sam) // 2]
                p = b.bid()
                q = b.yes.get(p, 0.0) if p is not None else 0.0
                if p is None or q <= 1e-9:
                    p, q, since, edge, roc = 0.0, 0.0, t, -1.0, -1.0
                else:
                    since = b.since.get(p, t)
                    edge = p - fair - fee(p)
                    roc = edge / (1 - p)
                # One row per CHANGE of top of book, not per message: a leg in
                # a dying match takes thousands of deep-book updates that move
                # nothing we trade on, and phase 2 only ever reads the touch.
                st = (p, q, since)
                if last_state.get(tk) == st:
                    continue
                last_state[tk] = st
                w.writerow([f"{t:.3f}", tk, tk.rsplit("-", 1)[0], f"{p:.4f}",
                            f"{q:.0f}", f"{since:.3f}", f"{fair:.4f}",
                            f"{m:.4f}", f"{edge:.4f}", f"{roc:.4f}", res])
                rows += 1
    # Two different problems, and conflating them hides the second one: a
    # COLLISION is the bug this check exists for, while UNNAMED is a hole in
    # tourn_map.json that silently shrinks the sample and is fixed by
    # rebuilding it, not by loosening the check.
    coll = [(a, b) for a, b in sorted(refused) if names.get(a) and names.get(b)]
    unnamed = [(a, b) for a, b in sorted(refused)
               if not (names.get(a) and names.get(b))]
    for a, b in coll:
        print(f"    COLLISION, not paired: {a} {names.get(a)!r} "
              f"vs {b} {names.get(b)!r}", flush=True)
    if unnamed:
        print(f"    {len(unnamed)} pairs refused for a MISSING NAME "
              f"(e.g. {unnamed[0][1]}); re-run build_tourn_map.py if a leg "
              f"you expected is absent", flush=True)
    return rows


def main():
    pats = [x for x in sys.argv[1:] if not x.startswith("-")]
    paths = capture_files(" ".join(pats) if pats else "ws_*.jsonl")
    if not paths:
        raise SystemExit("no capture files matched")
    for p in paths:
        day = os.path.basename(p).split("_", 1)[1].split(".")[0]
        op = os.path.join(OUT_DIR, f"cand_{day}.csv")
        n = extract(p, op)
        print(f"{os.path.basename(p)}: {n:,} candidate rows -> {op}", flush=True)


if __name__ == "__main__":
    main()
