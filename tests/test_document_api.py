import copy
import threading
import time
from unittest.mock import patch

import test_server
from voicework.harness import local_address


class DocumentApiTests(test_server.ServerTests):
    def test_automatic_transfers_and_exclusions_reach_preview_generation_and_review(self):
        from voicework.provider import _payload
        from test_automatic_samples import ARTICLE, sample
        saved = []
        for ident, flags in [('yes', {}), ('assisted', {'status': 'assisted'}), ('held', {'held_out': True}), ('unrelated', {})]:
            fixture = sample(ident, text=sample()['text'] + ' ' + ident)
            if ident == 'unrelated': fixture['context']['intent'] = 'invitation'
            record = self.server.store.add_sample({k: fixture[k] for k in ('text', 'context', 'title')})
            self.server.store.update_sample(record['id'], {'status': 'confirmed', **flags})
            saved.append(record['id'])
        before = self.server.store.samples()
        status, report, _ = self.request('POST', '/api/context/resolve', {'context': ARTICLE})
        self.assertEqual(status, 200)
        self.assertEqual(report['evidence_ids'], [saved[0]])
        self.assertEqual(report['sample_selection']['automatic_selections'][0]['kind'], 'automatic_transfer')
        captured = []
        def generate(context, source, controls, evidence, preferences):
            captured.append(_payload(context, evidence, preferences))
            return {'text': source, 'checks': {'status': 'checked'}}
        def check(context, source, draft, controls, evidence, preferences):
            captured.append(_payload(context, evidence, preferences))
            return {'status': 'checked'}
        with patch.object(self.provider, 'generate_document', side_effect=generate), patch.object(self.provider, 'check_document', side_effect=check):
            for exclusions in ([], [saved[0]]):
                for path in ('/api/documents', '/api/document-checks'):
                    body = {'context': ARTICLE, 'source': 'The database test ran.', 'excluded_sample_ids': exclusions}
                    if path.endswith('checks'): body['text'] = body['source']
                    status, job, _ = self.request('POST', path, body)
                    self.assertEqual(status, 202)
                    result = self.wait_job(job)['result']
                    expected = [] if exclusions else [saved[0]]
                    self.assertEqual(result['evidence_ids'], expected)
                    self.assertEqual([s['id'] for s in captured[-1]['authored_examples']], expected)
        self.assertEqual(captured[0], captured[1])
        self.assertEqual(self.server.store.samples(), before)
        self.server.store.update_sample(saved[0], {'held_out': True})
        self.assertEqual(self.request('POST', '/api/context/resolve', {'context': ARTICLE})[1]['evidence_ids'], [])

    def test_automatic_exclusion_validation_is_shared_across_document_routes(self):
        for value in (None, {}, ['duplicate', 'duplicate'], [7]):
            for path in ('/api/context/resolve', '/api/documents', '/api/document-checks'):
                body = {'excluded_sample_ids': value}
                if path != '/api/context/resolve': body['source'] = 'Draft.'
                if path.endswith('checks'): body['text'] = 'Draft.'
                with self.subTest(value=value, path=path):
                    self.assertEqual(self.request('POST', path, body)[0], 400)

    def test_document_references_use_stored_provenance_and_reach_generation_and_checks(self):
        sample = self.server.store.add_sample({'title': 'Synthetic note', 'text': 'I tested the queue. It kept growing.',
                                               'context': {'channel': 'work', 'audience': 'peer'}})
        self.server.store.update_sample(sample['id'], {'status': 'confirmed'})
        payload = {'source': 'The test ran.', 'context': {'channel': 'technical article', 'audience': 'readers'},
                   'authored_references': [{'id': sample['id'], 'aspect': 'Direct observations, not texting layout.'}]}
        before = self.server.store.samples()
        report = self.request('POST', '/api/context/resolve', {k:v for k,v in payload.items() if k != 'source'})[1]
        self.assertEqual(report['counts']['authored_examples'], 1)
        self.assertEqual(report['sample_selection']['automatic_match_count'], 0)
        self.assertEqual([item['id'] for item in report['reference_candidates']], [sample['id']])
        self.assertNotIn('text', report['reference_candidates'][0])
        captured = []
        from voicework.provider import _payload
        def generate(context, source, controls, evidence, preferences):
            captured.append(_payload(context, evidence, preferences))
            return {'text': source, 'provider': 'offline', 'warnings': []}
        def check(context, source, text, controls, evidence, preferences):
            captured.append(_payload(context, evidence, preferences))
            return {'status': 'checked'}
        with patch.object(self.provider, 'generate_document', side_effect=generate), patch.object(self.provider, 'check_document', side_effect=check):
            for path in ('/api/documents', '/api/document-checks'):
                body = {**payload, **({'text': payload['source']} if path.endswith('checks') else {})}
                status, job, _ = self.request('POST', path, body)
                self.assertEqual(status, 202)
                result = self.wait_job(job)['result']
                self.assertEqual(result['evidence_ids'], [sample['id']])
                self.assertNotIn('reference_candidates', result['context_report'])
        self.assertEqual(captured[0]['authored_examples'], captured[1]['authored_examples'])
        self.assertEqual(captured[0]['authored_examples'][0]['context']['channel'], 'work')
        self.assertEqual(self.server.store.samples(), before)

    def test_reference_revocation_and_holdout_are_checked_on_each_request(self):
        sample = self.server.store.add_sample({'text': 'Synthetic authored sample.'})
        body = {'source': 'Draft.', 'authored_references': [{'id': sample['id'], 'aspect': 'Pacing.'}]}
        self.assertEqual(self.request('POST', '/api/documents', body)[0], 400)
        self.server.store.update_sample(sample['id'], {'status': 'confirmed', 'held_out': True})
        self.assertEqual(self.request('POST', '/api/documents', body)[0], 400)
        self.server.store.update_sample(sample['id'], {'held_out': False})
        self.assertEqual(self.request('POST', '/api/documents', body)[0], 202)
        self.server.store.delete_sample(sample['id'])
        self.assertEqual(self.request('POST', '/api/document-checks', {**body, 'text': 'Draft.'})[0], 400)

    def test_reference_validation_is_shared_by_preview_rewrite_and_recheck(self):
        for value in (None, {}, [{'id': 'missing', 'aspect': 'Tone.'}], [{'id': 'x', 'aspect': ''}]):
            for path in ('/api/context/resolve', '/api/documents', '/api/document-checks'):
                body = {'authored_references': value}
                if path != '/api/context/resolve': body.update(source='Draft.')
                if path == '/api/document-checks': body.update(text='Draft.')
                with self.subTest(value=value, path=path):
                    self.assertEqual(self.request('POST', path, body)[0], 400)

    def test_native_mixed_edit_review_consolidates_duplicate_evidence(self):
        source = 'We cannot open this file. The search returned one note.'
        draft = "We can't open this file. The search could return only one note."
        def review(task, payload):
            self.assertEqual(task, 'document_review')
            edits = payload['meaning_change_requests']
            return {'voice_issues': [], 'voice_aspect_checks': [], 'warnings': [],
                'content_issues': [{'severity': 'warning', 'code': 'capacity_limit_added',
                    'message': 'The result is presented as the maximum attainable.', 'section': edits[1]['section'],
                    'change_ids': [edits[1]['change_id']]}],
                'meaning_checks': [{'change_id': item['change_id'], 'verdict': 'preserved' if index == 0 else 'changed',
                    'dimension': 'none' if index == 0 else 'claim_strength',
                    'reason': 'The inability is unchanged.' if index == 0 else 'An observed result becomes a capability limit.'}
                    for index, item in enumerate(edits)]}
        with patch.object(self.provider, 'mode', 'codex'), patch.object(self.provider, '_invoke', side_effect=review):
            status, job, _ = self.request('POST', '/api/document-checks', {'source': source, 'text': draft})
            self.assertEqual(status, 202)
            completed = self.wait_job(job)
        self.assertEqual(completed['status'], 'complete')
        checks = completed['result']['checks']
        self.assertEqual([item['verdict'] for item in checks['meaning_changes']['checks']], ['preserved', 'changed'])
        self.assertEqual(len(checks['content']['issues']), 1)
        self.assertEqual(checks['content']['issues'][0]['label'], 'Claim strength changed')
        self.assertEqual({s['origin'] for s in checks['content']['issues'][0]['evidence']}, {'deterministic', 'semantic_review', 'edit_assessment'})

    def test_meaning_assessments_reach_native_recheck_without_learning_or_rewriting(self):
        before_state = self.request('GET', '/api/state')[1]
        source, draft = 'The lab cannot accept this file.', "The lab can't accept this file."
        def review(task, payload):
            self.assertEqual(task, 'document_review')
            return {'content_issues': [], 'voice_issues': [], 'voice_aspect_checks': [], 'warnings': [],
                    'meaning_checks': [{'change_id': item['change_id'], 'verdict': 'preserved',
                                        'reason': 'Both passages state the same inability to accept the file.'}
                                       for item in payload['meaning_change_requests']]}
        with patch.object(self.provider, 'mode', 'codex'), patch.object(self.provider, '_invoke', side_effect=review):
            status, job, _ = self.request('POST', '/api/document-checks', {'source': source, 'text': draft, 'context': {'channel': 'work'}})
            self.assertEqual(status, 202)
            completed = self.wait_job(job)
        self.assertEqual(completed['status'], 'complete')
        result = completed['result']
        self.assertNotIn('text', result)
        self.assertEqual(result['checks']['content']['issues'], [])
        self.assertEqual(result['checks']['meaning_changes']['checks'][0]['verdict'], 'preserved')
        after_state = self.request('GET', '/api/state')[1]
        for key in ('samples', 'feedback', 'language_lessons', 'corrections', 'evaluations', 'quality_checks', 'identity_profiles', 'preference_traits'):
            self.assertEqual(before_state[key], after_state[key])

    def test_revision_intent_is_document_only_validated_and_frozen(self):
        captured = []
        def generate(context, source, controls, evidence, preferences):
            captured.append(copy.deepcopy(controls))
            return {'text': source, 'provider': 'offline', 'warnings': []}
        with patch.object(self.provider, 'generate_document', side_effect=generate):
            for revision in (None, 'rewrite', 'copyedit'):
                body = {'source': 'A synthetic draft.'}
                if revision is not None:
                    body['controls'] = {'revision': revision}
                status, job, _ = self.request('POST', '/api/documents', body)
                self.assertEqual(status, 202)
                self.assertEqual(self.wait_job(job)['status'], 'complete')
        self.assertEqual([value['revision'] for value in captured], ['rewrite', 'rewrite', 'copyedit'])
        for value in ('other', {}, [], 1, None):
            self.assertEqual(self.request('POST', '/api/documents', {'source': 'Draft.', 'controls': {'revision': value}})[0], 400)
        self.assertEqual(self.request('POST', '/api/draft', {'context': {'channel': 'work'}, 'brief': 'Brief.', 'controls': {'revision': 'rewrite'}})[0], 400)

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
