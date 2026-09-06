import asyncio
import importlib
import json
import os
import tempfile
import unittest

os.environ['KALSHI_DATA'] = tempfile.mkdtemp(prefix='paper-support-test-')
import winner_taker as W
from kalshi import Book
from paper_support import PaperLiquidity


class PaperTests(unittest.TestCase):
    def test_consumption_cancel_replenish_and_resync(self):
        book = Book()
        book.snapshot({'yes_dollars_fp': [['0.01', '100']]}, 1)
        liq = PaperLiquidity()
        liq.snapshot('T', book)
        liq.consume('T', .01, 70, 100)
        self.assertEqual(liq.available('T', .01, 100), 30)
        liq.snapshot('T', book)
        self.assertEqual(liq.available('T', .01, 100), 30)
        for delta, expected in [('-50', 0), ('20', 20)]:
            msg = {'side': 'yes', 'price_dollars': '.01', 'delta_fp': delta}
            book.delta(msg, 2)
            liq.delta('T', msg, book)
            self.assertEqual(liq.available('T', .01, book.bid_size()), expected)

    def make_bot(self):
        b = W.WinnerTaker(False)
        b.cfg.v.update(hard_cap=500, per_leg_cap=500, per_event_cap=500,
                       per_day_cap=500, paper_latency_ms=20)
        l = W.Leg('ATP', 'MATCH', 'Tournament', 'A', 'M', 'T', 'E', 'Player A')
        b.legs['T'] = l
        b.disc.started.add(('ATP', 'MATCH'))
        for _ in range(W.MIN_R):
            l.observe_r(.2)
        for tk, y, n in [('M', '.04', '.94'), ('T', '.08', '.90')]:
            b.books[tk].snapshot({'yes_dollars_fp': [[y, '100']],
                                  'no_dollars_fp': [[n, '100']]}, 1)
        b.paper_liquidity.snapshot('T', b.books['T'])
        return b, l

    def test_cancel_during_latency_does_not_fill(self):
        b, l = self.make_bot()
        async def run():
            c, _ = b.evaluate(l)
            task = asyncio.create_task(b.take(c))
            await asyncio.sleep(.005)
            b.books['T'].yes.clear()
            await task
        asyncio.run(run())
        self.assertFalse(b.pos)

    def test_same_liquidity_only_fills_once(self):
        b, l = self.make_bot()
        c, _ = b.evaluate(l)
        asyncio.run(b.take(c))
        asyncio.run(b.take(c))
        self.assertEqual(b.pos['T']['count'], 100)

    def test_restart_preserves_account_and_consumed_depth(self):
        b, l = self.make_bot()
        b.feed_enforced = b.feed_ready = True
        c, _ = b.evaluate(l)
        asyncio.run(b.take(c))
        restored = W.WinnerTaker(False)
        restored.restore()
        self.assertEqual(restored.locked(), b.locked())
        self.assertEqual(restored.by_day, b.by_day)
        self.assertEqual(restored.takes, 1)
        self.assertEqual(restored.paper_liquidity.available('T', .08, 100), 0)

    def test_feed_and_settlement_guards(self):
        b, l = self.make_bot()
        b.feed_enforced = True
        self.assertEqual(b.evaluate(l)[1], 'feed-not-ready')
        b.feed_ready = True
        b.settled_tks.add('T')
        self.assertEqual(b.evaluate(l)[1], 'settled-leg')


if __name__ == '__main__':
    unittest.main()
