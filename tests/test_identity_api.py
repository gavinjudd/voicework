import copy
import json
import threading
import time
import unittest
from unittest.mock import patch
from test_server import ServerTests
from test_identity import analysis
from voicework.mcp import VoiceworkMCP


class IdentityApiTests(unittest.TestCase):
    setUp=ServerTests.setUp
    tearDown=ServerTests.tearDown
    request=ServerTests.request

    def create(self):
        status,result,_=self.request('POST','/api/identities',{'label':'Team updates','context':{'channel':'work','audience':'teammates'}})
        self.assertEqual(status,201,result)
        return result['profile']

    def source(self,p):
        status,result,_=self.request('POST','/api/identities/'+p['id']+'/sources',{'text':'I want direct updates without unnecessary apologies.','source_ref':'Synthetic user statement'})
        self.assertEqual(status,201,result)
        return result['source']

    def wait(self,job):
        for _ in range(200):
            result=self.request('GET','/api/identity-jobs/'+job['id'])[1]
            if result['status'] not in ('pending','running'):return result
            time.sleep(.01)
        self.fail('Identity job did not finish')

    def infer(self,p,source):
        self.provider.mode='codex'
        with patch.object(self.provider,'_invoke',return_value=analysis(source,presence='Direct and considerate')):
            status,job,_=self.request('POST','/api/identities/'+p['id']+'/infer',{})
            self.assertEqual(status,202,job)
            result=self.wait(job)
        self.assertEqual(result['status'],'complete',result)
        return result['result']['proposal']

    def test_full_context_collection_inference_review_and_writing(self):
        before=self.request('GET','/api/state')[1]
        p=self.create();source=self.source(p);proposal=self.infer(p,source)
        resolve=lambda:self.request('POST','/api/context/resolve',{'context':p['context']})[1]
        self.assertEqual(resolve()['identity']['profiles'],[])
        self.assertEqual(self.request('POST','/api/identity-proposals/'+proposal['id'],{'action':'accept'})[0],200)
        captured=[]
        def generate(context,brief,controls,evidence,preferences):
            captured.extend(copy.deepcopy(preferences));return {'text':brief,'provider':'codex','warnings':[]}
        with patch.object(self.provider,'generate_draft',side_effect=generate):
            status,result,_=self.request('POST','/api/draft',{'context':p['context'],'brief':'A synthetic update.'})
        self.assertEqual(status,200,result)
        self.assertTrue(any(v.get('is_identity_profile') for v in captured))
        self.assertEqual(resolve()['counts']['feedback'],0)
        self.assertEqual(resolve()['identity']['profiles'][0]['values']['presence'],'Direct and considerate')
        after=self.request('GET','/api/state')[1]
        for key in ('samples','feedback','language_lessons','priors','corrections','evaluations','quality_checks'):
            self.assertEqual(before[key],after[key],key)
        self.assertEqual(self.request('POST','/api/identity-proposals/'+proposal['id'],{'action':'undo'})[0],200)
        self.assertEqual(resolve()['identity']['profiles'],[])

    def test_identity_assets_are_served_with_correct_mime(self):
        import http.client
        for path, mime in [('/identity-profile.mjs','text/javascript'),('/identity-profile.css','text/css')]:
            connection=http.client.HTTPConnection('127.0.0.1',self.port,timeout=5)
            try:
                connection.request('GET',path)
                response=connection.getresponse()
                self.assertEqual(response.status,200)
                self.assertIn(mime,response.getheader('Content-Type'))
                self.assertTrue(response.read())
            finally:connection.close()

    def test_review_routes_enforce_token_origin_and_validation(self):
        packet={'label':'New','context':{'channel':'work'}}
        for headers in ({'X-Voicework-Token':'wrong'},{'Origin':'https://foreign.test'}):
            self.assertEqual(self.request('POST','/api/identities',packet,headers=headers)[0],403)
        self.assertEqual(self.request('GET','/api/identities')[1]['profiles'],[])
        p=self.create();source=self.source(p)
        self.assertEqual(self.request('POST','/api/identities/'+p['id']+'/infer',{})[0],400)
        self.assertEqual(self.request('POST','/api/identities/'+p['id']+'/infer',{'confirmed':True})[0],400)
        self.assertEqual(self.request('PATCH','/api/identities/'+p['id'],{'revision':1,'context':{'channel':'personal'}})[0],400)
        self.assertEqual(self.request('PATCH','/api/identity-sources/'+source['id'],{'withdrawn':1})[0],400)
        self.assertEqual(self.request('HEAD','/api/identities/'+p['id'])[0],200)

    def test_bad_model_does_not_save_a_proposal(self):
        p=self.create();source=self.source(p);self.provider.mode='codex'
        result=analysis(source,presence='Fake');result['support']['presence'][0]['quote']='Invented context'
        with patch.object(self.provider,'_invoke',return_value=result):
            _,job,_=self.request('POST','/api/identities/'+p['id']+'/infer',{})
            final=self.wait(job)
        self.assertEqual(final['status'],'failed')
        self.assertIn('source evidence',final['error'])
        self.assertEqual(self.server.identities.proposals(p['id']),[])

    def test_abstention_surfaces_questions_without_profile_change(self):
        p=self.create();source=self.source(p);self.provider.mode='codex'
        result=analysis(source);result.update(abstain=True,questions=['Who is this for?'])
        with patch.object(self.provider,'_invoke',return_value=result):
            _,job,_=self.request('POST','/api/identities/'+p['id']+'/infer',{})
            final=self.wait(job)
        self.assertIsNone(final['result']['proposal'])
        self.assertEqual(final['result']['analysis']['questions'],['Who is this for?'])
        self.assertFalse(self.server.identities.get(p['id'])['active'])

    def test_document_freezes_reviewed_identity_profile(self):
        p=self.create();source=self.source(p);proposal=self.infer(p,source)
        self.request('POST','/api/identity-proposals/'+proposal['id'],{'action':'accept'})
        entered=threading.Event();release=threading.Event();captured=[]
        def generate(context,source,controls,evidence,preferences):
            captured.extend(copy.deepcopy(preferences));entered.set();release.wait(3)
            return {'text':source,'provider':'codex','warnings':[]}
        with patch.object(self.provider,'generate_document',side_effect=generate):
            _,job,_=self.request('POST','/api/documents',{'context':p['context'],'source':'Synthetic source.'})
            self.assertTrue(entered.wait(1))
            p=self.server.identities.get(p['id'])
            self.request('PATCH','/api/identities/'+p['id'],{'revision':p['revision'],'values':{'presence':'A later explicit edit'}})
            release.set()
            for _ in range(200):
                done=self.request('GET','/api/documents/'+job['id'])[1]
                if done['status']=='complete':break
                time.sleep(.01)
        selected=next(v for v in captured if v.get('is_identity_profile'))
        self.assertEqual(selected['values']['presence'],'Direct and considerate')
        self.assertEqual(done['result']['context_report']['identity']['profiles'][0]['values']['presence'],'Direct and considerate')

    def test_mcp_collector_cannot_approve_and_app_owns_model(self):
        mcp=VoiceworkMCP(self.server.store.path.parent,app_url='http://127.0.0.1:'+str(self.port))
        _,plan=mcp.create_identity_intake({'label':'Team updates','context':{'channel':'work','audience':'teammates'}})
        p=plan['profile'];self.assertFalse(p['active'])
        _,source=mcp.submit_identity_context({'profile_id':p['id'],'text':'I want direct updates without unnecessary apologies.','source_ref':'Synthetic connector record'})
        self.assertNotIn('payload',source)
        self.provider.mode='codex'
        with patch.object(self.provider,'_invoke',return_value=analysis(source,presence='Direct')) as model:
            _,job=mcp.process_identity_context({'profile_id':p['id']})
            final=self.wait(job)
        self.assertEqual(model.call_args.args[0],'identity')
        self.assertEqual(final['result']['proposal']['status'],'pending')
        listed=mcp.handle({'jsonrpc':'2.0','id':1,'method':'tools/list'})['result']['tools']
        self.assertFalse(any('accept_identity' in t['name'] or 'review_identity' in t['name'] for t in listed))
        response=mcp.handle({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'submit_identity_context','arguments':{'profile_id':p['id'],'text':'x','confirmed':True}}})
        self.assertTrue(response['result']['isError'])

    def test_mcp_refuses_mismatched_app_profile_and_remote_url(self):
        mcp=VoiceworkMCP(self.server.store.path.parent,app_url='http://127.0.0.1:'+str(self.port))
        with patch('voicework.mcp.request_json',return_value={'profile_instance':'another-profile','token':'test'}):
            with self.assertRaisesRegex(ValueError,'different profile'):
                mcp._identity_app('/api/identity-jobs/example')
        with self.assertRaises(ValueError):
            VoiceworkMCP(self.server.store.path.parent,app_url='https://example.test')


if __name__=='__main__':unittest.main()
