"""Staged integrated live entry/recovery runner. No service points here.

Run only after approval: python3 -m staged_recovery.runner --config PATH
The config must explicitly enable recovery; the shipped config is disabled.
"""
import argparse,asyncio,fcntl,json,os,time
from pathlib import Path
from pilot import Pilot,SHARED,OUT
from pilot_strategy import candidate
from winner_taker import WinnerTaker
from discovery_feed import FollowerDiscovery
from pilot_execution import request,exchange_cash,read_pages,D,timestamp
from kalshi import API,Book,get
from recovery_policy import choose_plan,adverse_quote,supported_fee_schedule
from score_context import context
from paper_support import atomic_json
from staged_recovery.ledger import RecoveryLedger


class Detector:
    def __init__(self,threshold,persistence):
        self.threshold=threshold;self.persistence=persistence;self.book=None;self.since=None
    def update(self,book,side,now,ready):
        if not ready or book is None:
            self.book=None;self.since=None;return False
        if self.book is not book:self.book=book;self.since=None
        bid=adverse_quote(book,side)
        if bid is None or bid<self.threshold:self.since=None;return False
        if self.since is None:self.since=now
        return now-self.since>=self.persistence


def order_body(row):
    body={'ticker':row['ticker'],'client_order_id':row['client_order_id'],
          'side':row['side'],'count':f"{D(row['count']):.2f}",'price':f"{D(row['price']):.4f}",
          'time_in_force':'immediate_or_cancel','self_trade_prevention_type':'taker_at_cross',
          'post_only':False,'reduce_only':row.get('purpose')=='exit'}
    if 'exchange_index' in row:body['exchange_index']=row['exchange_index']
    return body


def validate_config(config):
    if config.get('enabled') is not True:raise ValueError('recovery is not enabled; staged only')
    if config.get('threshold') not in (.02,.03,.05,.07,.10) or config.get('persistence') not in (5,10,30,60):
        raise ValueError('unreviewed recovery parameters')
    if type(config.get('allow_hedges')) is not bool:raise ValueError('explicit hedge policy required')
    return config


