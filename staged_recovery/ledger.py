"""Single durable budget and intent journal for entries and recovery orders."""
import json,time,uuid,math
from pathlib import Path
from decimal import ROUND_CEILING
from pilot_execution import Ledger,D,PREFIX,reserve_cost,read_pages,timestamp
from kalshi import API


def recovery_cost(row):
    if row.get('purpose') not in ('exit','hedge'):
        return reserve_cost(row['side'],row['price'],row['count'])
    p,n=D(row['price']),D(row['count'])
    # Fees are bounded at the limit: exit prices >= .83 (YES) or <= .17
    # (closing NO); hedges buy the adverse outcome for <= .20.
    fee=(D('.07')*p*(1-p)*n).quantize(D('.01'),rounding=ROUND_CEILING)
    return fee if row['purpose']=='exit' else reserve_cost(row['side'],p,n)


class RecoveryLedger(Ledger):
    def __init__(self,path,total=1000,per_match=200):
        self.path=str(path);self.total=D(total);self.per_match=D(per_match)
        self.state=json.loads(Path(path).read_text()) if Path(path).exists() else {'version':1,'orders':{}}
        if self.state.get('version')!=1 or not isinstance(self.state.get('orders'),dict):
            raise ValueError('invalid ledger')
        for cid,row in self.state['orders'].items():
            if (cid!=row.get('client_order_id') or row.get('status') not in ('unresolved','filled','no_fill','not_found')
                    or row.get('side') not in ('bid','ask') or not row.get('match')
                    or not D(row['price']).is_finite() or not 0<D(row['price'])<1
                    or not D(row['count']).is_finite() or not 0<D(row['count'])<=500
                    or D(row['reserved'])!=recovery_cost(row)):
                raise ValueError('invalid recovery reservation')
            if row.get('purpose'):
                parent=self.state['orders'].get(row.get('entry_id'),{})
                if (parent.get('purpose') or parent.get('status')!='filled' or parent.get('match')!=row['match']
                        or row['purpose'] not in ('exit','hedge')):
                    raise ValueError('invalid recovery parent')
                if row['purpose']=='exit' and (row['ticker']!=parent['ticker'] or row['side']==parent['side']):
                    raise ValueError('exit must close the original position')
                held_cost=D(parent['price']) if parent['side']=='bid' else 1-D(parent['price'])
                if row['purpose']=='exit':
                    proceeds=D(row['price']) if row['side']=='ask' else 1-D(row['price'])
                    if proceeds<held_cost-D('.02'):raise ValueError('invalid durable exit bound')
                elif (row['ticker']!=parent['match_ticker'] or row['side']==parent['side']
                        or (D(row['price']) if row['side']=='bid' else 1-D(row['price']))>D('.20')):
                    raise ValueError('invalid durable hedge mapping/bound')
            if row['status']=='filled':
                filled=D(row.get('filled', 'NaN'))
                if not filled.is_finite() or not 0<filled<=D(row['count']):raise ValueError('invalid filled quantity')
        for cid,entry in self.state['orders'].items():
            if entry.get('purpose') or entry['status']!='filled':continue
            protected=sum((D(o.get('filled',0)) for o in self.state['orders'].values()
                           if o.get('entry_id')==cid and o['status']=='filled'),D(0))
            if protected>D(entry['filled']):raise ValueError('recovery exceeds source quantity')
        self.state.setdefault('recovery',{'positions':{},'blocked_matches':[]})
        if self.used()>self.total or any(self.used(o['match'])>self.per_match for o in self.state['orders'].values()):
            raise ValueError('ledger exceeds configured budget')
        self.save()

    def prepare(self,c,cash_limit=None):
        if c['match'] in self.state['recovery']['blocked_matches']:return None
        return super().prepare(c,cash_limit)

    def remaining(self,entry_id):
        entry=self.state['orders'][entry_id]
        protected=sum((D(o.get('filled',0)) for o in self.state['orders'].values()
                       if o.get('entry_id')==entry_id and o['status']=='filled'),D(0))
        return max(D(0),D(entry['filled'])-protected)

    def block(self,match):
        if match not in self.state['recovery']['blocked_matches']:
            self.state['recovery']['blocked_matches'].append(match);self.save()

    def prepare_recovery(self,entry_id,plan,cash_limit,position_limit=None):
        if self.unresolved() or self.state.get('halt_reason'):return None
        entry=self.state['orders'][entry_id]
        if entry.get('purpose') or entry['status']!='filled':raise ValueError('invalid entry')
        self.block(entry['match'])
        attempts=[o for o in self.state['orders'].values() if o.get('entry_id')==entry_id]
        if len(attempts)>=3 or any(o['status']=='not_found' for o in attempts):return None
        if attempts and time.time()-max(o['created_at'] for o in attempts)<5:return None
        kind=plan['kind']; ladder=plan['ladder'];limit=D(plan['limit'])
        if kind=='exit':
            if plan['ticker']!=entry['ticker'] or ladder!=('yes' if entry['side']=='bid' else 'no'):
                raise ValueError('exit identity mismatch')
            side='ask' if ladder=='yes' else 'bid';price=limit if ladder=='yes' else 1-limit
            floor=(D(entry['price']) if entry['side']=='bid' else 1-D(entry['price']))-D('.02')
            if limit<floor or position_limit is None:raise ValueError('unbounded/unverified exit')
        elif kind=='hedge':
            # Release candidate permits only the exact original match market.
            # No guessed opponent ticker or cross-market identity substitution.
            if plan['ticker']!=entry['match_ticker'] or ladder!=('yes' if entry['side']=='bid' else 'no') or not 0<limit<=D('.20'):
                raise ValueError('unbounded/mismapped hedge')
            side='ask' if ladder=='yes' else 'bid';price=1-limit if ladder=='yes' else limit
        else:raise ValueError('unknown recovery kind')
        count=min(self.remaining(entry_id),D(plan['quantity']),D(500))
        if kind=='exit':count=min(count,D(position_limit))
        count=count.quantize(D('.01'),rounding='ROUND_DOWN')
        room=min(self.total-self.used(),self.per_match-self.used(entry['match']),D(cash_limit))
        if not room.is_finite() or room<0:raise ValueError('invalid recovery budget')
        row=dict(ticker=plan['ticker'],match=entry['match'],match_ticker=entry['match_ticker'],
                 side=side,price=str(price),purpose=kind,entry_id=entry_id)
        # Fractional IOC counts, rounded down, and worst-case fee reservation.
        lo,hi=0,int(count*100)
        while lo<hi:
            mid=(lo+hi+1)//2
            if recovery_cost(dict(row,count=str(D(mid)/100)))<=room:lo=mid
            else:hi=mid-1
        if lo<=0:return None
        row.update(count=str(D(lo)/100),status='unresolved',created_at=time.time(),
                   client_order_id=PREFIX+str(uuid.uuid4()))
        row['reserved']=str(recovery_cost(row))
        self.state['orders'][row['client_order_id']]=row
        self.save()
        return row

    def accept(self,row,response,terminal=False):
        # Base accept compares to an integer count; recovery supports hundredths.
        if not row.get('purpose'):return super().accept(row,response,terminal)
        o=response.get('order',response) if isinstance(response,dict) else {}
        if not isinstance(o,dict) or not o.get('order_id'):return False
        if o.get('ticker') not in (None,row['ticker']) or o.get('client_order_id') not in (None,row['client_order_id']):return False
        if o.get('status')=='resting' or (not terminal and o.get('status') not in ('executed','canceled')):return False
        try:
            filled=D(o.get('fill_count',o.get('fill_count_fp')))
            if not filled.is_finite() or not 0<=filled<=D(row['count']):return False
        except (ValueError,ArithmeticError):return False
        row.update(order_id=o['order_id'],filled=str(filled),response=o,
                   status='filled' if filled else 'no_fill',resolved_at=time.time())
        self.save();return True

    def reconcile(self,call=None,now=None):
        from pilot_execution import request
        call=request if call is None else call
        # Recovery absence is ambiguous: existing source fills/positions make
        # the entry-only clean-absence inference inapplicable. Never resubmit.
        # Base reconcile handles all rows conservatively and retains allocation
        # if the source market has any exposure; matching CID resolves normally.
        return super().reconcile(call,now)
