"""Synthetic fixtures: accepting one edit is not approval of a paragraph."""
import copy
import tempfile
import unittest
from pathlib import Path

import test_server
from voicework.document_decisions import DocumentDecisionStore, acknowledge, classifier_context, digest
from voicework.reviewed_decisions import current_edits, eligible, preview
from voicework.documents import parse_document
from voicework.document_wording import instruction_plan
from voicework.server import validate_controls

SOURCE = '# Laboratory notes\n\n## Scope\n\nThis describes the prototype, but I cannot confirm the hosted tests ran.\n\n## Results\n\nWe tested 8 valves.\n'
DRAFT = SOURCE.replace('This describes', 'I can describe')
CONTEXT = {'channel': 'technical article', 'audience': 'technical readers'}
CONTROLS = validate_controls({}, document=True)


def evidence(source=SOURCE, draft=DRAFT, quote='I can describe', dimension='claim_strength'):
    edit = next(copy.deepcopy(e) for e, _ in current_edits(source, draft) if quote in e['draft_quote'])
    edit.update(verdict='changed', dimension=dimension, reviewed=True, reason='The description becomes a statement of capability.')
    issue = {'code': 'meaning_changed', 'severity': 'warning', 'change_id': edit['change_id'], 'section': edit['section'],
             'dimension': dimension, 'message': edit['reason'], 'conflicting_assessments': False,
             'evidence': [{'origin': 'edit_assessment', 'code': 'meaning_changed', 'severity': 'warning',
                           'message': edit['reason'], 'change_ids': [edit['change_id']], 'blocking': True}]}
    return issue, edit


def checks(source=SOURCE, draft=DRAFT):
    issue, edit = evidence(source, draft)
    return {'status': 'review_required', 'source_hash': digest(source), 'draft_hash': digest(draft),
            'review': {'status': 'checked'}, 'content': {'status': 'review_required', 'semantic': 'checked', 'issues': [issue]},
            'voice': {'status': 'checked', 'issues': [], 'suggestions': []}, 'warnings': [],
            'meaning_changes': {'semantic_complete': True, 'unreviewed': 0, 'omitted': 0, 'checks': [edit]}}


class ReviewedDecisionsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'profile.sqlite3'
        self.store = DocumentDecisionStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def keep(self):
        issue, edit = evidence()
        return self.store.keep_reviewed(SOURCE, DRAFT, CONTEXT, CONTROLS, issue, edit, 'Accept this framing in this article.')

    def snapshot(self, source=SOURCE, draft=DRAFT, context=CONTEXT, controls=CONTROLS):
        return self.store.resolve(source, draft, context, controls)

    def test_persists_after_restart_and_preserves_raw_diagnostics(self):
        record = self.keep(); self.assertEqual(record['id'], self.keep()['id'])
        self.store = DocumentDecisionStore(self.path)
        native = checks(); result = acknowledge(native, self.snapshot())
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['content']['issues'], [])
        self.assertEqual(result['content']['accepted_issues'][0]['decision_id'], record['id'])
        self.assertEqual(result['content']['native_issues'], native['content']['issues'])
        self.assertEqual(result['meaning_changes']['checks'][0]['verdict'], 'changed')
        self.assertEqual(native['status'], 'review_required')
        self.assertNotIn('accepted_decision_id', native['meaning_changes']['checks'][0])
        self.assertEqual(len(self.snapshot()['items'][0]['history']), 1)

    def test_revocation_and_restore_have_reviewable_history(self):
        record = self.keep(); self.store.revoke(record['id'])
        self.store = DocumentDecisionStore(self.path)
        self.assertEqual(acknowledge(checks(), self.snapshot())['content']['issues'], checks()['content']['issues'])
        self.assertEqual(self.keep()['id'], record['id'])
        self.assertEqual([x['action'] for x in self.snapshot()['items'][0]['history']], ['restored', 'revoked', 'kept'])

    def test_changed_negation_actor_quantity_or_nearby_claim_disables_match(self):
        self.keep()
        for draft in [DRAFT.replace('cannot confirm', 'can confirm'), DRAFT.replace('I can', 'We can'),
                      DRAFT.replace('prototype,', 'prototype for 80 plants,'),
                      DRAFT.replace('tests ran.', 'tests ran. This proves it works everywhere.'),
                      DRAFT + '\nI can describe another project.\n']:
            with self.subTest(draft=draft):
                snap = self.snapshot(draft=draft)
                self.assertNotEqual(snap['items'][0]['applicability'], 'applicable')
                self.assertEqual(acknowledge(checks(), snap)['content']['issues'], checks()['content']['issues'])

    def test_other_article_context_controls_do_not_inherit(self):
        self.keep()
        self.assertEqual(self.snapshot(source=SOURCE.replace('Laboratory notes', 'Another story'))['items'], [])
        for kwargs in [{'context': dict(CONTEXT, audience='investors')}, {'controls': dict(CONTROLS, register='formal')}]:
            self.assertEqual(self.snapshot(**kwargs)['items'][0]['applicability'], 'context_changed')

    def test_different_edit_or_dimension_remains_open(self):
        self.keep()
        for field, value in [('dimension', 'actor'), ('draft_quote', 'We can describe'), ('source_context', 'New context')]:
            native = checks(); edit = native['meaning_changes']['checks'][0]; edit[field] = value
            if field == 'dimension': native['content']['issues'][0]['dimension'] = value
            self.assertEqual(acknowledge(native, self.snapshot())['content']['issues'], native['content']['issues'])

    def test_independent_review_numeric_and_protected_findings_remain_open(self):
        self.keep()
        for signal in [{'origin': 'semantic_review', 'code': 'meaning_changed', 'severity': 'warning'},
                       {'origin': 'deterministic', 'code': 'numbers_changed', 'severity': 'error'},
                       {'origin': 'deterministic', 'code': 'protected_content_changed', 'severity': 'error'}]:
            native = checks(); native['content']['issues'][0]['evidence'].append(signal)
            result = acknowledge(native, self.snapshot())
            self.assertEqual(result['content']['issues'], native['content']['issues'])
            with self.assertRaises(ValueError):
                preview(SOURCE, DRAFT, native['content']['issues'][0], native['meaning_changes']['checks'][0])

    def test_incomplete_uncertain_conflicting_or_mismatched_checks_never_clear(self):
        self.keep()
        variants = []
        for key, value in [('semantic_complete', False), ('unreviewed', 1), ('omitted', 1)]:
            native = checks(); native['meaning_changes'][key] = value; variants.append(native)
        native = checks(); native['meaning_changes']['checks'][0]['verdict'] = 'uncertain'; variants.append(native)
        native = checks(); native['content']['issues'][0]['conflicting_assessments'] = True; variants.append(native)
        native = checks(); native['source_hash'] = 'wrong'; variants.append(native)
        native = checks(); native['draft_hash'] = 'wrong'; variants.append(native)
        native = checks(); native['review']['status'] = 'not_checked'; variants.append(native)
        for native in variants:
            self.assertEqual(acknowledge(native, self.snapshot())['content']['issues'], native['content']['issues'])

    def test_unrelated_edit_problems_are_retained_while_exact_choice_is_acknowledged(self):
        self.keep(); draft = DRAFT.replace('8 valves', '9 valves')
        native = checks(draft=draft)
        new_issue = {'code': 'numbers_changed', 'severity': 'error'}
        native['content']['issues'].append(new_issue)
        snap = self.snapshot(draft=draft)
        self.assertEqual(snap['items'][0]['applicability'], 'applicable')
        result = acknowledge(native, snap)
        self.assertEqual(result['content']['issues'], [new_issue])
        self.assertEqual(len(result['content']['accepted_issues']), 1)
        self.assertEqual(result['status'], 'review_required')

    def test_preferences_and_process_warnings_never_clear(self):
        self.keep()
        for category in ('preference', 'warning', 'uncertain_voice'):
            native = checks()
            if category == 'warning': native['warnings'] = ['An independent review step failed.']
            else: native['voice']['issues'] = [{'classification': 'preference_violation' if category == 'preference' else 'uncertain'}]
            result = acknowledge(native, self.snapshot())
            self.assertEqual(result['status'], 'review_required')
            self.assertEqual(result['voice']['issues'], native['voice']['issues'])
            self.assertEqual(result['warnings'], native['warnings'])

    def test_inserted_words_use_exact_sentences_for_generation(self):
        source = '# Workshop notes\n\nThe teams have a lathe.\n'
        draft = '# Workshop notes\n\nThe teams have tuned a lathe.\n'
        issue, edit = evidence(source, draft, 'tuned', 'content')
        self.assertEqual(edit['source_quote'], '')
        item = preview(source, draft, issue, edit)
        self.assertEqual(item['source_quote'], source.split('\n\n')[1].strip())
        self.assertEqual(item['draft_quote'], draft.split('\n\n')[1].strip())

    def test_generation_gets_wording_but_classification_never_receives_approval(self):
        self.keep(); snap = self.snapshot()
        self.assertEqual(classifier_context(snap, SOURCE, DRAFT, CONTEXT, CONTROLS), [])
        plan = instruction_plan(SOURCE, CONTEXT, CONTROLS, parse_document(SOURCE)[0], decisions=snap)
        self.assertIn('I can describe', str(plan))
        self.assertNotIn('reviewed_edit_key', str(plan))

    def test_invalid_or_forged_coordinates_are_rejected_before_save(self):
        issue, edit = evidence()
        for change in [{'source_range': [0, 2]}, {'source_quote': 'invented'}, {'draft_context': 'invented'}]:
            with self.assertRaises(ValueError):
                self.store.keep_reviewed(SOURCE, DRAFT, CONTEXT, CONTROLS, issue, dict(edit, **change), 'Keep.')
        self.assertEqual(self.snapshot()['items'], [])


