"""Durable paper accounting and conservative displayed-liquidity tracking."""
import json
import os


def rest_quantity(market_response, orderbook_response, side, price):
    """Conservative executable-depth check for paper, using public responses."""
    market = (market_response or {}).get('market') or {}
    if market.get('status') != 'active' or market.get('result'):
        return 0.0, 'market-not-active'
    book = (orderbook_response or {}).get('orderbook_fp')
    if not isinstance(book, dict):
        return 0.0, 'missing-rest-book'
    levels = book.get(side + '_dollars')
    if not isinstance(levels, list):
        return 0.0, 'missing-rest-side'
    q = sum(float(q) for p, q in levels if abs(float(p) - price) < 1e-8)
    return max(0.0, q), 'verified' if q > 0 else 'rest-price-absent'


class PaperLiquidity:
    """Never fill the same displayed quantity twice.

    Positive deltas replenish capacity; cancellations remove capacity. A
    resnapshot can reduce capacity but cannot prove new orders arrived, so it
    never replenishes an already observed level. This is conservative when a
    cancellation removed an order we already simulated taking.
    """
    def __init__(self, levels=None):
        self.levels = levels or {}

    @staticmethod
    def key(price):
        return f"{price:.4f}"

    def snapshot(self, ticker, book):
        old = self.levels.get(ticker)
        new = {self.key(p): q for p, q in book.yes.items() if q > 0}
        if old is not None:
            new = {p: min(q, old.get(p, q)) for p, q in new.items()}
            # Remember exhausted prices even while absent from a snapshot.
            new.update({p: 0.0 for p in old if p not in new})
        self.levels[ticker] = new

    def delta(self, ticker, message, book):
        if message['side'] != 'yes':
            return
        p = float(message['price_dollars'])
        k = self.key(p)
        levels = self.levels.setdefault(ticker, {})
        levels[k] = max(0.0, min(book.yes.get(round(p, 4), 0.0),
                                levels.get(k, 0.0) + float(message['delta_fp'])))

    def available(self, ticker, price, displayed):
        return min(displayed, self.levels.get(ticker, {}).get(self.key(price), displayed))

    def consume(self, ticker, price, quantity, displayed):
        remaining = self.available(ticker, price, displayed) - quantity
        self.levels.setdefault(ticker, {})[self.key(price)] = max(0.0, remaining)


def atomic_json(path, value):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(value, f, separators=(',', ':'), allow_nan=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
