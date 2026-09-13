"""Conservative multi-level shadow execution against one REST/WS intersection."""
from decimal import Decimal, ROUND_DOWN

D = lambda x: Decimal(str(x))


def paper_sweep(row, ws, market_response, book_response):
    market = (market_response or {}).get('market') or {}
    if (market.get('ticker') != row['ticker'] or market.get('status') != 'active'
            or market.get('result')):
        return [], 'market-not-active'
    raw = (book_response or {}).get('orderbook_fp')
    side = 'no' if row['side'] == 'bid' else 'yes'
    if not isinstance(raw, dict) or not isinstance(raw.get(side+'_dollars'), list):
        return [], 'missing-rest-side'
    rest = {}
    try:
        for p, q in raw[side+'_dollars']:
            p, q = D(p), D(q)
            if not p.is_finite() or not q.is_finite() or not 0 <= p <= 1 or q < 0:
                return [], 'invalid-rest-depth'
            if p in rest:
                return [], 'duplicate-rest-price'
            rest[p] = q
    except (ValueError, TypeError, ArithmeticError):
        return [], 'invalid-rest-depth'
    remaining, fills = D(row['count']), []
    for p, q in sorted(getattr(ws, side).items(), reverse=True):
        p, q = D(p), D(round(q, 8))
        if not p.is_finite() or not q.is_finite() or q <= 0:
            continue
        price = 1-p if side == 'no' else p
        if (row['side'] == 'bid' and price > D(row['price'])) or (
                row['side'] == 'ask' and price < D(row['price'])):
            continue
        # Intersect per price, never min(total REST, total WS): the totals
        # could represent different prices and hence nonexistent liquidity.
        n = min(remaining, q, rest.get(p, D(0))).quantize(D('.01'), rounding=ROUND_DOWN)
        if n > 0:
            fills.append({'price': str(price), 'quantity': str(n)})
            remaining -= n
        if remaining <= 0:
            break
    return fills, 'verified'
