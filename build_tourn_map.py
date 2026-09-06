"""Build data/live/tourn_map.json: match key -> winner event, + winner results.

The ws capture subscribes to a player's tournament-winner legs by PLAYER CODE,
and a code identifies the player rather than the tournament, so a player in a
live Cincinnati match also carries their US Open leg in the same capture (the
collector does this deliberately -- see collector_ws.related_tickers). Any
analysis pairing a match with "the" winner leg therefore has to know which
tournament the match belonged to, and that lives only on the EVENT:

    KXWTAMATCH-26AUG20ANIPEG  product_metadata.competition = "WTA Cincinnati"
    KXWTA-26CINCIN            product_metadata.competition = "WTA Cincinnati"

That string is necessary and NOT sufficient. Kalshi labels US Open QUALIFYING
matches with the same competition as the main draw, and a three-letter code is
unique only within an event, so competition+code pairs different PEOPLE:

    KXATPMATCH-26AUG24MEJDRA-DRA  "Liam Draxl"      (qualifier)
    KXATP-26USO-DRA               "Jack Draper"

and in the capture the allocation study ran on, MED was both Medvedev and
Medjedovic, VAN both Van de Zandschulp and Van Assche, STE both Stearns and
Stephens. So `names` is dumped too, and the reader must confirm the pair is
one person before using it (see discovery.name_key for the matching rule --
'Caty McNally' and 'Catherine McNally' are the same player and must still
pair).

Results are baked in here too so a replay box needs no API access at all.

Run: python3 build_tourn_map.py     (a few minutes, ~20 paginated calls)
"""
import json, time, urllib.request, urllib.parse
from iolib import LIVE

BASE = "https://api.elections.kalshi.com/trade-api/v2"

def get(path, **params):
    url = f"{BASE}{path}" + ("?" + urllib.parse.urlencode(params) if params else "")
    for attempt in range(6):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if "429" in str(e) or "5" == str(getattr(e, "code", ""))[:1]:
                time.sleep(2 * (attempt + 1)); continue
            if getattr(e, "code", None) == 404:
                return None
            if attempt == 5: raise
            time.sleep(1 + attempt)

def all_events(series, status=None):
    out, cur = [], None
    while True:
        p = dict(series_ticker=series, limit=200)
        if status: p["status"] = status
        if cur: p["cursor"] = cur
        d = get("/events", **p)
        if not d: break
        out += d.get("events", [])
        cur = d.get("cursor")
        if not cur: break
        time.sleep(0.25)
    return out

def all_markets(series):
    """Every market in a series, settled included, for the name dump."""
    out, cur = [], None
    while True:
        p = dict(series_ticker=series, limit=1000)
        if cur: p["cursor"] = cur
        d = get("/markets", **p)
        if not d: break
        out += d.get("markets", [])
        cur = d.get("cursor")
        if not cur: break
        time.sleep(0.25)
    return out


def main():
    comp_of_key = {}            # 26AUG20ANIPEG -> "WTA Cincinnati"
    names = {}                  # ticker -> player name, BOTH scopes
    for s in ("KXATPMATCH", "KXWTAMATCH"):
        for st in ("settled", "open", None):
            for e in all_events(s, st):
                c = (e.get("product_metadata") or {}).get("competition")
                if c:
                    comp_of_key[e["event_ticker"].split("-")[1]] = c
        for m in all_markets(s):
            n = (m.get("yes_sub_title") or "").strip()
            if n:
                names[m["ticker"]] = n
        print(f"{s}: {len(comp_of_key)} match keys, {len(names)} names so far")

    win_event = {}              # "WTA Cincinnati" -> KXWTA-26CINCIN
    results = {}                # KXWTA-26CINCIN-ANI -> "no"
    # event -> {code: REAL market ticker}. Not derivable by gluing the event
    # to the code: Kalshi carries two market-ticker shapes inside one event,
    # so KXATP-26USO holds both KXATP-26USO-ZVE and KXATP-26-SWE (measured
    # 2026-09-04). f"{event}-{code}" names a market that does not exist for
    # every leg of the second shape, and they then drop out silently.
    win_leg = {}
    for s in ("KXATP", "KXWTA"):
        for e in all_events(s):
            c = (e.get("product_metadata") or {}).get("competition")
            if not c: continue
            ev = e["event_ticker"]
            win_event[c] = ev
            d = get("/markets", event_ticker=ev, limit=200)
            legs = win_leg.setdefault(ev, {})
            for m in (d or {}).get("markets", []):
                if m.get("result"):
                    results[m["ticker"]] = m["result"]
                n = (m.get("yes_sub_title") or "").strip()
                if n:
                    names[m["ticker"]] = n
                legs[m["ticker"].rsplit("-", 1)[-1]] = m["ticker"]
            time.sleep(0.2)
        print(f"{s}: {len(win_event)} competitions, {len(results)} settled legs")

    out = {"comp_of_key": comp_of_key, "win_event": win_event,
           "results": results, "names": names, "win_leg": win_leg}
    p = f"{LIVE}/tourn_map.json"
    json.dump(out, open(p, "w"))
    print(f"wrote {p}: {len(comp_of_key)} keys, {len(win_event)} competitions, "
          f"{len(results)} results, {len(names)} names")

if __name__ == "__main__":
    main()
