/** Table preview: the first n rows of one table, as the PERSON's own database credential sees them.
 *
 *  Design: docs/design-notes/REPLY-DESIGNER-TABLE-PREVIEW-2026-10-19.md. A preview is an act, not a view: nothing runs
 *  when the tab opens. The person picks a table and presses; before the press the pane says whose credential, how many
 *  rows, and that the rows are shown and never stored.
 *
 *  What this file keeps, and does not:
 *   - the rows live in this closure only, for as long as the grid is on screen; nothing is put in storage, a URL, an
 *     attribute or a log;
 *   - the person's password is in the credential form's input and nowhere else (the shared form empties it on a save);
 *   - a masked column's values never arrive here at all: the server does not select them. A revealed column's values
 *     arrive on a reveal and are dropped again by "Mask all again".
 *
 *  Words come from the reply. Where the build differs from the reply (Egeria's record is not built yet), the pane says
 *  so as a plain state, never as a promise. */
import { esc } from '/static/next/app.js';
import {
  getSchemaInventoryTree, getPreviewCredential, holdPreviewCredential, forgetPreviewCredential, previewTable,
} from '/static/re-api.js';
import { credentialChangeHtml, bindCredentialChange } from '/static/next/credential-change.js';
import { enhanceTable } from '/static/next/colresize.js';

const MASK = '••••••';                       // fixed width: a value's length leaks nothing
const PICKER_MAX = 200;

/* ── the door from Schema Inventory ───────────────────────────────────────── */
let _pick = null;
/** Schema Inventory's "preview rows ›" leaves the table it was pressed on; the tab picks it up once. */
export function setPreviewPick(schema, table) { _pick = { schema, table }; }
export function takePreviewPick() { const p = _pick; _pick = null; return p; }

/* ── pure helpers (exported for the harness) ──────────────────────────────── */

/** The picker's flat list of `schema.table`, from the Schema Inventory tree. */
export function previewTablesFrom(schemas) {
  const out = [];
  for (const s of schemas || []) {
    if (s.classification === 'system') continue;
    for (const t of s.tables || []) {
      out.push({ schema: s.schema, name: t.name, tableType: t.table_type || '', kind: /^VIEW$/i.test(t.table_type || '') ? 'view' : 'table' });
    }
  }
  return out;
}

export function hhmm(ts) {
  const d = new Date(ts);
  if (Number.isNaN(d.getTime())) return '';
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
}

/** The count line above the grid: "4 of 12 columns masked · 2 matched a data class · 1 confidential · 1 not profiled". */
export function countLine(counts) {
  const parts = [`${counts.masked} of ${counts.total} columns masked`];
  // Neutral on purpose: a matched class is not known to be privacy-relevant (the profile does not say), so it is not "PII".
  if (counts.pii) parts.push(`${counts.pii} matched a data class`);
  if (counts.confidential) parts.push(`${counts.confidential} confidential`);
  if (counts.not_profiled) parts.push(`${counts.not_profiled} not profiled`);
  if (counts.undecided) parts.push(`${counts.undecided} undecided`);
  return parts.join(' · ');
}

/** What the header says about Egeria's record. EVERY state other than a success is a plain word, never silence:
 *  "not built" and "failed" have their own sentences, any other state is named as it came. */
export function egeriaWords(egeria) {
  const st = egeria && typeof egeria.state === 'string' ? egeria.state : '';
  if (!st || st === 'recorded' || st === 'ok' || st === 'success') return '';
  if (st === 'not_built') return ' · Egeria record not built yet';
  if (st === 'failed') return ' · Egeria record failed';
  return ` · Egeria record ${st.replace(/_/g, ' ')}`;
}

