// Browser-owned document state. Editing and diagnostics never create training data.
export const DOCUMENT_SOURCE_LIMIT = 120000;
export const DOCUMENT_OUTPUT_LIMIT = 240000;
export const DOCUMENT_STORAGE_LIMIT = 2400000;
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const bounded = (value, limit) => typeof value === 'string' && value.length <= limit;
const contextLimits = {channel: 200, audience: 200, intent: 200, stakes: 200, relationship: 200, scenario: 2000};
export function cleanDocumentContext(value = {}) {
  return Object.fromEntries(Object.entries(contextLimits).map(([key, limit]) => [key, bounded(value?.[key], limit) ? value[key] : key === 'channel' ? 'work' : '']));
}
export function cleanAuthoredReferences(value = []) {
  if (!Array.isArray(value) || value.length > 6 || value.some(item => !object(item) || !bounded(item.id, 100) || !item.id.trim() || !bounded(item.aspect, 500) || !item.aspect.trim()) || new Set(value.map(item => item.id)).size !== value.length) return [];
  return value.map(item => ({id: item.id, aspect: item.aspect.trim()}));
}
export function cleanDocumentControls(value = {}) {
  return {...Object.fromEntries(['polish', 'detail', 'warmth', 'assertiveness'].map(key => [key, Number.isFinite(value?.[key]) && value[key] >= 0 && value[key] <= 100 ? value[key] : key === 'polish' || key === 'detail' ? 45 : 50])), structure: ['auto', 'prose', 'bullets'].includes(value?.structure) ? value.structure : 'auto', register: ['auto', 'casual', 'professional', 'formal'].includes(value?.register) ? value.register : 'auto', length: ['auto', 'preserve'].includes(value?.length) ? value.length : 'auto', revision: ['rewrite', 'copyedit'].includes(value?.revision) ? value.revision : 'rewrite'};
}
export function emptyDocument() {
  return {source: '', sourceName: '', text: '', context: cleanDocumentContext(), controls: cleanDocumentControls(), authoredReferences: [], excludedSampleIds: [], result: null, job: null, revision: 0, checkRevision: null, checkedSource: '', error: ''};
}
export function validDocumentJob(value) {
  return Boolean(object(value) && bounded(value.id, 200) && value.id.trim() && ['pending', 'running', 'complete', 'failed'].includes(value.status));
}
export function activeDocumentJob(value) { return validDocumentJob(value) && ['pending', 'running'].includes(value.status); }
export function documentChecksCurrent(value) {
  return Boolean(value?.result?.checks && Number.isSafeInteger(value.checkRevision) && value.checkRevision === value.revision && !activeDocumentJob(value.job));
}
export function reviseDocument(value, changes) {
  if (changes.context && JSON.stringify(value.context) !== JSON.stringify(changes.context)) changes = {...changes, authoredReferences: [], excludedSampleIds: []};
  const different = Object.entries(changes).some(([key, item]) => JSON.stringify(value[key]) !== JSON.stringify(item));
  return different ? {...value, ...changes, revision: value.revision + 1} : value;
}
export function documentDecisionChanged(value) {
  // A saved/undone decision changes the interpretation of diagnostics, never text.
  // Incrementing the revision also makes an already-running check stale.
  return {...value, revision: value.revision + 1, checkRevision: null};
}
export function validDocumentResult(value, kind = 'generate') {
  if (!object(value) || (kind === 'generate' && (!bounded(value.text, DOCUMENT_OUTPUT_LIMIT) || !value.text.trim()))) return false;
  return !value.checks || object(value.checks);
}
export function restoreDocument(value) {
  const result = emptyDocument();
  if (!object(value) || !bounded(value.source, DOCUMENT_SOURCE_LIMIT) || !bounded(value.text, DOCUMENT_OUTPUT_LIMIT)) return result;
  result.source = value.source; result.text = value.text;
  if (bounded(value.sourceName, 240)) result.sourceName = value.sourceName;
  result.context = cleanDocumentContext(value.context); result.controls = cleanDocumentControls(value.controls);
  result.authoredReferences = cleanAuthoredReferences(value.authoredReferences);
  result.excludedSampleIds = cleanExcludedSampleIds(value.excludedSampleIds);
  result.revision = Number.isSafeInteger(value.revision) && value.revision >= 0 && value.revision < Number.MAX_SAFE_INTEGER ? value.revision : 0;
  result.checkRevision = Number.isSafeInteger(value.checkRevision) && value.checkRevision >= 0 && value.checkRevision <= result.revision ? value.checkRevision : null;
  if (value.authoredReferences !== undefined && JSON.stringify(value.authoredReferences) !== JSON.stringify(result.authoredReferences)) result.checkRevision = null;
  if (value.excludedSampleIds !== undefined && JSON.stringify(value.excludedSampleIds) !== JSON.stringify(result.excludedSampleIds)) result.checkRevision = null;
  if (result.authoredReferences.some(item => result.excludedSampleIds.includes(item.id))) {result.excludedSampleIds = []; result.checkRevision = null;}
  if (bounded(value.checkedSource, DOCUMENT_SOURCE_LIMIT)) result.checkedSource = value.checkedSource;
  if (validDocumentResult(value.result, 'check') && JSON.stringify(value.result).length < 600000) result.result = value.result;
  if (bounded(value.error, 2000)) result.error = value.error;
  if (validDocumentJob(value.job) && ['generate', 'check'].includes(value.job.kind) && bounded(value.job.source, DOCUMENT_SOURCE_LIMIT) && bounded(value.job.text || '', DOCUMENT_OUTPUT_LIMIT) && Number.isSafeInteger(value.job.revision) && value.job.revision >= 0 && value.job.revision <= result.revision) {
    result.job = {id: value.job.id, status: value.job.status, kind: value.job.kind, source: value.job.source, text: value.job.text || '', context: cleanDocumentContext(value.job.context), controls: cleanDocumentControls(value.job.controls), revision: value.job.revision};
    if (value.job.excludedSampleIds !== undefined) result.job.excludedSampleIds = cleanExcludedSampleIds(value.job.excludedSampleIds);
    if (value.job.authoredReferences !== undefined) result.job.authoredReferences = cleanAuthoredReferences(value.job.authoredReferences);
  }
  return result;
}
export function acceptDocumentJob(document, response) {
  if (!validDocumentJob(response) || response.id !== document.job?.id) throw new Error('The document job response does not match this request. Your writing is preserved.');
  const next = {...document, job: {...document.job, status: response.status}, error: ''};
  if (response.status === 'failed') return {...next, error: bounded(response.error, 2000) ? response.error : 'The document request failed. Your source and current draft are preserved.'};
  if (response.status !== 'complete') return next;
  if (!validDocumentResult(response.result, document.job.kind) || (document.job.kind === 'check' && !response.result.checks)) throw new Error('The document result is incomplete or exceeds the supported size. Your writing is preserved.');
  if (document.job.kind === 'generate') {
    // An intervening local edit must never be silently overwritten by a late job.
    if (document.revision !== document.job.revision) return {...next, error: 'The request finished after your writing changed. Your current text was kept; generate again when ready.'};
    next.text = response.result.text;
    next.result = {...response.result, source_context: document.job.context};
  } else {
    next.result = {...document.result, ...response.result, text: document.result?.text || document.text};
  }
  next.checkedSource = document.job.source;
  next.checkRevision = response.result.checks ? document.job.revision : null;
  return next;
}
export function documentDownloadName(sourceName, extension = 'md') {
  const base = (typeof sourceName === 'string' ? sourceName : '').replace(/\.(md|txt)$/i, '').replace(/[^\p{L}\p{N}._ -]/gu, '').trim().slice(0, 180) || 'document';
  return `${base}-voicework.${extension === 'txt' ? 'txt' : 'md'}`;
}

export function cleanExcludedSampleIds(value) {
  if (!Array.isArray(value) || value.length > 100 || value.some(id => typeof id !== 'string' || !id.trim() || id.length > 100) || new Set(value).size !== value.length) return [];
  return [...value];
}
