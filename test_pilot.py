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


class FakeExchange:
    def __init__(self, orders=None, fills=None, positions=None, settlements=None, now=1600):
        self.orders, self.fills = orders or [], fills or []
        self.positions, self.settlements = positions or [], settlements or []
        self.now = now
        self.cutoff = 0
        self.stale = False
        self.fail = None
        self.historical_orders = []
        self.historical_fills = []

    def __call__(self, method, path):
        from datetime import datetime, timezone
        path = path.split('?')[0]
        if path.endswith(self.fail or 'NOFAIL'):
            return 503, {}
        stamp = lambda t: datetime.fromtimestamp(t,timezone.utc).isoformat()
        if path.endswith('/historical/cutoff'):
            return 200, {'orders_updated_ts':stamp(self.cutoff), 'trades_created_ts':stamp(self.cutoff)}
        if path.endswith('/exchange/user_data_timestamp'):
            return 200, {'as_of_time':stamp(self.now-300 if self.stale else self.now)}
        for suffix,key,rows in [('/portfolio/orders','orders',self.orders),
                                 ('/historical/orders','orders',self.historical_orders),
                                 ('/portfolio/fills','fills',self.fills),
                                 ('/historical/fills','fills',self.historical_fills),
                                 ('/portfolio/positions','market_positions',self.positions),
                                 ('/portfolio/settlements','settlements',self.settlements)]:
            if path.endswith(suffix):
                return 200, {key:rows,'cursor':''}
        raise AssertionError(path)


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
            self.ledger.reconcile(FakeExchange(orders=[order]))
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

    def test_authorized_cap_increase_preserves_used_budget(self):
        row = self.ledger.prepare(c())
        self.ledger.accept(row, {'order_id':'old', 'fill_count':'5'}, terminal=True)
        expanded = Ledger(self.path, total=250, per_match=50)
        self.assertEqual(expanded.used(), reserve_cost('bid', .99, 5))
        for i in range(5):
            row=expanded.prepare(c(match='new'+str(i)))
            if row:
                self.assertLessEqual(reserve_cost(row['side'],row['price'],row['count']),50)
                expanded.accept(row, {'order_id':str(i),'fill_count':str(row['count'])}, terminal=True)
        self.assertLessEqual(expanded.used(),250)
        self.assertIsNone(expanded.prepare(c(match='beyond')))

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

    def old_order(self):
        with patch('pilot_execution.time.time', return_value=1000):
            return self.ledger.prepare(c())

    def test_ten_minute_cutoff_requires_three_spaced_clean_scans(self):
        row=self.old_order(); exchange=FakeExchange()
        for now in [1599,1600,1610,1629,1630]:
            exchange.now=now; self.ledger.reconcile(exchange,now=now)
            self.assertEqual(row['status'],'unresolved')
        exchange.now=1660; self.ledger.reconcile(exchange,now=1660)
        self.assertEqual(row['status'],'not_found')
        self.assertEqual(self.ledger.used(),0)
        restored=Ledger(self.path)
        self.assertEqual(restored.state['orders'][row['client_order_id']]['status'],'not_found')

    def test_positions_fills_settlements_stale_or_failed_reads_block_release(self):
        for kwargs in [dict(fills=[{'order_id':'unknown'}]),
                       dict(positions=[{'ticker':'ticker','position_fp':'0.50','market_exposure_dollars':'.5'}]),
                       dict(settlements=[{'ticker':'ticker'}])]:
            with self.subTest(kwargs=kwargs):
                row=self.old_order(); exchange=FakeExchange(**kwargs)
                for now in [1600,1630,1660]:
                    exchange.now=now; self.ledger.reconcile(exchange,now=now)
                self.assertEqual(row['status'],'unresolved')
                self.ledger.state['orders'].clear(); self.ledger.save()
        for setting in ['stale','fail']:
            row=self.old_order(); exchange=FakeExchange()
            setattr(exchange,setting,True if setting=='stale' else '/portfolio/fills')
            for now in [1600,1630,1660]:
                exchange.now=now; self.ledger.reconcile(exchange,now=now)
            self.assertEqual(row['status'],'unresolved')
            self.ledger.state['orders'].clear(); self.ledger.save()

    def test_failed_scan_resets_consecutive_evidence(self):
        row=self.old_order(); exchange=FakeExchange()
        self.ledger.reconcile(exchange,now=1600)
        exchange.fail='/portfolio/fills';exchange.now=1630
        self.ledger.reconcile(exchange,now=1630)
        self.assertNotIn('absence_checks',row)
        exchange.fail=None;exchange.now=1660
        self.ledger.reconcile(exchange,now=1660)
        self.assertEqual(len(row['absence_checks']),1)

    def test_historical_order_and_late_fill_keep_risk(self):
        row=self.old_order(); exchange=FakeExchange();exchange.cutoff=1700
        order={'order_id':'late','client_order_id':row['client_order_id'],
               'ticker':'ticker','status':'executed','fill_count_fp':'1.00'}
        exchange.historical_orders=[order]
        self.ledger.reconcile(exchange,now=1600)
        self.assertEqual(row['status'],'filled')
        self.assertGreater(self.ledger.used(),0)
        # Simulate a persisted prior inference, then subsequent exchange discovery.
        row['status']='not_found';self.ledger.save()
        self.ledger.reconcile(exchange,now=1700)
        self.assertEqual(row['status'],'filled')
        self.assertTrue(self.ledger.state['halt_reason'])
        self.assertIsNone(self.ledger.prepare(c(match='other')))

    def test_historical_fill_with_zero_position_blocks_release(self):
        row=self.old_order();exchange=FakeExchange();exchange.cutoff=1700
        exchange.historical_fills=[{'order_id':'unattributed'}]
        for now in [1600,1630,1660]:
            exchange.now=now;self.ledger.reconcile(exchange,now=now)
        self.assertEqual(row['status'],'unresolved')

    def test_late_unattributed_fill_restores_reservation_and_halts(self):
        row=self.old_order();exchange=FakeExchange()
        for now in [1600,1630,1660]:
            exchange.now=now;self.ledger.reconcile(exchange,now=now)
        self.assertEqual(row['status'],'not_found')
        exchange.fills=[{'order_id':'late-with-missing-order'}];exchange.now=1700
        self.ledger.reconcile(exchange,now=1700)
        self.assertEqual(row['status'],'unresolved')
        self.assertTrue(self.ledger.state['halt_reason'])
        self.assertGreater(self.ledger.used(),0)

    def test_incomplete_pagination_cannot_prove_absence(self):
        row=self.old_order()
        def loop(*a):return 200, {'orders':[],'cursor':'repeat'}
        self.ledger.reconcile(loop,now=1700)
        self.assertEqual(row['status'],'unresolved')
        self.assertIn('repeated cursor',row['reconcile_error'])

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
