const test = require('node:test');
const assert = require('node:assert/strict');
let changedSpan, hasSimilarScope, correctionScopeSummary;
test.before(async () => ({changedSpan, hasSimilarScope, correctionScopeSummary} = await import('../web/correction-diff.mjs')));

test('unchanged drafts have no correction', () => assert.equal(changedSpan('Same text.', 'Same text.').changed, false));
test('word replacement expands common letter prefixes/suffixes', () => {
  const result = changedSpan('We should utilize this.', 'We should use this.');
  assert.equal(result.before, 'utilize'); assert.equal(result.after, 'use');
});
test('middle-word edits produce complete terms', () => {
  const result = changedSpan('This is useful.', 'This is useless.');
  assert.equal(result.before, 'useful'); assert.equal(result.after, 'useless');
});
test('insertions and deletions preserve exact reconstructable spans', () => {
  for (const [original, revised] of [['Hello friend', 'Hello dear friend'], ['Hello dear friend', 'Hello friend'], ['', 'New text'], ['Old text', '']]) {
    const span = changedSpan(original, revised);
    assert.equal(original.slice(0, span.beforeStart) + span.after + original.slice(span.beforeEnd), revised);
    assert.equal(revised.slice(0, span.afterStart) + span.before + revised.slice(span.afterEnd), original);
  }
});
test('punctuation and paragraph changes are retained', () => {
  const span = changedSpan('One. Two.', 'One.\n\nTwo.');
  assert.equal(span.before, ' '); assert.equal(span.after, '\n\n');
});
test('Unicode words and emoji are not split into invalid surrogates', () => {
  for (const [a, b] of [['Café bientôt', 'Café demain'], ['Hi 🌱 there', 'Hi 🌳 there'], ['a🌱b', 'a🌳b']]) {
    const span = changedSpan(a, b);
    assert.equal(a.slice(0, span.beforeStart) + span.after + a.slice(span.beforeEnd), b);
    assert.ok(!/(?:[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF])/.test(span.after));
  }
});
test('multiple separated changes retain the whole intervening provenance', () => {
  const span = changedSpan('First old phrase, then old ending.', 'First new phrase, then fresh ending.');
  assert.equal(span.before, 'old phrase, then old'); assert.equal(span.after, 'new phrase, then fresh');
});
test('long differences are flagged without silently truncating', () => {
  const span = changedSpan('a'.repeat(700), 'b'.repeat(900));
  assert.equal(span.phraseFits, false); assert.equal(span.before.length, 700); assert.equal(span.after.length, 900);
});
test('similar scope requires an actual matching field, not scenario alone', () => {
  assert.equal(hasSimilarScope({channel:'work', scenario:'A project update'}), false);
  assert.equal(hasSimilarScope({audience:'  '}), false);
  assert.equal(hasSimilarScope({audience:'Team'}), true);
  assert.match(correctionScopeSummary({channel:'personal', audience:'Friend', scenario:'Dinner'}, 'similar'), /All listed conditions must match; recognized equivalent labels count/);
  assert.ok(!correctionScopeSummary({audience:'Friend', scenario:'Dinner'}).includes('Dinner'));
  assert.match(correctionScopeSummary({channel:'work', audience:'Team'}, 'channel'), /regardless of recipient/);
});
test('repeated words preserve a single exact reconstruction', () => {
  for (const [a, b] of [['go go go now', 'go go now'], ['the plan is the plan', 'the plan was the plan'], ['okay okay', 'okay very okay'], ['aaaa', 'aaa']]) {
    const span = changedSpan(a, b);
    assert.equal(a.slice(0, span.beforeStart) + span.after + a.slice(span.beforeEnd), b);
    assert.equal(b.slice(0, span.afterStart) + span.before + b.slice(span.afterEnd), a);
  }
});
test('whitespace-only and blank presentation edits are visible without invented phrases', () => {
  for (const [a, b] of [['a b', 'a  b'], ['A\nB', 'A\n\nB'], [' ', '\n'], ['text', ' text ']]) {
    const span = changedSpan(a, b);
    assert.equal(span.changed, true);
    assert.equal(a.slice(0, span.beforeStart) + span.after + a.slice(span.beforeEnd), b);
  }
});
test('grapheme-aware preview preserves composed accents and emoji sequences', () => {
  for (const [a, b] of [['Hi 👩🏽 there', 'Hi 👩🏻 there'], ['Family 👨‍👩‍👧 today', 'Family 👨‍👩‍👦 today'], ['A café today', 'A cafe today']]) {
    const span = changedSpan(a, b);
    assert.equal(a.slice(0, span.beforeStart) + span.after + a.slice(span.beforeEnd), b);
  }
  assert.equal(changedSpan('Hi 👩🏽 there', 'Hi 👩🏻 there').before, '👩🏽');
});
