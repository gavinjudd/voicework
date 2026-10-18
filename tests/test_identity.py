import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from voicework.identity import IdentityStore, FIELDS, validate_inference
from voicework.store import Store
from voicework.context import PriorStore
from voicework.provider import _payload, _prompt


def analysis(source, **changes):
    values = {key: changes.get(key, '') for key in FIELDS}
    support = {key: ([{'source_id': source['id'], 'quote': 'I want direct updates', 'rationale': 'An explicit communication preference.'}] if value else []) for key, value in values.items()}
    return {'changes': values, 'support': support, 'uncertainties': ['Long-term goals remain unknown.'],
            'conflicts': [], 'questions': [], 'rationale': 'A context-specific interpretation.', 'abstain': False}


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'voicework.sqlite3'
        self.samples = Store(self.path)
        self.priors = PriorStore(self.path)
        self.store = IdentityStore(self.path)
        self.profile = self.store.create({'label': 'Team updates', 'context': {'channel': 'work', 'audience': 'teammates'}})
        self.source = self.store.intake(self.profile['id'], {'kind': 'context', 'text': 'I want direct updates without unnecessary apologies.', 'source_ref': 'user-provided note'})

    def tearDown(self):
        self.temp.cleanup()

    def proposed(self, **changes):
        prepared = self.store.prepare(self.profile['id'])
        return self.store.save_inference(prepared, analysis(self.source, **(changes or {'presence':'Direct and considerate'})), 'codex/test')['proposal']

    def accept(self, proposal):
        return self.store.review(proposal['id'], {'action': 'accept'})

    def test_pending_then_review_then_undo(self):
        proposal = self.proposed()
        context = {'channel': 'work', 'audience': 'teammates'}
        self.assertEqual(self.store.resolve(context)['profiles'], [])
        self.accept(proposal)
        self.assertEqual(self.store.resolve(context)['profiles'][0]['values']['presence'], 'Direct and considerate')
        self.store.review(proposal['id'], {'action': 'undo'})
        self.assertEqual(self.store.resolve(context)['profiles'], [])
        self.assertEqual([h['action'] for h in self.store.history(self.profile['id'])][:3], ['undo', 'accept', 'proposed'])

    def test_review_can_edit_and_select_fields(self):
        proposal = self.proposed(presence='Direct', goal='Inform teammates')
        result = self.store.review(proposal['id'], {'action':'accept', 'changes':{'presence':'Direct, with some warmth'}})
        self.assertEqual(result['profile']['values']['goal'], '')
        self.assertEqual(result['profile']['values']['presence'], 'Direct, with some warmth')

    def test_rejection_keeps_profile_and_source(self):
        p = self.proposed()
        result = self.store.review(p['id'], {'action':'reject', 'reason':'That reading is not what I meant'})
        self.assertFalse(result['profile']['active'])
        self.assertEqual(result['proposal']['status'], 'rejected')
        self.assertEqual(len(self.store.sources(self.profile['id'])), 1)

    def test_stale_proposal_and_later_edit_protect_newer_work(self):
        p = self.proposed()
        self.store.update(self.profile['id'], {'revision':1, 'values':{'presence':'My own wording'}})
        with self.assertRaisesRegex(ValueError, 'changed'):
            self.accept(p)
        with self.assertRaisesRegex(ValueError, 'Reload'):
            self.store.update(self.profile['id'], {'revision':1,'values':{'presence':'Old tab'}})
        p = self.proposed(presence='A newer possible presence')
        self.accept(p)
        profile = self.store.get(self.profile['id'])
        self.store.update(profile['id'], {'revision':profile['revision'], 'values':{'goal':'A newer explicit goal'}})
        with self.assertRaisesRegex(ValueError, 'newer work'):
            self.store.review(p['id'], {'action':'undo'})

    def test_withdrawn_context_stops_derived_guidance(self):
        self.accept(self.proposed())
        self.store.withdraw_source(self.source['id'], True)
        self.assertEqual(self.store.resolve(self.profile['context'])['profiles'], [])
        self.store.withdraw_source(self.source['id'], False)
        self.assertEqual(len(self.store.resolve(self.profile['context'])['profiles']), 1)

    def test_pausing_and_direct_edits(self):
        self.accept(self.proposed())
        p=self.store.get(self.profile['id'])
        self.store.update(p['id'], {'revision':p['revision'],'active':False})
        self.assertFalse(self.store.resolve(p['context'])['profiles'])
        p=self.store.get(p['id'])
        self.store.update(p['id'], {'revision':p['revision'],'values':{'presence':'My explicit choice'},'active':True})
        self.store.withdraw_source(self.source['id'], True)
        self.assertEqual(self.store.resolve(p['context'])['profiles'][0]['values']['presence'],'My explicit choice')

    def test_context_does_not_leak_or_average_roles(self):
        self.accept(self.proposed())
        self.assertFalse(self.store.resolve({'channel':'work','audience':'client'})['profiles'])
        self.assertFalse(self.store.resolve({'channel':'personal','audience':'teammates'})['profiles'])
        self.assertFalse(self.store.resolve({'channel':'work'})['profiles'])
        other=self.store.create({'label':'Another role','context':self.profile['context'],'values':{'presence':'A different presence'}})
        self.assertFalse(self.store.resolve(self.profile['context'])['profiles'])
        self.assertIn('Multiple identity',self.store.resolve(self.profile['context'])['warnings'][0])

    def test_more_specific_profile_wins(self):
        self.store.create({'label':'Broad work default','context':{'channel':'work'},'values':{'presence':'Broad'}})
        self.accept(self.proposed())
        self.assertEqual(self.store.resolve(self.profile['context'])['profiles'][0]['id'],self.profile['id'])

    def test_longform_scope_and_current_voice_priority(self):
        p=self.store.create({'label':'Public essays','context':{'channel':'long-form writing'},'values':{'presence':'Conversational builder'}})
        guidance=self.store.guidance({'channel':'technical article'})
        payload=_payload({'channel':'technical article'},[],guidance)
        self.assertEqual(payload['identity_profiles'][0]['id'],p['id'])
        self.assertEqual(payload['calibration_feedback'],[])
        self.assertEqual(payload['authored_examples'],[])
        self.assertIn('Do not trade',_prompt('document',payload))
        self.assertEqual(self.store.guidance({'channel':'personal'}),[])

    def test_hallucinated_quote_and_source_rejected(self):
        prepared=self.store.prepare(self.profile['id'])
        for change in ['quote','source_id']:
            result=analysis(self.source,presence='Direct')
            result['support']['presence'][0][change]='Not in this source'
            with self.assertRaisesRegex(ValueError,'invented'):
                self.store.save_inference(prepared,result,'codex/test')
        self.assertEqual(self.store.proposals(self.profile['id']),[])

    def test_abstention_and_missing_support(self):
        prepared=self.store.prepare(self.profile['id'])
        result=analysis(self.source,presence='Direct');result['abstain']=True
        self.assertIsNone(self.store.save_inference(prepared,result,'codex/test')['proposal'])
        result=analysis(self.source,presence='Direct');result['support']['presence']=[]
        with self.assertRaisesRegex(ValueError,'cited'):
            self.store.save_inference(prepared,result,'codex/test')

    def test_duplicate_source_and_inference_are_idempotent(self):
        same=self.store.intake(self.profile['id'], {'kind':'context','text':'I want direct updates without unnecessary apologies.','source_ref':'user-provided note'})
        self.assertEqual(same['id'],self.source['id'])
        self.assertEqual(self.proposed()['id'],self.proposed()['id'])

    def test_sources_and_life_goal_inference_are_not_facts_or_authorship(self):
        before=self.samples.samples()
        p=self.proposed(long_term_goals='A possible goal needing review')
        self.assertFalse(self.store.get(self.profile['id'])['active'])
        self.assertEqual(self.samples.samples(),before)
        self.assertEqual(self.priors.list(),[])
        text=_prompt('identity',self.store.prepare(self.profile['id']))
        self.assertIn('only goals explicitly stated',text)
        self.assertIn('collecting assistant may be cheap',text)

    def test_holdout_intake_inference_and_late_revocation(self):
        sample=self.samples.add_sample({'text':'I want direct updates without unnecessary apologies.'})
        self.accept(self.proposed())
        self.samples.update_sample(sample['id'], {'held_out':True})
        self.assertFalse(self.store.resolve(self.profile['context'])['profiles'])
        with self.assertRaisesRegex(ValueError,'Held-out'):
            self.store.intake(self.profile['id'], {'kind':'edit','before_text':'I want direct updates without unnecessary apologies.','after_text':'Clear updates please.'})
        with self.assertRaisesRegex(ValueError,'Held-out'):
            self.store.create({'label':'Copy','context':{'channel':'work'},'values':{'goal':sample['text']}})

    def test_invalid_batches_are_rejected_cleanly(self):
        for ids in ([{}], ['missing'], [], [self.source['id']]*2):
            with self.assertRaises(ValueError): self.store.prepare(self.profile['id'],ids)
        for invalid in ({'channel':''}, {}, None):
            with self.assertRaises(ValueError): self.store.create({'label':'Scope','context':invalid})
        with self.assertRaises(ValueError):self.store.withdraw_source(self.source['id'],1)
        with self.assertRaises(ValueError):self.store.intake(self.profile['id'],{'text':'x','observed_at':'yesterday'})

    def test_changed_native_source_and_context_boundary(self):
        ref=self.samples.add_sample({'text':'A third party reference about specific costs.'})
        source=self.store.intake(self.profile['id'],{'kind':'reference','source_id':ref['id']})
        self.assertTrue(source['eligible'])
        self.assertNotIn('status',source['payload'])
        self.assertEqual(source['payload']['authorship_review_status'],ref['status'])
        self.assertIn('not whether the user likes',source['payload']['reference_notice'])
        self.samples.update_sample(ref['id'],{'held_out':True})
        self.assertFalse(next(s for s in self.store.sources(self.profile['id']) if s['id']==source['id'])['eligible'])
        prior=self.priors.add({'label':'Narrow context','text':'A source summary.','context':{'channel':'personal','audience':'friend'},'source':'Synthetic'})
        with self.assertRaisesRegex(ValueError,'scope'):
            self.store.intake(self.profile['id'],{'kind':'prior','source_id':prior['id']})

    def test_persistence_and_only_additive_tables(self):
        IdentityStore(self.path)
        self.assertEqual(len(self.store.list()),1)
        self.assertEqual(IdentityStore(self.path).sources(self.profile['id'])[0]['id'],self.source['id'])
        self.assertEqual(len(self.samples.samples()),0)

    def test_abstention_questions_survive_reload_and_reuse(self):
        prepared=self.store.prepare(self.profile['id'])
        output=analysis(self.source);output.update(abstain=True,questions=['Who is the intended audience?'])
        saved=self.store.save_inference(prepared,output,'codex/test')
        reopened=IdentityStore(self.path)
        self.assertEqual(reopened.proposals(self.profile['id'])[0]['analysis']['questions'],output['questions'])
        cached=reopened.cached_inference(prepared,'codex/test')
        self.assertEqual(cached['analysis_record'],saved['analysis_record'])
        self.assertIsNone(cached['proposal'])
        self.assertFalse(reopened.get(self.profile['id'])['active'])

    def test_withdrawing_unused_source_preserves_cited_field(self):
        unused=self.store.intake(self.profile['id'],{'text':'The meeting is at noon.'})
        p=self.proposed(presence='Direct and considerate')
        self.accept(p)
        self.store.withdraw_source(unused['id'],True)
        self.assertEqual(self.store.resolve(self.profile['context'])['profiles'][0]['values']['presence'],'Direct and considerate')

    def test_credentials_and_unmarked_summaries_have_safe_defaults(self):
        for packet in ({'text':'Authorization: Bearer synthetic-secret'},
                       {'text':'A relevant passage.','source_ref':'https://example.com/note?access_token=synthetic'},
                       {'text':'A relevant passage.','source_ref':'https://name:password@example.com/note'}):
            with self.assertRaisesRegex(ValueError,'credential|token'):
                self.store.intake(self.profile['id'],packet)
        self.assertEqual(self.source['source_nature'],'unknown')
        summary=self.store.intake(self.profile['id'],{'text':'Assistant guesses a preference.','source_nature':'assistant_summary'})
        self.assertEqual(summary['source_nature'],'assistant_summary')
        with self.assertRaises(ValueError):
            self.store.intake(self.profile['id'],{'text':'x','source_nature':'verified_identity'})

    def test_collector_titles_and_record_status_cannot_supply_quotes(self):
        reference=self.samples.add_sample({'title':'I want direct updates','text':'An unrelated third-party passage.'})
        source=self.store.intake(self.profile['id'],{'kind':'reference','source_id':reference['id']})
        with self.assertRaisesRegex(ValueError,'invented'):
            self.store.save_inference(self.store.prepare(self.profile['id'],[source['id']]),analysis(source,presence='Direct'),'test/model')


if __name__=='__main__':unittest.main()
