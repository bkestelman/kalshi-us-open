import _test_environment
import asyncio,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from kalshi import Book
from staged_recovery.ledger import RecoveryLedger,recovery_cost
from staged_recovery.runner import RecoveryPilot,Detector,order_body,validate_config
from recovery_policy import choose_plan
from test_pilot import FakeExchange
from pilot_execution import Ledger,D


def book(yes=(),no=()):
    b=Book();b.yes=dict(yes);b.no=dict(no);return b

class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'ledger.json';self.ledger=RecoveryLedger(self.path)
        self.entry=self.ledger.prepare({'ticker':'RELATED','match':'MATCH','match_ticker':'MATCH-A',
            'side':'bid','price':.99,'quantity':5})
        self.ledger.accept(self.entry,{'order_id':'entry','fill_count_fp':'5'},True)
        self.cid=self.entry['client_order_id']
        self.exit={'kind':'exit','ticker':'RELATED','ladder':'yes','quantity':5,'limit':.97}
        self.hedge={'kind':'hedge','ticker':'MATCH-A','ladder':'yes','quantity':5,'limit':.20}

    def test_exit_closes_yes_and_reserves_only_fee(self):
        row=self.ledger.prepare_recovery(self.cid,self.exit,100,position_limit=5)
        self.assertEqual(row['side'],'ask');self.assertEqual(D(row['reserved']),D('.02'))
        self.assertEqual(order_body(row)['count'],'5.00');self.assertTrue(order_body(row)['reduce_only'])
        self.assertEqual(order_body(row)['price'],'0.9700')
        self.assertIsNone(self.ledger.prepare({'match':'OTHER','ticker':'X','side':'bid','price':.99,'quantity':5}))
        self.assertEqual(RecoveryLedger(self.path).used(),self.ledger.used())
        # Old entry-only runner fails safely on a recovery ledger, preventing
        # rollback from silently ignoring recovery allocation.
        with self.assertRaises(ValueError):Ledger(self.path,1000,200)

    def test_no_exit_buys_yes_reduce_only_at_complement(self):
        self.entry['side']='ask';self.entry['price']=.01;self.ledger.save()
        plan=dict(self.exit,ladder='no')
        row=self.ledger.prepare_recovery(self.cid,plan,100,position_limit=5)
        self.assertEqual(row['side'],'bid');self.assertEqual(order_body(row)['price'],'0.0300')
        self.assertTrue(order_body(row)['reduce_only'])

    def test_fractional_partial_fill_restart_never_overprotects(self):
        row=self.ledger.prepare_recovery(self.cid,dict(self.exit,quantity=1.25),100,position_limit=5)
        self.assertTrue(self.ledger.accept(row,{'order_id':'r','fill_count_fp':'.75'},True))
        ledger=RecoveryLedger(self.path)
        self.assertEqual(ledger.remaining(self.cid),D('4.25'))
        row=ledger.state['orders'][row['client_order_id']];row['created_at']-=6
        second=ledger.prepare_recovery(self.cid,self.hedge,100)
        self.assertEqual(D(second['count']),D('4.25'))
        self.assertFalse(order_body(second)['reduce_only'])
        ledger.accept(second,{'order_id':'r2','fill_count_fp':'4.25'},True)
        self.assertEqual(ledger.remaining(self.cid),0)
        self.assertIsNone(ledger.prepare_recovery(self.cid,self.exit,100,position_limit=5))

    def test_caps_and_settlement_do_not_recycle(self):
        self.ledger.per_match=D('5.00')
        row=self.ledger.prepare_recovery(self.cid,self.hedge,100)
        self.assertLessEqual(self.ledger.used('MATCH'),D('5.00'))
        self.assertLess(D(row['count']),1)
        self.ledger.accept(row,{'order_id':'r','fill_count_fp':row['count']},True)
        before=self.ledger.used();self.ledger.state['recovery']['positions'][self.cid]={'status':'settled'}
        self.ledger.save();self.assertEqual(RecoveryLedger(self.path).used(),before)

    def test_no_cash_or_position_and_invalid_routes(self):
        self.assertIsNone(self.ledger.prepare_recovery(self.cid,self.exit,0,position_limit=5))
        self.assertIsNone(self.ledger.prepare_recovery(self.cid,self.exit,100,position_limit=0))
        for plan in (dict(self.exit,limit=.01),dict(self.exit,ticker='WRONG'),dict(self.hedge,limit=.21),dict(self.hedge,ticker='MATCH-B')):
            with self.assertRaises(ValueError):self.ledger.prepare_recovery(self.cid,plan,100,position_limit=5)

    def test_timeout_matching_cid_reconciles_partial_without_resubmission(self):
        row=self.ledger.prepare_recovery(self.cid,self.exit,100,position_limit=5)
        restored=RecoveryLedger(self.path)
        order={'order_id':'r','ticker':'RELATED','client_order_id':row['client_order_id'],
            'status':'canceled','fill_count_fp':'2.25'}
        restored.reconcile(FakeExchange(orders=[order]))
        self.assertFalse(restored.unresolved());self.assertEqual(restored.remaining(self.cid),D('2.75'))
        self.assertEqual(len(restored.state['orders']),2)

    def test_existing_entry_exposure_cannot_clear_ambiguous_exit(self):
        row=self.ledger.prepare_recovery(self.cid,self.exit,100,position_limit=5);row['created_at']=1000
        for now in (1600,1630,1660):
            self.ledger.reconcile(FakeExchange(fills=[{'order_id':'entry'}],now=now),now=now)
        self.assertEqual(row['status'],'unresolved')
        self.assertIsNone(self.ledger.prepare_recovery(self.cid,self.exit,100,position_limit=5))

    def test_three_attempt_limit_and_block_persist(self):
        for i in range(3):
            row=self.ledger.prepare_recovery(self.cid,self.exit,100,position_limit=5)
            self.ledger.accept(row,{'order_id':str(i),'fill_count_fp':'0'},True);row['created_at']-=6
        self.assertIsNone(self.ledger.prepare_recovery(self.cid,self.exit,100,position_limit=5))
        self.ledger.save();ledger=RecoveryLedger(self.path)
        self.assertIn('MATCH',ledger.state['recovery']['blocked_matches'])
        self.assertIsNone(ledger.prepare({'ticker':'NEW','match':'MATCH','side':'bid','price':.99,'quantity':5}))

    def test_save_failure_prevents_caller_obtaining_intent(self):
        with patch.object(self.ledger,'save',side_effect=OSError('disk')):
            with self.assertRaises(OSError):self.ledger.prepare_recovery(self.cid,self.exit,100,position_limit=5)

    def test_malformed_fills_remain_unresolved(self):
        row=self.ledger.prepare_recovery(self.cid,self.exit,100,position_limit=5)
        for response in ({}, {'order_id':'x','fill_count_fp':'NaN'}, {'order_id':'x','fill_count_fp':'6'},
                         {'order_id':'x','fill_count_fp':'1','ticker':'WRONG'}, {'order_id':'x','fill_count_fp':'1','status':'resting'}):
            self.assertFalse(self.ledger.accept(row,response,True))
        self.assertTrue(self.ledger.unresolved())

