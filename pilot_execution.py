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
            if (row.get('client_order_id') != key or row.get('status') not in ('unresolved', 'filled', 'no_fill')
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
                    if o['status'] != 'no_fill' and (match is None or o['match'] == match)), D(0))

    def prepare(self, c):
        if self.unresolved():
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

    def reconcile(self, call=request):
        if not self.unresolved():
            return
        orders, cursor = [], None
        for _ in range(100):
            from urllib.parse import urlencode
            params = {'limit': 200}
            if cursor:
                params['cursor'] = cursor
            status, data = call('GET', API+'/portfolio/orders?'+urlencode(params))
            if status != 200 or not isinstance(data, dict) or not isinstance(data.get('orders'), list):
                return
            orders.extend(data['orders'])
            cursor = data.get('cursor')
            if not cursor:
                break
        else:
            return
        by_id = {o.get('client_order_id'): o for o in orders}
        for row in self.unresolved():
            o = by_id.get(row['client_order_id'])
            if o:
                self.accept(row, o)
        # Absence from a read is NOT proof of rejection. Keep paused/reserved.
