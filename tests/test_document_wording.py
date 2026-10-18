"""Fictional article instructions: persistence, native retries, and isolation."""
import copy
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import test_server
from test_document_keep_review import SOURCE, DRAFT, CONTEXT, CONTROLS, PREFERENCE, finding, review
from voicework.document_decisions import DocumentDecisionStore
from voicework.document_wording import DocumentWordingStore, instruction_plan
from voicework.documents import parse_document
from voicework.provider import Provider, _prompt


class ArticleWordingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'profile.sqlite3'
        self.keeps = DocumentDecisionStore(self.path)
        self.store = DocumentWordingStore(self.path)

    def tearDown(self):
        self.temp.cleanup()

    def save(self, **changes):
        values = dict(source=SOURCE, context=CONTEXT, controls=CONTROLS,
                      source_quote='We tested 4 valves.', wording='We tested 4 valves.', reason='Keep this wording in this article.')
        values.update(changes)
        return self.store.correct(**values)

    def snapshots(self):
        return {'document_decisions': self.keeps.resolve(SOURCE, SOURCE, CONTEXT, CONTROLS),
                'document_wording': self.store.resolve(SOURCE, CONTEXT, CONTROLS)}

    def generate(self, invoke, **changes):
        values = self.snapshots(); values.update(changes)
        with patch.object(Provider, '_invoke', side_effect=invoke):
            return Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [PREFERENCE], **values)

    @staticmethod
    def compliant(payload):
        blocks = copy.deepcopy(payload['blocks'])
        for instruction in payload.get('article_wording_instructions', []):
            for block in blocks:
                if block['id'] == instruction['block_id']:
                    block['text'] = block['text'].replace(instruction['source_quote'], instruction['required_text'])
        return {'blocks': [{'id': x['id'], 'text': x['text']} for x in blocks], 'warnings': []}

    def test_save_restore_revoke_and_restart_are_idempotent(self):
        saved = self.save(); self.assertEqual(self.save()['id'], saved['id'])
        self.store = DocumentWordingStore(self.path)
        row = self.store.resolve(SOURCE, CONTEXT, CONTROLS)['items'][0]
        self.assertEqual(row['applicability'], 'applicable'); self.assertEqual(len(row['history']), 1)
        self.store.revoke(saved['id']); self.store.revoke(saved['id'])
        self.assertEqual(self.store.resolve(SOURCE, CONTEXT, CONTROLS)['items'][0]['status'], 'revoked')
        self.assertEqual(self.save()['id'], saved['id'])
        self.assertEqual(len(self.store.resolve(SOURCE, CONTEXT, CONTROLS)['items'][0]['history']), 3)

    def test_validation_leaves_no_partial_record(self):
        for values in ({'source_quote': 'missing'}, {'source_quote': ''}, {'wording': ''}, {'reason': ''},
                       {'wording': 'line\nbreak'}, {'wording': 'control\x01'}, {'wording': ' leading'},
                       {'source': SOURCE + '\nWe tested 4 valves.'}, {'wording': 'The trial'}):
            with self.subTest(values=values), self.assertRaises(ValueError): self.save(**values)
        self.assertEqual(self.store.resolve(SOURCE, CONTEXT, CONTROLS)['items'], [])

    def test_scope_is_recomputed_not_trusted(self):
        self.save(); original = self.store.resolve(SOURCE, CONTEXT, CONTROLS)
        for source, context, controls in ((SOURCE+'Other article.', CONTEXT, CONTROLS),
                 (SOURCE, dict(CONTEXT, audience='investors'), CONTROLS),
                 (SOURCE, CONTEXT, dict(CONTROLS, register='formal'))):
            plan = instruction_plan(source, context, controls, parse_document(source)[0], corrections=original)
            self.assertEqual(plan['items'], []); self.assertEqual(len(plan['skipped']), 1)
        self.assertEqual(self.store.resolve(SOURCE+'Other article.', CONTEXT, CONTROLS)['items'], [])
        self.store.revoke(original['items'][0]['id'])
        snapshot = self.store.resolve(SOURCE, CONTEXT, CONTROLS)
        snapshot['items'][0]['applicability'] = 'applicable'
        self.assertEqual(instruction_plan(SOURCE, CONTEXT, CONTROLS, parse_document(SOURCE)[0], corrections=snapshot)['items'], [])

    def test_initial_generation_receives_keep_and_correction_not_profile_history(self):
        keep = self.keeps.keep(SOURCE, DRAFT, CONTEXT, CONTROLS, finding(), 'Keep this pacing.')
        correction = self.save(); calls = []
        def invoke(task, payload):
            calls.append((task, copy.deepcopy(payload)))
            return self.compliant(payload) if task == 'document' else review(payload)
        result = self.generate(invoke)
        self.assertEqual(result['text'], DRAFT)
        generation = [p for t, p in calls if t == 'document'][0]
        self.assertEqual({x['instruction_id'] for x in generation['article_wording_instructions']}, {keep['id'], correction['id']})
        self.assertNotIn('history', str(generation['article_wording_instructions']))
        self.assertTrue(all(x['status'] == 'retained' for x in result['checks']['article_wording']['items']))
        self.assertEqual(result['checks']['article_wording']['attempts'][0]['unmet'], [])
        self.assertEqual(result['checks']['status'], 'checked')

    def test_noncompliance_gets_one_model_retry_without_substitution(self):
        self.save(wording='We checked 4 valves.'); calls = []
        def invoke(task, payload):
            if task != 'document': return review(payload)
            calls.append(copy.deepcopy(payload))
            if len(calls) == 1: return {'blocks': [{'id': b['id'], 'text': b['text']} for b in payload['blocks']], 'warnings': []}
            return self.compliant(payload)
        result = self.generate(invoke)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['text'], SOURCE.replace('tested', 'checked'))
        self.assertTrue(calls[1]['article_wording_feedback']); self.assertIn('current_draft_blocks', calls[1])
        self.assertEqual(result['checks']['article_wording']['status'], 'checked')
        self.assertEqual(result['checks']['warnings'], [])

    def test_second_miss_is_visible_and_model_output_is_untouched(self):
        self.save(wording='We checked 4 valves.'); calls = []
        def invoke(task, payload):
            if task != 'document': return review(payload)
            calls.append(payload)
            return {'blocks': [{'id': b['id'], 'text': b['text']} for b in payload['blocks']], 'warnings': []}
        result = self.generate(invoke)
        self.assertEqual(len(calls), 2); self.assertEqual(result['text'], SOURCE)
        self.assertEqual(result['checks']['status'], 'review_required')
        self.assertEqual(result['checks']['article_wording']['items'][0]['status'], 'missing')
        self.assertTrue(result['checks']['warnings'])

    def test_failed_retry_preserves_complete_native_draft_and_warning(self):
        self.save(wording='We checked 4 valves.'); calls = []
        def invoke(task, payload):
            if task != 'document': return review(payload)
            calls.append(payload)
            if len(calls) == 2: raise ValueError('Synthetic provider failure')
            return {'blocks': [{'id': b['id'], 'text': b['text']} for b in payload['blocks']], 'warnings': []}
        result = self.generate(invoke)
        self.assertEqual(result['text'], SOURCE); self.assertTrue(result['checks']['warnings'])
        self.assertEqual(result['checks']['article_wording']['attempts'][-1]['status'], 'unavailable_or_invalid')

    def test_wrong_block_duplicate_or_old_phrase_cannot_count_as_compliance(self):
        self.save(wording='We checked 4 valves.')
        for mode in ('wrong_block', 'duplicate', 'old_remains'):
            def invoke(task, payload):
                if task != 'document': return review(payload)
                value = self.compliant(payload)
                target = next(b for b in value['blocks'] if 'We checked 4 valves.' in b['text'])
                if mode == 'wrong_block':
                    target['text'] = 'We tested 4 valves.'; value['blocks'][0]['text'] += ' We checked 4 valves.'
                elif mode == 'duplicate': target['text'] += ' We checked 4 valves.'
                else: target['text'] += ' We tested 4 valves.'
                return value
            result = self.generate(invoke)
            self.assertEqual(result['checks']['article_wording']['status'], 'review_required', mode)

    def test_generation_and_repair_both_receive_instructions(self):
        self.keeps.keep(SOURCE, DRAFT, CONTEXT, CONTROLS, finding(), 'Keep this pacing.')
        self.save(); calls = []; reviews = []
        def invoke(task, payload):
            if task == 'document': calls.append(copy.deepcopy(payload)); return self.compliant(payload)
            if task == 'document_meaning': return review(payload)
            reviews.append(payload)
            return review(payload, [finding(classification='preference_violation', code='distinct_conflict',
                                           basis='A separate synthetic style requirement remains.')] if len(reviews) == 1 else [])
        result = self.generate(invoke)
        self.assertEqual(result['revision_passes'], 2)
        self.assertTrue(calls[1]['revision_feedback']); self.assertTrue(calls[1]['article_wording_instructions'])

    def test_repair_that_drops_wording_is_retried_then_rejected(self):
        self.keeps.keep(SOURCE, DRAFT, CONTEXT, CONTROLS, finding(), 'Keep this pacing.')
        self.save(); reviews = []; repair_calls = []
        def invoke(task, payload):
            if task == 'document':
                value = self.compliant(payload)
                if payload.get('revision_feedback'):
                    repair_calls.append(payload)
                    for b in value['blocks']: b['text'] = b['text'].replace('We tested 4 valves.', 'We checked 4 valves.')
                return value
            if task == 'document_meaning': return review(payload)
            reviews.append(payload)
            return review(payload, [finding(classification='preference_violation', code='distinct_conflict', basis='A separate synthetic requirement remains.')])
        result = self.generate(invoke)
        self.assertEqual(len(repair_calls), 2)
        self.assertEqual(result['revision_passes'], 1); self.assertEqual(result['text'], DRAFT)
        self.assertEqual(result['checks']['article_wording']['items'][0]['status'], 'retained')
        self.assertTrue(result['checks']['voice']['issues']); self.assertTrue(result['checks']['warnings'])

    def test_wording_never_conditions_meaning_or_hides_numeric_errors(self):
        saved = self.save(wording='We tested 5 valves.'); calls = []
        def invoke(task, payload):
            calls.append((task, copy.deepcopy(payload)))
            return self.compliant(payload) if task == 'document' else review(payload)
        result = self.generate(invoke)
        self.assertEqual(result['checks']['article_wording']['status'], 'checked')
        self.assertTrue(result['checks']['content']['issues']); self.assertEqual(result['checks']['status'], 'review_required')
        primary = [p for t,p in calls if t == 'document_review'][0]
        self.assertTrue(primary['article_wording_choices']); self.assertEqual(primary['meaning_change_requests'], [])
        independent = [p for t,p in calls if t == 'document_meaning']
        self.assertTrue(independent)
        for payload in independent:
            self.assertNotIn(saved['id'], str(payload)); self.assertNotIn('article_wording_choices', payload)

    def test_failed_meaning_review_cannot_be_cleared_by_retained_wording(self):
        self.save(wording='We checked 4 valves.')
        def invoke(task, payload):
            if task == 'document_meaning': raise ValueError('Synthetic review failure')
            return self.compliant(payload) if task == 'document' else review(payload)
        result = self.generate(invoke)
        self.assertEqual(result['checks']['article_wording']['status'], 'checked')
        self.assertEqual(result['checks']['review']['status'], 'not_checked')
        self.assertGreater(result['checks']['meaning_changes']['unreviewed'], 0)

    def test_guidance_hash_stays_independent_of_article_instructions(self):
        def invoke(task,payload): return self.compliant(payload) if task == 'document' else review(payload)
        a = self.generate(invoke); self.save(); b = self.generate(invoke)
        self.assertEqual(a['checks']['guidance_hash'], b['checks']['guidance_hash'])

    def test_offline_rehearsal_never_inserts_corrections(self):
        self.save(wording='We checked 4 valves.')
        result = Provider('offline').generate_document(CONTEXT, SOURCE, CONTROLS, [], [PREFERENCE], **self.snapshots())
        self.assertEqual(result['text'], SOURCE); self.assertEqual(result['checks']['article_wording']['items'][0]['status'], 'missing')
        self.assertEqual(result['checks']['article_wording']['attempts'], [])

    def test_overlap_boundary_and_budget_fail_before_generation(self):
        self.save(wording='We checked 4 valves.')
        self.save(source_quote='tested 4 valves', wording='examined 4 valves')
        with patch.object(Provider, '_invoke') as invoke, self.assertRaises(ValueError):
            Provider('codex').generate_document(CONTEXT, SOURCE, CONTROLS, [], [PREFERENCE], **self.snapshots())
        invoke.assert_not_called()
        self.store = DocumentWordingStore(Path(self.temp.name)/'other.sqlite3')
        with self.assertRaises(ValueError): self.save(source_quote='# Trial notes', wording='# Lab notes')
        self.save()
        blocks = parse_document(SOURCE)[0]
        for block in blocks: block['text'] = block['text'].replace('We tested 4 valves.', 'We tested')
        with self.assertRaises(ValueError): instruction_plan(SOURCE, CONTEXT, CONTROLS, blocks, corrections=self.snapshots()['document_wording'])
        self.store = DocumentWordingStore(Path(self.temp.name)/'third.sqlite3'); self.save()
        snapshot = self.snapshots()['document_wording']; snapshot['items'] *= 100
        with self.assertRaises(ValueError): instruction_plan(SOURCE, CONTEXT, CONTROLS, parse_document(SOURCE)[0], corrections=snapshot)

    def test_later_generation_batch_receives_its_own_instruction(self):
        source = '# Longer fictional report\n\n' + '\n\n'.join(('Observation %d: ' % i) + 'The test remained stable. ' * 16 for i in range(40)) + '\n\nWe tested 4 valves.\n'
        saved = self.save(source=source, wording='We checked 4 valves.'); calls=[]
        snap = self.store.resolve(source, CONTEXT, CONTROLS)
        def invoke(task,payload):
            if task == 'document': calls.append(copy.deepcopy(payload)); return self.compliant(payload)
            return review(payload)
        with patch.object(Provider,'_invoke',side_effect=invoke):
            result=Provider('codex').generate_document(CONTEXT,source,CONTROLS,[],[PREFERENCE],document_wording=snap)
        self.assertGreater(len(calls),1)
        self.assertEqual(calls[0]['article_wording_instructions'],[])
        self.assertEqual(calls[-1]['article_wording_instructions'][0]['instruction_id'],saved['id'])
        self.assertEqual(result['text'],source.replace('We tested 4 valves.','We checked 4 valves.'))

    def test_frozen_record_and_plan_cannot_be_mutated_by_later_edits(self):
        saved = self.save(); snap = self.snapshots(); self.store.revoke(saved['id'])
        result = self.generate(lambda t,p: self.compliant(p) if t == 'document' else review(p), **snap)
        self.assertEqual(result['checks']['article_wording']['items'][0]['instruction_id'], saved['id'])
        self.assertEqual(self.snapshots()['document_wording']['items'][0]['status'], 'revoked')

    def test_standalone_check_keeps_optional_history_separate_from_exact_correction(self):
        self.keeps.keep(SOURCE, DRAFT, CONTEXT, CONTROLS, finding(), 'Keep this pacing.')
        self.save()
        draft = DRAFT.replace('showed what happened', 'revealed what happened')
        with patch.object(Provider, '_invoke', side_effect=lambda t,p: review(p)):
            result = Provider('codex').check_document(CONTEXT, SOURCE, draft, CONTROLS, [], [PREFERENCE], **self.snapshots())
        self.assertEqual(result['status'], 'checked')
        missing = next(x for x in result['article_wording']['items'] if x['kind'] == 'kept_rephrase')
        self.assertEqual(missing['status'], 'missing'); self.assertFalse(missing['required'])

    def test_prompt_limits_exact_words_to_article_instructions(self):
        self.assertIn('ARTICLE WORDING', _prompt('document', {}))
        self.assertIn('source meaning independently', _prompt('document_review', {}))
        for task in ('draft', 'document_meaning'):
            self.assertNotIn('ARTICLE WORDING', _prompt(task, {}))
            self.assertNotIn('Article_wording_choices', _prompt(task, {}))


