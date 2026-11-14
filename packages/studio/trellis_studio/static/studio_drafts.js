/* S1: my drafts, new from a template or a copy, the spec and Report forms, validate and preview.
 * Every call runs on the server as you, through pyegeria.authoring. Studio never decides validity itself. */
import { api } from '/static/studio_auth.js';
import { h, clear, readJson } from '/static/dom.js';
import { makeSplit } from '/static/panes.js';
import { renderValidation, renderPreview, renderFailure } from '/static/studio_results.js';
import '/static/studio_sheet.js';

const state = { draft: null, savedAs: null, dirty: false, templates: [] };
const panes = {};

const draftName = (d) => (d.kind === 'spec' ? d.label : d.kind === 'report' ? d.display_name : d.name);
const csv = (s) => String(s || '').split(',').map((x) => x.trim()).filter(Boolean);
const json = (body) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });

function setStatus(text, cls = 'st-muted') {
  clear(panes.status).append(h('span', { class: cls }, text));
}

function touch() { state.dirty = true; setStatus('Changed, not saved.', 'st-warn'); }

// ---- drafts list -------------------------------------------------------------------------------------------

async function loadList() {
  const host = clear(panes.list);
  host.append(h('p', { class: 'st-muted' }, 'Reading your drafts as you…'));
  const res = await readJson(await api('/api/drafts'));
  clear(host);
  if (!res.ok) {
    host.append(h('p', { class: 'st-error' }, "Your drafts couldn't be read."), h('p', {}, res.detail),
                h('p', { class: 'st-muted' }, 'This is not "no drafts": nothing was read.'));
    return;
  }
  const { drafts, notes, complete } = res.data;
  const count = complete ? `${drafts.length} draft${drafts.length === 1 ? '' : 's'}`
                         : `at least ${drafts.length} draft${drafts.length === 1 ? '' : 's'}`;
  host.append(h('p', {}, h('strong', {}, 'My drafts'), ' ', h('span', { class: 'st-muted' }, `(${count})`)));
  for (const n of notes || []) host.append(h('p', { class: 'st-warn' }, n));
  if (!drafts.length && complete) host.append(h('p', { class: 'st-muted' }, 'You have no drafts.'));
  for (const kind of ['spec', 'report', 'sheet']) {
    const mine = drafts.filter((d) => d.kind === kind);
    if (!mine.length) continue;
    host.append(h('h4', {}, { spec: 'Report specs', report: 'Reports', sheet: 'Sheets' }[kind]));
    host.append(h('ul', { class: 'st-list' }, mine.map((d) => h('li', {},
      h('button', { class: 'st-link', onclick: () => openDraft(kind, d.name) }, d.name || d.qualified_name),
      d.updated ? h('span', { class: 'st-muted' }, ` ${String(d.updated).slice(0, 10)}`) : null))));
  }
  host.append(newDraftForm());
}

function newDraftForm() {
  const name = h('input', { placeholder: 'New name', required: true });
  const tmpl = h('select', {}, state.templates.map((t) => h('option', { value: t.name }, `${t.name} (${t.kind})`)));
  const pKind = h('select', {}, ['spec', 'report', 'sheet'].map((k) => h('option', { value: k }, k)));
  const pLabel = h('input', { placeholder: 'Copy of (its name)' });
  const err = h('p', { class: 'st-error', hidden: true });
  const make = async (body) => {
    err.hidden = true;
    const res = await readJson(await api('/api/drafts/new', json(body)));
    if (!res.ok) { err.textContent = res.detail; err.hidden = false; return; }
    showDraft(res.data.draft, null);
    setStatus('New draft, not saved yet.', 'st-warn');
  };
  return h('div', { class: 'st-block st-new' },
    h('h4', {}, 'New draft'),
    h('label', {}, 'Name ', name),
    h('div', {}, h('label', {}, 'From template ', tmpl), ' ',
      h('button', { class: 'st-btn', onclick: () => make({ new_label: name.value.trim(), template: tmpl.value }) }, 'New')),
    h('div', {}, h('label', {}, 'Or copy a ', pKind), ' ', pLabel, ' ',
      h('button', { class: 'st-btn st-btn-quiet', onclick: () => make({ new_label: name.value.trim(),
        prototype: { kind: pKind.value, label: pLabel.value.trim() } }) }, 'Copy')),
    err);
}