class ReviewedDecisionApiTests(test_server.ServerTests):
    def test_browser_module_is_served(self):
        import urllib.request
        with urllib.request.urlopen('http://127.0.0.1:%d/reviewed-decisions.mjs' % self.port) as response:
            self.assertIn('text/javascript', response.headers['Content-Type'])
            self.assertIn(b'export function reviewedEditCandidate', response.read())

    def payload(self):
        issue, edit = evidence()
        return {'source': SOURCE, 'text': DRAFT, 'context': CONTEXT, 'controls': CONTROLS, 'finding': issue, 'assessment': edit}

    def test_preview_save_resolve_and_revoke(self):
        payload = self.payload()
        status, result, _ = self.request('POST', '/api/document-decisions/reviewed/preview', payload)
        self.assertEqual(status, 200); self.assertEqual(result['preview']['draft_quote'], 'I can describe')
        self.assertEqual(self.server.document_decisions.resolve(SOURCE, DRAFT, CONTEXT, CONTROLS)['items'], [])
        status, result, _ = self.request('POST', '/api/document-decisions/reviewed', dict(payload, reason='Accept this difference.'))
        self.assertEqual(status, 200)
        ident = result['decision']['id']
        self.assertEqual(result['decisions']['items'][0]['applicability'], 'applicable')
        self.assertEqual(self.request('POST', '/api/document-decisions/' + ident + '/revoke', {})[0], 200)

    def test_preview_and_save_reject_bad_token_and_malformed_findings(self):
        for route in ('/api/document-decisions/reviewed', '/api/document-decisions/reviewed/preview'):
            self.assertEqual(self.request('POST', route, self.payload(), {'X-Voicework-Token': 'wrong'})[0], 403)
            payload = dict(self.payload(), finding={'code': 'numbers_changed'})
            if not route.endswith('/preview'): payload['reason'] = 'Keep'
            self.assertEqual(self.request('POST', route, payload)[0], 400)


for _name in list(vars(test_server.ServerTests)):
    if _name.startswith('test_') and _name not in vars(ReviewedDecisionApiTests):
        setattr(ReviewedDecisionApiTests, _name, None)
