"""Counterfactual revival triggers, using research_revival's validated tape.

This is research only: no execution, no modification of positions or services.
An IOC is priced after a latency delay using full displayed depth; passive limit
fills are deliberately not inferred. Unknown books and partial depth stay unknown.
"""
import argparse
import bisect
import collections
import glob
import json
import math
import re
from pathlib import Path
from research_revival import quotes, sweep
from iolib import open_capture


def histories(path):
    source_stat = Path(path).stat()
    fingerprint = [source_stat.st_size, source_stat.st_mtime_ns]
    cache = Path(path + '.quotes.json')
    if cache.exists():
        saved = json.loads(cache.read_text())
        if saved['fingerprint'] == fingerprint:
            return saved['histories'], saved['resets'], saved['end']
    result = collections.defaultdict(list)
    epoch = 0
    resets = []
    end = 0
    with open_capture(path) as source:
        for line in source:
            if 'MATCH-' not in line and '"reset"' not in line:
                continue
            r = json.loads(line); end = r['t']
            if r.get('reset'):
                epoch += 1; resets.append(end); continue
            if not r['tk'].startswith(('KXATPMATCH-', 'KXWTAMATCH-')):
                continue
            bid, ask = quotes(r)
            state = (bid, ask, epoch)
            h = result[r['tk']]
            if not h or state != h[-1][1:]:
                h.append((end, *state))
    cache.write_text(json.dumps({'fingerprint':fingerprint,'histories':result,'resets':resets,'end':end}))
    return result, resets, end


def opposite_view(history):
    """NO-side quotes of the SAME market; do not substitute the opponent ticker."""
    return [(t, round(1-a,4) if a is not None else None,
             round(1-b,4) if b is not None else None, epoch) for t,b,a,epoch in history]


def trigger(h, start, end, threshold, persistence, resets):
    """First continuous bid >= threshold. Gaps reset persistence, never imply revival."""
    since = None
    previous_epoch = None
    for i, (t, bid, ask, epoch) in enumerate(h):
        next_t = h[i+1][0] if i+1 < len(h) else end
        if next_t <= start or t > end:
            continue
        lo, hi = max(start, t), min(next_t, end)
        j = bisect.bisect_right(resets, t)
        if j < len(resets):
            hi = min(hi, resets[j])
        if hi < lo:
            since = None; continue
        if epoch != previous_epoch:
            since = None
        previous_epoch = epoch
        if bid is None or bid < threshold or (ask is not None and bid >= ask):
            since = None; continue
        if since is None:
            since = lo
        if since + persistence < hi or (persistence == 0 and lo < hi):
            return since + persistence
        # A reset before the next quote ends this continuous interval.
        if hi < min(next_t, end):
            since = None
    return None


def coverage_seconds(h, start, end, resets):
    total = 0
    for i, (t, bid, ask, epoch) in enumerate(h):
        hi = min(h[i+1][0] if i+1 < len(h) else end, end)
        j = bisect.bisect_right(resets, t)
        if j < len(resets):
            hi = min(hi, resets[j])
        total += max(0, hi-max(start,t))
    return total


def episodes(h, resets, end):
    output = []
    active = None
    for i, (t, bid, ask, epoch) in enumerate(h):
        if active and epoch != active['epoch']:
            j = bisect.bisect_right(resets, active['start'])
            output.append(dict(active, end=resets[j] if j < len(resets) else t, reason='gap'))
            active = None
        one = bid is None and ask is not None and ask <= .01
        if one and active is None:
            active = {'start': t, 'epoch': epoch}
        elif not one and active:
            output.append(dict(active, end=t, reason='bid-returned' if bid is not None else 'empty-or-ask-change'))
            active = None
    if active:
        j = bisect.bisect_right(resets, active['start'])
        output.append(dict(active, end=resets[j] if j < len(resets) else end,
                           reason='gap' if j < len(resets) else 'capture-end'))
    return output


