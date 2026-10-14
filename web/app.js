import {changedSpan, hasSimilarScope, correctionScopeSummary} from './correction-diff.mjs';
import {confidenceView, pendingSampleCounts} from './voice-confidence.mjs';
import {DOCUMENT_SOURCE_LIMIT, DOCUMENT_OUTPUT_LIMIT, DOCUMENT_STORAGE_LIMIT, cleanDocumentContext, emptyDocument, restoreDocument, validDocumentJob, activeDocumentJob, documentChecksCurrent, reviseDocument, acceptDocumentJob, documentDownloadName} from './document-state.mjs';
const app = document.querySelector('#app');
const toastRegion = document.querySelector('#toast-region');
const routes = { write: 'Write', practice: 'Practice', samples: 'Samples', evaluate: 'Check fit', profile: 'Voice profile' };
const ui = {
  route: routes[location.hash.slice(1)] ? location.hash.slice(1) : 'write',
  filter: 'pending', context: { channel: 'work', audience: '', intent: '', stakes: '', relationship: '', scenario: '' },
  selections: new Map(), draft: null, brief: '', controls: { polish: 45, detail: 45, warmth: 50, assertiveness: 50, structure: 'auto', register: 'auto' },
  scanResult: null, scanRunning: null, exportText: '', includeExamples: false, storyTarget: 'spoken interview answer', storyChannels: null, storyExamples: false, storyVoice: null, practiceFocus: 'vernacular',
  lessonDraft: {dimension: 'vernacular', guidance: '', preferred: '', avoided: ''},
  editedDraft: '', draftUndo: null, rememberDraft: null, replacePrompt: false, generating: false, editorStorageError: false,
  qualityForm: {correctionId: '', brief: '', context: null}, qualityVotes: {},
  wholeVoiceOpen: false, writeMode: 'message', document: emptyDocument(), documentStorageError: false, documentStarting: false, documentReplacePrompt: false, documentContextPreview: null,
};
let state;
let nextId = 0;
let toastTimer;
const editorStorageKey = 'voicework-editor-v2';
function persistEditor() {
  try {
    localStorage.setItem(editorStorageKey, JSON.stringify({brief: ui.brief, draft: ui.draft, editedDraft: ui.editedDraft, draftUndo: ui.draftUndo, context: ui.context, controls: ui.controls, rememberDraft: ui.rememberDraft, qualityForm: ui.qualityForm, qualityVotes: ui.qualityVotes}));
    ui.editorStorageError = false;
  } catch { ui.editorStorageError = true; }
}
try {
  const saved = JSON.parse(localStorage.getItem(editorStorageKey) || 'null');
  if (saved && typeof saved === 'object') {
    if (typeof saved.brief === 'string') ui.brief = saved.brief.slice(0, 15000);
    if (saved.draft && typeof saved.draft.text === 'string' && saved.draft.text.length <= 30000) {
      ui.draft = saved.draft;
      ui.editedDraft = typeof saved.editedDraft === 'string' ? saved.editedDraft.slice(0, 30000) : saved.draft.text;
      ui.draftUndo = typeof saved.draftUndo === 'string' ? saved.draftUndo.slice(0, 30000) : null;
      if (saved.rememberDraft && typeof saved.rememberDraft.original_text === 'string' && typeof saved.rememberDraft.revised_text === 'string') ui.rememberDraft = saved.rememberDraft;
    }
    if (saved.context && typeof saved.context === 'object') for (const key of Object.keys(ui.context)) if (typeof saved.context[key] === 'string') ui.context[key] = saved.context[key];
    if (saved.controls && typeof saved.controls === 'object') for (const key of Object.keys(ui.controls)) if (key === 'structure' ? ['auto','prose','bullets'].includes(saved.controls[key]) : key === 'register' ? ['auto','casual','professional','formal'].includes(saved.controls[key]) : Number.isFinite(saved.controls[key]) && saved.controls[key] >= 0 && saved.controls[key] <= 100) ui.controls[key] = saved.controls[key];
    if (saved.qualityForm && typeof saved.qualityForm === 'object') ui.qualityForm = {...ui.qualityForm, ...saved.qualityForm};
    if (saved.qualityVotes && typeof saved.qualityVotes === 'object') ui.qualityVotes = saved.qualityVotes;
  }
} catch { ui.editorStorageError = true; }
window.addEventListener('pagehide', persistEditor);
const documentStorageKey = 'voicework-document-v1';
let documentSaveTimer, documentPollTimer, documentPolling = false, documentPageClosing = false;
function persistDocument() {
  clearTimeout(documentSaveTimer);
  try {
    const saved = JSON.stringify({mode: ui.writeMode, document: ui.document});
    if (saved.length > DOCUMENT_STORAGE_LIMIT) throw new Error('Document storage limit exceeded.');
    localStorage.setItem(documentStorageKey, saved); ui.documentStorageError = false;
  } catch { ui.documentStorageError = true; }
  updateDocumentStorageNotice();
}
function queueDocumentSave() { clearTimeout(documentSaveTimer); documentSaveTimer = setTimeout(persistDocument, 350); }
function updateDocumentStorageNotice() {
  const notice = document.querySelector('#document-storage-error');
  if (notice) { notice.hidden = !ui.documentStorageError; notice.textContent = 'Browser storage is unavailable or full. Download your source and edited draft before refreshing or closing this page.'; }
}
try {
  const raw = localStorage.getItem(documentStorageKey);
  if (raw && raw.length > DOCUMENT_STORAGE_LIMIT) throw new Error('Document storage is too large.');
  const saved = JSON.parse(raw || 'null');
  if (saved) { ui.document = restoreDocument(saved.document); ui.writeMode = saved.mode === 'document' ? 'document' : 'message'; }
} catch { ui.documentStorageError = true; }
window.addEventListener('beforeunload', () => {documentPageClosing = true; persistDocument();});
window.addEventListener('pagehide', () => {documentPageClosing = true; persistDocument();});
window.addEventListener('pageshow', () => {documentPageClosing = false;});

const uid = (prefix = 'field') => `${prefix}-${++nextId}`;
const plural = (number, single, multiple = `${single}s`) => `${number} ${number === 1 ? single : multiple}`;
const asText = value => value === null || value === undefined ? '' : typeof value === 'string' ? value : JSON.stringify(value);

function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [name, value] of Object.entries(attrs)) {
    if (value === undefined || value === null || value === false) continue;
    if (name === 'class') node.className = value;
    else if (name === 'text') node.textContent = asText(value);
    else if (name === 'style') {
      for (const declaration of value.split(';')) {
        const separator = declaration.indexOf(':');
        if (separator > 0) node.style.setProperty(declaration.slice(0, separator).trim(), declaration.slice(separator + 1).trim());
      }
    }
    else if (name.startsWith('on') && typeof value === 'function') node.addEventListener(name.slice(2).toLowerCase(), value);
    else if (name === 'checked' || name === 'disabled' || name === 'selected' || name === 'required' || name === 'open') node[name] = Boolean(value);
    else if (name === 'value') node.value = value;
    else node.setAttribute(name, value === true ? '' : asText(value));
  }
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(asText(child)));
  }
  return node;
}