class DetectorTests(unittest.TestCase):
    def test_flicker_cross_stale_and_reconnect_reset(self):
        d=Detector(.05,30);b=book([(.05,100)],[(.94,100)])
        self.assertFalse(d.update(b,'ask',0,True));self.assertTrue(d.update(b,'ask',30,True))
        b.yes={.04:100};self.assertFalse(d.update(b,'ask',31,True))
        b.yes={.05:100};self.assertFalse(d.update(b,'ask',32,True))
        self.assertFalse(d.update(b,'ask',62,False));self.assertFalse(d.update(b,'ask',63,True))
        replacement=book([(.05,100)],[(.94,100)])
        self.assertFalse(d.update(replacement,'ask',100,True))
        replacement.no={.96:100};self.assertFalse(d.update(replacement,'ask',130,True))

    def test_shipped_configuration_cannot_trade(self):
        config=json.loads(Path('staged_recovery/config.json').read_text())
        with self.assertRaises(ValueError):validate_config(config)

class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        with patch('staged_recovery.runner.OUT',self.tmp.name):
            self.bot=RecoveryPilot({'enabled':True,'threshold':.05,'persistence':30,'allow_hedges':True})
        self.addCleanup(self.bot.pool.shutdown);self.addCleanup(self.bot.order_pool.shutdown)
        self.bot.jlog=lambda row:None
        self.bot.feed_ready=True;self.bot.last_message_at=time.time();self.bot.account_ready=True
        self.entry=self.bot.ledger.prepare({'ticker':'RELATED','match':'MATCH','match_ticker':'MATCH-A',
            'side':'bid','price':.99,'quantity':5})
        self.bot.ledger.accept(self.entry,{'order_id':'entry','fill_count_fp':'5'},True)
        self.cid=self.entry['client_order_id'];self.bot.adopt()
        self.match=book([(.94,100)],[(.05,100)])
        self.bot.books['MATCH-A']=self.match;self.bot.books['RELATED']=book([(.99,5)])
        detector=Detector(.05,30);detector.update(self.match,'bid',time.monotonic()-31,True)
        self.bot.detectors[self.cid]=detector
        self.posts=[]

    def get(self,path):
        tk=path.split('/')[2]
        if path.endswith('/orderbook'):
            return {'orderbook_fp':{'yes_dollars':[['.99','5']] if tk=='RELATED' else [['.94','100']],
                                   'no_dollars':[] if tk=='RELATED' else [['.05','100']]}}
        return {'market':{'ticker':tk,'status':'active','exchange_index':3}}

    def request(self,method,path,body=None):
        if method=='POST':
            self.posts.append(body)
            on_disk=json.loads(Path(self.bot.ledger.path).read_text())
            self.assertEqual(on_disk['orders'][body['client_order_id']]['status'],'unresolved')
            return 201,{'order_id':'recovery','fill_count_fp':'2.25'}
        if path.endswith('/exchange/user_data_timestamp'):
            from datetime import datetime,timezone
            return 200,{'as_of_time':datetime.now(timezone.utc).isoformat()}
        return 200,{'updated_ts':time.time(),'balance_breakdown':[{'exchange_index':3,'balance':'800'}]}

    def run_recover(self,score='unconfirmed',positions=None,call=None):
        with patch('staged_recovery.runner.get',side_effect=self.get), \
             patch('staged_recovery.runner.request',side_effect=call or self.request), \
             patch('staged_recovery.runner.read_pages',return_value=positions if positions is not None else [{'ticker':'RELATED','position_fp':'5'}]), \
             patch('staged_recovery.runner.context',return_value={'state':score}):
            asyncio.run(self.bot.recover(self.cid,self.match,False))

    def test_full_decision_to_partial_exchange_fill(self):
        self.run_recover()
        self.assertEqual(len(self.posts),1);self.assertTrue(self.posts[0]['reduce_only'])
        self.assertEqual(self.bot.ledger.remaining(self.cid),D('2.75'))
        self.assertEqual(self.bot.positions()[self.cid]['status'],'partial')
        self.assertIsNone(self.bot.last_error)

    def test_favorable_score_stops_post_after_reads(self):
        self.run_recover(score='won');self.assertFalse(self.posts)
        self.assertEqual(self.bot.positions()[self.cid]['status'],'score_confirmed')

    def test_reconnect_or_stale_feed_prevents_post(self):
        self.bot.books['MATCH-A']=book([(.94,100)],[(.05,100)])
        self.run_recover();self.assertFalse(self.posts)
        self.bot.books['MATCH-A']=self.match;self.bot.last_message_at=time.time()-31
        self.run_recover();self.assertFalse(self.posts)

    def test_changed_account_exposure_halts_without_order(self):
        self.run_recover(positions=[{'ticker':'RELATED','position_fp':'1'}])
        self.assertFalse(self.posts);self.assertFalse(self.bot.account_ready)

    def test_transport_timeout_one_post_and_restart_reservation(self):
        def request(method,path,body=None):
            if method=='POST':self.posts.append(body);return -1,{'error':'TimeoutError'}
            return self.request(method,path,body)
        self.run_recover(call=request)
        self.assertEqual(len(self.posts),1);self.assertTrue(self.bot.ledger.unresolved())
        restored=RecoveryLedger(self.bot.ledger.path)
        self.assertEqual(restored.used(),self.bot.ledger.used());self.assertTrue(restored.unresolved())

    def test_stop_prevents_order_and_cash_read_failure_halts(self):
        self.bot.disabled_path.touch();self.run_recover();self.assertFalse(self.posts)
        self.bot.disabled_path.unlink()
        self.run_recover(call=lambda *a:(503,{}))
        self.assertFalse(self.posts);self.assertFalse(self.bot.account_ready)