def price_requests(path, requests):
    """As-of lookup; a reset discards depth. Use latest quote BEFORE arrival."""
    requests = sorted(requests, key=lambda x: x['arrival'])
    idx = 0; books = {}
    metadata = Path(path + '.meta.json')
    windows = json.loads(metadata.read_text()).get('depth_windows', []) if metadata.exists() else []
    def complete(req):
        if windows and not any(lo <= req['arrival'] <= hi for lo, hi in windows):
            req['hedge'] = req['hedge_other'] = req['exit'] = {'unknown': True, 'reason': 'outside depth window'}
            return
        for kind in ('hedge', 'hedge_other', 'exit'):
            if kind == 'hedge_other':
                if req.get('hedge_other_tk'):
                    b = books.get(req['hedge_other_tk'])
                elif req.get('hedge_tk'):
                    pair = req['hedge_tk'].rsplit('-', 1)[0]
                    candidates = [tk for tk in books if tk.rsplit('-', 1)[0] == pair and tk != req['hedge_tk']]
                    b = books[candidates[0]] if len(candidates) == 1 else None
                else:
                    b = None
            else:
                b = books.get(req[kind+'_tk'])
            if isinstance(b, str):
                b = json.loads(b)
            if b is None or b.get('depth_complete') is False:
                req[kind] = {'unknown': True}; continue
            # Hedge buys YES in the allegedly losing player's match.
            # Exit NO sells NO into NO bids; exit YES sells YES into YES bids.
            levels = b['yes'] if kind == 'hedge_other' else b['no'] if kind == 'hedge' or req['position_side'] == 'no' else b['yes']
            trade = sweep(levels, req['quantity'], buy_yes=kind.startswith('hedge'))
            fee_total = 0; remain = req['quantity']
            for p, size in levels:
                q = min(remain, size)
                fee_total += .07*p*(1-p)*q
                remain -= q
                if remain <= 0:
                    break
            trade['fee'] = math.ceil((fee_total-1e-12)*100)/100
            if kind.startswith('hedge'):
                trade['at_most_3c'] = sweep(levels, req['quantity'], buy_yes=True, max_buy_price=.03)
            req[kind] = trade
    exits = {req['exit_tk'] for req in requests}
    pairs = {req[key].rsplit('-',1)[0] for req in requests for key in ('hedge_tk','hedge_other_tk') if req.get(key)}
    # Each tape row already contains a reconstructed state. Retain its raw line
    # and decode depth only at execution timestamps, not on millions of updates.
    header = re.compile(r'^\{"t":([0-9.eE+\-]+),"tk":"([^"]+)"')
    with open_capture(path) as source:
        for line in source:
            match = header.match(line)
            if match:
                now, tk, reset = float(match[1]), match[2], False
            else:
                r = json.loads(line)
                now, tk, reset = r['t'], r.get('tk'), r.get('reset', False)
            while idx < len(requests) and requests[idx]['arrival'] < now:
                complete(requests[idx]); idx += 1
            if idx == len(requests):
                break
            if reset:
                books.clear()
            elif tk in exits or tk.rsplit('-',1)[0] in pairs:
                books[tk] = line
    # Requests beyond capture are unknown, not extrapolated executable prices.
    for req in requests[idx:]:
        req['hedge'] = req['hedge_other'] = req['exit'] = {'unknown': True}
    return requests


