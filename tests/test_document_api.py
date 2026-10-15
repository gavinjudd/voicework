import copy
import threading
import time
from unittest.mock import patch

import test_server
from voicework.harness import local_address


class DocumentApiTests(test_server.ServerTests):
    def wait_job(self, job):
        for _ in range(200):
            status, current, _ = self.request('GET', '/api/documents/' + job['id'])
            self.assertEqual(status, 200)
            if current['status'] in ('complete', 'failed'):
                return current
            time.sleep(.01)
        self.fail('Document job did not finish')

    def test_document_and_recheck_leave_training_untouched(self):
        before = self.request('GET', '/api/state')[1]
        source = '# Fresh investigation\n\nThe cache handled 17 requests. It did not reduce storage.\n\n```py\ncount = 17\n```\n'
        payload = {'source': source, 'context': {'channel': 'technical article', 'audience': 'technical readers'}, 'controls': {'register': 'formal'}}
        status, job, _ = self.request('POST', '/api/documents', payload)
        self.assertEqual(status, 202)
        result = self.wait_job(job)['result']
        self.assertEqual(result['text'], source)
        self.assertEqual(result['checks']['status'], 'review_required')
        self.assertIn('profile_fingerprint', result['context_report'])
        self.assertIn('counts', result['context_report'])
        payload['text'] = source.replace('17 requests', '71 requests')
        status, job, _ = self.request('POST', '/api/document-checks', payload)
        self.assertEqual(status, 202)
        result = self.wait_job(job)['result']
        self.assertTrue(result['checks']['content']['issues'])
        after = self.request('GET', '/api/state')[1]
        for key in ('samples', 'feedback', 'language_lessons', 'corrections', 'evaluations', 'quality_checks'):
            self.assertEqual(before[key], after[key])

    def test_document_validation_and_missing_job(self):
        for body in ({'source': ''}, {'source': 'x' * 120001}, {'source': 'test', 'controls': {'register': 'unknown'}}, {'source': 'test', 'unknown': True}):
            self.assertEqual(self.request('POST', '/api/documents', body)[0], 400)
        self.assertEqual(self.request('POST', '/api/document-checks', {'source': 'test'})[0], 400)
        self.assertEqual(self.request('GET', '/api/documents/' + '0' * 36)[0], 404)
        self.assertEqual(self.request('POST', '/api/documents', {'source': 'test'}, headers={'X-Voicework-Token': 'wrong'})[0], 403)

    def test_length_control_is_document_only_and_reaches_checks(self):
        source = '# Labels\n\n## Cover\n\nThe long versions\n'
        status, job, _ = self.request('POST', '/api/documents', {'source': source, 'context': {'channel': 'personal website'}, 'controls': {'length': 'preserve'}})
        self.assertEqual(status, 202)
        self.assertEqual(self.wait_job(job)['status'], 'complete')
        grown = source.replace('The long versions', 'The long versions, written out properly for anyone who wants more')
        status, job, _ = self.request('POST', '/api/document-checks', {'source': source, 'text': grown, 'context': {'channel': 'personal website'}, 'controls': {'length': 'preserve'}})
        self.assertEqual(status, 202)
        issues = self.wait_job(job)['result']['checks']['content']['issues']
        self.assertIn('section_lengthened', {issue['code'] for issue in issues})
        self.assertEqual(self.request('POST', '/api/documents', {'source': source, 'controls': {'length': 'shorter'}})[0], 400)
        self.assertEqual(self.request('POST', '/api/draft', {'context': {'channel': 'work'}, 'brief': 'Synthetic brief.', 'controls': {'length': 'preserve'}})[0], 400)

    def test_job_freezes_selected_inputs_and_bounds_concurrency(self):
        gate = threading.Event()
        entered = threading.Event()
        captured = []

        def generate(context, source, controls, evidence, preferences):
            captured.append(copy.deepcopy((context, source, controls, evidence, preferences)))
            entered.set()
            gate.wait(3)
            return {'text': source, 'provider': 'offline', 'warnings': [], 'evidence_ids': ['invented']}

        sample = self.server.store.add_sample({'text': 'A confirmed note.', 'context': {'channel': 'work'}})
        self.server.store.update_sample(sample['id'], {'status': 'confirmed'})
        with patch.object(self.provider, 'generate_document', side_effect=generate):
            status, first, _ = self.request('POST', '/api/documents', {'source': 'A new situation.', 'context': {'channel': 'work'}})
            self.assertEqual(status, 202)
            self.assertTrue(entered.wait(1))
            self.server.store.update_sample(sample['id'], {'held_out': True})
            status, second, _ = self.request('POST', '/api/documents', {'source': 'Another situation.'})
            self.assertEqual(status, 202)
            self.assertEqual(self.request('POST', '/api/documents', {'source': 'Too many.'})[0], 429)
            gate.set()
            result = self.wait_job(first)['result']
            self.assertEqual(self.wait_job(second)['status'], 'complete')
        self.assertEqual(captured[0][3][0]['text'], 'A confirmed note.')
        self.assertEqual(result['evidence_ids'], [sample['id']])

    def test_provider_failure_is_sanitized_without_partial_success(self):
        with patch.object(self.provider, 'generate_document', side_effect=ValueError('SECRET SOURCE TEXT')):
            job = self.request('POST', '/api/documents', {'source': 'Test source'})[1]
            result = self.wait_job(job)
        self.assertEqual(result['status'], 'failed')
        self.assertNotIn('SECRET', str(result))
        self.assertNotIn('result', result)

    def test_harness_origin_and_minimal_session(self):
        for url in ('https://example.com', 'http://127.0.0.1:8765/foo', 'http://x@localhost:8765', 'http://localhost:8765/?x=1'):
            with self.assertRaises(ValueError):
                local_address(url)
        self.assertEqual(local_address('http://127.0.0.1:8765'), ('127.0.0.1', 8765))
        session = self.request('GET', '/api/session')[1]
        self.assertEqual(session['token'], self.token)
        self.assertNotIn('samples', session)
        status, report, _ = self.request('POST', '/api/context/resolve', {'context': {'channel': 'unknown'}})
        self.assertEqual(status, 200)
        self.assertEqual(report['counts']['authored_examples'], 0)


# Reuse the HTTP fixture without rerunning inherited regression methods here.
for _name in list(vars(test_server.ServerTests)):
    if _name.startswith('test_') and _name not in vars(DocumentApiTests):
        setattr(DocumentApiTests, _name, None)
