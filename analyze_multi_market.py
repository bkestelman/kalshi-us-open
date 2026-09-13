"""Read-only comparison of durable pilot fills and independent paper opportunities."""
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parent


def analyze():
    result = {}
    for mode in ('live', 'paper'):
        ledger = json.loads((ROOT/f'data/pilot_{mode}/pilot_ledger.json').read_text())
        orders = list(ledger['orders'].values())
        terminal = [r for r in orders if r['status'] in ('filled', 'no_fill')]
        filled = [r for r in orders if r['status'] == 'filled']
        groups = Counter(r['match'] for r in filled)
        result[mode] = {
            'statuses': dict(Counter(r['status'] for r in orders)),
            'fully_filled': sum(float(r['filled']) == r['count'] for r in filled),
            'terminal_fill_ratio_by_contract': sum(float(r['filled']) for r in terminal)/sum(r['count'] for r in terminal),
            'terminal_latency_ms_median': median(1000*(r['resolved_at']-r['created_at']) for r in terminal),
            'multi_market_filled_matches': {k:v for k,v in groups.items() if v>1},
            'allocated': sum(float(r['reserved']) for r in orders if r['status'] not in ('no_fill','not_found')),
            'orders': [{k:r.get(k) for k in ('created_at','resolved_at','match','ticker','side','count','filled','reserved','status')} for r in orders],
        }
    start = min(r['created_at'] for r in result['live']['orders'])
    signals, fills = [], []
    for path in sorted((ROOT/'data/live').glob('winner_taker_actions_paper_*.jsonl')):
        for line in path.read_text().splitlines():
            r = json.loads(line)
            if r.get('t',0)<start:
                continue
            if r.get('a') == 'signal' and r.get('signal') == 'one-cent':
                signals.append(r)
            if r.get('a') in ('take','qualifier_take') and r.get('signal')=='book-inferred':
                fills.append({k:r.get(k) for k in ('tk','match_tk','count','price','decision_at','verification')})
    result['paper_one_cent_snapshots'] = [{
        'at': datetime.fromtimestamp(r['t'],timezone.utc).isoformat(),
        'match_ticker': r['match_tk'],
        'legs': r['legs'],
        'whole_contract_bid_markets': sum(0 < (x.get('bid') or 0) <= .15 and x.get('size',0)>=1 for x in r['legs']),
    } for r in signals]
    result['broad_paper_book_inferred_fills'] = fills
    return result


if __name__ == '__main__':
    print(json.dumps(analyze(), indent=2))
