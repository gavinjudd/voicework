"""Synthetic writing only: article-scoped choices must not silence real issues."""
import copy
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_server
from voicework.document_decisions import DocumentDecisionStore, acknowledge, scope_decisions
from voicework.provider import Provider
from voicework.server import validate_controls

SOURCE = '# Trial notes\n\nThe trial showed that the valves did not leak.\n\nWe tested 4 valves.\n'
DRAFT = '# Trial notes\n\nThe trial showed what happened. The valves did not leak.\n\nWe tested 4 valves.\n'
CONTEXT = {'channel': 'technical article', 'audience': 'technical readers'}
CONTROLS = validate_controls({}, document=True)


def suggestion(**changes):
    item = {'classification': 'style_suggestion', 'code': 'redundant_setup', 'label': 'Optional style suggestion',
            'severity': 'suggestion', 'message': 'Consider joining the setup and result.', 'section': 's1',
            'source_quote': 'The trial showed that the valves did not leak.',
            'draft_quote': 'The trial showed what happened. The valves did not leak.',
            'preference_ids': [], 'basis': 'An optional pacing choice, not a requirement.'}
    item.update(changes)
    return item


def checks(items=None, **changes):
    value = {'status': 'checked', 'content': {'status': 'checked', 'issues': []},
             'voice': {'status': 'checked', 'issues': [], 'suggestions': [suggestion()] if items is None else items},
             'warnings': [], 'meaning_changes': {'checks': []}}
    value.update(changes)
    return value


class DecisionStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'profile.sqlite3'
        self.store = DocumentDecisionStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def keep(self, **changes):
        values = dict(source=SOURCE, draft=DRAFT, context=CONTEXT, controls=CONTROLS,
                      suggestion=suggestion(), reason='Keep this pacing in this article.')
        values.update(changes)
        return self.store.keep(**values)

    def snapshot(self, source=SOURCE, draft=DRAFT, context=None, controls=None):
        return self.store.resolve(source, draft, context or CONTEXT, controls or CONTROLS)

    def test_keep_persists_and_duplicate_action_is_idempotent(self):
        first = self.keep(); self.assertEqual(self.keep()['id'], first['id'])
        self.store = DocumentDecisionStore(self.path)
        snapshot = self.snapshot()
        self.assertEqual(snapshot['items'][0]['applicability'], 'applicable')
        self.assertEqual(len(snapshot['items'][0]['history']), 1)
        native = checks(); result = acknowledge(native, snapshot)
        self.assertEqual(result['voice']['suggestions'], [])
        self.assertEqual(result['voice']['accepted_suggestions'][0]['decision_id'], first['id'])
        self.assertEqual(native['voice']['suggestions'], [suggestion()])

    def test_model_label_and_message_variation_still_matches_exact_accepted_edit(self):
        self.keep()
        result = acknowledge(checks([suggestion(code='opening_pacing', message='Another explanation of the same optional edit.')]), self.snapshot())
        self.assertEqual(len(result['voice']['accepted_suggestions']), 1)

    def test_other_article_context_and_controls_cannot_inherit_the_decision(self):
        self.keep()
        self.assertEqual(self.snapshot(source=SOURCE.replace('Trial notes', 'Another article'))['items'], [])
        for context, controls in [(dict(CONTEXT, channel='personal'), CONTROLS),
                                  (dict(CONTEXT, audience='board'), CONTROLS),
                                  (dict(CONTEXT, scenario='Legal disclosure'), CONTROLS),
                                  (CONTEXT, dict(CONTROLS, register='formal'))]:
            snap = self.snapshot(context=context, controls=controls)
            self.assertEqual(snap['items'][0]['applicability'], 'context_changed')
            self.assertEqual(len(acknowledge(checks(), snap)['voice']['suggestions']), 1)

    def test_changed_paragraph_ambiguous_quote_and_changed_polarity_stop_matching(self):
        self.keep()
        for text in [DRAFT.replace('did not leak', 'leaked'),
                     DRAFT.replace('did not leak.', 'did not leak. This proves every valve is safe.'),
                     DRAFT + '\n' + suggestion()['draft_quote'],
                     DRAFT.replace('happened. The', 'happened.\n\nThe')]:
            snap = self.snapshot(draft=text)
            self.assertEqual(snap['items'][0]['applicability'], 'passage_changed')
            self.assertEqual(len(acknowledge(checks(), snap)['voice']['suggestions']), 1)

    def test_changes_elsewhere_do_not_reopen_the_accepted_passage(self):
        self.keep()
        snap = self.snapshot(draft=DRAFT.replace('4 valves', '5 valves'))
        self.assertEqual(snap['items'][0]['applicability'], 'applicable')
        result = acknowledge(checks(content={'status': 'review_required', 'issues': [{'code': 'numbers_changed'}]}, status='review_required'), snap)
        self.assertEqual(len(result['voice']['accepted_suggestions']), 1)
        self.assertEqual(result['content']['issues'], [{'code': 'numbers_changed'}])
        self.assertEqual(result['status'], 'review_required')

    def test_violations_uncertainty_and_content_findings_are_never_acknowledged(self):
        self.keep()
        for classification in ('preference_violation', 'uncertain'):
            native = checks(status='review_required')
            native['voice']['issues'] = [suggestion(classification=classification)]
            native['warnings'] = ['Semantic review incomplete.']
            native['content']['issues'] = [{'code': 'meaning_changed'}]
            native['meaning_changes']['checks'] = [{'verdict': 'changed'}]
            result = acknowledge(native, self.snapshot())
            for key in ('status', 'content', 'warnings', 'meaning_changes'):
                self.assertEqual(result[key], native[key])
            self.assertEqual(result['voice']['issues'], native['voice']['issues'])

    def test_unrelated_optional_edits_and_new_preference_references_remain_open(self):
        self.keep()
        others = [suggestion(draft_quote='The valves did not leak.'), suggestion(preference_ids=['another-preference'])]
        result = acknowledge(checks([suggestion()] + others), self.snapshot())
        self.assertEqual(result['voice']['suggestions'], others)
        self.assertEqual(len(result['voice']['accepted_suggestions']), 1)

    def test_undo_restoration_and_history_survive_reopening(self):
        first = self.keep(); self.store.revoke(first['id']); self.store.revoke(first['id'])
        self.store = DocumentDecisionStore(self.path)
        snap = self.snapshot()
        self.assertEqual(snap['items'][0]['status'], 'revoked')
        self.assertEqual(len(snap['items'][0]['history']), 2)
        self.assertEqual(len(acknowledge(checks(), snap)['voice']['suggestions']), 1)
        self.assertEqual(self.keep()['id'], first['id'])
        self.assertEqual(len(self.snapshot()['items'][0]['history']), 3)

    def test_frozen_snapshot_does_not_change_during_later_mutations(self):
        first = self.keep(); snapshot = self.snapshot()
        self.store.revoke(first['id'])
        self.assertEqual(len(acknowledge(checks(), snapshot)['voice']['accepted_suggestions']), 1)
        self.assertEqual(len(acknowledge(checks(), self.snapshot())['voice']['suggestions']), 1)
        self.assertNotEqual(snapshot['snapshot_hash'], self.snapshot()['snapshot_hash'])

    def test_invalid_decisions_fail_before_any_write(self):
        for changes in [{'suggestion': suggestion(classification='preference_violation')},
                        {'suggestion': suggestion(source_quote='invented')},
                        {'suggestion': suggestion(draft_quote='')},
                        {'suggestion': suggestion(preference_ids=['p', 'p'])},
                        {'suggestion': suggestion(code='invalid code')}, {'reason': ''},
                        {'draft': DRAFT + suggestion()['draft_quote']}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.keep(**changes)
        self.assertEqual(self.snapshot()['items'], [])

    def test_scope_snapshot_never_crosses_source_identity(self):
        self.keep()
        snap = scope_decisions(self.snapshot()['items'], SOURCE + '\nNew fact.', DRAFT, CONTEXT, CONTROLS)
        self.assertEqual(snap['items'][0]['applicability'], 'document_changed')


class DocumentDecisionApiTests(test_server.ServerTests):
    def payload(self):
        return {'source': SOURCE, 'text': DRAFT, 'context': CONTEXT, 'controls': CONTROLS}

    def keep(self):
        response = self.request('POST', '/api/document-decisions', dict(self.payload(), suggestion=suggestion(), reason='Keep this optional pacing.'))
        self.assertEqual(response[0], 200, response[1])
        return response[1]['decision']

    def wait_job(self, job):
        for _ in range(200):
            current = self.request('GET', '/api/documents/' + job['id'])[1]
            if current['status'] in ('complete', 'failed'):
                self.assertEqual(current['status'], 'complete', current)
                return current['result']
            time.sleep(.01)
        self.fail('Document job timed out')

    def test_save_recheck_undo_and_training_remains_unchanged(self):
        before = self.request('GET', '/api/state')[1]
        decision = self.keep()
        self.assertEqual(self.request('POST', '/api/document-decisions/resolve', self.payload())[1]['items'][0]['id'], decision['id'])
        with patch.object(self.provider, 'check_document', return_value=checks()):
            result = self.wait_job(self.request('POST', '/api/document-checks', self.payload())[1])
            self.assertEqual(result['checks']['voice']['suggestions'], [])
            self.assertEqual(result['checks']['voice']['accepted_suggestions'][0]['decision_id'], decision['id'])
            self.assertNotIn('text', result)
            self.assertEqual(self.request('POST', '/api/document-decisions/' + decision['id'] + '/revoke', {})[0], 200)
            result = self.wait_job(self.request('POST', '/api/document-checks', self.payload())[1])
            self.assertEqual(len(result['checks']['voice']['suggestions']), 1)
        after = self.request('GET', '/api/state')[1]
        for key in ('samples', 'feedback', 'language_lessons', 'corrections', 'evaluations', 'quality_checks', 'profile'):
            self.assertEqual(before[key], after[key])

    def test_authenticated_validation_and_missing_record(self):
        payload = dict(self.payload(), suggestion=suggestion(), reason='Keep this pacing.')
        self.assertEqual(self.request('POST', '/api/document-decisions', payload, headers={'X-Voicework-Token': 'wrong'})[0], 403)
        for change in [{'unknown': True}, {'suggestion': suggestion(classification='uncertain')}, {'text': 'No matching quote'}, {'controls': {'register': 'invalid'}}]:
            self.assertEqual(self.request('POST', '/api/document-decisions', dict(payload, **change))[0], 400)
        self.assertEqual(self.request('POST', '/api/document-decisions/' + '0'*32 + '/revoke', {})[0], 404)
        self.assertEqual(self.request('POST', '/api/document-decisions/resolve', self.payload())[1]['items'], [])

    def test_decisions_are_frozen_for_review_separately_from_provider_guidance(self):
        decision = self.keep(); gate = threading.Event(); entered = threading.Event(); captured = []
        def check(*args, **kwargs):
            captured.append(copy.deepcopy((args, kwargs))); entered.set(); gate.wait(2); return checks()
        with patch.object(self.provider, 'check_document', side_effect=check):
            job = self.request('POST', '/api/document-checks', self.payload())[1]
            self.assertTrue(entered.wait(1))
            self.server.document_decisions.revoke(decision['id'])
            gate.set(); result = self.wait_job(job)
        self.assertEqual(len(result['checks']['voice']['accepted_suggestions']), 1)
        args, kwargs = captured[0]
        self.assertNotIn(decision['id'], str(args))
        self.assertEqual(len(args), 6)
        self.assertEqual(kwargs['document_decisions']['items'][0]['id'], decision['id'])
        self.assertEqual(kwargs['document_decisions']['items'][0]['status'], 'active')

    def test_generation_acknowledges_matching_choice_without_changing_text(self):
        self.keep()
        payload = self.payload(); payload.pop('text')
        generated = {'text': DRAFT, 'provider': 'offline', 'checks': checks()}
        with patch.object(self.provider, 'generate_document', return_value=generated):
            result = self.wait_job(self.request('POST', '/api/documents', payload)[1])
        self.assertEqual(result['text'], DRAFT)
        self.assertEqual(len(result['checks']['voice']['accepted_suggestions']), 1)


for _name in list(vars(test_server.ServerTests)):
    if _name.startswith('test_') and _name not in vars(DocumentDecisionApiTests):
        setattr(DocumentDecisionApiTests, _name, None)

if __name__ == '__main__':
    unittest.main()
