"""Read-only forward recovery simulation following live/shadow pilot fills.

No POST transport, no trade-key access, no writes to either source ledger.
A separate state file journals hypothetical recovery intents and verified fills.
"""
import asyncio
from collections import Counter
import fcntl
import json
from pathlib import Path
import time

from discovery_feed import FollowerDiscovery
from iolib import LIVE as OUT
from kalshi import Book, get
from paper_support import atomic_json
from recovery_policy import Revival, adverse_quote, choose_plan, fee
from score_context import context
from winner_taker import WinnerTaker

ROOT = Path(__file__).parent
SHARED = ROOT/'data/live'
SOURCES = {'pilot_live': (250,50), 'pilot_paper': (500,125)}


class Capacity:
    """Both ladders, separate counterfactual branches, conservative on snapshots."""
    def __init__(self, state=None):
        self.state = state or {}
        self.identities = {}

    def observe(self, source, ticker, book):
        branch = self.state.setdefault(source,{})
        old = branch.get(ticker,{})
        same = self.identities.get((source,ticker)) is book
        new = {}
        for side in ('yes','no'):
            previous = old.get(side,{})
            rows = {}
            current = getattr(book,side)
            for p,q in current.items():
                key = f'{p:.4f}'
                before = previous.get(key)
                available = q if before is None else min(q, before['available']+q-before['displayed']) if same else min(q,before['available'])
                rows[key] = {'displayed':q,'available':max(0,available)}
            rows.update({key:{'displayed':0,'available':0} for key in previous if key not in rows})
            new[side] = rows
        branch[ticker] = new
        self.identities[source,ticker] = book

    def book(self, source, ticker, actual, rest=None):
        self.observe(source,ticker,actual)
        b = Book()
        for side in ('yes','no'):
            levels = {}
            for key,row in self.state[source][ticker][side].items():
                p = float(key)
                q = row['available']
                if rest is not None:
                    q = min(q,getattr(rest,side).get(p,0))
                if q > 0:
                    levels[p] = q
            setattr(b,side,levels)
        return b

    def consume(self, source, plan):
        rows = self.state[source][plan['ticker']][plan['ladder']]
        for price,q in plan['levels']:
            ladder_price = price if plan['kind']=='exit' else round(1-price,4)
            row = rows[f'{ladder_price:.4f}']
            row['available'] = max(0,row['available']-q)


