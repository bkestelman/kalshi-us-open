"""Public Kalshi tennis scores, collected off the bot's execution path.

Snapshots retain local receive time, not an invented source update timestamp.
An unchanged score freshly fetched can still be delayed at the provider.
"""
import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

from iolib import LIVE as OUT
from kalshi import get, paginate
from paper_support import atomic_json


def discover():
    since = datetime.now(timezone.utc) - timedelta(days=2)
    milestones = paginate('/milestones', 'milestones', limit=200,
                          type='tennis_tournament_singles',
                          minimum_start_date=since.strftime('%Y-%m-%dT%H:%M:%SZ'))
    now = time.time()
    selected = {}
    for m in milestones:
        details = m.get('details') or {}
        event = details.get('main_game_event_ticker', '')
        if not event.startswith(('KXATPMATCH-', 'KXWTAMATCH-')):
            continue
        start = datetime.fromisoformat(m['start_date'].replace('Z', '+00:00')).timestamp()
        if now - 36 * 3600 <= start <= now + 3600:
            selected[m['id']] = m
    return selected


def collect(interval=2.0, once=False):
    os.makedirs(OUT, exist_ok=True)
    milestones, mappings, snapshots, previous = {}, {}, {}, {}
    next_discovery = 0.0
    while True:
        started = time.monotonic()
        try:
            if time.time() >= next_discovery:
                milestones = discover()
                next_discovery = time.time() + 60
                for m in milestones.values():
                    event = m['details']['main_game_event_ticker']
                    if event not in mappings:
                        data = get('/events/' + event, with_nested_markets='true') or {}
                        markets = (data.get('event') or {}).get('markets') or data.get('markets') or []
                        mappings[event] = {v['ticker']: {
                            'id': (v.get('custom_strike') or {}).get('tennis_competitor'),
                            'name': v.get('yes_sub_title'),
                            'rules_primary': v.get('rules_primary')}
                            for v in markets}
            # Finished matches only need a slow recheck for corrections.
            ids = [mid for mid, m in milestones.items()
                   if not snapshots.get(m['details']['main_game_event_ticker'], {}).get('finished')
                   or time.time() - snapshots[m['details']['main_game_event_ticker']]['received_at'] >= 60]
            for offset in range(0, len(ids), 100):
                request_at = time.time()
                data = get('/live_data/batch?' + urlencode(
                    {'milestone_ids': ids[offset:offset + 100]}, doseq=True)) or {}
                received_at = time.time()
                for live in data.get('live_datas') or []:
                    mid = live.get('milestone_id')
                    if mid not in milestones:
                        continue
                    m = milestones[mid]
                    event = m['details']['main_game_event_ticker']
                    details = live.get('details') or {}
                    compact = {k: v for k, v in details.items() if not k.endswith('_statistics')}
                    snapshot = {'event': event, 'milestone_id': mid,
                                'request_at': request_at, 'received_at': received_at,
                                'best_of': m['details'].get('best_of'),
                                'round': m['details'].get('round'),
                                'players': mappings.get(event, {}), 'score': compact,
                                'finished': bool(details.get('winner')) and details.get('status') == 'closed'}
                    snapshots[event] = snapshot
                    key = json.dumps(compact, sort_keys=True)
                    if previous.get(event) != key:
                        previous[event] = key
                        day = datetime.now(timezone.utc).strftime('%Y%m%d')
                        with open(os.path.join(OUT, f'tennis_scores_{day}.jsonl'), 'a') as f:
                            f.write(json.dumps(snapshot, separators=(',', ':')) + '\n')
            atomic_json(os.path.join(OUT, 'tennis_scores.json'), {
                'updated_at': time.time(), 'matches': snapshots})
            print(f'{datetime.now(timezone.utc).isoformat()} scores: '
                  f'{len(snapshots)} matches, {len(ids)} polled', flush=True)
        except Exception as e:
            print(f'{datetime.now(timezone.utc).isoformat()} score error: {type(e).__name__}: {e}', flush=True)
            if once:
                raise
        if once:
            return
        time.sleep(max(.1, interval - (time.monotonic() - started)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once', action='store_true')
    parser.add_argument('--interval', type=float, default=2.0)
    args = parser.parse_args()
    collect(args.interval, args.once)
