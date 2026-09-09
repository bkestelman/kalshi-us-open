"""Sequence-validated, compact order-book replay for post-signal recovery research.

Extract once, then analyze the compact tape. Prices are YES prices; no bids are
NO bids. A reset invalidates every book until its next snapshot. No live calls.
"""
import argparse
import gzip
import json
from kalshi import Book
from iolib import open_capture


def extract(paths, output, targets, windows=None, quotes_outside=False):
    books, previous, last, connection = {}, {}, {}, None
    count = resets = 0
    full_before = False
    with open(output + '.meta.json', 'w') as meta:
        json.dump({'paths': paths, 'targets': targets, 'depth_windows': windows or [],
                   'quotes_outside': quotes_outside}, meta, indent=2)
    with gzip.open(output, 'wt', compresslevel=1) as out:
        def emit(row):
            out.write(json.dumps(row, separators=(',', ':')) + '\n')
        for path in paths:
            with open_capture(path) as source:
                for line in source:
                    if not line.strip():
                        continue
                    v = json.loads(line)
                    if v.get('type') not in ('orderbook_snapshot', 'orderbook_delta'):
                        continue
                    count += 1
                    sid, seq, now = v.get('sid'), v.get('seq'), v['t']
                    conn = v.get('connection')
                    if conn != connection or (sid in previous and seq != previous[sid] + 1):
                        emit({'t': now, 'reset': True})
                        books.clear(); last.clear(); previous.clear()
                        resets += 1
                    connection = conn
                    previous[sid] = seq
                    m = v['msg']; tk = m.get('market_ticker', '')
                    if not any(target in tk for target in targets):
                        continue
                    if v['type'] == 'orderbook_snapshot':
                        books[tk] = Book(); books[tk].snapshot(m, now)
                    elif tk in books:
                        books[tk].delta(m, now)
                    else:
                        continue
                    b = books[tk]
                    full = not windows or any(lo <= now <= hi for lo, hi in windows)
                    def record(ticker, book, complete):
                        ladders = (book.yes, book.no)
                        if complete:
                            state = [sorted(((p,q) for p,q in side.items() if q > 1e-9), reverse=True) for side in ladders]
                        else:
                            state = [[max(((p,q) for p,q in side.items() if q > 1e-9), default=(0,0))] for side in ladders]
                            state = [[(p,q) for p,q in side if q > 1e-9] for side in state]
                        if (state, complete) != last.get(ticker):
                            emit({'t': now, 'tk': ticker, 'yes': state[0], 'no': state[1], 'depth_complete': complete})
                            last[ticker] = (state, complete)
                    # Seed unchanged related books when a pricing window opens.
                    if full and not full_before:
                        for ticker, book in books.items():
                            record(ticker, book, True)
                    full_before = full
                    if full or (quotes_outside and 'MATCH-' in tk):
                        record(tk, b, full)
    return {'messages': count, 'resets': resets, 'output': output}


def quotes(row):
    yes = [p for p, q in row['yes'] if q > 1e-9]
    no = [p for p, q in row['no'] if q > 1e-9]
    return (max(yes) if yes else None, round(1-max(no), 4) if no else None)


def sweep(levels, quantity, buy_yes=False, max_buy_price=None):
    """IOC execution at displayed depth; unfilled quantity is never invented."""
    cost = filled = 0
    for price, size in levels:
        if size <= 0:
            continue
        if buy_yes and max_buy_price is not None and 1-price > max_buy_price + 1e-9:
            break
        q = min(quantity-filled, size)
        cost += q * (1-price if buy_yes else price)
        filled += q
        if filled >= quantity:
            break
    return {'quantity': filled, 'value': cost}


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('paths', nargs='+'); p.add_argument('--output', required=True)
    p.add_argument('--target', action='append', required=True)
    p.add_argument('--depth-window', action='append', default=[], help='START:END Unix seconds')
    p.add_argument('--quotes-outside', action='store_true')
    a = p.parse_args()
    windows = [tuple(map(float, w.split(':'))) for w in a.depth_window]
    print(json.dumps(extract(a.paths, a.output, a.target, windows, a.quotes_outside)))
