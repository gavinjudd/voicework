"""Synthetic punctuation, relocation and real numeric-change regressions."""
import unittest
from unittest.mock import patch

from voicework.documents import NUMBER, _review_units, deterministic_checks
from voicework.edit_spans import TOKEN
from voicework.meaning import consolidate_content, meaning_report, plan_meaning_changes
from voicework.provider import Provider


class NumericReviewTests(unittest.TestCase):
    def check(self, source, draft, verdict='preserved', dimension='none'):
        def invoke(task, payload):
            result = {'content_issues': [], 'voice_issues': [], 'voice_aspect_checks': [], 'warnings': [],
                      'meaning_checks': [{'change_id': edit['change_id'], 'verdict': verdict,
                                          'dimension': dimension, 'reason': 'Synthetic controlled assessment.'}
                                         for edit in payload.get('meaning_change_requests', [])]}
            return {k: result[k] for k in ('meaning_checks', 'warnings')} if task == 'document_meaning' else result
        with patch.object(Provider, '_invoke', side_effect=invoke):
            return Provider('codex').check_document({'channel': 'article'}, source, draft, {'register': 'auto'}, [],
                [{'id': 'synthetic', 'is_language_lesson': True, 'guidance': 'Use clear prose.'}])

    def test_numeric_and_alignment_tokens_share_punctuation_boundaries(self):
        text = '21, 2042, $1,250, +2.50%, -3.25; −4‰. 1,234.50%'
        expected = ['21', '2042', '1,250', '+2.50%', '-3.25', '−4‰', '1,234.50%']
        self.assertEqual(NUMBER.findall(text), expected)
        tokens = [match.group() for match in TOKEN.finditer(text)]
        self.assertTrue(all(token in tokens for token in expected))
        self.assertEqual(tokens.count(','), 4)

    def test_punctuation_and_date_repositioning_do_not_create_numeric_alarms(self):
        pairs = [('People over 21, including visitors, can enter.', 'Visitors can enter if they are over 21.'),
                 ('In April 2042, the lab opened.', 'The lab opened in April 2042.'),
                 ('The group raised $1,250. All of it funded equipment.', 'The group raised $1,250, and all of it funded equipment.'),
                 ('The rate was +2.50%, according to the report.', 'The report gave a rate of +2.50%.'),
                 ('The reading was -3.25, before recalibration.', 'Before recalibration, the reading was -3.25.')]
        for source, draft in pairs:
            with self.subTest(source=source):
                checks = self.check(source, draft)
                self.assertEqual(checks['content']['issues'], [])
                self.assertTrue(checks['meaning_changes']['checks'])
                self.assertEqual(checks['meaning_changes']['reviewed'], checks['meaning_changes']['total'])

    def test_numbers_remain_complete_in_an_edit_next_to_a_comma(self):
        checks = self.check('The value was 1,234.50%, before adjustment.', 'The value was 1,234.60%, before adjustment.')
        edit = checks['meaning_changes']['checks'][0]
        self.assertEqual((edit['source_quote'], edit['draft_quote']), ('1,234.50%', '1,234.60%'))
        self.assertEqual(len(checks['content']['issues']), 1)
        self.assertEqual(checks['content']['issues'][0]['severity'], 'error')

    def test_real_numeric_differences_stay_errors_despite_preserved_model_verdict(self):
        pairs = [('4', '5'), ('+4', '-4'), ('−4', '4'), ('2.50', '2.05'), ('12%', '12'),
                 ('12%', '12‰'), ('20–30', '20–40'), ('1,250', '1250'), ('4 and 4', '4')]
        for before, after in pairs:
            with self.subTest(before=before, after=after):
                checks = self.check('Recorded values: %s.' % before, 'Recorded values: %s.' % after)
                self.assertTrue(any(i['code'] == 'numbers_changed' and i['severity'] == 'error'
                                    for i in checks['content']['issues']))

    def moved(self, source_count='4', draft_count='5'):
        fixed = 'The device handles estimates. Requests use a standard proof. '
        return ('The age threshold is 18. ' + fixed + 'The trial used %s pumps.' % source_count,
                fixed + 'The age threshold is 18. The trial used %s pumps.' % draft_count)

    def test_balanced_clause_move_does_not_inherit_an_unrelated_numeric_alarm(self):
        source, draft = self.moved()
        checks = self.check(source, draft)
        edits = checks['meaning_changes']['checks']
        self.assertEqual(len(edits), 3)
        self.assertEqual(len(checks['content']['issues']), 1)
        issue = checks['content']['issues'][0]
        self.assertEqual(issue['change_id'], next(e['change_id'] for e in edits if e['source_quote'] == '4'))
        self.assertTrue({'numbers_changed', 'section_numbers_changed'} <= {s['code'] for s in issue['evidence']})

    def test_moved_clause_with_no_numeric_change_has_no_numeric_alarm(self):
        source, draft = self.moved('4', '4')
        checks = self.check(source, draft)
        self.assertEqual(checks['content']['issues'], [])
        self.assertTrue(checks['meaning_changes']['checks'])

    def test_movement_sharing_a_changed_number_retains_an_unattributed_error(self):
        source, draft = self.moved('18', '19')
        checks = self.check(source, draft)
        numeric = [i for i in checks['content']['issues'] if i['code'] == 'numbers_changed']
        self.assertEqual(len(numeric), 1)
        self.assertEqual(numeric[0]['severity'], 'error')
        self.assertNotIn('change_id', numeric[0])

    def test_relocation_across_sections_retains_section_guards(self):
        source = '# North\n\nThe threshold is 18.\n\n# South\n\nThe trial uses 4 pumps.'
        draft = '# North\n\nThe trial uses 4 pumps.\n\n# South\n\nThe threshold is 18.'
        checks = self.check(source, draft)
        self.assertNotIn('numbers_changed', [i['code'] for i in checks['content']['issues']])
        self.assertTrue(any(i['code'] == 'section_numbers_changed' and i['severity'] == 'error'
                            for i in checks['content']['issues']))

    def test_equal_counts_cannot_clear_semantic_actor_swap(self):
        checks = self.check('North received 4 pumps. South received 5 pumps.',
                            'North received 5 pumps. South received 4 pumps.', 'changed', 'actor')
        self.assertTrue(checks['content']['issues'])
        self.assertTrue(all(i['label'] == 'Actor changed' for i in checks['content']['issues']))
        self.assertEqual(checks['status'], 'review_required')

    def test_balanced_move_never_clears_uncertain_assessments(self):
        source, draft = self.moved('4', '4')
        checks = self.check(source, draft, 'uncertain', 'uncertain')
        self.assertTrue(checks['content']['issues'])
        self.assertTrue(all(i['code'] == 'meaning_uncertain' for i in checks['content']['issues']))

    def test_missing_numeric_span_coverage_keeps_the_guard_independent(self):
        source, draft = self.moved()
        batches = _review_units(source, draft)
        plans, omitted = plan_meaning_changes(batches)
        verdicts = {e['change_id']: {'verdict': 'preserved', 'dimension': 'none', 'reason': 'Synthetic.'}
                    for plan in plans for e in plan}
        report = meaning_report(plans, omitted, verdicts, True)
        report['checks'] = [e for e in report['checks'] if e['source_quote'] != '4']
        issues, _ = consolidate_content(deterministic_checks(source, draft), [], report, NUMBER, batches)
        self.assertTrue(any(i['code'] == 'numbers_changed' and i['severity'] == 'error' and not i.get('change_id')
                            for i in issues))

    def test_numeric_attribution_covers_more_than_one_edit_call(self):
        source, draft = self.moved()
        with patch('voicework.meaning.MAX_MEANING_CHANGES', 1):
            checks = self.check(source, draft)
        self.assertEqual(checks['meaning_changes']['reviewed'], 3)
        self.assertEqual(len(checks['content']['issues']), 1)
        self.assertEqual(checks['content']['issues'][0]['code'], 'numbers_changed')
