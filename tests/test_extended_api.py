import unittest
from pathlib import Path
import test_server

class ExtendedApiTests(unittest.TestCase):
    setUp = test_server.ServerTests.setUp
    tearDown = test_server.ServerTests.tearDown
    request = test_server.ServerTests.request

    def test_inference_is_preview_and_holdout_is_not_sent(self):
        sample=self.server.store.add_sample({'text':'Hi team,\nPlease review the project update.','title':'Project email'})
        status,result,_=self.request('POST','/api/samples/'+sample['id']+'/infer',{})
        self.assertEqual(status,200)
        self.assertEqual(result['inference']['provider'],'offline')
        self.assertEqual(result['inference']['context']['channel'],'work')
        self.assertEqual(self.server.store.samples()[0]['status'],'pending')
        self.server.store.update_sample(sample['id'],{'held_out':True})
        self.assertEqual(self.request('POST','/api/samples/'+sample['id']+'/infer',{})[0],400)

    def test_prior_review_export_and_evaluation_stay_separate(self):
        payload={'label':'Work voice','text':'Keep work replies direct.','source':'Explicit review packet','context':{'channel':'work'}}
        status,value,_=self.request('POST','/api/priors',payload)
        self.assertEqual(status,201); ident=value['prior']['id']
        self.assertNotIn(payload['text'],self.request('GET','/api/export')[1]['markdown'])
        self.assertEqual(self.request('PATCH','/api/priors/'+ident,{'confirmed':True})[0],200)
        self.assertIn(payload['text'],self.request('GET','/api/export')[1]['markdown'])
        status,value,_=self.request('POST','/api/evaluations',{'context':{'channel':'work'},'brief':'The review is ready.'})
        self.assertEqual(status,201); case=value['case']
        self.assertNotIn('mapping',case)
        status,value,_=self.request('POST','/api/evaluations/'+case['id']+'/rate',{'choice':'tie'})
        self.assertEqual(status,200); self.assertIn('mapping',value['case'])
        self.assertEqual(value['summary']['rated_real'],0)
        self.assertEqual(value['summary']['rated_offline'],1)
        state=self.request('GET','/api/state')[1]
        self.assertEqual(state['feedback'],[]); self.assertEqual(state['samples'],[])
        self.assertEqual(self.request('DELETE','/api/priors/'+ident,{})[0],200)
        self.assertNotIn(payload['text'],self.request('GET','/api/export')[1]['markdown'])

    def test_resumable_discovery_route(self):
        source=Path(self.temp.name).resolve()/'sources'; source.mkdir()
        for n in range(3): (source/('%s.txt'%n)).write_text('Synthetic source %s'%n)
        status,value,_=self.request('POST','/api/discovery-jobs',{'roots':[str(source)]})
        self.assertEqual(status,201); ident=value['job']['id']
        status,value,_=self.request('POST','/api/discovery-jobs/'+ident+'/step',{'max_files':1})
        self.assertEqual(status,200); self.assertEqual(value['job']['added'],1)
        while value['job']['status']!='completed':
            status,value,_=self.request('POST','/api/discovery-jobs/'+ident+'/step',{'max_files':1})
            self.assertEqual(status,200)
        self.assertEqual(value['job']['added'],3)
        self.assertEqual(len(self.request('GET','/api/state')[1]['samples']),3)
