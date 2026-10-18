import copy
import unittest

from voicework.document_notices import coverage_notices, validate_coverage_codes
from voicework.guidance import writing_inputs
from voicework.learning import retrieve, validate_context
from voicework.provider import _payload, _prompt
from voicework.sample_selection import select_samples, validate_references, provider_samples

ARTICLE = validate_context({'channel': 'technical article', 'audience': 'technical readers'})


def sample(ident='note', **changes):
    return {'id': ident, 'title': 'Synthetic engineering note', 'text': 'The queue kept growing. That is why I tried batching. ' + ident,
            'status': 'confirmed', 'held_out': False, 'created_at': '2026-01-01',
            'context': validate_context({'channel': 'work', 'audience': 'peer', 'intent': 'explain a process'}), **changes}


REF = [{'id': 'note', 'aspect': 'Direct causal explanation; keep the article register.'}]


class AuthoredReferenceTests(unittest.TestCase):
    def test_empty_match_is_explained_without_automatic_broadening(self):
        samples = [sample(), sample('held', held_out=True), sample('ai', status='assisted')]
        context, evidence, _, report = writing_inputs(samples, [], [], [], ARTICLE)
        self.assertEqual(evidence, [])
        self.assertEqual(report['sample_selection']['confirmed_non_holdout'], 1)
        self.assertEqual(report['sample_selection']['automatic_match_count'], 0)
        self.assertEqual(report['sample_selection']['automatic_reasons'], {'purpose_unknown': 1, 'held_out': 1, 'not_confirmed': 1})
        self.assertEqual(context, ARTICLE)
        self.assertNotIn('reference_candidates', report)

    def test_named_reference_reaches_payload_without_relabeling_or_training(self):
        samples = [sample()]; original = copy.deepcopy(samples)
        context, evidence, preferences, report = writing_inputs(samples, [], [], [], ARTICLE, authored_references=REF)
        payload = _payload(context, evidence, preferences)
        actual = payload['authored_examples'][0]
        self.assertEqual(actual['id'], 'note')
        self.assertEqual(actual['text'], samples[0]['text'])
        self.assertEqual(actual['context'], samples[0]['context'])
        self.assertEqual(actual['selection']['aspect'], REF[0]['aspect'])
        self.assertTrue(actual['selection']['cross_context'])
        self.assertEqual(samples, original)
        self.assertEqual(retrieve(samples, ARTICLE), [])
        self.assertEqual(report['counts']['authored_examples'], 1)
        self.assertEqual(report['sample_selection']['automatic_selected_ids'], [])
        self.assertEqual(report['sample_selection']['document_references'][0]['context_match']['reason'], 'channel_mismatch')
        self.assertEqual([n['code'] for n in coverage_notices(payload)], ['cross_context_examples'])
        validate_coverage_codes({'coverage_notice_codes': ['cross_context_examples']}, payload)
        self.assertIn('References cannot authorize weakened substantiation', _prompt('document', payload))

    def test_hard_provenance_boundaries_survive_explicit_selection(self):
        for changes in ({'status': 'pending'}, {'status': 'assisted'}, {'status': 'rejected'}, {'held_out': True},
                        {'context': {'channel': 'work', 'audience': 'writing assistant'}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                select_samples([sample(**changes)], ARTICLE, REF)
        with self.assertRaises(ValueError):
            select_samples([], ARTICLE, REF)

    def test_provider_rechecks_authorship_holdout_and_target_scope(self):
        evidence, _ = select_samples([sample()], ARTICLE, REF)
        for changes in ({'status': 'pending'}, {'status': 'assisted'}, {'held_out': True},
                        {'context': {'channel': 'work', 'audience': 'AI assistant'}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                provider_samples([{**evidence[0], **changes}], ARTICLE)
        with self.assertRaises(ValueError):
            provider_samples(evidence, {**ARTICLE, 'audience': 'clients'})
        with self.assertRaises(ValueError):
            provider_samples([{**evidence[0], '_document_reference': {'target_context': ARTICLE}}], ARTICLE)

    def test_reference_validation_is_bounded_explicit_and_unique(self):
        for value in (None, {}, 'note', [None], [{'id': 'note'}], [{'id': 'note', 'aspect': '  '}],
                      [{'id': 'note', 'aspect': 'x' * 501}], [{'id': 'x' * 101, 'aspect': 'x'}],
                      REF * 2, [{'id': str(i), 'aspect': 'x'} for i in range(7)],
                      [{'id': 'note', 'aspect': 'x', 'text': 'Untrusted override'}]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_references(value)

    def test_explicit_references_are_not_lost_to_automatic_ranking(self):
        samples = [sample(str(i), context=ARTICLE) for i in range(9)] + [sample()]
        evidence, report = select_samples(samples, ARTICLE, REF)
        self.assertEqual(len(evidence), 6)
        self.assertEqual(evidence[0]['id'], 'note')
        self.assertEqual(len(provider_samples(evidence, ARTICLE)), 6)
        self.assertEqual(report['automatic_match_count'], 9)
        self.assertEqual(len(report['automatic_selected_ids']), 5)

    def test_selecting_an_automatic_match_does_not_duplicate_it(self):
        evidence, report = select_samples([sample(context=ARTICLE)], ARTICLE, REF)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(len(provider_samples(evidence, ARTICLE)), 1)
        self.assertEqual(coverage_notices(_payload(ARTICLE, evidence, [])), [])
        self.assertEqual(report['automatic_match_count'], 1)

    def test_cross_context_notice_cannot_hide_absent_examples(self):
        with self.assertRaises(ValueError):
            validate_coverage_codes({'coverage_notice_codes': ['cross_context_examples']}, {'authored_examples': []})
        self.assertEqual([n['code'] for n in coverage_notices({'authored_examples': []})], ['no_authored_examples', 'no_voice_guidance'])

    def test_ai_topics_do_not_make_a_human_audience_ai_addressed(self):
        evidence, _ = select_samples([sample(context={'channel': 'work', 'audience': 'AI researchers'})], ARTICLE, REF)
        self.assertEqual(len(evidence), 1)

    def test_selected_inputs_are_independent_copies(self):
        samples = [sample()]; evidence, report = select_samples(samples, ARTICLE, REF)
        samples[0]['text'] = 'Changed later'; samples[0]['context']['audience'] = 'Changed'
        self.assertNotEqual(evidence[0]['text'], 'Changed later')
        self.assertNotEqual(report['document_references'][0]['source_context']['audience'], 'Changed')
