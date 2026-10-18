"""Synthetic evidence-coverage notices never clear or create writing findings."""
import unittest
from unittest.mock import patch

from voicework.document_notices import coverage_notices, validate_coverage_codes
from voicework.documents import validate_batch_result, validate_review_result
from voicework.provider import Provider, _prompt, _schema
from test_documents import CONTEXT, CONTROLS, EVIDENCE, RULE, SOURCE, echo, violation


def covered(task, payload):
    result = echo(task, payload)
    result['coverage_notice_codes'] = ['no_authored_examples']
    return result


class DocumentCoverageTests(unittest.TestCase):
    def test_automatic_choices_are_frozen_for_generation_and_review_without_repair(self):
        from test_automatic_samples import ARTICLE, sample
        from voicework.sample_selection import select_samples
        corpus = [sample(), sample('held', held_out=True), sample('assisted', status='assisted')]
        evidence, _ = select_samples(corpus, ARTICLE)
        calls = []
        def invoke(task, payload):
            calls.append((task, payload))
            corpus[0]['text'] = 'Changed after selection.'
            return echo(task, payload)
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').generate_document(ARTICLE, SOURCE, CONTROLS, evidence, [])
        self.assertEqual(result['revision_passes'], 1)
        self.assertEqual(result['checks']['status'], 'checked')
        self.assertEqual([n['code'] for n in result['checks']['coverage_notices']], ['cross_context_examples'])
        samples = [payload['authored_examples'] for task, payload in calls if task in ('document', 'document_review')]
        self.assertEqual(len(samples), 2)
        self.assertEqual(samples[0], samples[1])
        self.assertEqual(samples[0][0]['selection']['kind'], 'automatic_transfer')
        self.assertEqual(samples[0][0]['text'], sample()['text'])

    def test_cross_context_reference_is_coverage_only_and_reaches_all_review_calls(self):
        from voicework.sample_selection import select_samples
        sample = {'id': 'synthetic-reference', 'title': 'Synthetic work note', 'text': 'I tried it. The queue kept growing.',
                  'status': 'confirmed', 'held_out': False, 'context': {'channel': 'work', 'audience': 'peer'}}
        context = {'channel': 'technical article'}
        evidence, _ = select_samples([sample], context, [{'id': sample['id'], 'aspect': 'Concrete observation and pacing.'}])
        calls = []
        def invoke(task, payload):
            calls.append((task, payload))
            result = echo(task, payload)
            if task in ('document', 'document_review'): result['coverage_notice_codes'] = ['cross_context_examples']
            return result
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').generate_document(context, SOURCE, CONTROLS, evidence, [])
        self.assertEqual(result['checks']['status'], 'checked')
        self.assertEqual(result['revision_passes'], 1)
        self.assertEqual([n['code'] for n in result['checks']['coverage_notices']], ['cross_context_examples'])
        for task, payload in calls:
            if task in ('document', 'document_review'):
                self.assertEqual(payload['authored_examples'][0]['id'], sample['id'])
                self.assertEqual(payload['authored_examples'][0]['selection']['aspect'], 'Concrete observation and pacing.')

    def check(self, invoke=covered, source=SOURCE, draft=SOURCE, evidence=None, lessons=None):
        with patch.object(Provider, '_invoke', side_effect=invoke):
            return Provider('codex').check_document(CONTEXT, source, draft, CONTROLS,
                evidence or [], [RULE] if lessons is None else lessons)

    def test_notice_alone_keeps_completed_content_and_voice_checks(self):
        checks = self.check()
        self.assertEqual([checks['status'], checks['content']['status'], checks['voice']['status']], ['checked'] * 3)
        self.assertEqual(checks['warnings'], [])
        self.assertEqual(checks['content']['issues'] + checks['voice']['issues'], [])
        self.assertEqual([n['code'] for n in checks['coverage_notices']], ['no_authored_examples'])

    def test_catalog_is_input_derived_even_if_model_reports_no_codes(self):
        calls = []
        def invoke(task, payload):
            calls.append(payload)
            result = echo(task, payload)
            result['coverage_notice_codes'] = []
            return result
        checks = self.check(invoke)
        self.assertEqual(checks['coverage_notices'], calls[0]['coverage_notice_catalog'])
        self.assertEqual(checks['status'], 'checked')

    def test_generation_and_review_coverage_do_not_trigger_repair(self):
        calls = []
        def invoke(task, payload):
            calls.append(task)
            return covered(task, payload)
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [RULE])
        self.assertEqual(calls, ['document', 'document_review'])
        self.assertEqual(result['text'], SOURCE)
        self.assertEqual(result['checks']['status'], 'checked')
        self.assertEqual(result['checks']['warnings'], [])
        self.assertEqual(len(result['checks']['coverage_notices']), 1)

    def test_optional_suggestion_plus_coverage_needs_no_change(self):
        def invoke(task, payload):
            result = covered(task, payload)
            finding = violation(payload, 'active_option', 'An optional active form could also work.')
            finding.update(classification='style_suggestion', basis='The original remains acceptable in this context.')
            result['voice_issues'] = [finding]
            return result
        checks = self.check(invoke)
        self.assertEqual(checks['status'], 'checked')
        self.assertEqual(checks['voice']['issues'], [])
        self.assertEqual(len(checks['voice']['suggestions']), 1)

    def test_preference_conflict_still_requires_voice_review(self):
        def invoke(task, payload):
            result = covered(task, payload)
            result['voice_issues'] = [violation(payload, 'indirect_expression', 'The applicable active-expression preference is unmet.')]
            return result
        checks = self.check(invoke)
        self.assertEqual(checks['status'], 'review_required')
        self.assertEqual(checks['voice']['status'], 'review_required')
        self.assertEqual(checks['content']['status'], 'checked')
        self.assertEqual(checks['voice']['issues'][0]['classification'], 'preference_violation')

    def test_actual_actor_change_still_requires_content_review(self):
        def invoke(task, payload):
            result = covered(task, payload)
            for item in result['meaning_checks']:
                item.update(verdict='changed', dimension='actor', reason='The draft assigns the action to someone else.')
            return result
        checks = self.check(invoke, '# Plan\n\nMira reviewed the draft.\n', '# Plan\n\nNoor reviewed the draft.\n')
        self.assertEqual(checks['status'], 'review_required')
        self.assertEqual(checks['content']['status'], 'review_required')
        self.assertEqual(checks['voice']['status'], 'checked')
        self.assertEqual(checks['content']['issues'][0]['label'], 'Actor changed')

    def test_numeric_guard_survives_notice_and_equivalence_claim(self):
        checks = self.check(draft=SOURCE.replace('12.5%', '15%'))
        self.assertEqual(checks['status'], 'review_required')
        self.assertTrue(any(i['severity'] == 'error' and i['code'] == 'numbers_changed' for i in checks['content']['issues']))

    def test_missing_edit_assessment_remains_actionable(self):
        def invoke(task, payload):
            result = covered(task, payload)
            result['meaning_checks'] = []
            return result
        checks = self.check(invoke, 'We may review it.', 'We will review it.')
        self.assertEqual(checks['status'], 'review_required')
        self.assertTrue(checks['content']['issues'])
        self.assertFalse(checks['meaning_changes']['checks'][0]['reviewed'])

    def test_unresolved_warning_is_not_reclassified_by_its_wording(self):
        warning = 'No authored examples can settle whether this changed qualification is equivalent.'
        def invoke(task, payload):
            result = covered(task, payload)
            result['warnings'] = [warning]
            return result
        checks = self.check(invoke)
        self.assertEqual(checks['status'], 'review_required')
        self.assertEqual(checks['warnings'], [warning])
        self.assertEqual(checks['content']['status'], 'checked')
        self.assertEqual(checks['voice']['status'], 'checked')

    def test_generator_conflict_is_not_cleared_by_review_coverage(self):
        warning = 'The supplied instructions disagree on whether a qualification may be removed.'
        def invoke(task, payload):
            result = covered(task, payload)
            if task == 'document':
                result['warnings'] = [warning]
            return result
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [RULE])
        self.assertEqual(result['checks']['status'], 'review_required')
        self.assertIn(warning, result['checks']['warnings'])

    def test_invalid_review_cannot_become_complete_with_a_notice(self):
        def invoke(task, payload):
            result = covered(task, payload)
            result['content_issues'] = 'not an array'
            return result
        checks = self.check(invoke)
        self.assertEqual(checks['review']['status'], 'not_checked')
        self.assertEqual(checks['status'], 'review_required')
        self.assertEqual(checks['content']['status'], 'not_checked')
        self.assertEqual(checks['voice']['status'], 'not_checked')
        self.assertTrue(checks['warnings'])

    def test_absent_voice_guidance_is_unchecked_not_a_draft_violation(self):
        checks = self.check(lessons=[])
        self.assertEqual(checks['status'], 'not_checked')
        self.assertEqual(checks['voice']['status'], 'not_checked')
        self.assertEqual(checks['voice']['issues'], [])
        self.assertEqual({n['code'] for n in checks['coverage_notices']}, {'no_authored_examples', 'no_voice_guidance'})

    def test_authored_evidence_has_no_missing_example_notice(self):
        checks = self.check(echo, evidence=EVIDENCE)
        self.assertEqual(checks['status'], 'checked')
        self.assertEqual(checks['coverage_notices'], [])

    def test_inapplicable_code_invalidates_review(self):
        checks = self.check(evidence=EVIDENCE)
        self.assertEqual(checks['review']['status'], 'not_checked')
        self.assertEqual(checks['status'], 'review_required')

    def test_unknown_duplicate_or_unstructured_codes_are_rejected(self):
        for value in ['no_authored_examples', [None], ['unknown'], ['no_authored_examples'] * 2, [{'message': 'The changed actor is fine.'}]]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_coverage_codes({'coverage_notice_codes': value})
        with self.assertRaises(ValueError):
            validate_coverage_codes(None)

    def test_legacy_results_remain_parseable_and_warnings_stay_conservative(self):
        legacy = {'content_issues': [], 'voice_issues': [], 'warnings': ['A supplied source conflict needs review.']}
        self.assertEqual(validate_review_result(legacy), legacy)
        checks = self.check(lambda task, payload: legacy)
        self.assertEqual(checks['status'], 'review_required')
        self.assertEqual(checks['warnings'], legacy['warnings'])
        block = {'id': 'b1', 'text': 'A fictional note.'}
        self.assertEqual(validate_batch_result({'blocks': [block], 'warnings': []}, [block]), [block])

    def test_document_schemas_and_prompts_separate_coverage_from_findings(self):
        for task in ('document', 'document_review'):
            schema = _schema(task)
            self.assertIn('coverage_notice_codes', schema['required'])
            self.assertEqual(set(schema['properties']['coverage_notice_codes']['items']['enum']), {'no_authored_examples', 'no_voice_guidance', 'cross_context_examples'})
            prompt = _prompt(task, {})
            self.assertIn('Do not repeat missing authored examples', prompt)
            self.assertIn('never hide them in coverage codes', prompt)
