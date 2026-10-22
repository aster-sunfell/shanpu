"""Completed timeouts must not stretch plots or hide real recovery/evidence gaps."""
import csv
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'lib'))
from report_charts import comparison_timeline
from report_data import completed_activity_end


class TimelineWindowTest(unittest.TestCase):
    def fixture(self, root, path='B', repeat=1, commit_second=59.9, finish_second=59.5):
        directory = root/f'{path}-{repeat}'
        directory.mkdir()
        origin = 1000
        with sqlite3.connect(directory/'evidence.sqlite') as db:
            db.executescript('''CREATE TABLE starts(rid TEXT,uid INTEGER,t REAL,e REAL,kind TEXT);
                CREATE TABLE finishes(rid TEXT,f REAL);
                CREATE TABLE after_orders(oid TEXT,uid INTEGER); CREATE TABLE before_orders(oid TEXT);
                CREATE TABLE commits(oid TEXT,uid INTEGER,t REAL);
                INSERT INTO starts VALUES('recovery:0',7,60000,61000,'eligible');
                INSERT INTO after_orders VALUES('900',7);''')
            db.execute("INSERT INTO finishes VALUES('recovery:0',?)", (origin+finish_second*1000,))
            db.execute("INSERT INTO commits VALUES('900',7,?)", (origin+commit_second*1000,))
        def write_csv(name, fields, rows):
            with (directory/name).open('w') as stream:
                writer=csv.DictWriter(stream, fieldnames=fields)
                writer.writeheader(); writer.writerows(rows)
        write_csv('timeline.csv', ('second','started','accepted','completed','pendingAccepted','outstanding'), [
            {'second':s, 'started':int(s==59), 'accepted':0, 'completed':int(s==int(commit_second)),
             'pendingAccepted':0, 'outstanding':int(59<=s<int(commit_second))} for s in range(181)])
        write_csv('database-timeline.csv', ('second','transactionStarts','transactionEnds','transactionActivePeak'), [
            {'second':s, 'transactionStarts':int(s==59), 'transactionEnds':int(s==int(commit_second)),
             'transactionActivePeak':int(59<=s<=int(commit_second))} for s in range(181)])
        (directory/'pressure-samples.json').write_text(json.dumps([
            {'second':s, 'poolActive':int(59<=s<commit_second), 'poolWaitThreads':0,
             'kafkaLag':int(path=='C' and 59<=s<commit_second)} for s in range(0,181,2)]))
        row={'path':path, 'repeat':repeat, 'valid':True, 'pressure':{'valid':True},
             'started':1, 'eligible':1, 'completed':1, 'originMs':origin, 'cutoffMs':181000,
             'baseSeconds':20, 'seconds':10, 'recoverySeconds':30,
             'ambiguousResponseCount':1, 'outcomes':{'timeout':1}, 'committedAfterResponseFailure':1,
             'orderConsumption':{'maxTransactionsPerSecond':400, 'scope':'backend-instance','mode':'smooth-no-catch-up'}}
        return directory,row

    def test_all_rounds_and_reference_lines_share_shortened_axis_without_modifying_evidence(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            points=[self.fixture(root,path,repeat) for repeat in (1,2,3) for path in ('B','C')]
            before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}
            html=comparison_timeline(points, ('B','C'))
            self.assertIn('省略已确认完成后的末尾空闲观察段', html)
            self.assertEqual(18, html.count('>70s</text>'))
            self.assertNotIn('>180s</text>', html)
            references=0
            for graphic in re.findall(r'<svg viewBox="0 0 600 275".*?</svg>', html):
                for line in ET.fromstring(graphic).iter('polyline'):
                    positions=[float(p.split(',')[0]) for p in line.attrib['points'].split()]
                    self.assertTrue(all(58<=x<=552 for x in positions))
                    if line.attrib['stroke']=='#596678':
                        references+=1
                        self.assertEqual([58,548], positions)
            self.assertEqual(3, references)
            self.assertEqual(before, {p:hashlib.sha256(p.read_bytes()).hexdigest() for p in before})
            self.assertTrue(all(row['cutoffMs']==181000 and row['outcomes']=={'timeout':1} for _,row in points))

    def test_late_commit_or_http_finish_keeps_the_actual_recovery_window(self):
        for kwargs, end in (({'commit_second':95.25},110), ({'finish_second':120.25},130)):
            with self.subTest(kwargs=kwargs), tempfile.TemporaryDirectory() as temp:
                root=Path(temp)
                points=[self.fixture(root), self.fixture(root,'C', **kwargs)]
                html=comparison_timeline(points, ('B','C'))
                self.assertEqual(6, html.count(f'>{end}s</text>'))
                self.assertNotIn('>180s</text>', html)

    def test_incomplete_invalid_or_missing_proof_keeps_full_observation_window(self):
        for change in ('incomplete','invalid','pressure','commit','finish','database','samples','lag','timeline-gap','db-gap'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                root=Path(temp);directory,row=self.fixture(root,'C')
                if change=='incomplete': row['completed']=0
                if change=='invalid': row['valid']=False
                if change=='pressure': row['pressure']['valid']=False
                if change in ('commit','finish'):
                    with sqlite3.connect(directory/'evidence.sqlite') as db:
                        db.execute('DELETE FROM '+('commits' if change=='commit' else 'finishes'))
                if change=='database': (directory/'evidence.sqlite').unlink()
                if change=='samples': (directory/'pressure-samples.json').unlink()
                if change in ('timeline-gap','db-gap'):
                    file=directory/('timeline.csv' if change=='timeline-gap' else 'database-timeline.csv')
                    lines=file.read_text().splitlines();del lines[76]
                    file.write_text('\n'.join(lines)+'\n')
                if change=='lag':
                    samples=json.loads((directory/'pressure-samples.json').read_text())
                    for sample in samples: sample['kafkaLag']=None
                    (directory/'pressure-samples.json').write_text(json.dumps(samples))
                html=comparison_timeline([(directory,row)], ('C',))
                self.assertEqual(3, html.count('>180s</text>'))
                self.assertNotIn('省略已确认完成后的末尾空闲观察段', html)

    def test_late_sampled_pressure_is_preserved_and_missing_zero_is_not_assumed(self):
        for remains_busy, end in ((False,110),(True,180)):
            with self.subTest(remains_busy=remains_busy), tempfile.TemporaryDirectory() as temp:
                root=Path(temp);directory,row=self.fixture(root,'C')
                samples=json.loads((directory/'pressure-samples.json').read_text())
                for sample in samples:
                    if sample['second']==100 or (remains_busy and sample['second']>100):
                        sample['poolWaitThreads']=2;sample['kafkaLag']=10
                (directory/'pressure-samples.json').write_text(json.dumps(samples))
                html=comparison_timeline([(directory,row)], ('C',))
                self.assertEqual(3, html.count(f'>{end}s</text>'))

    def test_legacy_schema_or_foreign_order_cannot_prove_completion(self):
        for change in ('schema','owner','before','cutoff'):
            with self.subTest(change=change), tempfile.TemporaryDirectory() as temp:
                directory,row=self.fixture(Path(temp))
                with sqlite3.connect(directory/'evidence.sqlite') as db:
                    if change=='schema': db.execute('DROP TABLE finishes')
                    if change=='owner': db.execute('UPDATE commits SET uid=8')
                    if change=='before': db.execute("INSERT INTO before_orders VALUES('900')")
                if change=='cutoff': row['cutoffMs']=60000
                self.assertIsNone(completed_activity_end(directory,row))


if __name__=='__main__': unittest.main()
