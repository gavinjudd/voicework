import assert from 'node:assert/strict';
import {identityFields,identityReviewable,identitySourcePacket} from '../web/identity-profile.mjs';

assert.deepEqual(identityFields,['goal','presence','long_term_goals','boundaries']);
assert.equal(identityReviewable({status:'pending',stale:false,source_issues:[]}),true);
assert.equal(identityReviewable({status:'pending',stale:true,source_issues:[]}),false);
assert.equal(identityReviewable({status:'pending',source_issues:['withdrawn']}),false);
assert.equal(identityReviewable({status:'accepted',source_issues:[]}),false);
assert.deepEqual(identitySourcePacket({kind:'edit',before_text:'Before',after_text:'After',context:{channel:'work'},confirmed:true,values:{presence:'Do not silently approve'}}),{kind:'edit',before_text:'Before',after_text:'After'});
assert.deepEqual(identitySourcePacket({kind:'context',text:'Raw source material',source_ref:'connector:123',collector:'A collecting assistant'}),{kind:'context',text:'Raw source material',source_ref:'connector:123',collector:'A collecting assistant'});
console.log('7 identity UI state/packet checks passed.');
