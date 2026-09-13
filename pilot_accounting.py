"""Order-attributed settlement accounting; release only verified filled orders."""
import time
from decimal import ROUND_CEILING
from pilot_execution import D, API, read_pages, timestamp


def money(value):
    value = D(value)
    if not value.is_finite() or value < 0:
        raise ValueError('invalid accounting amount')
    return value


def settlement_record(row, fills, settlement, now=None):
    if settlement.get('ticker') != row['ticker'] or settlement.get('market_result') not in ('yes', 'no'):
        raise ValueError('invalid settlement identity/outcome')
    settled_at = timestamp(settlement['settled_time'])
    if not row['created_at'] <= settled_at <= (now or time.time())+5:
        raise ValueError('invalid settlement time')
    matched = {}
    for f in fills:
        if f.get('order_id') != row['order_id']:
            continue
        if f.get('ticker') != row['ticker']:
            raise ValueError('fill market mismatch')
        fid = f['fill_id']
        if fid in matched and matched[fid] != f:
            raise ValueError('conflicting duplicate fill')
        matched[fid] = f
    quantity = sum((money(f['count_fp']) for f in matched.values()), D(0))
    if quantity != money(row['filled']) or quantity <= 0:
        raise ValueError('incomplete order fills')
    price_key = 'yes_price_dollars' if row['side'] == 'bid' else 'no_price_dollars'
    principal = D(0)
    for f in matched.values():
        price = money(f[price_key])
        if price > 1:
            raise ValueError('invalid fill price')
        principal += money(f['count_fp'])*price
    fees = sum((money(f['fee_cost']) for f in matched.values()), D(0))
    outcome = 'yes' if row['side'] == 'bid' else 'no'
    payout = quantity if settlement['market_result'] == outcome else D(0)
    return dict(source='exchange', principal=str(principal), fees=str(fees),
                payout=str(payout), net_profit=str(payout-principal-fees),
                settled_at=settled_at, result=settlement['market_result'],
                fill_ids=sorted(matched), verified_at=now or time.time())


def refresh(ledger, call, public_get, live):
    targets = [o for o in ledger.state['orders'].values()
               if o['status'] == 'filled' and not o.get('settlement')]
    if not targets:
        ledger.state.pop('accounting_error', None)
        return
    try:
        if live:
            start = int(min(o['created_at'] for o in targets)-60)
            fills = read_pages(call, '/portfolio/fills', 'fills', min_ts=start)
            status, cutoff = call('GET', API+'/historical/cutoff')
            if status != 200 or not isinstance(cutoff, dict):
                raise ValueError('historical cutoff unavailable')
            if start <= timestamp(cutoff['trades_created_ts']):
                fills += read_pages(call, '/historical/fills', 'fills', min_ts=start)
            settlements = read_pages(call, '/portfolio/settlements', 'settlements', min_ts=start)
        updates = []
        for row in targets:
            if live:
                found = [s for s in settlements if s.get('ticker') == row['ticker']]
                if not found:
                    continue
                if len(found) != 1:
                    raise ValueError('ambiguous settlement records')
                record = settlement_record(row, fills, found[0])
            else:
                data = public_get('/markets/'+row['ticker'])
                market = (data or {}).get('market', {})
                if market.get('ticker') != row['ticker']:
                    raise ValueError('paper settlement market unavailable')
                if market.get('status') != 'finalized' or market.get('result') not in ('yes','no'):
                    continue
                n = money(row['filled'])
                levels = row.get('paper_execution', {}).get('levels')
                if not levels:
                    levels = [{'quantity': str(n), 'price': row['price']}]
                if sum((money(x['quantity']) for x in levels), D(0)) != n:
                    raise ValueError('paper fill quantity mismatch')
                principal, fees = D(0), D(0)
                for x in levels:
                    q, p = money(x['quantity']), money(x['price'])
                    if p > 1:
                        raise ValueError('invalid paper price')
                    principal += q*(p if row['side']=='bid' else 1-p)
                    fees += D('.07')*q*p*(1-p)
                fees = fees.quantize(D('.01'), rounding=ROUND_CEILING)
                payout = n if market['result']==('yes' if row['side']=='bid' else 'no') else D(0)
                record = dict(source='paper_estimate', principal=str(principal), fees=str(fees),
                              payout=str(payout), net_profit=str(payout-principal-fees),
                              settled_at=time.time(), result=market['result'], verified_at=time.time())
            updates.append((row, record))
        for row, record in updates:
            row['settlement'] = record
        ledger.state.pop('accounting_error', None)
        ledger.state['accounting_checked_at'] = time.time()
        try:
            ledger.save()
        except Exception:
            for row, _ in updates:
                row.pop('settlement', None)
            raise RuntimeError('settlement persistence failed')
    except RuntimeError:
        raise
    except Exception as exc:
        ledger.state['accounting_error'] = type(exc).__name__+': '+str(exc)[:180]
        ledger.save()


def summary(ledger):
    orders = list(ledger.state['orders'].values())
    records = [o['settlement'] for o in orders if o.get('settlement')]
    remaining = max(D(0), ledger.total-ledger.used())
    return dict(budget_policy='settlement_recycling' if getattr(ledger,'recycle',False) else 'cumulative',
                remaining_budget=str(remaining), budget_exhausted=remaining < D('.86'),
                budget_status='insufficient_for_one_contract' if remaining < D('.86') else 'available',
                settled_orders=len(records), unsettled_filled_orders=sum(o['status']=='filled' and not o.get('settlement') for o in orders),
                settled_principal=str(sum((D(r['principal']) for r in records),D(0))),
                settled_fees=str(sum((D(r['fees']) for r in records),D(0))),
                realized_profit=str(sum((D(r['net_profit']) for r in records),D(0))),
                accounting_error=ledger.state.get('accounting_error'),
                accounting_checked_at=ledger.state.get('accounting_checked_at'),
                lifetime_reserved=str(sum((D(o['reserved']) for o in orders if o['status']=='filled'),D(0))))
