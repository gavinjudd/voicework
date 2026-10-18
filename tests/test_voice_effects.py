"""Fictional passages only: register and viewpoint are independent judgments."""
import copy
import unittest
from unittest.mock import patch
from voicework.documents import validate_review_result
from voicework.provider import Provider, _schema, _prompt
from voicework.voice_review import validate_effects
from test_voice_classification import CONTEXT, CONTROLS, PREF, response, finding

SOURCE = "The route check mattered a lot more than I'd expected.\n"
FORMAL = 'The route check proved substantially more consequential than I had anticipated.\n'
RULE = dict(PREF, guidance='Prefer natural descriptions over stiff abstractions. Retain source-supplied observations and expectations in this fictional article.')

def effects(register='over_formal', perspective='preserved'):
    return {'register': {'verdict': register, 'reason': 'Assess wording against the supplied register guidance.'},
            'perspective': {'verdict': perspective, 'reason': 'Assess the source reaction independently of diction and pronouns.'}}

def issue(register='over_formal', perspective='preserved', classification='preference_violation', source=SOURCE, draft=FORMAL):
    return finding(classification, source_quote=source.strip(), draft_quote=draft.strip(),
        voice_effects=effects(register, perspective), message='Review this fictional expression against the applicable guidance.')

class VoiceEffectsTests(unittest.TestCase):
    def check(self, item, source=SOURCE, draft=FORMAL):
        with patch.object(Provider, '_invoke', side_effect=lambda task, payload: response(payload, item)):
            return Provider('codex').check_document(CONTEXT, source, draft, CONTROLS, [], [RULE])

    def test_independent_axes_keep_violation_without_inventing_perspective_loss(self):
        for register, perspective in [('over_formal', 'preserved'), ('fits_context', 'reduced'), ('over_formal', 'reduced'), ('over_casual', 'preserved')]:
            with self.subTest(register=register, perspective=perspective):
                result = self.check(issue(register, perspective));item = result['voice']['issues'][0]
                self.assertEqual(item['classification'], 'preference_violation')
                self.assertEqual(item['label'], 'Preference violation')
                self.assertEqual(item['voice_effects'], effects(register, perspective))
                self.assertEqual(result['status'], 'review_required')
        self.assertEqual(self.check(issue())['voice']['issues'][0]['effect_summary'], 'Register: Over-formal wording. Personal perspective: Preserved.')

    def test_pronoun_presence_or_absence_does_not_override_the_assessment(self):
        for draft, perspective in [('I found the route check useful.\n', 'reduced'), ('The route check mattered more than expected.\n', 'preserved')]:
            with self.subTest(draft=draft):
                item = issue('fits_context', perspective, source=SOURCE, draft=draft)
                self.assertEqual(self.check(item, draft=draft)['voice']['issues'][0]['voice_effects']['perspective']['verdict'], perspective)

    def test_optional_effects_and_coverage_do_not_require_changes(self):
        result = self.check(issue('fits_context', 'preserved', 'style_suggestion'))
        self.assertEqual(result['status'], 'checked')
        self.assertEqual(result['voice']['issues'], [])
        self.assertIn('Personal perspective: Preserved', result['voice']['suggestions'][0]['effect_summary'])
        self.assertTrue(result['coverage_notices'])

    def test_legacy_findings_do_not_receive_guessed_effects(self):
        item = issue();item.pop('voice_effects');item = self.check(item)['voice']['issues'][0]
        self.assertNotIn('voice_effects', item)
        self.assertIn('not assessed separately', item['effect_summary'])
        self.assertEqual(item['classification'], 'preference_violation')

    def test_malformed_incomplete_or_extra_effects_invalidate_review(self):
        bad = [None, {}, {'register': effects()['register']}, dict(effects(), proof=True)]
        for axis in ('register', 'perspective'):
            for changes in [{'verdict': 'proven'}, {'reason': ''}, {'reason': ' '}, {'reason': 'x' * 1001}, {'score': 1}]:
                value = effects();value[axis].update(changes);bad.append(value)
        for value in bad:
            with self.subTest(value=value), self.assertRaises(ValueError):validate_effects(value)
            item = issue();item['voice_effects'] = value
            self.assertEqual(self.check(item)['review']['status'], 'not_checked')
        item = issue();item.pop('basis')
        with self.assertRaises(ValueError):validate_review_result(response({}, item))

    def test_native_schema_requires_both_axes_for_findings_and_aspects(self):
        schema = _schema('document_review')['properties']
        for key in ('voice_issues', 'voice_aspect_checks'):
            item = schema[key]['items'];self.assertIn('voice_effects', item['required'])
            axes = item['properties']['voice_effects'];self.assertEqual(set(axes['required']), {'register', 'perspective'})
            self.assertFalse(axes['additionalProperties'])
            for axis in ('register', 'perspective'):
                self.assertEqual(set(axes['properties'][axis]['required']), {'verdict', 'reason'})

    def test_aspect_assessment_keeps_both_axes_in_the_user_finding(self):
        rule = dict(RULE, preferred="mattered a lot more than I'd expected")
        def invoke(task, payload):
            result = response(payload)
            result['voice_aspect_checks'] = [dict(anchor_id=a['anchor_id'], verdict='flattened', source_quote=SOURCE.strip(),
                draft_quote=FORMAL.strip(), reason='The wording is too formal; the expectation survives.', classification='preference_violation',
                preference_ids=[RULE['id']], basis=RULE['guidance'], voice_effects=effects()) for a in payload['voice_aspect_requests']]
            return result
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').check_document(CONTEXT, SOURCE, FORMAL, CONTROLS, [], [rule])
        self.assertEqual(len(result['voice']['issues']), 1)
        self.assertEqual(result['voice']['issues'][0]['voice_effects']['perspective']['verdict'], 'preserved')
        self.assertEqual(result['source_voice_phrases']['aspect_checks'][0]['voice_effects'], effects())

    def test_effects_cannot_clear_independent_numeric_failure(self):
        source, draft = 'We checked 3 routes.\n', 'We checked 4 routes.\n'
        result = self.check(issue('fits_context', 'preserved', 'style_suggestion', source, draft), source, draft)
        self.assertEqual(result['status'], 'review_required')
        self.assertTrue(any(i['code'] == 'numbers_changed' and i['severity'] == 'error' for i in result['content']['issues']))

    def test_repair_receives_the_separate_effects_unchanged(self):
        calls = []
        def invoke(task, payload):
            calls.append((task, copy.deepcopy(payload)))
            if task == 'document':
                return {'blocks': [{'id': b['id'], 'text': SOURCE if payload.get('revision_feedback') else FORMAL} for b in payload['blocks']], 'warnings': []}
            return response(payload, issue() if len(calls) == 2 else None)
        with patch.object(Provider, '_invoke', side_effect=invoke):
            result = Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [RULE])
        self.assertEqual(len(calls), 4)
        self.assertEqual(calls[2][1]['revision_feedback']['voice_issues'][0]['voice_effects'], effects())
        self.assertEqual(result['revision_passes'], 2)
        self.assertEqual(result['text'], SOURCE)

    def test_prompts_separate_register_viewpoint_and_repair(self):
        prompt = _prompt('document_review', {})
        for part in ['two independent assessments', 'First-person pronouns alone', 'surrounding prose retains', "neighboring edit's effect", 'Do not call writing impersonal merely']:
            self.assertIn(part, prompt)
        self.assertIn('do not add first-person pronouns', _prompt('document', {'revision_feedback': {'voice_issues': []}}))
