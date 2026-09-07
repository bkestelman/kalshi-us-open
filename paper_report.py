"""Summarize durable paper accounts, observation coverage and service health."""
import argparse
from collections import Counter
import glob
import json
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone

from iolib import LIVE as OUT
from paper_support import atomic_json


def read(name, directory=OUT):
    path = os.path.join(directory, name)
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        return json.load(f)


def report():
    now = time.time()
    health = read('winner_taker_health.json')
    scores = read('tennis_scores.json')
    short = read('winner_taker_paper_account.json')
    long = read('qualifier_paper_account.json')
    confirmed_dir = os.path.join(os.path.dirname(OUT), 'confirmed')
    confirmed_health = read('winner_taker_health.json', confirmed_dir)
    confirmed_short = read('winner_taker_paper_account.json', confirmed_dir)
    confirmed_long = read('qualifier_paper_account.json', confirmed_dir)
    counts = Counter()
    confirmed_counts = Counter()
    discovery = read('winner_taker_discovery.json')
    day = datetime.now(timezone.utc).strftime('%Y%m%d')
    for directory, counter in ((OUT, counts), (confirmed_dir, confirmed_counts)):
        for path in glob.glob(os.path.join(directory, f'winner_taker_actions_paper_{day}.jsonl')):
            with open(path) as f:
                for line in f:
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    counter[row.get('a', 'unknown')] += 1
    warnings = []
    try:
        review_state = subprocess.run(['systemctl', 'show', 'paper-review.service',
                                       '--property=Result', '--value'],
                                      capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        review_state = 'unknown'
    if review_state not in ('success', ''):
        warnings.append('scheduled Codex review result: ' + review_state)
    related = read('related_health.json')
    if not related or now - related.get('updated_at', 0) > 90:
        warnings.append('related-market collector heartbeat missing or stale')
    elif related.get('market_count') and not related.get('feed_ready'):
        warnings.append('related-market collector awaiting snapshots: ' + related.get('error', ''))
    discovery_age = now - discovery.get('updated_at', 0)
    if discovery_age > 300:
        warnings.append('primary discovery snapshot missing or older than 300 seconds')
    captures = {}
    for prefix in ('winner_taker_ws', 'related_ws'):
        paths = glob.glob(os.path.join(OUT, prefix + '_*.jsonl*'))
        latest = max(paths, key=os.path.getmtime) if paths else None
        age = now - os.path.getmtime(latest) if latest else None
        captures[prefix] = {'path': latest, 'age_s': round(age, 1) if age is not None else None,
                            'bytes': os.path.getsize(latest) if latest else 0}
        # Related capture refreshes subscriptions every ten minutes, including overnight.
        active = prefix == 'related_ws' or bool(health.get('matches'))
        limit = 720 if prefix == 'related_ws' else 180
        if active and (age is None or age > limit):
            warnings.append(prefix + ' capture missing or not being flushed')
    health_age = now - health.get('updated_at', 0)
    score_age = now - scores.get('updated_at', 0)
    if health_age > 300:
        warnings.append('paper heartbeat missing or older than 300 seconds')
    if health.get('matches') and not health.get('feed_ready'):
        warnings.append('paper feed awaiting snapshots')
    if health.get('matches') and now - health.get('last_message_at', 0) > 180:
        warnings.append('watched matches but no feed messages for 180 seconds')
    if score_age > 30:
        warnings.append('score collector snapshot missing or older than 30 seconds')
    if not confirmed_health or now - confirmed_health.get('updated_at', 0) > 300:
        warnings.append('confirmation-only paper heartbeat older than 300 seconds')
    if confirmed_health.get('matches') and not confirmed_health.get('feed_ready'):
        warnings.append('confirmation-only paper feed awaiting snapshots')
    if shutil.disk_usage(OUT).free < 2 * 1024**3:
        warnings.append('less than 2 GiB disk free')
    return {'at': datetime.now(timezone.utc).isoformat(), 't': now,
            'warnings': warnings, 'related_health': related, 'captures': captures, 'review_result': review_state, 'heartbeat_age_s': round(health_age, 1),
            'score_cache_age_s': round(score_age, 1), 'health': health,
            'today_action_counts': dict(counts),
            'confirmed_today_action_counts': dict(confirmed_counts),
            'discovery_age_s': round(discovery_age, 1),
            'confirmed_health': confirmed_health,
            'confirmed_account': {
                'short_takes': confirmed_short.get('takes', 0),
                'short_realized': confirmed_short.get('realized', 0),
                'short_positions': len(confirmed_short.get('positions', {})),
                'qualifier_takes': confirmed_long.get('takes', 0),
                'qualifier_realized': confirmed_long.get('realized', 0),
                'qualifier_positions': len(confirmed_long.get('positions', {}))},
            'short_account': {'takes': short.get('takes', 0), 'realized': short.get('realized', 0),
                              'positions': len(short.get('positions', {}))},
            'qualifier_account': {'takes': long.get('takes', 0), 'realized': long.get('realized', 0),
                                  'positions': len(long.get('positions', {}))}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--record', action='store_true')
    args = parser.parse_args()
    result = report()
    if args.record:
        atomic_json(os.path.join(OUT, 'paper_report.json'), result)
        with open(os.path.join(OUT, 'paper_monitor.jsonl'), 'a') as f:
            f.write(json.dumps(result, separators=(',', ':')) + '\n')
    print(json.dumps(result, indent=2))