/** The header line. Egeria's record is not built, and the header says so as a plain state. */
export function headerHtml(r) {
  const rec = r.recorded;
  const when = rec ? hhmm(rec.at) : '';
  const egeria = egeriaWords(r.egeria);
  const recorded = rec ? ` · <span data-pv-recorded>recorded · RE ${esc(when)}${egeria}</span>` : '';
  return `<div data-pv-header class="mb-s2 text-answer text-ink"><span class="font-semibold">${esc(r.schema)}.${esc(r.table)}</span>`
    + ` · first ${r.limit} rows as the database returned them · not a sample, not ordered`
    + ` · as you (${esc(r.as_user)})${when ? ` · ${esc(when)}` : ''}${recorded}</div>`;
}

function cellHtml(v) {
  if (v === null || v === undefined) return '<span class="text-ink-muted">NULL</span>';
  return esc(String(v));
}

function columnHeadHtml(c, confirming) {
  let tag = '';
  if (c.revealed) {
    tag = `<span data-pv-tag="revealed" class="ml-[4px] rounded-sm border border-rule px-[4px] text-provenance text-ink">revealed by you · recorded</span>`;
  } else if (c.masked) {
    const muted = c.reason === 'not_profiled' || c.reason === 'undecided';
    tag = `<span data-pv-tag="${esc(c.reason)}" title="${esc(c.hover)}" class="ml-[4px] rounded-sm border border-rule px-[4px] text-provenance ${muted ? 'text-ink-muted' : 'text-ink'}">${esc(c.tag)}</span>`
      + ` <button type="button" data-pv-reveal="${esc(c.name)}" class="cursor-pointer bg-transparent text-provenance text-ink underline">show values</button>`;
  }
  const confirm = confirming === c.name
    ? `<div data-pv-confirm class="mt-[2px] font-normal text-ink">Show ${esc(c.name)}'s values? This is recorded with your name in RE.
        <button type="button" data-pv-confirm-yes class="cursor-pointer bg-transparent text-accent-ink underline">Show</button>
        · <button type="button" data-pv-confirm-no class="cursor-pointer bg-transparent text-ink underline">keep masked</button></div>`
    : '';
  return `<th data-pv-col="${esc(c.name)}" class="py-[3px] pr-s3 text-left align-top font-mono font-normal text-ink">${esc(c.name)}${tag}${confirm}</th>`;
}

/** The grid. Masked cells are the fixed "••••••" in muted ink. */
export function gridHtml(r, confirming = '') {
  const head = r.columns.map((c) => columnHeadHtml(c, confirming)).join('');
  const body = r.rows.map((row) => `<tr class="border-b border-rule">${r.columns.map((c) => (c.masked && !c.revealed
    ? `<td data-pv-masked class="py-[3px] pr-s3 font-mono text-ink-muted">${MASK}</td>`
    : `<td class="py-[3px] pr-s3 font-mono text-ink">${cellHtml(row[c.name])}</td>`)).join('')}</tr>`).join('');
  return `<div class="overflow-x-auto"><table data-pv-grid data-rc-id="table-preview.${esc(r.schema)}.${esc(r.table)}" class="border-collapse text-caveat"><thead><tr class="border-b border-rule">${head}</tr></thead><tbody>${body}</tbody></table></div>`
    + (r.columns_not_shown > 0 ? `<div data-pv-more-columns class="mt-s1 text-provenance text-ink-muted">${r.columns_not_shown} more columns not shown</div>` : '');
}

/** The refusal states. Each keeps the header and offers its own next press; "no rows" is never shown for the first
 *  two. */
