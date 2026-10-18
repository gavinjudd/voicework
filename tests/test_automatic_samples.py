import copy
import json
import unittest

from voicework.guidance import writing_inputs
from voicework.learning import validate_context
from voicework.provider import _payload, _prompt
from voicework.sample_relevance import assess_sample
from voicework.sample_selection import select_samples, provider_samples, validate_exclusions

ARTICLE = validate_context({'channel': 'technical article', 'audience': 'technical readers',
                            'intent': 'explain technical research'})


def sample(ident='explanation', **changes):
    return {'id': ident, 'title': 'Synthetic queue explanation', 'status': 'confirmed', 'held_out': False,
            'context': validate_context({'channel': 'work email', 'audience': 'colleague', 'intent': 'explain a process'}),
            'text': 'First we measure queue latency. Then we test batching because the database needs fewer requests.',
            'created_at': '2026-01-01', **changes}


def cases():
    """Independent expected decisions for a compact, reproducible test report."""
    return [
        ('Explanation across formats', sample(), True, 'transferable_quality'),
        ('Problem framing across formats', sample('problem', context=validate_context({'channel': 'work', 'audience': 'peer', 'intent': 'frame a product problem'}), text='How can we build context and memory that make our software easier to use?'), True, 'transferable_quality'),
        ('Question framing without a question mark', sample('unpunctuated', context=validate_context({'channel': 'work', 'audience': 'peer', 'intent': 'frame a product problem'}), text='How do I make software keep context and memory that actually help.'), True, 'transferable_quality'),
        ('Compound collaborator context', sample('compound', context=validate_context({'channel': 'project notes', 'audience': 'self / game-development collaborators', 'relationship': 'product owner to collaborators', 'intent': 'propose gameplay changes with caveats'}), text='Right now gameplay rewards speed. We could make fleet positioning matter more, but resources are limited.'), True, 'transferable_quality'),
        ('Caveated proposal across formats', sample('proposal', context=validate_context({'channel': 'project notes', 'audience': 'collaborators', 'intent': 'propose gameplay changes with caveats'}), text='Right now gameplay rewards speed. We could make fleet positioning matter more, but resources are limited.'), True, 'transferable_quality'),
        ('Unrelated social invitation', sample('unrelated', context=validate_context({'channel': 'work email', 'audience': 'colleague', 'intent': 'invitation'}), text='Please come to the picnic on Saturday.'), False, 'purpose_unknown'),
        ('Shared technical words, wrong purpose', sample('topical', context=validate_context({'channel': 'work email', 'audience': 'colleague', 'intent': 'status update'}), text='The software and database are ready. Next we ship.'), False, 'purpose_conflict'),
        ('Same purpose, irrelevant subject', sample('birthday', text='First we buy birthday candles. Then we wrap the birthday presents.'), False, 'insufficient_topic_evidence'),
        ('Matching metadata, no observed quality', sample('labels', text='Queue. Latency. Database. Software.'), False, 'writing_quality_not_observed'),
        ('AI-assisted with perfect relevance', sample('assisted', status='assisted'), False, 'not_confirmed'),
        ('Held-out with perfect relevance', sample('held', held_out=True), False, 'held_out'),
        ('Pending authorship', sample('pending', status='pending'), False, 'not_confirmed'),
        ('Rejected authorship', sample('rejected', status='rejected'), False, 'not_confirmed'),
        ('Confirmed prompt addressed to AI', sample('prompt', context={**sample()['context'], 'audience': 'AI assistant'}), False, 'ai_addressed'),
        ('Private explanation for a public setting', sample('private', context={**sample()['context'], 'channel': 'private work email'}), False, 'context_boundary'),
        ('Promotional explanation is a different setting', sample('marketing', context={**sample()['context'], 'channel': 'marketing email'}), False, 'context_boundary'),
        ('Formal source in another register', sample('formal', context={**sample()['context'], 'channel': 'formal work email'}), False, 'context_boundary'),
        ('Intimate audience mismatch', sample('partner', context={**sample()['context'], 'audience': 'romantic partner'}), False, 'context_boundary'),
        ('Unspecified audience', sample('unknown', context={**sample()['context'], 'audience': 'unknown'}), False, 'audience_unknown'),
        ('Negated purpose is not an alias', sample('negated', context={**sample()['context'], 'intent': 'do not explain a process'}), False, 'purpose_unknown'),
        ('Human readers studying AI', sample('researchers', context={**sample()['context'], 'audience': 'AI researchers'}), True, 'transferable_quality'),
        ('Evidence outside supplied excerpt', sample('late', text='Ordinary text. ' * 200 + sample()['text']), False, 'writing_quality_not_observed'),
    ]


