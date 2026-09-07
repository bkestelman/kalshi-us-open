"""One book-decided candidate function used by both live and shadow pilot."""
from score_context import context, future_round
from qualifier_paper import secured_ticker

SERIES = {'KXATP', 'KXWTA', 'KXATPADVANCE', 'KXWTAADVANCE'}


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
            side, price, quantity = 'bid', wb.ask(), wb.no.get(round(1-wb.ask(), 4), 0) if wb.ask() else 0
    elif mb.bid() is None and mb.ask() is not None and mb.ask() <= .01:
        if score['state'] != 'won' and future_round(score['round'], leg.win_tk):
            side, price, quantity = 'ask', wb.bid(), wb.bid_size()
    if side is None or price is None or quantity < 1:
        return None
    if (side == 'bid' and not .85 <= price < 1) or (side == 'ask' and not 0 < price <= .15):
        return None
    return {'ticker': leg.win_tk, 'match': leg.match_tk.rsplit('-', 1)[0],
            'match_ticker': leg.match_tk, 'side': side, 'price': price,
            'quantity': quantity, 'score': score,
            'match_bid': mb.bid(), 'match_ask': mb.ask()}
