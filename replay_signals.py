"""Audit one-sided match signals with sequence-validated capture reconstruction."""
import argparse
import gzip
import json
import time

from kalshi import Book


def replay(paths, targets):
    sequences, books, active, episodes = {}, {}, {}, []
    gaps = 0
    messages = 0
    for path in paths:
        opener = gzip.open if path.endswith('.gz') else open
        with opener(path, 'rt') as f:
            for line in f:
                try:
                    v = json.loads(line)
                except ValueError:
                    continue
                typ = v.get('type')
                if typ not in ('orderbook_snapshot', 'orderbook_delta'):
                    continue
                messages += 1
                sid, seq = v.get('sid'), v.get('seq')
                now = v.get('t')
                previous = sequences.get(sid)
                if previous is not None and seq != previous + 1:
                    gaps += 1
                    for tk, ep in active.items():
                        episodes.append(dict(ep, end=now, end_reason='feed-gap'))
                    active.clear()
                    books.clear()
                sequences[sid] = seq
                m = v.get('msg') or {}
                tk = m.get('market_ticker', '')
                if not tk.startswith(('KXATPMATCH-', 'KXWTAMATCH-')) or not any(t in tk for t in targets):
                    continue
                if typ == 'orderbook_snapshot':
                    books[tk] = Book()
                    books[tk].snapshot(m, now)
                elif tk in books:
                    books[tk].delta(m, now)
                else:
                    continue
                b = books[tk]
                bid, ask = b.bid(), b.ask()
                state = bid is None and ask is not None and ask <= .02
                ep = active.get(tk)
                if state:
                    if ep is None:
                        ep = active[tk] = {'tk': tk, 'start': now, 'min_ask': ask,
                                           'max_ask': ask, 'one_cent_start': None,
                                           'start_type': typ}
                    ep['min_ask'] = min(ep['min_ask'], ask)
                    ep['max_ask'] = max(ep['max_ask'], ask)
                    if ask <= .01 and ep['one_cent_start'] is None:
                        ep['one_cent_start'] = now
                elif ep:
                    episodes.append(dict(ep, end=now, end_reason='book-recovered',
                                         end_bid=bid, end_ask=ask))
                    active.pop(tk)
    episodes.extend(dict(ep, end=None, end_reason='capture-ended') for ep in active.values())
    return {'paths': paths, 'targets': targets, 'messages': messages,
            'gaps_or_reconnects': gaps, 'episodes': episodes}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+')
    parser.add_argument('--target', action='append', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    result = replay(args.paths, args.target)
    with open(args.output, 'w') as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