class ArticleWordingApiTests(test_server.ServerTests):
    def payload(self): return {'source': SOURCE, 'context': CONTEXT, 'controls': CONTROLS}
    def save_payload(self): return dict(self.payload(), source_quote='We tested 4 valves.', wording='We tested 4 valves.', reason='Keep this wording.')
    def wait_job(self, job):
        for _ in range(200):
            current = self.request('GET', '/api/documents/' + job['id'])[1]
            if current['status'] in ('complete','failed'):
                self.assertEqual(current['status'], 'complete', current); return current['result']
            time.sleep(.01)
        self.fail('Job did not finish')

    def test_save_resolve_revoke_and_no_learning(self):
        before = self.request('GET', '/api/state')[1]
        status, response, _ = self.request('POST', '/api/document-wording', self.save_payload())
        self.assertEqual(status, 200); ident = response['correction']['id']
        result = self.wait_job(self.request('POST', '/api/documents', self.payload())[1])
        self.assertEqual(result['checks']['document_wording']['items'][0]['id'], ident)
        self.assertEqual(result['checks']['article_wording']['items'][0]['status'], 'retained')
        self.request('POST', '/api/document-wording/'+ident+'/revoke', {})
        rows = self.request('POST', '/api/document-wording/resolve', self.payload())[1]['items']
        self.assertEqual(rows[0]['status'], 'revoked')
        after = self.request('GET', '/api/state')[1]
        for key in ('samples','feedback','language_lessons','profile','corrections'): self.assertEqual(before[key],after[key])
        self.assertNotIn(ident,self.request('GET','/api/export')[1]['markdown'])

    def test_authentication_and_validation_before_write(self):
        self.assertEqual(self.request('POST','/api/document-wording',self.save_payload(),headers={'X-Voicework-Token':'wrong'})[0],403)
        for change in ({'unknown':True},{'source_quote':'missing'},{'reason':''},{'controls':{'register':'invalid'}}):
            self.assertEqual(self.request('POST','/api/document-wording',dict(self.save_payload(),**change))[0],400)
        self.assertEqual(self.request('POST','/api/document-wording/'+'0'*32+'/revoke',{})[0],404)
        self.assertEqual(self.request('POST','/api/document-wording/resolve',self.payload())[1]['items'],[])

    def test_job_snapshot_survives_revoke_during_generation(self):
        saved = self.request('POST','/api/document-wording',self.save_payload())[1]['correction']
        entered, gate = threading.Event(), threading.Event(); captured=[]
        original = self.provider.generate_document
        def generate(*args,**kwargs):
            captured.append(copy.deepcopy((args,kwargs))); entered.set(); gate.wait(2); return original(*args,**kwargs)
        with patch.object(self.provider,'generate_document',side_effect=generate):
            job=self.request('POST','/api/documents',self.payload())[1]; self.assertTrue(entered.wait(1))
            self.server.document_wording.revoke(saved['id']); gate.set(); result=self.wait_job(job)
        self.assertNotIn(saved['id'],str(captured[0][0]))
        self.assertEqual(captured[0][1]['document_wording']['items'][0]['status'],'active')
        self.assertEqual(result['checks']['article_wording']['items'][0]['status'],'retained')


for _name in list(vars(test_server.ServerTests)):
    if _name.startswith('test_') and _name not in vars(ArticleWordingApiTests): setattr(ArticleWordingApiTests,_name,None)

if __name__ == '__main__': unittest.main()
