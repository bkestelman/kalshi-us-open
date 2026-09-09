import _test_environment  # Before any production module importing iolib.
import asyncio
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from kalshi import Book
from recovery_policy import Revival, choose_plan, supported_fee_schedule
from recovery_paper import Capacity, RecoveryPaper


def book(yes=(), no=()):
    b=Book(); b.yes=dict(yes); b.no=dict(no); return b


def position(side='ask',quantity=100):
    return {'source':'pilot_live','ticker':'RELATED','match_ticker':'MATCH-A','match':'MATCH',
            'side':side,'price':.01 if side=='ask' else .99,'quantity':quantity,
            'protected':0,'attempts':[],'status':'watching','retry_at':0}


class PolicyTests(unittest.TestCase):
    def test_match_maker_fee_category_has_standard_taker_fee(self):
        self.assertTrue(supported_fee_schedule({'fee_type':'quadratic_with_maker_fees','fee_multiplier':1}))
        self.assertTrue(supported_fee_schedule({'fee_type':'quadratic','fee_multiplier':1}))
        self.assertFalse(supported_fee_schedule({'fee_type':'quadratic','fee_multiplier':2}))
        self.assertFalse(supported_fee_schedule({'fee_type':'flat','fee_multiplier':1}))
        self.assertFalse(supported_fee_schedule({'fee_type':'quadratic','fee_multiplier':None}))

    def test_zheng_prefers_99c_exit_over_14c_hedge(self):
        p=position()
        plan=choose_plan(p,{'RELATED':book(no=[(.99,100)]),'MATCH-A':book([(.1,100)],[(.86,100)])},50)
        self.assertEqual((plan['kind'],plan['quantity']),('exit',100))
        self.assertAlmostEqual(plan['gross']-plan['fee']-99.07,-.14)

    def test_bad_exit_rejected_and_hedge_budget_partial(self):
        p=position('bid',5)
        books={'RELATED':book([(.01,100)]),'MATCH-A':book([(.97,100)],[(.02,100)])}
        plan=choose_plan(p,books,.10)
        self.assertEqual(plan['kind'],'hedge')
        self.assertEqual(plan['quantity'],3)
        self.assertLessEqual(plan['gross']+plan['fee'],.10000001)

    def test_no_unbounded_hedge_or_liquidation(self):
        p=position('bid',5)
        self.assertIsNone(choose_plan(p,{'RELATED':book([(.01,100)]),'MATCH-A':book([(.7,100)],[(.29,100)])},50))

    def test_fractional_partial_depth(self):
        p=position('bid',5)
        plan=choose_plan(p,{'RELATED':book([(.99,1.25)])},.1)
        self.assertEqual(plan['quantity'],1.25)

    def test_persistence_reconnect_and_feed_gap(self):
        r=Revival(); b=book([(.1,10)],[(.89,10)])
        self.assertFalse(r.update(b,'ask',0))
        self.assertTrue(r.update(b,'ask',5))
        replacement=book([(.1,10)],[(.89,10)])
        self.assertFalse(r.update(replacement,'ask',6))
        self.assertFalse(r.update(replacement,'ask',10))
        self.assertFalse(r.update(replacement,'ask',11,ready=False))
        self.assertFalse(r.update(replacement,'ask',12))
        self.assertTrue(r.update(replacement,'ask',17))

    def test_two_cent_winning_revival_never_triggers(self):
        r=Revival(); b=book([(.97,100)],[(.02,100)])
        self.assertFalse(r.update(b,'bid',0))
        self.assertFalse(r.update(b,'bid',60))

    def test_capacity_both_sides_and_restart(self):
        c=Capacity(); b=book(no=[(.99,10)])
        c.observe('a','T',b)
        c.consume('a',{'ticker':'T','ladder':'no','kind':'exit','levels':[(.99,10)]})
        self.assertFalse(c.book('a','T',b).no)
        c=Capacity(json.loads(json.dumps(c.state)))
        replacement=book(no=[(.99,10)])
        self.assertFalse(c.book('a','T',replacement).no)
        replacement.no[.99]=12
        self.assertEqual(c.book('a','T',replacement).no[.99],2)
        self.assertEqual(c.book('b','T',replacement).no[.99],12)


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.patch=patch('recovery_paper.OUT',str(self.root))
        self.patch.start(); self.addCleanup(self.patch.stop)
        self.bot=RecoveryPaper()
        self.addCleanup(self.bot.pool.shutdown)
        self.addCleanup(self.bot.order_pool.shutdown)
        self.bot.jlog=lambda row:None
        self.bot.ingest=lambda:None
        self.bot.ledgers={'pilot_live':{'orders':{}}}
        self.bot.feed_ready=True; self.bot.last_message_at=time.time()
        self.p=position('bid',5)
        self.bot.state['positions']['test']=self.p
        self.match=book([(.85,20)],[(.14,20)])
        self.bot.books['MATCH-A']=self.match
        self.bot.books['RELATED']=book([(.99,5)])

    def test_delayed_rest_fill_and_durable_protected_quantity(self):
        plan=choose_plan(self.p,self.bot.books,50)
        # Requested five at decision; only four survive until execution.
        plan['quantity']=5
        self.bot.books['RELATED'].yes[.99]=4
        intent={'plan':plan}; self.p['attempts'].append(intent)
        def get(path):
            if path.endswith('/orderbook'):
                return {'orderbook_fp':{'yes_dollars':[['.99','4']],'no_dollars':[]}}
            return {'market':{'status':'active','result':''}}
        with patch('recovery_paper.get',side_effect=get):
            asyncio.run(self.bot.execute_paper('test',intent,self.match))
        self.assertEqual(self.p['protected'],4)
        self.assertEqual(self.p['status'],'partial')
        saved=json.loads(self.bot.path.read_text())
        self.assertEqual(saved['positions']['test']['protected'],4)
        self.assertEqual(saved['capacity']['pilot_live']['RELATED']['yes']['0.9900']['available'],0)

    def test_reconnect_during_submission_is_no_fill(self):
        intent={'plan':choose_plan(self.p,self.bot.books,50)}
        self.p['attempts'].append(intent)
        self.bot.books['MATCH-A']=book([(.85,20)],[(.14,20)])
        def get(path):
            return {'orderbook_fp':{'yes_dollars':[['.99','4']]}} if path.endswith('/orderbook') else {'market':{'status':'active'}}
        with patch('recovery_paper.get',side_effect=get):
            asyncio.run(self.bot.execute_paper('test',intent,self.match))
        self.assertNotIn('fill',intent)
        self.assertEqual(self.p['protected'],0)

    def test_favorable_score_veto(self):
        with patch('recovery_paper.context',return_value={'state':'won'}):
            self.bot.evaluate_recovery()
        self.assertEqual(self.p['status'],'score_confirmed')
        self.assertFalse(self.p['attempts'])

    def test_hedge_settlement_compares_against_hold(self):
        self.p['attempts']=[{'fill':{'kind':'hedge','ticker':'MATCH-A','ladder':'yes',
                                  'quantity':5,'gross':.15,'fee':.02}}]
        self.p['protected']=5; self.p['status']='protected'
        with patch('recovery_paper.get',return_value={'market':{'result':'yes'}}):
            asyncio.run(self.bot.settlements())
        self.assertAlmostEqual(self.p['baseline_pnl'],.04)
        self.assertAlmostEqual(self.p['pnl'],-.13)
        self.assertEqual(self.p['status'],'settled')
        # Settlement does not recycle the simulated hedge allocation.
        self.assertAlmostEqual(self.bot.budget(self.p),49.83)

    def test_no_live_budget_recycling_after_settlement(self):
        self.bot.ledgers['pilot_live']['orders']['old']={
            'client_order_id':'old','status':'filled','reserved':'49.90','match':'MATCH'}
        self.p['status']='settled'
        self.assertAlmostEqual(self.bot.budget(self.p),.10)

    def test_pending_restart_never_repeats_intent(self):
        self.p['status']='pending'; self.bot.save()
        restored=RecoveryPaper()
        self.addCleanup(restored.pool.shutdown); self.addCleanup(restored.order_pool.shutdown)
        self.assertEqual(restored.state['positions']['test']['status'],'interrupted')


if __name__=='__main__':
    unittest.main()