export function refusalHtml(r) {
  const fq = `${r.schema}.${r.table}`;
  const ctl = (attr, label) => ` <button type="button" ${attr} class="cursor-pointer bg-transparent text-accent-ink underline">${label}</button>`;
  switch (r.outcome) {
    case 'no_read':
      return `<div data-pv-refusal="no_read" class="text-state-warn">your credential can't read ${esc(fq)} · ${esc(r.sentence)}${ctl('data-pv-cred-open', 'use another credential ›')}</div>`;
    case 'timeout':
      return `<div data-pv-refusal="timeout" class="text-state-warn">${esc(r.sentence)}${ctl('data-pv-retry', 'try again')}</div>`;
    case 'empty':
      return `<div data-pv-refusal="empty" class="text-ink"><span class="font-mono">∅</span> 0 rows · ${esc(fq)} is empty, as of ${esc(hhmm(r.recorded && r.recorded.at))}</div>`;
    case 'stale':
      return `<div data-pv-refusal="stale" class="text-state-warn">the stored catalog does not match the database · ${esc(r.sentence)} · survey it again to refresh the catalog</div>`;
    case 'credential_refused':
      return `<div data-pv-refusal="credential_refused" class="text-state-warn">${esc(r.sentence)}${ctl('data-pv-cred-open', 'use another credential ›')}</div>`;
    default:
      return `<div data-pv-refusal="${esc(r.outcome)}" class="text-state-warn">no rows shown · ${esc(r.sentence || r.outcome)}</div>`;
  }
}

/* ── the pane ─────────────────────────────────────────────────────────────── */

