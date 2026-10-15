const test = require('node:test');
const assert = require('node:assert/strict');
let d;
test.before(async () => {d = await import('../web/document-state.mjs');});
function pending(kind = 'generate') {
  const doc = d.emptyDocument();
  doc.source = '# Plan\n\nShip on Friday.\n';
  doc.job = {id: 'job-123', status: 'pending', kind, source: doc.source, text: doc.text, context: {...doc.context}, controls: {...doc.controls}, revision: doc.revision};
  return doc;
}
function complete(id = 'job-123', overrides = {}) {
  return {id, status: 'complete', result: {text: '# Plan\n\nWe ship Friday.\n', provider: 'codex', evidence_ids: [], warnings: [], checks: {status: 'review_required', content: {status: 'checked', issues: []}, voice: {status: 'review_required', issues: [{severity: 'warning', code: 'formality', message: 'Review the register.'}]}, source_hash: 'source', draft_hash: 'draft'}, ...overrides}};
}
test('job identifiers and states must be bounded known values', () => {
  for (const value of [null, [], {}, {id: '', status: 'pending'}, {id: 'x'.repeat(201), status: 'pending'}, {id: 'j', status: 'success'}, {id: 4, status: 'running'}]) assert.equal(d.validDocumentJob(value), false);
  assert.equal(d.validDocumentJob({id: 'j', status: 'pending'}), true);
  assert.equal(d.activeDocumentJob({id: 'j', status: 'complete'}), false);
});
test('completion preserves source and exact editable draft including Markdown whitespace', () => {
  const doc = pending(); const next = d.acceptDocumentJob(doc, complete());
  assert.equal(next.source, doc.source);
  assert.equal(next.checkedSource, doc.source);
  assert.equal(next.text, '# Plan\n\nWe ship Friday.\n');
  assert.deepEqual(next.result.source_context, doc.context);
  assert.equal(d.documentChecksCurrent(next), true);
  assert.equal(doc.text, '');
});
test('editing output, source, context or controls invalidates earlier checks', () => {
  const checked = d.acceptDocumentJob(pending(), complete());
  for (const patch of [{text: checked.text + 'Edited.'}, {source: checked.source + 'An extra fact.'}, {context: {...checked.context, audience: 'Board'}}, {controls: {...checked.controls, register: 'formal'}}]) {
    const next = d.reviseDocument(checked, patch);
    assert.equal(d.documentChecksCurrent(next), false);
    assert.equal(next.result.checks.draft_hash, 'draft');
    assert.equal(next.checkedSource, checked.source);
  }
  assert.equal(d.reviseDocument(checked, {text: checked.text}), checked);
});
test('checks completing after an edit never label the new text checked', () => {
  const doc = pending('check'); doc.text = 'First revision.'; doc.job.text = doc.text;
  const changed = d.reviseDocument(doc, {text: 'Second revision.'});
  const next = d.acceptDocumentJob(changed, complete());
  assert.equal(next.text, 'Second revision.');
  assert.equal(next.checkRevision, 0);
  assert.equal(d.documentChecksCurrent(next), false);
});
test('late generation cannot overwrite intervening changes', () => {
  const doc = d.reviseDocument(pending(), {text: 'Keep my new edits.'});
  const next = d.acceptDocumentJob(doc, complete());
  assert.equal(next.text, 'Keep my new edits.');
  assert.match(next.error, /current text was kept/);
  assert.equal(next.result, null);
});
test('failed jobs keep source and prior editable text', () => {
  const doc = pending(); doc.text = 'Prior draft.';
  const next = d.acceptDocumentJob(doc, {id: 'job-123', status: 'failed', error: 'The provider timed out.'});
  assert.equal(next.source, doc.source); assert.equal(next.text, doc.text);
  assert.equal(next.job.status, 'failed'); assert.match(next.error, /timed out/);
});
test('wrong jobs, missing output and oversize output do not become successful drafts', () => {
  assert.throws(() => d.acceptDocumentJob(pending(), complete('another-job')), /does not match/);
  for (const text of ['', ' ', 'a'.repeat(d.DOCUMENT_OUTPUT_LIMIT + 1), undefined]) assert.throws(() => d.acceptDocumentJob(pending(), complete('job-123', {text})), /incomplete/);
  assert.throws(() => d.acceptDocumentJob(pending('check'), {id: 'job-123', status: 'complete', result: {}}), /incomplete/);
});
test('bounded storage restores active jobs with exact Unicode source and output', () => {
  const doc = pending('check'); doc.source = '## café 🌱\n\n  exact spacing  \n'; doc.text = 'Revised 🌳\n'; doc.job.source = doc.source; doc.job.text = doc.text;
  const restored = d.restoreDocument(JSON.parse(JSON.stringify(doc)));
  assert.equal(restored.source, doc.source); assert.equal(restored.text, doc.text);
  assert.deepEqual(restored.job, doc.job);
  assert.equal(d.activeDocumentJob(restored.job), true);
});
test('oversized storage is rejected without silent source or draft truncation', () => {
  for (const key of ['source', 'text']) {
    const doc = pending(); doc[key] = 'x'.repeat((key === 'source' ? d.DOCUMENT_SOURCE_LIMIT : d.DOCUMENT_OUTPUT_LIMIT) + 1);
    const restored = d.restoreDocument(doc); assert.equal(restored.source, ''); assert.equal(restored.text, ''); assert.equal(restored.job, null);
  }
});
test('invalid persisted controls and context cannot extend supported values', () => {
  const doc = {...d.emptyDocument(), context: {channel: 'w'.repeat(201), audience: {html: 'x'}, intent: 'request review'}, controls: {polish: -1, detail: Infinity, structure: 'html', register: 'authoritative'}, revision: -8, checkRevision: 900};
  const restored = d.restoreDocument(doc);
  assert.equal(restored.context.channel, 'work'); assert.equal(restored.context.audience, ''); assert.equal(restored.context.intent, 'request review');
  assert.equal(restored.controls.polish, 45); assert.equal(restored.controls.structure, 'auto'); assert.equal(restored.controls.register, 'auto'); assert.equal(restored.checkRevision, null);
});
test('document length control keeps only known values', () => {
  assert.equal(d.emptyDocument().controls.length, 'auto');
  assert.equal(d.cleanDocumentControls({length: 'preserve'}).length, 'preserve');
  assert.equal(d.cleanDocumentControls({length: 'shorter'}).length, 'auto');
  assert.equal(d.restoreDocument({...d.emptyDocument(), controls: {length: {html: 'x'}}}).controls.length, 'auto');
});
test('download names stay bounded and contain no path components', () => {
  assert.equal(d.documentDownloadName('Plan.md'), 'Plan-voicework.md');
  assert.equal(d.documentDownloadName('../folder/Plan.txt', 'txt'), '..folderPlan-voicework.txt');
  assert.equal(d.documentDownloadName(''), 'document-voicework.md');
  assert.ok(d.documentDownloadName('A'.repeat(1000)).length < 200);
});
