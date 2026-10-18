"""Fictional feedback and manuscripts; no private writing fixtures."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from voicework.documents import validate_review_result
from voicework.guidance import writing_inputs
from voicework.preference_learner import PreferenceLearner
from voicework.provider import Provider, _prompt

PHRASE = 'a fair bit more than I figured'
ALTERNATE = "a good deal more than I'd thought"
NEUTRAL = 'more than expected'
SOURCE = '# Trial\n\nWe tested 3 valves, and the cleanup mattered ' + PHRASE + '.\n\n## Later\n\nEverything else stayed the same.\n'
CONTEXT = {'channel': 'technical article'}
CONTROLS = {'register': 'auto', 'length': 'auto', 'revision': 'rewrite'}


def lesson(ident='preferred-source', **changes):
    value = {'id': ident, 'enabled': True, 'is_language_lesson': True, 'context': CONTEXT, 'dimension': 'diction',
        'guidance': 'Keep conversational degree and personal reaction; the same quality can use different words.',
        'preferred': PHRASE, 'avoided': NEUTRAL}
    value.update(changes)
    return value


def echo(task, payload):
    if task == 'document':
        return {'blocks': [{'id': b['id'], 'text': b['text']} for b in payload['blocks']], 'warnings': []}
    return {'content_issues': [], 'voice_issues': [], 'voice_aspect_checks': [], 'warnings': [],
            'meaning_checks': [{'change_id': item['change_id'], 'verdict': 'preserved', 'dimension': 'none',
                                'reason': 'The synthetic rewrite keeps the claim while changing expression.'}
                               for item in payload.get('meaning_change_requests', [])]}


def review(payload, verdict=None):
    result = echo('document_review', payload)
    for request in payload.get('voice_aspect_requests', []):
        unit = next(u for u in payload['units'] if u['section'] == request['section'])
        chosen = verdict or ('preserved' if ALTERNATE in unit['draft'] else 'flattened')
        result['voice_aspect_checks'].append({'anchor_id': request['anchor_id'], 'verdict': chosen,
            'source_quote': PHRASE, 'draft_quote': ALTERNATE if ALTERNATE in unit['draft'] else NEUTRAL,
            'reason': 'Retains colloquial degree and a personal comparison.' if chosen == 'preserved' else
                'The neutral version removes the colloquial degree and personal reaction selected in the lesson.',
            'classification': 'none' if chosen in ('preserved', 'overridden') else 'uncertain' if chosen == 'uncertain' else 'preference_violation',
            'preference_ids': request['lesson_ids'],
            'basis': 'The applicable anchor lesson explicitly preserves this colloquial degree and personal reaction.'})
    return result


class VoicePreservationTests(unittest.TestCase):
    def test_optional_aspect_suggestion_does_not_trigger_repair_or_require_changes(self):
        calls = []
        def invoke(task, payload):
            calls.append(task)
            if task == 'document_review':
                result = review(payload)
                for item in result['voice_aspect_checks']:
                    item.update(classification='style_suggestion', basis='This is an optional presentation choice in the synthetic context.')
                return result
            result = echo(task, payload)
            for block in result['blocks']:block['text'] = block['text'].replace(PHRASE, NEUTRAL)
            return result
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [lesson()])
        self.assertEqual(calls, ['document', 'document_review'])
        self.assertEqual(result['checks']['voice']['issues'], [])
        self.assertEqual(result['checks']['voice']['suggestions'][0]['classification'], 'style_suggestion')
        self.assertEqual(result['checks']['status'], 'checked')

    def test_legacy_flattened_aspect_remains_uncertain_without_automatic_repair(self):
        calls = []
        def invoke(task, payload):
            calls.append(task)
            if task == 'document_review':
                result = review(payload)
                for item in result['voice_aspect_checks']:
                    for key in ('classification', 'preference_ids', 'basis'):item.pop(key)
                return result
            result = echo(task, payload)
            for block in result['blocks']:block['text'] = block['text'].replace(PHRASE, NEUTRAL)
            return result
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [lesson()])
        self.assertEqual(calls, ['document', 'document_review'])
        self.assertEqual(result['checks']['voice']['issues'][0]['classification'], 'uncertain')
        self.assertEqual(result['checks']['status'], 'review_required')

    def test_fresh_expression_preserves_aspect_without_copying_preferred_words(self):
        def invoke(task, payload):
            return review(payload) if task == 'document_review' else echo(task, payload)
        with patch.object(Provider, '_invoke', side_effect=invoke):
            checks = Provider('codex').check_document(CONTEXT, SOURCE, SOURCE.replace(PHRASE, ALTERNATE), CONTROLS, [], [lesson()])
        self.assertEqual(checks['voice']['issues'], [])
        self.assertEqual(checks['voice']['status'], 'checked')
        self.assertEqual(checks['source_voice_phrases']['phrases'][0]['status'], 'changed')
        self.assertEqual(checks['source_voice_phrases']['aspect_checks'][0]['verdict'], 'preserved')

    def test_repair_restores_quality_using_different_words_and_keeps_boundaries(self):
        calls = []
        def invoke(task, payload):
            calls.append((task, copy.deepcopy(payload)))
            if task == 'document_review':return review(payload)
            result = echo(task, payload)
            for block in result['blocks']:
                block['text'] = block['text'].replace(PHRASE, ALTERNATE if payload.get('revision_feedback') else NEUTRAL).strip()
            return result
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [lesson()])
        self.assertEqual([t for t, p in calls], ['document', 'document_review', 'document', 'document_review'])
        self.assertEqual(calls[2][1]['revision_feedback']['voice_issues'][0]['code'], 'source_voice_flattened')
        self.assertEqual({b['section'] for b in calls[2][1]['blocks']}, {'s1'})
        self.assertEqual(result['text'], SOURCE.replace(PHRASE, ALTERNATE))
        self.assertEqual(result['revision_passes'], 2)
        self.assertEqual(result['checks']['voice']['status'], 'checked')

    def test_changed_words_without_aspect_review_are_uncertain_and_do_not_trigger_literal_repair(self):
        calls = []
        def invoke(task, payload):
            calls.append(task)
            result = echo(task, payload)
            if task == 'document':
                for block in result['blocks']:block['text'] = block['text'].replace(PHRASE, ALTERNATE)
            return result
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [lesson()])
        self.assertEqual(calls, ['document', 'document_review'])
        self.assertIn(ALTERNATE, result['text'])
        self.assertEqual(result['checks']['voice']['issues'][0]['code'], 'source_voice_aspect_unreviewed')
        self.assertEqual(result['checks']['voice']['status'], 'review_required')

    def test_unresolved_flattening_retains_initial_draft_and_does_not_loop(self):
        calls = []
        def invoke(task, payload):
            calls.append(task)
            if task == 'document_review':return review(payload)
            result = echo(task, payload)
            for block in result['blocks']:block['text'] = block['text'].replace(PHRASE, NEUTRAL)
            return result
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [lesson()])
        self.assertEqual(calls, ['document', 'document_review', 'document', 'document_review'])
        self.assertEqual(result['revision_passes'], 1)
        self.assertTrue(any('not accepted' in w for w in result['warnings']))
        self.assertEqual(result['checks']['voice']['issues'][0]['code'], 'source_voice_flattened')

    def test_wrong_context_disabled_lessons_and_absent_examples_create_no_anchors(self):
        preferences = [lesson(context={'channel': 'personal'}), lesson('disabled', enabled=False),
            lesson('absent', preferred='a completely different phrase')]
        with patch.object(Provider, '_invoke', side_effect=echo):
            result = Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], preferences)
        self.assertEqual(result['checks']['source_voice_phrases']['phrases'], [])

    def test_a_repair_with_missing_aspect_review_cannot_claim_success(self):
        reviews = []
        def invoke(task, payload):
            if task == 'document_review':
                reviews.append(payload)
                return review(payload) if len(reviews) == 1 else echo(task, payload)
            result = echo(task, payload)
            for block in result['blocks']:
                block['text'] = block['text'].replace(PHRASE, ALTERNATE if payload.get('revision_feedback') else NEUTRAL)
            return result
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [lesson()])
        self.assertEqual(len(reviews), 2)
        self.assertEqual(result['text'], SOURCE.replace(PHRASE, NEUTRAL))
        self.assertEqual(result['revision_passes'], 1)
        self.assertTrue(any('not accepted' in w for w in result['warnings']))

    def test_explicit_formal_register_overrides_conversational_anchors(self):
        with patch.object(Provider, '_invoke', side_effect=echo):
            checks = Provider('codex').check_document(CONTEXT, SOURCE, SOURCE.replace(PHRASE, NEUTRAL), dict(CONTROLS, register='formal'), [], [lesson()])
        self.assertEqual(checks['source_voice_phrases']['phrases'], [])
        self.assertEqual(checks['source_voice_phrases']['formal_override'], 1)
        self.assertEqual(checks['voice']['issues'], [])

    def test_protected_text_and_whole_paragraphs_do_not_become_voice_templates(self):
        source = '# ' + PHRASE + '\n\n```text\n' + PHRASE + '\n```\n\nA new prose passage.\n'
        with patch.object(Provider, '_invoke', side_effect=echo):
            result = Provider('codex').generate_document(CONTEXT, source, CONTROLS, [], [lesson(), lesson('paragraph', preferred='A new prose passage. ' * 15)])
        self.assertEqual(result['checks']['source_voice_phrases']['phrases'], [])

    def test_capitalization_and_wrapping_are_retained_but_moving_words_requires_aspect_review(self):
        with patch.object(Provider, '_invoke', side_effect=echo):
            checks = Provider('codex').check_document(CONTEXT, SOURCE, SOURCE.replace(PHRASE, PHRASE.upper().replace(' MORE ', ' MORE\n')), CONTROLS, [], [lesson()])
        self.assertEqual(checks['source_voice_phrases']['phrases'][0]['status'], 'retained')
        moved = SOURCE.replace(PHRASE, NEUTRAL).replace('Everything else stayed the same.', 'Everything else stayed the same. ' + PHRASE + '.')
        with patch.object(Provider, '_invoke', side_effect=echo):
            checks = Provider('codex').check_document(CONTEXT, SOURCE, moved, CONTROLS, [], [lesson()])
        self.assertEqual(checks['voice']['issues'][0]['section'], 's1')
        self.assertEqual(checks['source_voice_phrases']['aspect_checks'][0]['verdict'], 'uncertain')

    def test_duplicate_lesson_examples_share_one_anchor(self):
        with patch.object(Provider, '_invoke', side_effect=echo):
            result = Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [lesson(), lesson('second', preferred=PHRASE.upper())])
        phrases = result['checks']['source_voice_phrases']['phrases']
        self.assertEqual(len(phrases), 1)
        self.assertEqual(set(phrases[0]['lesson_ids']), {'preferred-source', 'second'})

    def test_copyedit_can_report_flattening_without_rewriting(self):
        with patch.object(Provider, '_invoke', side_effect=lambda t, p:review(p) if t == 'document_review' else echo(t, p)):
            checks = Provider('codex').check_document(CONTEXT, SOURCE, SOURCE.replace(PHRASE, NEUTRAL), dict(CONTROLS, revision='copyedit'), [], [lesson()])
        self.assertEqual(checks['voice']['issues'][0]['code'], 'source_voice_flattened')

    def test_unavailable_review_keeps_uncertainty_visible(self):
        with patch.object(Provider, '_invoke', side_effect=ValueError('Unavailable synthetic review')):
            checks = Provider('codex').check_document(CONTEXT, SOURCE, SOURCE.replace(PHRASE, ALTERNATE), CONTROLS, [], [lesson()])
        self.assertEqual(checks['voice']['status'], 'not_checked')
        self.assertEqual(checks['voice']['issues'][0]['code'], 'source_voice_aspect_unreviewed')

    def test_invalid_quote_unknown_anchor_duplicate_and_invented_override_cannot_pass(self):
        for bad in ('source_quote', 'draft_quote', 'anchor_id', 'duplicate', 'override'):
            def invoke(task, payload):
                result = review(payload)
                check = result['voice_aspect_checks'][0]
                if bad == 'duplicate':result['voice_aspect_checks'].append(dict(check))
                elif bad == 'override':check['verdict'] = 'overridden'
                else:check[bad] = 'invented wording'
                return result
            with self.subTest(bad=bad), patch.object(Provider, '_invoke', side_effect=invoke):
                checks = Provider('codex').check_document(CONTEXT, SOURCE, SOURCE.replace(PHRASE, ALTERNATE), CONTROLS, [], [lesson()])
                self.assertEqual(checks['review']['status'], 'not_checked')
                self.assertEqual(checks['source_voice_phrases']['aspect_checks'][0]['verdict'], 'uncertain')

    def test_persisted_aspect_without_example_tokens_reaches_fresh_writing_and_withdraws(self):
        with tempfile.TemporaryDirectory() as folder:
            database = Path(folder) / 'synthetic.sqlite3'
            learner = PreferenceLearner(database)
            trait = learner.create_trait({'label': 'Conversational degree', 'dimension': 'diction', 'context': {'channel': 'long-form writing'},
                'description': 'Keep the source-supported degree and personal reaction in natural conversational expression. Fresh wording is welcome; do not add emphasis.'})
            event = learner.add_evidence(trait['id'], {'source_kind': 'direct', 'value': 1, 'confirmed': True,
                'attribution': 'Synthetic explicit feedback about personal reaction, not a fixed phrase.'})
            pending = learner.add_evidence(trait['id'], {'source_kind': 'direct', 'value': -1, 'confirmed': False,
                'attribution': 'An unreviewed alternative interpretation.'})
            reloaded = PreferenceLearner(database)
            context, evidence, prefs, report = writing_inputs([], [], [], [], CONTEXT, reloaded)
            self.assertEqual(len(prefs), 1)
            self.assertEqual(report['preference_ids'], [trait['id']])
            self.assertNotIn(pending['id'], prefs[0]['evidence_ids'])
            captured = []
            def invoke(task, payload):
                captured.append(payload)
                return echo(task, payload)
            fresh = 'The repair took a lot longer than I thought it would.\n'
            with patch.object(Provider, '_invoke', side_effect=invoke):
                Provider('codex').generate_document(context, fresh, CONTROLS, evidence, prefs)
            self.assertEqual(captured[0]['source_voice_phrases'], [])
            self.assertNotIn(PHRASE, str(captured))
            self.assertIn('natural conversational', captured[0]['learned_preferences'][0]['guidance'])
            self.assertEqual(reloaded.resolve({'channel': 'personal'})['preferences'], [])
            reloaded.review_evidence(event['id'], 'retract', 'Synthetic user withdrawal')
            self.assertEqual(PreferenceLearner(database).resolve(CONTEXT)['preferences'], [])

    def test_malformed_aspect_checks_are_rejected(self):
        for bad in (None, {}, ['not a verdict'], [{'anchor_id': 'x'}]):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_review_result(dict(echo('document_review', {}), voice_aspect_checks=bad))

    def test_prompts_call_for_generalization_and_review_of_qualities_not_literal_words(self):
        self.assertIn('never literal locks', _prompt('document', {'controls': CONTROLS}))
        self.assertIn('none of the original words occurs', _prompt('draft', {}))
        self.assertIn('Different words are preserved', _prompt('document_review', {}))
        self.assertIn('source_voice_flattened', _prompt('document_review', {}))


if __name__ == '__main__':unittest.main()
