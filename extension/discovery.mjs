// Pure filtering/schema helpers shared by the extension and Node tests.
export const MAX_SELECTED = 20;
export const MAX_DOCUMENT_BYTES = 1024 * 1024;
export const MAX_DOCUMENT_CHARS = 300000;
export const MAX_BATCH_BYTES = 4 * 1024 * 1024;
export const DISCOVERY_TTL_MS = 15 * 60 * 1000;
const DOCUMENT_ID = /^[A-Za-z0-9_-]{16,200}$/;

export function documentId(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.hostname !== 'docs.google.com' || url.username || url.password) return null;
    return url.pathname.match(/^\/document\/(?:u\/\d+\/)?d\/([A-Za-z0-9_-]{16,200})(?:\/|$)/)?.[1] || null;
  } catch { return null; }
}
export function canonicalDocumentUrl(id) {
  if (!DOCUMENT_ID.test(id)) throw new Error('Invalid Google document ID.');
  return `https://docs.google.com/document/d/${id}/edit`;
}
export function isGoogleDocumentType(value) {
  const text = String(value || '');
  return /application\/vnd\.google-apps\.document/i.test(text) || /\bGoogle Docs\b/i.test(text);
}
export function normalizeCandidates(records, pageUrl) {
  let page;
  try { page = new URL(pageUrl); } catch { return []; }
  if (page.protocol !== 'https:' || !['drive.google.com', 'docs.google.com'].includes(page.hostname)) return [];
  if (!Array.isArray(records)) return [];
  const candidates = new Map();
  for (const record of records.slice(0, 500)) {
    if (!record || typeof record !== 'object') continue;
    const linkId = documentId(record.url);
    const rowId = page.hostname === 'drive.google.com' && isGoogleDocumentType(record.typeHint) && DOCUMENT_ID.test(record.id || '') ? record.id : null;
    const id = linkId || rowId;
    if (!id || candidates.has(id)) continue;
    candidates.set(id, { id, title: String(record.title || 'Untitled Google Doc').replace(/\s+/g, ' ').trim().slice(0, 200), url: canonicalDocumentUrl(id), hint: String(record.typeHint || 'Google document link').replace(/\s+/g, ' ').trim().slice(0, 300) });
  }
  return [...candidates.values()];
}
export function validateSelection(candidates, selectedIds) {
  if (!Array.isArray(candidates) || !Array.isArray(selectedIds) || selectedIds.length < 1 || selectedIds.length > MAX_SELECTED || new Set(selectedIds).size !== selectedIds.length) throw new Error('Select between 1 and 20 documents.');
  return selectedIds.map(id => {
    const candidate = candidates.find(item => item.id === id);
    if (!candidate || documentId(candidate.url) !== id) throw new Error('A selected document is not a valid discovered candidate.');
    return candidate;
  });
}
export function exportItems(candidate, content, context = {}) {
  if (documentId(candidate.url) !== candidate.id) throw new Error('Invalid source document.');
  const text = String(content || '').replace(/^\uFEFF/, '').replace(/\r\n/g, '\n').trim();
  if (!text) throw new Error('The document export was empty.');
  if (/^\s*(?:<!doctype\s+html|<html[\s>])/i.test(text)) throw new Error('Google returned a sign-in or error page, not document text.');
  if (text.length > MAX_DOCUMENT_CHARS) throw new Error('This document exceeds 300,000 characters. Download it and import selected excerpts instead.');
  const chunks = [];
  let position = 0;
  while (position < text.length) {
    let end = Math.min(position + 30000, text.length);
    if (end < text.length) {
      const paragraph = text.lastIndexOf('\n\n', end - 1);
      if (paragraph > position + 15000) end = paragraph + 2;
      if (/[\uD800-\uDBFF]/.test(text[end - 1])) end--;
    }
    chunks.push(text.slice(position, end)); position = end;
  }
  const title = String(candidate.title || 'Google Doc').slice(0, 170);
  const scenario = String(context.scenario || `Selected Google Doc: ${title}. Title is a provisional context hint; authorship is unconfirmed.`).slice(0, 2000);
  return chunks.map((text, index) => ({ title: chunks.length > 1 ? `${title} · part ${index + 1} of ${chunks.length}` : title, text, source_type: 'browser-google-docs', source_url: canonicalDocumentUrl(candidate.id), context: { channel: String(context.channel || 'work').slice(0, 200), audience: String(context.audience || '').slice(0, 200), scenario } }));
}
export function exportEnvelope(results) {
  return { items: results.filter(result => result.state === 'ready').flatMap(result => result.items || []) };
}

// Serialized by chrome.scripting.executeScript: keep this function self-contained.
export function readRenderedGoogleDocuments() {
  if (location.protocol !== 'https:' || !['drive.google.com', 'docs.google.com'].includes(location.hostname)) return { pageUrl: location.href, records: [], error: 'Open a Google Drive list or a Google Doc first.' };
  const records = [];
  const visible = element => {
    const rect = element.getBoundingClientRect();
    const style = getComputedStyle(element);
    return rect.width > 0 && rect.height > 0 && rect.bottom > 0 && rect.right > 0 && rect.top < innerHeight && rect.left < innerWidth && style.visibility !== 'hidden' && style.display !== 'none';
  };
  if (location.hostname === 'docs.google.com' && /^\/document\//.test(location.pathname)) records.push({ url: location.href, title: document.title.replace(/\s*[-–]\s*Google Docs\s*$/, ''), typeHint: 'Current Google Doc' });
  for (const anchor of [...document.querySelectorAll('a[href]')].slice(0, 2000)) {
    if (!visible(anchor)) continue;
    if (/^https:\/\/docs\.google\.com\/document\//.test(anchor.href)) records.push({ url: anchor.href, title: anchor.innerText || anchor.getAttribute('aria-label') || anchor.getAttribute('title') || 'Google Doc', typeHint: 'Visible Google Docs link' });
  }
  if (location.hostname === 'drive.google.com') {
    for (const element of [...document.querySelectorAll('[role="row"][data-id], [role="row"] [data-id], [role="gridcell"][data-id]')].slice(0, 1000)) {
      if (!visible(element)) continue;
      const row = element.closest('[role="row"]') || element;
      const imageLabels = [...row.querySelectorAll('[aria-label], img[alt], [data-mime-type]')].slice(0, 30).map(node => node.getAttribute('aria-label') || node.getAttribute('alt') || node.getAttribute('data-mime-type') || '').join(' ');
      const typeHint = [row.getAttribute('aria-label'), row.getAttribute('data-mime-type'), element.getAttribute('data-mime-type'), imageLabels].filter(Boolean).join(' ').slice(0, 2000);
      if (!/Google Docs|application\/vnd\.google-apps\.document/i.test(typeHint)) continue;
      const name = row.querySelector('[data-tooltip]');
      const title = (name?.getAttribute('data-tooltip') || row.innerText?.split('\n').find(line => line.trim()) || row.getAttribute('aria-label') || 'Google Doc').slice(0, 200);
      records.push({ id: element.getAttribute('data-id'), title, typeHint });
    }
  }
  return { pageUrl: location.href, records: records.slice(0, 500), inspected: records.length };
}
