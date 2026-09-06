"""Paper-only YES taker for the qualification secured by this match.

Separate $500 research account, sharing the bot's feed and latency setting.
This module has no order API and is never instantiated in live mode.
"""
import asyncio
import json
import math
import os
import time
from types import SimpleNamespace

from kalshi import fee, get
from paper_support import PaperLiquidity, atomic_json
from score_context import context


def secured_ticker(round_name, ticker):
    target = {'round of 16': 'QUAR', 'quarterfinals': 'SEMI',
              'quarterfinal': 'SEMI', 'semifinals': 'FIN',
              'semifinal': 'FIN', 'final': 'WIN'}.get((round_name or '').lower())
    if target == 'WIN':
        return 'ADVANCE-' not in ticker
    return target is not None and 'ADVANCE-' in ticker and ticker.rsplit('-', 1)[0].endswith(target)


class QualifierPaper:
    def __init__(self, owner, directory):
        self.owner = owner
        self.path = os.path.join(directory, 'qualifier_paper_account.json')
        self.positions, self.pending, self.settled = {}, {}, set()
        self.liquidity = PaperLiquidity()
        self.realized = 0.0
        self.takes = 0
        if os.path.exists(self.path):
            with open(self.path) as f:
                state = json.load(f)
            if state['version'] != 1:
                raise ValueError('unsupported qualifier account')
            self.positions = state['positions']
            self.settled = set(state['settled'])
            self.liquidity = PaperLiquidity(state['liquidity'])
            self.realized, self.takes = state['realized'], state['takes']

    def save(self):
        atomic_json(self.path, {'version': 1, 'positions': self.positions,
                               'settled': sorted(self.settled), 'liquidity': self.liquidity.levels,
                               'realized': self.realized, 'takes': self.takes})

    def book_update(self, ticker, message, book, snapshot):
        inverse = SimpleNamespace(yes=book.no)
        if snapshot:
            self.liquidity.snapshot(ticker, inverse)
        elif message['side'] == 'no':
            self.liquidity.delta(ticker, dict(message, side='yes'), inverse)

    def candidate(self, leg):
        owner, tk = self.owner, leg.win_tk
        if not owner.feed_ready or tk in self.settled or tk in self.pending:
            return None
        score = context(owner.scores, leg.match_tk)
        if not secured_ticker(score.get('round'), tk) or score['state'] == 'lost':
            return None
        mb, wb = owner.books.get(leg.match_tk), owner.books.get(tk)
        if mb is None or wb is None:
            return None
        confirmed = score['state'] == 'won'
        inferred = ((leg.tour, leg.key) in owner.disc.started
                    and mb.bid() is not None and mb.bid() >= .99 and mb.ask() is None)
        if not (confirmed or inferred):
            return None
        p = wb.ask()
        if p is None or not 0 < p < 1 or (not confirmed and p < .85):
            return None
        no_price = round(1 - p, 4)
        q = self.liquidity.available(tk, no_price, wb.no.get(no_price, 0))
        if q < 1:
            return None
        locked = sum(v['cost'] for v in self.positions.values()) + sum(self.pending.values())
        room = max(0, 500 - locked)
        if not confirmed:
            risk = sum(v['cost'] for v in self.positions.values() if v['risk'] == leg.risk)
            room = min(room, 125 - risk)
        n = min(int(q), 500, int(max(0, room - .01) / (p + fee(p))))
        if n < 1:
            return None
        return {'leg': leg, 'price': p, 'no_price': no_price, 'count': n,
                'score': score, 'signal': 'score-confirmed' if confirmed else 'book-inferred',
                'decision_at': time.time()}

    def on_leg(self, leg):
        c = self.candidate(leg)
        if c:
            # Reserve synchronously before scheduling, including rounded fee.
            n, p = c['count'], c['price']
            self.pending[leg.win_tk] = n * p + math.ceil(n * fee(p) * 100 - 1e-9) / 100
            asyncio.create_task(self.take(c))

    async def take(self, c):
        owner, leg = self.owner, c['leg']
        tk, p, no_p = leg.win_tk, c['price'], c['no_price']
        try:
            await asyncio.sleep(max(0, owner.cfg['paper_latency_ms']) / 1000)
            wb = owner.books.get(tk)
            displayed = wb.no.get(no_p, 0) if wb and owner.feed_ready else 0
            n = min(c['count'], int(self.liquidity.available(tk, no_p, displayed)))
            if n < 1:
                owner.jlog({'a': 'qualifier_no_fill', 'tk': tk, 'price': p,
                            'decision_at': c['decision_at']})
                return
            self.liquidity.consume(tk, no_p, n, displayed)
            cost = n * p + math.ceil(n * fee(p) * 100 - 1e-9) / 100
            pos = self.positions.setdefault(tk, {'count': 0, 'cost': 0.0,
                                                 'risk': leg.risk, 'match_tk': leg.match_tk})
            pos['count'] += n
            pos['cost'] += cost
            self.takes += 1
            self.save()
            owner.jlog({'a': 'qualifier_take', 'tk': tk, 'match_tk': leg.match_tk,
                        'side': 'yes', 'price': p, 'count': n, 'cost': cost,
                        'signal': c['signal'], 'score': c['score'],
                        'decision_at': c['decision_at'],
                        'elapsed_ms': (time.time() - c['decision_at']) * 1000,
                        'account': 'qualifier-paper-500', 'fill_model': 'delayed-displayed-liquidity-v1'})
        except Exception as e:
            owner.jlog({'a': 'qualifier_error', 'error': str(e)})
            owner.stop = True
        finally:
            self.pending.pop(tk, None)

    async def settle(self):
        loop = asyncio.get_running_loop()
        for tk, pos in list(self.positions.items()):
            data = await loop.run_in_executor(self.owner.pool, get, '/markets/' + tk)
            result = (data or {}).get('market', {}).get('result')
            if result not in ('yes', 'no'):
                continue
            pnl = (pos['count'] if result == 'yes' else 0) - pos['cost']
            self.realized += pnl
            self.positions.pop(tk)
            self.settled.add(tk)
            self.save()
            self.owner.jlog({'a': 'qualifier_settle', 'tk': tk, 'result': result,
                            'count': pos['count'], 'realized': pnl,
                            'account': 'qualifier-paper-500'})
