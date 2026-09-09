"""Pure, price-bounded recovery policy. No transport or account access."""
from decimal import Decimal, ROUND_CEILING

THRESHOLD = .10
PERSISTENCE = 5.0
MAX_HEDGE = .20
MAX_EXIT_LOSS = .02  # Gross dollars per contract relative to entry, before fees.


def fee(levels):
    value = sum((Decimal('.07') * Decimal(str(p)) * (1-Decimal(str(p))) *
                 Decimal(str(q)) for p, q in levels), Decimal(0))
    return float(value.quantize(Decimal('.01'), rounding=ROUND_CEILING))


def adverse_quote(book, entry_side):
    bid, ask = book.bid(), book.ask()
    if bid is not None and ask is not None and bid >= ask:
        return None
    return round(1-ask, 4) if entry_side == 'bid' and ask is not None else bid if entry_side == 'ask' else None


class Revival:
    """Persistence uses a monotonic clock and resets on a new book/gap."""
    def __init__(self):
        self.book = None
        self.since = None

    def update(self, book, entry_side, now, ready=True):
        if not ready or book is None:
            self.book = None
            self.since = None
            return False
        if book is not self.book:
            self.book, self.since = book, None
        bid = adverse_quote(book, entry_side)
        if bid is None or bid < THRESHOLD:
            self.since = None
            return False
        if self.since is None:
            self.since = now
        return now-self.since >= PERSISTENCE


def choose_plan(position, books, budget):
    """Choose greatest protected quantity, then greatest guaranteed proceeds.

    A sale supplies its price now; a matching hedge supplies at least $1 at
    normal settlement minus its cost. Compare those values after fees. Every
    route has an explicit limit; never sweep through arbitrarily bad prices.
    """
    quantity = position['quantity']-position.get('protected', 0)
    if quantity <= 0:
        return None
    ticker, match = position['ticker'], position['match_ticker']
    entry_side = position['side']
    unit_cost = position['price'] if entry_side == 'bid' else 1-position['price']
    routes = [(ticker, 'yes' if entry_side == 'bid' else 'no', 'exit', max(.01,unit_cost-MAX_EXIT_LOSS)),
              (match, 'yes' if entry_side == 'bid' else 'no', 'hedge', MAX_HEDGE)]
    # An opponent ticker is optional; the exact entry market always supplies
    # the primary hedge (buy its NO for a winner entry, YES for a loser entry).
    others = [tk for tk in books if tk.rsplit('-',1)[0] == match.rsplit('-',1)[0] and tk != match]
    if len(others) == 1:
        routes.append((others[0], 'no' if entry_side == 'bid' else 'yes', 'hedge', MAX_HEDGE))
    choices = []
    for tk, ladder, kind, limit in routes:
        book = books.get(tk)
        if book is None:
            continue
        if book.bid() is not None and book.ask() is not None and book.bid() >= book.ask():
            continue
        used, filled, gross = [], 0, 0
        levels = sorted(getattr(book,ladder).items(), reverse=True)
        for p, size in levels:
            if size <= 0:
                continue
            price = p if kind == 'exit' else round(1-p,4)
            if (kind == 'exit' and price < limit-1e-9) or (kind == 'hedge' and price > limit+1e-9):
                break
            upper = int((min(size, quantity-filled)+1e-9)*100)
            lo, hi = 0, upper
            while lo < hi:
                mid = (lo+hi+1)//2
                charges = fee(used+[(price,mid/100)])
                extra = gross+price*mid/100+charges if kind == 'hedge' else charges
                if extra <= budget+1e-9:
                    lo = mid
                else:
                    hi = mid-1
            count = lo/100
            if count:
                used.append((price,count)); filled += count; gross += price*count
            if filled >= quantity:
                break
        if not filled:
            continue
        charges = fee(used)
        guaranteed = gross-charges if kind == 'exit' else filled-gross-charges
        choices.append({'ticker':tk,'ladder':ladder,'kind':kind,'limit':limit,
                        'quantity':filled,'levels':used,'gross':gross,'fee':charges,
                        'guaranteed_proceeds':guaranteed})
    return max(choices, key=lambda p:(p['quantity'],p['guaranteed_proceeds'])) if choices else None
