import _test_environment
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from kalshi import Book
from pilot_execution import Ledger, reserve_cost
from pilot_strategy import candidate


def c(side='bid', price=.99, match='match', tk='ticker'):
    return {'ticker': tk, 'match': match, 'side': side, 'price': price, 'quantity': 1000}


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name)/'ledger.json'
        self.ledger = Ledger(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ambiguous_request_survives_restart_and_blocks_other_match(self):
        row = self.ledger.prepare(c())
        self.assertEqual(row['count'], 5)
        restored = Ledger(self.path)
        self.assertIsNone(restored.prepare(c(match='other')))
        restored.reconcile(lambda *a: (200, {'orders': [], 'cursor': ''}))
        self.assertEqual(len(restored.unresolved()), 1)
        self.assertEqual(restored.used(), reserve_cost('bid', .99, 5))

    def test_reconcile_partial_fill_once_and_preserve_allocation(self):
        row = self.ledger.prepare(c())
        order = {'order_id': 'exchange-id', 'client_order_id': row['client_order_id'],
                 'ticker': row['ticker'], 'fill_count_fp': '2.00', 'status': 'canceled'}
        for _ in range(2):
            self.ledger.reconcile(lambda *a: (200, {'orders': [order]}))
        self.assertFalse(self.ledger.unresolved())
        self.assertEqual(self.ledger.used(), reserve_cost('bid', .99, 5))
        self.assertIsNone(self.ledger.prepare(c(side='ask', price=.01)))

    def test_both_sides_share_match_cap_and_total(self):
        for i in range(5):
            row = self.ledger.prepare(c(match=str(i), side='ask', price=.01))
            self.assertEqual(row['count'], 5)
            self.ledger.accept(row, {'order_id': str(i), 'fill_count': '5'}, terminal=True)
        self.assertLessEqual(self.ledger.used(), 25)
        self.assertIsNone(self.ledger.prepare(c(match='sixth')))
        self.assertIsNone(self.ledger.prepare(c(match='0', side='bid')))

    def test_malformed_and_nonterminal_responses_keep_reservation(self):
        row = self.ledger.prepare(c())
        for response in [{}, {'order_id': 'x'}, {'order_id': 'x', 'fill_count': '-1'},
                         {'order_id': 'x', 'fill_count': 'NaN'},
                         {'order_id': 'x', 'fill_count': '9'},
                         {'order_id': 'x', 'fill_count': '0', 'status': 'resting'}]:
            self.assertFalse(self.ledger.accept(row, response))
            self.assertEqual(len(self.ledger.unresolved()), 1)

    def test_terminal_zero_fill_releases_budget(self):
        row = self.ledger.prepare(c())
        self.assertTrue(self.ledger.accept(row, {'order_id': 'x', 'fill_count': '0'}, terminal=True))
        self.assertEqual(self.ledger.used(), 0)
        self.assertIsNotNone(self.ledger.prepare(c()))

    def test_save_failure_prevents_intent_return(self):
        with patch.object(self.ledger, 'save', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                self.ledger.prepare(c())

    def test_idle_connection_discarded_before_post_not_retried(self):
        from pilot_execution import request
        events = []
        response = SimpleNamespace(status=201, read=lambda: b'{}')
        conn = SimpleNamespace(request=lambda *a, **k: events.append('send'),
                               getresponse=lambda: response)
        with patch('pilot_execution._load', return_value=('id', 'priv')), \
             patch('pilot_execution.sign', return_value={}), \
             patch('pilot_execution._connection', return_value=conn), \
             patch('pilot_execution._request_clock', SimpleNamespace(last=0)), \
             patch('pilot_execution.time.monotonic', return_value=100), \
             patch('pilot_execution._drop', side_effect=lambda: events.append('drop')):
            request('POST', '/test', {})
        self.assertEqual(events, ['drop', 'send'])

    def test_single_attempt_post_on_transport_failure(self):
        from pilot_execution import request
        conn = SimpleNamespace(request=__import__('unittest.mock').mock.Mock(side_effect=TimeoutError()))
        with patch('pilot_execution._load', return_value=('id', 'priv')), \
             patch('pilot_execution.sign', return_value={}), \
             patch('pilot_execution._connection', return_value=conn), \
             patch('pilot_execution._drop'):
            self.assertEqual(request('POST', '/test', {})[0], -1)
            self.assertEqual(conn.request.call_count, 1)


class StrategyTests(unittest.TestCase):
    def setUp(self):
        self.leg = SimpleNamespace(tour='WTA', key='match', comp='US Open Women Singles',
                                  win_tk='KXWTAADVANCE-26USOQUAR-AAA', match_tk='MATCH-AAA')
        self.mb, self.wb = Book(), Book()
        self.owner = SimpleNamespace(feed_ready=True, disc=SimpleNamespace(started={('WTA','match')}),
                                     scores={}, books={self.leg.win_tk:self.wb,self.leg.match_tk:self.mb})
        self.score = {'state':'unconfirmed','round':'Round Of 16'}

    def get(self):
        with patch('pilot_strategy.context', return_value=self.score):
            return candidate(self.owner,self.leg)

    def test_yes_requires_one_sided_99_and_never_final_score(self):
        self.mb.yes={.99:100}; self.wb.no={.01:10}
        self.assertEqual(self.get()['side'],'bid')
        self.mb.no={.01:1}
        self.assertIsNone(self.get())

    def test_no_strict_one_cent_not_two_cent_or_model_edge(self):
        self.mb.no={.98:100}; self.wb.yes={.02:10}
        self.assertIsNone(self.get())
        self.mb.no={.99:100}
        self.assertEqual(self.get()['side'],'ask')
        self.mb.yes={.01:1}
        self.assertIsNone(self.get())

    def test_score_contradiction_veto_and_no_already_secured_round(self):
        self.mb.no={.99:100}; self.wb.yes={.02:10}
        self.score['state']='won'
        self.assertIsNone(self.get())
        self.score={'state':'unconfirmed','round':'Quarterfinals'}
        self.assertIsNone(self.get())

    def test_not_started_or_unready_feed_veto(self):
        self.mb.yes={.99:100}; self.wb.no={.01:10}
        self.owner.feed_ready=False
        self.assertIsNone(self.get())
        self.owner.feed_ready=True; self.owner.disc.started=set()
        self.assertIsNone(self.get())


if __name__=='__main__':
    unittest.main()
