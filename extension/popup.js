import { MAX_SELECTED, MAX_DOCUMENT_BYTES, MAX_BATCH_BYTES, DISCOVERY_TTL_MS, normalizeCandidates, readRenderedGoogleDocuments, exportItems, exportEnvelope } from './discovery.mjs';
const $ = id => document.getElementById(id);
let discovery = {candidates: [], selected_ids: [], results: [], run_state: 'idle', updated_at: Date.now()};
function safeUrl(value) {
  try { const u = new URL(value); if (!['https:', 'http:'].includes(u.protocol)) return ''; u.search = ''; u.hash = ''; return u.href; } catch { return ''; }
}
function populate(item) {
  $('title').value = (item.title || '').slice(0, 200); $('url').value = safeUrl(item.url || ''); $('text').value = (item.text || '').slice(0, 30000);
}
function showPanel(mode) {
  for (const name of ['sample', 'discovery']) { const active = mode === name; $(`${name}-panel`).hidden = !active; $(`show-${name}`).classList.toggle('active', active); $(`show-${name}`).setAttribute('aria-pressed', String(active)); }
}
function download(payload, filename) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type: 'application/json'}); const link = document.createElement('a');
  link.href = URL.createObjectURL(blob); link.download = filename; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}
async function persist() { discovery.updated_at = Date.now(); await chrome.storage.session.set({discovery}); }
function batchContext() { return {channel: $('batch-channel').value, audience: $('batch-audience').value.trim(), scenario: $('batch-scenario').value.trim()}; }
async function capture() {
  $('status').textContent = '';
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true}); if (!tab?.id) throw new Error('No active tab.');
    let text = '';
    try { const results = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: () => { const el = document.activeElement; if (el?.tagName === 'TEXTAREA') return el.value.slice(el.selectionStart, el.selectionEnd); return window.getSelection()?.toString() || ''; }}); text = results[0]?.result || ''; } catch { /* Canvas editors and restricted pages need a deliberate paste. */ }
    populate({title: tab.title || 'Browser sample', url: tab.url || '', text});
    $('status').textContent = text ? 'Review the selected excerpt before downloading.' : 'Selection unavailable here. Paste only the passage you want to review.';
  } catch { $('status').textContent = 'Could not access this tab. You can paste a sample manually.'; }
}
function renderCandidates() {
  const selected = new Set(discovery.selected_ids || []); const running = discovery.run_state === 'running';
  $('candidate-list').replaceChildren(); $('batch-controls').hidden = !discovery.candidates.length;
  for (const candidate of discovery.candidates) {
    const result = discovery.results.find(item => item.id === candidate.id);
    const row = document.createElement('article'); row.className = 'candidate';
    const label = document.createElement('label'); const check = document.createElement('input'); check.type = 'checkbox'; check.checked = selected.has(candidate.id); check.disabled = running;
    const title = document.createElement('span'); title.className = 'candidate-title'; title.textContent = candidate.title;
    check.addEventListener('change', async () => {
      const ids = new Set(discovery.selected_ids); if (check.checked) ids.add(candidate.id); else ids.delete(candidate.id);
      if (ids.size > MAX_SELECTED) { check.checked = false; $('discovery-status').textContent = 'Select no more than 20 documents per batch.'; return; }
      discovery.selected_ids = [...ids]; await persist(); renderCandidates();
    });
    label.append(check, title); row.append(label);
    const links = document.createElement('div'); links.className = 'candidate-links';
    for (const [text, url] of [['Open document', candidate.url], ['TXT download', `https://docs.google.com/document/d/${candidate.id}/export?format=txt`]]) { const link = document.createElement('a'); link.textContent = text; link.href = url; link.target = '_blank'; link.rel = 'noopener noreferrer'; links.append(link); }
    row.append(links);
    const state = document.createElement('p');
    state.textContent = result?.state === 'ready' ? `${result.items.length} sample part${result.items.length === 1 ? '' : 's'} collected · authorship unconfirmed` : result?.state === 'failed' ? result.error : 'Text not collected yet. Source type and context are provisional.';
    state.className = result?.state === 'failed' ? 'error' : result?.state === 'ready' ? 'ready' : ''; row.append(state);
    const fileLabel = document.createElement('label'); fileLabel.className = 'small'; fileLabel.textContent = 'Or attach this document’s downloaded TXT';
    const file = document.createElement('input'); file.type = 'file'; file.accept = '.txt,text/plain'; file.disabled = running;
    file.addEventListener('change', async () => {
      try {
        if (!file.files[0]) return;
        if (file.files[0].size > MAX_DOCUMENT_BYTES) throw new Error('Choose a TXT export of 1 MiB or less.');
        const items = exportItems(candidate, await file.files[0].text(), batchContext());
        const results = discovery.results.filter(item => item.id !== candidate.id); results.push({id: candidate.id, state: 'ready', items, method: 'user-attached-txt'});
        if (new TextEncoder().encode(JSON.stringify(exportEnvelope(results))).length > MAX_BATCH_BYTES) throw new Error('The collected-text limit is 4 MiB. Export or clear the current batch first.');
        discovery.results = results; await persist(); renderCandidates(); $('discovery-status').textContent = 'TXT attached to this source. Review authorship after importing.';
      } catch (error) { $('discovery-status').textContent = error.message; }
    });
    fileLabel.append(file); row.append(fileLabel); $('candidate-list').append(row);
  }
  const ready = discovery.results.filter(result => result.state === 'ready' && selected.has(result.id)).length;
  const failed = discovery.results.filter(result => result.state === 'failed' && selected.has(result.id)).length;
  $('discovery-summary').textContent = `${discovery.candidates.length} visible-source candidates saved · ${selected.size}/20 selected · ${ready} collected · ${failed} failed. Scroll Drive and discover again to add more visible rows.`;
  $('discover').disabled = running; $('fetch-documents').disabled = running || !selected.size;
  $('clear-discovery').disabled = running; $('forget-access').disabled = running; $('download-batch').disabled = !ready || running;
  $('fetch-documents').textContent = running ? `Collecting… ${ready + failed}/${selected.size} resolved` : 'Allow Docs access & collect selected text';
}
$('show-sample').addEventListener('click', () => showPanel('sample'));
$('show-discovery').addEventListener('click', () => showPanel('discovery'));
$('capture').addEventListener('click', capture);
$('export').addEventListener('click', async () => {
  const text = $('text').value.trim(); if (!text) { $('status').textContent = 'Add a writing excerpt first.'; $('text').focus(); return; }
  const item = {title: $('title').value.trim() || 'Browser sample', text, source_type: 'browser', source_url: safeUrl($('url').value), context: {channel: $('channel').value, audience: $('audience').value.trim(), scenario: $('scenario').value.trim()}};
  download({items: [item]}, 'voicework-browser-sample.json'); await chrome.storage.session.remove('capture'); $('status').textContent = 'Downloaded. Import this file in Voicework → Samples.';
});
$('discover').addEventListener('click', async () => {
  $('discover').disabled = true; $('discovery-status').textContent = 'Reading currently visible document candidates…';
  try {
    const [tab] = await chrome.tabs.query({active: true, currentWindow: true}); if (!tab?.id) throw new Error('No active tab.');
    const host = new URL(tab.url || '').hostname; if (!['drive.google.com', 'docs.google.com'].includes(host)) throw new Error('Open Google Drive or a Google Doc in this tab, then try again.');
    const responses = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: readRenderedGoogleDocuments});
    const result = responses[0]?.result; if (!result || result.error) throw new Error(result?.error || 'Could not read this tab.');
    const found = normalizeCandidates(result.records, result.pageUrl); const merged = new Map(discovery.candidates.map(item => [item.id, item]));
    for (const candidate of found) merged.set(candidate.id, candidate);
    if (merged.size > 200) throw new Error('This session reached 200 candidates. Export or clear it before discovering more.');
    discovery.candidates = [...merged.values()]; discovery.page_url = safeUrl(result.pageUrl); await persist(); renderCandidates();
    $('discovery-status').textContent = found.length ? `${found.length} document candidate${found.length === 1 ? '' : 's'} found in the current rendered view. Select the documents you want.` : 'No visible Google Docs links or recognizable document rows were found. Open a Doc directly, switch Drive to list view, or scroll to load more rows. Sheets, Slides, folders, and hidden/unloaded rows are not imported.';
  } catch (error) { $('discovery-status').textContent = error.message || 'Discovery failed. Open a Google Doc directly or use excerpt capture.'; }
  finally { $('discover').disabled = discovery.run_state === 'running'; }
});
$('fetch-documents').addEventListener('click', () => {
  // Request synchronously from the click gesture; never prompt on startup.
  chrome.permissions.request({origins: ['https://docs.google.com/*']}).then(async granted => {
    if (!granted) { $('discovery-status').textContent = 'Docs access was not granted. You can still download TXT yourself and attach it to a candidate.'; return; }
    $('fetch-documents').disabled = true; $('discovery-status').textContent = 'Collecting selected document text. You may close the popup; progress stays in this browser session.';
    try {
      const reply = await chrome.runtime.sendMessage({action: 'collect-documents', selected_ids: discovery.selected_ids, context: batchContext()});
      if (reply?.error) throw new Error(reply.error);
      $('discovery-status').textContent = 'Collection finished. Review individual results; failed documents are never replaced with empty samples.';
    } catch (error) { $('discovery-status').textContent = error.message; }
    finally { const saved = await chrome.storage.session.get('discovery'); if (saved.discovery) discovery = saved.discovery; renderCandidates(); }
  }).catch(error => { $('discovery-status').textContent = error.message; });
});
$('download-batch').addEventListener('click', () => {
  const selected = new Set(discovery.selected_ids); const results = discovery.results.filter(item => selected.has(item.id)); const payload = exportEnvelope(results);
  if (!payload.items.length) { $('discovery-status').textContent = 'Collect or attach text for at least one selected document first.'; return; }
  download(payload, 'voicework-google-docs-samples.json');
  const complete = results.filter(result => result.state === 'ready').length;
  $('discovery-status').textContent = `Downloaded ${payload.items.length} sample parts from ${complete}/${selected.size} selected documents. ${selected.size - complete} selected documents have no collected text and are not included. Import in Voicework → Samples to review authorship.`;
});
$('clear-discovery').addEventListener('click', async () => { discovery = {candidates: [], selected_ids: [], results: [], run_state: 'idle', updated_at: Date.now()}; await chrome.storage.session.remove('discovery'); renderCandidates(); $('discovery-status').textContent = 'Discovery candidates and collected text cleared from session storage.'; });
$('forget-access').addEventListener('click', async () => { await chrome.permissions.remove({origins: ['https://docs.google.com/*']}); $('discovery-status').textContent = 'Optional Google Docs host access removed. Current-tab discovery still works when you invoke it.'; });
chrome.storage.onChanged.addListener((changes, area) => { if (area === 'session' && changes.discovery?.newValue) { discovery = changes.discovery.newValue; renderCandidates(); } });
(async () => {
  const stored = await chrome.storage.session.get(['capture', 'discovery']);
  if (stored.discovery && Date.now() - stored.discovery.updated_at < DISCOVERY_TTL_MS) { discovery = stored.discovery; if (discovery.context) { $('batch-channel').value = discovery.context.channel || 'work'; $('batch-audience').value = discovery.context.audience || ''; $('batch-scenario').value = discovery.context.scenario || ''; } }
  else await chrome.storage.session.remove('discovery');
  if (discovery.run_state === 'running' && Date.now() - (discovery.run_started_at || discovery.updated_at) > 240000) {
    const completed = new Set(discovery.results.map(item => item.id));
    for (const id of discovery.selected_ids) if (!completed.has(id)) discovery.results.push({id, state: 'failed', error: 'The browser background task was interrupted. Select this document and retry.'});
    discovery.run_state = 'interrupted'; await persist();
    $('discovery-status').textContent = 'An interrupted collection was recovered. Completed text is preserved; unfinished documents are marked failed.';
  }
  renderCandidates();
  const item = stored.capture;
  if (item && Date.now() - Date.parse(item.captured_at) < DISCOVERY_TTL_MS) populate(item);
  await chrome.storage.session.remove('capture');
})();
