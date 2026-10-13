const test = require('node:test');
const assert = require('node:assert/strict');
let h;
test.before(async () => { h = await import('../extension/discovery.mjs'); });
const ID = 'abcdefghijklmnop_123456789';
const URL = `https://docs.google.com/document/d/${ID}/edit`;
const candidate = {id: ID, title: 'Project notes', url: URL};

test('accepts canonical and account-specific document URLs, strips source query by canonicalizing', () => {
  assert.equal(h.documentId(`${URL}?token=redact#heading`), ID);
  assert.equal(h.documentId(`https://docs.google.com/document/u/2/d/${ID}/edit`), ID);
  const [item] = h.normalizeCandidates([{url: `${URL}?secret=value`, title: ' Notes  today '}], 'https://drive.google.com/drive/my-drive');
  assert.equal(item.url, URL); assert.equal(item.title, 'Notes today');
});
test('rejects spoofed domains, credentials, non-HTTPS, Sheets and published pages', () => {
  for (const url of [`http://docs.google.com/document/d/${ID}/edit`, `https://docs.google.com.evil.example/document/d/${ID}/edit`, `https://evil@docs.google.com/document/d/${ID}/edit`, `https://docs.google.com/spreadsheets/d/${ID}/edit`, 'javascript:alert(1)', `https://docs.google.com/document/d/e/${ID}/pub`]) assert.equal(h.documentId(url), null, url);
});
test('only accepts typed Drive row identifiers, never arbitrary data-id values', () => {
  const records = [{id: ID, title: 'Doc', typeHint: 'application/vnd.google-apps.document'}, {id: 'another_valid_identifier', title: 'Sheet', typeHint: 'Google Sheets'}, {id: '../escape', title: 'Bad', typeHint: 'Google Docs'}];
  assert.equal(h.normalizeCandidates(records, 'https://drive.google.com/drive/my-drive').length, 1);
  assert.equal(h.normalizeCandidates(records, 'https://example.com').length, 0);
  assert.equal(h.normalizeCandidates(records, 'https://docs.google.com').length, 0);
});
test('deduplicates candidate IDs and tolerates malformed records', () => {
  assert.deepEqual(h.normalizeCandidates(null, 'https://drive.google.com'), []);
  assert.equal(h.normalizeCandidates([null, {}, {url: URL}, {url: `${URL}?x=y`}], 'https://drive.google.com').length, 1);
});
test('rejects unknown, duplicate, empty and over-limit selections', () => {
  assert.equal(h.validateSelection([candidate], [ID])[0], candidate);
  for (const ids of [[], ['unknown'], [ID, ID], Array.from({length: 21}, (_, i) => String(i))]) assert.throws(() => h.validateSelection([candidate], ids));
});
test('exports import-compatible items with canonical provenance, context, and no authorship assertion', () => {
  const [item] = h.exportItems(candidate, 'A passage I will review.', {channel: 'personal', audience: 'Friend'});
  assert.deepEqual(Object.keys(item).sort(), ['context', 'source_type', 'source_url', 'text', 'title']);
  assert.equal(item.source_url, URL); assert.equal(item.context.channel, 'personal');
  assert.equal(item.context.audience, 'Friend'); assert.match(item.context.scenario, /authorship is unconfirmed/);
  assert.equal(item.status, undefined); assert.equal(item.confirmed, undefined);
});
test('splits long documents without silent text loss and keeps each part importable', () => {
  const original = 'First paragraph.\n\n' + 'word '.repeat(14000) + 'Final sentence.';
  const items = h.exportItems(candidate, original);
  assert.ok(items.length > 1); assert.equal(items.map(item => item.text).join(''), original);
  assert.ok(items.every(item => item.text.length <= 30000));
  assert.match(items[0].title, /part 1 of/);
});
test('does not split Unicode surrogate pairs at chunk boundaries', () => {
  const text = 'a'.repeat(29999) + '🌱' + 'b'.repeat(20);
  const items = h.exportItems(candidate, text);
  assert.equal(items.map(item => item.text).join(''), text);
  assert.equal(items[0].text.length, 29999); assert.ok(items[1].text.startsWith('🌱'));
});
test('rejects empty, HTML login responses and oversized documents without truncation', () => {
  for (const text of ['', '  ', '<!doctype html><title>Sign in</title>', '<html lang="en">Error</html>', 'x'.repeat(300001)]) assert.throws(() => h.exportItems(candidate, text));
});
test('export envelope includes only successfully collected item records', () => {
  const items = h.exportItems(candidate, 'A genuine candidate, awaiting review.');
  assert.deepEqual(h.exportEnvelope([{id: ID, state: 'ready', items}, {id: 'failed', state: 'failed', error: 'No access'}]), {items});
});
test('manifest grants no history, cookies, all-site or Google content-host permission', () => {
  const manifest = require('../extension/manifest.json');
  assert.deepEqual(manifest.optional_host_permissions, ['https://docs.google.com/*']);
  assert.equal(manifest.host_permissions, undefined);
  for (const permission of ['tabs', 'history', 'cookies', 'webRequest', '<all_urls>']) assert.ok(!manifest.permissions.includes(permission));
});