class CashRunnerTests(unittest.TestCase):
    def test_cash_preparation_rechecks_signal_and_never_posts_on_missing_shard(self):
        from pilot import Pilot
        from types import SimpleNamespace
        with tempfile.TemporaryDirectory() as d, patch('pilot.OUT',d), patch('pilot.SHARED',Path(d)):
            (Path(d)/'winner_taker_discovery.json').write_text('{}')
            (Path(d)/'pilot_config.json').write_text(json.dumps(dict(total_cap=1000,per_match_cap=200,recycle_on_settlement=True)))
            bot=Pilot(True)
            self.addCleanup(bot.pool.shutdown);self.addCleanup(bot.order_pool.shutdown)
            bot.jlog=lambda r:None;bot.last_message_at=time.time()
            bot.account_ready=True;bot.feed_ready=True
            leg=SimpleNamespace(win_tk='T')
            candidate={'ticker':'T','match':'M','match_ticker':'M-A','side':'ask','price':.09,'quantity':124}
            seen=[]
            async def execute(row, batch=False):seen.append(row)
            bot.execute=execute
            with patch('pilot.get',return_value={'market':{'ticker':'T','status':'active','exchange_index':0}}), \
                 patch('pilot.request',return_value=(200,{'updated_ts':time.time(),'balance_breakdown':[{'exchange_index':0,'balance':'99.8211'}]})), \
                 patch('pilot.candidate',return_value=candidate):
                asyncio.run(bot.prepare_live(leg))
            self.assertEqual(seen[0]['count'],109)
            self.assertEqual(seen[0]['exchange_index'],0)
            bot.ledger.accept(seen[0],{'order_id':'x','fill_count_fp':'0'},True);seen.clear()
            with patch('pilot.get',return_value={'market':{'ticker':'T','status':'active','exchange_index':0}}), \
                 patch('pilot.request',return_value=(200,{'balance':90000,'updated_ts':time.time()})), \
                 patch('pilot.candidate',return_value=candidate):
                asyncio.run(bot.prepare_live(leg))
            self.assertFalse(seen);self.assertIn('per-exchange',bot.cash_error)