async function openDraft(kind, name) {
  if (state.dirty && !confirm('Discard the unsaved changes?')) return;
  const res = await readJson(await api(`/api/drafts/${kind}/${encodeURIComponent(name)}`));
  if (!res.ok) { renderFailure(panes.checks, `Couldn't open ${name}.`, res.detail); return; }
  showDraft(res.data.draft, name);
  setStatus('Opened from your drafts in Egeria.');
}

// ---- forms -------------------------------------------------------------------------------------------------

function field(label, value, onInput, attrs = {}) {
  const input = attrs.multiline ? h('textarea', { rows: attrs.rows || 3 }) : h('input', {});
  input.value = value ?? '';
  input.addEventListener('input', () => { onInput(input.value); touch(); });
  return h('label', { class: 'st-field' }, label, input);
}

function specForm(d) {
  const fs = d.format_set;
  fs.action = fs.action || { function: '', required_params: [], optional_params: [], spec_params: {} };
  const a = fs.action;
  const box = h('div', {});
  box.append(
    field('Label (ReportSpec::<label>)', d.label, (v) => { d.label = v; d.qualified_name = null; }),
    field('Heading', fs.heading, (v) => { fs.heading = v; }),
    field('Description', fs.description, (v) => { fs.description = v; }, { multiline: true }),
    field('Target type', fs.target_type, (v) => { fs.target_type = v || null; }),
    field('Family', fs.family, (v) => { fs.family = v || null; }),
    h('h4', {}, 'Find method'),
    field('Function (e.g. ClassificationExplorer.find_elements_by_property_value)', a.function, (v) => { a.function = v; }),
    field('Required parameters (comma-separated)', (a.required_params || []).join(', '), (v) => { a.required_params = csv(v); }),
    field('Optional parameters (comma-separated)', (a.optional_params || []).join(', '), (v) => { a.optional_params = csv(v); }),
    jsonField('Spec parameters (JSON)', a.spec_params || {}, (v) => { a.spec_params = v; }),
  );
  (fs.formats || []).forEach((f, i) => box.append(formatEditor(fs, f, i)));
  box.append(h('button', { class: 'st-btn st-btn-quiet', onclick: () => {
    fs.formats.push({ types: ['ALL'], attributes: [] }); touch(); renderForm(); } }, 'Add a format'));
  // question_spec (question links) is phase 2: kept as it is, not shown.
  return box;
}

function formatEditor(fs, f, i) {
  const rows = h('tbody', {});
  const redraw = () => {
    clear(rows);
    (f.attributes || []).forEach((c, j) => rows.append(h('tr', {},
      ['name', 'key'].map((k) => h('td', {}, cell(c, k))),
      h('td', {}, select(c, 'render', ['', 'text', 'join', 'bullets', 'kv', 'subtable', 'count', 'link'])),
      h('td', {}, cell(c, 'max_items', true)), h('td', {}, cell(c, 'max_lines', true)),
      h('td', {}, select(c, 'label', ['', 'display_name', 'qualified_name'])),
      h('td', {}, h('button', { class: 'st-btn st-btn-quiet', title: 'Move up', onclick: () => { move(j, -1); } }, '↑'),
        h('button', { class: 'st-btn st-btn-quiet', title: 'Remove', onclick: () => { f.attributes.splice(j, 1); touch(); redraw(); } }, '✕')))));
  };
  const move = (j, dir) => { const k = j + dir; if (k < 0) return; const a = f.attributes; [a[j], a[k]] = [a[k], a[j]]; touch(); redraw(); };
  redraw();
  return h('div', { class: 'st-block' },
    h('h4', {}, `Format ${i + 1}`),
    field('Output types (comma-separated, e.g. TABLE, DICT, ALL)', (f.types || []).join(', '), (v) => { f.types = csv(v); }),
    h('table', { class: 'st-cols' },
      h('thead', {}, h('tr', {}, ['Column', 'Key', 'Render', 'Max items', 'Max lines', 'Label', ''].map((t) => h('th', {}, t)))),
      rows),
    h('button', { class: 'st-btn st-btn-quiet', onclick: () => { (f.attributes = f.attributes || []).push({ name: '', key: '' }); touch(); redraw(); } }, 'Add a column'),
    (fs.formats.length > 1) ? h('button', { class: 'st-btn st-btn-quiet', onclick: () => { fs.formats.splice(i, 1); touch(); renderForm(); } }, 'Remove this format') : null);
}

function cell(obj, key, numeric = false) {
  const input = h('input', { value: obj[key] ?? '', size: numeric ? 4 : 14 });
  input.addEventListener('input', () => {
    const v = input.value.trim();
    obj[key] = numeric ? (v === '' ? null : Number(v)) : v;
    touch();
  });
  return input;
}

