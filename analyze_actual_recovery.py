"""Recovery sensitivity on actual paper/live fills, using validated quote tapes."""
import json,gzip,collections,bisect
from pathlib import Path
from analyze_revival import trigger,coverage_seconds,opposite_view,histories
from score_context import confirmed_winner
from analyze_recovery_cohort import GRID

def main():
    views={};endpoints={}
    # Original primary Sep7–8 replay includes complete actual fill windows.
    h,resets,end=histories('data/research/revival_sep0708.jsonl.gz')
    for tk,history in h.items():views[tk]=history;endpoints[tk]=(resets,end,'primary Sep7–8')
    # Older capture supplies Sep6/Pegula and subsequent day adds new actual fills.
    for path in [Path('data/research/cohort/ws_20260906.jsonl.gz'),Path('data/research/actual/primary_20260909.jsonl.gz')]:
        meta_path=Path(str(path)+'.meta.json')
        if not meta_path.exists():continue
        meta=json.loads(meta_path.read_text());new=collections.defaultdict(list);rr=[];epoch=0
        with gzip.open(path,'rt') as f:
            for line in f:
                r=json.loads(line)
                if r.get('reset'):rr.append(r['t']);epoch+=1
                else:new[r['tk']].append((r['t'],r['bid'],r['ask'],epoch))
        for tk,history in new.items():
            # Match dates are separate; preserve the primary when both exist.
            if tk not in views or 'primary_20260909' in str(path):
                views[tk]=history;endpoints[tk]=(rr,meta['end'],str(path))
    finals={};outcomes={}
    for path in sorted(Path('data/live').glob('tennis_scores_*.jsonl')):
        for line in open(path):
            r=json.loads(line);winner=confirmed_winner(r.get('score',{}),r.get('best_of'))
            if not winner:continue
            finals.setdefault(r['event'],r['received_at'])
            for tk,player in r.get('players',{}).items():
                if player.get('id'):outcomes[tk]='won' if player['id']==winner else 'lost'
    exclusions=set(json.loads(Path('data/live/paper_action_exclusions.json').read_text())['run_ids'])
    entries=[]
    for path in sorted(Path('data/live').glob('winner_taker_actions_paper_*.jsonl')):
        for line in open(path):
            r=json.loads(line)
            if r.get('run_id') in exclusions or r.get('a') not in ('take','qualifier_take') or r.get('signal')!='book-inferred':continue
            entries.append({'source':'broad_paper','ticker':r['tk'],'match_ticker':r['match_tk'],
                'side':'bid' if r['a']=='qualifier_take' else 'ask','t':r['t'],'quantity':r['count']})
    for source in ('pilot_live','pilot_paper'):
        ledger=json.loads(Path('data',source,'pilot_ledger.json').read_text())
        for row in ledger['orders'].values():
            if row['status']=='filled':
                entries.append({'source':source,'ticker':row['ticker'],'match_ticker':row['match_ticker'],
                    'side':row['side'],'t':row.get('resolved_at',row['created_at']),'quantity':float(row['filled'])})
    results=[]
    for e in entries:
        tk=e['match_ticker'];event=tk.rsplit('-',1)[0];row=dict(e,event=event)
        if tk not in views or event not in finals:
            row['coverage']='missing capture or final score';results.append(row);continue
        h=views[tk];resets,capture_end,source=endpoints[tk]
        stop=finals[event]
        if h[0][0]>e['t'] or stop>capture_end or stop<=e['t']:
            row['coverage']='entry/final outside captured interval';results.append(row);continue
        view=opposite_view(h) if e['side']=='bid' else h
        covered=coverage_seconds(view,e['t'],stop,resets)
        row.update(coverage_seconds=covered,window_seconds=stop-e['t'],capture=source,
            coverage='complete' if covered>=stop-e['t']-.001 else 'gaps',
            inference_correct=outcomes.get(tk)==('won' if e['side']=='bid' else 'lost'),final_score_received=stop,
            triggers={f'{th:.2f}/{sec}':trigger(view,e['t'],stop,th,sec,resets) for th,sec in GRID})
        results.append(row)
    summary={}
    for source in ('all','broad_paper','pilot_live','pilot_paper'):
        rows=[r for r in results if source=='all' or r['source']==source]
        good=[r for r in rows if r.get('inference_correct')]
        summary[source]={'entries':len(rows),'fully_covered_entries':sum(r['coverage']=='complete' for r in rows),
            'fully_covered_matches':len({r['event'] for r in rows if r['coverage']=='complete'}),'grid':{}}
        for th,sec in GRID:
            key=f'{th:.2f}/{sec}'
            summary[source]['grid'][key]=sorted({r['event'] for r in good if r.get('triggers',{}).get(key) is not None})
    report={'summary':summary,'entries':results}
    Path('data/research/actual_recovery_results.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(summary,indent=2))

if __name__=='__main__':main()