class AdversarialTests(unittest.TestCase):
    setUp = LedgerTests.setUp
    def test_randomized_budgets_prices_and_fractions(self):
        import random
        rng=random.Random(9009)
        for i in range(100):
            with tempfile.TemporaryDirectory() as d:
                ledger=RecoveryLedger(Path(d)/'l.json')
                side=rng.choice(['bid','ask']);paid=D(rng.randint(85,99))/100
                price=paid if side=='bid' else 1-paid
                entry=ledger.prepare({'ticker':'T','match':'M','match_ticker':'M-A','side':side,
                    'price':str(price),'quantity':rng.randint(1,100)})
                ledger.accept(entry,{'order_id':'e','fill_count_fp':str(entry['count'])},True)
                kind=rng.choice(['exit','hedge'])
                plan={'kind':kind,'ticker':'T' if kind=='exit' else 'M-A',
                    'ladder':'yes' if side=='bid' else 'no','quantity':rng.randint(1,10000)/100,
                    'limit':float(paid-D('.02')) if kind=='exit' else .20}
                cash=D(rng.randint(0,2000))/100
                row=ledger.prepare_recovery(entry['client_order_id'],plan,cash,position_limit=entry['count'])
                if row:
                    self.assertLessEqual(D(row['reserved']),cash)
                    self.assertLessEqual(ledger.used('M'),200)
                    self.assertLessEqual(D(row['count']),D(entry['count']))
                    self.assertLessEqual(D(row['count']),D(plan['quantity']))
                    restored=RecoveryLedger(ledger.path)
                    self.assertEqual(restored.used(),ledger.used())

    def test_corrupt_recovery_mapping_or_quantity_refuses_startup(self):
        row=self.ledger.prepare_recovery(self.cid,self.hedge,100)
        self.ledger.accept(row,{'order_id':'r','fill_count_fp':'5'},True)
        original=json.loads(Path(self.ledger.path).read_text())
        for field,value in [('ticker','WRONG'),('filled','5.01'),('count','NaN')]:
            damaged=json.loads(json.dumps(original));damaged['orders'][row['client_order_id']][field]=value
            Path(self.ledger.path).write_text(json.dumps(damaged))
            with self.assertRaises((ValueError,ArithmeticError)):RecoveryLedger(self.ledger.path)

if __name__=='__main__':unittest.main()
