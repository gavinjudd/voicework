import { MAX_DOCUMENT_BYTES, MAX_BATCH_BYTES, DISCOVERY_TTL_MS, validateSelection, exportItems } from './discovery.mjs';

chrome.runtime.onInstalled.addListener(() => {
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({id: 'capture', title: 'Review sample in Voicework', contexts: ['selection']});
  });
});
chrome.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== 'capture') return;
  await chrome.storage.session.set({capture: {text: (info.selectionText || '').slice(0, 30000), title: tab?.title || 'Browser sample', url: info.pageUrl || '', captured_at: new Date().toISOString()}});
  try { await chrome.action.openPopup(); } catch { /* Open the toolbar icon to review. */ }
});

async function readPlainText(response) {
  if (!response.ok) throw new Error(`Google returned HTTP ${response.status}. Open the document and check access.`);
  if (!/^text\/plain(?:;|$)/i.test(response.headers.get('content-type') || '')) throw new Error('Google did not return plain text. Sign in or use the document’s TXT download, then attach it.');
  const finalUrl = new URL(response.url);
  if (finalUrl.hostname !== 'docs.google.com') throw new Error('The export redirected outside the granted Docs host. Use TXT download and attach the file.');
  const declaredSize = Number(response.headers.get('content-length'));
  if (declaredSize > MAX_DOCUMENT_BYTES) throw new Error('Export exceeds the 1 MiB per-document read limit. Download and select a smaller excerpt.');
  const reader = response.body?.getReader();
  if (!reader) throw new Error('This browser did not provide a readable export. Use TXT download instead.');
  const decoder = new TextDecoder(); let bytes = 0; let text = '';
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) break;
      bytes += value.byteLength;
      if (bytes > MAX_DOCUMENT_BYTES) { await reader.cancel(); throw new Error('Export exceeds the 1 MiB per-document read limit.'); }
      text += decoder.decode(value, {stream: true});
    }
    return text + decoder.decode();
  } finally { reader.releaseLock(); }
}
async function collectSelected(message) {
  if (!await chrome.permissions.contains({origins: ['https://docs.google.com/*']})) throw new Error('Google Docs access was not granted. Use the manual TXT attachment option.');
  const {discovery} = await chrome.storage.session.get('discovery');
  if (!discovery || Date.now() - discovery.updated_at > DISCOVERY_TTL_MS) throw new Error('Discovery expired. Discover the current tab again.');
  if (discovery.run_state === 'running') throw new Error('A collection is already running.');
  const selected = validateSelection(discovery.candidates, message.selected_ids);
  const selectedIds = new Set(selected.map(item => item.id));
  const run = {...discovery, run_started_at: Date.now(), selected_ids: [...selectedIds], run_state: 'running', run_id: crypto.randomUUID(), updated_at: Date.now(), results: (discovery.results || []).filter(result => !selectedIds.has(result.id)), context: message.context || {}};
  await chrome.storage.session.set({discovery: run});
  const started = Date.now();
  for (const candidate of selected) {
    let result;
    if (Date.now() - started > 180000) result = {id: candidate.id, state: 'failed', error: 'The 3-minute batch limit was reached. Select this document again to retry.'};
    else {
      const controller = new AbortController(); const timer = setTimeout(() => controller.abort(), 12000);
      try {
        // Browser manages the existing session. Never read cookies, tokens, or page internals.
        const response = await fetch(`https://docs.google.com/document/d/${encodeURIComponent(candidate.id)}/export?format=txt`, {credentials: 'include', redirect: 'error', signal: controller.signal});
        const text = await readPlainText(response);
        const items = exportItems(candidate, text, run.context);
        if (new TextEncoder().encode(JSON.stringify({items: [...run.results.flatMap(item => item.items || []), ...items]})).length > MAX_BATCH_BYTES) throw new Error('The 4 MiB collected-text limit was reached. Export or clear this batch before collecting more.');
        result = {id: candidate.id, state: 'ready', items, method: 'authenticated-txt-export'};
      } catch (error) {
        result = {id: candidate.id, state: 'failed', error: error.name === 'AbortError' ? 'Google did not finish within 12 seconds. Retry or use TXT download.' : error instanceof TypeError ? 'Google export could not be read directly (network, access, or redirect). Open TXT download and attach the file.' : error.message};
      } finally { clearTimeout(timer); }
    }
    run.results.push(result); run.updated_at = Date.now(); await chrome.storage.session.set({discovery: run});
  }
  run.run_state = 'complete'; run.updated_at = Date.now(); await chrome.storage.session.set({discovery: run});
  return {ok: true};
}
let collecting = false;
chrome.runtime.onMessage.addListener((message, sender, reply) => {
  if (message?.action !== 'collect-documents') return false;
  if (sender.id !== chrome.runtime.id) { reply({error: 'Unsupported sender.'}); return false; }
  if (collecting) { reply({error: 'A collection is already running.'}); return false; }
  collecting = true;
  collectSelected(message).then(reply).catch(error => reply({error: error.message})).finally(() => { collecting = false; });
  return true;
});
