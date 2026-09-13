"""Threshold sensitivity by distinct match; no outcome-selected entries.

Prespecified cohort: Aug6–19 development; Aug20–23 and Sep2–9 validation.
Known Zheng/Sep7–8 matches are reported separately, never called a holdout.
Strict one-sided episodes are hypothetical entry opportunities, not actual fills.
"""
import argparse,bisect,collections,gzip,json,time
from pathlib import Path
from analyze_revival import trigger,episodes,coverage_seconds,opposite_view
from kalshi import get

GRID=[(c/100,s) for c in (2,3,5,7,10) for s in (5,10,30,60)]

def analyze(directory,output):
    cache=Path(directory)/'market_results.json'
    results=json.loads(cache.read_text()) if cache.exists() else {}
    starts={}
    for line in open('/home/ubuntu/kalshi-tennis/data/live/match_meta.jsonl'):
        r=json.loads(line)
        from datetime import datetime
        t=datetime.fromisoformat(r['detected_start']).timestamp()
        for tk in r['tickers']: starts[tk]=min(t,starts.get(tk,t))
    finals={}
    from score_context import confirmed_winner
    for path in Path('data/live').glob('tennis_scores_*.jsonl'):
        for line in open(path):
            r=json.loads(line)
            if confirmed_winner(r.get('score',{}),r.get('best_of')):
                finals[r['event']]=min(r['received_at'],finals.get(r['event'],r['received_at']))
    allrows=[];unknown=[];days=[]
    for path in sorted(Path(directory).glob('ws_*.jsonl.gz')):
        meta_path=Path(str(path)+'.meta.json')
        if not meta_path.exists():continue
        meta=json.loads(meta_path.read_text());days.append(meta)
        histories=collections.defaultdict(list);resets=[];epoch=0
        with gzip.open(path,'rt') as f:
            for line in f:
                r=json.loads(line)
                if r.get('reset'):epoch+=1;resets.append(r['t'])
                else:histories[r['tk']].append((r['t'],r['bid'],r['ask'],epoch))
        for tk,h in histories.items():
            # One view per actual market. Deduplicate opposite listings by event
            # when reporting matches; keep them separate as execution evidence.
            for side,view in [('ask',h),('bid',opposite_view(h))]:
                es=episodes(view,resets,meta['end'])
                es=[e for e in es if e['start']>=starts.get(tk,float('inf'))]
                if not es:continue
                if results.get(tk,{}).get('result') not in ('yes','no'):
                    m=(get('/markets/'+tk) or {}).get('market',{})
                    results[tk]={k:m.get(k) for k in ('result','close_time','status','rules_primary','rules_secondary')}
                    cache.write_text(json.dumps(results,indent=2))
                result=results[tk].get('result')
                if result not in ('yes','no'):
                    unknown.append(tk);continue
                from datetime import datetime
                close=results[tk].get('close_time')
                close=datetime.fromisoformat(close.replace('Z','+00:00')).timestamp() if close else float('inf')
                event=tk.rsplit('-',1)[0]
                success=result==('yes' if side=='bid' else 'no')
                for e in es:
                    start=e['start']
                    end=min(start+3600,close,finals.get(event,float('inf')))
                    if end<=start:continue
                    coverage=coverage_seconds(view,start,end,resets)
                    covered=end<=meta['end'] and coverage>=end-start-.001
                    found={f'{threshold:.2f}/{seconds}':trigger(view,start,min(end,meta['end']),threshold,seconds,resets)
                           for threshold,seconds in GRID}
                    allrows.append({'day':path.name[3:11],'ticker':tk,'event':event,'side':side,
                        'start':start,'end':end,'success':success,'covered':covered,
                        'coverage_s':coverage,'triggers':found})
        print(path.name,'episodes so far',len(allrows),flush=True)
    summary={}
    for cohort in ('development','unseen_validation','known_recent'):
        rows=[r for r in allrows if ('development' if r['day']<'20260820' else 'known_recent'
             if r['day'] in ('20260905','20260907','20260908') else 'unseen_validation')==cohort]
        good={r['event'] for r in rows if r['success'] and r['covered']}
        bad={r['event'] for r in rows if not r['success'] and r['covered']}
        summary[cohort]={'successful_matches_observed':len({r['event'] for r in rows if r['success']}),
            'false_signal_matches_observed':len({r['event'] for r in rows if not r['success']}),
            'successful_matches_full_coverage':len(good),'false_signal_matches_full_coverage':len(bad),
            'incomplete_episodes':sum(not r['covered'] for r in rows),'grid':{}}
        for th,s in GRID:
            key=f'{th:.2f}/{s}'
            hitgood={r['event'] for r in rows if r['success'] and r['triggers'][key] is not None}
            hitbad={r['event'] for r in rows if not r['success'] and r['triggers'][key] is not None}
            summary[cohort]['grid'][key]={'successful_matches_interrupted':sorted(hitgood),
                'false_signal_matches_detected':sorted(hitbad),
                'successful_matches_interrupted_full_coverage':sorted({r['event'] for r in rows if r['success'] and r['covered'] and r['triggers'][key] is not None})}
    Path(output).write_text(json.dumps({'summary':summary,'episodes':allrows,'unknown_results':unknown,'sources':days},indent=2))
    print(json.dumps(summary,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('directory');p.add_argument('output');a=p.parse_args()
    analyze(a.directory,a.output)