function select(obj, key, options) {
  const s = h('select', {}, options.map((o) => h('option', { value: o }, o || '—')));
  s.value = obj[key] || '';
  s.addEventListener('change', () => { obj[key] = s.value || null; touch(); });
  return s;
}

function jsonField(label, value, onValue) {
  const ta = h('textarea', { rows: 3, class: 'st-mono' });
  ta.value = JSON.stringify(value, null, 1);
  const err = h('span', { class: 'st-error', hidden: true }, 'Not JSON yet: not applied.');
  ta.addEventListener('input', () => {
    try { onValue(JSON.parse(ta.value || '{}')); err.hidden = true; touch(); } catch (e) { err.hidden = false; }
  });
  return h('label', { class: 'st-field' }, label, ta, err);
}

function paramRows(obj, title) {
  const body = h('tbody', {});
  const redraw = () => {
    clear(body);
    for (const [k, v] of Object.entries(obj)) {
      const val = h('input', { value: typeof v === 'string' ? v : JSON.stringify(v) });
      val.addEventListener('input', () => {
        let x = val.value; try { x = JSON.parse(val.value); } catch (e) { /* a plain string */ }
        obj[k] = x; touch();
      });
      body.append(h('tr', {}, h('td', {}, k), h('td', {}, val),
        h('td', {}, h('button', { class: 'st-btn st-btn-quiet', onclick: () => { delete obj[k]; touch(); redraw(); } }, '✕'))));
    }
  };
  redraw();
  const key = h('input', { placeholder: 'parameter name (snake_case)' });
  return h('div', { class: 'st-block' }, h('h4', {}, title),
    h('table', { class: 'st-cols' }, h('thead', {}, h('tr', {}, h('th', {}, 'Name'), h('th', {}, 'Value'), h('th', {}, ''))), body),
    h('div', {}, key, ' ', h('button', { class: 'st-btn st-btn-quiet', onclick: () => {
      const k = key.value.trim(); if (!k || k in obj) return; obj[k] = ''; key.value = ''; touch(); redraw(); } }, 'Add')));
}

function reportForm(d) {
  d.parameters = d.parameters || {};
  d.analytic_params = d.analytic_params || {};
  return h('div', {},
    field('Display name', d.display_name, (v) => { d.display_name = v; }),
    field('Description', d.description, (v) => { d.description = v || null; }, { multiline: true }),
    field('Report spec it runs (label)', d.spec, (v) => { d.spec = v; }),
    field('Output format (TABLE, DICT, ...)', d.output_format, (v) => { d.output_format = v; }),
    field('Pinned spec version (blank follows the latest, D14)', d.pinned_version ?? '', (v) => {
      d.pinned_version = v.trim() === '' ? null : Number(v); }),
    paramRows(d.parameters, 'Parameters'),
    h('p', { class: 'st-muted' }, 'Each parameter is written both ways (D7): typed in reportParameters and as its camelCase copy. You edit one list; pyegeria writes both.'),
    paramRows(d.analytic_params, 'Analytic parameters (for an analytic routine)'));
}

function rawEditor(d) {
  const ta = h('textarea', { rows: 18, class: 'st-mono st-raw' });
  ta.value = JSON.stringify(d, null, 2);
  const err = h('p', { class: 'st-error', hidden: true });
  return h('details', { class: 'st-block' }, h('summary', {}, 'The whole draft as JSON'), ta, err,
    h('button', { class: 'st-btn st-btn-quiet', onclick: () => {
      try { state.draft = JSON.parse(ta.value); err.hidden = true; touch(); renderForm(); }
      catch (e) { err.textContent = `Not JSON: ${e.message}`; err.hidden = false; } } }, 'Use this JSON'));
}

function renderForm() {
  const host = clear(panes.form);
  const d = state.draft;
  if (!d) { host.append(h('p', { class: 'st-muted' }, 'Open a draft, or make a new one.')); return; }
  host.append(h('h3', {}, `${{ spec: 'Report spec', report: 'Report', sheet: 'Sheet' }[d.kind]}: ${draftName(d) || '(unnamed)'}`));
  if (d.derived_from) host.append(h('p', { class: 'st-muted' }, `Made from ${d.derived_from}.`));
  for (const n of d.notes || []) host.append(h('p', { class: 'st-warn' }, n));
  host.append(panes.status);
  host.append(actions());
  if (d.kind === 'spec') host.append(specForm(d));
  else if (d.kind === 'report') host.append(reportForm(d));
  else if (window.StudioSheetForm) host.append(window.StudioSheetForm(d, { touch, renderForm }));
  host.append(rawEditor(d));
}

