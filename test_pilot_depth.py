import _test_environment
import tempfile
import unittest
import time
from unittest.mock import Mock, patch
from pathlib import Path
from kalshi import Book
from pilot_strategy import executable_levels
from pilot_execution import Ledger, D, reserve_cost
from pilot_depth import paper_sweep
from pilot import Pilot


class SweepTests(unittest.TestCase):
    def prepare(self, levels, side='bid', cash=1000, cap=200):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        ledger = Ledger(Path(tmp.name)/'ledger.json', 1000, cap)
        row = ledger.prepare({'ticker': 'GAU', 'match': 'MATCH', 'side': side,
                              'price': levels[-1][0], 'quantity': sum(q for _, q in levels),
                              'levels': levels}, cash_limit=cash)
        return ledger, row

    def test_gauff_sweeps_97_and_99_and_survives_restart_partial(self):
        ledger, row = self.prepare([[.97,36.31],[.99,3.07]])
        self.assertEqual((row['count'],row['price'],row['reserved']), (39,.99,'38.64'))
        ledger.accept(row, {'order_id':'gauff', 'fill_count':'9.38'}, terminal=True)
        restored = Ledger(ledger.path,1000,200)
        self.assertEqual(restored.used(), D('38.64'))
        self.assertEqual(restored.state['orders'][row['client_order_id']]['filled'], '9.38')

    def test_mirrored_no_sweep(self):
        _, row = self.prepare([[.03,36.31],[.01,3.07]], 'ask')
        self.assertEqual((row['count'],row['price'],row['reserved']), (39,.01,'38.64'))

    def test_budget_keeps_more_affordable_best_level_and_tight_limit(self):
        for side, levels, price in [('bid',[[.85,100],[.99,100]],.85),
                                    ('ask',[[.15,100],[.01,100]],.15)]:
            _, row = self.prepare(levels,side,cash=86)
            self.assertEqual((row['count'],row['price']), (100,price))

    def test_caps_and_cash_bound_worst_case_at_deeper_level(self):
        for cash, cap in [(40,200),(200,40)]:
            ledger,row = self.prepare([[.97,6.31],[.98,20],[.99,300]],cash=cash,cap=cap)
            self.assertEqual((row['count'],row['price']), (40,.99))
            self.assertLessEqual(D(row['reserved']),min(cash,cap))
            self.assertGreater(reserve_cost('bid',.99,41), min(cash,cap))
            ledger.accept(row,{'order_id':'one','fill_count':'40'},True)
            self.assertIsNone(ledger.prepare(dict(row,quantity=100),cash_limit=0))

    def test_fractional_levels_aggregate_and_bounds_hold(self):
        b=Book();b.no={.16:100,.12:.7,.02:.3,0:100}
        levels=executable_levels(b,'bid')
        self.assertEqual(levels,[[.88,.7],[.98,.3]])
        _,row=self.prepare(levels)
        self.assertEqual((row['count'],row['price']), (1,.98))

    def test_shadow_intersects_each_level_and_keeps_fractional_fills(self):
        row={'ticker':'GAU','side':'bid','price':.99,'count':39}
        b=Book();b.no={.03:6.31,.02:10,.01:3.069999999999993}
        market={'market':{'ticker':'GAU','status':'active'}}
        rest={'orderbook_fp':{'no_dollars':[['.03','6.31'],['.01','3.07']]}}
        fills,why=paper_sweep(row,b,market,rest)
        self.assertEqual(why,'verified')
        self.assertEqual(fills,[{'price':'0.97','quantity':'6.31'},
                                {'price':'0.99','quantity':'3.07'}])
        row['price']=.97
        self.assertEqual(paper_sweep(row,b,market,rest)[0],fills[:1])
        row['side']='ask';row['price']=.01
        b.yes=b.no.copy();rest['orderbook_fp']['yes_dollars']=rest['orderbook_fp']['no_dollars']
        self.assertEqual(sum(D(f['quantity']) for f in paper_sweep(row,b,market,rest)[0]),D('9.38'))
        market['market']['result']='yes'
        self.assertEqual(paper_sweep(row,b,market,rest)[0],[])

    def test_shadow_missing_malformed_and_disjoint_depth_do_not_fill(self):
        row={'ticker':'GAU','side':'bid','price':.99,'count':39}
        b=Book();b.no={.03:10}
        market={'market':{'ticker':'GAU','status':'active'}}
        for raw in [None,{}, {'orderbook_fp':{'no_dollars':[['.01','10']]}},
                    {'orderbook_fp':{'no_dollars':[['.03','NaN']]}}]:
            self.assertEqual(paper_sweep(row,b,market,raw)[0],[])


class ExecutionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.pilot=object.__new__(Pilot)
        self.pilot.ledger=Ledger(Path(self.tmp.name)/'ledger.json',1000,200)
        self.pilot.retry={};self.pilot.jlog=Mock();self.pilot.order_pool=None
        self.pilot.account_ready=True;self.pilot.feed_ready=True
        self.pilot.last_message_at=time.time();self.pilot.busy=True
        self.row=self.pilot.ledger.prepare({'ticker':'GAU','match':'MATCH','side':'bid',
            'price':.99,'quantity':39.38,'levels':[[.97,36.31],[.99,3.07]]})

    async def test_live_posts_one_sweep_ioc_with_durable_worst_case_reservation(self):
        self.pilot.live=True
        def exchange(method,path,body):
            restored=Ledger(self.pilot.ledger.path,1000,200)
            self.assertEqual(restored.used(),D('38.64'))
            self.assertEqual(body['count'],'39.00')
            self.assertEqual(body['price'],'0.9900')
            self.assertEqual(body['time_in_force'],'immediate_or_cancel')
            return 201,{'order_id':'fill','fill_count':'9.38'}
        with patch('pilot.request',side_effect=exchange) as call:
            await self.pilot.execute(self.row)
        self.assertEqual(call.call_count,1)
        self.assertEqual(self.row['filled'],'9.38')
        self.assertEqual(self.pilot.ledger.used(),D('38.64'))
        self.assertFalse(self.pilot.busy)

    async def test_shadow_fetches_one_book_and_persists_multi_level_fill(self):
        self.pilot.live=False
        b=Book();b.no={.03:6.31,.01:3.07};self.pilot.books={'GAU':b}
        def get(path):
            if path.endswith('/orderbook'):
                return {'orderbook_fp':{'no_dollars':[['.03','6.31'],['.01','3.07']]}}
            return {'market':{'ticker':'GAU','status':'active'}}
        with patch('pilot.get',side_effect=get) as call:
            await self.pilot.execute(self.row)
        self.assertEqual(call.call_count,2)
        self.assertEqual(self.row['filled'],'9.38')
        self.assertEqual(len(self.row['paper_execution']['levels']),2)
        restored=Ledger(self.pilot.ledger.path,1000,200)
        self.assertEqual(restored.state['orders'][self.row['client_order_id']]['filled'],'9.38')


if __name__ == '__main__':
    unittest.main()
