"""Capped live and shadow tennis pilot: identical strategy, different execution."""
import argparse
import asyncio
import fcntl
import json
import os
from pathlib import Path
import signal
import time

from discovery_feed import FollowerDiscovery
from iolib import LIVE as OUT
from kalshi import API, get
from paper_support import atomic_json
from pilot_execution import Ledger, PREFIX, request
from pilot_strategy import candidate, SERIES
from winner_taker import WinnerTaker

SHARED = Path(__file__).parent / 'data' / 'live'


class Pilot(WinnerTaker):
    def __init__(self, live):
        super().__init__(False)  # Reuse discovery/feed only, never legacy live executor.
        self.live = live
        self.disc = FollowerDiscovery(str(SHARED / 'winner_taker_discovery.json'))
        self.ledger = Ledger(Path(OUT)/'pilot_ledger.json', 25 if live else 500,
                             5 if live else 125)
        self.started_at = time.time()
        self.busy = False
        self.retry = {}
        self.last_reconcile = 0
        self.account_ready = not live
        self.last_error = None
        self.disabled_path = Path(OUT)/'STOP'

    def evaluate(self, leg):
        return None, 'pilot-shared-strategy'

    def on_book(self, tk):
        if self.busy or not self.account_ready or self.disabled_path.exists() or self.ledger.unresolved() or self.ledger.state.get('halt_reason'):
            return
        if time.time()-self.last_message_at > 30:
            return
        if time.time()-(SHARED/'winner_taker_discovery.json').stat().st_mtime > 180:
            return
        for leg in self.legs.values():
            if tk not in (leg.win_tk, leg.match_tk):
                continue
            if time.time() < self.retry.get(leg.win_tk, 0):
                continue
            # One filled order per market/direction for this pilot. Prevents
            # shadow duplicate-depth fills and bounds repeated live attempts.
            if any(o['ticker'] == leg.win_tk and o['status'] in ('filled', 'not_found')
                   for o in self.ledger.state['orders'].values()):
                continue
            c = candidate(self, leg)
            if c:
                row = self.ledger.prepare(c)
                if row:
                    self.busy = True
                    asyncio.create_task(self.execute(row))
                    return

    async def execute(self, row):
        try:
            if self.live:
                body = {'ticker': row['ticker'], 'client_order_id': row['client_order_id'],
                        'side': row['side'], 'count': f"{row['count']}.00", 'price': f"{row['price']:.4f}",
                        'time_in_force': 'immediate_or_cancel',
                        'self_trade_prevention_type': 'taker_at_cross', 'post_only': False}
                status, response = await asyncio.get_running_loop().run_in_executor(
                    self.order_pool, request, 'POST', API+'/portfolio/events/orders', body)
                row['http_status'] = status
                row['raw_response'] = response
                self.ledger.save()
                # IOC V2 response is terminal; absent/malformed response remains
                # unresolved. Even 4xx rejections reconcile rather than retry.
                resolved = status in (200, 201) and self.ledger.accept(row, response, terminal=True)
                self.jlog({'a': 'pilot_order', 'client_order_id': row['client_order_id'],
                           'ticker': row['ticker'], 'side': row['side'], 'resolved': bool(resolved),
                           'status': status, 'filled': row.get('filled'), 'reserved': row['reserved']})
            else:
                await asyncio.sleep(.1)
                side = 'yes' if row['side'] == 'ask' else 'no'
                p = row['price'] if side == 'yes' else round(1-row['price'], 4)
                qty, verification = await self.verify_paper_depth(row['ticker'], side, p)
                b = self.books.get(row['ticker'])
                wsqty = (b.yes if side == 'yes' else b.no).get(p, 0) if b and self.feed_ready else 0
                n = min(row['count'], int(qty), int(wsqty))
                self.ledger.accept(row, {'order_id': 'paper-'+row['client_order_id'],
                                        'fill_count': str(n)}, terminal=True)
                self.jlog({'a': 'pilot_paper_fill', 'ticker': row['ticker'], 'side': row['side'],
                           'price': row['price'], 'count': n, 'verification': verification})
        except Exception as exc:
            # Reservation already persisted; leave it unresolved and stop entry.
            self.account_ready = False
            self.last_error = type(exc).__name__+': '+str(exc)[:200]
            self.jlog({'a': 'pilot_error', 'error': self.last_error})
        finally:
            self.retry[row['ticker']] = time.time()+10
            self.busy = False

    async def preflight(self):
        for series in SERIES:
            d = await asyncio.to_thread(get, '/series/'+series)
            s = (d or {}).get('series', {})
            if s.get('fee_type') != 'quadratic' or float(s.get('fee_multiplier', 0)) != 1:
                raise RuntimeError('unverified fee schedule: '+series)
        if self.live:
            # Read-only signed account validation; never use a test order.
            status, balance = await asyncio.to_thread(request, 'GET', API+'/portfolio/balance')
            if status != 200 or not isinstance(balance, dict) or balance.get('balance', 0) < 2500:
                raise RuntimeError('account read failed or less than $25 available')
            cursor = None
            for _ in range(100):
                from urllib.parse import urlencode
                params = {'limit': 200}
                if cursor:
                    params['cursor'] = cursor
                status, data = await asyncio.to_thread(request, 'GET', API+'/portfolio/orders?'+urlencode(params))
                if status != 200 or not isinstance(data, dict) or not isinstance(data.get('orders'), list):
                    raise RuntimeError('cannot reconcile account orders at startup')
                for o in data['orders']:
                    cid = o.get('client_order_id', '')
                    if cid.startswith(PREFIX) and cid not in self.ledger.state['orders']:
                        raise RuntimeError('exchange pilot order missing from durable ledger')
                cursor = data.get('cursor')
                if not cursor:
                    break
            else:
                raise RuntimeError('account pagination incomplete')
            await asyncio.to_thread(self.ledger.reconcile)
        self.account_ready = True

    async def maintenance(self):
        while True:
            try:
                self.scores = json.loads((SHARED/'tennis_scores.json').read_text()).get('matches', {})
                if self.live and not self.busy and time.time()-self.last_reconcile > 10:
                    self.last_reconcile = time.time()
                    self.busy = True
                    try:
                        await asyncio.to_thread(self.ledger.reconcile)
                    finally:
                        self.busy = False
                health = {'updated_at': time.time(), 'mode': 'live' if self.live else 'paper',
                          'pid': os.getpid(), 'started_at': self.started_at, 'feed_ready': self.feed_ready,
                          'messages': self.msgs, 'last_message_at': self.last_message_at,
                          'legs': len(self.legs), 'allocated': str(self.ledger.used()),
                          'total_cap': str(self.ledger.total), 'per_match_cap': str(self.ledger.per_match),
                          'unresolved': len(self.ledger.unresolved()), 'error': self.last_error or self.ledger.state.get('halt_reason'),
                          'disabled': self.disabled_path.exists(), 'account_ready': self.account_ready,
                          'orders': len(self.ledger.state['orders'])}
                atomic_json(str(Path(OUT)/'pilot_health.json'), health)
                # Evaluate all loaded books once after initial snapshots/score
                # refresh too; steady-state entries remain websocket-driven.
                for tk in list(self.legs_by_match):
                    self.on_book(tk)
            except Exception as exc:
                self.account_ready = False
                self.last_error = str(exc)[:200]
                raise
            await asyncio.sleep(1)

    async def run(self):
        self.feed_enforced = True
        self.dirty = asyncio.Event()
        await self.preflight()
        self.jlog({'a': 'pilot_start', 'cap': str(self.ledger.total),
                   'match_cap': str(self.ledger.per_match), 'strategy': 'one-sided-99-v1'})
        await asyncio.gather(self.discovery_loop(), self.ws_loop(), self.maintenance())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['paper', 'live'])
    args = parser.parse_args()
    Path(OUT).mkdir(parents=True, exist_ok=True)
    with (Path(OUT)/'pilot.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        asyncio.run(Pilot(args.mode == 'live').run())


if __name__ == '__main__':
    main()
