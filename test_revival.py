import _test_environment  # Must precede modules importing iolib.
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from analyze_revival import episodes, opposite_view, price_requests, trigger
from research_revival import extract, quotes, sweep


class RevivalTest(unittest.TestCase):
    def test_winner_revival_uses_same_books_no_bid(self):
        view = opposite_view([(0,.99,None,0),(2,.98,.99,0)])
        self.assertEqual(view,[(0,None,.01,0),(2,.01,.02,0)])
        self.assertEqual(trigger(view,0,10,.01,1,[]),3)

    def test_empty_book_does_not_revive(self):
        h = [(0,None,.01,0), (10,None,None,0), (20,.01,.02,0)]
        self.assertEqual(trigger(h,0,30,.01,3,[]),23)
        self.assertEqual(episodes(h,[],30)[0]['reason'],'empty-or-ask-change')

    def test_gap_cancels_persistence(self):
        h = [(0,.01,.02,0), (20,.01,.02,1)]
        self.assertEqual(trigger(h,0,30,.01,5,[3]),25)
        self.assertIsNone(trigger(h,0,19,.01,5,[3]))

    def test_crossed_quote_does_not_prove_revival(self):
        h = [(0,.1,.01,0),(5,.1,.11,0)]
        self.assertEqual(trigger(h,0,20,.1,3,[]),8)

    def test_bid_drop_cancels_persistence(self):
        h = [(0,None,.01,0), (2,.01,.02,0), (4,None,.01,0), (10,.02,.03,0)]
        self.assertEqual(trigger(h,0,20,.01,3,[]),13)

    def test_depth_and_bounded_price(self):
        levels = [[.98,2],[.96,10]]
        self.assertAlmostEqual(sweep(levels,5,True)['value'],.16)
        self.assertEqual(sweep(levels,5,True,.03)['quantity'],2)
        self.assertEqual(sweep(levels,20,True)['quantity'],12)
        self.assertEqual(quotes({'yes':[[.02,0]],'no':[[.99,2]]}),(None,.01))

    def test_sequence_gap_requires_new_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = str(Path(d)/'source.jsonl'), str(Path(d)/'tape.gz')
            rows = [
                {'type':'orderbook_snapshot','sid':1,'seq':1,'t':0,'msg':{'market_ticker':'MATCH','yes_dollars_fp':[],'no_dollars_fp':[['.99','5']]}},
                {'type':'orderbook_delta','sid':1,'seq':3,'t':1,'msg':{'market_ticker':'OTHER','side':'yes','price_dollars':'.01','delta_fp':'5'}},
                {'type':'orderbook_delta','sid':1,'seq':4,'t':2,'msg':{'market_ticker':'MATCH','side':'yes','price_dollars':'.01','delta_fp':'5'}},
            ]
            Path(src).write_text(''.join(json.dumps(r)+'\n' for r in rows))
            extract([src],dst,['MATCH'])
            with gzip.open(dst,'rt') as f: tape=[json.loads(l) for l in f]
            self.assertEqual(len(tape),2)
            self.assertTrue(tape[1]['reset'])

    def test_window_seeds_unchanged_related_depth(self):
        with tempfile.TemporaryDirectory() as d:
            src, dst = str(Path(d)/'source.jsonl'), str(Path(d)/'tape.gz')
            rows = []
            for seq, t, tk in [(1,0,'RELATED'),(2,2,'MATCH-X')]:
                rows.append({'type':'orderbook_snapshot','sid':1,'seq':seq,'t':t,
                             'msg':{'market_ticker':tk,'yes_dollars_fp':[['.9','10']],
                                    'no_dollars_fp':[['.09','10']]}})
            Path(src).write_text(''.join(json.dumps(r)+'\n' for r in rows))
            extract([src],dst,['RELATED','MATCH'],windows=[(1,5)],quotes_outside=True)
            with gzip.open(dst,'rt') as f: tape=[json.loads(l) for l in f]
            self.assertTrue(any(r.get('tk')=='RELATED' and r['t']==2 and r['depth_complete'] for r in tape))

    def test_arrival_book_not_future_or_pre_gap(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d)/'tape.gz')
            with gzip.open(path,'wt') as f:
                for row in [{'t':0,'tk':'X','yes':[[.9,5]],'no':[[.98,5]]},
                            {'t':2,'reset':True},
                            {'t':4,'tk':'X','yes':[[.1,5]],'no':[[.5,5]]}]:
                    f.write(json.dumps(row)+'\n')
            req=[dict(arrival=t,hedge_tk='X',exit_tk='X',quantity=5,position_side='yes') for t in (1,3,5)]
            out=price_requests(path,req)
            self.assertAlmostEqual(out[0]['exit']['value'],4.5)
            self.assertAlmostEqual(out[0]['hedge']['value'],.1)
            self.assertTrue(out[1]['exit']['unknown'])
            self.assertTrue(out[2]['exit']['unknown'])
            Path(path+'.meta.json').write_text(json.dumps({'depth_windows':[[2,4]]}))
            outside=price_requests(path,[dict(arrival=1,hedge_tk='X',exit_tk='X',quantity=5,position_side='yes')])
            self.assertTrue(outside[0]['exit']['unknown'])


if __name__ == '__main__':
    unittest.main()
