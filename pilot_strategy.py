"""One book-decided candidate function used by both live and shadow pilot."""
from score_context import context, future_round
from qualifier_paper import secured_ticker
from decimal import Decimal
import math

SERIES = {'KXATP', 'KXWTA', 'KXATPADVANCE', 'KXWTAADVANCE'}


def executable_levels(book, side):
    """Eligible YES-price limits in execution priority, including fractional depth."""
    ladder = book.no if side == 'bid' else book.yes
    levels = []
    for level, quantity in sorted(ladder.items(), reverse=True):
        price = round(1-level, 4) if side == 'bid' else level
        eligible = .85 <= price < 1 if side == 'bid' else 0 < price <= .15
        if eligible and math.isfinite(quantity) and quantity > 0:
            levels.append([price, round(quantity, 8)])
    return levels


def candidate(owner, leg):
    if not owner.feed_ready or (leg.tour, leg.key) not in owner.disc.started:
        return None
    if leg.win_tk.split('-')[0] not in SERIES or 'US Open' not in leg.comp:
        return None
    score = context(owner.scores, leg.match_tk)
    # Require a verified identity/round mapping, but never wait for final score.
    if score['state'] in ('missing', 'identity-mismatch') or not score.get('round'):
        return None
    mb, wb = owner.books.get(leg.match_tk), owner.books.get(leg.win_tk)
    if mb is None or wb is None:
        return None
    side = None
    if mb.bid() is not None and mb.bid() >= .99 and mb.ask() is None:
        if score['state'] != 'lost' and secured_ticker(score['round'], leg.win_tk):
            side = 'bid'
    elif mb.bid() is None and mb.ask() is not None and mb.ask() <= .01:
        if score['state'] != 'won' and future_round(score['round'], leg.win_tk):
            side = 'ask'
    if side is None:
        return None
    levels = executable_levels(wb, side)
    quantity = sum((Decimal(str(q)) for _, q in levels), Decimal(0))
    if quantity < 1:
        return None
    return {'ticker': leg.win_tk, 'match': leg.match_tk.rsplit('-', 1)[0],
            'match_ticker': leg.match_tk, 'side': side, 'price': levels[-1][0],
            'quantity': float(quantity), 'levels': levels, 'score': score,
            'match_bid': mb.bid(), 'match_ask': mb.ask()}
