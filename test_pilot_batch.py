import _test_environment
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import random
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from pilot import Pilot
from pilot_execution import D, Ledger, reserve_cost


def c(ticker='A', side='bid', levels=None, shard=3, match='MATCH'):
    levels = levels or [[.99 if side == 'bid' else .01, 500]]
    return dict(ticker=ticker, match=match, side=side, price=levels[-1][0],
                levels=levels, quantity=sum(q for _, q in levels), exchange_index=shard)


class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.ledger = Ledger(Path(self.tmp.name)/'ledger.json', 1000, 200)

    def test_three_markets_share_cap_even_when_every_order_fills(self):
        rows = self.ledger.prepare_batch([c('SEMI'), c('FINAL'), c('WINNER', 'ask')], {3: D(1000)})
        self.assertEqual(len(rows), 3)
        self.assertLessEqual(max(r['count'] for r in rows)-min(r['count'] for r in rows), 1)
        self.assertLessEqual(self.ledger.used(), 200)
        for r in rows:
            self.ledger.accept(r, {'order_id':r['ticker'], 'fill_count':str(r['count'])}, True)
        self.assertLessEqual(Ledger(self.ledger.path,1000,200).used(), 200)

    def test_shallow_book_redistributes_room_and_shards_share_cash_once(self):
        rows = self.ledger.prepare_batch([c('A',levels=[[.99,2]]), c('B'), c('C',shard=0)], {3: D(20), 0:D(5)})
        by = {r['ticker']:r for r in rows}
        self.assertEqual(by['A']['count'],2)
        self.assertGreater(by['B']['count'],2)
        self.assertLessEqual(sum(D(r['reserved']) for r in rows if r['exchange_index']==3),20)
        self.assertLessEqual(D(by['C']['reserved']),5)

    def test_single_candidate_preserves_best_prefix_and_fractional_sweep(self):
        for levels, cash, count, price in [([[.85,100],[.99,100]],86,100,.85),
                                         ([[.97,36.31],[.99,3.07]],200,39,.99),
                                         ([[.88,.7],[.98,.3]],200,1,.98)]:
            self.ledger.state['orders'].clear()
            row, = self.ledger.prepare_batch([c(levels=levels)],{3:D(cash)})
            self.assertEqual((row['count'],row['price']),(count,price))

    def test_restart_blocks_and_partial_reservation_retained(self):
        rows = self.ledger.prepare_batch([c('A'),c('B')])
        restored = Ledger(self.ledger.path,1000,200)
        self.assertEqual(len(restored.unresolved()),2)
        self.assertEqual(restored.prepare_batch([c('C')]),[])
        used = self.ledger.used()
        self.ledger.accept(rows[0],{'order_id':'a','fill_count':'.01'},True)
        self.assertEqual(self.ledger.used(),used)
        self.assertEqual(self.ledger.prepare_batch([c('C')]),[])
        self.ledger.accept(rows[1],{'order_id':'b','fill_count':'0'},True)
        self.assertEqual(self.ledger.prepare_batch([c('A')]),[])
        self.assertTrue(self.ledger.prepare_batch([c('C')]))

    def test_validation_halt_and_save_failure(self):
        for candidates, cash in [([c(),c()],None),([c(),c('B',match='OTHER')],None),
                                 ([c()],{}),([c()],{3:D('NaN')})]:
            with self.assertRaises(ValueError): self.ledger.prepare_batch(candidates,cash)
        self.ledger.state['halt_reason']='halt'
        self.assertEqual(self.ledger.prepare_batch([c()]),[])
        self.ledger.state.pop('halt_reason')
        with patch.object(self.ledger,'save',side_effect=OSError('disk')):
            with self.assertRaises(OSError): self.ledger.prepare_batch([c(),c('B')])
        self.assertEqual(len(self.ledger.unresolved()),2)

    def test_random_all_fill_costs_obey_existing_match_total_and_shard_caps(self):
        rng=random.Random(10)
        for _ in range(100):
            self.ledger.state['orders'].clear()
            self.ledger.total=D(rng.randrange(5,400))
            self.ledger.per_match=D(rng.randrange(2,200))
            prior=self.ledger.prepare(c('PRIOR',match='OTHER'),cash_limit=D(2))
            if prior: self.ledger.accept(prior,{'order_id':'p','fill_count':'1'},True)
            cash={0:D(rng.randrange(0,100)),3:D(rng.randrange(0,100))}
            candidates=[c(str(i),side=rng.choice(['bid','ask']),shard=rng.choice([0,3]))
                        for i in range(rng.randrange(1,9))]
            rows=self.ledger.prepare_batch(candidates,cash)
            self.assertLessEqual(self.ledger.used(),self.ledger.total)
            self.assertLessEqual(self.ledger.used('MATCH'),self.ledger.per_match)
            for shard,limit in cash.items():
                self.assertLessEqual(sum(D(r['reserved']) for r in rows if r['exchange_index']==shard),limit)
            for r in rows:
                self.assertEqual(D(r['reserved']),reserve_cost(r['side'],r['price'],r['count']))


class ConcurrentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.p=object.__new__(Pilot)
        self.p.live=True; self.p.ledger=Ledger(Path(self.tmp.name)/'ledger.json',1000,200)
        self.p.order_pool=ThreadPoolExecutor(max_workers=8)
        self.addCleanup(self.p.order_pool.shutdown)
        self.p.retry={};self.p.jlog=Mock();self.p.cash_checked={}
        self.p.cash_error=None;self.p.last_error=None;self.p.next_batch_at=0
        self.p.busy=True;self.p.account_ready=True;self.p.feed_ready=True
        self.p.last_message_at=time.time();self.p.disabled_path=Path(self.tmp.name)/'STOP'
        Path(self.tmp.name,'winner_taker_discovery.json').touch()
        self.shared=patch('pilot.SHARED',Path(self.tmp.name));self.shared.start();self.addCleanup(self.shared.stop)
        self.legs=[SimpleNamespace(win_tk=t,match_tk='MATCH-'+t) for t in ['A','B','C']]
        self.p.legs={l.win_tk:l for l in self.legs}
        self.get=patch('pilot.get',side_effect=lambda path:{'market':{
            'ticker':path.rsplit('/',1)[-1],'status':'active','exchange_index':3}})
        self.get.start();self.addCleanup(self.get.stop)
        self.candidate=patch('pilot.candidate',side_effect=lambda owner,leg:c(leg.win_tk))
        self.candidate.start();self.addCleanup(self.candidate.stop)

    def balance(self):
        return 200,{'updated_ts':time.time(),'balance_breakdown':[{'exchange_index':3,'balance':'1000'}]}

    async def test_all_posts_enter_before_any_response_and_all_intents_are_durable(self):
        barrier=threading.Barrier(3,timeout=3)
        seen=[]
        def exchange(method,path,body=None):
            if method=='GET': return self.balance()
            state=json.loads(Path(self.p.ledger.path).read_text())
            self.assertEqual(len(state['orders']),3)
            self.assertTrue(all(r['status']=='unresolved' for r in state['orders'].values()))
            seen.append(body['ticker']);barrier.wait()
            if body['ticker']=='A': return -1,{'error':'timeout'}
            return 201,{'order_id':body['ticker'],'fill_count':body['count']}
        with patch('pilot.request',side_effect=exchange): await self.p.prepare_batch(self.legs)
        self.assertEqual(set(seen),{'A','B','C'})
        self.assertEqual(len(self.p.ledger.unresolved()),1)
        self.assertFalse(self.p.entry_ready())
        self.assertLessEqual(self.p.ledger.used(),200)

    async def test_fast_completion_does_not_clear_busy_with_sibling_pending(self):
        entered=threading.Event();release=threading.Event()
        def exchange(method,path,body=None):
            if method=='GET':return self.balance()
            if body['ticker']=='B':
                entered.set();release.wait(3)
            return 201,{'order_id':body['ticker'],'fill_count':'0'}
        with patch('pilot.request',side_effect=exchange):
            task=asyncio.create_task(self.p.prepare_batch(self.legs[:2]))
            try:
                self.assertTrue(await asyncio.to_thread(entered.wait,2))
                for _ in range(100):
                    if any(r['status']=='no_fill' for r in self.p.ledger.state['orders'].values()): break
                    await asyncio.sleep(.01)
                self.assertTrue(self.p.busy)
                self.p.on_book('A')
                self.assertEqual(len(self.p.ledger.state['orders']),2)
            finally:
                release.set();await task
        self.assertFalse(self.p.busy)

    async def test_cash_read_rechecks_signal_and_save_failure_sends_no_posts(self):
        calls=[]
        def exchange(method,*args):calls.append(method);return self.balance()
        with patch('pilot.request',side_effect=exchange),patch('pilot.candidate',return_value=None):
            await self.p.prepare_batch(self.legs)
        self.assertEqual(calls,['GET']);self.assertFalse(self.p.ledger.state['orders'])
        calls.clear();self.p.busy=True
        with patch('pilot.request',side_effect=exchange),patch.object(self.p.ledger,'save',side_effect=OSError('disk')):
            await self.p.prepare_batch(self.legs)
        self.assertEqual(calls,['GET']);self.assertFalse(self.p.account_ready)

    async def test_related_update_collects_both_players_and_all_markets(self):
        self.p.busy=False
        pending=[]
        async def prepare(legs):pending.extend(legs);self.p.busy=False
        with patch.object(self.p,'prepare_batch',side_effect=prepare):
            self.p.on_book('A')
            await asyncio.sleep(0)
        self.assertEqual({l.win_tk for l in pending},{'A','B','C'})

    async def test_sequential_subclass_keeps_its_own_preparation_checks(self):
        from staged_recovery.runner import RecoveryPilot
        self.assertFalse(RecoveryPilot.batch_entries)
        self.p.batch_entries=False;self.p.busy=False
        pending=[]
        async def prepare(leg):pending.append(leg)
        with patch.object(self.p,'prepare_live',side_effect=prepare), \
             patch.object(self.p,'prepare_batch') as batch:
            self.p.on_book('A');await asyncio.sleep(0)
        batch.assert_not_called()
        self.assertEqual(pending,[self.legs[0]])

    async def test_missing_shard_skips_only_affected_markets_and_stop_vetoes(self):
        def get(path):
            ticker=path.rsplit('/',1)[-1]
            return {'market':{'ticker':ticker,'status':'active',
                              'exchange_index':0 if ticker=='B' else 3}}
        calls=[]
        def exchange(method,path,body=None):
            if method=='GET': return self.balance()
            calls.append(body['ticker'])
            return 201,{'order_id':body['ticker'],'fill_count':'0'}
        with patch('pilot.get',side_effect=get),patch('pilot.request',side_effect=exchange):
            await self.p.prepare_batch(self.legs)
        self.assertEqual(set(calls),{'A','C'})
        self.assertIn('exchange balance',self.p.cash_error)
        calls.clear();self.p.disabled_path.touch()
        with patch('pilot.request',side_effect=exchange):await self.p.prepare_batch(self.legs)
        self.assertEqual(calls,[])


if __name__=='__main__':unittest.main()
