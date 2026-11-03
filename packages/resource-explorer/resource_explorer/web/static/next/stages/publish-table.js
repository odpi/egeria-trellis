/* Curate's publish table (declared extensibility slice 1, PR 2; DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-
 * CONFIDENCE.md §5, the owner's answer 3). On a database's "Describe and publish" tab, above the Publish band:
 *
 *   a row per declared measure: analysis, measure, value with unit, as-of, confidence form with its basis, scope,
 *   and "writes as" (the declared Egeria type, or "generic measure · no native type declared"); a Publish | Keep in
 *   RE selector per row, saved at once as a signed choice; the state of the last publish, from proof rows.
 *
 * Nothing here is written per analysis: every word comes from the server's `publish_table.build_rows`, which reads
 * the declarations. There is no press on this table: a database's one press is the commit on Contents, a file
 * system's is the folder survey's Publish; each carries the chosen rows inside RE's own survey report and reads each back. A row's "published" word comes from the proof row
 * that read-back leaves, never from a click. A row that cannot publish says why, and offers Keep in RE only.
 *
 * The owner's answers of 2026-10-28 (DECLARED-EXTENSIBILITY-SLICE1-PR2-PUBLISH-TABLE.md §4a): a measure already in
 * Egeria another way shows that route as its state (`r.elsewhere`) and offers Keep in RE only; a Publish choice lasts
 * across surveys, and its line says so (`r.choice.standing`).
 */
import { stateEntry } from '/static/next/glyphs.js';
import { getPublishTable, setPublishChoice } from '/static/re-api.js';
import { state, esc, openCurate } from '/static/next/app.js';
import { fmtBytes } from '/static/next/stages/native-report.js';

/** The heading and the one sentence under it. */
export const TABLE_HEAD = 'Publish · declared measures';
export const NO_ROWS_WORDS = 'no analysis declares a measure for this resource yet';
export const NOT_READ_WORDS = 'the declared measures could not be read';
export const SIGN_IN_WORDS = 'sign in to choose — a choice needs an author';

