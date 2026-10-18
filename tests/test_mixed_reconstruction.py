"""Synthetic mixed-passage cases: joins, relocations and independent claims."""
import unittest
from unittest.mock import patch

from voicework.documents import _review_units
from voicework.edit_spans import changed_edits
from voicework.meaning import plan_meaning_changes
from voicework.provider import Provider


SOURCE = ("When we examined records withheld from calibration, none of the reports exposed the faults.\n\n"
          "Later, in a separate inspection, we selected the manual report. Each choice depended on the seal. "
          "For the closing note, we'd never write a promise.")
DRAFT = ("We examined records used for calibration, and not one of the reports exposed the faults.\n\n"
         "In a later separate inspection, we selected the manual report, and each choice depended on the seal. "
         "We would never close with a promise.")


def plan(source, draft):
    batches = _review_units(source, draft)
    plans, omitted = plan_meaning_changes(batches)
    return batches, [item for group in plans for item in group], omitted


class MixedReconstructionTests(unittest.TestCase):
    def test_sentence_join_does_not_merge_negation_with_changed_test_condition(self):
        _, edits, omitted = plan(SOURCE, DRAFT)
        real = next(e for e in edits if 'withheld' in e['source_quote'])
        harmless = next(e for e in edits if 'not one' in e['draft_quote'])
        closing = next(e for e in edits if 'would never' in e['draft_quote'])
        self.assertEqual(len({e['change_id'] for e in (real, harmless, closing)}), 3)
        self.assertNotIn('none', real['source_quote'])
        self.assertNotIn('withheld', harmless['source_quote'])
        self.assertEqual(omitted, {})
        self.assertIn('records used for calibration', harmless['draft_context'])

    def test_zero_to_no_rephrase_is_separate_from_zero_to_some_change(self):
        source = ('The scanner found zero. It matched one rule and zero records. '
                  'We kept no results, since we did not have evidence.')
        draft = ('The scanner found no faults, and it matched one rule and some records. '
                 "We kept none of the results, since we didn't have evidence.")
        _, edits, omitted = plan(source, draft)
        harmless = next(e for e in edits if 'no faults' in e['draft_quote'])
        real = next(e for e in edits if 'some' in e['draft_quote'])
        contraction = next(e for e in edits if "didn't" in e['draft_quote'])
        self.assertEqual(len({e['change_id'] for e in (harmless, real, contraction)}), 3)
        self.assertEqual(omitted, {})

    def test_sentence_split_also_keeps_rephrases_and_reversals_separate(self):
        source = 'We cannot open the vault, and its owner has never approved the transfer.'
        draft = "We can't open the vault. Its owner has approved the transfer."
        _, edits, omitted = plan(source, draft)
        harmless = next(e for e in edits if 'cannot' in e['source_quote'])
        real = next(e for e in edits if 'never' in e['source_quote'])
        self.assertNotEqual(real['change_id'], harmless['change_id'])
        self.assertEqual(omitted, {})

    def test_reordered_negative_clause_stays_together_and_away_from_quantity(self):
        source = ('The sample contained zero minerals. We flagged all the damaged pieces, '
                  'and we did not end up shipping any of them.')
        draft = ('The sample contained some minerals, and we ended up shipping none of the damaged pieces, '
                 'all of which we had flagged.')
        _, edits, omitted = plan(source, draft)
        quantity = next(e for e in edits if 'some' in e['draft_quote'])
        negative = next(e for e in edits if 'none' in e['draft_quote'])
        self.assertNotEqual(quantity['change_id'], negative['change_id'])
        self.assertIn('not end up shipping', negative['source_quote'])
        self.assertEqual(negative['assessment_scope'], 'combined')
        self.assertEqual(omitted, {})

    def test_relocated_claim_is_not_split_into_independent_deletion_and_addition(self):
        source = 'We inspected the damaged seal, and then we documented the findings.'
        draft = 'After documenting the findings, we inspected the damaged seal.'
        edits = list(changed_edits(source, draft))
        self.assertEqual(len(edits), 1)
        self.assertEqual(edits[0][-1], 'combined')

    def test_only_function_words_do_not_join_distant_edits(self):
        source = 'We had no maps. The crew delivered the sealed box to us, but we never opened it.'
        draft = 'No maps were available to us, and the crew delivered the sealed box. We opened it.'
        _, edits, _ = plan(source, draft)
        harmless = next(e for e in edits if 'maps' in e['source_quote'])
        real = next(e for e in edits if 'never' in e['source_quote'])
        self.assertNotEqual(harmless['change_id'], real['change_id'])

    def test_ranges_and_marker_coverage_survive_unicode_crlf_and_repeated_clauses(self):
        source = 'Café 🌱 cannot proceed.\r\n\r\n' + SOURCE + '\r\n\r\n' + SOURCE
        draft = "Café 🌱 can't proceed.\r\n\r\n" + DRAFT + '\r\n\r\n' + DRAFT
        batches, edits, omitted = plan(source, draft)
        self.assertEqual(omitted, {})
        self.assertEqual(len(edits), len({e['change_id'] for e in edits}))
        for edit in edits:
            unit = batches[0][edit['unit_index']]
            a, b = edit['source_range']; c, d = edit['draft_range']
            self.assertEqual(unit['source'][a:b], edit['source_quote'])
            self.assertEqual(unit['draft'][c:d], edit['draft_quote'])
            self.assertIn(edit['source_quote'], edit['source_context'])
            self.assertIn(edit['draft_quote'], edit['draft_context'])

    def test_long_sentence_does_not_force_independent_edits_together(self):
        source = 'We cannot proceed, ' + 'given the details supplied by the field team, ' * 100 + 'and we may leave.'
        draft = source.replace('cannot', "can't").replace('may leave', 'will leave')
        _, edits, omitted = plan(source, draft)
        self.assertEqual([(e['source_quote'], e['draft_quote']) for e in edits], [('cannot', "can't"), ('may', 'will')])
        self.assertEqual(omitted, {})

    def test_more_than_24_mixed_edits_receive_distinct_verdicts_and_one_alert_per_change(self):
        def sites(text):
            return '\n\n'.join('# Site %d\n\n%s' % (i, text.replace('records', 'station %d records' % i)
                .replace('inspection', 'inspection at station %d' % i)) for i in range(8))
        source, draft = sites(SOURCE), sites(DRAFT)
        calls = []

        def invoke(task, payload):
            calls.append((task, payload))
            checks = []
            for edit in payload['meaning_change_requests']:
                changed = 'withheld' in edit['source_quote']
                checks.append({'change_id': edit['change_id'], 'verdict': 'changed' if changed else 'preserved',
                               'dimension': 'condition' if changed else 'none',
                               'reason': 'Records become calibration data.' if changed else 'This local rephrase retains its claim in context.'})
            result = {'meaning_checks': checks, 'warnings': []}
            if task == 'document_review':
                result.update(content_issues=[], voice_issues=[], voice_aspect_checks=[])
            return result

        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').check_document({'channel': 'article'}, source, draft, {}, [],
                [{'is_language_lesson': True, 'guidance': 'Use straightforward prose.'}])
        report = result['meaning_changes']
        self.assertGreater(report['total'], 24)
        self.assertEqual(report['reviewed'], report['total'])
        self.assertEqual((report['unreviewed'], report['omitted']), (0, 0))
        self.assertEqual(len(result['content']['issues']), 8)
        self.assertEqual(len({e['change_id'] for e in result['content']['issues']}), 8)
        harmless = [e for e in report['checks'] if 'not one' in e['draft_quote']]
        self.assertEqual(len(harmless), 8)
        self.assertTrue(all(e['verdict'] == 'preserved' for e in harmless))
        self.assertTrue(all(len(p['meaning_change_requests']) <= 24 for _, p in calls))
        self.assertTrue(any(task == 'document_meaning' for task, _ in calls))
        self.assertFalse(any(e['code'] == 'negation_changed' for e in result['content']['issues']))

    def test_missing_harmless_assessment_remains_visible_beside_real_change(self):
        def invoke(task, payload):
            checks = [{'change_id': e['change_id'], 'verdict': 'changed' if 'withheld' in e['source_quote'] else 'preserved',
                       'dimension': 'condition' if 'withheld' in e['source_quote'] else 'none', 'reason': 'A bounded test assessment.'}
                      for e in payload['meaning_change_requests'] if 'not one' not in e['draft_quote']]
            return {'meaning_checks': checks, 'warnings': [], 'content_issues': [], 'voice_issues': [], 'voice_aspect_checks': []}
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').check_document({'channel': 'article'}, SOURCE, DRAFT, {}, [], [])
        self.assertEqual(result['meaning_changes']['unreviewed'], 1)
        self.assertEqual({e['code'] for e in result['content']['issues']}, {'meaning_changed', 'meaning_not_checked'})
