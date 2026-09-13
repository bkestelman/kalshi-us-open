"""Capped live and shadow tennis pilot: identical strategy, different execution."""
from concurrent.futures import ThreadPoolExecutor
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
from pilot_execution import Ledger, PREFIX, request, exchange_cash, D
from pilot_depth import paper_sweep
from pilot_accounting import refresh as refresh_accounting, summary as accounting_summary
from pilot_strategy import candidate, SERIES
from winner_taker import WinnerTaker

SHARED = Path(__file__).parent / 'data' / 'live'


class Pilot(WinnerTaker):
    batch_entries = True

    def __init__(self, live):
        super().__init__(False)  # Reuse discovery/feed only, never legacy live executor.
        self.live = live
        self.order_pool.shutdown(wait=False)
        self.order_pool = ThreadPoolExecutor(max_workers=8)
        self.next_batch_at = 0
        self.disc = FollowerDiscovery(str(SHARED / 'winner_taker_discovery.json'))
        config = json.loads((Path(OUT)/'pilot_config.json').read_text())
        if (type(config.get('recycle_on_settlement')) is not bool
                or any(not D(config[k]).is_finite() or D(config[k]) <= 0
                       for k in ('total_cap', 'per_match_cap'))):
            raise ValueError('invalid local pilot configuration')
        self.ledger = Ledger(Path(OUT)/'pilot_ledger.json', config['total_cap'],
                             config['per_match_cap'], recycle=config['recycle_on_settlement'])
        self.last_accounting = 0
        self.started_at = time.time()
        self.busy = False
        self.retry = {}
        self.last_reconcile = 0
        self.account_ready = not live
        self.last_error = None
        self.cash_error = None
        self.cash_checked = {}
        self.disabled_path = Path(OUT)/'STOP'

    def evaluate(self, leg):
        return None, 'pilot-shared-strategy'

    def entry_ready(self):
        return (self.account_ready and self.ledger.total-self.ledger.used() >= D('.86')
                and not self.disabled_path.exists()
                and not self.ledger.unresolved() and not self.ledger.state.get('halt_reason')
                and self.feed_ready and time.time()-self.last_message_at <= 30
                and time.time()-(SHARED/'winner_taker_discovery.json').stat().st_mtime <= 180)

    def available(self, leg):
        return (time.time() >= self.retry.get(leg.win_tk, 0)
                and not any(o['ticker'] == leg.win_tk and o['status'] in ('filled', 'not_found')
                            for o in self.ledger.state['orders'].values()))

    def on_book(self, tk):
        if self.busy or time.time() < getattr(self, 'next_batch_at', 0) or not self.entry_ready():
            return
        for leg in self.legs.values():
            if tk not in (leg.win_tk, leg.match_tk) or not self.available(leg):
                continue
            if candidate(self, leg):
                if not self.batch_entries:
                    self.busy = True
                    asyncio.create_task(self.prepare_live(leg))
                    return
                match = leg.match_tk.rsplit('-', 1)[0]
                # Include BOTH player listings and all related markets. A
                # qualifier update must also trigger available tournament legs.
                legs = list({x.win_tk: x for x in self.legs.values()
                             if x.match_tk.rsplit('-', 1)[0] == match
                             and self.available(x)}.values())
                self.busy = True
                asyncio.create_task(self.prepare_batch(legs))
                return

    async def prepare_live(self, leg):
        # Compatibility for callers preparing a single market.
        await self.prepare_batch([leg])

    async def prepare_batch(self, legs):
        """Recheck signals after parallel market/cash reads, then reserve together."""
        try:
            self.next_batch_at = time.time()+1
            # Eight markets cover both players through R16. Bound each burst;
            # later batches can consider further markets with fresh cash.
            legs = legs[:8]
            markets, cash_limits = {}, None
            if self.live:
                results = await asyncio.gather(
                    *(asyncio.to_thread(get, '/markets/'+leg.win_tk) for leg in legs),
                    asyncio.to_thread(request, 'GET', API+'/portfolio/balance'),
                    return_exceptions=True)
                balance_result = results[-1]
                if isinstance(balance_result, Exception):
                    raise ValueError('exchange cash read failed') from balance_result
                status, balance = balance_result
                if status != 200 or not isinstance(balance, dict):
                    raise ValueError('exchange cash read failed')
                cash_limits = {}
                errors = []
                for leg, data in zip(legs, results[:-1]):
                    try:
                        if isinstance(data, Exception):
                            raise ValueError('market read failed') from data
                        market = (data or {}).get('market', {})
                        if (market.get('ticker') != leg.win_tk or market.get('status') != 'active'
                                or market.get('result')):
                            continue
                        index, cash = exchange_cash(market, balance)
                        markets[leg.win_tk] = index
                        cash_limits[index] = cash
                        self.cash_checked[str(index)] = {'cash': str(cash), 'at': time.time()}
                    except Exception as exc:
                        errors.append(leg.win_tk+': '+str(exc)[:150])
                self.cash_error = '; '.join(errors) or None
                if errors:
                    self.jlog({'a': 'pilot_cash_check_error', 'error': self.cash_error})
            if not self.entry_ready():
                return
            candidates = []
            for leg in legs:
                if not self.available(leg) or (self.live and leg.win_tk not in markets):
                    continue
                c = candidate(self, leg)
                if c:
                    if self.live:
                        c['exchange_index'] = markets[leg.win_tk]
                        c['cash_at_prepare'] = str(cash_limits[c['exchange_index']])
                    candidates.append(c)
            rows = self.ledger.prepare_batch(candidates, cash_limits)
            if rows:
                self.next_batch_at = time.time()+1
                self.jlog({'a': 'pilot_batch', 'batch_id': rows[0]['batch_id'],
                           'match': rows[0]['match'], 'candidates': candidates,
                           'orders': [{'ticker': r['ticker'], 'count': r['count'],
                                       'price': r['price'], 'reserved': r['reserved']} for r in rows]})
                # execute catches individual failures. The gate stays closed
                # until ALL responses finish; unresolved siblings block entry.
                await asyncio.gather(*(self.execute(row, batch=True) for row in rows))
        except Exception as exc:
            error = type(exc).__name__+': '+str(exc)[:200]
            if self.ledger.unresolved():
                self.account_ready = False
                self.last_error = error
                self.jlog({'a': 'pilot_error', 'error': error})
            else:
                self.cash_error = error
                self.next_batch_at = time.time()+10
                self.jlog({'a': 'pilot_cash_check_error', 'error': error})
        finally:
            self.busy = False

    async def execute(self, row, batch=False):
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
                started = time.time()
                market, book = await asyncio.gather(
                    asyncio.to_thread(get, '/markets/'+row['ticker']),
                    asyncio.to_thread(get, '/markets/'+row['ticker']+'/orderbook'))
                b = self.books.get(row['ticker'])
                fills, why = (paper_sweep(row, b, market, book)
                              if b and self.feed_ready and time.time()-self.last_message_at <= 30
                              else ([], 'feed-not-ready'))
                n = sum((D(f['quantity']) for f in fills), D(0))
                verification = {'status': why, 'requested_at': started,
                                'received_at': time.time(), 'levels': fills}
                row['paper_execution'] = verification
                self.ledger.accept(row, {'order_id': 'paper-'+row['client_order_id'],
                                        'fill_count': str(n)}, terminal=True)
                self.jlog({'a': 'pilot_paper_fill', 'ticker': row['ticker'], 'side': row['side'],
                           'price': row['price'], 'count': float(n), 'verification': verification})
        except Exception as exc:
            # Reservation already persisted; leave it unresolved and stop entry.
            self.account_ready = False
            self.last_error = type(exc).__name__+': '+str(exc)[:200]
            self.jlog({'a': 'pilot_error', 'error': self.last_error})
        finally:
            self.retry[row['ticker']] = time.time()+10
            if not batch:
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
            if status != 200 or not isinstance(balance, dict) or balance.get('balance', 0) <= 0:
                raise RuntimeError('account read failed or no available cash')
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
        if self.batch_entries:
            await asyncio.to_thread(refresh_accounting, self.ledger, request, get, self.live)
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
                if self.batch_entries and not self.busy and time.time()-self.last_accounting > 60:
                    self.last_accounting = time.time()
                    self.busy = True
                    try:
                        await asyncio.to_thread(refresh_accounting, self.ledger, request, get, self.live)
                    finally:
                        self.busy = False
                health = {'updated_at': time.time(), 'mode': 'live' if self.live else 'paper',
                          'pid': os.getpid(), 'started_at': self.started_at, 'feed_ready': self.feed_ready,
                          'messages': self.msgs, 'last_message_at': self.last_message_at,
                          'legs': len(self.legs), 'allocated': str(self.ledger.used()),
                          'total_cap': str(self.ledger.total), 'per_match_cap': str(self.ledger.per_match),
                          'unresolved': len(self.ledger.unresolved()), 'error': self.last_error or self.ledger.state.get('halt_reason'),
                          'disabled': self.disabled_path.exists(), 'account_ready': self.account_ready,
                          'orders': len(self.ledger.state['orders']),
                          'cash_error': self.cash_error, 'exchange_cash': self.cash_checked}
                health.update(accounting_summary(self.ledger))
                health['pnl_basis'] = 'actual_exchange_fees' if self.live else 'paper_estimated_fees'
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
