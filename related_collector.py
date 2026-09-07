"""Capture annual Grand Slam books and rules; no order submission or inference.

Annual title history and remaining majors must be verified before these contracts
can become trading legs. Preserve raw sequence numbers for later replay.
"""
import asyncio
from datetime import datetime, timezone
import inspect
import json
import os
import signal
import time

import websockets
from iolib import LIVE, DayWriter
from kalshi import paginate, ws_headers, WS_HOST, WS_PATH
from paper_support import atomic_json

SERIES = ('KXATPGRANDSLAM', 'KXWTAGRANDSLAM', 'KXGRANDSLAM')


def discover():
    markets = []
    for series in SERIES:
        markets.extend(paginate('/markets', 'markets', series_ticker=series, status='open', limit=1000))
    return markets


async def run():
    writer = DayWriter('related_ws', level=1, flush_sec=2)
    connection = 0
    messages = 0
    state = {'feed_ready': False, 'last_message_at': 0, 'market_count': 0}

    def health():
        atomic_json(os.path.join(LIVE, 'related_health.json'),
                    dict(state, updated_at=time.time(), messages=messages, pid=os.getpid()))

    def record(row):
        now = time.time()
        writer.write(json.dumps(dict(row, t=now, connection=connection), separators=(',', ':'))+'\n',
                     datetime.fromtimestamp(now, timezone.utc).strftime('%Y%m%d'), now)

    try:
        while True:
            state['feed_ready'] = False
            health()
            try:
                markets = await asyncio.to_thread(discover)
                tickers = sorted({m['ticker'] for m in markets})
                state['market_count'] = len(tickers)
                atomic_json(os.path.join(LIVE, 'related_markets.json'),
                            {'updated_at': time.time(), 'markets': markets})
                record({'type': 'catalog', 'markets': markets})
                if not tickers:
                    await asyncio.sleep(60)
                    continue
                connection += 1
                state['connection'] = connection
                header = ('additional_headers' if 'additional_headers' in
                          inspect.signature(websockets.connect).parameters else 'extra_headers')
                async with websockets.connect(WS_HOST+WS_PATH, ping_interval=10, ping_timeout=30,
                                              **{header: ws_headers()}) as ws:
                    await ws.send(json.dumps({'id': connection, 'cmd': 'subscribe', 'params':
                                  {'channels': ['orderbook_delta'], 'market_tickers': tickers}}))
                    deadline = time.time()+600
                    sequences, snapshots = {}, set()
                    next_health = 0
                    while time.time() < deadline:
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=10)
                        except asyncio.TimeoutError:
                            health()
                            if writer.fh:
                                writer.fh.flush()
                            continue
                        msg = json.loads(raw)
                        record(msg)
                        if msg.get('type') == 'error':
                            raise ValueError('subscription error: '+str(msg.get('msg')))
                        if msg.get('type') in ('orderbook_snapshot', 'orderbook_delta'):
                            sid, seq = msg.get('sid'), msg.get('seq')
                            tk = msg['msg'].get('market_ticker')
                            if sid is None or not isinstance(seq, int):
                                raise ValueError('missing sequence')
                            if sid in sequences and seq != sequences[sid]+1:
                                raise ValueError('sequence gap')
                            sequences[sid] = seq
                            if msg['type'] == 'orderbook_snapshot':
                                snapshots.add(tk)
                            elif tk not in snapshots:
                                raise ValueError('delta before snapshot')
                            messages += 1
                            state['last_message_at'] = time.time()
                            state['feed_ready'] = snapshots.issuperset(tickers)
                            state['snapshots'] = len(snapshots)
                            state.pop('error', None)
                        if time.time() >= next_health:
                            health()
                            next_health = time.time()+10
            except Exception as exc:
                state.update(feed_ready=False, error=str(exc)[:200])
                record({'type': 'capture_error', 'error': str(exc)[:200]})
                health()
                await asyncio.sleep(10)
    finally:
        writer.close()


async def main():
    task = asyncio.create_task(run())
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, task.cancel)
    try:
        await task
    except asyncio.CancelledError:
        pass


if __name__ == '__main__':
    asyncio.run(main())
