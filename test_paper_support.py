import asyncio
import importlib
import json
import os
import tempfile
import unittest
import time

os.environ['KALSHI_DATA'] = tempfile.mkdtemp(prefix='paper-support-test-')
import winner_taker as W
from kalshi import Book
from paper_support import PaperLiquidity, rest_quantity
from score_context import context, future_round, confirmed_winner
from qualifier_paper import QualifierPaper, secured_ticker
from discovery_feed import FollowerDiscovery


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
                       per_day_cap=500, paper_latency_ms=20, paper_verify_rest=False)
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

    def test_score_identity_freshness_and_future_round(self):
        matches = {'EVENT': {'players': {'EVENT-A': {'id': 'a'}},
                            'milestone_id': 'id', 'received_at': 100,
                            'round': 'Quarterfinals', 'best_of': '5',
                            'score': {'competitor1_id': 'a', 'competitor2_id': 'b',
                                      'winner': 'b', 'status': 'closed', 'match_status': 'ended'}}}
        self.assertEqual(context(matches, 'EVENT-A', 101)['state'], 'lost')
        self.assertEqual(context(matches, 'EVENT-A', 200)['state'], 'stale')
        self.assertEqual(context(matches, 'EVENT-B', 101)['state'], 'identity-mismatch')
        self.assertFalse(future_round('Quarterfinals', 'KXATPADVANCE-26USOQUAR-A'))
        self.assertTrue(future_round('Quarterfinals', 'KXATPADVANCE-26USOSEMI-A'))
        self.assertFalse(future_round(None, 'KXATPADVANCE-26USOFIN-A'))

    def test_confirmed_score_needs_no_r_and_winner_vetoes(self):
        b, l = self.make_bot()
        l.match_tk = 'EVENT-A'
        b.books[l.match_tk] = b.books['M']
        l.rn = 0
        b.scores = {'EVENT': {'players': {'EVENT-A': {'id': 'a'}},
                             'milestone_id': 'id', 'received_at': time.time(),
                             'round': 'Round Of 16', 'score': {
                                 'competitor1_id': 'a', 'competitor2_id': 'b',
                                 'winner': 'b', 'status': 'closed', 'match_status': 'ended'}}}
        c, reason = b.evaluate(l)
        self.assertIsNone(reason)
        self.assertEqual(c['signal'], 'score-confirmed')
        b.scores['EVENT']['score']['winner'] = 'a'
        self.assertEqual(b.evaluate(l)[1], 'score-confirmed-winner')

    def test_winner_only_buys_newly_secured_round(self):
        self.assertTrue(secured_ticker('Round Of 16', 'KXWTAADVANCE-26USOQUAR-NOS'))
        self.assertFalse(secured_ticker('Round Of 16', 'KXWTAADVANCE-26USOFIN-NOS'))
        self.assertFalse(secured_ticker('Round Of 16', 'KXWTA-26USO-NOS'))
        self.assertTrue(secured_ticker('Final', 'KXWTA-26USO-NOS'))

    def test_ended_final_score_does_not_wait_for_closed(self):
        score = {'competitor1_id': 'a', 'competitor2_id': 'b', 'winner': 'b',
                 'status': 'ended', 'match_status': 'ended',
                 'competitor1_overall_score': 0, 'competitor2_overall_score': 3}
        self.assertEqual(confirmed_winner(score, '5'), 'b')
        score['competitor2_overall_score'] = 2
        self.assertIsNone(confirmed_winner(score, '5'))
        self.assertEqual(confirmed_winner(score, '3'), 'b')
        score['winner'] = 'a'
        self.assertIsNone(confirmed_winner(score, '3'))

    def test_confirmation_only_variant_rejects_inferred_books(self):
        b, l = self.make_bot()
        b.cfg.v['require_score_confirmation'] = True
        b.books[l.match_tk].yes.clear()
        b.books[l.match_tk].no = {.99: 1000}
        self.assertEqual(b.evaluate(l)[1], 'awaiting-score-confirmation')

    def test_follower_discovery_add_refresh_close_and_expiry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, 'discovery.json')
            row = ['ATP', 'MATCH', 'Tournament', {'A': {'match': 'M', 'name': 'A', 'legs': [['T', 'E']]}}]
            data = {'updated_at': time.time(), 'groups': [row],
                    'started': [['ATP', 'MATCH']], 'schedule': {}}
            with open(path, 'w') as f:
                json.dump(data, f)
            follower = FollowerDiscovery(path)
            newly, alive = follower.scan(set())
            self.assertEqual(newly, [row])
            self.assertEqual(alive, {('ATP', 'MATCH')})
            newly, _ = follower.scan({('ATP', 'MATCH'), ('WTA', 'OLD')})
            self.assertFalse(newly)
            self.assertEqual(follower.refreshed, [row])
            self.assertEqual(follower.closed, {('WTA', 'OLD')})
            data['updated_at'] -= 181
            with open(path, 'w') as f:
                json.dump(data, f)
            with self.assertRaises(ValueError):
                follower.scan(set())

    def test_rest_verification_rejects_closed_and_absent_liquidity(self):
        market = {'market': {'status': 'active', 'result': ''}}
        book = {'orderbook_fp': {'yes_dollars': [['.01', '123.45']],
                                  'no_dollars': [['.01', '206']]}}
        self.assertEqual(rest_quantity(market, book, 'yes', .01), (123.45, 'verified'))
        self.assertEqual(rest_quantity(market, book, 'no', .01), (206, 'verified'))
        self.assertEqual(rest_quantity(market, book, 'yes', .02)[0], 0)
        self.assertEqual(rest_quantity({'market': {'status': 'closed'}}, book, 'yes', .01)[0], 0)
        self.assertEqual(rest_quantity(market, {}, 'yes', .01)[0], 0)

    def test_winner_fill_consumes_asks_and_restores(self):
        b, l = self.make_bot()
        l.match_tk, l.win_tk = 'EVENT-A', 'KXATP-26TEST-A'
        b.books[l.match_tk] = b.books['M']
        b.books[l.win_tk].snapshot({'yes_dollars_fp': [['.98', '500']],
                                   'no_dollars_fp': [['.01', '206']]}, 1)
        b.feed_ready = True
        b.scores = {'EVENT': {'players': {'EVENT-A': {'id': 'a'}},
                             'milestone_id': 'id', 'received_at': time.time(),
                             'round': 'Final', 'score': {
                                 'competitor1_id': 'a', 'competitor2_id': 'b',
                                 'winner': 'a', 'status': 'closed', 'match_status': 'ended'}}}
        directory = tempfile.mkdtemp(prefix='qualifier-test-')
        q = QualifierPaper(b, directory)
        q.book_update(l.win_tk, {}, b.books[l.win_tk], True)
        c = q.candidate(l)
        self.assertEqual(c['count'], 206)
        asyncio.run(q.take(c))
        self.assertEqual(q.positions[l.win_tk]['count'], 206)
        self.assertAlmostEqual(q.positions[l.win_tk]['cost'], 204.09)
        self.assertIsNone(q.candidate(l))
        restored = QualifierPaper(b, directory)
        self.assertEqual(restored.positions, q.positions)


if __name__ == '__main__':
    unittest.main()
