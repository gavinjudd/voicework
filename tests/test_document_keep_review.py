"""Synthetic regression cases for scoped approvals in native voice classification."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voicework.document_decisions import DocumentDecisionStore, acknowledge, classifier_context
from voicework.provider import Provider, _prompt
from voicework.server import validate_controls

SOURCE = '# Trial notes\n\nThe trial showed that the valves did not leak.\n\nWe tested 4 valves.\n'
DRAFT = '# Trial notes\n\nThe trial showed what happened. The valves did not leak.\n\nWe tested 4 valves.\n'
CONTEXT = {'channel': 'technical article', 'audience': 'technical readers'}
CONTROLS = validate_controls({}, document=True)
PREFERENCE = {'id': 'plain-language', 'is_language_lesson': True, 'enabled': True, 'context': CONTEXT,
              'dimension': 'syntax', 'guidance': 'Use plain language and trim redundant setup without losing reasoning.',
              'preferred': '', 'avoided': ''}


def finding(**changes):
    item = {'classification': 'style_suggestion', 'severity': 'warning', 'code': 'redundant_setup',
            'message': 'Consider combining the setup and result.', 'section': 's1',
            'source_quote': 'The trial showed that the valves did not leak.',
            'draft_quote': 'The trial showed what happened. The valves did not leak.',
            'preference_ids': ['plain-language'], 'basis': 'An optional pacing tradeoff under the plain-language lesson.'}
    item.update(changes)
    return item


def review(payload, findings=None):
    value = {'content_issues': [], 'voice_issues': findings or [], 'voice_aspect_checks': [], 'warnings': [],
            'meaning_checks': [{'change_id': item['change_id'], 'verdict': 'preserved', 'dimension': 'none',
                                'reason': 'The synthetic edit preserves the stated test result.'}
                               for item in payload.get('meaning_change_requests', [])]}
    return {key:value[key] for key in ('meaning_checks','warnings')} if 'meaning_batch' in payload else value


class KeepReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DocumentDecisionStore(Path(self.temp.name) / 'profile.sqlite3')
        self.saved = self.store.keep(SOURCE, DRAFT, CONTEXT, CONTROLS, finding(), 'Keep this pacing in this article.')
        self.snapshot = self.store.resolve(SOURCE, DRAFT, CONTEXT, CONTROLS)

    def tearDown(self):
        self.temp.cleanup()

    def check(self, invoke, source=SOURCE, draft=DRAFT, snapshot=None, context=None, controls=None):
        with patch.object(Provider, '_invoke', side_effect=invoke):
            return Provider('codex').check_document(context or CONTEXT, source, draft, controls or CONTROLS, [], [PREFERENCE],
                                                   document_decisions=self.snapshot if snapshot is None else snapshot)

    def test_context_projects_only_the_narrow_objection_and_is_immutable(self):
        before = copy.deepcopy(self.snapshot)
        choices = classifier_context(self.snapshot, SOURCE, DRAFT, CONTEXT, CONTROLS)
        self.assertEqual(choices[0]['scope'], 'unchanged_passage')
        self.assertEqual(choices[0]['prior_objection']['basis'], finding()['basis'])
        self.assertNotIn('history', choices[0])
        self.assertNotIn('context', choices[0])
        choices[0]['prior_objection']['preference_ids'].append('changed')
        self.assertEqual(self.snapshot, before)

    def test_changed_passage_is_history_not_automatic_approval(self):
        draft = DRAFT.replace('showed what happened', 'revealed what happened')
        choices = classifier_context(self.snapshot, SOURCE, draft, CONTEXT, CONTROLS)
        self.assertEqual(choices[0]['scope'], 'changed_passage')
        native = {'voice': {'suggestions': [finding()], 'issues': []}}
        result = acknowledge(native, self.store.resolve(SOURCE, draft, CONTEXT, CONTROLS))
        self.assertEqual(result['voice']['accepted_suggestions'], [])

    def test_other_article_context_controls_and_revoked_choices_are_excluded(self):
        for source, context, controls in [(SOURCE+'\nOther article.', CONTEXT, CONTROLS),
                (SOURCE, dict(CONTEXT, audience='board'), CONTROLS),
                (SOURCE, CONTEXT, dict(CONTROLS, register='formal'))]:
            self.assertEqual(classifier_context(self.snapshot, source, DRAFT, context, controls), [])
        self.store.revoke(self.saved['id'])
        revoked = self.store.resolve(SOURCE, DRAFT, CONTEXT, CONTROLS)
        self.assertEqual(classifier_context(revoked, SOURCE, DRAFT, CONTEXT, CONTROLS), [])

    def test_stale_applicability_is_recomputed_from_actual_draft(self):
        snap = copy.deepcopy(self.snapshot)
        snap['items'][0]['applicability'] = 'applicable'
        altered = DRAFT.replace('did not leak', 'leaked')
        choices = classifier_context(snap, SOURCE, altered, CONTEXT, CONTROLS)
        self.assertEqual(choices[0]['scope'], 'changed_passage')
        self.assertEqual(classifier_context(snap, SOURCE+'\n', altered, CONTEXT, CONTROLS), [])

    def test_scope_without_decisions_does_not_change_guidance_hash(self):
        calls = []
        def invoke(task, payload):
            calls.append(copy.deepcopy(payload)); return review(payload)
        with_choice = self.check(invoke)
        without_choice = self.check(invoke, snapshot={})
        self.assertEqual(with_choice['guidance_hash'], without_choice['guidance_hash'])
        self.assertEqual(calls[0]['article_keep_decisions'][0]['decision_id'], self.saved['id'])
        self.assertNotIn('article_keep_decisions', calls[1])
        self.assertEqual(calls[2]['article_keep_decisions'], [])
        self.assertEqual(with_choice['document_decision_review']['choices'],
                         [{'decision_id': self.saved['id'], 'scope': 'unchanged_passage'}])
        self.assertNotIn(self.saved['id'], str(calls[0]['language_lessons']))

    def test_native_optional_classification_can_be_acknowledged(self):
        def invoke(task, payload):
            if task=='document_review':
                self.assertEqual(payload['article_keep_decisions'][0]['scope'], 'unchanged_passage')
            else:
                self.assertNotIn('article_keep_decisions', payload)
            return review(payload, [finding()])
        result = acknowledge(self.check(invoke), self.snapshot)
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['voice']['suggestions'], [])
        self.assertEqual(result['voice']['accepted_suggestions'][0]['decision_id'], self.saved['id'])

    def test_classifier_violation_or_uncertainty_is_never_silently_downgraded(self):
        for classification in ('preference_violation', 'uncertain'):
            item = finding(classification=classification, code='independent_conflict',
                           basis='A different requirement remains unmet despite the accepted pacing.')
            result = acknowledge(self.check(lambda task, payload: review(payload, [item])), self.snapshot)
            self.assertEqual(result['voice']['issues'][0]['classification'], classification)
            self.assertEqual(result['voice']['accepted_suggestions'], [])
            self.assertEqual(result['status'], 'review_required')

    def test_content_checks_survive_an_active_keep_in_the_same_paragraph(self):
        source = SOURCE.replace('did not leak.', 'did not leak. We tested 4 valves.', 1)
        draft = DRAFT.replace('did not leak.', 'did not leak. We tested 5 valves.', 1)
        self.store.keep(source, draft, CONTEXT, CONTROLS, finding(), 'Keep the pacing only.')
        snap = self.store.resolve(source, draft, CONTEXT, CONTROLS)
        result = acknowledge(self.check(lambda task, payload: review(payload, [finding()]), source=source, draft=draft, snapshot=snap), snap)
        self.assertEqual(result['document_decision_review']['choices'][0]['scope'], 'unchanged_passage')
        self.assertTrue(any(x['code']=='numbers_changed' for x in result['content']['issues']))
        self.assertEqual(len(result['voice']['accepted_suggestions']), 1)
        self.assertEqual(result['status'], 'review_required')

    def test_meaning_verdict_survives_optional_acknowledgement(self):
        def invoke(task, payload):
            value = review(payload, [finding()])
            if task=='document_meaning':
                self.assertNotIn('article_keep_decisions',payload)
                value['meaning_checks'][0].update(verdict='changed', dimension='scope', reason='A separate synthetic scope concern remains.')
            return value
        result = acknowledge(self.check(invoke), self.snapshot)
        self.assertEqual(result['meaning_changes']['checks'][0]['verdict'], 'changed')
        self.assertTrue(result['content']['issues'])
        self.assertEqual(result['status'], 'review_required')

    def test_all_edit_verdicts_are_assessed_without_the_style_choice(self):
        calls=[]
        def invoke(task,payload):
            calls.append((task,copy.deepcopy(payload)))
            return review(payload,[finding()])
        result=self.check(invoke)
        primary=[p for task,p in calls if task=='document_review']
        independent=[p for task,p in calls if task=='document_meaning']
        self.assertEqual(primary[0]['meaning_change_requests'],[])
        self.assertTrue(primary[0]['article_keep_decisions'])
        ids=[item['change_id'] for p in independent for item in p['meaning_change_requests']]
        self.assertEqual(set(ids),{item['change_id'] for item in result['meaning_changes']['checks']})
        self.assertEqual(len(ids),len(set(ids)))
        self.assertTrue(all('article_keep_decisions' not in p for p in independent))
        self.assertEqual(result['review']['calls_total'],len(calls))
        self.assertEqual(result['review']['calls_completed'],len(calls))

    def test_clean_voice_review_cannot_hide_failed_independent_meaning_review(self):
        def invoke(task,payload):
            if task=='document_meaning':raise ValueError('Synthetic unavailable content reviewer')
            return review(payload,[finding()])
        result=acknowledge(self.check(invoke),self.snapshot)
        self.assertEqual(result['review']['status'],'not_checked')
        self.assertEqual(result['status'],'review_required')
        self.assertGreater(result['meaning_changes']['unreviewed'],0)
        self.assertTrue(result['warnings'])

    def test_supplemental_meaning_calls_never_receive_keep_context(self):
        source = SOURCE + '\n'.join('Reading %s stayed stable.' % i for i in range(35))
        draft = DRAFT + '\n'.join('Reading %s remained stable.' % i for i in range(35))
        self.store.keep(source, draft, CONTEXT, CONTROLS, finding(), 'Keep the opening pacing.')
        snap = self.store.resolve(source, draft, CONTEXT, CONTROLS)
        calls = []
        def invoke(task, payload):
            calls.append((task, copy.deepcopy(payload)))
            value = review(payload)
            return {k:value[k] for k in ('meaning_checks','warnings')} if task=='document_meaning' else value
        result = self.check(invoke, source=source, draft=draft, snapshot=snap)
        self.assertEqual(result['review']['status'], 'checked')
        self.assertTrue(any(task=='document_meaning' for task,payload in calls))
        for task,payload in calls:
            if task=='document_meaning':
                self.assertNotIn('article_keep_decisions', payload)
                self.assertNotIn(snap['items'][0]['id'], str(payload))

    def test_generation_receives_narrow_wording_instructions_and_review_is_rescoped(self):
        calls=[]
        def invoke(task,payload):
            calls.append((task,copy.deepcopy(payload)))
            if task=='document':
                return {'blocks':[{'id':block['id'],'text':block['text'].replace(finding()['source_quote'],finding()['draft_quote'])} for block in payload['blocks']], 'warnings':[]}
            return review(payload,[finding()])
        initial = self.store.resolve(SOURCE, SOURCE, CONTEXT, CONTROLS)
        with patch.object(Provider,'_invoke',side_effect=invoke):
            result=Provider('codex').generate_document(CONTEXT,SOURCE,CONTROLS,[],[PREFERENCE],document_decisions=initial)
        self.assertEqual(result['text'],DRAFT)
        self.assertEqual(result['revision_passes'],1)
        for task,payload in calls:
            if task=='document':
                self.assertNotIn('article_keep_decisions',payload)
                self.assertEqual(payload['article_wording_instructions'][0]['required_text'],finding()['draft_quote'])
            elif task=='document_review':self.assertEqual(payload['article_keep_decisions'][0]['scope'],'unchanged_passage')
            else:self.assertNotIn('article_keep_decisions',payload)

    def test_repair_rechecks_choice_scope_on_actual_repaired_text(self):
        calls=[]; reviews=[]
        def invoke(task,payload):
            calls.append((task,copy.deepcopy(payload)))
            if task=='document':
                replacement=finding()['draft_quote']
                if payload.get('revision_feedback'):replacement='Overall, '+replacement
                return {'blocks':[{'id':block['id'],'text':block['text'].replace(finding()['source_quote'],replacement)} for block in payload['blocks']], 'warnings':[]}
            if task=='document_meaning':return review(payload)
            reviews.append(payload['article_keep_decisions'][0]['scope'])
            return review(payload,[finding(classification='preference_violation',code='independent_conflict',basis='A different language requirement is unmet.')] if len(reviews)==1 else [])
        with patch.object(Provider,'_invoke',side_effect=invoke):
            result=Provider('codex').generate_document(CONTEXT,SOURCE,CONTROLS,[],[PREFERENCE],document_decisions=self.snapshot)
        self.assertEqual(result['revision_passes'],2)
        self.assertEqual(reviews,['unchanged_passage','changed_passage'])
        self.assertTrue(all('article_keep_decisions' not in p for t,p in calls if t=='document'))

    def test_excessive_context_fails_visible_without_silently_dropping_choices(self):
        snap=copy.deepcopy(self.snapshot)
        snap['items']=[copy.deepcopy(snap['items'][0]) for _ in range(30)]
        for index,item in enumerate(snap['items']):
            item['id']='decision-%s'%index; item['reason']='x'*1000
        with patch.object(Provider,'_invoke') as invoke:
            result=Provider('codex').check_document(CONTEXT,SOURCE,DRAFT,CONTROLS,[],[PREFERENCE],document_decisions=snap)
        invoke.assert_not_called()
        self.assertEqual(result['review']['status'],'not_checked')
        self.assertEqual(result['status'],'review_required')
        self.assertTrue(result['warnings'])

    def test_policy_is_voice_review_only_and_preserves_distinct_conflicts(self):
        prompt=_prompt('document_review',{})
        for phrase in ('scope=unchanged_passage','scope=changed_passage','historical context',
                       'NEVER authorize a meaning change','independent preference violation','not a new preference violation'):
            self.assertIn(phrase,prompt)
        for task in ('document','document_meaning','draft'):
            self.assertNotIn('Article-scoped keep decisions',_prompt(task,{}))


if __name__=='__main__':unittest.main()
