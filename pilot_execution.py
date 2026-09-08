"""Single-attempt live orders and durable, fail-closed pilot exposure accounting."""
from decimal import Decimal, ROUND_CEILING
import json
import os
from pathlib import Path
import uuid
import time
import threading

from kalshi import API, _load, sign, _connection, _drop
from paper_support import atomic_json

D = lambda x: Decimal(str(x))
PREFIX = 'tennis-pilot-'
_request_clock = threading.local()


def request(method, path, body=None):
    # No POST retry: a lost response does not establish a rejected order.
    key_id, priv = _load(os.path.expanduser('~/trade-key-id'), os.path.expanduser('~/trade-key'))
    headers = sign(priv, key_id, method, path)
    headers['Content-Type'] = 'application/json'
    try:
        # A quiet tennis interval can outlive the server's HTTP keepalive.
        # Discard an idle socket BEFORE sending; never retry a failed POST.
        if method == 'POST' and time.monotonic() - getattr(_request_clock, 'last', 0) > 15:
            _drop()
        conn = _connection()
        conn.request(method, path, body=json.dumps(body) if body else None, headers=headers)
        response = conn.getresponse()
        raw = response.read()
        _request_clock.last = time.monotonic()
        return response.status, json.loads(raw) if raw else None
    except Exception as exc:
        _drop()
        return -1, {'error': type(exc).__name__}


def reserve_cost(side, price, count):
    p, n = D(price), D(count)
    fee = (D('.07') * p * (1-p) * n).quantize(D('.01'), rounding=ROUND_CEILING)
    return n * (p if side == 'bid' else 1-p) + fee