/** The draft as sent: the form's own "_" keys dropped. */
function outgoing() {
  return JSON.parse(JSON.stringify(state.draft, (k, v) => (k.startsWith('_') ? undefined : v)));
}

function actions() {
  const drEgeria = h('input', { type: 'checkbox' });
  const runAll = h('input', { type: 'checkbox' });
  const rows = h('input', { type: 'number', min: 1, max: 200, value: 25, size: 4 });
  return h('div', { class: 'st-actions' },
    h('button', { class: 'st-btn', onclick: saveDraft }, 'Save draft'),
    h('button', { class: 'st-btn st-btn-quiet', onclick: () => validateDraft(drEgeria.checked) }, 'Validate'),
    h('label', { class: 'st-muted' }, drEgeria, ' with Dr.Egeria'),
    h('button', { class: 'st-btn st-btn-quiet', onclick: () => previewDraft(Number(rows.value) || 25, runAll.checked) }, 'Preview'),
    h('label', { class: 'st-muted' }, ' rows ', rows),
    state.draft.kind === 'sheet' ? h('label', { class: 'st-muted' }, runAll, ' run every placement') : null,
    state.savedAs ? h('button', { class: 'st-btn st-btn-quiet', onclick: deleteDraft }, 'Delete draft') : null);
}

function showDraft(draft, savedAs) {
  state.draft = draft; state.savedAs = savedAs; state.dirty = false;
  clear(panes.checks); clear(panes.preview);
  renderForm();
  document.dispatchEvent(new CustomEvent('studio:draft-open', { detail: { draft } }));
}

async function saveDraft() {
  setStatus('Saving to Egeria as you…');
  const res = await readJson(await api('/api/drafts', { ...json({ draft: outgoing() }), method: 'PUT' }));
  if (!res.ok) { setStatus(`Not saved: ${res.detail}`, 'st-error'); return; }
  state.savedAs = draftName(state.draft); state.dirty = false;
  setStatus(res.data.words, 'st-ok');
  renderForm(); setStatus(res.data.words, 'st-ok');
  loadList();
}

async function deleteDraft() {
  const d = state.draft;
  if (!confirm(`Delete your draft ${state.savedAs} from Egeria? This can't be undone.`)) return;
  const res = await readJson(await api(`/api/drafts/${d.kind}/${encodeURIComponent(state.savedAs)}`, { method: 'DELETE' }));
  if (!res.ok) { setStatus(`Not deleted: ${res.detail}`, 'st-error'); return; }
  state.draft = null; state.savedAs = null; state.dirty = false;
  renderForm(); loadList();
}

async function validateDraft(drEgeria) {
  clear(panes.checks).append(h('p', { class: 'st-muted' }, 'Validating as you…'));
  const res = await readJson(await api('/api/validate', json({ draft: outgoing(), dr_egeria: drEgeria })));
  if (!res.ok) return renderFailure(panes.checks, 'Validation could not run.', res.detail);
  renderValidation(panes.checks, res.data);
}

async function previewDraft(maxRows, runPlacements = false) {
  clear(panes.preview).append(h('p', { class: 'st-muted' }, 'Running a sample as you…'));
  const res = await readJson(await api('/api/preview', json({ draft: outgoing(), max_rows: maxRows,
                                                              run_placements: runPlacements })));
  if (!res.ok) return renderFailure(panes.preview, 'Preview could not run.', res.detail);
  renderPreview(panes.preview, res.data);
}

// ---- mount -------------------------------------------------------------------------------------------------

export async function mountDrafts(main) {
  panes.list = h('section', { 'aria-label': 'My drafts' });
  panes.form = h('section', { 'aria-label': 'Draft' });
  panes.status = h('div', { class: 'st-status' });
  panes.checks = h('div', { 'aria-label': 'Validation' });
  panes.preview = h('div', { 'aria-label': 'Preview' });
  const right = h('section', { 'aria-label': 'Checks and preview' },
    h('h4', {}, 'Validation'), panes.checks, h('h4', {}, 'Preview'), panes.preview);
  clear(main).append(makeSplit('drafts', [{ el: panes.list, width: 260 }, { el: panes.form, width: 560 }, { el: right }]));
  main.setAttribute('data-studio-app', 'drafts');
  const t = await readJson(await api('/api/templates'));
  state.templates = t.ok ? t.data.templates : [];
  renderForm();
  await loadList();
}

export const studioDrafts = { showDraft, state, loadList, openDraft };
