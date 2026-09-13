import _test_environment
import tempfile
import unittest
from pathlib import Path
from pilot_execution import Ledger, exchange_cash, reserve_cost

class CashTests(unittest.TestCase):
    def balance(self):
        return {'balance':87974,'updated_ts':1000,'balance_breakdown':[
            {'exchange_index':0,'balance':'99.8211'},
            {'exchange_index':3,'balance':'779.9277'}]}

    def test_rejected_alcaraz_order_sized_to_its_exchange(self):
        index,cash=exchange_cash({'exchange_index':0},self.balance(),1000)
        with tempfile.TemporaryDirectory() as d:
            ledger=Ledger(Path(d)/'ledger.json',1000,200)
            row=ledger.prepare({'ticker':'ALC','match':'MATCH','side':'ask',
                'price':.09,'quantity':124},cash_limit=cash)
            self.assertEqual(index,0)
            self.assertEqual(row['count'],109)
            self.assertLessEqual(reserve_cost('ask',.09,row['count']),cash)
            self.assertGreater(reserve_cost('ask',.09,row['count']+1),cash)
            self.assertEqual(Ledger(Path(d)/'ledger.json',1000,200).used(),ledger.used())

    def test_other_exchange_cash_does_not_limit_shelton(self):
        _,cash=exchange_cash({'exchange_index':3},self.balance(),1000)
        with tempfile.TemporaryDirectory() as d:
            ledger=Ledger(Path(d)/'ledger.json',1000,200)
            row=ledger.prepare({'ticker':'SHE','match':'MATCH','side':'bid',
                'price':.88,'quantity':200},cash_limit=cash)
            self.assertEqual(row['count'],200)

    def test_missing_stale_duplicate_nonfinite_fail_closed(self):
        cases=[({},self.balance()),({'exchange_index':0},{'balance':87974}),
            ({'exchange_index':0},dict(self.balance(),updated_ts=900))]
        for value in ('NaN','Infinity','-1'):
            b=self.balance(); b['balance_breakdown'][0]['balance']=value
            cases.append(({'exchange_index':0},b))
        b=self.balance();b['balance_breakdown']*=2;cases.append(({'exchange_index':0},b))
        for market,balance in cases:
            with self.subTest(market=market,balance=balance),self.assertRaises(ValueError):
                exchange_cash(market,balance,1000)

    def test_zero_cash_no_intent_and_caps_still_bind(self):
        with tempfile.TemporaryDirectory() as d:
            ledger=Ledger(Path(d)/'ledger.json',1000,200)
            c={'ticker':'T','match':'M','side':'ask','price':.01,'quantity':500}
            self.assertIsNone(ledger.prepare(c,cash_limit=0))
            self.assertFalse(ledger.state['orders'])
            row=ledger.prepare(c,cash_limit=900)
            self.assertLessEqual(reserve_cost('ask',.01,row['count']),200)

if __name__=='__main__':unittest.main()