/** Mounts the pane into `host`. `dbName` is the database's display name for the credential form. */
export async function mountTablePreview(host, { slug, dbName }) {
  const S = { tables: [], filter: '', picked: takePreviewPick(), cred: null, result: null, confirming: '', busy: false, treeError: '' };

  host.innerHTML = `<div data-table-preview>
    <p data-pv-intro class="mb-s2 max-w-[75ch] text-ink-muted">A preview reads the live table as you, not from a stored survey. Nothing runs until you press.
      Shown, never stored · each preview is recorded with your name in RE · Egeria record not built yet.</p>
    <div data-pv-picker class="mb-s3"></div>
    <div data-pv-controls class="mb-s3"></div>
    <div data-pv-cred-slot class="mb-s3"></div>
    <div data-pv-result></div>
  </div>`;
  const q = (sel) => host.querySelector(sel);

  function selected() { return S.tables.find((t) => t.schema === S.picked?.schema && t.name === S.picked?.table) || null; }

  function renderPicker() {
    const el = q('[data-pv-picker]');
    if (S.treeError) { el.innerHTML = `<div class="text-state-warn">Could not read the table list: ${esc(S.treeError)}</div>`; return; }
    if (!S.tables.length) { el.innerHTML = '<div class="text-ink-muted">No stored tables yet — run a survey first.</div>'; return; }
    const f = S.filter.trim().toLowerCase();
    const matches = S.tables.filter((t) => !f || `${t.schema}.${t.name}`.toLowerCase().includes(f));
    const shown = matches.slice(0, PICKER_MAX);
    el.innerHTML = `<input data-pv-filter type="text" value="${esc(S.filter)}" placeholder="name contains…"
        class="mb-s1 w-full max-w-[40ch] rounded-sm border border-rule bg-transparent px-s2 py-[4px] text-caveat text-ink placeholder:text-ink-muted" />
      <div data-pv-list class="max-h-[16rem] max-w-[60ch] overflow-y-auto border border-rule text-caveat">${shown.map((t) => {
        const on = t.schema === S.picked?.schema && t.name === S.picked?.table;
        return `<button type="button" data-pv-pick="${esc(t.schema)}.${esc(t.name)}" data-schema="${esc(t.schema)}" data-table="${esc(t.name)}"
          class="block w-full cursor-pointer border-0 bg-transparent px-s2 py-[2px] text-left font-mono ${on ? 'text-accent-ink' : 'text-ink'}">${esc(t.schema)}.${esc(t.name)}${t.kind === 'view' ? ' <span class="font-sans text-ink-muted">view</span>' : ''}</button>`;
      }).join('') || '<div class="px-s2 py-[2px] text-ink-muted">no table matches</div>'}</div>
      ${matches.length > shown.length ? `<div class="text-provenance text-ink-muted">${matches.length - shown.length} more — narrow the filter</div>` : ''}`;
    const input = el.querySelector('[data-pv-filter]');
    input.addEventListener('input', (e) => { S.filter = e.target.value; const pos = e.target.selectionStart; renderPicker(); const n = q('[data-pv-filter]'); n.focus(); n.setSelectionRange(pos, pos); });
    el.querySelectorAll('[data-pv-pick]').forEach((b) => b.addEventListener('click', () => {
      S.picked = { schema: b.dataset.schema, table: b.dataset.table };
      S.result = null; S.confirming = '';
      renderPicker(); renderControls(); renderResult();
    }));
  }

  function renderControls() {
    const el = q('[data-pv-controls]');
    const t = selected();
    if (S.cred && S.cred.engine_supported === false) { el.innerHTML = `<div data-pv-unsupported class="text-state-warn">${esc(S.cred.unsupported)}</div>`; return; }
    if (!t) { el.innerHTML = '<div class="text-ink-muted">Pick a table to preview.</div>'; return; }
    const fq = `${t.schema}.${t.name}`;
    const isView = t.kind === 'view';
    const label = (n) => (isView ? `Run the view, ${n} rows` : `Show ${n} rows`);
    let body;
    if (!S.cred || !S.cred.held) {
      body = `<span class="text-ink">Previews run as you, with your own credential for ${esc(dbName)}, not the surveyor's.</span>
        <button type="button" data-pv-cred-open class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink">Enter your credential ›</button>`;
    } else {
      body = `<button type="button" data-pv-run="20" class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink">${esc(label(20))}</button>
        <button type="button" data-pv-run="100" class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink">${esc(label(100))}</button>
        <span class="text-ink-muted">as you (${esc(S.cred.user)})</span>
        <button type="button" data-pv-cred-open class="cursor-pointer bg-transparent text-ink underline">use another credential ›</button>
        ${S.forgetFailed ? '<span data-pv-forget-failed class="text-state-warn">couldn\'t forget the credential · try again</span>' : ''}`;
    }
    el.innerHTML = `<div class="mb-s1 text-ink"><span class="font-mono">${esc(fq)}</span> <span class="text-ink-muted">${isView ? 'view' : (t.tableType === 'MATERIALIZED VIEW' ? 'materialized view' : 'table')}</span></div>
      ${isView ? '<div data-pv-view-cost class="mb-s1 text-provenance text-ink-muted">a view runs its query on every read · stopped after 10 s</div>' : ''}
      <div class="flex flex-wrap items-center gap-s2">${body}</div>
      <div class="mt-s1 text-provenance text-ink-muted">shown, never stored · each preview is recorded with your name in RE · Egeria record not built yet</div>`;
    el.querySelectorAll('[data-pv-run]').forEach((b) => b.addEventListener('click', () => run(Number(b.dataset.pvRun), [])));
    el.querySelector('[data-pv-cred-open]')?.addEventListener('click', useAnotherCredential);
  }

  /** EVERY "use another credential" path comes through here: the held credential is dropped on the server first, so
   *  cancelling the form leaves nothing held that the screen says is gone. With none held it only opens the form. */
  async function useAnotherCredential() {
    if (S.cred && S.cred.held) {
      try {
        await forgetPreviewCredential(slug);
      } catch {
        // The server may still hold it: keep saying so, and say that the forget failed. Never show "gone" for a
        // credential that is not.
        S.forgetFailed = true;
        renderControls();
        return;
      }
      S.forgetFailed = false;
      S.cred = { ...(S.cred || {}), held: false };
      renderControls();
    }
    openCredential();
  }

  function openCredential() {
    const slot = q('[data-pv-cred-slot]');
    slot.innerHTML = credentialChangeHtml({ slug, db_user: '' }, {
      titleHtml: `Your credential for <span class="font-mono">${esc(dbName)}</span>`,
      noteHtml: 'kept for this session only · never stored',
      saveLabel: 'Test and keep', prefill: false, passwordPlaceholder: 'password',
    });
    bindCredentialChange(slot, slug, {
      saveLabel: 'Test and keep', notWord: 'not kept',
      saveFn: async (user, password) => {
        const r = await holdPreviewCredential(slug, { user, password });
        S.forgetFailed = false;
        S.cred = { ...(S.cred || {}), held: true, user: r.user };
        renderControls();
        return `<div data-cred-saved class="text-ink">✓ kept for this session only · ${esc(r.user)}</div>`;
      },
      onCancel: () => { slot.innerHTML = ''; },
    });
  }

  async function run(limit, reveal) {
    const t = selected();
    if (!t || S.busy) return;
    S.busy = true; S.confirming = '';
    S.lastLimit = limit;
    const el = q('[data-pv-result]');
    el.innerHTML = '<div class="text-ink-muted">… reading the table as you</div>';
    host.querySelectorAll('[data-pv-run]').forEach((b) => { b.disabled = true; });
    try {
      S.result = await previewTable(slug, { schema: t.schema, table: t.name, limit, reveal });
    } catch (err) {
      S.result = null;
      if (err && err.status === 409 && /no credential/i.test(err.message || '')) {
        S.cred = { ...(S.cred || {}), held: false };
        renderControls();
      }
      el.innerHTML = `<div data-pv-error class="text-state-warn">no rows shown · ${esc(err && err.message ? err.message : String(err))}</div>`;
      S.busy = false;
      host.querySelectorAll('[data-pv-run]').forEach((b) => { b.disabled = false; });
      return;
    }
    S.busy = false;
    renderResult();
    host.querySelectorAll('[data-pv-run]').forEach((b) => { b.disabled = false; });
  }

  function revealedNames() { return (S.result?.columns || []).filter((c) => c.revealed).map((c) => c.name); }

  function renderResult() {
    const el = q('[data-pv-result]');
    const r = S.result;
    if (!r) { el.innerHTML = ''; return; }
    if (r.outcome === 'rows') {
      const rev = revealedNames();
      el.innerHTML = `${headerHtml(r)}
        <div data-pv-counts class="mb-s2 text-caveat text-ink-muted">${esc(countLine(r.counts))}${rev.length
          ? ' · <button type="button" data-pv-mask-all class="cursor-pointer bg-transparent text-ink underline">Mask all again</button>' : ''}</div>
        ${gridHtml(r, S.confirming)}`;
    } else {
      el.innerHTML = `${headerHtml(r)}${refusalHtml(r)}`;
    }
    // the grid's columns resize like every /next table (colresize.js); a redraw replaces the table, so it is wired here
    el.querySelectorAll('table[data-pv-grid]').forEach((t) => enhanceTable(t));
    el.querySelectorAll('[data-pv-reveal]').forEach((b) => b.addEventListener('click', () => { S.confirming = b.dataset.pvReveal; renderResult(); }));
    el.querySelector('[data-pv-confirm-no]')?.addEventListener('click', () => { S.confirming = ''; renderResult(); });
    el.querySelector('[data-pv-confirm-yes]')?.addEventListener('click', () => {
      const col = S.confirming;
      run(r.limit, [...revealedNames(), col]);
    });
    el.querySelector('[data-pv-mask-all]')?.addEventListener('click', () => {
      // client-side: the revealed values are dropped from memory and the grid is masked again; the next preview of
      // the table starts masked anyway
      const gone = new Set(revealedNames());
      S.result = { ...r, columns: r.columns.map((c) => ({ ...c, revealed: false })), rows: r.rows.map((row) => Object.fromEntries(Object.entries(row).filter(([k]) => !gone.has(k)))) };
      renderResult();
    });
    el.querySelector('[data-pv-retry]')?.addEventListener('click', () => run(S.lastLimit || 20, []));
    el.querySelector('[data-pv-cred-open]')?.addEventListener('click', useAnotherCredential);
  }

  // reads: the table list (the Schema Inventory's own call) and whether a credential of this person's is held
  try { S.cred = await getPreviewCredential(slug); } catch (err) { S.cred = { held: false, error: err.message }; }
  try { S.tables = previewTablesFrom((await getSchemaInventoryTree(slug)).schemas); } catch (err) { S.treeError = err.message; }
  renderPicker(); renderControls(); renderResult();
}