class RecoveryPaper(WinnerTaker):
    def __init__(self):
        super().__init__(False)
        self.disc = FollowerDiscovery(str(SHARED/'winner_taker_discovery.json'))
        self.path = Path(OUT)/'recovery_state.json'
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {
            'version':1,'since':time.time(),'positions':{},'blocked':[],'capacity':{}}
        if self.state.get('version') != 1:
            raise ValueError('unsupported recovery state')
        self.capacity = Capacity(self.state['capacity'])
        self.detectors = {}
        self.ledgers = {}
        self.busy = False
        self.error = None
        self.started = time.time()
        self.last_ingest = self.last_settle = 0
        for p in self.state['positions'].values():
            if p['status']=='pending':
                p['status']='interrupted'
        self.save()

    def save(self):
        self.state['capacity'] = self.capacity.state
        atomic_json(str(self.path), self.state)

    def ingest(self):
        changed = False
        for source in SOURCES:
            self.ledgers[source] = json.loads((ROOT/'data'/source/'pilot_ledger.json').read_text())
            for cid,row in self.ledgers[source]['orders'].items():
                key = source+':'+cid
                if row['status']!='filled' or row['created_at'] < self.state['since'] or key in self.state['positions']:
                    continue
                self.state['positions'][key] = {
                    'source':source,'ticker':row['ticker'],'match_ticker':row['match_ticker'],
                    'match':row['match'],'side':row['side'],'price':row['price'],
                    'quantity':float(row['filled']),'filled_at':row.get('resolved_at',row['created_at']),
                    'protected':0,'attempts':[],'status':'watching','retry_at':0,
                    'entry_score':row.get('score',{})}
                if (source+':'+row['match'] in self.state['blocked']
                        or self.budget(self.state['positions'][key],clamp=False)<-1e-9):
                    self.state['positions'][key]['status']='entry_would_be_blocked'
                self.jlog({'a':'recovery_adopt','position':key,'source':source})
                changed = True
        if changed:
            self.save()

    def budget(self, position, clamp=True):
        source, match = position['source'], position['match']
        total_cap, match_cap = SOURCES[source]
        rows = self.ledgers[source]['orders'].values()
        used = [o for o in rows if o['status'] not in ('no_fill','not_found')
                and self.state['positions'].get(source+':'+o['client_order_id'],{}).get('status')!='entry_would_be_blocked']
        total = sum(float(o['reserved']) for o in used)
        per_match = sum(float(o['reserved']) for o in used if o['match']==match)
        for p in self.state['positions'].values():
            if p['source'] != source:
                continue
            for attempt in p['attempts']:
                fill = attempt.get('fill')
                if fill:
                    extra = fill['fee']+(fill['gross'] if fill['kind']=='hedge' else 0)
                    total += extra
                    if p['match']==match:
                        per_match += extra
        room=min(total_cap-total,match_cap-per_match)
        return max(0,room) if clamp else room

    def on_book(self, ticker):
        actual = self.books.get(ticker)
        if actual:
            for source in SOURCES:
                self.capacity.observe(source,ticker,actual)
        self.evaluate_recovery()

    def evaluate_recovery(self):
        now = time.time()
        ready = self.feed_ready and now-self.last_message_at < 30
        for key,p in self.state['positions'].items():
            if p['status'] not in ('watching','partial','no_depth'):
                continue
            score = context(self.scores,p['match_ticker'])
            favorable = 'won' if p['side']=='bid' else 'lost'
            if score['state']==favorable:
                p['status']='score_confirmed'; self.save()
                self.jlog({'a':'recovery_favorable_score','position':key})
                continue
            detector = self.detectors.setdefault(key,Revival())
            book = self.books.get(p['match_ticker'])
            triggered = detector.update(book,p['side'],time.monotonic(),ready)
            adverse_final = score['state'] in ('won','lost') and score['state'] != favorable
            if not ready or not (triggered or adverse_final) or self.busy or now < p['retry_at']:
                continue
            if len(p['attempts']) >= 3:
                p['status']='needs_review'; self.save(); continue
            block = p['source']+':'+p['match']
            if block not in self.state['blocked']:
                self.state['blocked'].append(block)
            usable = {tk:self.capacity.book(p['source'],tk,b) for tk,b in self.books.items()}
            plan = choose_plan(p,usable,self.budget(p))
            if plan is None:
                if p['status']!='no_depth':
                    self.jlog({'a':'recovery_no_bounded_route','position':key,'budget':self.budget(p)})
                p['status']='no_depth'; p['retry_at']=now+5; self.save()
                continue
            intent = {'created_at':now,'plan':plan,'adverse_bid':adverse_quote(book,p['side']) if book else None,
                      'reason':'adverse_final_score' if adverse_final else '10c-for-5s','score':score}
            p['attempts'].append(intent); p['status']='pending'
            self.save()  # Durable before the simulated submission/latency.
            self.busy = True
            asyncio.create_task(self.execute_paper(key,intent,book))
            return

    async def execute_paper(self, key, intent, match_book):
        p = self.state['positions'][key]
        plan = intent['plan']
        try:
            await asyncio.sleep(.1)
            started = time.time()
            market, response = await asyncio.gather(
                asyncio.to_thread(get,'/markets/'+plan['ticker']),
                asyncio.to_thread(get,'/markets/'+plan['ticker']+'/orderbook'))
            m = (market or {}).get('market',{})
            raw = (response or {}).get('orderbook_fp')
            actual = self.books.get(plan['ticker'])
            if (m.get('status')!='active' or m.get('result') or not isinstance(raw,dict)
                    or not self.feed_ready or time.time()-self.last_message_at>=30 or actual is None
                    or self.books.get(p['match_ticker']) is not match_book):
                intent['verification']={'status':'no-fill-invalid-market-or-feed','at':time.time()}
            else:
                rest = Book()
                rest.snapshot({side+'_dollars_fp':raw.get(side+'_dollars',[]) for side in ('yes','no')},time.time())
                available = self.capacity.book(p['source'],plan['ticker'],actual,rest)
                # Execute only the original route, original quantity and limit.
                # Never replace a submitted route after seeing subsequent prices.
                levels = sorted(getattr(available,plan['ladder']).items(),reverse=True)
                used=[]; count=0; gross=0
                for price,size in levels:
                    paid = price if plan['kind']=='exit' else round(1-price,4)
                    if (plan['kind']=='exit' and paid < plan['limit']-1e-9) or (plan['kind']=='hedge' and paid > plan['limit']+1e-9):
                        break
                    q = min(size,plan['quantity']-count)
                    q = int((q+1e-9)*100)/100
                    if q>0:
                        used.append((paid,q)); count+=q; gross+=paid*q
                    if count >= plan['quantity']-1e-9:
                        break
                fill = dict(plan,quantity=round(count,2),levels=used,gross=gross,fee=fee(used))
                extra = fill['fee']+(gross if plan['kind']=='hedge' else 0)
                # Fresh source allocations may have changed during verification.
                self.ingest()
                available_budget=self.budget(p)
                if count>0 and extra <= available_budget+1e-9:
                    self.capacity.consume(p['source'],fill)
                    intent['fill']=fill
                    p['protected']=round(p['protected']+count,2)
                intent['verification']={'status':'verified','requested_at':started,'received_at':time.time(),
                                        'rest':raw,'market_status':m['status'],'budget_at_fill':available_budget,
                                        'ws':{side:sorted(getattr(actual,side).items(),reverse=True) for side in ('yes','no')},
                                        'budget_blocked': count>0 and extra>available_budget+1e-9}
            p['status']='protected' if p['protected'] >= p['quantity']-1e-9 else 'partial'
            p['retry_at']=time.time()+5
            self.save()
            self.jlog({'a':'recovery_paper_result','position':key,'intent':intent,'remaining':p['quantity']-p['protected']})
        except Exception as exc:
            p['status']='interrupted'
            self.error=type(exc).__name__+': '+str(exc)[:200]
            self.save()
            self.jlog({'a':'recovery_error','position':key,'error':self.error})
        finally:
            self.busy=False

    async def settlements(self):
        cache={}
        async def result(ticker):
            if ticker not in cache:
                d=await asyncio.to_thread(get,'/markets/'+ticker)
                cache[ticker]=(d or {}).get('market',{}).get('result')
            return cache[ticker]
        changed=False
        for key,p in self.state['positions'].items():
            if p.get('pnl') is not None or p['status']=='entry_would_be_blocked':
                continue
            market_result=await result(p['ticker'])
            if market_result not in ('yes','no'):
                continue
            held='yes' if p['side']=='bid' else 'no'
            unit_cost=p['price'] if held=='yes' else 1-p['price']
            basis=p['quantity']*unit_cost+fee([(unit_cost,p['quantity'])])
            closed=receipts=hedge_cost=hedge_payout=0
            ready=True
            for intent in p['attempts']:
                fill=intent.get('fill')
                if not fill: continue
                if fill['kind']=='exit':
                    closed+=fill['quantity']; receipts+=fill['gross']-fill['fee']
                else:
                    hedge_result=await result(fill['ticker'])
                    if hedge_result not in ('yes','no'):
                        ready=False; break
                    purchased='no' if fill['ladder']=='yes' else 'yes'
                    hedge_cost+=fill['gross']+fill['fee']
                    hedge_payout+=fill['quantity'] if hedge_result==purchased else 0
            if ready:
                p['baseline_pnl']=(p['quantity'] if market_result==held else 0)-basis
                p['pnl']=receipts+(p['quantity']-closed if market_result==held else 0)+hedge_payout-basis-hedge_cost
                p['status']='settled'; changed=True
                self.jlog({'a':'recovery_settlement','position':key,'pnl':p['pnl'],'baseline_pnl':p['baseline_pnl']})
        if changed:self.save()

    async def maintenance(self):
        while True:
            now=time.time()
            self.scores=json.loads((SHARED/'tennis_scores.json').read_text()).get('matches',{})
            if now-self.last_ingest>=1:
                self.ingest(); self.last_ingest=now
            self.evaluate_recovery()
            if not self.busy and now-self.last_settle>=60:
                self.last_settle=now
                self.busy=True
                try:
                    await self.settlements()
                finally:
                    self.busy=False
            counts=Counter(p['status'] for p in self.state['positions'].values())
            health={'updated_at':time.time(),'started_at':self.started,'mode':'paper-only','feed_ready':self.feed_ready,
                    'last_message_at':self.last_message_at,'legs':len(self.legs),'positions':dict(counts),'error':self.error,
                    'paper_pnl':sum(p.get('pnl',0) for p in self.state['positions'].values()),
                    'baseline_pnl':sum(p.get('baseline_pnl',0) for p in self.state['positions'].values()),
                    'unprotected_alerts':sum(counts[s] for s in ('partial','no_depth','needs_review','interrupted'))}
            atomic_json(str(Path(OUT)/'recovery_health.json'),health)
            await asyncio.sleep(.25)

    async def run(self):
        for series in ('KXATP','KXWTA','KXATPADVANCE','KXWTAADVANCE','KXATPMATCH','KXWTAMATCH'):
            data=await asyncio.to_thread(get,'/series/'+series)
            rule=(data or {}).get('series',{})
            if rule.get('fee_type')!='quadratic' or float(rule.get('fee_multiplier',0))!=1:
                raise RuntimeError('unverified recovery fee schedule: '+series)
        self.feed_enforced=True
        self.dirty=asyncio.Event()
        await asyncio.gather(self.discovery_loop(),self.ws_loop(),self.maintenance())


def main():
    Path(OUT).mkdir(parents=True,exist_ok=True)
    # A dedicated directory is mandatory; never share source accounting files.
    if Path(OUT).resolve() in {ROOT/'data/live',ROOT/'data/pilot_live',ROOT/'data/pilot_paper'}:
        raise RuntimeError('set KALSHI_DATA to a dedicated recovery directory')
    with (Path(OUT)/'recovery.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        asyncio.run(RecoveryPaper().run())


if __name__=='__main__':
    main()
