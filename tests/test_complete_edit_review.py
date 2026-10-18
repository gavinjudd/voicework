"""Synthetic regressions for complete, bounded edit assessment."""
import copy
import unittest
from unittest.mock import patch

from voicework.documents import _review_units, validate_meaning_result
from voicework.meaning import MAX_BATCH_CHANGE_CHARS, MAX_MEANING_CHANGES, meaning_batches, plan_meaning_changes
from voicework.provider import Provider, _prompt, _schema


class CompleteEditReviewTests(unittest.TestCase):
    def pair(self, count=60):
        source = '\n\n'.join('We cannot inspect route %d.' % i for i in range(count))
        return source, source.replace('cannot', "can't")

    def check(self, source, draft, mutate=None):
        self.calls = []
        def call(task, payload):
            self.calls.append((task, copy.deepcopy(payload)))
            result = {'meaning_checks': [{'change_id': r['change_id'], 'verdict': 'preserved', 'dimension': 'none',
                       'reason': 'The same inability and actor are retained.'} for r in payload['meaning_change_requests']], 'warnings': []}
            if task == 'document_review':
                result.update(content_issues=[], voice_issues=[], voice_aspect_checks=[])
            if mutate:
                mutate(task, payload, result)
            return result
        with patch.object(Provider, '_invoke', side_effect=call):
            return Provider('codex').check_document({'channel': 'article'}, source, draft, {'register': 'auto'}, [],
                                                  [{'id': 'synthetic', 'is_language_lesson': True, 'enabled': True,
                                                    'context': {'channel': 'article'}, 'dimension': 'diction',
                                                    'guidance': 'Use clear prose.', 'preferred': '', 'avoided': ''}])

    def test_all_sixty_edits_get_one_verdict_and_resolve_lexical_alerts(self):
        checks = self.check(*self.pair())
        report = checks['meaning_changes']
        self.assertEqual((report['total'], report['reviewed'], report['unreviewed'], report['omitted']), (60, 60, 0, 0))
        self.assertEqual(checks['status'], 'checked')
        self.assertEqual(checks['content']['issues'], [])
        self.assertEqual([task for task, _ in self.calls], ['document_review', 'document_meaning', 'document_meaning'])
        requested = [r['change_id'] for _, p in self.calls for r in p['meaning_change_requests']]
        self.assertEqual(len(requested), len(set(requested)))
        self.assertEqual(requested, ['m%d' % i for i in range(1, 61)])
        signal = next(x for x in checks['content']['signals'] if x['code'] == 'negation_changed')
        self.assertFalse(signal['blocking'])
        self.assertEqual(len(signal['change_ids']), 60)
        self.assertEqual(checks['review']['calls_total'], checks['review']['calls_completed'])

    def test_late_actual_reversal_remains_one_alert_with_explicit_general_support(self):
        source, draft = self.pair(40)
        draft = draft.replace("We can't inspect route 39.", 'We inspect route 39.')
        def mutation(task, payload, result):
            if task == 'document_review':
                self.assertEqual(len(payload['meaning_change_catalog']), 40)
                result['content_issues'] = [{'code': 'position_reversed', 'severity': 'warning', 'section': 's1',
                    'message': 'The last route changes inability into action.', 'change_ids': ['m40']}]
            for check in result['meaning_checks']:
                if check['change_id'] == 'm40':
                    check.update(verdict='changed', dimension='polarity', reason='The writer now says the route is inspected.')
        checks = self.check(source, draft, mutation)
        self.assertEqual(len(checks['content']['issues']), 1)
        issue = checks['content']['issues'][0]
        self.assertEqual((issue['change_id'], issue['label']), ('m40', 'Position reversed'))
        self.assertEqual({e['origin'] for e in issue['evidence']}, {'deterministic', 'semantic_review', 'edit_assessment'})

    def test_late_uncertainty_is_not_cleared_by_earlier_preserved_edits(self):
        def mutation(task, payload, result):
            if task == 'document_meaning':
                result['meaning_checks'][-1].update(verdict='uncertain', dimension='uncertain', reason='The context is ambiguous.')
        checks = self.check(*self.pair(40), mutate=mutation)
        self.assertEqual(checks['status'], 'review_required')
        self.assertEqual(checks['content']['issues'][0]['code'], 'meaning_uncertain')
        self.assertEqual(checks['meaning_changes']['reviewed'], 40)

    def test_missing_late_assessment_remains_explicit(self):
        def mutation(task, payload, result):
            if task == 'document_meaning':
                result['meaning_checks'].pop()
        checks = self.check(*self.pair(40), mutate=mutation)
        self.assertEqual(checks['meaning_changes']['unreviewed'], 1)
        self.assertIn('meaning_not_checked', [x['code'] for x in checks['content']['issues']])
        self.assertEqual(checks['status'], 'review_required')

    def test_failed_supplement_never_claims_complete_review_or_resolves_global_signal(self):
        def mutation(task, payload, result):
            if task == 'document_meaning':
                raise ValueError('Synthetic interruption')
        checks = self.check(*self.pair(40), mutate=mutation)
        self.assertEqual(checks['review']['status'], 'not_checked')
        self.assertEqual(checks['meaning_changes']['unreviewed'], 16)
        self.assertTrue(any(x['code'] == 'negation_changed' and x['blocking'] for x in checks['content']['signals']))
        self.assertEqual(checks['review']['units_completed'], 0)

    def test_supplement_cannot_reassess_an_earlier_batch_id_or_emit_general_findings(self):
        for mode in ('earlier_id', 'duplicate', 'general_findings'):
            def mutation(task, payload, result):
                if task == 'document_meaning':
                    if mode == 'earlier_id': result['meaning_checks'][0]['change_id'] = 'm1'
                    if mode == 'duplicate': result['meaning_checks'] *= 2
                    if mode == 'general_findings': result['voice_issues'] = []
            with self.subTest(mode=mode):
                checks = self.check(*self.pair(40), mutate=mutation)
                self.assertEqual(checks['review']['status'], 'not_checked')
                self.assertEqual(checks['meaning_changes']['reviewed'], 24)

    def test_context_budget_starts_another_batch_without_omitting_requests(self):
        requests = [{'change_id': 'm%d' % i, 'source_context': 'a' * 3500, 'draft_context': 'b' * 3500} for i in range(9)]
        groups = meaning_batches(requests)
        self.assertEqual([x for group in groups for x in group], requests)
        self.assertEqual(len(groups), 3)
        for group in groups:
            self.assertLessEqual(len(group), MAX_MEANING_CHANGES)
            self.assertLessEqual(sum(len(x['source_context']) + len(x['draft_context']) for x in group), MAX_BATCH_CHANGE_CHARS)

    def test_large_changed_sentence_has_full_context_and_exact_ranges(self):
        source = 'We cannot inspect this route ' + 'with the supplied information ' * 180 + '.'
        draft = source.replace('cannot', "can't")
        checks = self.check(source, draft)
        self.assertEqual(checks['meaning_changes']['omitted'], 0)
        self.assertEqual(checks['meaning_changes']['reviewed'], 1)
        request = self.calls[0][1]['meaning_change_requests'][0]
        self.assertEqual(request['source_context'], source)
        self.assertEqual(request['draft_context'], draft)
        self.assertGreater(len(source) + len(draft), 4000)

    def test_no_changes_need_no_supplemental_call(self):
        checks = self.check('The route is closed.', 'The route is closed.')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(checks['meaning_changes']['total'], 0)
        self.assertEqual(checks['review']['edit_batches_total'], 0)

    def test_supplement_schema_is_strict_and_separate_from_voice_and_general_review(self):
        schema = _schema('document_meaning')
        self.assertEqual(set(schema['required']), {'meaning_checks', 'warnings'})
        self.assertFalse(schema['additionalProperties'])
        prompt = _prompt('document_meaning', {})
        self.assertIn('not evidence of changed meaning', prompt)
        self.assertIn('Do not warn about missing authored examples', prompt)
        with self.assertRaises(ValueError):
            validate_meaning_result({'meaning_checks': [], 'warnings': [], 'content_issues': []})

    def test_every_planned_edit_keeps_its_original_unit_and_ranges(self):
        source, draft = self.pair(70)
        batches = _review_units(source, draft)
        plans, omitted = plan_meaning_changes(batches)
        self.assertFalse(omitted)
        for units, requests in zip(batches, plans):
            for group in meaning_batches(requests):
                for request in group:
                    unit = units[request['unit_index']]
                    a, b = request['source_range']; c, d = request['draft_range']
                    self.assertEqual(unit['source'][a:b], request['source_quote'])
                    self.assertEqual(unit['draft'][c:d], request['draft_quote'])
