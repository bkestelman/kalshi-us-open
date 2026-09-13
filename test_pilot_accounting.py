import _test_environment
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pilot_execution import Ledger, D
from pilot_accounting import settlement_record, refresh, summary


class AccountingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/'ledger.json'
        self.ledger = Ledger(self.path, 10, 10, recycle=True)
        self.row = self.ledger.prepare(dict(ticker='T', match='M', side='bid', price=.9, quantity=10))
        self.ledger.accept(self.row, dict(order_id='O', fill_count='2.5'), terminal=True)
        self.row['created_at'] = 1
        self.fill = dict(order_id='O', ticker='T', fill_id='F', count_fp='2.5',
                         yes_price_dollars='.88', no_price_dollars='.12', fee_cost='.02')
        self.settlement = dict(ticker='T', market_result='yes', settled_time='2026-09-12T22:42:58Z')

    def test_partial_settlement_releases_full_reservation_and_preserves_history(self):
        self.assertGreater(self.ledger.used(), 9)
        self.row['settlement'] = settlement_record(self.row, [self.fill], self.settlement)
        self.ledger.save()
        restored = Ledger(self.path, 10, 10, recycle=True)
        self.assertEqual(restored.used(), 0)
        self.assertEqual(restored.used('M'), 0)
        self.assertEqual(summary(restored)['realized_profit'], '0.280')
        self.assertEqual(len(restored.state['orders']), 1)
        self.assertFalse(restored.prepare_batch([dict(ticker='T',match='M',side='bid',price=.9,quantity=2)]))
        self.assertGreater(Ledger(self.path,10,10).used(),9)

    def test_losses_and_no_side_use_actual_fees(self):
        self.row['side']='ask'
        r=settlement_record(self.row,[self.fill],self.settlement)
        self.assertEqual(D(r['net_profit']),D('-.32'))
        self.assertEqual(D(r['principal']),D('.30'))

    def test_missing_duplicate_and_unrelated_fills(self):
        with self.assertRaises(ValueError):settlement_record(self.row,[],self.settlement)
        other=dict(self.fill,order_id='MANUAL',fill_id='X',count_fp='100')
        r=settlement_record(self.row,[self.fill,self.fill,other],self.settlement)
        self.assertEqual(D(r['principal']),D('2.2'))
        for change in ({'count_fp':'NaN'},{'ticker':'OTHER'},{'fee_cost':'-1'}):
            with self.assertRaises(ValueError):settlement_record(self.row,[dict(self.fill,**change)],self.settlement)

    def test_refresh_failure_holds_allocation_and_reports(self):
        before=self.ledger.used()
        refresh(self.ledger,lambda *a:(500,{}),None,True)
        self.assertEqual(self.ledger.used(),before)
        self.assertTrue(summary(self.ledger)['accounting_error'])

    def test_signed_refresh_historical_and_restart(self):
        paths=[]
        def call(method,path):
            self.assertEqual(method,'GET')
            paths.append(path)
            if '/historical/cutoff' in path:
                return 200,dict(trades_created_ts='2026-09-01T00:00:00Z')
            if '/historical/fills' in path:
                return 200,dict(fills=[self.fill],cursor='')
            if '/portfolio/fills' in path:
                return 200,dict(fills=[],cursor='')
            if '/portfolio/settlements' in path:
                return 200,dict(settlements=[self.settlement],cursor='')
            self.fail(path)
        refresh(self.ledger,call,None,True)
        self.assertIsNone(summary(self.ledger)['accounting_error'])
        self.assertEqual(Ledger(self.path,10,10,recycle=True).used(),0)
        self.assertTrue(any('/historical/fills' in p for p in paths))

    def test_unsettled_outcome_and_incomplete_pagination_retain_budget(self):
        before=self.ledger.used()
        refresh(self.ledger,None,lambda p:dict(market=dict(ticker='T',status='closed',result='yes')),False)
        self.assertEqual(self.ledger.used(),before)
        refresh(self.ledger,lambda *a:(200,dict(fills=[self.fill])),None,True)
        self.assertEqual(self.ledger.used(),before)
        self.assertTrue(summary(self.ledger)['accounting_error'])

    def test_save_failure_never_releases_in_memory(self):
        data={'market':{'ticker':'T','status':'finalized','result':'yes'}}
        before=self.ledger.used()
        with patch.object(self.ledger,'save',side_effect=OSError('disk full')):
            with self.assertRaises(RuntimeError):refresh(self.ledger,None,lambda p:data,False)
        self.assertEqual(self.ledger.used(),before)

    def test_paper_release_is_idempotent_and_estimated(self):
        data={'market':{'ticker':'T','status':'finalized','result':'yes'}}
        refresh(self.ledger,None,lambda p:data,False)
        first=summary(self.ledger)
        refresh(self.ledger,None,lambda p:self.fail('already settled'),False)
        self.assertEqual(first,summary(self.ledger))
        self.assertEqual(self.ledger.used(),0)
        self.assertEqual(self.row['settlement']['source'],'paper_estimate')

    def test_unresolved_and_absent_are_not_settled(self):
        self.row['status']='unresolved'
        refresh(self.ledger,None,lambda p:self.fail('unresolved'),False)
        self.assertGreater(self.ledger.used(),9)
        self.ledger.total = self.ledger.used()+D('.1')
        self.assertTrue(summary(self.ledger)['budget_exhausted'])

if __name__ == '__main__':unittest.main()
