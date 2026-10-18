"""Synthetic integration checks for the evidence -> guidance -> draft loop."""
import copy
import json
import threading
import time
import unittest
from unittest.mock import patch

import test_server
from test_documents import echo
from test_preference_learner import evidence_packet, trait_packet
from voicework.guidance import writing_inputs
from voicework.mcp import VoiceworkMCP
from voicework.provider import Provider, _payload, _prompt
from voicework.quality import _eligible_preferences


CONTEXT = {"channel": "long-form writing"}


class PreferenceApiTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def create(self, **changes):
        status, data, _ = self.request('POST', '/api/preferences', trait_packet(**changes))
        self.assertEqual(status, 201, data)
        return data['trait']

    def add(self, trait, **changes):
        status, data, _ = self.request('POST', '/api/preferences/' + trait['id'] + '/evidence', evidence_packet(**changes))
        self.assertEqual(status, 201, data)
        return data['evidence']

    def resolve(self, context=None):
        status, data, _ = self.request('POST', '/api/preferences/resolve', {'context': context or CONTEXT})
        self.assertEqual(status, 200, data)
        return data

    def wait_job(self, job):
        for _ in range(200):
            status, current, _ = self.request('GET', '/api/documents/' + job['id'])
            self.assertEqual(status, 200)
            if current['status'] in ('complete', 'failed'):
                return current
            time.sleep(.01)
        self.fail('Document job did not finish')

    def test_reviewed_evidence_reaches_draft_without_mutating_authored_or_legacy_data(self):
        before = self.request('GET', '/api/state')[1]
        trait = self.create()
        event = self.add(trait, confirmed=False)
        self.assertEqual(self.resolve()['preferences'], [])
        result = self.request('POST', '/api/preference-evidence/' + event['id'] + '/confirm', {'reason': 'Reviewed the aspect'})
        self.assertEqual(result[0], 200)
        captured = []
        def generate(context, brief, controls, evidence, preferences):
            captured.append(copy.deepcopy(preferences))
            return {'text': brief, 'provider': 'offline', 'warnings': []}
        with patch.object(self.provider, 'generate_draft', side_effect=generate):
            status, result, _ = self.request('POST', '/api/draft', {'context': {'channel': 'technical article'}, 'brief': 'A fictional cache explanation.'})
        self.assertEqual(status, 200, result)
        self.assertEqual(captured[0][0]['id'], trait['id'])
        self.assertTrue(captured[0][0]['is_preference'])
        self.assertEqual(result['context_report']['counts']['learned_preferences'], 1)
        self.assertEqual(result['context_report']['counts']['feedback'], 0)
        after = self.request('GET', '/api/state')[1]
        for key in ('samples', 'feedback', 'language_lessons', 'corrections', 'evaluations', 'quality_checks'):
            self.assertEqual(before[key], after[key], key)
        self.assertEqual(before['voice_confidence']['validation'], after['voice_confidence']['validation'])
        self.assertEqual(after['voice_confidence']['evidence']['authored_samples'], 0)
        self.assertEqual(after['voice_confidence']['evidence']['learned_preferences'], 1)

    def test_conflict_withdrawal_restore_and_pause_change_guidance(self):
        trait = self.create()
        self.add(trait, value=1, attribution='I prefer this quality here.')
        against = self.add(trait, value=-1, attribution='This independent correction points away from that quality.')
        self.assertFalse(self.resolve()['preferences'])
        self.assertIn('conflicting evidence', self.resolve()['warnings'][0])
        path = '/api/preference-evidence/' + against['id']
        self.assertEqual(self.request('POST', path + '/retract', {'reason': 'That judgment concerned a different aspect'})[0], 200)
        self.assertEqual(len(self.resolve()['preferences']), 1)
        self.assertEqual(self.request('POST', path + '/restore', {'reason': 'Reconsidered the counterexample'})[0], 200)
        self.assertFalse(self.resolve()['preferences'])
        self.request('POST', path + '/retract', {'reason': 'Keep it withdrawn'})
        self.request('PATCH', '/api/preferences/' + trait['id'], {'enabled': False})
        self.assertFalse(self.resolve()['preferences'])
        self.assertEqual(self.request('GET', '/api/preferences/' + trait['id'])[1]['trait']['total_evidence'], 2)

    def test_routes_enforce_origin_token_validation_and_atomic_failure(self):
        packet = trait_packet()
        for headers in ({'X-Voicework-Token': 'wrong'}, {'Origin': 'https://foreign.test'}):
            self.assertEqual(self.request('POST', '/api/preferences', packet, headers=headers)[0], 403)
        self.assertEqual(self.request('GET', '/api/preferences')[1]['traits'], [])
        trait = self.create()
        path = '/api/preferences/' + trait['id']
        for body in ({'description': 'Change the meaning under existing evidence'}, {'enabled': 1}):
            self.assertEqual(self.request('PATCH', path, body)[0], 400)
        for query in ('limit=0', 'offset=-1', 'limit=bad', 'limit=101'):
            self.assertEqual(self.request('GET', path + '/evidence?' + query)[0], 400)
        self.assertEqual(self.request('POST', path + '/evidence', evidence_packet(value=True))[0], 400)
        self.assertEqual(self.request('POST', '/api/preferences/resolve', {'context': CONTEXT, 'unknown': 1})[0], 400)
        self.assertEqual(self.request('HEAD', path)[0], 200)
        self.assertEqual(self.request('GET', '/api/preferences/missing')[0], 404)

    def test_context_and_provenance_are_visible_without_raw_passages_in_exports(self):
        trait = self.create(context={'channel': 'work', 'audience': 'teammates'})
        self.add(trait, source_kind='edit', before_text='UNIQUE RAW SOURCE before.', after_text='UNIQUE RAW SOURCE after.',
                 style_only=True, meaning_preserved=True)
        self.assertFalse(self.resolve(CONTEXT)['preferences'])
        self.assertFalse(self.resolve({'channel': 'work'})['preferences'])
        self.assertEqual(len(self.resolve({'channel': 'work', 'audience': 'my teammates'})['preferences']), 1)
        # The immutable source record remains inspectable locally.
        evidence = self.request('GET', '/api/preferences/' + trait['id'] + '/evidence')[1]['items']
        self.assertIn('UNIQUE RAW SOURCE', evidence[0]['before_text'])
        markdown = self.request('GET', '/api/export')[1]['markdown']
        self.assertIn('Learned preferences', markdown)
        self.assertNotIn('UNIQUE RAW SOURCE', markdown)
        voice = self.request('GET', '/api/export/story-engine?target=team+update&channel=work')[1]['story_voice']
        self.assertEqual(voice['provenance']['counts']['preferences_used'], 1)
        self.assertNotIn('UNIQUE RAW SOURCE', json.dumps(voice))
        self.assertIn('audience', voice['voice_profile']['traits'][0])

    def test_pending_document_job_freezes_preference_before_pause(self):
        trait = self.create()
        self.add(trait)
        gate, entered, captured = threading.Event(), threading.Event(), []
        def generate(context, source, controls, evidence, preferences):
            captured.extend(copy.deepcopy(preferences)); entered.set(); gate.wait(3)
            return {'text': source, 'provider': 'offline', 'warnings': []}
        with patch.object(self.provider, 'generate_document', side_effect=generate):
            status, job, _ = self.request('POST', '/api/documents', {'context': CONTEXT, 'source': 'A fictional source.'})
            self.assertEqual(status, 202)
            self.assertTrue(entered.wait(1))
            self.request('PATCH', '/api/preferences/' + trait['id'], {'enabled': False})
            gate.set()
            completed = self.wait_job(job)
        self.assertEqual(completed['status'], 'complete')
        self.assertEqual(captured[0]['id'], trait['id'])
        self.assertTrue(captured[0]['estimate']['applies'])
        self.assertEqual(completed['result']['context_report']['preference_ids'], [trait['id']])
        self.assertFalse(self.resolve()['preferences'])

    def test_round_and_whole_profile_comparison_use_the_same_learner(self):
        trait = self.create()
        self.add(trait)
        captured = []
        def generate(context, evidence, preferences, focus='mixed'):
            captured.append(preferences)
            return {'options': [{'id': 'a', 'text': 'Fictional A.'}, {'id': 'b', 'text': 'Fictional B.'}], 'provider': 'offline', 'warnings': []}
        with patch.object(self.provider, 'generate_round', side_effect=generate):
            self.assertEqual(self.request('POST', '/api/rounds', {'context': CONTEXT})[0], 201)
        self.assertEqual(captured[0][0]['id'], trait['id'])
        calls = []
        def draft(context, brief, controls, evidence, preferences):
            calls.append(preferences)
            return {'text': brief, 'provider': 'offline', 'warnings': []}
        with patch.object(self.provider, 'generate_draft', side_effect=draft):
            self.assertEqual(self.request('POST', '/api/evaluations', {'context': CONTEXT, 'brief': 'A new fictional case.'})[0], 201)
        self.assertEqual(sorted(len(value) for value in calls), [0, 1])
        self.assertEqual(self.server.preference_learner.get_trait(trait['id'])['total_evidence'], 1)

    def test_correction_check_excludes_evidence_from_the_target_correction(self):
        packet = {'original_text': 'We utilize a chart.', 'revised_text': 'We use a chart.', 'guidance': 'Use ordinary verbs.',
                  'context': CONTEXT, 'scope': 'channel', 'kind': 'style'}
        correction = self.request('POST', '/api/corrections', packet)[1]['correction']
        trait = self.create()
        self.add(trait, source_kind='correction', source_id=correction['id'])
        self.add(trait, source_kind='edit', before_text=packet['original_text'], after_text=packet['revised_text'],
                 style_only=True, meaning_preserved=True, attribution='The same edit entered manually must not leak into its own check.')
        self.assertEqual(len(self.resolve()['preferences']), 1)
        calls = []
        def draft(context, brief, controls, evidence, preferences):
            calls.append(preferences)
            return {'text': brief, 'provider': 'offline', 'warnings': []}
        with patch.object(self.provider, 'generate_draft', side_effect=draft):
            status, result, _ = self.request('POST', '/api/corrections/' + correction['id'] + '/checks', {'context': CONTEXT, 'brief': 'A fresh fictional case.'})
        self.assertEqual(status, 201, result)
        self.assertTrue(all(not item.get('is_preference') for values in calls for item in values))
        self.assertEqual(sorted(len(values) for values in calls), [0, 1])
        # A previously derived comparison cannot smuggle its target lesson back in either.
        linked = self.server.preference_learner.guidance(CONTEXT)[0]
        self.assertEqual(_eligible_preferences([linked], CONTEXT, correction['lesson_id']), [])

    def test_mcp_proposes_pending_evidence_and_exports_identical_scoped_guidance(self):
        trait = self.create()
        mcp = VoiceworkMCP(self.server.store.path.parent)
        def call(name, arguments):
            return mcp.handle({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {'name': name, 'arguments': arguments}})['result']
        packet = {**evidence_packet(), 'trait_id': trait['id']}; packet.pop('confirmed')
        proposed = call('propose_preference_evidence', packet)
        self.assertFalse(proposed['isError'])
        self.assertEqual(proposed['structuredContent']['status'], 'pending')
        self.assertFalse(self.resolve()['preferences'])
        self.assertTrue(call('propose_preference_evidence', {**packet, 'confirmed': True})['isError'])
        ident = proposed['structuredContent']['evidence_id']
        self.request('POST', '/api/preference-evidence/' + ident + '/confirm', {'reason': 'Reviewed the attribution'})
        guidance = call('get_voice_guidance', {'channel': 'technical article'})['structuredContent']
        self.assertEqual(guidance['learned_preferences'][0]['id'], trait['id'])
        self.assertEqual(guidance['examples'], [])
        self.assertEqual(call('list_preferences', {})['structuredContent']['preferences'][0]['id'], trait['id'])

    def test_live_payload_has_separate_soft_guidance_and_document_review_recognizes_it(self):
        trait = self.create()
        self.add(trait)
        context, evidence, preferences, report = writing_inputs([], [], [], [], CONTEXT, self.server.preference_learner)
        payload = _payload(context, evidence, preferences)
        self.assertEqual(len(payload['learned_preferences']), 1)
        self.assertEqual(payload['calibration_feedback'], [])
        self.assertEqual(payload['authored_examples'], [])
        self.assertIn('strong but tentative', _prompt('draft', payload))
        provider = Provider(mode='codex')
        captured = []
        def invoke(task, frozen):
            captured.append(copy.deepcopy(frozen))
            return echo(task, frozen)
        with patch.object(provider, '_invoke', side_effect=invoke):
            result = provider.generate_document(context, 'A fictional document.', {}, evidence, preferences)
        self.assertTrue(result['checks']['voice']['guidance_available'])
        self.assertTrue(all(item['learned_preferences'][0]['id'] == trait['id'] for item in captured))
