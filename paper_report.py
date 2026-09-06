"""Summarize durable paper accounts, observation coverage and service health."""
import argparse
from collections import Counter
import glob
import json
import os
import shutil
import time
from datetime import datetime, timezone

from iolib import LIVE as OUT
from paper_support import atomic_json


def read(name):
    path = os.path.join(OUT, name)
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
    counts = Counter()
    day = datetime.now(timezone.utc).strftime('%Y%m%d')
    for path in glob.glob(os.path.join(OUT, f'winner_taker_actions_paper_{day}.jsonl')):
        with open(path) as f:
            for line in f:
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                counts[row.get('a', 'unknown')] += 1
    warnings = []
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
    if shutil.disk_usage(OUT).free < 2 * 1024**3:
        warnings.append('less than 2 GiB disk free')
    return {'at': datetime.now(timezone.utc).isoformat(), 't': now,
            'warnings': warnings, 'heartbeat_age_s': round(health_age, 1),
            'score_cache_age_s': round(score_age, 1), 'health': health,
            'today_action_counts': dict(counts),
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
