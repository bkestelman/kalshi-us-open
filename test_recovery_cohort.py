import _test_environment
import gzip,json,tempfile,unittest
from pathlib import Path
from research_recovery_cohort import extract

class CohortTests(unittest.TestCase):
    def extract(self,rows):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        source=str(Path(tmp.name)/'in.gz');out=str(Path(tmp.name)/'out.gz')
        with gzip.open(source,'wt') as f:
            for row in rows:f.write(json.dumps(row)+'\n')
        meta=extract(source,out)
        with gzip.open(out,'rt') as f:result=[json.loads(line) for line in f]
        return meta,result

    def snap(self,t=0,seq=1,tk='KXATPMATCH-E-A'):
        return {'type':'orderbook_snapshot','t':t,'sid':1,'seq':seq,
                'msg':{'market_ticker':tk,'yes_dollars_fp':[['.99','10']],'no_dollars_fp':[]}}
    def delta(self,t,seq,side='yes',p='.99',q='-10',tk='KXATPMATCH-E-A'):
        return {'type':'orderbook_delta','t':t,'sid':1,'seq':seq,
                'msg':{'market_ticker':tk,'side':side,'price_dollars':p,'delta_fp':q}}
    def test_top_removal_and_opposing_bid(self):
        m,r=self.extract([self.snap(),self.delta(1,2,p='.98',q='5'),self.delta(2,3),self.delta(3,4,side='no',p='.05',q='5')])
        self.assertEqual([(x['bid'],x['ask']) for x in r],[(.99,None),(.98,None),(.98,.95)])
    def test_unrelated_gap_invalidates_match_and_requires_snapshot(self):
        m,r=self.extract([self.snap(),self.delta(1,3,tk='UNRELATED'),self.delta(2,4),self.snap(3,5)])
        self.assertEqual(m['resets'],1);self.assertEqual(len(r),3);self.assertTrue(r[1]['reset'])
    def test_long_silence_invalidates_at_thirty_seconds_not_next_message(self):
        m,r=self.extract([self.snap(),self.delta(100,2),self.snap(101,3)])
        self.assertEqual(r[1],{'t':30,'reset':True})
        self.assertEqual(r[2]['t'],101)
    def test_initial_delta_is_not_a_seed(self):
        m,r=self.extract([self.delta(0,1,q='5'),self.snap(1,2)])
        self.assertEqual(len(r),1);self.assertEqual(r[0]['t'],1)

class TailTests(unittest.TestCase):
    def test_closed_day_truncation_fails(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'ws_20260806.jsonl.gz'
            path.write_bytes(gzip.compress((json.dumps(CohortTests().snap())+'\n').encode())[:-8])
            with self.assertRaises(EOFError):extract(str(path),str(Path(d)/'out.gz'))

    def test_open_current_day_tail_is_explicit_and_bounded(self):
        from datetime import datetime,timezone
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/('ws_'+datetime.now(timezone.utc).strftime('%Y%m%d')+'.jsonl.gz')
            path.write_bytes(gzip.compress((json.dumps(CohortTests().snap())+'\n').encode())[:-8])
            meta=extract(str(path),str(Path(d)/'out.gz'))
            self.assertTrue(meta['open_tail'])
            self.assertEqual(meta['messages'],1)
            self.assertEqual(meta['end'],0)
            self.assertEqual(meta['source_fingerprint'][0],path.stat().st_size)

if __name__=='__main__':unittest.main()
