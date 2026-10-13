import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from voicework.store import Store
from voicework.discovery_jobs import DiscoveryJobs

class DiscoveryJobTests(unittest.TestCase):
    def test_missing_context_gets_provisional_suggestions_and_explicit_context_survives(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(); source = root / 'writing'; source.mkdir()
            (source / 'family.txt').write_text('Hey Mom, thanks for the birthday invitation.')
            (source / 'mystery.txt').write_text('A few unrelated words.')
            (source / 'selected.json').write_text(json.dumps({'items': [{
                'title': 'Explicit context', 'text': 'A birthday note mentioning Mom.',
                'context': {'channel': 'public', 'audience': 'Subscribers'}
            }]}))
            store = Store(root / 'data' / 'test.sqlite3'); jobs = DiscoveryJobs(store)
            result = jobs.step(jobs.create([str(source)])['id'])
            self.assertEqual(result['added'], 3)
            samples = {sample['title']: sample for sample in store.samples()}
            self.assertEqual(samples['family']['context']['channel'], 'personal')
            self.assertEqual(samples['mystery']['context']['channel'], 'unknown')
            self.assertEqual(samples['Explicit context']['context']['channel'], 'public')
            self.assertEqual(samples['Explicit context']['context']['audience'], 'Subscribers')
            self.assertTrue(all(sample['status'] == 'pending' for sample in samples.values()))

    def test_directory_expansion_is_checkpointed_and_resumes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(); source = root / 'writing'; source.mkdir()
            for n in range(7):
                (source / ('%s.txt' % n)).write_text('Distinct synthetic paragraph %s.' % n)
            store = Store(root / 'data' / 'test.sqlite3'); jobs = DiscoveryJobs(store)
            job = jobs.create([str(source)])
            with patch('voicework.discovery_jobs.MAX_BATCH_ENTRIES', 2):
                first = jobs.step(job['id'])
                self.assertEqual(first['status'], 'paused')
                self.assertEqual(first['remaining'], 3)  # directory cursor plus two children
                self.assertEqual(first['scanned'], 0)
                jobs = DiscoveryJobs(Store(store.path))
                result = first
                for _ in range(10):
                    result = jobs.step(job['id'])
                    if result['status'] == 'completed':
                        break
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['added'], 7)
            self.assertEqual(result['duplicates'], 0)
            self.assertEqual(len(store.samples()), 7)

    def test_changed_directory_and_file_are_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(); source = root / 'writing'; source.mkdir()
            document = source / 'a.txt'; document.write_text('Original candidate.')
            (source / 'b.txt').write_text('Unchanged candidate.')
            store = Store(root / 'data' / 'test.sqlite3'); jobs = DiscoveryJobs(store)
            job = jobs.create([str(source)])
            with patch('voicework.discovery_jobs.MAX_BATCH_ENTRIES', 1):
                jobs.step(job['id'])
            # Change both a queued document and the paused directory listing.
            document.write_text('A newly changed candidate with different length.')
            (source / 'c.txt').write_text('Added after the snapshot.')
            result = jobs.step(job['id'])
            self.assertEqual(result['status'], 'completed')
            self.assertGreaterEqual(result['skipped'], 1)
            self.assertTrue(any('changed' in warning for warning in result['warnings']))
            self.assertFalse(any(s['text'] == 'Added after the snapshot.' for s in store.samples()))
            self.assertFalse(any(s['text'].startswith('A newly changed') for s in store.samples()))

    def test_failed_document_does_not_abort_other_queued_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(); source = root / 'writing'; source.mkdir()
            (source / 'a.docx').write_bytes(b'Invalid document, private source contents')
            (source / 'b.txt').write_text('A valid independent candidate.')
            store = Store(root / 'data' / 'test.sqlite3'); jobs = DiscoveryJobs(store)
            result = jobs.step(jobs.create([str(source)])['id'])
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['scanned'], 2)
            self.assertEqual(result['added'], 1)
            self.assertEqual(result['skipped'], 1)
            self.assertIn('extraction failed', ' '.join(result['warnings']))
            self.assertNotIn('private source contents', ' '.join(result['warnings']))
            self.assertEqual(store.samples()[0]['status'], 'pending')

    def test_batches_resume_and_finish_without_skipping_or_reimporting(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve(); source=root/'writing'; source.mkdir()
            for n in range(5): (source/('%s.txt' % n)).write_text('Synthetic unique writing %s.' % n)
            store=Store(root/'data'/'test.sqlite3'); jobs=DiscoveryJobs(store)
            job=jobs.create([str(source)])
            result=jobs.step(job['id'],2)
            self.assertEqual(result['added'],2); self.assertGreater(result['remaining'],0)
            # Reconstruct the service to prove restart persistence.
            jobs=DiscoveryJobs(Store(store.path))
            while result['status'] != 'completed': result=jobs.step(job['id'],2)
            self.assertEqual(result['added'],5)
            self.assertEqual(len(store.samples()),5)
            self.assertTrue(all(x['status']=='pending' for x in store.samples()))
            self.assertEqual(jobs.step(job['id'],2)['added'],5)

    def test_exclusion_and_cancel(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve(); source=root/'writing'; source.mkdir()
            (source/'secret-token.txt').write_text('Secret should stay out')
            (source/'normal.txt').write_text('A candidate worth reviewing.')
            (source/'escape.txt').symlink_to(root/'outside.txt')
            (root/'outside.txt').write_text('Excluded outside root')
            store=Store(root/'data'/'test.sqlite3'); jobs=DiscoveryJobs(store)
            job=jobs.create([str(source)])
            result=jobs.step(job['id']); self.assertEqual(result['added'],1)
            self.assertGreaterEqual(result['skipped'],2)
            second=jobs.create([str(source)]); jobs.cancel(second['id'])
            self.assertEqual(jobs.step(second['id'])['status'],'cancelled')
            with self.assertRaises(ValueError): jobs.create([str(root/'data')])
