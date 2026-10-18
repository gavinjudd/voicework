"""Synthetic preferences and writing only; classification is not an accuracy score."""
import copy
import unittest
from unittest.mock import patch
from voicework.documents import validate_review_result
from voicework.provider import Provider, _prompt, _schema

CONTEXT = {'channel': 'technical article'}
CONTROLS = {'register': 'auto', 'revision': 'rewrite', 'length': 'auto'}
SOURCE = 'The lab can start tomorrow.\n'
DRAFT = 'Hey folks, the lab can start tomorrow.\n'
PREF = {'id': 'dispatch-rule', 'is_language_lesson': True, 'enabled': True, 'context': CONTEXT, 'dimension': 'diction',
        'guidance': 'Use a formal dispatch update. Do not add casual greetings such as Hey folks.', 'preferred': '', 'avoided': ''}


def response(payload, finding=None):
    return {'content_issues': [], 'voice_issues': [finding] if finding else [], 'voice_aspect_checks': [], 'warnings': [],
            'meaning_checks': [{'change_id': item['change_id'], 'verdict': 'preserved', 'dimension': 'none',
                                'reason': 'The synthetic greeting retains the same timing and ability.'}
                               for item in payload.get('meaning_change_requests', [])]}


def finding(classification='style_suggestion', **changes):
    result = {'severity': 'warning', 'code': 'greeting_choice', 'message': 'Consider a plainer opening.', 'section': 's1',
              'classification': classification, 'preference_ids': ['dispatch-rule'],
              'source_quote': SOURCE.strip(), 'draft_quote': DRAFT.strip(),
              'basis': 'The supplied dispatch guidance concerns this greeting in this context.'}
    result.update(changes)
    return result