const whoAmI = () => (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';

/** The published state's cue key (glyphs.js), by the server's `published.state`. */
const PUBLISHED_CUE = { published: 'measured', changed: 'partial', not_read_back: 'error', local: 'unrun' };

const cue = (stateKey, word, title = '') => {
  const e = stateEntry(stateKey);
  // The tone class is a literal in each branch (no class interpolation): ok, warn, or muted.
  const open = e.tone === 'text-state-ok' ? '<span class="text-state-ok"'
    : e.tone === 'text-state-warn' ? '<span class="text-state-warn"' : '<span class="text-ink-muted"';
  return `${open} data-cue="${esc(stateKey)}" title="${esc(title || e.word)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(word)}</span>`;
};

const when = (iso) => String(iso || '').slice(0, 16).replace('T', ' ');

/** Publish | Keep in RE, the pressed half marked. Publish is disabled when the row cannot publish (the reason is its
 *  title and the line under the row); both are disabled for someone not signed in. */
function selectorHtml(r, signedIn, entityType = 'database') {
  const chosen = (r.choice && r.choice.choice) || '';
  // The pressed half and the other are two literal class strings (no class interpolation, for the coverage check).
  const half = (choice, label, disabled, title) => `<button type="button" data-publish-choice="${choice}"
    aria-pressed="${chosen === choice ? 'true' : 'false'}" title="${esc(title)}"
    class="${chosen === choice
    ? 'cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink disabled:cursor-default disabled:opacity-60'
    : 'cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60'}"${disabled ? ' disabled' : ''}>${label}</button>`;
  const press = entityType === 'filesystem' ? "the folder survey's Publish" : 'the commit on Contents';
  const pubTitle = r.publishable ? `Publish ${r.measure} with ${press}` : r.why_not;
  return `<span class="inline-flex gap-s1" role="group" aria-label="Publish or keep ${esc(r.measure)}">
    ${half('publish', 'Publish', !signedIn || !r.publishable, signedIn ? pubTitle : SIGN_IN_WORDS)}
    ${half('keep', 'Keep in RE', !signedIn, signedIn ? 'Keep this measure in Resource Explorer only' : SIGN_IN_WORDS)}</span>`;
}

/** Who chose, when; or "not chosen · stays in RE". */
function choiceLineHtml(r) {
  if (!r.choice) return '<span class="text-provenance text-ink-muted" data-choice-line>not chosen · stays in RE</span>';
  const word = r.choice.choice === 'publish' ? 'will publish' : 'kept in RE';
  const standing = r.choice.standing ? ` · <span data-choice-standing>${esc(r.choice.standing)}</span>` : '';
  return `<span class="text-provenance text-ink-muted" data-choice-line>${esc(word)} · ${esc(r.choice.by)} · ${esc(when(r.choice.at))}${standing}</span>`;
}

function valueHtml(r) {
  if (r.value === null || r.value === undefined) {
    return `<span class="text-ink-muted" data-absence="${esc(r.absence || '')}">—</span>
      <span class="block max-w-[40ch] text-provenance text-ink-muted">${esc(r.absence_words || '')}</span>`;
  }
  // A size in bytes reads as every other size in RE does (native-report's fmtBytes), not as a raw count.
  const words = r.unit === 'bytes' && typeof r.value === 'number' ? fmtBytes(r.value) : r.value_words;
  return `<span class="tnum text-ink">${esc(words)}</span>`;
}

function writesAsHtml(r) {
  const w = r.writes_as || {};
  return `<span data-writes-as="${esc(w.route || '')}" class="${w.route === 'generic' ? 'text-ink-muted' : 'text-ink'}">${esc(w.words || '')}</span>`;
}

function stateHtml(r) {
  // Already in Egeria another way: that route is the row's state, whatever this table has sent before.
  if (r.elsewhere) {
    return `<span data-published="elsewhere" data-elsewhere="${esc(r.elsewhere.route)}">${cue('measured', r.elsewhere.words)}</span>`;
  }
  const p = r.published || { state: 'local', words: 'local · not published' };
  return `<span data-published="${esc(p.state)}">${cue(PUBLISHED_CUE[p.state] || 'unrun', p.words, p.why || '')}</span>`;
}

/** The second date. A stored record was measured at a time; a derivation is recomputed on every read, so it says what
 *  it was derived from, never a measurement time of its own. */
function measuredHtml(r) {
  if (r.derived) {
    const words = r.measured_at ? `derived on read from the survey of ${esc(when(r.measured_at))}`
      : 'derived on read; the survey it read is not recorded';
    return `<span class="block text-provenance text-ink-muted" data-derived-from>${words}</span>`;
  }
  return r.measured_at ? `<span class="block text-provenance text-ink-muted" data-measured-at>measured ${esc(when(r.measured_at))}</span>` : '';
}

/** One row; the reason a row cannot publish sits under it, in words. */
export function rowHtml(r, signedIn, entityType = 'database') {
  return `<tr class="border-b border-rule align-top" data-publish-row="${esc(r.locator)}">
    <td class="py-[4px] pr-s2 text-left">${esc(r.analysis)}${r.rows_band ? `<span class="block text-provenance text-ink-muted" data-rows-band>${esc(r.rows_band)}</span>` : ''}</td>
    <td class="py-[4px] pr-s2 text-left">${esc(r.measure)} <span class="text-provenance text-ink-muted">· ${esc(r.unit)}</span></td>
    <td class="py-[4px] pr-s2 text-left">${valueHtml(r)}</td>
    <td class="py-[4px] pr-s2 text-left tnum">${r.as_of ? `${esc(when(r.as_of))}<span class="block text-provenance text-ink-muted" data-as-of-label>as of</span>` : '<span class="text-ink-muted">no as-of</span>'}${
      measuredHtml(r)}</td>
    <td class="py-[4px] pr-s2 text-left">${esc((r.confidence && r.confidence.words) || '')}</td>
    <td class="py-[4px] pr-s2 text-left">${esc(r.scope || '—')}</td>
    <td class="py-[4px] pr-s2 text-left">${writesAsHtml(r)}</td>
    <td class="py-[4px] pr-s2 text-left">${selectorHtml(r, signedIn, entityType)}
      <span class="block">${choiceLineHtml(r)}</span>
      ${!r.publishable && r.why_not ? `<span class="block max-w-[40ch] text-provenance text-ink-muted" data-why-not>${esc(r.why_not)}</span>` : ''}
      <span class="block text-provenance text-state-warn" data-choice-feedback></span></td>
    <td class="py-[4px] text-left">${stateHtml(r)}</td>
  </tr>`;
}

/** The whole table. `table` is the server's answer; null = not read. */
export function publishTableHtml(table, signedIn, entityType = 'database') {
  const head = `<div class="mb-s1 text-caps uppercase tracking-caps text-ink">${TABLE_HEAD}</div>`;
  if (!table || !Array.isArray(table.rows)) {
    return `${head}<div class="text-caveat" data-publish-table-state="not_read">${cue('unknown', NOT_READ_WORDS)}</div>`;
  }
  if (!table.rows.length) {
    return `${head}<div class="text-caveat text-ink-muted" data-publish-table-state="empty">${esc(NO_ROWS_WORDS)}</div>`;
  }
  const n = Number(table.chosen || 0);
  return `${head}
    <div class="text-caveat text-ink-muted" data-publish-table-press>${esc(table.press || '')}
      · <span class="tnum" data-publish-table-chosen>${n}</span> chosen to publish
      ${entityType === 'database' ? '· <button type="button" data-publish-table-goto="curate_contents" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">Contents ›</button>' : ''}
      ${signedIn ? '' : `· <span>${esc(SIGN_IN_WORDS)}</span>`}</div>
    <div class="min-w-0 max-w-full overflow-x-auto" data-publish-table-state="read">
      <table class="mt-s2 w-full border-collapse text-caveat">
        <thead><tr class="border-b border-rule-strong text-caps uppercase tracking-caps text-ink-muted">
          <th class="py-[4px] pr-s2 text-left">Analysis</th>
          <th class="py-[4px] pr-s2 text-left">Measure</th>
          <th class="py-[4px] pr-s2 text-left">Value</th>
          <th class="py-[4px] pr-s2 text-left">As of</th>
          <th class="py-[4px] pr-s2 text-left">Confidence</th>
          <th class="py-[4px] pr-s2 text-left">Scope</th>
          <th class="py-[4px] pr-s2 text-left">Writes as</th>
          <th class="py-[4px] pr-s2 text-left">Choice</th>
          <th class="py-[4px] text-left">State</th>
        </tr></thead>
        <tbody>${table.rows.map((r) => rowHtml(r, signedIn, entityType)).join('')}</tbody>
      </table></div>`;
}

/** Draw the table into `el` and bind its selectors. A choice is saved at once; the row redraws from the server's
 *  answer (the saved row), never from the click; a refusal stays under the row in words. Never throws into the pane. */
export async function renderPublishTable(el, slug, entityType = 'database') {
  if (!el) return;
  const signedIn = !!whoAmI();
  let table = null;
  try { table = await getPublishTable(entityType, slug); } catch (_) { table = null; }
  if (!el.isConnected || slug !== state.selectedSlug) return;
  el.innerHTML = publishTableHtml(table, signedIn, entityType);
  const go = el.querySelector('[data-publish-table-goto]');
  if (go) go.onclick = () => openCurate({ tab: go.dataset.publishTableGoto });
  el.querySelectorAll('[data-publish-row]').forEach((tr) => bindRow(el, tr, slug, entityType, table, signedIn));
}

function bindRow(el, tr, slug, entityType, table, signedIn) {
  const row = table.rows.find((r) => r.locator === tr.getAttribute('data-publish-row'));
  tr.querySelectorAll('[data-publish-choice]').forEach((b) => b.addEventListener('click', async () => {
    if (b.disabled || b.dataset.pending) return;                 // a second press is ignored
    b.dataset.pending = '1';
    const feedback = tr.querySelector('[data-choice-feedback]');
    feedback.textContent = '';
    try {
      const saved = await setPublishChoice(entityType, slug, row.analysis_id, row.measure, b.dataset.publishChoice);
      if (!el.isConnected) return;
      Object.assign(row, saved);
      table.chosen = table.rows.filter((r) => r.publishable && r.choice && r.choice.choice === 'publish').length;
      const fresh = el.ownerDocument.createElement('tbody');
      fresh.innerHTML = rowHtml(row, signedIn, entityType);
      const next = fresh.firstElementChild;
      tr.replaceWith(next);
      const count = el.querySelector('[data-publish-table-chosen]');
      if (count) count.textContent = String(table.chosen);
      bindRow(el, next, slug, entityType, table, signedIn);
    } catch (err) {
      delete b.dataset.pending;
      feedback.textContent = `not saved · ${err.message || 'the choice was refused'}`;
    }
  }));
}
