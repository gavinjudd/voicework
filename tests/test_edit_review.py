"""Fictional span, evidence-grouping and mixed-edit regressions."""
import unittest
from unittest.mock import patch
from voicework.documents import _review_units, validate_review_result
from voicework.meaning import plan_meaning_changes
from voicework.provider import Provider, _schema


PREFERENCES = [{'is_language_lesson': True, 'guidance': 'Keep direct conversational prose.'}]


def response(payload, verdict='changed', dimension='claim_strength'):
    return {'content_issues': [], 'voice_issues': [], 'voice_aspect_checks': [], 'warnings': [],
            'meaning_checks': [{'change_id': item['change_id'], 'verdict': verdict, 'dimension': dimension,
                                'reason': 'The observation is strengthened into a capability limit.'}
                               for item in payload.get('meaning_change_requests', [])]}


class EditReviewTests(unittest.TestCase):
    def check(self, source, draft, invoke=None):
        def call(task, payload):
            result = invoke(payload) if invoke else response(payload)
            return {key: result[key] for key in ('meaning_checks', 'warnings')} if task == 'document_meaning' else result
        with patch.object(Provider, '_invoke', side_effect=call):
            return Provider('codex').check_document({'channel': 'technical article'}, source, draft, {'register': 'auto'}, [], PREFERENCES)

    def test_three_overlapping_signals_become_one_alert_with_all_evidence(self):
        def duplicate(payload):
            result = response(payload)
            result['content_issues'] = [{'severity': 'warning', 'code': 'capacity_limit_added',
                'message': 'The observed result becomes the maximum achievable.', 'section': payload['units'][0]['section'],
                'change_ids': [payload['meaning_change_requests'][0]['change_id']]}]
            return result
        checks = self.check('The search returned one note.', 'The search could return only one note.', duplicate)
        self.assertEqual(len(checks['content']['issues']), 1)
        issue = checks['content']['issues'][0]
        self.assertEqual(issue['label'], 'Claim strength changed')
        self.assertEqual({s['origin'] for s in issue['evidence']}, {'deterministic', 'semantic_review', 'edit_assessment'})
        self.assertEqual(len(checks['content']['signals']), 3)

    def test_numeric_guard_errors_survive_a_preserved_semantic_judgment(self):
        checks = self.check('The lab tested 4 valves.', 'The lab tested 5 valves.', lambda p: response(p, 'preserved', 'none'))
        self.assertEqual(len(checks['content']['issues']), 1)
        issue = checks['content']['issues'][0]
        self.assertEqual(issue['code'], 'numbers_changed')
        self.assertEqual(issue['severity'], 'error')
        self.assertEqual(issue['label'], 'Numeric text changed')
        self.assertTrue({'numbers_changed', 'section_numbers_changed'} <= {s['code'] for s in issue['evidence']})

    def test_decimal_and_formatted_numeric_changes_keep_complete_tokens(self):
        checks = self.check('The trial saved 1,234.50%.', 'The trial saved 1,234.60%.', lambda p: response(p, 'changed', 'quantity'))
        self.assertEqual(len(checks['content']['issues']), 1)
        item = checks['meaning_changes']['checks'][0]
        self.assertEqual(item['source_quote'], '1,234.50%')
        self.assertEqual(item['draft_quote'], '1,234.60%')
        self.assertEqual(checks['content']['issues'][0]['label'], 'Quantity changed')

    def test_numeric_guards_remain_attributed_across_multiple_edit_batches(self):
        source = 'The trial handled 4 requests. The separate lab tested 6 valves.'
        draft = 'The trial handled 5 requests. The separate lab tested 7 valves.'
        with patch('voicework.meaning.MAX_MEANING_CHANGES', 1):
            checks = self.check(source, draft, lambda p: response(p, 'changed', 'quantity'))
        self.assertEqual(checks['meaning_changes']['omitted'], 0)
        self.assertEqual(len(checks['content']['issues']), 2)
        self.assertTrue(all(item['code'] == 'numbers_changed' and item.get('change_id') and item['severity'] == 'error'
                            for item in checks['content']['issues']))

    def test_mixed_paragraph_has_separate_preserved_and_changed_edits(self):
        source = 'We cannot open this file. The search returned one note.'
        draft = "We can't open this file. The search could return only one note."
        def mixed(payload):
            result = response(payload)
            for check, request in zip(result['meaning_checks'], payload['meaning_change_requests']):
                if request['source_quote'] == 'cannot':
                    check.update(verdict='preserved', dimension='none', reason='The same inability is retained.')
            return result
        checks = self.check(source, draft, mixed)
        edits = checks['meaning_changes']['checks']
        self.assertEqual(len(edits), 2)
        self.assertEqual([e['verdict'] for e in edits], ['preserved', 'changed'])
        self.assertEqual([(e['source_quote'], e['draft_quote']) for e in edits], [('cannot', "can't"), ('returned', 'could return only')])
        self.assertEqual(len(checks['content']['issues']), 1)
        self.assertEqual(checks['content']['issues'][0]['change_id'], edits[1]['change_id'])

    def test_independent_changes_in_one_sentence_are_assessed_separately(self):
        source = 'We cannot send the draft, and we may review the route.'
        draft = "We can't send the draft, and we will review the route."
        plans, omitted = plan_meaning_changes(_review_units(source, draft))
        edits = [e for p in plans for e in p]
        self.assertEqual([(e['source_quote'], e['draft_quote']) for e in edits], [('cannot', "can't"), ('may', 'will')])
        self.assertEqual(omitted, {})
        for edit in edits:
            self.assertEqual(edit['source_context'], source)
            self.assertEqual(edit['draft_context'], draft)

    def test_duplicate_wording_at_distinct_locations_is_not_collapsed(self):
        source = 'We may review the route. We may review the plan.'
        draft = 'We will review the route. We will review the plan.'
        checks = self.check(source, draft, lambda p: response(p, 'changed', 'commitment'))
        self.assertEqual(len(checks['content']['issues']), 2)
        self.assertEqual(len({item['change_id'] for item in checks['content']['issues']}), 2)

    def test_unknown_links_make_review_incomplete_instead_of_hiding_a_finding(self):
        def unknown(payload):
            result = response(payload)
            result['content_issues'] = [{'severity': 'error', 'code': 'actor_changed', 'message': 'The actor changed.',
                                        'section': '', 'change_ids': ['not-this-batch']}]
            return result
        checks = self.check('The search returned one note.', 'The search could return only one note.', unknown)
        self.assertEqual(checks['review']['status'], 'not_checked')
        self.assertEqual(checks['content']['issues'][0]['code'], 'meaning_not_checked')

    def test_conflicting_assessments_produce_one_visible_unresolved_alert(self):
        def conflict(payload):
            result = response(payload, 'preserved', 'none')
            result['content_issues'] = [{'severity': 'warning', 'code': 'obligation_added', 'message': 'Possibility becomes a promise.',
                'section': '', 'change_ids': [payload['meaning_change_requests'][0]['change_id']]}]
            return result
        checks = self.check('We may review it.', 'We will review it.', conflict)
        issue = checks['content']['issues'][0]
        self.assertEqual(len(checks['content']['issues']), 1)
        self.assertEqual(issue['code'], 'meaning_review_conflict')
        self.assertTrue(issue['conflicting_assessments'])
        self.assertEqual(checks['status'], 'review_required')

    def test_unattributed_independent_findings_are_not_fuzzily_merged(self):
        def unrelated(payload):
            result = response(payload, 'preserved', 'none')
            result['content_issues'] = [{'severity': 'warning', 'code': 'check_actor', 'message': message, 'section': '', 'change_ids': []}
                for message in ('Check the first actor.', 'Check the second actor.')]
            return result
        checks = self.check('We cannot review it.', "We can't review it.", unrelated)
        self.assertEqual(len(checks['content']['issues']), 2)

    def test_source_ranges_are_exact_for_unicode_crlf_insertions_and_deletions(self):
        source = 'Café 🌱 cannot proceed.\r\n\r\nWe may review the note.'
        draft = "Café 🌱 can't proceed.\r\n\r\nWe will promptly review the note."
        batches = _review_units(source, draft)
        plans, _ = plan_meaning_changes(batches)
        for units, requests in zip(batches, plans):
            for edit in requests:
                unit = units[edit['unit_index']]
                a, b = edit['source_range'];c, d = edit['draft_range']
                self.assertEqual(unit['source'][a:b], edit['source_quote'])
                self.assertEqual(unit['draft'][c:d], edit['draft_quote'])
                self.assertIn(edit['source_context'], source)
                self.assertIn(edit['draft_context'], draft)

    def test_related_sentence_reconstruction_has_explicit_combined_scope(self):
        source = 'Most of the lessons came from observing the failures.'
        draft = 'Observing the failures taught us most of the lessons.'
        plans, _ = plan_meaning_changes(_review_units(source, draft))
        edits = [e for batch in plans for e in batch]
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0]['assessment_scope'], 'combined')

    def test_semantic_dimension_is_required_by_provider_and_validated_when_supplied(self):
        schema = _schema('document_review')
        self.assertIn('dimension', schema['properties']['meaning_checks']['items']['required'])
        self.assertIn('change_ids', schema['properties']['content_issues']['items']['required'])
        for verdict, dimension in [('changed', 'negation'), ('preserved', 'actor'), ('changed', 'none')]:
            result = response({'meaning_change_requests': [{'change_id': 'm1'}]}, verdict, dimension)
            with self.subTest(verdict=verdict, dimension=dimension), self.assertRaises(ValueError):
                validate_review_result(result)

    def test_legacy_dimensionless_assessment_has_neutral_label(self):
        def legacy(payload):
            result = response(payload)
            for item in result['meaning_checks']:
                item.pop('dimension')
            return result
        checks = self.check('We may review it.', 'We will review it.', legacy)
        self.assertEqual(checks['content']['issues'][0]['label'], 'Meaning changed')
        self.assertNotIn('Negation wording', checks['content']['issues'][0]['message'])

    def test_long_paragraph_with_short_local_edit_needs_no_truncated_review(self):
        source = 'We may review it. ' + 'The other details remain the same. ' * 200
        checks = self.check(source, source.replace('may', 'will'), lambda p: response(p, 'changed', 'commitment'))
        self.assertEqual(len(checks['meaning_changes']['checks']), 1)
        self.assertEqual(checks['meaning_changes']['omitted'], 0)
        self.assertEqual(checks['meaning_changes']['checks'][0]['source_quote'], 'may')