const paths = {
  practice: ['M4 4h6v6H4z', 'M14 4h6v6h-6z', 'M4 14h6v6H4z', 'M14 17h6', 'M17 14v6'],
  samples: ['M6 3h9l4 4v14H6z', 'M14 3v5h5', 'M9 12h7', 'M9 16h5'],
  write: ['M14 5l5 5', 'M4 20l4-1 13-13a2.1 2.1 0 0 0-3-3L5 16z', 'M13 20h8'],
  profile: ['M4 17v-3', 'M9 20V8', 'M14 17V4', 'M19 20V11'],
  lock: ['M6 11h12v10H6z', 'M8 11V7a4 4 0 0 1 8 0v4', 'M12 15v2'],
  arrow: ['M5 12h14', 'M14 7l5 5-5 5'],
  plus: ['M12 5v14', 'M5 12h14'],
  check: ['M5 12l4 4L19 6'],
  close: ['M6 6l12 12', 'M18 6L6 18'],
  info: ['M12 8h.01', 'M12 11v5'],
  folder: ['M3 7V5h7l2 3h9v12H3z'],
  download: ['M12 3v12', 'M7 10l5 5 5-5', 'M4 16v5h16v-5'],
  upload: ['M12 16V4', 'M7 9l5-5 5 5', 'M4 17v4h16v-4'],
  copy: ['M8 8h12v13H8z', 'M16 8V3H3v13h5'],
  spark: ['M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z'],
  feather: ['M4 21L17 8', 'M7 17c-3-3-1-9 4-12 4-2 9-2 10-2 0 3 0 8-3 12-3 4-7 4-11 2z', 'M12 13h6'],
  globe: ['M3 12h18', 'M12 3c6 6 6 12 0 18-6-6-6-12 0-18'],
  shield: ['M12 3l8 3v6c0 5-8 9-8 9s-8-4-8-9V6z', 'M8 12l3 3 5-6'],
  refresh: ['M20 7v5h-5', 'M4 17v-5h5', 'M5 8a8 8 0 0 1 13-3l2 3', 'M19 16a8 8 0 0 1-13 3l-2-3'],
};
function icon(name, size = 18) {
  const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg');
  for (const [key, value] of Object.entries({ viewBox: '0 0 24 24', width: size, height: size, fill: 'none', stroke: 'currentColor', 'stroke-width': '1.5', 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'aria-hidden': 'true' })) svg.setAttribute(key, value);
  if (name === 'info' || name === 'globe') {
    const circle = document.createElementNS(svg.namespaceURI, 'circle');
    for (const [key, value] of Object.entries({ cx: 12, cy: 12, r: 9 })) circle.setAttribute(key, value);
    svg.append(circle);
  }
  for (const d of paths[name] || paths.info) {
    const path = document.createElementNS(svg.namespaceURI, 'path'); path.setAttribute('d', d); svg.append(path);
  }
  return svg;
}
function button(label, handler, style = '', iconName) {
  return el('button', { type: 'button', class: `button ${style}`, onClick: handler }, iconName && icon(iconName), label);
}
function badge(label, tone = '') { return el('span', { class: `badge ${tone}` }, label); }
function field(label, control, optional = '') {
  control.id ||= uid();
  return el('div', { class: 'field' }, el('label', { class: 'field-label', for: control.id }, label, optional && el('span', { class: 'optional' }, optional)), control);
}
function input(attrs = {}) { return el('input', { type: 'text', ...attrs }); }
function select(options, value, attrs = {}) {
  const control = el('select', attrs, options.map(option => {
    const [key, label] = Array.isArray(option) ? option : [option, option];
    return el('option', { value: key, selected: key === value }, label);
  }));
  return control;
}
function details(label, body, open = false) { return el('details', { class: 'details', open }, el('summary', {}, label), body); }
function helper(text) { return el('p', { class: 'helper' }, text); }
function toast(message, error = false) {
  clearTimeout(toastTimer);
  toastRegion.replaceChildren(el('div', { class: `toast ${error ? 'error' : ''}` }, icon(error ? 'info' : 'check'), message));
  toastTimer = setTimeout(() => toastRegion.replaceChildren(), error ? 10000 : 4500);
}
async function api(path, method = 'GET', body) {
  const options = { method, headers: { Accept: 'application/json' }, credentials: 'same-origin' };
  if (method !== 'GET') {
    options.headers['Content-Type'] = 'application/json';
    options.headers['X-Voicework-Token'] = state?.token || '';
    options.body = JSON.stringify(body || {});
  }
  let response;
  try { response = await fetch(`/api${path}`, options); }
  catch { const error = new Error('The local workspace is not responding. Keep the Voicework server running, then try again.'); error.networkError = true; throw error; }
  let data;
  try { data = await response.json(); }
  catch { throw new Error('The server returned an unreadable response. Please try again.'); }
  if (!response.ok) {const error = new Error(data.error || `Request failed (${response.status}).`); error.status = response.status; throw error;}
  return data;
}
async function refresh(renderPage = true) {
  state = await api('/state');
  ui.exportText = '';
  ui.storyVoice = null; // Revoked lessons must not linger in a stale story-voice preview.
  if (renderPage) render();
}
async function busy(control, callback, label = 'Saving…') {
  const old = [...control.childNodes];
  control.disabled = true;
  control.replaceChildren(document.createTextNode(label));
  control.setAttribute('aria-busy', 'true');
  try { await callback(); }
  catch (error) { toast(error.message || 'Something went wrong. Please try again.', true); }
  finally { control.disabled = false; control.removeAttribute('aria-busy'); control.replaceChildren(...old); }
}
function navigate(route) {
  if (!routes[route]) return;
  ui.route = route;
  if (location.hash !== `#${route}`) history.replaceState(null, '', `#${route}`);
  render();
  document.querySelector('#main')?.focus({ preventScroll: true });
  window.scrollTo({ top: 0, behavior: 'instant' });
}
window.addEventListener('hashchange', () => {
  if (routes[location.hash.slice(1)]) { ui.route = location.hash.slice(1); render(); }
});
const LIVE_PROVIDERS = new Set(['codex', 'anthropic', 'claude']);
const PROVIDER_LABELS = { codex: 'Codex', anthropic: 'Anthropic API', claude: 'Claude', offline: 'Offline rehearsal' };
function isLive(provider = state?.settings?.provider) { return LIVE_PROVIDERS.has(provider); }
function modelName(provider = state?.settings?.provider) { return PROVIDER_LABELS[provider] || 'the model'; }
function providerPhrase(provider = state?.settings?.provider) { return provider === 'anthropic' ? 'the Anthropic API' : modelName(provider); }
function providerName(provider = state?.settings?.provider) { return isLive(provider) ? `${modelName(provider)} configured` : 'Offline rehearsal'; }
function providerPill(provider = state?.settings?.provider) {
  const enabled = isLive(provider);
  return el('span', { class: 'status-pill', title: enabled ? `Generation uses your configured ${modelName(provider)} provider.` : 'Deterministic local examples. No AI model is running.' }, el('span', { class: `status-dot ${enabled ? 'live' : ''}` }), providerName(provider));
}
function contextPills(context = {}) {
  return el('div', { class: 'context-pills' }, ['channel', 'audience', 'intent', 'relationship', 'stakes'].filter(key => context[key]).map(key => el('span', { class: 'context-pill', title: key }, context[key])));
}
function contextEditor(seed = ui.context, options = {}) {
  const values = { channel: 'work', audience: '', intent: '', stakes: '', relationship: '', scenario: '', ...seed };
  const controls = {};
  const contextId = uid('contexts');
  const channel = controls.channel = input({ value: values.channel, list: contextId, maxlength: 200, placeholder: 'work, personal, community…' });
  const audience = controls.audience = input({ value: values.audience, maxlength: 200, placeholder: 'A teammate, close friend…' });
  const wrap = el('div', { class: 'context-editor' }, el('div', { class: 'form-row' }, field('Context', channel), field('Who is this for?', audience, 'optional')), el('datalist', { id: contextId }, ['work', 'personal', 'community', 'public writing', 'long-form writing', 'technical article', 'reflective essay', 'project case study'].map(value => el('option', { value }))));
  const more = el('div', {}, el('div', { class: 'form-row' },
    field('What should it achieve?', controls.intent = input({ value: values.intent, maxlength: 200, placeholder: 'Ask for a decision, reconnect…' })),
    field('Relationship', controls.relationship = input({ value: values.relationship, maxlength: 200, placeholder: 'Peer, manager, close friend…' }))),
    field('What is at stake?', controls.stakes = input({ value: values.stakes, maxlength: 200, placeholder: 'Routine update, difficult conversation…' })));
  const scenario = field(options.scenarioLabel || 'Situation', controls.scenario = el('textarea', { value: values.scenario, rows: 2, maxlength: 2000, placeholder: 'The situation and facts your writing needs to fit.', style: 'min-height: 70px' }), 'optional');
  if (options.scenario) wrap.append(scenario);
  else more.append(scenario);
  wrap.append(details('Intent, relationship & more context', more, options.expanded));
  Object.entries(controls).forEach(([key, control]) => control.addEventListener('input', () => { if (options.remember !== false) { ui.context[key] = control.value; persistEditor(); } if (options.onChange) options.onChange(Object.fromEntries(Object.entries(controls).map(([name, input]) => [name, input.value.trim()]))); }));
  return { node: wrap, controls, get: () => Object.fromEntries(Object.entries(controls).map(([key, control]) => [key, control.value.trim()])) };
}
function pageHeading(eyebrow, title, description, action) {
  return el('div', { class: 'page-heading' }, el('div', {}, el('div', { class: 'eyebrow' }, eyebrow), el('h1', {}, title), el('p', {}, description)), action);
}
function panelHeading(title, description, extra) {
  return el('div', { class: 'panel-head' }, el('div', {}, el('h2', {}, title), description && el('p', {}, description)), extra);
}
function callout(title, text, tone = '', action) {
  return el('div', { class: `callout ${tone}` }, icon(tone === 'warning' ? 'info' : 'shield'), el('div', {}, el('strong', {}, title), el('p', {}, text)), action);
}
function empty(title, description, action, iconName = 'samples') {
  return el('div', { class: 'empty-state' }, icon(iconName), el('h3', {}, title), el('p', {}, description), action);
}
function render() {
  if (!state) return;
  const pending = state.samples.filter(sample => sample.status === 'pending').length;
  const nav = el('nav', { class: 'nav', 'aria-label': 'Workspace' }, Object.entries(routes).map(([key, label]) => el('button', { class: `nav-button ${ui.route === key ? 'active' : ''}`, 'aria-current': ui.route === key ? 'page' : undefined, onClick: () => navigate(key) }, icon(key), label, key === 'samples' && pending > 0 && el('span', { class: 'nav-count' }, pending))));
  const sidebar = el('aside', { class: 'sidebar' }, el('div', { class: 'brand' }, el('span', { class: 'brand-mark', 'aria-hidden': 'true' }, 'v.'), el('div', {}, el('div', { class: 'brand-name' }, 'voicework'), el('div', { class: 'brand-caption' }, 'A practice in sounding like you'))), el('div', { class: 'nav-label' }, 'Your workspace'), nav,
    el('div', { class: 'sidebar-bottom' }, el('div', { class: 'local-label' }, icon('lock'), 'Local by default'), el('p', { class: 'sidebar-note' }, isLive() ? `Samples stay in this workspace. Selected evidence is sent to ${providerPhrase()} when you generate.` : 'Your samples and feedback stay in this workspace. You decide what becomes evidence.'), el('div', { class: 'sidebar-footer' }, el('span', {}, 'VOICEWORK / LOCAL'), el('span', {}, 'v0.1'))));
  const pages = { practice: renderPractice, samples: renderSamples, write: renderWrite, evaluate: renderEvaluate, profile: renderProfile };
  const main = el('main', { id: 'main', class: 'main fade-in', tabindex: '-1' }, pages[ui.route]());
  const workspace = el('div', { class: 'workspace' }, el('header', { class: 'topbar' }, el('div', { class: 'breadcrumb' }, el('span', {}, 'Personal workspace'), el('span', { class: 'slash' }, '/'), el('strong', {}, routes[ui.route])), providerPill()), main);
  app.replaceChildren(sidebar, workspace);
  app.removeAttribute('aria-busy');
  document.title = `${routes[ui.route]} · Voicework`;
}
const languageDimensions = {
  vernacular: {label: 'Vernacular', plain: 'Everyday expressions', description: 'Contractions, idioms, and expressions you naturally use.', example: '“Sounds good” or “That works for me” — which would you actually say?', guidance: 'e.g. Use contractions and everyday expressions with friends. “Sounds good” feels natural; “That is acceptable” does not.', preferred: 'e.g. Sounds good to me', avoided: 'e.g. That is acceptable', coaching: 'Which expressions feel natural or borrowed?', note: 'e.g. “I say ‘sounds good,’ never ‘certainly.’ Keep the contractions.”'},
  syntax: {label: 'Syntax', plain: 'Sentence structure', description: 'How you arrange ideas: clauses, fragments, and sentence openings.', example: 'Do you lead with the point, add a qualifier first, or use a short fragment?', guidance: 'e.g. Lead with the point, then explain why. Short fragments are fine in a text to a friend.', preferred: 'e.g. I can do Friday. Does that work?', avoided: 'e.g. If that works for you, Friday is a day on which I am available.', coaching: 'Which sentence shape fits how you think?', note: 'e.g. “Put the answer first. I would split that long sentence in two.”'},
  diction: {label: 'Diction', plain: 'Word choice', description: 'The specific words you favor, their precision, and their level of formality.', example: '“Use” or “utilize”? Name the words you reach for and the ones you avoid.', guidance: 'e.g. Use concrete, ordinary words in work updates. Avoid corporate filler unless the technical term is necessary.', preferred: 'e.g. use, help, start', avoided: 'e.g. utilize, facilitate, commence', coaching: 'Which words would you choose or replace?', note: 'e.g. “Use ‘help’ instead of ‘facilitate.’ That adjective is too formal.”'},
  rhythm: {label: 'Rhythm', plain: 'Pace and cadence', description: 'Sentence length, pauses, repetition, and how the writing sounds aloud.', example: 'A short statement. Then a longer thought. Or a smoother, more even flow?', guidance: 'e.g. Mix short and longer sentences. Give an important point its own beat, without making every line abrupt.', preferred: 'e.g. It needs work. The idea is good, but the timing is off.', avoided: 'e.g. It needs work. Good idea. Wrong time. Fix it.', coaching: 'Where should the writing slow down or land?', note: 'e.g. “Too many short sentences in a row. Let the second thought breathe.”'},
};
const practiceFocuses = {mixed: {label: 'Mixed', plain: 'Whole voice', description: 'Compare expressions, word choice, sentence structure, and cadence together.', example: 'Notice both what it says and how you would say it.', coaching: 'What sounds like you, and what would you change?', note: 'e.g. “A uses my words, but I would start with the answer and shorten the second sentence.”'}, ...languageDimensions};
function activeLanguageLessons() { return (state.language_lessons || []).filter(lesson => lesson.enabled); }
function openWholeVoiceCheck() {
  ui.wholeVoiceOpen = true;
  navigate('evaluate');
  const target = document.querySelector('#whole-voice-comparison');
  target?.scrollIntoView({block: 'start', behavior: 'instant'});
  target?.querySelector('summary')?.focus({preventScroll: true});
}
function confidencePanel() {
  const confidence = confidenceView(state.voice_confidence);
  const panel = el('section', {class: 'panel panel-pad confidence-panel', 'aria-label': 'Voice confidence'}, el('h2', {class: 'section-title'}, 'Voice confidence'));
  if (!confidence) {
    panel.append(el('p', {class: 'confidence-status'}, 'Unavailable'), helper('Confidence data is unavailable. Reload to check again.'));
    return panel;
  }
  const {evidence, validation} = confidence;
  const number = value => value === null ? 'Unavailable' : String(value);
  const progressText = `${confidence.completed} of ${confidence.total} evidence checkpoints`;
  const noteId = uid('confidence-note');
  const checkpoints = el('ol', {class: 'confidence-checkpoints'}, confidence.milestones.map((milestone, index) => el('li', {class: milestone.complete ? 'is-complete' : ''},
    el('span', {class: 'confidence-checkpoint-index', 'aria-hidden': 'true'}, milestone.complete ? icon('check', 12) : index + 1),
    el('div', {}, el('strong', {}, milestone.label), el('span', {class: 'confidence-checkpoint-state'}, milestone.complete ? 'Complete' : 'Not yet complete'), helper(milestone.detail)))));
  const outcomes = el('dl', {class: 'confidence-counts'}, [['Calibrated wins', validation.calibrated_wins], ['Baseline wins', validation.baseline_wins], ['Ties', validation.ties], ['Neither', validation.neither], ['Factual flags', validation.factual_flags]].map(([label, value]) => el('div', {}, el('dt', {}, label), el('dd', {}, number(value)))));
  panel.append(...[
    el('p', {class: 'confidence-status'}, confidence.label),
    el('div', {class: 'confidence-bar', role: 'progressbar', 'aria-label': 'Voice confidence evidence readiness', 'aria-valuemin': 0, 'aria-valuemax': confidence.total, 'aria-valuenow': confidence.completed, 'aria-valuetext': progressText, 'aria-describedby': noteId}, Array.from({length: confidence.total}, (_, index) => el('span', {class: index < confidence.completed ? 'is-complete' : '', 'aria-hidden': 'true'}))),
    el('p', {class: 'confidence-progress'}, progressText),
    el('p', {class: 'helper', id: noteId}, 'Evidence readiness, not a measured writing-accuracy score.'),
    el('div', {class: 'confidence-evidence'}, el('p', {}, el('strong', {}, number(evidence.authored_samples)), ' confirmed authored samples'), helper('Held-out samples excluded.'), el('p', {}, `${number(evidence.feedback)} saved feedback · ${number(evidence.lessons)} active ${evidence.lessons === 1 ? 'lesson' : 'lessons'}`), helper('Feedback and lessons are preferences, not authored writing.')),
    el('p', {class: 'confidence-validation'}, el('strong', {}, number(validation.rated_real)), ' rated real voice comparisons'),
    validation.rated_real > 0 ? outcomes : null,
    confidence.next_step ? el('p', {class: 'helper confidence-next'}, confidence.next_step) : null,
    button('Check fit', openWholeVoiceCheck, 'wide small', 'arrow'),
    details('Checkpoints & observed results', el('div', {class: 'confidence-detail'}, checkpoints, el('h3', {}, 'Overall voice comparisons'), helper(`${number(validation.distinct_briefs)} distinct context + brief cases · ${validation.channels.length} known channels`), validation.rated_real > 0 ? null : outcomes, helper('These are judgments on the drafts compared. Outcomes do not determine this readiness bar.'), el('h3', {}, 'Correction checks, separately'), helper(`${number(validation.correction_checks)} rated real correction checks. These test one saved correction, not the overall voice profile.`), helper('Offline rehearsals do not count toward real validation.'), evidence.channels.length > 0 ? el('p', {class: 'helper'}, `Training channels: ${evidence.channels.join(', ')}.`) : null, confidence.note ? helper(confidence.note) : null))
  ].filter(child => child !== null));
  return panel;
}
function journeyPanel() {
  const profile = state.profile || {};
  const lessons = activeLanguageLessons().length;
  const confirmed = state.samples.filter(sample => sample.status === 'confirmed' && !sample.held_out).length;
  const xp = Number(profile.xp) || 0;
  return el('section', { class: 'panel panel-pad journey-panel', 'aria-label': 'Practice progress' },
    el('div', { class: 'journey-title' }, el('div', { class: 'section-title', style: 'margin-bottom: 19px' }, 'Your practice'), badge(`Level ${profile.level || 1}`, 'muted')),
    el('div', { class: 'journey-number' }, xp, el('small', {}, 'contribution XP')),
    helper(profile.next_milestone || 'Make a choice or teach a language preference to begin.'),
    el('div', { class: 'metric-grid' }, el('div', {}, el('div', { class: 'metric-value' }, state.feedback.length), el('div', { class: 'metric-name' }, 'Considered choices')), el('div', {}, el('div', { class: 'metric-value' }, lessons), el('div', { class: 'metric-name' }, 'Active language lessons'))),
    el('p', { class: 'helper', style: 'margin-top: 17px' }, `${plural(confirmed, 'confirmed sample')} · optional evidence. XP counts contributions, not writing accuracy.`));
}
function stepsPanel() {
  const rows = [
    [state.feedback.length > 0, 'Choose what sounds like you', 'Compare expressions, words, sentence shape, or rhythm.'],
    [activeLanguageLessons().length > 0, 'Teach the difference directly', 'Say what to prefer, what to avoid, and when it applies.'],
    [false, 'Try it in a real draft', 'Keep the useful changes. Correct what still feels off.'],
  ];
  return el('section', { class: 'panel panel-pad' }, el('div', { class: 'section-title' }, 'Start without samples'), el('ol', { class: 'steps' }, rows.map(([done, title, text], index) => el('li', { class: `step ${done ? 'done' : ''}` }, el('span', { class: 'step-index' }, done ? icon('check', 12) : index + 1), el('div', {}, el('strong', {}, title), el('p', {}, text))))));
}
function sampleForm({ compact = false } = {}) {
  const title = input({ placeholder: 'e.g. A project update I sent last week', maxlength: 200 });
  const text = el('textarea', { class: 'sample-text', placeholder: 'Paste an email, message, post, or paragraph you wrote…', required: true, maxlength: 30000, 'aria-label': 'Your writing sample' });
  const context = contextEditor(ui.context);
  const submit = button('Add to review', null, 'primary', 'arrow'); submit.type = 'submit';
  const form = el('form', { class: compact ? '' : 'panel' },
    !compact && el('div', { class: 'panel-body', style: 'padding-bottom: 2px' }, el('div', { class: 'intro-heading' }, el('div', {}, el('div', { class: 'eyebrow' }, '01 / Your own words'), el('h2', {}, 'Start with something you wrote.'), el('p', {}, 'An email. A text to a friend. A post you cared about. Real writing gives your voice a place to start.')), el('div', { class: 'ink-symbol' }, icon('feather', 62)))),
    el('div', { class: compact ? '' : 'panel-body', style: compact ? '' : 'padding-top: 14px' }, field('Give it a name', title, 'optional'), field('Your writing', text), context.node),
    el('div', { class: compact ? 'button-group' : 'panel-foot' }, helper('You’ll confirm authorship before it is used.'), submit));
  form.addEventListener('submit', event => { event.preventDefault(); busy(submit, async () => {
    if (!text.value.trim()) throw new Error('Paste a writing sample first.');
    await api('/samples', 'POST', { title: title.value.trim() || 'Untitled sample', text: text.value.trim(), context: context.get(), source_type: 'manual' });
    ui.filter = 'pending'; await refresh(); toast('Sample added. Confirm whether you wrote it.');
  }, 'Adding…'); });
  return form;
}
function renderPractice() {
  const confirmed = state.samples.filter(sample => sample.status === 'confirmed' && !sample.held_out);
  const pending = pendingSampleCounts(state.samples);
  const feedbackIds = new Set(state.feedback.map(item => String(item.round_id)));
  const unanswered = state.rounds.filter(round => !feedbackIds.has(String(round.id)) && round.status !== 'completed');
  const currentRound = unanswered.at(-1);
  const column = el('div', { class: 'primary-column' }, currentRound ? roundPanel(currentRound) : roundSetup(), languageLessonPanel({includeForm: true}));
  if (pending.review) column.append(callout(`${plural(pending.review, 'sample')} waiting for review`, 'You can keep practicing now. Confirm authorship later if you want these samples to add evidence.', '', button('Review samples', () => navigate('samples'), 'small', 'arrow')));
  const hasTeaching = state.feedback.length > 0 || activeLanguageLessons().length > 0;
  const startNote = !confirmed.length && isLive()
    ? (hasTeaching ? `Your explicit lessons and choices can guide ${providerPhrase()} even without samples. Calibration is still developing; judge each result.` : `Uncalibrated, and ready to learn. ${providerPhrase()} can start with a comparison now; your choices, corrections, and direct lessons will provide direction.`)
    : (isLive() ? `Your lessons, choices, and optional confirmed writing provide context for ${providerPhrase()}.` : 'Offline mode lets you record lessons and rehearse comparisons with deterministic examples. A language model is not running.');
  const aside = el('aside', { class: 'aside-column' }, journeyPanel(), stepsPanel(), el('div', { class: 'aside-note' }, el('h3', {}, 'Samples are optional.'), el('p', {}, startNote), button('Add writing samples later', () => navigate('samples'), 'quiet small', 'plus')), el('div', { class: 'aside-note' }, el('h3', {}, 'Teach the language and the presentation.'), el('p', {}, 'Your everyday expressions, vocabulary, sentence structure, and rhythm can change with the audience. Paragraphs, bullets, openings, and sign-offs have their own preferences, too.')));
  return [pageHeading('Calibration / words, sentences, and context', 'Teach it how you actually write.', 'Start with a comparison or a direct lesson. No writing samples required; every choice gives it clearer direction.'), el('div', { class: 'layout confidence-layout' }, confidencePanel(), column, aside)];
}
function roundSetup() {
  const context = contextEditor(ui.context, { scenario: true });
  const focusButtons = [];
  const focusHelp = el('div', {class: 'focus-help', 'aria-live': 'polite'});
  const updateHelp = () => { const item = practiceFocuses[ui.practiceFocus]; focusHelp.replaceChildren(el('strong', {}, item.plain), el('p', {}, item.description), el('p', {class: 'focus-example'}, item.example)); };
  const picker = el('div', {class: 'focus-picker', role: 'group', 'aria-label': 'Practice focus'}, Object.entries(practiceFocuses).map(([key, item]) => {
    const control = el('button', {type: 'button', class: `focus-choice ${ui.practiceFocus === key ? 'active' : ''}`, 'aria-pressed': String(ui.practiceFocus === key), onClick: () => { ui.practiceFocus = key; focusButtons.forEach(([value, target]) => { target.classList.toggle('active', value === key); target.setAttribute('aria-pressed', String(value === key)); }); updateHelp(); }}, item.label);
    focusButtons.push([key, control]); return control;
  }));
  updateHelp();
  const submit = button(isLive() ? 'Compare two versions' : 'Rehearse a comparison', null, 'primary', 'arrow'); submit.type = 'submit';
  const form = el('form', { class: 'panel' }, panelHeading('What should we tune?', 'Pick a focus, then give it a situation.', badge('Samples optional')), el('div', { class: 'panel-body' }, el('div', {class: 'field'}, el('span', {class: 'field-label'}, 'Practice focus'), picker, focusHelp), context.node), el('div', { class: 'panel-foot' }, helper(isLive() ? (activeLanguageLessons().length || state.feedback.length ? 'Uses your contextual lessons and choices. Samples are optional.' : 'Start uncalibrated. Teach it through choices and corrections.') : 'Offline comparisons rehearse the workflow with simple rules.'), submit));
  form.addEventListener('submit', event => { event.preventDefault(); busy(submit, async () => { await api('/rounds', 'POST', { context: context.get(), focus: ui.practiceFocus }); await refresh(); }, 'Creating comparison…'); });
  return form;
}
function languageLessonForm(lesson = null) {
  const draft = lesson || ui.lessonDraft;
  const context = contextEditor(lesson?.context || ui.context, {remember: false});
  const dimension = select(Object.entries(languageDimensions).map(([key, item]) => [key, `${item.label} — ${item.plain}`]), draft.dimension || 'vernacular');
  const guidance = el('textarea', {rows: 3, maxlength: 2000, value: draft.guidance || '', onInput: event => { if (!lesson) ui.lessonDraft.guidance = event.target.value; }});
  const preferred = el('textarea', {rows: 2, maxlength: 500, class: 'phrase-input', value: draft.preferred || '', onInput: event => { if (!lesson) ui.lessonDraft.preferred = event.target.value; }});
  const avoided = el('textarea', {rows: 2, maxlength: 500, class: 'phrase-input', value: draft.avoided || '', onInput: event => { if (!lesson) ui.lessonDraft.avoided = event.target.value; }});
  const explanation = helper('');
  const updatePrompts = () => { const item = languageDimensions[dimension.value]; explanation.textContent = item.description; guidance.placeholder = item.guidance; preferred.placeholder = item.preferred; avoided.placeholder = item.avoided; if (!lesson) ui.lessonDraft.dimension = dimension.value; };
  dimension.addEventListener('change', updatePrompts); updatePrompts();
  const submit = button(lesson ? 'Save lesson changes' : 'Save this language lesson', null, lesson ? 'small primary' : 'primary', 'check'); submit.type = 'submit';
  const form = el('form', {class: 'language-lesson-form'}, field('What are you teaching?', dimension), explanation, el('div', {style: 'margin-top: 18px'}, field('How should it sound?', guidance, 'or use a phrase below'), el('div', {class: 'form-row'}, field('Say it this way', preferred, 'optional'), field('Avoid this', avoided, 'optional'))), details('Where does this lesson apply?', context.node, true), el('div', {class: 'button-group', style: 'margin-top: 18px'}, submit));
  form.addEventListener('submit', event => { event.preventDefault(); busy(submit, async () => {
    const content = {dimension: dimension.value, guidance: guidance.value.trim(), preferred: preferred.value.trim(), avoided: avoided.value.trim(), context: context.get()};
    if (!content.guidance && !content.preferred && !content.avoided) throw new Error('Add guidance, a preferred phrase, or something to avoid.');
    await api(lesson ? `/language-lessons/${encodeURIComponent(lesson.id)}` : '/language-lessons', lesson ? 'PATCH' : 'POST', content);
    if (!lesson) ui.lessonDraft = {dimension: dimension.value, guidance: '', preferred: '', avoided: ''};
    await refresh(); toast(lesson ? 'Language lesson updated.' : 'Lesson saved. No writing sample was needed.');
  }); });
  return form;
}
function languageLessonRow(lesson) {
  const enabled = Boolean(lesson.enabled);
  const dimension = languageDimensions[lesson.dimension] || {label: lesson.dimension, plain: 'Language preference'};
  const row = el('article', {class: `language-lesson ${enabled ? '' : 'is-disabled'}`}, el('div', {class: 'review-top'}, el('div', {}, el('h3', {}, dimension.label, el('span', {class: 'lesson-dimension'}, ` / ${dimension.plain}`)), contextPills(lesson.context)), badge(enabled ? 'Active lesson' : 'Not in use', enabled ? '' : 'muted')),
    lesson.guidance && el('p', {class: 'lesson-guidance'}, lesson.guidance),
    (lesson.preferred || lesson.avoided) && el('dl', {class: 'lesson-phrases'}, lesson.preferred && el('div', {}, el('dt', {}, 'Prefer'), el('dd', {}, lesson.preferred)), lesson.avoided && el('div', {}, el('dt', {}, 'Avoid'), el('dd', {}, lesson.avoided))));
  const toggle = button(enabled ? 'Stop using this lesson' : 'Use this lesson again', () => busy(toggle, async () => { await api(`/language-lessons/${encodeURIComponent(lesson.id)}`, 'PATCH', {enabled: !enabled}); await refresh(); toast(enabled ? 'Lesson disabled. It will not guide future generation.' : 'Lesson enabled for its context.'); }), 'small');
  const remove = button('Delete', () => {
    const confirmation = el('div', {class: 'lesson-delete'}, helper('Delete this lesson permanently? Disabling keeps it available for later.'));
    const confirm = button('Delete lesson', () => busy(confirm, async () => { await api(`/language-lessons/${encodeURIComponent(lesson.id)}`, 'DELETE', {}); await refresh(); toast('Language lesson deleted.'); }), 'small danger');
    confirmation.append(el('div', {class: 'button-group'}, confirm, button('Keep it', () => { confirmation.remove(); remove.disabled = false; }, 'small'))); row.append(confirmation); remove.disabled = true;
  }, 'small quiet');
  row.append(el('div', {class: 'button-group', style: 'margin-top: 13px'}, toggle, remove), details('Edit language and context', languageLessonForm(lesson)));
  return row;
}
function languageLessonPanel({includeForm = false} = {}) {
  const lessons = state.language_lessons || [];
  const panel = el('section', {class: 'panel', id: 'language-lessons'}, panelHeading(includeForm ? 'Teach your language directly' : 'Your language lessons', 'State a preference or give a phrase to use and one to avoid. Each lesson belongs to a context.', badge(`${activeLanguageLessons().length} active`, 'muted')));
  const body = el('div', {class: 'panel-body'});
  if (includeForm) body.append(languageLessonForm(), el('p', {class: 'helper', style: 'margin-top: 14px'}, 'This records your explicit preference. It does not claim you authored an example or change model weights.'));
  if (lessons.length) body.append(el('div', {class: 'language-lesson-list'}, includeForm && el('div', {class: 'section-title'}, 'Saved language lessons'), lessons.map(languageLessonRow)));
  else if (!includeForm) body.append(helper('No direct language lessons yet. You can teach your expressions, sentence structure, word choice, and rhythm without importing any writing.'), button('Teach a language preference', () => { navigate('practice'); document.querySelector('#language-lessons')?.scrollIntoView({behavior: 'smooth', block: 'start'}); }, 'small', 'plus'));
  panel.append(body); return panel;
}
const presentationOptions = {
  paragraph_length: ['Paragraph length', [['', 'No preference'], ['short', 'Short paragraphs'], ['medium', 'Medium paragraphs'], ['long', 'Longer paragraphs']]],
  structure: ['Structure', [['', 'No preference'], ['prose', 'Flowing prose'], ['bullets', 'Bullet points'], ['mixed', 'Prose + bullets']]],
  opening: ['Opening', [['', 'No preference'], ['direct', 'Get to the point'], ['warm', 'A warm opener'], ['context first', 'Context first'], ['no greeting', 'No greeting']]],
  signoff: ['Sign-off', [['', 'No preference'], ['none', 'No sign-off'], ['brief', 'Brief sign-off'], ['warm', 'Warm sign-off'], ['formal', 'Formal sign-off']]],
  emoji: ['Emoji', [['', 'No preference'], ['none', 'None'], ['occasional', 'Occasionally'], ['expressive', 'Expressive']]],
  detail: ['Detail', [['', 'No preference'], ['essentials only', 'Essentials only'], ['some context', 'Some context'], ['thorough', 'Explain thoroughly']]],
};
function roundPanel(round) {
  const focus = practiceFocuses[round.focus] || practiceFocuses.mixed;
  const initial = ui.selections.get(String(round.id)) || { choice: '', edited_text: '', notes: '', presentation: {} };
  ui.selections.set(String(round.id), initial);
  const buttons = [];
  const submit = button('Save my feedback', null, 'primary', 'check'); submit.type = 'submit'; submit.disabled = !initial.choice;
  const setChoice = choice => { initial.choice = choice; for (const [key, control] of buttons) { control.classList.toggle('selected', key === choice); control.setAttribute('aria-pressed', key === choice ? 'true' : 'false'); } submit.disabled = !choice; };
  const options = el('div', { class: 'round-options', role: 'group', 'aria-label': 'Choose the wording closer to your voice' }, round.options.map(option => {
    const control = el('button', { type: 'button', class: `option ${initial.choice === option.id ? 'selected' : ''}`, 'aria-pressed': initial.choice === option.id ? 'true' : 'false', onClick: () => setChoice(option.id) }, el('span', { class: 'option-top' }, el('span', { class: 'option-letter' }, option.id.toUpperCase()), el('span', { class: 'option-prompt' }, 'This feels closer'), el('span', { class: 'option-radio', 'aria-hidden': 'true' })), el('span', { class: 'option-text' }, option.text));
    buttons.push([option.id, control]); return control;
  }));
  const neither = button('Neither feels right', () => setChoice('neither'), initial.choice === 'neither' ? 'selected' : ''); neither.setAttribute('aria-pressed', initial.choice === 'neither' ? 'true' : 'false'); buttons.push(['neither', neither]);
  const rewrite = el('textarea', { rows: 3, placeholder: `Rewrite a phrase or sentence your way. ${focus.example}`, value: initial.edited_text, maxlength: 10000, onInput: event => { initial.edited_text = event.target.value; } });
  const notes = el('textarea', { rows: 2, style: 'min-height: 70px', placeholder: focus.note, value: initial.notes, maxlength: 4000, onInput: event => { initial.notes = event.target.value; } });
  const presentation = el('div', { class: 'presentation-fields' }, Object.entries(presentationOptions).map(([key, [label, choices]]) => field(label, select(choices, initial.presentation[key] || '', { onChange: event => { initial.presentation[key] = event.target.value; } }))));
  const form = el('form', { class: 'panel' }, panelHeading('Which sounds more like you?', `${focus.label}: ${focus.coaching}`, badge(isLive(round.provider) ? `${modelName(round.provider)} round` : 'Heuristic rehearsal', isLive(round.provider) ? '' : 'amber')),
    el('div', { class: 'round-context' }, contextPills(round.context), round.context?.scenario && el('p', { class: 'round-scenario' }, round.context.scenario)),
    el('div', { class: 'panel-body' }, options, el('div', { class: 'neither-row' }, neither, helper('A rejection is useful feedback, too.')),
      el('div', { class: 'feedback-fields' }, field('Teach the language difference', notes, 'optional'), field('Your words, your sentence', rewrite, 'optional correction'), details('Presentation: paragraphs, structure & more', presentation, false))),
    el('div', { class: 'panel-foot' }, helper('Your choices and corrections teach preferences in this context.'), submit));
  if (round.warnings?.length) form.insertBefore(el('div', { class: 'panel-body', style: 'padding-bottom: 0' }, helper(round.warnings.join(' '))), form.children[2]);
  form.addEventListener('submit', event => { event.preventDefault(); busy(submit, async () => {
    if (!initial.choice) throw new Error('Choose A, B, or neither before saving.');
    await api('/feedback', 'POST', { round_id: round.id, choice: initial.choice, edited_text: initial.edited_text.trim(), notes: initial.notes.trim(), presentation: Object.fromEntries(Object.entries(initial.presentation).filter(([, value]) => value)) });
    ui.selections.delete(String(round.id)); await refresh(); toast('Choice saved. Your context-specific preferences are growing.');
  }); });
  return form;
}
function sampleCard(sample, { nested = false } = {}) {
  const title = sample.title || 'Untitled sample';
  const source = sample.source_path || sample.source_url || (sample.source_type === 'manual' ? 'Pasted by you' : sample.source_type || 'Imported sample');
  const labels = { pending: ['Needs review', 'amber'], confirmed: ['Confirmed', ''], assisted: ['AI-assisted', 'muted'], rejected: ['Not yours', 'red'] };
  const [statusLabel, tone] = labels[sample.status] || [sample.status, 'muted'];
  const card = el('article', { class: `review-card ${nested ? '' : 'panel'}` },
    el('div', { class: 'review-top' }, el('div', {}, el('h3', { class: 'review-title' }, title), el('div', { class: 'review-source' }, icon(sample.source_url ? 'globe' : 'samples'), source)), el('div', { class: 'button-group' }, badge(statusLabel, tone), sample.held_out && badge('Held out', 'muted'))),
    el('div', { class: 'review-body', tabindex: '0', 'aria-label': `Sample text: ${title}` }, sample.text), contextPills(sample.context));
  const actions = el('div', { class: 'review-actions' });
  const actionButtons = el('div', { class: 'button-group' });
  const changeStatus = (status, label, style) => {
    const control = button(label, () => busy(control, async () => {
      await api(`/samples/${encodeURIComponent(sample.id)}`, 'PATCH', { status }); await refresh();
      toast(status === 'confirmed' ? 'Authorship confirmed. This sample can inform your voice.' : status === 'assisted' ? 'Marked AI-assisted. Excluded from positive style evidence.' : 'Marked as not yours. Excluded from style evidence.');
    }), `${style} small`, status === 'confirmed' ? 'check' : undefined);
    return control;
  };
  if (sample.status !== 'confirmed') actionButtons.append(changeStatus('confirmed', 'I wrote this', 'primary'));
  if (sample.status !== 'assisted') actionButtons.append(changeStatus('assisted', 'AI-assisted', ''));
  if (sample.status !== 'rejected') actionButtons.append(changeStatus('rejected', 'Not mine', ''));
  actions.append(actionButtons);
  card.append(actions);
  if (!sample.held_out) {
    const preview = el('div', {});
    const infer = button(isLive() ? `Suggest context with ${providerPhrase()}` : 'Suggest context locally', () => busy(infer, async () => {
      const {inference} = await api(`/samples/${encodeURIComponent(sample.id)}/infer`, 'POST', {});
      const edit = contextEditor(inference.context, {remember: false, expanded: true, scenario: true});
      const apply = button('Apply reviewed context', () => busy(apply, async () => {
        await api(`/samples/${encodeURIComponent(sample.id)}`, 'PATCH', {context: edit.get()}); await refresh(); toast('Context updated. Authorship status was preserved.');
      }), 'small primary');
      preview.replaceChildren(el('section', {class:'panel panel-pad', style:'margin-top:12px'}, el('h3', {}, 'A provisional reconstruction'),
        helper(isLive(inference.provider) ? 'Model suggestions. Check them against what actually happened.' : 'Local clues only; no AI model was used.'),
        el('ul', {}, (inference.rationale || []).map(x => el('li', {}, x))),
        details('Uncertainties and authorship clues', el('div', {}, [...(inference.uncertainties || []), ...(inference.authorship_signals || [])].map(x => helper(x)))), edit.node, apply));
    }, 'Analyzing…'), 'small', 'spark');
    card.append(el('div', {style:'margin-top:12px'}, infer, helper(isLive() ? `This action sends this sample to ${providerPhrase()} for analysis. It does not confirm authorship.` : 'Infer a starting context from the passage and source, then correct it.'), preview));
  }
  const context = contextEditor(sample.context, { remember: false });
  const titleControl = input({ value: title, maxlength: 200 });
  const heldOut = input({ type: 'checkbox', checked: sample.held_out });
  const save = button('Save sample details', () => busy(save, async () => {
    await api(`/samples/${encodeURIComponent(sample.id)}`, 'PATCH', { title: titleControl.value.trim() || 'Untitled sample', context: context.get(), held_out: heldOut.checked }); await refresh(); toast('Sample details saved.');
  }), 'small');
  const remove = button('Delete sample', () => {
    const confirmation = el('div', { class: 'callout warning', style: 'margin-top: 12px' }, el('div', {}, el('strong', {}, 'Delete this sample?'), el('p', {}, 'It will be removed from the workspace, future retrieval, and exports.')));
    const confirm = button('Delete permanently', () => busy(confirm, async () => { await api(`/samples/${encodeURIComponent(sample.id)}`, 'DELETE'); await refresh(); toast('Sample deleted.'); }), 'small danger');
    confirmation.append(el('div', { class: 'button-group' }, confirm, button('Keep sample', () => confirmation.remove(), 'small')));
    remove.after(confirmation); remove.disabled = true;
    confirmation.addEventListener('click', () => { if (!confirmation.isConnected) remove.disabled = false; });
  }, 'small quiet');
  card.append(details('Edit context, source details & holdout', el('div', { class: 'review-context' }, field('Sample name', titleControl), context.node,
    el('p', { class: 'helper', style: 'margin: 16px 0' }, `Source: ${source}. ${sample.source_type ? `Type: ${sample.source_type}. ` : ''}Context labels are editable; imported guesses are provisional.`),
    el('label', { class: 'checkbox', style: 'margin-bottom: 16px' }, heldOut, el('span', {}, 'Hold this sample out. Keep it for future comparison, excluded from training prompts, profile patterns, and example exports.')),
    el('div', { class: 'button-group' }, save, remove))));
  return card;
}
function renderSamples() {
  const pending = pendingSampleCounts(state.samples);
  const reviewSummary = el('p', {class: 'helper sample-review-summary'}, `${pending.review} pending review · ${pending.held_out} pending held out. Held-out writing stays excluded from training, even after authorship is confirmed.`);
  const filters = [['pending', 'To review'], ['confirmed', 'Confirmed'], ['assisted', 'Assisted'], ['rejected', 'Not mine'], ['all', 'All']];
  const filtered = state.samples.filter(sample => ui.filter === 'all' || sample.status === ui.filter);
  const filterBar = el('div', { class: 'filters', role: 'group', 'aria-label': 'Filter samples' }, filters.map(([key, label]) => el('button', { class: `filter ${ui.filter === key ? 'active' : ''}`, 'aria-pressed': key === ui.filter ? 'true' : 'false', onClick: () => { ui.filter = key; render(); } }, label, el('span', { class: 'filter-count' }, key === 'all' ? state.samples.length : state.samples.filter(sample => sample.status === key).length))));
  const list = el('div', { class: 'review-list' }, filtered.length ? filtered.slice().reverse().map(sample => sampleCard(sample)) : empty(ui.filter === 'pending' ? 'Your inbox is clear.' : 'No samples in this view yet.', ui.filter === 'pending' ? 'Paste your own writing, scan a folder you choose, or import a browser capture. Nothing becomes evidence until you confirm it.' : 'Confirmed authorship and AI-assisted writing are kept separate so your profile has a clear foundation.'));
  const paste = el('section', { class: 'panel panel-pad', id: 'paste-sample' }, el('h2', {}, 'Add a writing sample'), helper('Paste a message, email, or passage. Each sample starts in your review inbox.'), el('div', { style: 'margin-top: 18px' }, sampleForm({ compact: true })));
  const sidebar = el('aside', { class: 'aside-column' }, discoveryPanel(), importPanel(), el('div', { class: 'aside-note' }, el('h3', {}, 'Good evidence has a clear source.'), el('p', {}, 'Saved paths and page URLs preserve provenance. “I wrote this” confirms authorship; it is never inferred from a file’s presence. Mixed or edited AI output belongs in Assisted.')));
  const add = button('Paste a sample', () => { paste.scrollIntoView({ behavior: 'smooth', block: 'start' }); paste.querySelector('textarea')?.focus({ preventScroll: true }); }, '', 'plus');
  return [pageHeading('Evidence / your words, with context', 'Your writing library.', 'A considered collection of things you actually wrote. Review first, learn second.', add), el('div', { class: 'layout confidence-layout' }, confidencePanel(), el('div', { class: 'primary-column' }, el('div', {}, reviewSummary, filterBar, list), paste), sidebar)];
}
async function runScan(id) {
  if (ui.scanRunning) return;
  ui.scanRunning = id; render();
  try {
    while (ui.scanRunning === id) {
      const {job} = await api(`/discovery-jobs/${encodeURIComponent(id)}/step`, 'POST', {max_files:100});
      await refresh(false); render();
      if (job.status === 'completed' || job.status === 'cancelled') { ui.scanRunning = null; render(); toast(`Discovery ${job.status}. ${job.added} candidates added for review.`); break; }
    }
  } catch (error) { ui.scanRunning = null; render(); toast(error.message, true); }
}
function discoveryPanel() {
  const roots = el('textarea', {rows:3, style:'min-height:90px;font-size:11px', placeholder:'/Users/you/Documents\n/Users/you/Downloads', 'aria-label':'Absolute folder paths', required:true});
  const submit = button('Start discovery', null, 'wide', 'folder'); submit.type = 'submit';
  const panel = el('form', {class:'panel panel-pad'}, el('div', {class:'section-title'}, 'From your computer'), el('h3', {}, 'Find writing across folders'),
    helper('Choose folders or your home directory. The scanner works in resumable batches until the selected tree is covered. Nothing is confirmed automatically.'), field('Folder paths', roots), submit,
    el('p', {class:'helper', style:'margin-top:12px'}, 'Readable TXT, Markdown, email, DOCX, PDF when available, and supported JSON exports. Hidden, sensitive, generated and linked paths are excluded. Closing this page pauses after the current batch.'));
  panel.addEventListener('submit', event => { event.preventDefault(); busy(submit, async () => {
    if (ui.scanRunning) throw new Error('Pause the active scan before starting another.');
    const paths = roots.value.split('\n').map(x => x.trim()).filter(Boolean);
    const {job} = await api('/discovery-jobs', 'POST', {roots:paths});
    ui.filter='pending'; await refresh(false); await runScan(job.id);
  }, 'Scanning…'); });
  for (const job of (state.discovery_jobs || []).slice(0,4)) {
    const active = ui.scanRunning === job.id;
    const controls = el('div', {class:'button-group'});
    if (active) controls.append(button('Pause after this batch', () => {ui.scanRunning=null; render();}, 'small'));
    else if (job.status === 'paused') controls.append(button('Resume discovery', () => runScan(job.id), 'small'));
    panel.append(el('div', {class:'scan-result'}, el('strong', {}, active ? 'Scanning selected folders' : job.status),
      helper(`${job.scanned} files read · ${job.added} added · ${job.duplicates} duplicates · ${job.skipped} excluded · ${job.remaining} queued`),
      details('Scope and extraction issues', el('div', {}, helper(job.coverage), job.roots.map(x=>helper(x)), (job.warnings || []).map(x=>helper(x)))), controls));
  }
  return panel;
}

