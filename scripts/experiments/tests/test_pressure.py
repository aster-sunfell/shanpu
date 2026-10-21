import copy
import csv
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'lib'))
from pressure import attach_pressure


class PressureTest(unittest.TestCase):
    def fixture(self):
        # The largest pressure occurs AFTER the burst, in recovery; it must count.
        rows=[{'epochSecond':s,'starts':n,'ends':n,'notCommitted':0,'activePeak':p,'activeEnd':0}
              for s,n,p in [(10,2,2),(20,5,3),(32,8,4)]]
        raw={'version':1,'activityId':12,'started':15,'ended':15,'active':0,'peakActive':4,'errors':0,'buckets':rows}
        result={'originMs':10000,'cutoffMs':40000,'completed':15,'backendDelta':{'syncAttempts':15,'asyncAttempts':0,'poolWaitMs':23,'poolWaitCount':4}}
        return raw,result

    def test_recovery_peak_counted_and_lag_sampling_missing_is_not_zero(self):
        raw,result=self.fixture()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            samples=[{'timeMs':11000,'backend':{'timeMs':11000,'poolActive':4,'poolWaitThreads':2},'kafkaLag':{'orders':9}},
                     {'timeMs':13000,'backend':{'timeMs':13000,'poolActive':2,'poolWaitThreads':0}}]
            (root/'samples.jsonl').write_text(''.join(json.dumps(s)+'\n' for s in samples))
            attach_pressure(result,{'activityId':12},{},{'transactionPressure':raw},root)
            self.assertTrue(result['pressure']['valid'])
            self.assertEqual(8,result['pressure']['peakStartRps'])
            self.assertEqual(4,result['pressure']['peakActive'])
            self.assertEqual(2,result['pressure']['sampledPoolWaitThreadsPeak'])
            self.assertEqual([None,9,None],[s['kafkaLag'] for s in json.loads((root/'pressure-samples.json').read_text())])
            rows=list(csv.DictReader((root/'database-timeline.csv').read_text().splitlines()))
            self.assertEqual('8',next(r for r in rows if float(r['second'])==22)['transactionStarts'])

    def test_missing_foreign_truncated_and_inconsistent_evidence_never_yields_peak(self):
        base,result=self.fixture()
        for changes in ({'activityId':99},{'errors':1},{'active':1},{'ended':14},{'started':16},{'buckets':[]},{'peakActive':999}):
            with self.subTest(changes=changes),tempfile.TemporaryDirectory() as temp:
                row=copy.deepcopy(result)
                attach_pressure(row,{'activityId':12},{},{'transactionPressure':{**base,**changes}},temp)
                self.assertFalse(row['pressure']['valid'])
                self.assertIsNone(row['pressure']['peakStartRps'])
                self.assertEqual(1,len((Path(temp)/'database-timeline.csv').read_text().splitlines()))

    def test_transaction_spanning_empty_seconds_retains_concurrency(self):
        raw,result=self.fixture()
        raw.update(started=1,ended=1,peakActive=1,buckets=[
            {'epochSecond':10,'starts':1,'ends':0,'notCommitted':0,'activePeak':1,'activeEnd':1},
            {'epochSecond':13,'starts':0,'ends':1,'notCommitted':1,'activePeak':1,'activeEnd':0}])
        result.update(completed=0,backendDelta={'syncAttempts':1})
        with tempfile.TemporaryDirectory() as temp:
            attach_pressure(result,{'activityId':12},{},{'transactionPressure':raw},temp)
            self.assertTrue(result['pressure']['valid'])
            self.assertEqual(1,result['pressure']['notCommitted'])
            rows=list(csv.DictReader((Path(temp)/'database-timeline.csv').read_text().splitlines()))
            self.assertEqual(['1','1','1','1'],[r['transactionActivePeak'] for r in rows[:4]])

if __name__=='__main__': unittest.main()