class RecoveryPilot(Pilot):
    # Keep staged recovery's position checks and entry/recovery arbitration.
    # Concurrent recovery-aware entry allocation has not been enabled.
    batch_entries = False

    def __init__(self,config):
        self.recovery_config=validate_config(config)
        WinnerTaker.__init__(self,False)
        self.live=True
        self.disc=FollowerDiscovery(str(SHARED/'winner_taker_discovery.json'))
        self.ledger=RecoveryLedger(Path(OUT)/'pilot_ledger.json',1000,200)
        self.started_at=time.time();self.busy=False;self.retry={};self.last_reconcile=0
        self.account_ready=False;self.last_error=None;self.cash_error=None;self.cash_checked={}
        self.disabled_path=Path(OUT)/'STOP';self.detectors={};self.recovery_retry={}
        self.last_settle=0;self.recovery_ready=False

    def positions(self):
        return self.ledger.state['recovery']['positions']

    def adopt(self):
        changed=False
        for cid,row in self.ledger.state['orders'].items():
            if row.get('purpose') or row['status']!='filled' or cid in self.positions():continue
            self.positions()[cid]={'status':'watching'};changed=True
        if changed:
            self.ledger.save()
            if self.dirty:self.dirty.set()

    def tickers(self):
        tks=set(super().tickers())
        for cid,p in self.positions().items():
            if p['status'] in ('settled','score_confirmed'):continue
            entry=self.ledger.state['orders'][cid]
            tks.update((entry['ticker'],entry['match_ticker']))
        return sorted(tks)

    def on_book(self,tk):
        if not self.recovery_ready or self.busy:return
        self.adopt()
        self.evaluate_recovery()
        if not self.busy:super().on_book(tk)

    def evaluate_recovery(self):
        if (self.busy or not self.account_ready or self.disabled_path.exists()
                or self.ledger.unresolved() or self.ledger.state.get('halt_reason')):return
        ready=self.feed_ready and time.time()-self.last_message_at<30
        for cid,p in self.positions().items():
            if p['status'] in ('settled','score_confirmed','needs_review'):continue
            entry=self.ledger.state['orders'][cid]
            if self.ledger.remaining(cid)<=0:continue
            attempts=[o for o in self.ledger.state['orders'].values() if o.get('entry_id')==cid]
            if len(attempts)>=3 or any(o['status']=='not_found' for o in attempts):
                p['status']='needs_review';self.ledger.save();continue
            score=context(self.scores,entry['match_ticker'])
            favorable='won' if entry['side']=='bid' else 'lost'
            if score['state']==favorable:
                p['status']='score_confirmed';self.ledger.save();continue
            detector=self.detectors.setdefault(cid,Detector(self.recovery_config['threshold'],self.recovery_config['persistence']))
            book=self.books.get(entry['match_ticker'])
            revival=detector.update(book,entry['side'],time.monotonic(),ready)
            adverse=score['state'] in ('won','lost') and score['state']!=favorable
            if not ready or not (revival or adverse):continue
            self.ledger.block(entry['match'])
            p['status']='triggered';self.ledger.save()
            if time.time()<self.recovery_retry.get(cid,0):continue
            self.busy=True;self.recovery_inflight=True
            asyncio.create_task(self.recover(cid,book,adverse));return

    async def prepare_live(self,leg):
        try:
            md=await asyncio.to_thread(get,'/markets/'+leg.win_tk)
            market=(md or {}).get('market',{})
            status,balance=await asyncio.to_thread(request,'GET',API+'/portfolio/balance')
            if status!=200:raise ValueError('entry cash read failed')
            index,cash=exchange_cash(market,balance)
            positions=await asyncio.to_thread(read_pages,request,'/portfolio/positions','market_positions',ticker=leg.win_tk)
            if any(p.get('ticker')!=leg.win_tk or D(p['position_fp'])!=0 for p in positions):
                return  # Do not mix ownership with a pre-existing/manual position.
            self.cash_error=None;self.cash_checked[str(index)]={'cash':str(cash),'at':time.time()}
            if (market.get('ticker')!=leg.win_tk or market.get('status')!='active' or market.get('result')
                    or self.disabled_path.exists() or time.time()-self.last_message_at>=30
                    or time.time()-(SHARED/'winner_taker_discovery.json').stat().st_mtime>180):return
            # Give recovery priority over a new entry after the asynchronous reads.
            self.busy=False
            self.evaluate_recovery()
            if self.busy:return
            self.busy=True
            c=candidate(self,leg)
            if c:
                c.update(exchange_index=index,cash_at_prepare=str(cash))
                row=self.ledger.prepare(c,cash_limit=cash)
                if row:await self.execute(row)
        except Exception as exc:
            self.cash_error=type(exc).__name__+': '+str(exc)[:200]
        finally:
            self.retry[leg.win_tk]=time.time()+10
            # evaluate_recovery may have handed the single-flight slot to a task.
            if not getattr(self,'recovery_inflight',False):self.busy=False

    def route_position(self,cid):
        e=self.ledger.state['orders'][cid]
        return dict(e,price=float(e['price']),quantity=float(self.ledger.remaining(cid)),protected=0)

    async def recover(self,cid,match_book,adverse_final):
        try:
            entry=self.ledger.state['orders'][cid];p=self.positions()[cid]
            # Read-only fresh settlement, depth, shard cash and actual exposure.
            tickers=[entry['ticker']]
            if self.recovery_config['allow_hedges']:tickers.append(entry['match_ticker'])
            markets={};books={}
            for tk in tickers:
                md,bd=await asyncio.gather(asyncio.to_thread(get,'/markets/'+tk),
                    asyncio.to_thread(get,'/markets/'+tk+'/orderbook'))
                m=(md or {}).get('market',{});raw=(bd or {}).get('orderbook_fp')
                if tk==entry['ticker'] and m.get('ticker')==tk:
                    if m.get('result') in ('yes','no'):
                        p['status']='settled';p['result']=m['result'];self.ledger.save();return
                    if m.get('status')!='active':return
                if m.get('ticker')!=tk or m.get('status')!='active' or m.get('result') or not isinstance(raw,dict):continue
                ws=self.books.get(tk)
                if ws is None:continue
                rest=Book();rest.snapshot({s+'_dollars_fp':raw.get(s+'_dollars',[]) for s in ('yes','no')},time.time())
                b=Book()
                for side in ('yes','no'):
                    setattr(b,side,{price:min(q,getattr(rest,side).get(price,0))
                        for price,q in getattr(ws,side).items() if q>0 and getattr(rest,side).get(price,0)>0})
                markets[tk]=m;books[tk]=b
            status,balance=await asyncio.to_thread(request,'GET',API+'/portfolio/balance')
            if status!=200:raise ValueError('recovery cash unavailable')
            stamp_status,stamp=await asyncio.to_thread(request,'GET',API+'/exchange/user_data_timestamp')
            if stamp_status!=200 or not isinstance(stamp,dict):raise ValueError('account watermark unavailable')
            as_of=timestamp(stamp['as_of_time'])
            latest=max([entry.get('resolved_at',entry['created_at'])]+[
                o.get('resolved_at',o['created_at']) for o in self.ledger.state['orders'].values()
                if o.get('entry_id')==cid and o['status']=='filled'])
            if not time.time()-60<=as_of<=time.time()+5 or as_of<latest:
                p['status']='awaiting_account_watermark';self.ledger.save();return
            pos=await asyncio.to_thread(read_pages,request,'/portfolio/positions','market_positions',ticker=entry['ticker'])
            if any(r.get('ticker')!=entry['ticker'] for r in pos):raise ValueError('position filter mismatch')
            if len(pos)>1:raise ValueError('multiple account position rows')
            actual=D(pos[0]['position_fp']) if pos else D(0)
            if not actual.is_finite():raise ValueError('invalid position quantity')
            held=actual if entry['side']=='bid' else -actual
            closed=sum((D(o.get('filled',0)) for o in self.ledger.state['orders'].values()
                        if o.get('entry_id')==cid and o.get('purpose')=='exit' and o['status']=='filled'),D(0))
            if held!=D(entry['filled'])-closed:
                # Manual changes or unsettled account data: do not guess ownership.
                raise ValueError('source position differs from owned recovery exposure')
            # Recheck signal AFTER reads. Never act on a recovered quote or a
            # replaced book generation merely because an earlier timer fired.
            score=context(self.scores,entry['match_ticker'])
            favorable='won' if entry['side']=='bid' else 'lost'
            if score['state']==favorable:
                p['status']='score_confirmed';self.ledger.save();return
            ready=(self.feed_ready and time.time()-self.last_message_at<30
                   and self.books.get(entry['match_ticker']) is match_book)
            detector=self.detectors[cid]
            adverse=score['state'] in ('won','lost') and score['state']!=favorable
            if not ready or not (detector.update(match_book,entry['side'],time.monotonic(),ready) or adverse):return
            if self.disabled_path.exists() or self.ledger.unresolved() or self.ledger.state.get('halt_reason'):return
            room=float(min(self.ledger.total-self.ledger.used(),self.ledger.per_match-self.ledger.used(entry['match'])))
            options=[]
            for tk,b in books.items():
                index,cash=exchange_cash(markets[tk],balance)
                plan=choose_plan(self.route_position(cid),{tk:b},min(room,float(cash)))
                if plan:options.append((plan,index,cash))
            if not options:
                p['status']='no_bounded_route';self.ledger.save();return
            plan,index,cash=max(options,key=lambda v:(v[0]['quantity'],v[0]['guaranteed_proceeds']))
            row=self.ledger.prepare_recovery(cid,plan,cash,position_limit=held)
            if row is None:
                p['status']='budget_or_attempt_limit';self.ledger.save();return
            row['exchange_index']=index;row['cash_at_prepare']=str(cash)
            row['decision']={'threshold':self.recovery_config['threshold'],'persistence':self.recovery_config['persistence'],
                'score':score,'adverse_bid':adverse_quote(match_book,entry['side']),'plan':plan}
            self.ledger.save()
            await self.execute(row)
            p['status']='protected' if self.ledger.remaining(cid)==0 else 'partial'
            self.ledger.save()
        except Exception as exc:
            self.last_error='recovery: '+type(exc).__name__+': '+str(exc)[:200]
            self.account_ready=False
            self.jlog({'a':'recovery_error','error':self.last_error})
        finally:
            self.recovery_retry[cid]=time.time()+5;self.recovery_inflight=False;self.busy=False

    async def execute(self,row):
        if not row.get('purpose'):return await super().execute(row)
        try:
            status,response=await asyncio.get_running_loop().run_in_executor(self.order_pool,request,
                'POST',API+'/portfolio/events/orders',order_body(row))
            row['http_status']=status;row['raw_response']=response;self.ledger.save()
            resolved=status in (200,201) and self.ledger.accept(row,response,terminal=True)
            self.jlog({'a':'recovery_order','client_order_id':row['client_order_id'],
                'ticker':row['ticker'],'purpose':row['purpose'],'resolved':bool(resolved),'http_status':status})
        except Exception as exc:
            self.last_error='recovery submit: '+type(exc).__name__;self.account_ready=False
            raise

    async def settlement_refresh(self):
        for cid,p in self.positions().items():
            if p['status']=='settled':continue
            entry=self.ledger.state['orders'][cid]
            m=(await asyncio.to_thread(get,'/markets/'+entry['ticker']) or {}).get('market',{})
            if m.get('result') in ('yes','no'):
                p['status']='settled';p['result']=m['result'];self.ledger.save()

    async def recovery_maintenance(self):
        while True:
            if self.account_ready and not self.busy:
                self.adopt()
                if time.time()-self.last_settle>=60:
                    self.busy=True
                    try:await self.settlement_refresh()
                    finally:self.busy=False
                    self.last_settle=time.time()
                self.evaluate_recovery()
            health={'updated_at':time.time(),'enabled':True,'policy':self.recovery_config,
                'positions':self.positions(),'unresolved':len(self.ledger.unresolved()),'error':self.last_error}
            atomic_json(str(Path(OUT)/'live_recovery_health.json'),health)
            await asyncio.sleep(.25)

    async def run(self):
        # Match hedges require the same known taker fee category preflight.
        for series in ('KXATPMATCH','KXWTAMATCH'):
            s=(await asyncio.to_thread(get,'/series/'+series) or {}).get('series',{})
            if not supported_fee_schedule(s):raise ValueError('unknown match fee schedule')
        self.adopt();await self.settlement_refresh()
        self.recovery_ready=True
        await asyncio.gather(super().run(),self.recovery_maintenance())


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',required=True);a=parser.parse_args()
    config=validate_config(json.loads(Path(a.config).read_text()))
    if Path(OUT).resolve() != (SHARED.parent/'pilot_live').resolve():
        raise ValueError('KALSHI_DATA must point to the existing pilot_live directory')
    # Share the existing lock and ledger: two entry/recovery processes cannot run.
    with (Path(OUT)/'pilot.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        asyncio.run(RecoveryPilot(config).run())

if __name__=='__main__':main()