class Ledger:
    def __init__(self, path, total=25, per_match=5):
        self.path = str(path)
        self.total, self.per_match = D(total), D(per_match)
        self.state = json.loads(Path(path).read_text()) if Path(path).exists() else {'version': 1, 'orders': {}}
        if self.state.get('version') != 1 or not isinstance(self.state.get('orders'), dict):
            raise ValueError('invalid pilot ledger')
        for key, row in self.state['orders'].items():
            if (row.get('client_order_id') != key or row.get('status') not in ('unresolved', 'filled', 'no_fill', 'not_found')
                    or row.get('side') not in ('bid', 'ask') or not row.get('match')
                    or not D(row['reserved']).is_finite() or D(row['reserved']) < 0
                    or D(row['reserved']) != reserve_cost(row['side'], row['price'], row['count'])):
                raise ValueError('invalid durable order reservation')
        if self.used() > self.total or any(self.used(o['match']) > self.per_match
                                          for o in self.state['orders'].values()):
            raise ValueError('ledger exceeds configured pilot budget')
        self.save()

    def save(self):
        atomic_json(self.path, self.state)
        # Persist the renamed directory entry as well as file contents.
        fd = os.open(str(Path(self.path).parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def unresolved(self):
        return [o for o in self.state['orders'].values() if o['status'] == 'unresolved']

    def used(self, match=None):
        # Filled allocations are not recycled during this first pilot. Thus the
        # lifetime total bounds loss even across settlement, restarts and wins.
        return sum((D(o['reserved']) for o in self.state['orders'].values()
                    if o['status'] not in ('no_fill', 'not_found') and (match is None or o['match'] == match)), D(0))

    def prepare(self, c):
        if self.unresolved() or self.state.get('halt_reason'):
            return None
        room = min(self.total-self.used(), self.per_match-self.used(c['match']))
        n = min(int(c['quantity']), 500)
        while n > 0 and reserve_cost(c['side'], c['price'], n) > room:
            n -= 1
        if n < 1:
            return None
        oid = PREFIX+str(uuid.uuid4())
        row = dict(c, client_order_id=oid, count=n, status='unresolved', created_at=time.time(),
                   reserved=str(reserve_cost(c['side'], c['price'], n)))
        self.state['orders'][oid] = row
        self.save()  # MUST precede the HTTP request; failure prevents submission.
        return row

    def accept(self, row, response, terminal=False):
        o = response.get('order', response) if isinstance(response, dict) else {}
        if not isinstance(o, dict) or not o.get('order_id'):
            return False
        if o.get('client_order_id') not in (None, row['client_order_id']):
            return False
        if o.get('ticker') not in (None, row['ticker']):
            return False
        key = 'fill_count' if 'fill_count' in o else 'fill_count_fp'
        try:
            filled = D(o[key])
            if not filled.is_finite() or not 0 <= filled <= row['count']:
                return False
        except (KeyError, ValueError, ArithmeticError):
            return False
        if o.get('status') == 'resting':
            return False
        if not terminal and o.get('status') not in ('executed', 'canceled'):
            return False
        row.update(order_id=o['order_id'], filled=str(filled), response=o,
                   status='filled' if filled else 'no_fill', resolved_at=time.time())
        # Retain full requested reservation for any fill; conservative even if
        # a partial response or fee report is incomplete. Never infer actual P&L.
        self.save()
        return True

    def reconcile(self, call=request, now=None):
        now = time.time() if now is None else now
        targets = [o for o in self.state['orders'].values()
                   if o['status'] in ('unresolved', 'not_found')]
        for row in targets:
            was_released = row['status'] == 'not_found'
            try:
                orders = read_pages(call, '/portfolio/orders', 'orders', ticker=row['ticker'])
                # Completed orders/fills can migrate to historical storage.
                status, cutoff = call('GET', API+'/historical/cutoff')
                if status != 200 or not isinstance(cutoff, dict):
                    raise ValueError('historical cutoff unavailable')
                start = row['created_at']-60
                order_cutoff = timestamp(cutoff['orders_updated_ts'])
                fill_cutoff = timestamp(cutoff['trades_created_ts'])
                if start <= order_cutoff:
                    orders += read_pages(call, '/historical/orders', 'orders',
                                         ticker=row['ticker'], min_ts=int(start))
                matching = [o for o in orders if o.get('client_order_id') == row['client_order_id']]
                if matching:
                    if row['status'] == 'not_found':
                        self.state['halt_reason'] = 'Late exchange order appeared after absent-order release'
                    for order in matching:
                        if self.accept(row, order):
                            break
                    row.pop('absence_checks', None)
                    self.save()
                    continue
                # Keep checking released tombstones for late orders OR fills.
                if now-row['created_at'] < 600:
                    continue
                status, stamp = call('GET', API+'/exchange/user_data_timestamp')
                if status != 200 or not isinstance(stamp, dict):
                    raise ValueError('account data watermark unavailable')
                as_of = timestamp(stamp['as_of_time'])
                if not now-60 <= as_of <= now+5 or as_of < row['created_at']+600:
                    raise ValueError('account data watermark stale or before cutoff')
                fills = read_pages(call, '/portfolio/fills', 'fills',
                                   ticker=row['ticker'], min_ts=int(start))
                if start <= fill_cutoff:
                    fills += read_pages(call, '/historical/fills', 'fills',
                                        ticker=row['ticker'], min_ts=int(start))
                positions = read_pages(call, '/portfolio/positions', 'market_positions',
                                       ticker=row['ticker'])
                settlements = read_pages(call, '/portfolio/settlements', 'settlements',
                                         ticker=row['ticker'], min_ts=int(start))
                # Be conservative around unrelated manual activity too. Any
                # same-market fill/settlement within this window blocks release.
                if fills or settlements:
                    if was_released:
                        self.state['halt_reason'] = 'Late fill/settlement evidence after absent-order release'
                        row['status'] = 'unresolved'
                    raise ValueError('same-market fill or settlement exists; attribution required')
                for pos in positions:
                    if pos.get('ticker') != row['ticker']:
                        raise ValueError('position filter mismatch')
                    quantity, exposure = D(pos['position_fp']), D(pos['market_exposure_dollars'])
                    if not quantity.is_finite() or not exposure.is_finite() or quantity != 0 or exposure != 0:
                        if was_released:
                            self.state['halt_reason'] = 'Position appeared after absent-order release'
                            row['status'] = 'unresolved'
                        raise ValueError('nonzero or invalid market position/exposure')
                # An unmatched working order in this market also blocks release.
                if any(o.get('status') not in ('executed', 'canceled') for o in orders):
                    raise ValueError('same-market pending/nonterminal order exists')
                if was_released:
                    row['last_absence_recheck_at'] = now
                    self.save()
                    continue
                checks = row.setdefault('absence_checks', [])
                # A long gap is not consecutive evidence; refresh all three.
                if checks and now-checks[-1]['at'] > 120:
                    checks.clear()
                if not checks or now-checks[-1]['at'] >= 30:
                    checks.append({'at': now, 'as_of': as_of, 'orders': len(orders),
                                   'fills': 0, 'positions': len(positions), 'settlements': 0})
                if len(checks) >= 3 and checks[-1]['at']-checks[0]['at'] >= 60:
                    row.update(status='not_found', filled='0', resolved_at=now,
                               resolution='assumed-not-executed-after-10m-and-three-clean-checks')
                row.pop('reconcile_error', None)
                self.save()
            except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
                row['reconcile_error'] = str(exc)[:200]
                row.pop('absence_checks', None)
                self.save()


def timestamp(value):
    from datetime import datetime
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def read_pages(call, path, key, **params):
    from urllib.parse import urlencode
    rows, cursor, seen = [], None, set()
    for _ in range(100):
        query = dict(params, limit=200)
        if cursor:
            query['cursor'] = cursor
        status, data = call('GET', API+path+'?'+urlencode(query))
        if (status != 200 or not isinstance(data, dict) or not isinstance(data.get(key), list)
                or 'cursor' not in data or not isinstance(data['cursor'], str)):
            raise ValueError(path+' incomplete or failed read')
        rows.extend(data[key])
        cursor = data['cursor']
        if not cursor:
            return rows
        if cursor in seen:
            raise ValueError(path+' repeated cursor')
        seen.add(cursor)
    raise ValueError(path+' pagination limit reached')