function importPanel() {
  const file = input({ type: 'file', accept: '.json,application/json', class: 'file-input', 'aria-label': 'Browser capture or writing export JSON file' });
  const submit = button('Import for review', null, 'wide', 'upload'); submit.type = 'submit';
  const form = el('form', { class: 'panel panel-pad' }, el('div', { class: 'section-title' }, 'From your browser'), el('h3', {}, 'Import a capture'), el('p', { class: 'helper', style: 'margin-bottom: 15px' }, 'Use the bundled browser extension to save writing you select, then import its JSON here.'), field('Capture file', file), submit, el('p', { class: 'helper', style: 'margin-top: 12px' }, 'The extension supports selected text and a reviewed picker for Google Docs shown in a Drive tab. It reports per-document import failures. ChatGPT conversation exports can be added through the folder scanner; only user turns are candidates.'));
  form.addEventListener('submit', event => { event.preventDefault(); busy(submit, async () => {
    if (!file.files[0]) throw new Error('Choose a JSON capture file first.');
    if (file.files[0].size > 10 * 1024 * 1024) throw new Error('This file is larger than 10 MB. Use a smaller capture.');
    let payload; try { payload = JSON.parse(await file.files[0].text()); } catch { throw new Error('This file is not valid JSON. Choose an exported browser capture.'); }
    const items = Array.isArray(payload) ? payload : payload.items;
    if (!Array.isArray(items)) throw new Error('Expected a capture with an “items” array. For ChatGPT exports, use the folder scanner.');
    if (!items.length) throw new Error('This capture does not contain any writing samples.');
    const result = await api('/import', 'POST', { items }); ui.filter = 'pending'; await refresh(); toast(`${plural(result.added || 0, 'sample')} imported · ${result.duplicates || 0} duplicates skipped.`);
  }, 'Importing…'); });
  return form;
}
function currentDraftOriginal() { return ui.draft?.original_text ?? ui.draft?.text ?? ''; }
function currentTeachingBaseline() { return ui.draft?.teaching_baseline ?? currentDraftOriginal(); }
function draftHasTeachingChanges() { return Boolean(ui.draft && ui.editedDraft.trim() !== currentTeachingBaseline().trim()); }
function draftHasEdits() { return Boolean(ui.draft && ui.editedDraft !== currentDraftOriginal()); }
function openCorrectionPreview() {
  const original = currentTeachingBaseline();
  if (!ui.draft || original.trim() === ui.editedDraft.trim()) { toast('Make a wording, sentence, or presentation change first. Outer whitespace alone is not a lesson.', true); return; }
  const span = changedSpan(original, ui.editedDraft);
  ui.rememberDraft = {original_text: original, revised_text: ui.editedDraft, guidance: '', preferred: span.phraseFits ? span.after.trim() : '', avoided: span.phraseFits ? span.before.trim() : '', context: {...(ui.draft.context || {})}, scope: 'similar', kind: '', dimension: 'diction'};
  persistEditor(); render(); document.querySelector('#remember-correction')?.scrollIntoView({behavior: 'smooth', block: 'start'});
}
function launchCorrectionCheck(correction) {
  ui.qualityForm = {correctionId: correction.id, brief: '', context: {...correction.context}}; persistEditor(); navigate('evaluate');
}
async function generateCurrentDraft() {
  const context = {...ui.context}; const brief = ui.brief.trim(); const controls = {...ui.controls};
  if (!brief) throw new Error('Add what you want to say, or a rough draft, first.');
  ui.generating = true; ui.replacePrompt = false; render();
  try {
    const result = await api('/draft', 'POST', {context, brief, controls});
    ui.draft = {...result, original_text: result.text, context, brief, local_id: crypto.randomUUID()};
    ui.editedDraft = result.text; ui.draftUndo = null; ui.rememberDraft = null; persistEditor();
    toast('Draft ready. Edit freely; nothing is learned unless you ask to remember it.');
  } finally { ui.generating = false; render(); }
}
function draftControls(values, onChange, disabled = false) {
  const controls = el('div', {class: 'control-grid'});
  for (const [key, title, left, right] of [['polish', 'Polish', 'Light touch', 'Substantial rewrite'], ['detail', 'Detail', 'Essentials', 'Full context'], ['warmth', 'Warmth', 'Matter-of-fact', 'More personal'], ['assertiveness', 'Assertiveness', 'Gentle', 'Direct']]) {
    const output = el('output', {class: 'control-value'}, values[key]);
    const range = input({type: 'range', min: 0, max: 100, step: 1, value: values[key], disabled, 'aria-label': title, onInput: event => {values[key] = Number(event.target.value); output.textContent = event.target.value; onChange({...values});}});
    range.id = uid('range'); output.setAttribute('for', range.id);
    controls.append(el('div', {class: 'control-row'}, el('label', {class: 'control-label', for: range.id}, title, output), range, el('div', {class: 'range-labels'}, el('span', {}, left), el('span', {}, right))));
  }
  const structure = select([['auto', 'Use my preferences'], ['prose', 'Prose'], ['bullets', 'Bullets']], values.structure, {disabled, onChange: event => {values.structure = event.target.value; onChange({...values});}});
  const register = select([['auto', 'Fit the context'], ['casual', 'Casual'], ['professional', 'Professional'], ['formal', 'Formal']], values.register || 'auto', {disabled, onChange: event => {values.register = event.target.value; onChange({...values});}});
  return el('div', {}, controls, el('div', {class: 'form-row'}, field('Structure', structure), field('Register', register)), helper('These controls affect this draft. They do not create remembered preferences or authorize new facts.'));
}
function renderWrite() {
  const mode = el('div', {class: 'segmented write-mode', role: 'group', 'aria-label': 'Writing mode'}, [['message', 'Message'], ['document', 'Whole document']].map(([value, label]) => el('button', {type: 'button', class: `segment ${ui.writeMode === value ? 'active' : ''}`, 'aria-pressed': String(ui.writeMode === value), onClick: () => {ui.writeMode = value; persistDocument(); render();}}, label)));
  const pieces = ui.writeMode === 'document' ? renderDocumentWrite() : renderMessageWrite();
  pieces.splice(1, 0, mode);
  return pieces;
}
function changeDocument(changes) {
  if (changes.context) {ui.documentContextPreview = null; document.querySelector('#document-context-preview')?.replaceChildren();}
  ui.document = reviseDocument(ui.document, changes); queueDocumentSave(); refreshDocumentDiagnostics();
}
function guardDocumentPaste(control, limit) {
  control.addEventListener('paste', event => {
    const inserted = event.clipboardData?.getData('text/plain');
    if (typeof inserted === 'string' && control.value.length - (control.selectionEnd - control.selectionStart) + inserted.length > limit) {
      event.preventDefault(); toast(`This paste exceeds ${limit.toLocaleString()} characters. Nothing was pasted or truncated.`, true);
    }
  });
}
function downloadDocumentText(text, filename) {
  const url = URL.createObjectURL(new Blob([text], {type: 'text/plain;charset=utf-8'}));
  const link = el('a', {href: url, download: filename}); document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function scheduleDocumentPoll(delay = 1500) {
  clearTimeout(documentPollTimer);
  if (activeDocumentJob(ui.document.job)) documentPollTimer = setTimeout(pollDocumentJob, delay);
}
async function pollDocumentJob() {
  if (documentPolling || !activeDocumentJob(ui.document.job)) return;
  documentPolling = true;
  const id = ui.document.job.id;
  try {
    const response = await api(`/documents/${encodeURIComponent(id)}`);
    if (ui.document.job?.id !== id) return;
    const kind = ui.document.job.kind;
    ui.document = acceptDocumentJob(ui.document, response); persistDocument();
    if (response.status === 'complete') {
      if (kind === 'generate' && ui.route === 'write' && ui.writeMode === 'document') render();
      else refreshDocumentDiagnostics(true);
      toast(ui.document.error || (kind === 'generate' ? 'Document ready. Review the source, wording and checks before using it.' : documentChecksCurrent(ui.document) ? 'Checks returned. Review potential issues before using your draft.' : 'Checks returned for an earlier edit. Run checks again for your current text.'), Boolean(ui.document.error));
    } else if (response.status === 'failed') {
      if (ui.route === 'write' && ui.writeMode === 'document') render();
      toast(ui.document.error, true);
    } else refreshDocumentDiagnostics();
  } catch (error) {
    if (documentPageClosing || ui.document.job?.id !== id) return;
    if (error.networkError) {
      ui.document.error = 'Connection interrupted. Retrying this request in a few seconds; your source and draft are preserved.';
      persistDocument(); refreshDocumentDiagnostics();
    } else {
      ui.document.error = `${error.message} If the server restarted, start a new request. Your source and draft are preserved.`;
      ui.document.job.status = 'failed'; persistDocument();
      if (ui.route === 'write' && ui.writeMode === 'document') render();
    }
  } finally { documentPolling = false; scheduleDocumentPoll(ui.document.error ? 5000 : 1500); }
}
async function startDocumentJob(kind) {
  if (ui.documentStarting || activeDocumentJob(ui.document.job)) return;
  const doc = ui.document;
  if (!doc.source.trim()) throw new Error('Paste a document or choose a Markdown or text file first.');
  if (doc.source.length > DOCUMENT_SOURCE_LIMIT) throw new Error('The source exceeds 120,000 characters. Nothing was truncated.');
  if (kind === 'check' && !doc.text.trim()) throw new Error('Add or generate a draft before checking it.');
  if (doc.text.length > DOCUMENT_OUTPUT_LIMIT) throw new Error('The draft exceeds 240,000 characters. Nothing was truncated.');
  const snapshot = {kind, source: doc.source, text: kind === 'check' ? doc.text : '', context: {...doc.context}, controls: {...doc.controls}, revision: doc.revision};
  ui.documentStarting = true; ui.documentReplacePrompt = false; ui.document.error = ''; render();
  try {
    const body = {context: snapshot.context, source: snapshot.source, controls: snapshot.controls};
    if (kind === 'check') body.text = snapshot.text;
    const response = await api(kind === 'check' ? '/document-checks' : '/documents', 'POST', body);
    if (!validDocumentJob(response)) throw new Error('The server returned an invalid document job. Your source and current draft are preserved.');
    ui.document.job = {...snapshot, id: response.id, status: response.status};
    ui.document = acceptDocumentJob(ui.document, response);
    persistDocument(); scheduleDocumentPoll(0);
  } catch (error) { ui.document.error = error.message; persistDocument(); throw error; }
  finally { ui.documentStarting = false; render(); }
}
function trainedContextPicker(onSelect, disabled = false) {
  const trained = [...activeLanguageLessons(), ...state.samples.filter(sample => sample.status === 'confirmed' && !sample.held_out), ...(state.feedback || []), ...(state.priors || []).filter(prior => prior.confirmed)];
  const contexts = [...new Map(trained.map(lesson => {const context = cleanDocumentContext(lesson.context); return [JSON.stringify(context), context];})).values()];
  if (!contexts.length) return helper('No trained contexts yet. Set the audience and purpose below; samples and lessons are optional.');
  const picker = select([['', 'Choose a context you have taught…'], ...contexts.map((context, index) => [String(index), [context.channel, context.audience, context.intent].filter(Boolean).join(' · ')])], '', {disabled, onChange: event => {if (event.target.value !== '') onSelect({...contexts[Number(event.target.value)]});}});
  return field('Use a trained context', picker, 'optional');
}
function documentContextReport(result) {
  const report = result?.context_report;
  const body = el('div', {}, helper('Context matching explains which guidance was supplied. It does not measure voice fidelity.'));
  if (report && typeof report === 'object') {
    const context = report.requested_context || report.context || result.source_context;
    if (context) body.append(contextPills(context));
    if (report.counts) body.append(el('p', {class: 'document-guidance-counts'}, `${report.counts.authored_examples || 0} authored examples · ${report.counts.language_lessons || 0} language lessons · ${report.counts.feedback || 0} saved preferences · ${report.counts.priors || 0} confirmed priors`));
    for (const [key, label] of [['changes', 'Context changes'], ['matches', 'Recognized matches'], ['warnings', 'Scope warnings'], ['supplied', 'Supplied guidance']]) {
      if (report[key] && (!Array.isArray(report[key]) || report[key].length)) body.append(el('h3', {}, label), el('pre', {class: 'provenance-text'}, typeof report[key] === 'string' ? report[key] : JSON.stringify(report[key], null, 2)));
    }
    const remainder = Object.fromEntries(Object.entries(report).filter(([key]) => !['requested_context', 'context', 'changes', 'matches', 'warnings', 'supplied', 'counts'].includes(key)));
    if (Object.keys(remainder).length) body.append(el('pre', {class: 'provenance-text'}, JSON.stringify(remainder, null, 2)));
  } else body.append(helper('No context matching report was returned for this draft.'));
  const sources = (Array.isArray(result?.evidence_ids) ? result.evidence_ids : []).map(id => state.samples.find(sample => String(sample.id) === String(id))).filter(Boolean);
  body.append(sources.length ? el('ul', {class: 'evidence-list'}, sources.map(sample => el('li', {}, sample.title || 'Untitled authored sample'))) : helper('No confirmed authored sample references were returned.'));
  for (const warning of Array.isArray(result?.warnings) ? result.warnings : []) body.append(helper(warning));
  return details('Context, supplied guidance & limitations', body);
}
function documentReviewContents() {
  const doc = ui.document, checking = ui.documentStarting || activeDocumentJob(doc.job);
  const current = documentChecksCurrent(doc), checks = doc.result?.checks;
  const summary = !checks ? 'Not checked' : !current ? 'Checks need rerunning' : checks.status === 'not_checked' ? 'Not checked' : checks.status === 'checked' ? 'Checks returned · review before use' : 'Review required';
  const content = el('div', {}, el('div', {class: 'review-top'}, el('h3', {}, 'Content & voice review'), badge(summary, current && checks?.status === 'checked' ? 'muted' : 'amber')), helper('Checks flag potential content and voice issues. They can miss omissions or factual changes and do not guarantee fidelity. Human review is required before use.'));
  if (checks && !current) content.append(el('p', {class: 'document-stale'}, checking ? 'A request is in progress. Previous checks do not validate the current draft.' : 'The source, draft, context or controls changed after these checks. Run checks again for the current version.'));
  if (checks) {
    if (checks.limitation) content.append(helper(checks.limitation));
    for (const warning of Array.isArray(checks.warnings) ? checks.warnings : []) content.append(helper(warning));
    if (Number.isInteger(checks.review?.units_completed) && Number.isInteger(checks.review?.units_total)) content.append(helper(`Review coverage: ${checks.review.units_completed} of ${checks.review.units_total} review units completed${checks.review.status === 'not_checked' ? ' · semantic review not performed' : ''}.`));
    const columns = el('div', {class: 'document-checks-grid'});
    for (const [key, title] of [['content', 'Content preservation'], ['voice', 'Voice & presentation']]) {
      const check = checks[key];
      const issues = Array.isArray(check?.issues) ? check.issues : [];
      const column = el('section', {class: 'document-check-column'}, el('h4', {}, title), helper(!current ? 'Earlier version · not current' : check?.status === 'not_checked' || !check ? 'Not checked' : check.status === 'checked' ? 'Review returned findings' : 'Review required'));
      if (!issues.length) column.append(helper(check?.status === 'not_checked' || !check ? 'No substantive check was performed.' : 'No potential issues were reported. This does not establish correctness.'));
      else column.append(el('ul', {class: 'document-issues'}, issues.map(issue => el('li', {}, el('strong', {}, `${issue?.severity || 'review'}${issue?.section ? ` · ${issue.section}` : ''}`), el('p', {}, issue?.message || 'Review this passage.'), issue?.code && el('span', {class: 'helper'}, issue.code)))));
      columns.append(column);
    }
    content.append(columns);
  }
  const recheck = button(checking ? 'Request in progress…' : checks ? 'Run checks again' : 'Check current draft', () => busy(recheck, () => startDocumentJob('check'), 'Starting…'), 'small', 'shield');
  recheck.disabled = checking || !doc.text.trim() || !doc.source.trim();
  content.append(el('div', {class: 'document-review-actions'}, recheck, helper('Checking never saves a training lesson or quality vote.')));
  if (checks?.source_hash || checks?.draft_hash) content.append(details('Checked version fingerprints', el('div', {}, helper(`Source: ${checks.source_hash || 'unavailable'}`), helper(`Draft: ${checks.draft_hash || 'unavailable'}`))));
  return content;
}
function documentJobContents() {
  const doc = ui.document;
  if (ui.documentStarting) return helper('Starting a document request… Your source and current draft remain saved locally.');
  if (activeDocumentJob(doc.job)) return el('div', {class: 'document-job-notice'}, el('strong', {}, `${doc.job.kind === 'check' ? 'Checking your current draft' : 'Rewriting your document'}…`), helper(doc.error || 'You can use another tab in Voicework while this runs. Refreshing reconnects to this request while the server is running.'));
  if (doc.error) {
    const retry = button('Retry request status', () => { if (!ui.document.job) return; ui.document.job.status = 'pending'; ui.document.error = ''; persistDocument(); render(); scheduleDocumentPoll(0); }, 'small');
    return el('div', {class: 'inline-error'}, el('p', {}, doc.error), doc.job && retry);
  }
  return null;
}
function documentEvidenceContents() {
  const doc = ui.document;
  return doc.result ? [documentContextReport(doc.result), doc.checkedSource && details('Preserved source used for the last result', el('div', {}, helper('This snapshot remains unchanged when you edit the source field above.'), el('textarea', {class: 'document-preserved-source', readonly: true, rows: 8, value: doc.checkedSource, 'aria-label': 'Preserved source for last result'})))] : [];
}
function refreshDocumentDiagnostics(includeEvidence = false) {
  const working = ui.documentStarting || activeDocumentJob(ui.document.job);
  const submit = document.querySelector('#document-submit');
  if (submit) {submit.disabled = working; submit.textContent = working ? 'Request in progress…' : isLive() ? 'Rewrite whole document' : 'Rehearse document rewrite';}
  const file = document.querySelector('#document-file'); if (file) file.disabled = working;
  const evidence = document.querySelector('#document-evidence');
  if (evidence && includeEvidence) {evidence.replaceChildren(...documentEvidenceContents().filter(Boolean)); evidence.hidden = !ui.document.result;}
  document.querySelector('#document-review')?.replaceChildren(documentReviewContents());
  document.querySelector('#document-job-status')?.replaceChildren(documentJobContents() || '');
}
function renderDocumentWrite() {
  const doc = ui.document;
  const working = ui.documentStarting || activeDocumentJob(doc.job);
  const generating = ui.documentStarting || (activeDocumentJob(doc.job) && doc.job.kind === 'generate');
  const context = contextEditor(doc.context, {remember: false, onChange: value => changeDocument({context: value})});
  Object.values(context.controls).forEach(control => { control.disabled = generating; });
  const count = helper(`${doc.source.length.toLocaleString()} / 120,000 characters`);
  const source = el('textarea', {class: 'document-source', value: doc.source, rows: 13, maxlength: DOCUMENT_SOURCE_LIMIT, disabled: generating, 'aria-label': 'Original document source', placeholder: '# Document title\n\nPaste the complete Markdown or plain-text document here.', onInput: event => {changeDocument({source: event.target.value}); count.textContent = `${event.target.value.length.toLocaleString()} / 120,000 characters`;}});
  guardDocumentPaste(source, DOCUMENT_SOURCE_LIMIT);
  const file = input({id: 'document-file', type: 'file', accept: '.md,.txt,text/plain,text/markdown', class: 'file-input', disabled: working, 'aria-label': 'Read a local Markdown or text document'});
  file.addEventListener('change', async () => {
    const chosen = file.files?.[0]; if (!chosen) return;
    try {
      if (!/\.(md|txt)$/i.test(chosen.name)) throw new Error('Choose a .md or .txt file. Other formats are not supported.');
      if (chosen.size > DOCUMENT_SOURCE_LIMIT * 4) throw new Error('This file is too large. Use a document up to 120,000 characters. Nothing was imported.');
      const text = await chosen.text();
      if (text.length > DOCUMENT_SOURCE_LIMIT) throw new Error('This document exceeds 120,000 characters. Nothing was imported or truncated.');
      if (text.includes('\0')) throw new Error('This file is not plain text. Export a UTF-8 Markdown or text copy first.');
      changeDocument({source: text, sourceName: chosen.name}); persistDocument(); render(); toast('File read locally. Review the source and context before generating.');
    } catch (error) { toast(error.message, true); } finally { file.value = ''; }
  });
  const trained = trainedContextPicker(value => {changeDocument({context: value}); persistDocument(); render();}, generating);
  const preview = button('Preview matching guidance', () => busy(preview, async () => {
    const selectedContext = JSON.stringify(ui.document.context);
    const report = await api('/context/resolve', 'POST', {context: {...ui.document.context}});
    if (selectedContext !== JSON.stringify(ui.document.context)) {toast('Context changed while previewing. Preview the current context again.', true); return;}
    ui.documentContextPreview = report.context_report || report;
    document.querySelector('#document-context-preview')?.replaceChildren(documentContextReport({context_report: ui.documentContextPreview}));
  }, 'Matching…'), 'small'); preview.disabled = generating;
  const previewArea = el('div', {id: 'document-context-preview'}, ui.documentContextPreview && documentContextReport({context_report: ui.documentContextPreview}));
  const adjustments = draftControls({...doc.controls}, value => changeDocument({controls: value}), generating);
  const submit = button(working ? 'Request in progress…' : isLive() ? 'Rewrite whole document' : 'Rehearse document rewrite', null, 'primary', 'write'); submit.type = 'submit'; submit.id = 'document-submit'; submit.disabled = working;
  const sourceDownload = button('Download source', () => downloadDocumentText(ui.document.source, (ui.document.sourceName || 'document-source.md').replace(/[\/\\]/g, '_')), 'small quiet', 'download'); sourceDownload.disabled = !doc.source; source.addEventListener('input', () => {sourceDownload.disabled = !ui.document.source;});
  const form = el('form', {class: 'panel document-input'}, panelHeading('Your original document', 'Plain text or Markdown. The complete source stays separate from the draft.'), el('div', {class: 'panel-body'}, field('Read a local file', file), helper('Choosing a file reads it in this browser. Generation or checks send its text to the local server and, with a live provider, to your configured model provider.'), field('Or paste the document', source), el('div', {class: 'document-source-meta'}, count, sourceDownload), doc.sourceName && helper(`Local file: ${doc.sourceName}`), trained, context.node, el('div', {class: 'document-context-preview'}, preview, previewArea), details('Adjust this document', adjustments)), el('div', {class: 'panel-foot'}, helper('Preserve the substance. Review every important change.'), submit));
  form.addEventListener('submit', event => {event.preventDefault(); if (ui.documentStarting || activeDocumentJob(ui.document.job)) return; if (ui.document.text) {ui.documentReplacePrompt = true; render(); return;} busy(submit, () => startDocumentJob('generate'), 'Starting…');});
  if (ui.documentReplacePrompt) {
    const replace = button('Generate a replacement', () => busy(replace, () => startDocumentJob('generate'), 'Starting…'), 'small primary');
    form.append(el('div', {class: 'replace-notice'}, el('strong', {}, 'Replace the current document draft?'), helper('Download or copy any edits you want to keep. The current draft will be replaced only after the new generation succeeds; your source stays separate.'), el('div', {class: 'button-group'}, button('Keep current draft', () => {ui.documentReplacePrompt = false; render();}, 'small'), replace)));
  }
  const output = el('section', {class: 'panel document-output', 'aria-label': 'Editable document result'}, panelHeading('Your document draft', 'Edit, review, and export the current text.', doc.result && badge(isLive(doc.result.provider) ? modelName(doc.result.provider) : 'Offline rehearsal', isLive(doc.result.provider) ? '' : 'amber')));
  const textCount = helper(`${doc.text.length.toLocaleString()} / 240,000 characters · Edits stay in this browser.`);
  const draft = el('textarea', {class: 'draft-editor document-editor', value: doc.text, rows: 22, maxlength: DOCUMENT_OUTPUT_LIMIT, readonly: generating, 'aria-label': 'Edit your document draft', placeholder: 'Your whole-document draft will appear here. You can also paste an existing revision to check against the source.', onInput: event => {changeDocument({text: event.target.value}); textCount.textContent = `${event.target.value.length.toLocaleString()} / 240,000 characters · Edits stay in this browser.`;}});
  guardDocumentPaste(draft, DOCUMENT_OUTPUT_LIMIT);
  const copy = button('Copy current text', async () => {try {await navigator.clipboard.writeText(ui.document.text); toast('Current edited text copied. No training was saved.');} catch {toast('Clipboard access was denied. Select the draft and copy it directly.', true);}}, 'small', 'copy');
  const downloadMd = button('Download .md', () => downloadDocumentText(ui.document.text, documentDownloadName(ui.document.sourceName, 'md')), 'small', 'download');
  const downloadTxt = button('Download .txt', () => downloadDocumentText(ui.document.text, documentDownloadName(ui.document.sourceName, 'txt')), 'small');
  const updateExport = () => {for (const control of [copy, downloadMd, downloadTxt]) control.disabled = !ui.document.text;}; updateExport(); draft.addEventListener('input', updateExport);
  output.append(draft, el('div', {class: 'draft-edit-toolbar'}, textCount, el('div', {class: 'button-group'}, copy, downloadMd, downloadTxt), helper('Whole documents are not sent to “Remember this correction.” Teach a specific preference in Practice when useful.')),
    el('div', {class: 'document-review', id: 'document-review'}, documentReviewContents()));
  output.append(el('div', {class: 'evidence', id: 'document-evidence', hidden: !doc.result}, documentEvidenceContents()));
  const pieces = [pageHeading('Writing / whole documents', 'Keep the substance. Find your voice.', 'Rewrite a complete document in context, then inspect content and voice separately.')];
  if (!isLive()) pieces.push(callout('Offline rehearsal', 'This walkthrough uses simple text rules. Content and voice are not substantively checked, and the result does not demonstrate learned writing quality.', 'warning'));
  pieces.push(el('div', {id: 'document-storage-error', class: 'inline-error', hidden: !ui.documentStorageError}, 'Browser storage is unavailable or full. Download your source and edited draft before refreshing or closing this page.'), el('div', {id: 'document-job-status', role: 'status', 'aria-live': 'polite'}, documentJobContents()), el('div', {class: 'write-layout document-layout'}, form, output), el('p', {class: 'helper local-draft-note'}, 'Source, context, output and the current request ID stay in this browser. Nothing becomes an authored sample, lesson or quality vote automatically. Review before using the document.'));
  return pieces;
}
function renderMessageWrite() {
  const context = contextEditor(ui.context, { scenario: false });
  const brief = el('textarea', { class: 'write-brief', placeholder: 'What do you want to say? Paste the message you’re answering, describe the facts, or start with a rough draft.', required: true, maxlength: 15000, value: ui.brief, disabled: ui.generating, onInput: event => { ui.brief = event.target.value; persistEditor(); } });
  const adjustments = draftControls(ui.controls, value => { ui.controls = value; persistEditor(); });
  const submit = button(ui.generating ? 'Writing…' : isLive() ? 'Draft this message' : 'Preview a rehearsal draft', null, 'primary', 'write'); submit.type = 'submit'; submit.disabled = ui.generating;
  const form = el('form', { class: 'panel' }, panelHeading('What are you trying to say?', 'Start with a real message. Samples are optional.'), el('div', { class: 'panel-body' }, field('Your message or rough draft', brief), context.node, details('Adjust this draft', adjustments)), el('div', { class: 'panel-foot' }, helper('Review the facts and wording before sending.'), submit));
  form.addEventListener('submit', event => { event.preventDefault();
    if (draftHasEdits() || ui.rememberDraft) { ui.replacePrompt = true; render(); return; }
    busy(submit, generateCurrentDraft, 'Writing…');
  });
  if (ui.replacePrompt) {
    const replace = button('Replace with a new draft', () => busy(replace, generateCurrentDraft, 'Writing…'), 'small primary');
    form.append(el('div', {class: 'replace-notice'}, el('strong', {}, 'Replace your current edited draft?'), helper('Your edits and any unsaved memory preview will be replaced only after the new draft succeeds. Copy anything you want to keep first.'), el('div', {class: 'button-group'}, button('Keep editing this draft', () => { ui.replacePrompt = false; render(); }, 'small'), replace)));
  }
  const output = el('section', { class: 'panel', 'aria-label': 'Editable draft result' }, panelHeading('Your draft', ui.draft ? 'Edit the text. Decide separately what to remember.' : 'A useful first draft, then your finishing touch.', ui.draft && badge(isLive(ui.draft.provider) ? modelName(ui.draft.provider) : 'Offline heuristic', isLive(ui.draft.provider) ? '' : 'amber')));
  if (ui.draft) {
    const draftEditor = el('textarea', {class: 'draft-editor', value: ui.editedDraft, maxlength: 30000, rows: 12, 'aria-label': 'Edit your draft', readonly: ui.generating || Boolean(ui.rememberDraft)});
    const editStatus = helper(ui.draft.teaching_baseline && !draftHasTeachingChanges() ? (ui.draft.teaching_baseline_reason === 'dismissed' ? 'Current edits kept for this message only.' : ui.draft.teaching_baseline_reason === 'undone' ? 'Your draft edit remains. The remembered lesson was undone.' : 'This correction was remembered. Future edits stay separate.') : draftHasEdits() ? 'Edited by you. These changes have not been remembered.' : 'Generated draft. Editing alone teaches nothing.');
    const remember = button('Remember this correction', openCorrectionPreview, 'small primary'); remember.disabled = !draftHasTeachingChanges() || ui.generating || Boolean(ui.rememberDraft);
    const undo = button(draftHasEdits() ? 'Undo my edits' : 'Restore my edits', () => {
      if (draftHasEdits()) { ui.draftUndo = ui.editedDraft; ui.editedDraft = currentDraftOriginal(); }
      else if (ui.draftUndo !== null) { ui.editedDraft = ui.draftUndo; ui.draftUndo = null; }
      ui.rememberDraft = null; persistEditor(); render();
    }, 'small quiet'); undo.disabled = (!draftHasEdits() && ui.draftUndo === null) || ui.generating || Boolean(ui.rememberDraft);
    draftEditor.addEventListener('input', () => { ui.editedDraft = draftEditor.value; persistEditor(); remember.disabled = !draftHasTeachingChanges() || ui.generating; undo.disabled = !draftHasEdits() && ui.draftUndo === null; undo.textContent = draftHasEdits() ? 'Undo my edits' : 'Restore my edits'; editStatus.textContent = ui.draft.teaching_baseline && !draftHasTeachingChanges() ? (ui.draft.teaching_baseline_reason === 'dismissed' ? 'Current edits kept for this message only.' : ui.draft.teaching_baseline_reason === 'undone' ? 'Your draft edit remains. The remembered lesson was undone.' : 'This correction was remembered. Future edits stay separate.') : draftHasEdits() ? 'Edited by you. These changes have not been remembered.' : 'Generated draft. Editing alone teaches nothing.'; });
    const copy = button('Copy current draft', async () => { try { await navigator.clipboard.writeText(ui.editedDraft); toast('Your current draft was copied. No learning was saved.'); } catch { toast('Clipboard access was denied. Select the draft text and copy it directly.', true); } }, 'small', 'copy');
    output.append(draftEditor, el('div', {class: 'draft-edit-toolbar'}, editStatus, el('div', {class: 'button-group'}, copy, undo)), el('div', {class: 'panel-foot'}, helper('Only an explicitly confirmed correction becomes a lesson.'), remember));
    const evidenceSamples = (ui.draft.evidence_ids || []).map(id => state.samples.find(sample => String(sample.id) === String(id))).filter(Boolean);
    output.append(el('div', {class: 'evidence'}, contextPills(ui.draft.context), details('Draft context, sources & limitations', el('div', {}, helper('This is the context captured when this draft was generated. Changing the compose form does not change it.'), evidenceSamples.length ? el('ul', {class: 'evidence-list'}, evidenceSamples.map(sample => el('li', {}, sample.title || 'Untitled sample'))) : helper('No confirmed authored source samples were supplied.'), (ui.draft.warnings || []).map(helper)))));
  } else output.append(el('div', { class: 'draft-empty' }, icon('feather'), el('h3', {}, 'Write first. Teach as you go.'), el('p', {}, 'Describe a real message on the left. Edit the result naturally. You can ask it to remember a useful correction when one appears.')), el('div', {class: 'evidence'}, helper(`${plural(activeLanguageLessons().length, 'active language lesson')} · ${plural(state.feedback.length, 'saved choice')} · samples optional`)));
  const right = el('div', {class: 'primary-column'}, output, ui.rememberDraft && correctionRememberPanel());
  const pieces = [pageHeading('Writing / a useful draft, a considered correction', 'Write it. Make it yours.', 'Use a draft today. Teach a preference only when you have something worth remembering.')];
  if (!isLive()) pieces.push(el('div', {style: 'margin-bottom: 21px'}, callout('Offline rehearsal', 'This mode uses simple text rules, not a language model. Your explicitly saved lessons persist; rehearsal drafts cannot demonstrate improved writing quality.', 'warning')));
  if (ui.editorStorageError) pieces.push(el('div', {class: 'inline-error'}, 'Browser storage is unavailable. Copy your draft before refreshing or closing this page.'));
  pieces.push(el('div', {class: 'write-layout'}, form, right), el('p', {class: 'helper local-draft-note'}, 'Drafts, edits, and unsaved memory previews stay in this browser across navigation and refresh. Copying, editing, and adjusting controls never teach a lesson by themselves.'), correctionsHistoryPanel());
  return pieces;
}
function correctionRememberPanel() {
  const pending = ui.rememberDraft;
  const span = changedSpan(pending.original_text, pending.revised_text);
  const takeaway = el('textarea', {rows: 3, maxlength: 2000, value: pending.guidance, placeholder: 'e.g. Use everyday verbs in updates to my team. Prefer “use” over “utilize.”', onInput: event => { pending.guidance = event.target.value; persistEditor(); }});
  const kind = select([['', 'Choose what changed…'], ['wording', 'Wording'], ['sentence', 'Sentence structure'], ['presentation', 'Presentation'], ['meaning', 'Facts or meaning']], pending.kind, {onChange: event => { pending.kind = event.target.value; pending.dimension = event.target.value === 'wording' ? 'diction' : 'syntax'; persistEditor(); updateScope(); }});
  const scope = select([['similar', 'Similar messages (recommended)'], ['channel', 'All writing in this context']], pending.scope, {onChange: event => { pending.scope = event.target.value; persistEditor(); updateScope(); }});
  const scopeSummary = el('p', {class: 'scope-summary', 'aria-live': 'polite'});
  const context = contextEditor(pending.context, {remember: false, onChange: value => { pending.context = value; persistEditor(); updateScope(); }});
  const preferred = el('textarea', {rows: 2, class: 'phrase-input', maxlength: 500, value: pending.preferred, placeholder: 'Optional short example to favor', onInput: event => { pending.preferred = event.target.value; persistEditor(); }});
  const avoided = el('textarea', {rows: 2, class: 'phrase-input', maxlength: 500, value: pending.avoided, placeholder: 'Optional short example to avoid', onInput: event => { pending.avoided = event.target.value; persistEditor(); }});
  const notice = el('div', {class: 'inline-error', hidden: true});
  const save = button('Remember this preference', null, 'primary small', 'check'); save.type = 'submit';
  const updateScope = () => {
    const facts = pending.kind === 'meaning'; save.disabled = facts;
    notice.hidden = !facts; notice.textContent = 'Facts and meaning corrections apply only to this draft. Keep your edit; it cannot be saved as a style preference.';
    scopeSummary.textContent = correctionScopeSummary(pending.context, pending.scope);
    context.controls.audience.placeholder = pending.scope === 'similar' ? 'Who should this apply to? e.g. my team' : 'Not used for a broad context rule';
  };
  const preview = el('div', {class: 'correction-compare'}, el('div', {}, el('div', {class: 'field-label'}, 'Before'), el('div', {class: 'correction-text before'}, span.before || '(nothing)')), el('div', {}, el('div', {class: 'field-label'}, 'Your correction'), el('div', {class: 'correction-text after'}, span.after || '(removed)')));
  const form = el('form', {class: 'panel', id: 'remember-correction'}, panelHeading('Remember what you meant to teach.', 'Check the change, write the takeaway, and choose where it applies.'), el('div', {class: 'panel-body'}, preview, !span.phraseFits && el('p', {class: 'helper', style: 'margin: 12px 0'}, 'This edit is too long for a short phrase rule. Describe a concise takeaway below; the full original and revised draft will be preserved without truncation.'), field('What did you change?', kind), notice, field('The lesson to remember', takeaway, 'required; 2,000 characters max'), details('Optional short examples', el('div', {class: 'form-row'}, field('Prefer', preferred), field('Avoid', avoided))), field('Use this for', scope), context.node, scopeSummary, helper('Only recipient, purpose, relationship, and stakes define a similar-message match. Situation notes are background context, not an enforced matching filter.')), el('div', {class: 'panel-foot'}, button('Just this message', () => { ui.draft.teaching_baseline = pending.revised_text; ui.draft.teaching_baseline_reason = 'dismissed'; ui.rememberDraft = null; persistEditor(); render(); toast('Kept your edited draft. Nothing was learned.'); }, 'small quiet'), save));
  updateScope();
  form.addEventListener('submit', event => { event.preventDefault(); busy(save, async () => {
    if (!pending.kind) { kind.focus(); throw new Error('Choose what you changed before remembering it.'); }
    if (pending.kind === 'meaning') throw new Error('Facts and meaning changes cannot be saved as a style lesson.');
    if (!pending.guidance.trim()) { takeaway.focus(); throw new Error('Write a short takeaway describing what to remember.'); }
    if (pending.scope === 'similar' && !hasSimilarScope(pending.context)) { context.controls.audience.focus(); throw new Error('Who is this for? Add a recipient to keep the lesson specific, or explicitly choose all writing in this context.'); }
    const result = await api('/corrections', 'POST', {original_text: pending.original_text, revised_text: pending.revised_text, guidance: pending.guidance.trim(), preferred: pending.preferred.trim(), avoided: pending.avoided.trim(), context: pending.context, scope: pending.scope, dimension: pending.dimension, kind: 'style'});
    ui.draft.teaching_baseline = pending.revised_text; ui.draft.teaching_baseline_reason = 'remembered'; ui.draft.teaching_correction_id = result.correction.id; ui.rememberDraft = null; ui.lastCorrectionId = result.correction.id; persistEditor(); await refresh(); toast('Preference remembered for the scope you chose. You can undo it below.');
  }); });
  return form;
}
function undoCorrectionButton(correction, label = 'Undo remembered correction') {
  const undo = button(label, () => busy(undo, async () => { await api(`/corrections/${encodeURIComponent(correction.id)}/undo`, 'POST', {}); if (ui.draft?.teaching_correction_id === correction.id) { ui.draft.teaching_baseline_reason = 'undone'; persistEditor(); } await refresh(); toast('This remembered correction is no longer used. Your edited draft remains intact.'); }), 'small');
  return undo;
}
function correctionsHistoryPanel() {
  const corrections = state.corrections || [];
  const list = el('div', {class: 'panel-body'});
  if (!corrections.length) list.append(helper('No corrections remembered yet. Edit a draft, then choose “Remember this correction” only when it should shape future writing.'));
  for (const correction of corrections) {
    const isActive = correction.status === 'active';
    const statuses = {active: 'In use', undone: 'Undone', inactive: 'Lesson disabled', lesson_changed: 'Lesson edited separately', lesson_deleted: 'Lesson deleted'};
    const span = changedSpan(correction.original_text, correction.revised_text);
    const row = el('article', {class: 'remembered-correction'}, el('div', {class: 'review-top'}, el('h3', {}, correction.guidance), badge(statuses[correction.status] || correction.status, isActive ? '' : 'muted')), helper(correctionScopeSummary(correction.context, correction.scope)), details('Review the saved correction', el('div', {}, el('div', {class: 'correction-compare'}, el('div', {}, el('div', {class: 'field-label'}, 'Before'), el('div', {class: 'correction-text before'}, span.before || '(nothing)')), el('div', {}, el('div', {class: 'field-label'}, 'After'), el('div', {class: 'correction-text after'}, span.after || '(removed)'))), details('Full original and revised drafts', el('div', {}, el('h3', {}, 'Original'), el('pre', {class: 'provenance-text'}, correction.original_text), el('h3', {}, 'Revised'), el('pre', {class: 'provenance-text'}, correction.revised_text))))));
    if (isActive) row.append(el('div', {class: 'button-group', style: 'margin-top: 12px'}, undoCorrectionButton(correction), button('Check on a new message', () => launchCorrectionCheck(correction), 'small quiet', 'arrow')));
    else if (correction.status === 'lesson_changed') row.append(helper('The lesson was edited separately. Review it in Voice profile; undo will not overwrite those changes.'));
    list.append(row);
  }
  return el('section', {class: 'panel correction-history'}, panelHeading('What you’ve asked it to remember', 'Review or undo saved corrections here. Your draft edits are separate.', badge(`${corrections.filter(item => item.status === 'active').length} in use`, 'muted')), list);
}
function renderProfile() {
  const profile = state.profile || {};
  const confirmed = state.samples.filter(sample => sample.status === 'confirmed');
  const eligible = confirmed.filter(sample => !sample.held_out);
  const contexts = profile.contexts || [];
  const preferences = profile.preferences || [];
  const patterns = profile.patterns || [];
  const lessonContexts = profile.language_contexts || [];
  const coverageMap = new Map();
  for (const item of contexts) coverageMap.set(item.channel, {samples: item.count, lessons: 0});
  for (const item of lessonContexts) { const row = coverageMap.get(item.channel) || {samples: 0, lessons: 0}; row.lessons = item.count; coverageMap.set(item.channel, row); }
  const stats = el('div', { class: 'panel stats-strip' }, [[activeLanguageLessons().length, 'Active language lessons'], [state.feedback.length, 'Saved choices'], [eligible.length, 'Optional authored samples'], [coverageMap.size, 'Contexts with lessons or samples']].map(([value, label]) => el('div', { class: 'stat-cell' }, el('strong', {}, value), el('span', {}, label))));
  const patternBody = patterns.length ? el('div', { class: 'patterns' }, patterns.map(pattern => el('div', { class: 'pattern' }, el('div', {}, el('strong', {}, pattern.name), el('p', {}, asText(pattern.value))), badge(`${pattern.evidence_count || 0} sources`, 'muted')))) : empty('Authored samples are optional.', 'Your direct lessons and choices can guide writing now. Add confirmed samples if you want observed writing patterns here, too.', button('Add your writing', () => navigate('samples'), 'small', 'plus'), 'profile');
  const patternPanel = el('section', { class: 'panel' }, panelHeading('Observed writing patterns', `${profile.patterns_scope || 'Descriptive signals from confirmed writing.'} ${confirmed.length - eligible.length} held-out samples excluded.`), el('div', { class: 'panel-body' }, patternBody));
  const preferenceBody = preferences.length ? preferences.slice().reverse().map(preference => el('article', { class: 'preference' }, contextPills(typeof preference.context === 'object' ? preference.context : { channel: preference.context || 'work' }), preference.focus && el('div', {style: 'margin-top: 8px'}, badge(`${practiceFocuses[preference.focus]?.label || preference.focus} practice`, 'muted')), preference.notes && el('p', {}, preference.notes), Object.keys(preference.presentation || {}).length > 0 && el('dl', {}, Object.entries(preference.presentation).map(([key, value]) => el('div', {}, el('dt', {}, (presentationOptions[key]?.[0] || key) + ':'), el('dd', {}, asText(value))))), preference.edited_text && details('Your feedback rewrite', el('p', { style: 'white-space: pre-wrap' }, preference.edited_text)), !preference.notes && !Object.keys(preference.presentation || {}).length && !preference.edited_text && el('p', {}, preference.choice === 'neither' ? 'Rejected both alternatives for this context.' : `Preferred option ${asText(preference.choice).toUpperCase()} for this context.`))) : empty('Tell it what feels right.', 'A preferred phrase, an unwanted sign-off, or a choice of bullets over prose all give your profile useful direction.', button('Start a practice round', () => navigate('practice'), 'small', 'arrow'), 'practice');
  const preferencePanel = el('section', { class: 'panel' }, panelHeading('Preferences, in context', 'Your choices and reasons, kept separate from authored evidence.'), el('div', { class: 'panel-body' }, preferenceBody));
  const coverage = el('section', { class: 'panel panel-pad' }, el('div', { class: 'section-title' }, 'Context coverage'), coverageMap.size ? [...coverageMap].map(([channel, counts]) => el('div', { class: 'coverage-row' }, el('strong', {}, channel || 'work'), el('span', {}, `${plural(counts.lessons, 'lesson')} · ${plural(counts.samples, 'sample')}`))) : helper('Teach a language preference in any context to start. Work, personal, community, and labels you create stay distinct.'));
  const provider = el('section', { class: 'panel panel-pad' }, el('div', { class: 'section-title' }, 'Generation provider'), providerPill(), el('p', { class: 'export-note' }, isLive() ? `Using ${{anthropic: 'the Anthropic API', claude: 'Claude through your Claude Code login', codex: 'your local Codex CLI'}[state.settings.provider]}${state.settings.model ? ` with ${state.settings.model}` : ''}. Selected evidence and your brief are sent to the configured model when you generate.` : 'Offline mode rehearses the workflow with deterministic rules. No language model is running. The README explains how to start with the Claude, Codex or Anthropic provider.'), helper(state.settings.provider === 'claude' ? (state.capabilities?.provider_available ? 'Claude Code CLI detected. It runs with your login in safe mode, with no tools, memory or plugins. Sign-in is verified only when generation succeeds.' : 'The Claude Code CLI was not found by the server.') : state.settings.provider === 'anthropic' ? (state.capabilities?.provider_available ? 'An API key is present in the server environment. It is never shown here; access is verified only when generation succeeds.' : 'ANTHROPIC_API_KEY is not set for the server process.') : state.capabilities?.codex_available ? 'Codex CLI detected. Authentication is verified only when generation succeeds.' : 'Codex CLI availability has not been confirmed by the server.'));
  return [pageHeading('Voice profile / evidence, not a score', 'A voice that keeps its context.', 'See what you’ve contributed, what you’ve explicitly preferred, and where there is more to learn.'), stats, el('div', { class: 'layout confidence-layout' }, confidencePanel(), el('div', { class: 'primary-column' }, proposalPanel(), languageLessonPanel(), preferencePanel, patternPanel, priorPanel()), el('aside', { class: 'aside-column' }, exportPanel(), storyEnginePanel(), coverage, provider, el('div', { class: 'aside-note' }, el('h3', {}, 'A profile, not a personality verdict.'), el('p', {}, 'This is a portable collection of evidence and preferences. Coverage grows with your contributions; a completion or XP count never establishes writing accuracy.'))))];
}
function proposalPanel() {
  const proposals = state.lesson_proposals || [];
  if (!proposals.length) return null;
  const decide = (proposal, decision, control) => busy(control, async () => {
    await api(`/lesson-proposals/${encodeURIComponent(proposal.id)}/${decision}`, 'POST', {});
    await refresh();
    toast(decision === 'accept' ? 'Lesson saved for its context.' : 'Proposal dismissed. Nothing was saved.');
  });
  const items = proposals.map(proposal => {
    const lesson = proposal.lesson;
    const accept = button('Save as lesson', null, 'primary small');
    const dismiss = button('Dismiss', null, 'small');
    accept.addEventListener('click', () => decide(proposal, 'accept', accept));
    dismiss.addEventListener('click', () => decide(proposal, 'dismiss', dismiss));
    const lines = [['Guidance', lesson.guidance], ['Prefer', lesson.preferred], ['Avoid', lesson.avoided], ['Why the assistant suggested it', proposal.rationale]].filter(([, value]) => value);
    const change = (proposal.before || proposal.after) && details('The correction it came from', el('div', {}, proposal.before && el('p', { class: 'helper' }, `Before: ${proposal.before}`), proposal.after && el('p', { class: 'helper' }, `After: ${proposal.after}`)));
    return el('article', { class: 'preference' }, el('div', { class: 'section-title' }, `${lesson.dimension} · from ${proposal.source}`), contextPills(lesson.context),
      ...lines.map(([label, value]) => el('p', {}, el('strong', {}, `${label}: `), value)), change || null,
      el('div', { class: 'proposal-actions' }, accept, dismiss));
  });
  return el('section', { class: 'panel' }, panelHeading('Proposed lessons', 'Suggested by a connected assistant. Nothing changes until you save one.', badge(`${proposals.length} waiting`, 'amber')), el('div', { class: 'panel-body' }, items));
}

function exportPanel() {
  const include = input({ type: 'checkbox', checked: ui.includeExamples, onChange: event => { ui.includeExamples = event.target.checked; ui.exportText = ''; preview.replaceChildren(); } });
  const preview = el('div', {});
  const previewButton = button('Preview export', () => busy(previewButton, async () => {
    const result = await api(`/export${ui.includeExamples ? '?examples=1' : ''}`); ui.exportText = result.markdown;
    preview.replaceChildren(details('Portable profile preview', el('pre', { class: 'export-preview' }, result.markdown), true));
  }, 'Loading…'), 'wide small');
  const download = button('Export voice profile', () => busy(download, async () => {
    const result = await api(`/export${ui.includeExamples ? '?examples=1' : ''}`);
    const blob = new Blob([result.markdown], { type: 'text/markdown;charset=utf-8' }); const url = URL.createObjectURL(blob);
    const link = el('a', { href: url, download: result.filename || 'VOICE_PROFILE.md' }); document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast('Voice profile exported.');
  }, 'Preparing…'), 'primary wide', 'download');
  if (ui.exportText) preview.append(details('Portable profile preview', el('pre', { class: 'export-preview' }, ui.exportText), true));
  return el('section', { class: 'panel panel-pad' }, el('div', { class: 'section-title' }, 'Take your voice with you'), el('h3', {}, 'A portable voice profile'), el('p', { class: 'export-note' }, 'Export a Markdown profile for Cursor, Codex, ChatGPT, or another writing workflow. Source text is excluded by default.'), el('label', { class: 'checkbox', style: 'margin-bottom: 17px' }, include, el('span', {}, 'Include my confirmed writing examples. Held-out samples stay excluded.')), download, el('div', { style: 'margin-top: 7px' }, previewButton), preview);
}

function storyEnginePanel() {
  const sources = state.story_engine_sources || [];
  if (!ui.storyChannels) ui.storyChannels = sources.filter(item => item.suggested).map(item => item.channel);
  const preview = el('div', {});
  const reset = () => { ui.storyVoice = null; preview.replaceChildren(); };
  const target = input({ maxlength: 200, value: ui.storyTarget, placeholder: 'e.g. spoken interview answer, short story narrator', onInput: event => { ui.storyTarget = event.target.value; reset(); } });
  const sourceList = sources.length ? el('div', { class: 'story-sources', role: 'group', 'aria-label': 'Voicework channels to draw from' }, sources.map(item => {
    const box = input({ type: 'checkbox', checked: ui.storyChannels.includes(item.channel), onChange: event => {
      ui.storyChannels = event.target.checked ? [...new Set([...ui.storyChannels, item.channel])] : ui.storyChannels.filter(key => key !== item.channel); reset();
    } });
    const counts = [item.lessons && `${item.lessons} lesson${item.lessons === 1 ? '' : 's'}`, item.feedback && `${item.feedback} practice`, item.samples && `${item.samples} sample${item.samples === 1 ? '' : 's'}`].filter(Boolean).join(' · ');
    return el('label', { class: 'checkbox' }, box, el('span', {}, el('strong', {}, item.label), ` — ${counts}${item.suggested ? ' (saved as broad long-form)' : ''}`));
  })) : helper('No lessons, practice notes, or confirmed samples yet. Teach a lesson or remember a correction first.');
  const examples = input({ type: 'checkbox', checked: ui.storyExamples, onChange: event => { ui.storyExamples = event.target.checked; reset(); } });
  const query = () => {
    if (!ui.storyTarget.trim()) throw new Error('Describe where Story Engine will use this voice.');
    if (!ui.storyChannels.length) throw new Error('Choose at least one channel whose habits should carry into the story.');
    const params = new URLSearchParams({ target: ui.storyTarget.trim() });
    for (const channel of ui.storyChannels) params.append('channel', channel);
    if (ui.storyExamples) params.set('examples', '1');
    return `/export/story-engine?${params}`;
  };
  const render = voice => {
    const p = voice.provenance;
    preview.replaceChildren(
      el('p', { class: 'export-note' }, `${p.counts.traits} traits · ${p.counts.avoid} avoid · ${p.counts.examples_included} authored excerpt${p.counts.examples_included === 1 ? '' : 's'} · from ${p.counts.lessons_used} lessons and ${p.counts.feedback_used} practice notes`),
      ...p.warnings.map(text => el('div', { class: 'callout warning', style: 'margin: 8px 0' }, text)),
      details('Story Engine voice preview', el('pre', { class: 'export-preview' }, JSON.stringify(voice, null, 2)), true));
  };
  const previewButton = button('Preview', () => busy(previewButton, async () => {
    const result = await api(query()); ui.storyVoice = result.story_voice; render(result.story_voice);
  }, 'Loading…'), 'wide small');
  const download = button('Export for Story Engine', () => busy(download, async () => {
    const result = await api(query()); ui.storyVoice = result.story_voice; render(result.story_voice);
    const blob = new Blob([JSON.stringify(result.story_voice, null, 2) + '\n'], { type: 'application/json;charset=utf-8' }); const url = URL.createObjectURL(blob);
    const link = el('a', { href: url, download: result.filename || 'story-voice.json' }); document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
    toast('Story Engine voice exported.');
  }, 'Preparing…'), 'primary wide', 'download');
  if (ui.storyVoice) render(ui.storyVoice);
  return el('section', { class: 'panel panel-pad' }, el('div', { class: 'section-title' }, 'Tell stories in your voice'), el('h3', {}, 'Use with Story Engine'),
    el('p', { class: 'export-note' }, 'Turns the lessons, practice notes and patterns from the channels you choose into a Story Engine voice file. Each habit keeps the context it was learned in. Prior packets and AI options are never included.'),
    field('Story use', target), el('div', { class: 'field-label', style: 'margin: 12px 0 6px' }, 'Draw habits from'), sourceList,
    el('label', { class: 'checkbox', style: 'margin: 14px 0 17px' }, examples, el('span', {}, 'Include up to 5 confirmed excerpts from these channels as voice samples. Held-out samples stay excluded.')),
    download, el('div', { style: 'margin-top: 7px' }, previewButton), preview,
    helper('Then: python3 story_engine.py bundle --voice-profile story-voice.json --seed "…"'));
}

function priorPanel() {
  const label = input({maxlength:200, placeholder:'e.g. What my assistant already knows about my work replies'});
  const text = el('textarea', {maxlength:4000, rows:5, placeholder:'A compact, reviewable description of your preferences. This is prior context, not a sample of your authored writing.'});
  const source = input({maxlength:500, placeholder:'Where this understanding came from'});
  const context = contextEditor(ui.context, {remember:false});
  const submit = button('Add prior context for review', null, 'small primary'); submit.type='submit';
  const form = el('form', {}, field('Prior label',label),field('What should the assistant know?',text),field('Source',source),context.node,submit);
  form.addEventListener('submit', event=>{event.preventDefault();busy(submit,async()=>{
    await api('/priors','POST',{label:label.value.trim(),text:text.value.trim(),source:source.value.trim(),context:context.get()});await refresh();toast('Prior added for your review.');
  });});
  const list=el('div', {});
  for (const prior of state.priors || []) {
    const approve=button(prior.confirmed ? 'Stop using this prior' : 'Confirm this understanding',()=>busy(approve,async()=>{await api(`/priors/${encodeURIComponent(prior.id)}`,'PATCH',{confirmed:!prior.confirmed});await refresh();}), 'small');
    list.append(el('article',{class:'preference'},el('h3',{},prior.label),contextPills(prior.context),badge(prior.confirmed?'Reviewed prior':'Needs review',prior.confirmed?'':'amber'),el('p',{style:'white-space:pre-wrap'},prior.text),helper(prior.source),approve));
  }
  return el('section',{class:'panel'},panelHeading('Prior understanding','Bring useful knowledge from another assistant explicitly. Review it before it influences drafts.'),el('div',{class:'panel-body'},list,details('Add a context packet',form),helper('Only confirmed priors in the matching context are used. Hidden conversation history is not imported.')));
}
function renderEvaluate() {
  const summary = state.quality_summary || {};
  const active = (state.corrections || []).filter(item => item.status === 'active');
  if (!active.some(item => item.id === ui.qualityForm.correctionId)) {
    ui.qualityForm.correctionId = active[0]?.id || '';
    ui.qualityForm.context = active[0] ? {...active[0].context} : {...ui.context};
  }
  const selected = active.find(item => item.id === ui.qualityForm.correctionId);
  const column = el('div', {class: 'primary-column'});
  for (const item of (state.quality_checks || []).filter(check => check.status === 'unrated')) column.append(qualityCheckPanel(item));
  if (selected) {
    const correction = select(active.map(item => [item.id, item.guidance.length > 100 ? `${item.guidance.slice(0, 97)}…` : item.guidance]), selected.id, {onChange: event => {
      const target = active.find(item => item.id === event.target.value);
      ui.qualityForm.correctionId = target.id; ui.qualityForm.context = {...target.context}; persistEditor(); render();
    }});
    const context = contextEditor(ui.qualityForm.context || selected.context, {remember: false, onChange: value => { ui.qualityForm.context = value; persistEditor(); }});
    const brief = el('textarea', {required: true, rows: 4, maxlength: 15000, value: ui.qualityForm.brief || '', placeholder: 'Use a new message with the same kind of recipient or purpose. Supply the facts both drafts must preserve; do not paste your previous correction as the answer.', onInput: event => { ui.qualityForm.brief = event.target.value; persistEditor(); }});
    const submit = button(isLive() ? 'Check this correction blind' : 'Rehearse this correction check', null, 'primary', 'arrow'); submit.type = 'submit';
    const setup = el('form', {class: 'panel'}, panelHeading('Did this correction actually help?', 'Try it on a new brief. The two drafts differ only in whether this correction is included.'), el('div', {class: 'panel-body'}, field('Remembered correction', correction), helper(selected.guidance), el('p', {class: 'scope-summary'}, correctionScopeSummary(selected.context, selected.scope)), field('A new message to write', brief), context.node), el('div', {class: 'panel-foot'}, helper('Without / with labels stay hidden until you rate both.'), submit));
    setup.addEventListener('submit', event => { event.preventDefault(); busy(submit, async () => {
      if (!brief.value.trim()) throw new Error('Give the check a new message and its facts.');
      const result = await api(`/corrections/${encodeURIComponent(selected.id)}/checks`, 'POST', {context: context.get(), brief: brief.value.trim()});
      ui.qualityForm.brief = ''; persistEditor(); await refresh(); toast('Blind check ready. Judge meaning and effort for each draft.');
    }, 'Preparing two drafts…'); });
    column.append(setup);
  } else column.append(el('section', {class: 'panel panel-pad'}, el('h2', {}, 'Start with a correction worth remembering.'), helper('Write a real message, edit it, and explicitly remember a style preference. Then check that one correction on a new brief here.'), el('div', {style: 'margin-top: 15px'}, button('Go to your draft', () => navigate('write'), 'primary small', 'write'))));
  const counts = el('section', {class: 'panel panel-pad'}, el('h2', {}, 'What these checks have shown'), helper(`${summary.rated_real || 0} rated AI checks · ${summary.updated_wins || 0} preferred with correction · ${summary.previous_wins || 0} preferred without · ${summary.ties || 0} ties · ${summary.neither || 0} neither`), helper(`${summary.meaning_issues || 0} checks with meaning concerns · ${summary.rated_offline || 0} rated offline rehearsals excluded from AI results`), el('p', {class: 'helper', style: 'margin-top: 10px'}, 'These judgments apply to the messages checked. A preference win, synthetic scenario, or rehearsal does not prove personal accuracy or general improvement.'));
  const history = (state.quality_checks || []).filter(item => item.status !== 'unrated');
  for (const item of history) counts.append(qualityCheckPanel(item, true));
  column.append(counts);
  const wholeVoice = details('Compare your whole voice profile', el('div', {class: 'whole-voice-evaluation'}, helper('Compare the full calibrated profile with a baseline. These judgments inform voice confidence readiness and stay separate from correction checks.'), renderWholeVoiceEvaluation()), ui.wholeVoiceOpen);
  wholeVoice.addEventListener('toggle', () => { ui.wholeVoiceOpen = wholeVoice.open; });
  column.append(el('section', {class: 'panel panel-pad', id: 'whole-voice-comparison'}, wholeVoice));
  return [pageHeading('Check fit / one correction at a time', 'Keep the changes that help.', 'Compare a saved correction on a new message. Judge which needs less work and whether both preserve the meaning.'), !isLive() && el('div', {style: 'margin-bottom: 20px'}, callout('Offline checks are rehearsals', 'Both versions use deterministic rules. Ratings are kept separately and do not count as evidence that an AI model improved.', 'warning')), column];
}
function qualityCheckPanel(item, nested = false) {
  const rated = item.status !== 'unrated';
  const correction = (state.corrections || []).find(value => value.id === item.correction_id);
  const draft = ui.qualityVotes[item.id] || {choice: '', meaning: {a: '', b: ''}, effort: {a: '', b: ''}, notes: ''};
  ui.qualityVotes[item.id] = draft;
  const chooseButtons = [];
  const submit = button('Save judgment & reveal labels', null, 'primary small'); submit.type = 'submit';
  const updateChoice = value => { draft.choice = value; chooseButtons.forEach(([key, target]) => { target.classList.toggle('selected', key === value); target.setAttribute('aria-pressed', String(key === value)); }); persistEditor(); };
  const options = el('div', {class: 'quality-options'});
  for (const option of item.options || []) {
    const id = option.id;
    const label = rated ? (item.mapping?.[id] === 'updated' ? 'With this correction' : 'Without this correction') : 'Identity hidden';
    const choose = button(`Prefer ${id.toUpperCase()}`, () => updateChoice(id), `small ${draft.choice === id ? 'selected' : ''}`); choose.setAttribute('aria-pressed', String(draft.choice === id)); chooseButtons.push([id, choose]);
    const body = el('div', {class: 'quality-option'}, el('div', {class: 'review-top'}, el('span', {class: 'option-letter'}, id.toUpperCase()), badge(label, 'muted')), el('div', {class: 'option-text quality-text'}, option.text));
    if (rated) body.append(el('div', {class: 'quality-rating-summary'}, helper(`Meaning: ${item.meaning?.[id] || 'not recorded'}`), helper(`Editing effort: ${item.effort?.[id] || 'not recorded'}`)));
    else {
      const meaning = select([['', 'Choose…'], ['preserved', 'Meaning preserved'], ['changed', 'Meaning changed'], ['unsure', 'I’m not sure']], draft.meaning[id], {required: true, onChange: event => { draft.meaning[id] = event.target.value; persistEditor(); }});
      const effort = select([['', 'Choose…'], ['none', 'Ready as written'], ['light', 'Light edits'], ['substantial', 'Substantial edits']], draft.effort[id], {required: true, onChange: event => { draft.effort[id] = event.target.value; persistEditor(); }});
      body.append(el('div', {class: 'quality-rating-fields'}, field(`Does ${id.toUpperCase()} keep the meaning?`, meaning), field(`How much work does ${id.toUpperCase()} need?`, effort), choose));
    }
    options.append(body);
  }
  const body = el('div', {class: nested ? '' : 'panel-body'}, correction && el('p', {class: 'helper'}, `Testing: ${correction.guidance}`), contextPills(item.context), el('p', {class: 'quality-brief'}, item.brief), options);
  if (item.warnings?.length) body.append(details('Provider notes', el('div', {}, item.warnings.map(helper))));
  if (rated) {
    const result = {updated: 'Preferred with this correction', previous: 'Preferred without this correction', tie: 'Both fit equally', neither: 'Neither fit'}[item.result] || item.result;
    body.append(el('p', {class: 'quality-result'}, result), item.notes && helper(item.notes), correction?.status === 'active' && undoCorrectionButton(correction, 'Undo this correction'));
    return el('article', {class: nested ? 'rated-quality-check' : 'panel'}, body);
  }
  const alternatives = el('div', {class: 'button-group', role: 'group', 'aria-label': 'Overall preference'});
  for (const [value, label] of [['tie', 'Both fit equally'], ['neither', 'Neither fits']]) { const control = button(label, () => updateChoice(value), `small ${draft.choice === value ? 'selected' : ''}`); control.setAttribute('aria-pressed', String(draft.choice === value)); chooseButtons.push([value, control]); alternatives.append(control); }
  const notes = el('textarea', {rows: 2, maxlength: 2000, value: draft.notes, placeholder: 'What helped, hurt, or changed the meaning?', onInput: event => { draft.notes = event.target.value; persistEditor(); }});
  body.append(alternatives, el('div', {style: 'margin-top: 16px'}, field('A note about your judgment', notes, 'optional')));
  const form = el('form', {class: 'panel'}, panelHeading('Which version would you use?', 'Rate the meaning and editing effort of both, then choose a version, tie, or neither.', badge(isLive(item.provider) ? 'Blind AI check' : 'Offline rehearsal', isLive(item.provider) ? '' : 'amber')), body, el('div', {class: 'panel-foot'}, helper('These ratings evaluate the correction. They do not train new preferences.'), submit));
  form.addEventListener('submit', event => { event.preventDefault(); busy(submit, async () => {
    if (!draft.choice) throw new Error('Choose A, B, both, or neither.');
    if (!draft.meaning.a || !draft.meaning.b || !draft.effort.a || !draft.effort.b) throw new Error('Rate meaning and editing effort for both A and B.');
    await api(`/quality-checks/${encodeURIComponent(item.id)}/rate`, 'POST', {choice: draft.choice, meaning: draft.meaning, effort: draft.effort, notes: draft.notes.trim()});
    delete ui.qualityVotes[item.id]; persistEditor(); await refresh(); toast('Judgment saved. Without / with labels are now revealed.');
  }); });
  return form;
}

function renderWholeVoiceEvaluation() {
  const summary=state.evaluation_summary || {};
  const context=contextEditor(ui.context,{scenario:true});
  const brief=el('textarea',{required:true,maxlength:15000,rows:4,placeholder:'Give both versions the same facts and desired outcome. Do not paste held-out answer text.', 'aria-label':'Comparison brief'});
  const submit=button(isLive()?'Create a blind comparison':'Rehearse a blind comparison',null,'primary');submit.type='submit';
  const setup=el('form',{class:'panel'},panelHeading('Does calibration actually help?','Compare a baseline and your calibrated voice before seeing which is which.'),el('div',{class:'panel-body'},context.node,field('Same brief for both drafts',brief),helper('Both versions use the same model and controls. Evaluation votes stay separate from training.')),el('div',{class:'panel-foot'},helper(isLive()?`Generates two drafts through ${providerPhrase()}.`:'Offline rehearsals do not count as evidence of AI quality.'),submit));
  setup.addEventListener('submit',event=>{event.preventDefault();busy(submit,async()=>{await api('/evaluations','POST',{context:context.get(),brief:brief.value.trim()});await refresh();toast('Comparison ready. The labels are hidden until you vote.');},'Comparing…');});
  const cases=el('div',{class:'primary-column'});
  for(const item of (state.evaluations || []).filter(x=>x.status==='unrated')) {
    let choice='';const buttons=[];
    const notes=el('textarea',{rows:2,maxlength:2000,placeholder:'What would you change before sending?', 'aria-label':'Evaluation notes'});
    const factual=input({type:'checkbox'});
    const save=button('Record judgment and reveal labels',()=>busy(save,async()=>{
      if(!choice)throw new Error('Choose A, B, tie or neither.');
      await api(`/evaluations/${encodeURIComponent(item.id)}/rate`,'POST',{choice,factual_issue:factual.checked,notes:notes.value.trim()});await refresh();toast('Judgment recorded separately from your training feedback.');
    }),'primary');save.disabled=true;
    const choose=value=>{choice=value;save.disabled=false;for(const [v,b]of buttons){b.classList.toggle('selected',v===value);b.setAttribute('aria-pressed',v===value?'true':'false');}};
    const options=el('div',{class:'round-options'});
    for(const option of item.options){const b=el('button',{type:'button',class:'option','aria-pressed':'false',onClick:()=>choose(option.id)},el('span',{class:'option-letter'},option.id.toUpperCase()),el('span',{class:'option-text'},option.text));buttons.push([option.id,b]);options.append(b);}
    const alternate=el('div',{class:'button-group'});for(const [v,label]of [['tie','Both fit equally'],['neither','Neither fits']]){const b=button(label,()=>choose(v),'small');buttons.push([v,b]);alternate.append(b);}
    cases.append(el('section',{class:'panel'},panelHeading('Which would you send with fewer changes?',item.brief,badge(isLive(item.provider)?'Blind comparison':'Rehearsal','muted')),el('div',{class:'panel-body'},contextPills(item.context),options,alternate,field('Optional evaluation note',notes),el('label',{class:'checkbox'},factual,el('span',{},'One or both drafts change facts or commitments.'))),el('div',{class:'panel-foot'},helper('Names are revealed after your judgment is recorded.'),save)));
  }
  const history=(state.evaluations||[]).filter(x=>x.status!=='unrated');
  const results=el('section',{class:'panel panel-pad'},el('h2',{},'Your observed results'),helper(`${summary.rated_real||0} rated AI comparisons · ${summary.calibrated_wins||0} calibrated wins · ${summary.baseline_wins||0} baseline wins · ${summary.ties||0} ties · ${summary.neither||0} neither`),helper(`${summary.factual_flags||0} factual concerns · ${summary.rated_offline||0} rated rehearsals excluded from AI results`),helper('These are your judgments on these tasks, not a universal accuracy percentage.'),history.slice(0,8).map(x=>el('article',{class:'preference'},contextPills(x.context),el('p',{},`${isLive(x.provider)?'AI':'Rehearsal'} · ${x.result} · A: ${x.mapping?.a} / B: ${x.mapping?.b}`),x.notes&&helper(x.notes))));
  return [pageHeading('Evaluation / fewer edits, better fit','Check whether it sounds more like you.','Judge comparable drafts blind, then inspect what helped.'),el('div',{class:'primary-column'},cases,setup,results)];
}

try { await refresh(); if (activeDocumentJob(ui.document.job)) scheduleDocumentPoll(0); }
catch (error) {
  app.replaceChildren(el('main', { class: 'boot-state', id: 'main' }, el('span', { class: 'brand-mark' }, 'v.'), el('h1', {}, 'Your workspace is offline.'), el('p', { class: 'helper', style: 'max-width: 360px; text-align: center; margin-bottom: 20px' }, error.message), button('Try again', () => location.reload(), 'primary', 'refresh')));
  app.removeAttribute('aria-busy');
}
