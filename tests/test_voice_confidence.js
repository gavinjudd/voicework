const test = require('node:test');
const assert = require('node:assert/strict');
let confidenceView, pendingSampleCounts;
test.before(async () => ({confidenceView, pendingSampleCounts} = await import('../web/voice-confidence.mjs')));

function readiness(overrides = {}) {
  return {label: 'Evidence only', completed: 1, total: 3, milestones: [{label: 'Evidence saved', complete: true, detail: 'Confirmed writing or explicit preferences.'}], evidence: {authored_samples: 39, feedback: 0, lessons: 0, channels: ['work']}, validation: {rated_real: 0, calibrated_wins: 0, baseline_wins: 0, ties: 0, neither: 0, factual_flags: 0, distinct_briefs: 0, channels: [], correction_checks: 2}, ...overrides};
}

test('missing or invalid readiness is unavailable, not zero progress', () => {
  for (const value of [undefined, null, {}, [], 'ready', {completed: 0, total: 3}, readiness({completed: NaN}), readiness({completed: Infinity}), readiness({total: 100}), readiness({label: ' '})]) assert.equal(confidenceView(value), null);
});

test('server readiness is preserved independently of sample volume and correction checks', () => {
  const result = confidenceView(readiness());
  assert.equal(result.label, 'Evidence only');
  assert.equal(result.completed, 1);
  assert.equal(result.evidence.authored_samples, 39);
  assert.equal(result.validation.rated_real, 0);
  assert.equal(result.validation.correction_checks, 2);
  assert.equal(result.evidence.feedback, 0);
  assert.equal(result.evidence.lessons, 0);
});

test('a genuine empty profile can display zero checkpoints', () => {
  const result = confidenceView(readiness({label: 'No evidence yet', completed: 0, evidence: {authored_samples: 0, feedback: 0, lessons: 0, channels: []}}));
  assert.equal(result.completed, 0);
  assert.equal(result.evidence.authored_samples, 0);
  assert.equal(result.total, 3);
});

test('progress cannot escape the three segments or expose fractional checkpoints', () => {
  for (const [value, expected] of [[-5, 0], [-0.2, 0], [1.9, 1], [3, 3], [1000, 3]]) assert.equal(confidenceView(readiness({completed: value})).completed, expected);
});

test('invalid counts remain unavailable and string counts cannot become HTML or numeric truth', () => {
  const result = confidenceView(readiness({evidence: {authored_samples: '<img src=x onerror=alert(1)>', feedback: -1, lessons: Infinity}, validation: {rated_real: '3', correction_checks: 2.7}}));
  assert.equal(result.evidence.authored_samples, null);
  assert.equal(result.evidence.feedback, null);
  assert.equal(result.evidence.lessons, null);
  assert.equal(result.validation.rated_real, null);
  assert.equal(result.validation.correction_checks, 2);
});

test('milestone completion requires a boolean and channels contain only distinct text values', () => {
  const result = confidenceView(readiness({milestones: [null, {label: 'Checked', complete: 'false'}, {label: 'Compared', complete: true}, {label: 'Extra', complete: true}], evidence: {channels: ['work', ' work ', '', null, 3]}}));
  assert.equal(result.milestones.length, 3);
  assert.deepEqual(result.milestones.map(item => item.complete), [false, false, true]);
  assert.deepEqual(result.evidence.channels, ['work']);
});

test('review counts separate pending holdouts without changing authored status', () => {
  const samples = [{status: 'pending'}, {status: 'pending', held_out: false}, {status: 'pending', held_out: true}, {status: 'confirmed', held_out: true}, {status: 'confirmed'}, {status: 'assisted'}, {status: 'rejected'}, null];
  assert.deepEqual(pendingSampleCounts(samples), {review: 2, held_out: 1});
  assert.deepEqual(pendingSampleCounts(undefined), {review: 0, held_out: 0});
});