def run(path, zpath, output):
    h, resets, end = histories(path)
    excluded = set(json.load(open('data/live/paper_action_exclusions.json'))['run_ids'])
    from score_context import confirmed_winner
    final_scores = {}
    outcomes = {}
    for score_path in sorted(glob.glob('data/live/tennis_scores_*.jsonl')):
        for line in open(score_path):
            score = json.loads(line)
            if confirmed_winner(score.get('score', {}), score.get('best_of')):
                final_scores.setdefault(score['event'], score['received_at'])
                winner = confirmed_winner(score['score'], score.get('best_of'))
                for tk, player in score.get('players', {}).items():
                    if player.get('id') in (score['score'].get('competitor1_id'), score['score'].get('competitor2_id')):
                        outcomes[tk] = 'won' if player['id'] == winner else 'lost'
    entries = []
    actions = []
    for p in sorted(glob.glob('data/live/winner_taker_actions_paper_2026090[78].jsonl')):
        for line in open(p):
            r = json.loads(line)
            if r.get('run_id') in excluded or r.get('a') not in ('take', 'qualifier_take') or r.get('signal') != 'book-inferred':
                continue
            actions.append(dict(r, source='paper'))
    ledger = json.load(open('data/pilot_live/pilot_ledger.json'))
    for order in ledger['orders'].values():
        if order['status'] == 'filled':
            actions.append({'a': 'qualifier_take' if order['side'] == 'bid' else 'take',
                            'tk': order['ticker'], 'match_tk': order['match_ticker'],
                            't': order['resolved_at'], 'count': float(order['filled']), 'source': 'live'})
    for r in actions:
        match = r['match_tk']
        signal_match = match
        event = signal_match.rsplit('-',1)[0]
        view = h.get(signal_match, [])
        if r['a'] == 'qualifier_take':
            candidates = [tk for tk in h if tk.rsplit('-', 1)[0] == match.rsplit('-', 1)[0] and tk != match]
            match = candidates[0] if len(candidates) == 1 else None
            view = opposite_view(view)
        if not view or view[0][0] > r['t']:
            entries.append({'market': r['tk'], 'coverage': 'entry predates valid snapshot'}); continue
        entry = {'source': r['source'], 'market': r['tk'], 'signal_match': signal_match, 'match': match, 't': r['t'], 'quantity': r['count'],
                 'position_side': 'yes' if r['a'] == 'qualifier_take' else 'no', 'triggers': {},
                 'hedge_other_tk': signal_match if r['a'] == 'qualifier_take' else None,
                 'inference_correct': outcomes.get(signal_match) == ('won' if r['a'] == 'qualifier_take' else 'lost'),
                 'final_score_received': final_scores.get(event), 'match_outcome': outcomes.get(match),
                 'coverage_seconds': coverage_seconds(view,r['t'],min(r['t']+3600,end),resets),
                 'pre_final_coverage_seconds': coverage_seconds(view,r['t'],min(r['t']+3600,end,final_scores.get(event,end)),resets),
                 'max_revival_bid': max((bid for t,bid,ask,epoch in view if r['t'] <= t <= min(r['t']+3600,final_scores.get(event,end)) and bid is not None and (ask is None or bid < ask)), default=None)}
        for threshold in (.01, .02, .03, .05, .10, .15):
            for persistence in (0, 1, 3, 5, 10, 30):
                t = trigger(view, r['t'], min(r['t']+3600, end, final_scores.get(event,end)), threshold, persistence, resets)
                entry['triggers'][f'{threshold}/{persistence}'] = t
        entries.append(entry)
    requests = []
    for e in entries:
        if 'triggers' not in e: continue
        for rule, t in e['triggers'].items():
            if t is not None:
                requests.append(dict(rule=rule, arrival=t+.1, hedge_tk=e['match'], hedge_other_tk=e['hedge_other_tk'], exit_tk=e['market'],
                                     quantity=e['quantity'], position_side=e['position_side'], entry=e['t']))
    priced = price_requests(path, requests) if requests else []
    result = {'depth_metadata': {p:json.loads(Path(p+'.meta.json').read_text()) for p in (path,zpath)}, 'capture_end':end, 'reset_count':len(resets), 'window_seconds': 3600, 'latency_seconds': .1, 'entries': entries, 'true_entry_executions': priced}
    tia = 'KXATPMATCH-26SEP08TIAMIC-TIA'
    result['tiafoe'] = {'minimum_two_sided_mid': min((round((b+a)/2, 4) for t,b,a,e in h[tia] if b is not None and a is not None and b < a), default=None),
                        'one_cent_episodes': episodes(h[tia], resets, end)}
    zh, zr, ze = histories(zpath)
    result['september5'] = {}
    zreq = []
    for tk, history in zh.items():
        eps = episodes(history, zr, ze)
        for ep in eps:
            ep['revivals'] = {}
            for threshold in (.01,.02,.03,.05,.10,.15):
                for persistence in (0,1,3,5,10,30):
                    t = trigger(history, ep['start'], min(ep['start']+3600, ze), threshold,persistence,zr)
                    ep['revivals'][f'{threshold}/{persistence}'] = t
                    if t is not None and tk.endswith('-ZHE'):
                        for latency in (.1,.5):
                            zreq.append(dict(rule=f'{threshold}/{persistence}', episode=ep['start'], arrival=t+latency,
                                             latency=latency,hedge_tk=tk,exit_tk='KXWTA-26USO-ZHE', quantity=100,position_side='no'))
        result['september5'][tk] = eps
    result['zheng_hypothetical_100_contracts'] = price_requests(zpath,zreq)
    with open(output,'w') as f:
        json.dump(result,f,indent=2)
    print(json.dumps({'entries':len(entries),'covered':sum('triggers'in e for e in entries),
                      'triggered_entries':sum(any(t is not None for t in e.get('triggers',{}).values()) for e in entries),
                      'tiafoe':result['tiafoe'], 'output':output},indent=2))

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tape', default='data/research/revival_sep0708.jsonl.gz')
    parser.add_argument('--zheng-tape', default='data/research/revival_sep05.jsonl.gz')
    parser.add_argument('--output', default='data/research/revival_analysis.json')
    args = parser.parse_args()
    run(args.tape, args.zheng_tape, args.output)