class VoiceClassificationTests(unittest.TestCase):
    def check(self, issue, preferences=None, source=SOURCE, draft=DRAFT):
        with patch.object(Provider, '_invoke', side_effect=lambda t, p: response(p, issue)):
            return Provider('codex').check_document(CONTEXT, source, draft, CONTROLS, [], preferences if preferences is not None else [PREF])

    def test_optional_suggestion_is_separate_and_does_not_require_review(self):
        c = self.check(finding())
        self.assertEqual(c['status'], 'checked')
        self.assertEqual(c['voice']['issues'], [])
        self.assertEqual(c['voice']['suggestions'][0]['classification'], 'style_suggestion')
        self.assertEqual(c['voice']['suggestions'][0]['severity'], 'suggestion')
        self.assertEqual(c['voice']['suggestions'][0]['preference_refs'][0]['guidance'], PREF['guidance'])

    def test_concrete_conflict_remains_a_review_required_violation(self):
        c = self.check(finding('preference_violation'))
        self.assertEqual(c['voice']['status'], 'review_required')
        self.assertEqual(c['voice']['suggestions'], [])
        self.assertEqual(c['voice']['issues'][0]['label'], 'Preference violation')

    def test_only_violation_can_trigger_the_bounded_repair(self):
        for classification, expected in [('style_suggestion', 2), ('uncertain', 2), ('preference_violation', 4)]:
            calls = []
            def invoke(task, payload):
                calls.append((task, copy.deepcopy(payload)))
                if task == 'document':
                    return {'blocks': [{'id': b['id'], 'text': SOURCE if payload.get('revision_feedback') else DRAFT}
                                       for b in payload['blocks']], 'warnings': []}
                return response(payload, finding(classification) if len(calls) == 2 else None)
            with self.subTest(classification=classification), patch.object(Provider, '_invoke', side_effect=invoke):
                result = Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [PREF])
                self.assertEqual(len(calls), expected)
                self.assertEqual(result['text'], SOURCE if expected == 4 else DRAFT)

    def test_unknown_or_out_of_context_reference_invalidates_review(self):
        for ref in ['invented', 'inactive-rule']:
            c = self.check(finding('preference_violation', preference_ids=[ref]))
            self.assertEqual(c['review']['status'], 'not_checked')

    def test_invented_source_or_draft_quote_invalidates_review(self):
        for field in ['source_quote', 'draft_quote']:
            c = self.check(finding('preference_violation', **{field: 'This was never supplied.'}))
            self.assertEqual(c['review']['status'], 'not_checked')

    def test_missing_reference_or_passage_leaves_claimed_violation_uncertain(self):
        for changes in [{'preference_ids': []}, {'source_quote': '', 'draft_quote': ''}]:
            c = self.check(finding('preference_violation', **changes))
            self.assertEqual(c['voice']['issues'][0]['classification'], 'uncertain')
            self.assertEqual(c['voice']['issues'][0]['requested_classification'], 'preference_violation')

    def test_inferred_trait_or_identity_alone_cannot_establish_requirement(self):
        prefs = [dict(PREF, id='taste', is_language_lesson=False, is_preference=True,
                      estimate={'applies': True, 'influence': 1.0}),
                 {'id': 'identity', 'is_identity_profile': True, 'label': 'Synthetic presence',
                  'context': CONTEXT, 'values': {'presence': 'Prefer a calm voice.'}, 'revision': 1}]
        for ref in ['taste', 'identity:identity:presence']:
            c = self.check(finding('preference_violation', preference_ids=[ref]), prefs)
            self.assertEqual(c['voice']['issues'], [])
            self.assertEqual(c['voice']['suggestions'][0]['classification'], 'style_suggestion')
            self.assertIn('supporting', c['voice']['suggestions'][0]['classification_note'])

    def test_explicit_current_register_can_ground_a_conflict(self):
        with patch.object(Provider, '_invoke', side_effect=lambda t, p: response(p, finding('preference_violation', preference_ids=['control:register']))):
            c = Provider('codex').check_document(CONTEXT, SOURCE, DRAFT, dict(CONTROLS, register='formal'), [], [])
        self.assertEqual(c['voice']['issues'][0]['classification'], 'preference_violation')

    def test_legacy_warning_remains_uncertain_and_never_silently_passes(self):
        old = {k: v for k, v in finding().items() if k in ('severity', 'code', 'message', 'section')}
        c = self.check(old)
        self.assertEqual(c['voice']['issues'][0]['classification'], 'uncertain')
        self.assertEqual(c['voice']['status'], 'review_required')
        self.assertIn('legacy', c['voice']['issues'][0]['classification_note'])

    def test_generic_editorial_option_without_preference_is_only_a_suggestion(self):
        c = self.check(finding(preference_ids=[]))
        self.assertEqual(c['voice']['issues'], [])
        self.assertEqual(len(c['voice']['suggestions']), 1)

    def test_style_suggestion_cannot_clear_numeric_or_semantic_safeguards(self):
        source = 'The lab tested 4 valves.\n';draft = 'The lab tested 5 valves.\n'
        c = self.check(finding(source_quote=source.strip(), draft_quote=draft.strip()), source=source, draft=draft)
        self.assertEqual(c['content']['issues'][0]['code'], 'numbers_changed')
        self.assertEqual(c['content']['issues'][0]['severity'], 'error')
        self.assertEqual(c['status'], 'review_required')

    def test_classification_schema_requires_evidence_and_rejects_partial_fields(self):
        schema = _schema('document_review')['properties']
        self.assertIn('classification', schema['voice_issues']['items']['required'])
        self.assertIn('basis', schema['voice_aspect_checks']['items']['required'])
        for changes in [{'classification': 'proven'}, {'preference_ids': ['x', 'x']}, {'basis': ''}, {'draft_quote': 42}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                validate_review_result(response({}, finding(**changes)))
        partial = finding();partial.pop('basis')
        with self.assertRaises(ValueError):validate_review_result(response({}, partial))

    def test_prompt_requires_scope_exceptions_and_optional_treatment(self):
        prompt = _prompt('document_review', {})
        for fragment in ['style_suggestion', 'voice_reference_catalog', 'surrounding prose',
                         'first person in every sentence', 'not mandatory rules', 'not in warnings']:
            self.assertIn(fragment, prompt)

    def test_catalog_is_frozen_eligible_context_only(self):
        seen = []
        def invoke(task, payload):
            seen.append(copy.deepcopy(payload['voice_reference_catalog']))
            return response(payload)
        with patch.object(Provider, '_invoke', side_effect=invoke):
            Provider('codex').check_document(CONTEXT, SOURCE, DRAFT, CONTROLS, [],
                [PREF, dict(PREF, id='personal-rule', context={'channel': 'personal'})])
        self.assertEqual([r['id'] for r in seen[0]], ['dispatch-rule'])
        self.assertNotIn('guidance', seen[0][0])


if __name__ == '__main__':unittest.main()
