"""Synthetic source-relative meaning reviews; no personal writing fixtures."""
import copy
import unittest
from unittest.mock import patch

from voicework.documents import _review_units, validate_review_result
from voicework.meaning import MAX_MEANING_CHANGES, plan_meaning_changes
from voicework.provider import Provider, _prompt, _schema


CONTEXT = {'channel': 'technical article'}
CONTROLS = {'revision': 'rewrite', 'register': 'auto'}
PREFERENCES = [{'id': 'synthetic', 'is_language_lesson': True, 'guidance': 'Use clear, conversational prose.', 'context': CONTEXT}]


def review_response(payload, verdict='preserved'):
    return {'content_issues': [], 'voice_issues': [], 'voice_aspect_checks': [], 'warnings': [],
            'meaning_checks': [{'change_id': item['change_id'], 'verdict': verdict,
                                'reason': 'The available measurements remain insufficient for either proposal.' if verdict == 'preserved' else 'The revised passage asserts a different obligation or outcome.'}
                               for item in payload.get('meaning_change_requests', [])]}


class MeaningTests(unittest.TestCase):
    def check(self, before, after, verdict='preserved', invoke=None, provider='codex'):
        self.calls = []
        self.tasks = []
        def call(task, payload):
            self.assertIn(task, ('document_review', 'document_meaning'))
            self.calls.append(copy.deepcopy(payload))
            self.tasks.append(task)
            result = invoke(payload) if invoke else review_response(payload, verdict)
            return {key: result[key] for key in ('meaning_checks', 'warnings')} if task == 'document_meaning' else result
        with patch.object(Provider, '_invoke', side_effect=call):
            return Provider(provider).check_document(CONTEXT, before, after, CONTROLS, [], PREFERENCES)

    def test_equivalent_negation_is_a_reviewable_note_not_a_content_warning(self):
        before = 'Neither proposal can be assessed from price alone.\n'
        after = "Price alone can't tell us enough about either proposal.\n"
        checked = self.check(before, after)
        self.assertEqual(checked['content']['issues'], [])
        self.assertEqual(checked['content']['status'], 'checked')
        change = checked['meaning_changes']['checks'][0]
        self.assertEqual(change['verdict'], 'preserved')
        self.assertTrue(change['reviewed'])
        a, b = change['source_range']; c, d = change['draft_range']
        self.assertEqual(change['source_quote'], before[a:b])
        self.assertEqual(change['draft_quote'], after[c:d])
        self.assertEqual(change['source_context'], before.strip())
        self.assertEqual(change['draft_context'], after.strip())
        self.assertEqual(change['assessment_scope'], 'combined')
        self.assertEqual(len(self.calls), 1)

    def test_equivalent_added_negative_and_modal_contraction_get_explicit_reviews(self):
        for before, after in [('I write differently for parents and pupils.', "My writing for parents isn't how I write for pupils."),
                              ('We cannot accept this file.', "We can't accept this file."),
                              ('We will review it.', "We'll review it.")]:
            with self.subTest(after=after):
                checked = self.check(before, after)
                self.assertEqual(checked['content']['issues'], [])
                self.assertTrue(checked['meaning_changes']['checks'])
                self.assertTrue(all(item['verdict'] == 'preserved' for item in checked['meaning_changes']['checks']))

    def test_reversal_qualification_and_observed_result_becoming_capability_limit_stay_flagged(self):
        pairs = [('We did not approve the route.', 'We approved the route.'),
                 ('We may review the route.', 'We will review the route.'),
                 ('The search returned one document.', 'The search could return only one document.')]
        for before, after in pairs:
            with self.subTest(after=after):
                checked = self.check(before, after, 'changed')
                self.assertEqual(checked['content']['issues'][0]['code'], 'meaning_changed')
                self.assertEqual(checked['content']['status'], 'review_required')
                self.assertEqual(checked['meaning_changes']['checks'][0]['verdict'], 'changed')

    def test_moved_negation_is_requested_even_when_global_counts_match(self):
        checked = self.check('We did not approve A. We approved B.', 'We approved A. We did not approve B.', 'changed')
        self.assertTrue(self.calls[0]['meaning_change_requests'])
        self.assertIn('meaning_changed', [item['code'] for item in checked['content']['issues']])

    def test_uncertain_and_legacy_missing_assessments_never_clear_to_clean(self):
        before, after = 'We may review it.', 'We will review it.'
        for response, expected in [(lambda p: review_response(p, 'uncertain'), 'meaning_uncertain'),
                                   (lambda p: {k: v for k, v in review_response(p).items() if k != 'meaning_checks'}, 'meaning_not_checked')]:
            with self.subTest(expected=expected):
                checked = self.check(before, after, invoke=response)
                self.assertIn(expected, [item['code'] for item in checked['content']['issues']])
                self.assertEqual(checked['status'], 'review_required')
                if expected == 'meaning_not_checked':
                    self.assertIn('commitment_changed', [item['code'] for item in checked['content']['signals']])

    def test_each_changed_passage_needs_its_own_assessment(self):
        before = 'We cannot open this file.\n\nWe may review the route.'
        after = "We can't open this file.\n\nWe will review the route."
        def first_only(payload):
            result = review_response(payload)
            result['meaning_checks'] = result['meaning_checks'][:1]
            return result
        checked = self.check(before, after, invoke=first_only)
        self.assertEqual([item['verdict'] for item in checked['meaning_changes']['checks']], ['preserved', 'uncertain'])
        self.assertIn('meaning_not_checked', [item['code'] for item in checked['content']['issues']])

    def test_invalid_or_repeated_ids_do_not_resolve_alerts(self):
        for mode in ('unknown', 'duplicate'):
            def invalid(payload):
                result = review_response(payload)
                if mode == 'unknown':
                    result['meaning_checks'][0]['change_id'] = 'other-batch'
                else:
                    result['meaning_checks'] *= 2
                return result
            with self.subTest(mode=mode):
                checked = self.check('We may review it.', 'We will review it.', invoke=invalid)
                self.assertEqual(checked['review']['status'], 'not_checked')
                self.assertIn('commitment_changed', [item['code'] for item in checked['content']['signals']])

    def test_missing_or_failed_model_keeps_candidates_unassessed(self):
        for provider in ('offline', 'codex'):
            def failure(payload):
                raise ValueError('Synthetic provider failure')
            with self.subTest(provider=provider):
                checked = self.check('We did not agree.', 'We agreed.', invoke=failure, provider=provider)
                self.assertEqual(checked['review']['status'], 'not_checked')
                self.assertIn('negation_changed', [item['code'] for item in checked['content']['signals']])
                self.assertFalse(checked['meaning_changes']['checks'][0]['reviewed'])

    def test_partial_review_never_resolves_global_alerts_using_one_successful_batch(self):
        before = ''.join('We may review route %s. %s\n\n' % (index, 'The supplied details remain available. ' * 23) for index in range(20))
        after = before.replace('may review', 'will review')
        count = [0]
        def incomplete(payload):
            count[0] += 1
            if count[0] == 2:
                raise ValueError('Synthetic interruption')
            return review_response(payload)
        checked = self.check(before, after, invoke=incomplete)
        self.assertGreater(len(_review_units(before, after)), 1)
        self.assertEqual(checked['review']['status'], 'not_checked')
        self.assertIn('commitment_changed', [item['code'] for item in checked['content']['signals']])

    def test_protected_errors_and_independent_semantic_findings_cannot_be_cleared(self):
        def independent(payload):
            result = review_response(payload)
            result['content_issues'] = [{'severity': 'warning', 'code': 'actor_changed', 'message': 'The actor changed.', 'section': ''}]
            return result
        checked = self.check('# Plan\n\nWe may review 2 routes.', '# Renamed\n\nWe will review 3 routes.', invoke=independent)
        codes = {item['code'] for item in checked['content']['issues']}
        self.assertTrue({'heading_changed', 'numbers_changed', 'actor_changed'} <= codes)

    def test_per_call_budgets_cover_all_edits_and_long_context_without_truncation(self):
        before = '\n\n'.join('We may review route %s.' % index for index in range(40))
        after = before.replace('may', 'will')
        checked = self.check(before, after)
        self.assertEqual(len(checked['meaning_changes']['checks']), 40)
        self.assertEqual(checked['meaning_changes']['omitted'], 0)
        self.assertEqual(checked['content']['issues'], [])
        self.assertTrue(all(len(call['meaning_change_requests']) <= MAX_MEANING_CHANGES for call in self.calls))
        self.assertEqual(self.tasks, ['document_review', 'document_meaning'])
        long = 'We may review it ' + 'these details are supplied ' * 110
        checked = self.check(long, long.replace('may', 'will'))
        self.assertEqual(checked['meaning_changes']['omitted'], 0)
        self.assertEqual(len(checked['meaning_changes']['checks']), 1)
        self.assertTrue(checked['meaning_changes']['checks'][0]['reviewed'])

    def test_split_marker_at_review_boundary_is_not_silently_resolved_elsewhere(self):
        batches = [[{'source': 'We may review it.\n\nThis is n', 'draft': 'We will review it.\n\nThis is allowed.', 'section': 's1'}],
                   [{'source': 'ot allowed.', 'draft': '', 'section': 's1'}]]
        _, omitted = plan_meaning_changes(batches)
        self.assertGreater(omitted.get('negation', 0), 0)

    def test_exact_quotes_are_local_and_no_synthetic_draft_claim_becomes_a_quote(self):
        before = 'We may review it.\r\n\r\nAnother supplied detail.'
        after = 'We will review it.\r\n\r\nAnother supplied detail.'
        checked = self.check(before, after)
        for item in checked['meaning_changes']['checks']:
            self.assertIn(item['source_quote'], before)
            self.assertIn(item['draft_quote'], after)
        self.assertEqual(self.calls[0]['authored_examples'], [])

    def test_schema_validation_and_prompt_require_explicit_non_certifying_assessments(self):
        schema = _schema('document_review')
        self.assertIn('meaning_checks', schema['required'])
        good = {'change_id': 'm1', 'verdict': 'preserved', 'reason': 'The same condition applies.'}
        for bad in (None, {}, [dict(good, accurate=True)], [dict(good, verdict='safe')],
                    [dict(good, reason='')], [dict(good, reason='x' * 1001)], [dict(good, change_id=4)]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_review_result(dict(review_response({}), meaning_checks=bad))
        prompt = _prompt('document_review', {})
        self.assertIn('not evidence of changed meaning', prompt)
        self.assertIn('Missing assessments cannot silently clear', prompt)
        self.assertIn('Voice review remains separate', prompt)
