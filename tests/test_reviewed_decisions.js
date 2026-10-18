const test = require('node:test');
const assert = require('node:assert/strict');
let candidate;
test.before(async () => {candidate = (await import('../web/reviewed-decisions.mjs')).reviewedEditCandidate;});
function fixture() {
  const edit = {change_id:'m1', verdict:'changed', reviewed:true, dimension:'scope', reason:'The scope differs.'};
  const issue = {change_id:'m1', code:'meaning_changed', severity:'warning', dimension:'scope',
    evidence:[{origin:'edit_assessment', code:'meaning_changed', severity:'warning', change_ids:['m1'], message:edit.reason}]};
  return {edit, issue, checks:{meaning_changes:{checks:[edit]}}};
}
test('only the current direct meaning assessment offers a review action', () => {
  const {issue, checks, edit} = fixture();
  assert.equal(candidate(issue, checks), edit);
  assert.equal(candidate({...issue, change_id:'m2'}, checks), null);
  assert.equal(candidate({...issue, dimension:'actor'}, checks), null);
  assert.equal(candidate({...issue, conflicting_assessments:true}, checks), null);
  assert.equal(candidate(issue, {}), null);
});
test('independent, protected and incomplete findings never offer keep', () => {
  for (const signal of [{origin:'semantic_review'}, {origin:'deterministic',code:'numbers_changed',severity:'error'}]) {
    const {issue, checks} = fixture(); issue.evidence.push(signal); assert.equal(candidate(issue, checks), null);
  }
  const {issue, checks, edit} = fixture(); edit.reviewed=false; assert.equal(candidate(issue, checks), null);
  edit.reviewed=true; edit.verdict='uncertain'; assert.equal(candidate(issue, checks), null);
});
test('nonblocking corroboration can accompany the exact accepted finding', () => {
  const {issue, checks, edit} = fixture();
  issue.evidence.push({origin:'deterministic',code:'negation_changed',blocking:false});
  assert.equal(candidate(issue, checks), edit);
  issue.evidence[1].blocking=true; assert.equal(candidate(issue, checks), null);
});