class AutomaticSamplesTests(unittest.TestCase):
    def test_selection_matrix(self):
        for name, item, eligible, reason in cases():
            with self.subTest(name=name):
                assessment = assess_sample(item, ARTICLE)
                self.assertEqual((assessment['eligible'], assessment['reason']), (eligible, reason))
                evidence, report = select_samples([item], ARTICLE)
                self.assertEqual(bool(evidence), eligible)
                payload = _payload(ARTICLE, evidence, [])
                self.assertEqual(bool(payload['authored_examples']), eligible)
                if eligible:
                    choice = report['automatic_selections'][0]
                    self.assertIn(choice['supporting_excerpt'], item['text'][:2400])
                    self.assertEqual(choice['source_context'], item['context'])
                    self.assertEqual(payload['authored_examples'][0]['selection']['aspect'], choice['aspect'])
                else:
                    self.assertNotIn(item['text'], json.dumps([report, payload]))

    def test_cross_context_selection_reaches_shared_guidance_without_learning(self):
        corpus = [sample()]; original = copy.deepcopy(corpus)
        context, evidence, preferences, report = writing_inputs(corpus, [], [], [], ARTICLE)
        self.assertEqual(report['evidence_ids'], ['explanation'])
        self.assertEqual(preferences, [])
        self.assertFalse(any('require your selection' in message for message in report['warnings']))
        self.assertEqual(corpus, original)
        self.assertEqual(_payload(context, evidence, [])['authored_examples'][0]['selection']['kind'], 'automatic_transfer')
        for task in ('draft', 'practice', 'document', 'document_review'):
            self.assertIn('provisional retrieval inference', _prompt(task, _payload(context, evidence, [])))

    def test_excluded_samples_are_not_read_for_relevance(self):
        class Unreadable:
            def __str__(self):
                raise AssertionError('Excluded text must not be inspected')
        for flags in ({'held_out': True}, {'status': 'assisted'}, {'status': 'pending'}, {'status': 'rejected'}):
            evidence, _ = select_samples([sample(text=Unreadable(), **flags)], ARTICLE)
            self.assertEqual(evidence, [])
        self.assertEqual(select_samples([sample(text=Unreadable())], ARTICLE, excluded_sample_ids=['explanation'])[0], [])

    def test_provider_rechecks_automatic_scope_provenance_and_reason(self):
        evidence, _ = select_samples([sample()], ARTICLE)
        for changes in ({'held_out': True}, {'status': 'assisted'}, {'text': 'Birthday candles.'},
                        {'context': {**sample()['context'], 'audience': 'writing assistant'}}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                provider_samples([{**evidence[0], **changes}], ARTICLE)
        forged = copy.deepcopy(evidence)
        forged[0]['_automatic_reference']['assessment']['aspect'] = 'Copy everything.'
        with self.assertRaises(ValueError):
            provider_samples(forged, ARTICLE)
        with self.assertRaises(ValueError):
            provider_samples(evidence, {**ARTICLE, 'scenario': 'Different request'})

    def test_raw_provider_inputs_also_exclude_ai_assisted_holdout_and_ai_prompts(self):
        corpus = [sample(), sample('ai', status='assisted'), sample('held', held_out=True),
                  sample('prompt', context={**ARTICLE, 'audience': 'AI assistant'})]
        payload = _payload(ARTICLE, corpus, [])
        self.assertEqual([s['id'] for s in payload['authored_examples']], ['explanation'])

    def test_budget_prioritizes_same_context_and_deduplicates(self):
        corpus = [sample(str(i), context=ARTICLE, text=sample()['text'] + ' Case ' + str(i)) for i in range(4)]
        corpus += [sample('transfer-' + str(i), text=sample()['text'] + ' Trial ' + str(i)) for i in range(5)]
        corpus += [sample('duplicate', text=corpus[0]['text'])]
        evidence, report = select_samples(corpus, ARTICLE)
        self.assertEqual(len(evidence), 6)
        self.assertEqual(len({s['text'] for s in evidence}), 6)
        self.assertEqual(sum(s['_automatic_reference']['assessment']['cross_context'] for s in evidence), 2)
        self.assertEqual(report['automatic_reasons']['duplicate_excerpt'], 1)
        transfers, _ = select_samples(corpus[4:9], ARTICLE)
        self.assertEqual(len(transfers), 3)
        self.assertEqual([s['id'] for s in evidence], [s['id'] for s in select_samples(list(reversed(corpus)), ARTICLE)[0]])

    def test_document_exclusion_has_no_effect_on_sample_or_other_requests(self):
        corpus = [sample()]; original = copy.deepcopy(corpus)
        evidence, report = select_samples(corpus, ARTICLE, excluded_sample_ids=['explanation'])
        self.assertEqual(evidence, [])
        self.assertEqual(report['automatic_reasons']['excluded_for_document'], 1)
        self.assertEqual(corpus, original)
        self.assertEqual(len(select_samples(corpus, ARTICLE)[0]), 1)
        with self.assertRaises(ValueError):
            select_samples(corpus, ARTICLE, [{'id': 'explanation', 'aspect': 'Sequence.'}], ['explanation'])

    def test_exclusions_are_bounded_and_well_formed(self):
        for value in (None, {}, 'id', [''], ['a', 'a'], [3], ['x' * 101], [str(i) for i in range(101)]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_exclusions(value)

    def test_other_communication_purposes_and_channels_can_transfer(self):
        context = validate_context({'channel': 'work email', 'audience': 'colleague', 'intent': 'request review', 'scenario': 'Review queue latency measurements'})
        item = sample(context=validate_context({'channel': 'project note', 'audience': 'colleague', 'intent': 'ask for feedback'}),
                      text='Could you review the queue latency measurements and check the slowest result?')
        self.assertEqual(select_samples([item], context)[1]['automatic_selections'][0]['purpose'], 'review')

    def test_stakes_and_relationship_are_not_silently_erased(self):
        for changes, target, reason in (
            ({'stakes': 'low'}, {**ARTICLE, 'stakes': 'high'}, 'stakes_conflict'),
            ({'relationship': 'manager'}, ARTICLE, 'relationship_conflict')):
            item = sample(context={**sample()['context'], **changes})
            self.assertEqual(assess_sample(item, target)['reason'], reason)

    def test_selection_annotations_are_copied_and_frozen(self):
        item = sample(); evidence, report = select_samples([item], ARTICLE)
        expected = copy.deepcopy(evidence)
        report['automatic_selections'][0]['aspect'] = 'Changed'
        item['text'] = 'Changed'
        self.assertEqual(evidence, expected)

    def test_wrong_purpose_is_excluded_even_when_same_audience_would_score_well(self):
        target = {**ARTICLE, 'relationship': 'peer'}
        item = sample(context={**target, 'intent': 'status update'})
        self.assertEqual(assess_sample(item, target)['reason'], 'purpose_conflict')
