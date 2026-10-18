const test = require('node:test');
const assert = require('node:assert/strict');
let canTeachComparison, canTeachPractice;
test.before(async () => ({canTeachComparison, canTeachPractice} = await import('../web/preference-learner.mjs')));

test('only saved directional real-model practice choices offer attributed learning',()=>{
  assert.equal(canTeachPractice({provider:'codex',choice:'a'}),true);
  for(const value of [null,{}, {provider:'offline',choice:'a'}, {provider:'codex',choice:'neither'}]) assert.equal(canTeachPractice(value),false);
});

test('learning is offered only after a meaningful real comparison judgment', () => {
  const rated = {status:'rated',provider:'codex',choice:'a',meaning:{a:'preserved',b:'preserved'},factual_issue:false};
  assert.equal(canTeachComparison(rated,'quality'),true);
  assert.equal(canTeachComparison(rated,'evaluation'),true);
  for (const patch of [{status:'unrated'},{provider:'offline'},{choice:'tie'},{choice:'neither'},{choice:null}]) {
    assert.equal(canTeachComparison({...rated,...patch},'quality'),false);
    assert.equal(canTeachComparison({...rated,...patch},'evaluation'),false);
  }
  for (const meaning of [{a:'preserved',b:'changed'},{a:'unsure',b:'preserved'},{}]) assert.equal(canTeachComparison({...rated,meaning},'quality'),false);
  assert.equal(canTeachComparison({...rated,factual_issue:true},'evaluation'),false);
});
