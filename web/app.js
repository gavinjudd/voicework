const app = document.querySelector('#app');
const toastRegion = document.querySelector('#toast-region');
const routes = { practice: 'Practice', samples: 'Samples', write: 'Write', evaluate: 'Check fit', profile: 'Voice profile' };
const ui = {
  route: routes[location.hash.slice(1)] ? location.hash.slice(1) : 'practice',
  filter: 'pending', context: { channel: 'work', audience: '', intent: '', stakes: '', relationship: '', scenario: '' },
  selections: new Map(), draft: null, brief: '', controls: { polish: 45, detail: 45, warmth: 50, assertiveness: 50, structure: 'auto' },
  scanResult: null, scanRunning: null, exportText: '', includeExamples: false,
};
let state;
let nextId = 0;
let toastTimer;
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
  catch { throw new Error('The local workspace is not responding. Keep the Voicework server running, then try again.'); }
  let data;
  try { data = await response.json(); }
  catch { throw new Error('The server returned an unreadable response. Please try again.'); }
  if (!response.ok) throw new Error(data.error || `Request failed (${response.status}).`);
  return data;
}
async function refresh(renderPage = true) {
  state = await api('/state');
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
function providerName(provider = state?.settings?.provider) { return provider === 'codex' ? 'Codex configured' : 'Offline rehearsal'; }
function providerPill(provider = state?.settings?.provider) {
  const enabled = provider === 'codex';
  return el('span', { class: 'status-pill', title: enabled ? 'Generation uses your configured Codex CLI provider.' : 'Deterministic local examples. No AI model is running.' }, el('span', { class: `status-dot ${enabled ? 'live' : ''}` }), providerName(provider));
}
function contextPills(context = {}) {
  return el('div', { class: 'context-pills' }, ['channel', 'audience', 'intent', 'relationship', 'stakes'].filter(key => context[key]).map(key => el('span', { class: 'context-pill', title: key }, context[key])));
}
function contextEditor(seed = ui.context, options = {}) {
  const values = { channel: 'work', audience: '', intent: '', stakes: '', relationship: '', scenario: '', ...seed };
  const controls = {};
  const contextId = uid('contexts');
  const channel = controls.channel = input({ value: values.channel, list: contextId, maxlength: 80, placeholder: 'work, personal, community…' });
  const audience = controls.audience = input({ value: values.audience, maxlength: 160, placeholder: 'A teammate, close friend…' });
  const wrap = el('div', { class: 'context-editor' }, el('div', { class: 'form-row' }, field('Context', channel), field('Who is this for?', audience, 'optional')), el('datalist', { id: contextId }, ['work', 'personal', 'community', 'public writing'].map(value => el('option', { value }))));
  const more = el('div', {}, el('div', { class: 'form-row' },
    field('What should it achieve?', controls.intent = input({ value: values.intent, maxlength: 200, placeholder: 'Ask for a decision, reconnect…' })),
    field('Relationship', controls.relationship = input({ value: values.relationship, maxlength: 160, placeholder: 'Peer, manager, close friend…' }))),
    field('What is at stake?', controls.stakes = input({ value: values.stakes, maxlength: 200, placeholder: 'Routine update, difficult conversation…' })));
  const scenario = field(options.scenarioLabel || 'Situation', controls.scenario = el('textarea', { value: values.scenario, rows: 2, maxlength: 2000, placeholder: 'The situation and facts your writing needs to fit.', style: 'min-height: 70px' }), 'optional');
  if (options.scenario) wrap.append(scenario);
  else more.append(scenario);
  wrap.append(details('Intent, relationship & more context', more, options.expanded));
  if (options.remember !== false) Object.entries(controls).forEach(([key, control]) => control.addEventListener('input', () => { ui.context[key] = control.value; }));
  return { node: wrap, get: () => Object.fromEntries(Object.entries(controls).map(([key, control]) => [key, control.value.trim()])) };
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
    el('div', { class: 'sidebar-bottom' }, el('div', { class: 'local-label' }, icon('lock'), 'Local by default'), el('p', { class: 'sidebar-note' }, state.settings.provider === 'codex' ? 'Samples stay in this workspace. Selected evidence is sent to Codex when you generate.' : 'Your samples and feedback stay in this workspace. You decide what becomes evidence.'), el('div', { class: 'sidebar-footer' }, el('span', {}, 'VOICEWORK / LOCAL'), el('span', {}, 'v0.1'))));
  const pages = { practice: renderPractice, samples: renderSamples, write: renderWrite, evaluate: renderEvaluate, profile: renderProfile };
  const main = el('main', { id: 'main', class: 'main fade-in', tabindex: '-1' }, pages[ui.route]());
  const workspace = el('div', { class: 'workspace' }, el('header', { class: 'topbar' }, el('div', { class: 'breadcrumb' }, el('span', {}, 'Personal workspace'), el('span', { class: 'slash' }, '/'), el('strong', {}, routes[ui.route])), providerPill()), main);
  app.replaceChildren(sidebar, workspace);
  app.removeAttribute('aria-busy');
  document.title = `${routes[ui.route]} · Voicework`;
}
function journeyPanel() {
  const profile = state.profile || {};
  const confirmed = state.samples.filter(sample => sample.status === 'confirmed').length;
  const feedbackCount = state.feedback.length;
  const xp = Number(profile.xp) || 0;
  return el('section', { class: 'panel panel-pad journey-panel', 'aria-label': 'Practice progress' },
    el('div', { class: 'journey-title' }, el('div', { class: 'section-title', style: 'margin-bottom: 19px' }, 'Your practice'), badge(`Level ${profile.level || 1}`, 'muted')),
    el('div', { class: 'journey-number' }, xp, el('small', {}, 'contribution XP')),
    helper(profile.next_milestone || 'Add your first sample to get started.'),
    el('div', { class: 'metric-grid' }, el('div', {}, el('div', { class: 'metric-value' }, confirmed), el('div', { class: 'metric-name' }, 'Confirmed samples')), el('div', {}, el('div', { class: 'metric-value' }, feedbackCount), el('div', { class: 'metric-name' }, 'Considered choices'))),
    el('p', { class: 'helper', style: 'margin-top: 17px' }, 'XP counts your contributions. It is not a score of how accurately we know your voice.'));
}
function stepsPanel() {
  const confirmed = state.samples.some(sample => sample.status === 'confirmed');
  const feedback = state.feedback.length > 0;
  const rows = [
    [confirmed, 'Start with your own words', 'Confirm a sample you actually wrote.'],
    [feedback, 'Make a few considered choices', 'Choose wording, explain why, shape the format.'],
    [false, 'Put your voice to work', 'Draft with context. Keep what feels like you.'],
  ];
  return el('section', { class: 'panel panel-pad' }, el('div', { class: 'section-title' }, 'A little at a time'), el('ol', { class: 'steps' }, rows.map(([done, title, text], index) => el('li', { class: `step ${done ? 'done' : ''}` }, el('span', { class: 'step-index' }, done ? icon('check', 12) : index + 1), el('div', {}, el('strong', {}, title), el('p', {}, text))))));
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
  const pending = state.samples.filter(sample => sample.status === 'pending');
  const feedbackIds = new Set(state.feedback.map(item => String(item.round_id)));
  const unanswered = state.rounds.filter(round => !feedbackIds.has(String(round.id)) && round.status !== 'completed');
  const currentRound = unanswered.at(-1);
  const column = el('div', { class: 'primary-column' });
  if (!confirmed.length && !pending.length && !currentRound) column.append(sampleForm());
  if (pending.length) column.append(el('section', { class: 'panel' }, panelHeading('A quick authorship check', 'Only writing you confirm becomes positive style evidence.', badge(`${pending.length} to review`, 'amber')), sampleCard(pending[0], { nested: true }), pending.length > 1 && el('div', { class: 'panel-foot' }, helper(`${pending.length - 1} more waiting in your inbox.`), button('Review all samples', () => navigate('samples'), 'small', 'arrow'))));
  if (currentRound) column.append(roundPanel(currentRound));
  else if (confirmed.length) column.append(roundSetup());
  else column.append(callout('Try a practice round first', 'Explore wording and presentation with a generic rehearsal. Your choices are preference feedback, never a sample of your authorship.', '', button('Try a round', () => { const section = roundSetup(); column.append(section); section.scrollIntoView({ behavior: 'smooth', block: 'start' }); section.querySelector('input')?.focus({ preventScroll: true }); }, 'small', 'arrow')));
  if (confirmed.length && !currentRound) column.append(el('div', { class: 'aside-note' }, el('h3', {}, 'Your professional voice and your personal voice can differ.'), el('p', {}, 'Choose a context before each round. A concise work update, a warm message to a friend, and a public post don’t need the same wording or shape.')));
  const aside = el('aside', { class: 'aside-column' }, journeyPanel(), stepsPanel(), el('div', { class: 'aside-note' }, el('h3', {}, 'Wording is only half of it.'), el('p', {}, 'Paragraph length, bullet points, opening lines, sign-offs, and emoji are part of your voice, too. Every round makes room for those preferences.')));
  return [pageHeading('Calibration / one choice at a time', 'Sound more like yourself.', 'Bring your writing. Notice what fits. Build a voice profile that respects the context.'), el('div', { class: 'layout' }, column, aside)];
}
function roundSetup() {
  const context = contextEditor(ui.context, { scenario: true });
  const submit = button(state.settings.provider === 'codex' ? 'Create a practice round' : 'Create a rehearsal round', null, 'primary', 'arrow'); submit.type = 'submit';
  const form = el('form', { class: 'panel' }, panelHeading('Set the scene', 'A useful choice starts with a real situation.', badge('Wording + presentation')), el('div', { class: 'panel-body' }, context.node), el('div', { class: 'panel-foot' }, helper(state.settings.provider === 'codex' ? 'Uses confirmed, non-held-out samples and your feedback.' : 'Offline examples are deterministic, not model-generated.'), submit));
  form.addEventListener('submit', event => { event.preventDefault(); busy(submit, async () => { await api('/rounds', 'POST', { context: context.get() }); await refresh(); }, 'Creating round…'); });
  return form;
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
  const rewrite = el('textarea', { rows: 3, placeholder: 'Write it your way, or make a small edit to the version you preferred.', value: initial.edited_text, maxlength: 10000, onInput: event => { initial.edited_text = event.target.value; } });
  const notes = el('textarea', { rows: 2, style: 'min-height: 70px', placeholder: 'e.g. “A is closer, but skip the opener and keep it to two short paragraphs.”', value: initial.notes, maxlength: 4000, onInput: event => { initial.notes = event.target.value; } });
  const presentation = el('div', { class: 'presentation-fields' }, Object.entries(presentationOptions).map(([key, [label, choices]]) => field(label, select(choices, initial.presentation[key] || '', { onChange: event => { initial.presentation[key] = event.target.value; } }))));
  const form = el('form', { class: 'panel' }, panelHeading('Which sounds more like you?', 'Choose the closer fit. There are no right answers.', badge(round.provider === 'codex' ? 'Codex round' : 'Heuristic rehearsal', round.provider === 'codex' ? '' : 'amber')),
    el('div', { class: 'round-context' }, contextPills(round.context), round.context?.scenario && el('p', { class: 'round-scenario' }, round.context.scenario)),
    el('div', { class: 'panel-body' }, options, el('div', { class: 'neither-row' }, neither, helper('A rejection is useful feedback, too.')),
      el('div', { class: 'feedback-fields' }, field('What made the difference?', notes, 'optional'), details('Make it sound exactly right', field('Your version', rewrite, 'feedback only'), Boolean(initial.edited_text)), details('How should it look in this context?', presentation, true))),
    el('div', { class: 'panel-foot' }, helper('Choices and rewrites are feedback, not authored samples.'), submit));
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
    const infer = button(state.settings.provider === 'codex' ? 'Suggest context with Codex' : 'Suggest context locally', () => busy(infer, async () => {
      const {inference} = await api(`/samples/${encodeURIComponent(sample.id)}/infer`, 'POST', {});
      const edit = contextEditor(inference.context, {remember: false, expanded: true, scenario: true});
      const apply = button('Apply reviewed context', () => busy(apply, async () => {
        await api(`/samples/${encodeURIComponent(sample.id)}`, 'PATCH', {context: edit.get()}); await refresh(); toast('Context updated. Authorship status was preserved.');
      }), 'small primary');
      preview.replaceChildren(el('section', {class:'panel panel-pad', style:'margin-top:12px'}, el('h3', {}, 'A provisional reconstruction'),
        helper(inference.provider === 'codex' ? 'Model suggestions. Check them against what actually happened.' : 'Local clues only; no AI model was used.'),
        el('ul', {}, (inference.rationale || []).map(x => el('li', {}, x))),
        details('Uncertainties and authorship clues', el('div', {}, [...(inference.uncertainties || []), ...(inference.authorship_signals || [])].map(x => helper(x)))), edit.node, apply));
    }, 'Analyzing…'), 'small', 'spark');
    card.append(el('div', {style:'margin-top:12px'}, infer, helper(state.settings.provider === 'codex' ? 'This action sends this sample to Codex for analysis. It does not confirm authorship.' : 'Infer a starting context from the passage and source, then correct it.'), preview));
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
  const filters = [['pending', 'To review'], ['confirmed', 'Confirmed'], ['assisted', 'Assisted'], ['rejected', 'Not mine'], ['all', 'All']];
  const filtered = state.samples.filter(sample => ui.filter === 'all' || sample.status === ui.filter);
  const filterBar = el('div', { class: 'filters', role: 'group', 'aria-label': 'Filter samples' }, filters.map(([key, label]) => el('button', { class: `filter ${ui.filter === key ? 'active' : ''}`, 'aria-pressed': key === ui.filter ? 'true' : 'false', onClick: () => { ui.filter = key; render(); } }, label, el('span', { class: 'filter-count' }, key === 'all' ? state.samples.length : state.samples.filter(sample => sample.status === key).length))));
  const list = el('div', { class: 'review-list' }, filtered.length ? filtered.slice().reverse().map(sample => sampleCard(sample)) : empty(ui.filter === 'pending' ? 'Your inbox is clear.' : 'No samples in this view yet.', ui.filter === 'pending' ? 'Paste your own writing, scan a folder you choose, or import a browser capture. Nothing becomes evidence until you confirm it.' : 'Confirmed authorship and AI-assisted writing are kept separate so your profile has a clear foundation.'));
  const paste = el('section', { class: 'panel panel-pad', id: 'paste-sample' }, el('h2', {}, 'Add a writing sample'), helper('Paste a message, email, or passage. Each sample starts in your review inbox.'), el('div', { style: 'margin-top: 18px' }, sampleForm({ compact: true })));
  const sidebar = el('aside', { class: 'aside-column' }, discoveryPanel(), importPanel(), el('div', { class: 'aside-note' }, el('h3', {}, 'Good evidence has a clear source.'), el('p', {}, 'Saved paths and page URLs preserve provenance. “I wrote this” confirms authorship; it is never inferred from a file’s presence. Mixed or edited AI output belongs in Assisted.')));
  const add = button('Paste a sample', () => { paste.scrollIntoView({ behavior: 'smooth', block: 'start' }); paste.querySelector('textarea')?.focus({ preventScroll: true }); }, '', 'plus');
  return [pageHeading('Evidence / your words, with context', 'Your writing library.', 'A considered collection of things you actually wrote. Review first, learn second.', add), el('div', { class: 'layout' }, el('div', { class: 'primary-column' }, el('div', {}, filterBar, list), paste), sidebar)];
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
function renderWrite() {
  const context = contextEditor(ui.context, { scenario: false });
  const brief = el('textarea', { class: 'write-brief', placeholder: 'What do you want to say? Include the facts, the outcome you want, and anything that must stay exactly as written.', required: true, maxlength: 15000, value: ui.brief, onInput: event => { ui.brief = event.target.value; } });
  const controls = el('div', { class: 'control-grid' });
  for (const [key, title, left, right] of [['polish', 'Polish', 'Light touch', 'Substantial rewrite'], ['detail', 'Detail', 'Essentials', 'Full context'], ['warmth', 'Warmth', 'Matter-of-fact', 'More personal'], ['assertiveness', 'Assertiveness', 'Gentle', 'Direct']]) {
    const output = el('output', { class: 'control-value' }, ui.controls[key]);
    const range = input({ type: 'range', min: 0, max: 100, step: 1, value: ui.controls[key], 'aria-label': title, onInput: event => { ui.controls[key] = Number(event.target.value); output.textContent = event.target.value; } });
    range.id = uid('range'); output.setAttribute('for', range.id);
    controls.append(el('div', { class: 'control-row' }, el('label', { class: 'control-label', for: range.id }, title, output), range, el('div', { class: 'range-labels' }, el('span', {}, left), el('span', {}, right))));
  }
  const structureButtons = [];
  const structure = el('div', { class: 'segmented', role: 'group', 'aria-label': 'Draft structure' }, [['auto', 'Use my preferences'], ['prose', 'Prose'], ['bullets', 'Bullets']].map(([value, label]) => {
    const control = el('button', { type: 'button', class: `segment ${ui.controls.structure === value ? 'active' : ''}`, 'aria-pressed': ui.controls.structure === value ? 'true' : 'false', onClick: () => { ui.controls.structure = value; structureButtons.forEach(([key, button]) => { button.classList.toggle('active', key === value); button.setAttribute('aria-pressed', key === value ? 'true' : 'false'); }); } }, label);
    structureButtons.push([value, control]); return control;
  }));
  const submit = button(state.settings.provider === 'codex' ? 'Draft in my voice' : 'Preview a rehearsal draft', null, 'primary', 'write'); submit.type = 'submit';
  const form = el('form', { class: 'panel' }, panelHeading('What are you writing?', 'Your facts. Your context. Your level of change.'), el('div', { class: 'panel-body' }, context.node, el('div', { style: 'margin-top: 20px' }, field('Your brief or rough draft', brief)), el('div', { class: 'feedback-fields' }, el('h3', {}, 'Make it fit the moment'), controls, el('div', { class: 'field' }, el('span', { class: 'field-label' }, 'Structure'), structure))), el('div', { class: 'panel-foot' }, helper('Review the result before you use it.'), submit));
  form.addEventListener('submit', event => { event.preventDefault(); busy(submit, async () => {
    if (!brief.value.trim()) throw new Error('Add a brief or rough draft first.');
    ui.draft = await api('/draft', 'POST', { brief: brief.value.trim(), context: context.get(), controls: ui.controls }); render(); toast('Draft ready. Review the wording and evidence.');
  }, 'Writing…'); });
  const output = el('section', { class: 'panel draft-panel', 'aria-label': 'Draft result', 'aria-live': 'polite' }, panelHeading('Your draft', ui.draft ? 'A starting point for your review.' : 'A blank page, with a little backup.', ui.draft && badge(ui.draft.provider === 'codex' ? 'Codex' : 'Offline heuristic', ui.draft.provider === 'codex' ? '' : 'amber')));
  if (ui.draft) {
    const evidenceSamples = (ui.draft.evidence_ids || []).map(id => state.samples.find(sample => String(sample.id) === String(id))).filter(Boolean);
    output.append(el('div', { class: 'draft-text' }, ui.draft.text), el('div', { class: 'panel-foot' }, helper('Generated drafts are not added as authored evidence.'), button('Copy draft', async () => { try { await navigator.clipboard.writeText(ui.draft.text); toast('Draft copied.'); } catch { toast('Clipboard access was denied. Select and copy the draft text directly.', true); } }, 'small', 'copy')),
      el('div', { class: 'evidence' }, el('div', { class: 'section-title' }, 'Evidence used'), evidenceSamples.length ? el('ul', { class: 'evidence-list' }, evidenceSamples.map(sample => el('li', {}, `${sample.title || 'Untitled sample'} · ${sample.context?.channel || 'work'}`))) : helper('No confirmed source samples were used.'), ui.draft.warnings?.length && el('div', { style: 'margin-top: 12px' }, ui.draft.warnings.map(warning => helper(warning)))));
  } else output.append(el('div', { class: 'draft-empty' }, icon('feather'), el('h3', {}, 'Make the first draft yours.'), el('p', {}, 'Start with the facts on the left. Your confirmed writing and context-specific preferences will guide the result.')),
    el('div', { class: 'evidence' }, el('div', { class: 'section-title' }, 'A clear foundation'), helper(`${plural(state.samples.filter(sample => sample.status === 'confirmed' && !sample.held_out).length, 'eligible sample')} · ${plural(state.feedback.length, 'saved preference', 'saved preferences')}`)));
  return [pageHeading('Writing / voice meets purpose', 'Same you. Different moments.', 'Choose how much help you want, from a light polish to a more considered rewrite.'), state.settings.provider !== 'codex' && el('div', { style: 'margin-bottom: 21px' }, callout('You’re in offline rehearsal', 'This mode uses deterministic text rules. It does not run a language model or demonstrate learned writing quality. Connect the Codex provider to generate adaptive drafts.', 'warning')), el('div', { class: 'write-layout' }, form, output)];
}
function renderProfile() {
  const profile = state.profile || {};
  const confirmed = state.samples.filter(sample => sample.status === 'confirmed');
  const eligible = confirmed.filter(sample => !sample.held_out);
  const contexts = profile.contexts || [];
  const preferences = profile.preferences || [];
  const patterns = profile.patterns || [];
  const stats = el('div', { class: 'panel stats-strip' }, [[eligible.length, 'Samples informing your voice'], [state.feedback.length, 'Saved choices'], [contexts.length, 'Contexts with evidence'], [confirmed.length - eligible.length, 'Samples held out']].map(([value, label]) => el('div', { class: 'stat-cell' }, el('strong', {}, value), el('span', {}, label))));
  const patternBody = patterns.length ? el('div', { class: 'patterns' }, patterns.map(pattern => el('div', { class: 'pattern' }, el('div', {}, el('strong', {}, pattern.name), el('p', {}, asText(pattern.value))), badge(`${pattern.evidence_count || 0} sources`, 'muted')))) : empty('Your profile starts with evidence.', 'Confirm a few things you wrote to see observed writing patterns here.', button('Add your writing', () => navigate('samples'), 'small', 'plus'), 'profile');
  const patternPanel = el('section', { class: 'panel' }, panelHeading('Observed writing patterns', profile.patterns_scope || 'Descriptive signals from confirmed, non-held-out writing.'), el('div', { class: 'panel-body' }, patternBody));
  const preferenceBody = preferences.length ? preferences.slice().reverse().map(preference => el('article', { class: 'preference' }, contextPills(typeof preference.context === 'object' ? preference.context : { channel: preference.context || 'work' }), preference.notes && el('p', {}, preference.notes), Object.keys(preference.presentation || {}).length > 0 && el('dl', {}, Object.entries(preference.presentation).map(([key, value]) => el('div', {}, el('dt', {}, (presentationOptions[key]?.[0] || key) + ':'), el('dd', {}, asText(value))))), preference.edited_text && details('Your feedback rewrite', el('p', { style: 'white-space: pre-wrap' }, preference.edited_text)), !preference.notes && !Object.keys(preference.presentation || {}).length && !preference.edited_text && el('p', {}, preference.choice === 'neither' ? 'Rejected both alternatives for this context.' : `Preferred option ${asText(preference.choice).toUpperCase()} for this context.`))) : empty('Tell it what feels right.', 'A preferred phrase, an unwanted sign-off, or a choice of bullets over prose all give your profile useful direction.', button('Start a practice round', () => navigate('practice'), 'small', 'arrow'), 'practice');
  const preferencePanel = el('section', { class: 'panel' }, panelHeading('Preferences, in context', 'Your choices and reasons, kept separate from authored evidence.'), el('div', { class: 'panel-body' }, preferenceBody));
  const coverage = el('section', { class: 'panel panel-pad' }, el('div', { class: 'section-title' }, 'Context coverage'), contexts.length ? contexts.map(context => el('div', { class: 'coverage-row' }, el('strong', {}, context.channel || 'work'), el('span', {}, plural(context.count || 0, 'sample')))) : helper('No confirmed context coverage yet. Work, personal, community, and any labels you create stay distinct.'));
  const provider = el('section', { class: 'panel panel-pad' }, el('div', { class: 'section-title' }, 'Generation provider'), providerPill(), el('p', { class: 'export-note' }, state.settings.provider === 'codex' ? `Using your local Codex CLI${state.settings.model ? ` with ${state.settings.model}` : ''}. Selected evidence and your brief are sent to the configured model when you generate.` : 'Offline mode rehearses the workflow with deterministic rules. No language model is running. The README explains how to start with the Codex provider.'), helper(state.capabilities?.codex_available ? 'Codex CLI detected. Authentication is verified only when generation succeeds.' : 'Codex CLI availability has not been confirmed by the server.'));
  return [pageHeading('Voice profile / evidence, not a score', 'A voice that keeps its context.', 'See what you’ve contributed, what you’ve explicitly preferred, and where there is more to learn.'), stats, el('div', { class: 'layout' }, el('div', { class: 'primary-column' }, patternPanel, preferencePanel, priorPanel()), el('aside', { class: 'aside-column' }, exportPanel(), coverage, provider, el('div', { class: 'aside-note' }, el('h3', {}, 'A profile, not a personality verdict.'), el('p', {}, 'This is a portable collection of evidence and preferences. Coverage grows with your contributions; a completion or XP count never establishes writing accuracy.'))))];
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
  const summary=state.evaluation_summary || {};
  const context=contextEditor(ui.context,{scenario:true});
  const brief=el('textarea',{required:true,maxlength:15000,rows:4,placeholder:'Give both versions the same facts and desired outcome. Do not paste held-out answer text.', 'aria-label':'Comparison brief'});
  const submit=button(state.settings.provider==='codex'?'Create a blind comparison':'Rehearse a blind comparison',null,'primary');submit.type='submit';
  const setup=el('form',{class:'panel'},panelHeading('Does calibration actually help?','Compare a baseline and your calibrated voice before seeing which is which.'),el('div',{class:'panel-body'},context.node,field('Same brief for both drafts',brief),helper('Both versions use the same model and controls. Evaluation votes stay separate from training.')),el('div',{class:'panel-foot'},helper(state.settings.provider==='codex'?'Generates two drafts through Codex.':'Offline rehearsals do not count as evidence of AI quality.'),submit));
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
    cases.append(el('section',{class:'panel'},panelHeading('Which would you send with fewer changes?',item.brief,badge(item.provider==='codex'?'Blind comparison':'Rehearsal','muted')),el('div',{class:'panel-body'},contextPills(item.context),options,alternate,field('Optional evaluation note',notes),el('label',{class:'checkbox'},factual,el('span',{},'One or both drafts change facts or commitments.'))),el('div',{class:'panel-foot'},helper('Names are revealed after your judgment is recorded.'),save)));
  }
  const history=(state.evaluations||[]).filter(x=>x.status!=='unrated');
  const results=el('section',{class:'panel panel-pad'},el('h2',{},'Your observed results'),helper(`${summary.rated_real||0} rated AI comparisons · ${summary.calibrated_wins||0} calibrated wins · ${summary.baseline_wins||0} baseline wins · ${summary.ties||0} ties · ${summary.neither||0} neither`),helper(`${summary.factual_flags||0} factual concerns · ${summary.rated_offline||0} rated rehearsals excluded from AI results`),helper('These are your judgments on these tasks, not a universal accuracy percentage.'),history.slice(0,8).map(x=>el('article',{class:'preference'},contextPills(x.context),el('p',{},`${x.provider==='codex'?'AI':'Rehearsal'} · ${x.result} · A: ${x.mapping?.a} / B: ${x.mapping?.b}`),x.notes&&helper(x.notes))));
  return [pageHeading('Evaluation / fewer edits, better fit','Check whether it sounds more like you.','Judge comparable drafts blind, then inspect what helped.'),el('div',{class:'primary-column'},cases,setup,results)];
}

try { await refresh(); }
catch (error) {
  app.replaceChildren(el('main', { class: 'boot-state', id: 'main' }, el('span', { class: 'brand-mark' }, 'v.'), el('h1', {}, 'Your workspace is offline.'), el('p', { class: 'helper', style: 'max-width: 360px; text-align: center; margin-bottom: 20px' }, error.message), button('Try again', () => location.reload(), 'primary', 'refresh')));
  app.removeAttribute('aria-busy');
}
