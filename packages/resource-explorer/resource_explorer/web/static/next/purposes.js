/* The investigation-purposes list, for every picker that offers it.
 *
 * Decision (project owner, 2026-10-13): Egeria holds the list; RE serves it from Egeria, or from a cache it
 * labels with its age, or from its shipped seed (GET /api/investigations/purposes says which, in `words`).
 * And "warn only - suggestive everywhere": a purpose may be picked from the list OR typed. A value outside
 * the list is never an error. It carries a small cue and the words "not in the list".
 *
 * Used by the investigation create/edit forms (stages/investigation.js) and Enrichment's Intended use
 * (stages/enrichment.js, also drawn by the Context tab).
 */
import { getInvestigationPurposes } from '/static/re-api.js';

const escHtml = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({
  '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
}[c]));

let _vocab = null;
let _loading = null;

/** The list as last loaded, or null before the first load finishes. */
export function purposesNow() { return _vocab; }

/** For tests and for a caller that already holds the answer. */
export function setPurposes(vocab) { _vocab = vocab ? normalise(vocab) : null; }

function normalise(v) {
  const values = Array.isArray(v?.values) && v.values.length
    ? v.values
    : (v?.purposes || []).map((name) => ({ name, description: '' }));
  return { ...v, values, purposes: values.map((x) => x.name) };
}

/** Loads once per page. A failed read is remembered as an empty list WITH its words, so a picker still
 *  offers free text and says why the list is missing; it never retries in a loop. */
export function loadPurposes({ refresh = false } = {}) {
  if (_vocab && !refresh) return Promise.resolve(_vocab);
  if (!_loading) {
    _loading = getInvestigationPurposes()
      .then(normalise)
      .catch((err) => normalise({ purposes: [], source: 'none', words: `the purposes list could not be read: ${err.message}` }))
      .then((v) => { _vocab = v; _loading = null; return v; });
  }
  return _loading;
}

const fold = (s) => String(s || '').trim().split(/\s+/).join(' ').toLowerCase();

export function isListed(text, names) {
  const f = fold(text);
  return !!f && (names || []).some((n) => fold(n) === f);
}

/** The cue: a mark on the element plus a short word, never a sentence or an error. */
export function notInListCueHtml() {
  return `<span data-purpose-cue="not-in-list" class="whitespace-nowrap text-provenance text-ink-muted"
    title="Not in the purposes list. Kept as written: the list is suggestive, and free text is kept so new purposes can be found in it.">◌ not in the list</span>`;
}

/** One purpose as shown: the word, and the cue when it is not in the list. With no list to compare against
 *  (not loaded, or unreadable) no cue is drawn: "not in the list" would be a claim nothing measured. */
export function purposeChipHtml(text, names) {
  if (!text) return '';
  return !names?.length || isListed(text, names)
    ? `<span data-purpose="${escHtml(text)}">${escHtml(text)}</span>`
    : `<span data-purpose="${escHtml(text)}" data-purpose-unlisted>${escHtml(text)} ${notInListCueHtml()}</span>`;
}

/** An investigation's purposes (values and free text), each with its cue. '' when it has none. */
export function purposesSummaryHtml(inv, names) {
  const parts = [...(inv?.purposes || []), ...(inv?.purpose_text ? [inv.purpose_text] : [])];
  return parts.map((p) => purposeChipHtml(p, names)).join(', ');
}

/** Where the list came from, said only when it is NOT Egeria's list read now (a cache, its age, or the
 *  shipped seed), or when RE's cached copy could not be updated. '' otherwise. */
export function purposeSourceHtml(vocab) {
  if (!vocab || (vocab.source === 'egeria' && !vocab.cache_error)) return '';
  return `<div data-purpose-source="${escHtml(vocab.source || 'none')}" class="text-provenance text-ink-muted">${
    escHtml(vocab.words || '')}${vocab.stale ? ' <span class="text-state-warn">· stale</span>' : ''}</div>`;
}

/** Checkboxes for the list (a description on hover), any already-chosen value outside the list kept checked
 *  with its cue, and a free-text box. Read it back with `readPurposePicker(root, prefix)`. */
export function purposePickerHtml({ prefix, vocab, selected = [], text = '' }) {
  const names = vocab?.purposes || [];
  const box = (name, title, checked, unlisted) => `<label class="inline-flex cursor-pointer items-center gap-[4px] text-caveat text-ink" ${
    title ? `title="${escHtml(title)}"` : ''}>
      <input type="checkbox" data-purpose-box="${prefix}" value="${escHtml(name)}" ${checked ? 'checked' : ''}> ${escHtml(name)}${
      unlisted ? ` ${notInListCueHtml()}` : ''}</label>`;
  const listed = (vocab?.values || []).map((v) => box(v.name, v.description, selected.some((s) => fold(s) === fold(v.name)), false));
  const kept = selected.filter((s) => !isListed(s, names)).map((s) => box(s, '', true, names.length > 0));
  return `<div class="mb-[3px] flex flex-wrap gap-s2" data-purpose-picker="${prefix}">${[...listed, ...kept].join('')}</div>
    <input id="${prefix}-purpose-text" type="text" value="${escHtml(text)}" placeholder="or say it in your own words"
      class="mb-[3px] w-full rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink placeholder:text-ink-muted">
    <div id="${prefix}-purpose-text-cue" class="text-provenance">${text && names.length && !isListed(text, names) ? notInListCueHtml() : ''}</div>
    ${purposeSourceHtml(vocab)}`;
}

/** Wires the free-text box's cue: shown as soon as the text is not a list value, cleared when it is. */
export function wirePurposePicker(root, prefix) {
  const input = root.querySelector(`#${prefix}-purpose-text`);
  const cue = root.querySelector(`#${prefix}-purpose-text-cue`);
  if (!input || !cue) return;
  input.addEventListener('input', () => {
    const t = input.value.trim();
    const names = purposesNow()?.purposes || [];
    cue.innerHTML = t && names.length && !isListed(t, names) ? notInListCueHtml() : '';
  });
}

export function readPurposePicker(root, prefix) {
  return {
    purposes: [...root.querySelectorAll(`[data-purpose-box="${prefix}"]:checked`)].map((c) => c.value),
    purpose_text: (root.querySelector(`#${prefix}-purpose-text`)?.value || '').trim(),
  };
}
