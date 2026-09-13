"""Extract sequence-validated match quotes from a prespecified historical cohort.

Reads local gzip or streams immutable S3 objects. Never sends exchange orders.
Each day is independently seeded only by snapshots (conservative at midnight).
"""
import argparse,gzip,json,sys,time,subprocess
from pathlib import Path
try: import orjson
except ImportError: orjson=None
from kalshi import Book
loads=orjson.loads if orjson else json.loads

def extract(source,output):
    process=None
    fingerprint=None
    if not source.startswith('s3://'):
        stat=Path(source).stat();fingerprint=[stat.st_size,stat.st_mtime_ns]
    if source.startswith('s3://'):
        process=subprocess.Popen(['aws','s3','cp',source,'-','--only-show-errors'],stdout=subprocess.PIPE)
        raw=process.stdout
    else:raw=open(source,'rb')
    books={};tops={};previous={};connection=None;last={};last_t=None
    count=resets=0; tickers=set();begin=None;end=None
    from datetime import datetime,timezone
    tail={'allowed':datetime.now(timezone.utc).strftime('%Y%m%d') in source,'truncated':False}
    def lines(inp):
        while True:
            try:line=inp.readline()
            except EOFError:
                if not tail['allowed']:raise
                tail['truncated']=True;return
            if not line:return
            yield line
    with gzip.GzipFile(fileobj=raw) as inp,gzip.open(output,'wt',compresslevel=1) as out:
        for line in lines(inp):
            v=loads(line)
            if v.get('type') not in ('orderbook_snapshot','orderbook_delta'):continue
            count+=1;now=v['t'];sid=v.get('sid');seq=v.get('seq');conn=v.get('connection')
            if begin is None:begin=now
            if (conn!=connection or (sid in previous and seq!=previous[sid]+1)
                    or (last_t is not None and now-last_t>30)):
                books.clear();tops.clear();last.clear();previous.clear();resets+=1
                out.write(json.dumps({'t':min(now,last_t+30) if last_t is not None else now,'reset':True})+'\n')
            connection=conn;previous[sid]=seq;last_t=now;end=now
            m=v['msg'];tk=m.get('market_ticker','')
            if not tk.startswith(('KXATPMATCH-','KXWTAMATCH-')):continue
            if v['type']=='orderbook_snapshot':
                books[tk]=Book();books[tk].snapshot(m,now)
                tops[tk]=[max((p for p,q in getattr(books[tk],side).items() if q>1e-9),default=None) for side in ('yes','no')]
            elif tk in books:
                b=books[tk];b.delta(m,now);side=m['side'];i=0 if side=='yes' else 1
                price=round(float(m['price_dollars']),4);ladder=getattr(b,side)
                top=tops[tk][i]
                if ladder.get(price,0)>1e-9 and (top is None or price>top):tops[tk][i]=price
                elif top==price and ladder.get(price,0)<=1e-9:tops[tk][i]=max(ladder,default=None)
            else:continue
            bid,no=tops[tk];ask=round(1-no,4) if no is not None else None
            state=(bid,ask)
            if state!=last.get(tk):
                out.write(json.dumps({'t':now,'tk':tk,'bid':bid,'ask':ask},separators=(',',':'))+'\n')
                last[tk]=state;tickers.add(tk)
    raw.close()
    if process and process.wait()!=0:raise RuntimeError('S3 read failed')
    meta={'validity_version':2,'open_tail':tail['truncated'],'source_fingerprint':fingerprint,'source':source,'messages':count,'resets':resets,'start':begin,'end':end,'tickers':sorted(tickers)}
    Path(output+'.meta.json').write_text(json.dumps(meta,indent=2))
    return meta

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('output');a=p.parse_args()
    import fcntl
    with open(a.output+'.lock','a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        meta=Path(a.output+'.meta.json')
        saved=json.loads(meta.read_text()) if meta.exists() else {}
        fresh=True
        if saved.get('open_tail') and not a.source.startswith('s3://'):
            stat=Path(a.source).stat();fresh=saved.get('source_fingerprint')==[stat.st_size,stat.st_mtime_ns]
        if saved.get('validity_version')==2 and fresh:
            m=saved
        else:
            m=extract(a.source,a.output)
        print(json.dumps({k:v for k,v in m.items() if k!='tickers'}),flush=True)
