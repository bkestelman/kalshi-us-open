"""Offline execution and score-timing summary for a bounded paper run."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import glob
import json
import os
import statistics

from iolib import LIVE
from score_context import confirmed_winner


def rows(pattern):
    for path in sorted(glob.glob(pattern)):
        with open(path) as f:
            for line in f:
                try:
                    yield json.loads(line)
                except ValueError:
                    continue


def summarize(directory, since, score_directory=None):
    score_directory = score_directory or directory
    ended = {}
    for row in rows(os.path.join(score_directory, 'tennis_scores_*.jsonl')):
        if confirmed_winner(row.get('score', {}), row.get('best_of')):
            ended.setdefault(row['event'], row['received_at'])
    actions = [r for r in rows(os.path.join(directory, 'winner_taker_actions_paper_*.jsonl'))
               if r['t'] >= since]
    fills = []
    for row in actions:
        if row['a'] not in ('take', 'qualifier_take'):
            continue
        first_ended = ended.get(row['match_tk'].rsplit('-', 1)[0])
        fills.append({
            'at': datetime.fromtimestamp(row['t'], timezone.utc).isoformat(),
            'ticker': row['tk'], 'side': 'yes' if row['a'] == 'qualifier_take' else 'no',
            'count': row['count'], 'yes_price': row['price'],
            'signal': row.get('signal'), 'elapsed_ms': row.get('elapsed_ms'),
            'seconds_before_first_ended_score': round(first_ended - row['t'], 3)
                if first_ended is not None else None,
            'rest_quantity': row.get('verification', {}).get('quantity'),
            'score_at_decision': row.get('score')})
    latencies = [r['elapsed_ms'] for r in fills if r['elapsed_ms'] is not None]
    no_fills = [r for r in actions if r['a'] in ('no_fill', 'qualifier_no_fill')]
    return {'since': datetime.fromtimestamp(since, timezone.utc).isoformat(),
            'as_of': datetime.now(timezone.utc).isoformat(),
            'action_counts': dict(Counter(r['a'] for r in actions)),
            'fill_count': len(fills), 'contracts': sum(r['count'] for r in fills),
            'median_elapsed_ms': statistics.median(latencies) if latencies else None,
            'no_fill_reasons': dict(Counter(r.get('verification', {}).get('status', 'unknown')
                                            for r in no_fills)),
            'settled_short_pnl_rounded': sum(r['realized'] for r in actions if r['a'] == 'settle'),
            'settled_qualifier_pnl': sum(r['realized'] for r in actions if r['a'] == 'qualifier_settle'),
            'fills': fills}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--since', required=True, help='UTC ISO timestamp')
    parser.add_argument('--directory', default=LIVE)
    parser.add_argument('--score-directory')
    parser.add_argument('--output')
    args = parser.parse_args()
    since = datetime.fromisoformat(args.since.replace('Z', '+00:00')).timestamp()
    result = summarize(args.directory, since, args.score_directory)
    payload = json.dumps(result, indent=2)
    if args.output:
        with open(args.output, 'w') as f:
            f.write(payload + '\n')
    print(payload)
