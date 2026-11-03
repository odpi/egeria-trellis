/* First index, asked first (REPLY-DESIGNER-GROUP-SCHEDULING-AND-FIRST-INDEX-2026-10-19 §4 and
 * REPLY-DESIGNER-FIRST-INDEX-CONSENT-CHOICE-2026-10-20).
 *
 * THE RULE: nothing is indexed without a yes that names it. One repository gets one ask ("Index it" · "not now").
 * A batch offers two paths, "Index all N" and "Ask me for each ›". Either way every repository that gets indexed
 * is named by a POST to /api/first-index/queue, and it enters the queue AT ONCE (not when the dialog closes).
 *
 * THE LIST IS THE RECORD. In "Ask me for each", the stepping block at the top asks about one repository; the list
 * below holds every repository with the answer it has been given. An answer can be changed by pressing its row
 * until that repository has started indexing. After it has started the row says "indexing" and offers nothing.
 * A row's state word comes from the progress read (run rows), never from what this file just did.
 *
 * "Skipped" and "not asked" are this dialog's own state: they have no run row, so nothing outside the dialog says
 * them. Closing the dialog mid-way is "Skip the rest": nothing unanswered is indexed.
 *
 * REMEMBERED: the PATH ("Index all" or "Ask me for each") the person used last, per browser, to focus that button
 * next time. Never the consent: the ask is always shown and both buttons are always offered.
 */
import { ago } from '/static/next/format.js';
import { getFirstIndexAsk, queueFirstIndex, withdrawFirstIndex, getFirstIndexProgress } from '/static/re-api.js';
import { openDialog } from '/static/next/worklist.js';
import { state, esc, $ } from '/static/next/app.js';
import { indexStateCueHtml, ordinal, INDEX_IT_WORD } from '/static/next/stages/repo-header.js';

export const UNKNOWN_UNTIL_FIRST_INDEX = 'unknown until the first index';
export const PATH_KEY = 're_first_index_path';

/* ── words ─────────────────────────────────────────────────────────────── */

export function fmtBytes(n) {
  if (n == null) return '';
  const mb = n / 1048576;
  if (mb >= 10) return `${Math.round(mb).toLocaleString('en-US')} MB`;
  if (mb >= 1) return `${mb.toFixed(1)} MB`;
  return `${Math.max(1, Math.round(n / 1024)).toLocaleString('en-US')} KB`;
}

export function fmtDuration(secs) {
  if (secs == null) return '';
  if (secs < 90) return `${Math.max(1, Math.round(secs))} s`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

const num = (n) => Number(n).toLocaleString('en-US');

/** One repository's figures. Measured ones say where from; estimated ones say what from; one that cannot be
 *  estimated says so in the owner's words. A repository with no inventory is "not measured", never "0 files". */
export function figuresHtml(r) {
  const measured = r.measured
    ? `${num(r.files)} files · ${fmtBytes(r.text_bytes)} of text · ${esc(r.source)}`
    : `not measured · ${esc(r.source || 'no file inventory yet')}`;
  const time = r.seconds != null
    ? `about ${fmtDuration(r.seconds)} · ${esc(r.seconds_source)}`
    : `time: ${UNKNOWN_UNTIL_FIRST_INDEX}`;
  const size = r.chunks != null
    ? `about ${num(r.chunks)} chunks of index · ${esc(r.chunks_source)}`
    : `index size: ${UNKNOWN_UNTIL_FIRST_INDEX}`;
  return `<div data-ask-figures>
    <div data-figure="measured">${measured}</div>
    <div data-figure="fetch">fetches: ${esc(r.fetch)}</div>
    <div data-figure="estimate">${time} · ${size}</div>
  </div>`;
}

/** Totals over a set of repositories: only the figures that exist, each with how many it covers. */
export function totalsOf(repos) {
  const sum = (k) => {
    const have = repos.filter((r) => r[k] != null);
    return { value: have.reduce((a, r) => a + r[k], 0), of: have.length };
  };
  return { count: repos.length, files: sum('files'), text: sum('text_bytes'), seconds: sum('seconds'), chunks: sum('chunks') };
}

export function totalsLine(repos) {
  const t = totalsOf(repos);
  const n = t.count;
  const parts = [];
  parts.push(t.files.of ? `${num(t.files.value)} files · ${fmtBytes(t.text.value)} of text${t.files.of < n ? ` (measured for ${t.files.of} of ${n})` : ''}`
    : 'files and text: not measured');
  parts.push(`${repos.length} to fetch`);
  parts.push(t.seconds.of === n && n > 0 ? `about ${fmtDuration(t.seconds.value)}, estimated` : `time: ${UNKNOWN_UNTIL_FIRST_INDEX}`);
  parts.push(t.chunks.of === n && n > 0 ? `about ${num(t.chunks.value)} chunks of index, estimated` : `index size: ${UNKNOWN_UNTIL_FIRST_INDEX}`);
  return parts.join(' · ');
}

/* ── progress, shared by the cue, the Automate list and the dialog ─────── */

const subscribers = new Set();
let pollMs = 3000;
let timer = null;
let lastRead = 0;
export function _setPollMs(ms) { pollMs = ms; }

export function subscribeFirstIndex(fn) { subscribers.add(fn); return () => subscribers.delete(fn); }

/** Read the progress once, put it on `state.firstIndex`, bring `state.projects` into line with what the run rows
 *  prove, repaint the cues, tell the subscribers. */
export async function refreshFirstIndex() {
  let p;
  try { p = await getFirstIndexProgress(); } catch { return state.firstIndex; }
  const lines = {};
  for (const l of p.lines || []) lines[l.slug] = l;
  lastRead = Date.now();
  state.firstIndex = { lines, list: p.lines || [], done: p.done, of: p.of, active: !!p.active };
  for (const l of p.lines || []) {
    const proj = (state.projects || []).find((x) => x.slug === l.slug);
    if (proj && l.index_state) proj.index_state = l.index_state;
  }
  repaintCues();
  for (const fn of [...subscribers]) fn(state.firstIndex);
  if (state.firstIndex.active) schedulePoll();
  return state.firstIndex;
}

/** Read the progress unless it was read a moment ago (the header calls this on every render). */
export function ensureFirstIndexFresh(maxAgeMs = 15000) {
  if (state.firstIndex && Date.now() - lastRead < maxAgeMs) return Promise.resolve(state.firstIndex);
  return refreshFirstIndex();
}

function schedulePoll() {
  if (timer) return;
  timer = setTimeout(() => { timer = null; refreshFirstIndex(); }, pollMs);
  timer.unref?.();
}

/** The cue on the open repository's header changes IN PLACE (the replaced node is the cue only). */
export function repaintCues() {
  const slug = state.selectedSlug;
  if (!slug || state.resourceType !== 'repo' || typeof document === 'undefined') return;
  const host = document.querySelector('[data-index-cue]');
  const p = (state.projects || []).find((x) => x.slug === slug);
  const html = indexStateCueHtml(p, state.firstIndex?.lines?.[slug] || null);
  if (host) {
    if (!html) { host.remove(); return; }
    const tpl = document.createElement('template');
    tpl.innerHTML = html;
    host.replaceWith(tpl.content.firstElementChild);
  }
}

/** Wire `index it ›` in a header (called by bindResourceHeader). Delegated, so a repainted cue keeps working. */
const boundRoots = new WeakSet();
export function bindFirstIndexDoor(root) {
  if (!root || boundRoots.has(root)) return;
  boundRoots.add(root);
  root.addEventListener('click', (ev) => {
    const b = ev.target.closest('[data-first-index-open]');
    if (!b) return;
    openFirstIndexAsk({ slugs: [state.selectedSlug] });
  });
}

/* ── the remembered path ───────────────────────────────────────────────── */

export function rememberedPath() {
  try { const v = localStorage.getItem(PATH_KEY); return v === 'all' || v === 'each' ? v : ''; } catch { return ''; }
}
function rememberPath(path) {
  try { localStorage.setItem(PATH_KEY, path); } catch { /* private mode: the default is only a convenience */ }
}

/* ── the ask ───────────────────────────────────────────────────────────── */

const newBatchId = () => (globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : `b-${Date.now()}-${Math.random().toString(16).slice(2)}`);

/** Open the ask for these repositories. One slug is the single ask; more is the batch ask with its two paths.
 *  `listOpen` opens the batch with its list shown (the Automate door: "choose which"). */
export async function openFirstIndexAsk({ slugs, listOpen = false }) {
  const el = openDialog(slugs.length === 1 ? 'Index for chat?' : `Index ${slugs.length} repositories for chat?`, '');
  const body = el.querySelector('#wl-detail-body');
  let ask;
  try { ask = await getFirstIndexAsk(slugs); }
  catch (err) { body.innerHTML = `<p data-ask-error class="text-state-warn">Could not read the figures: ${esc(err.message)}</p>`; return el; }
  const repos = (ask.repositories || []).filter((r) => r.index_state === 'not_yet_indexed');
  if (!repos.length) { body.innerHTML = '<p data-ask-nothing class="text-ink-muted">Nothing here is waiting for a first index.</p>'; return el; }
  if (repos.length === 1) { renderSingle(el, body, repos[0]); return el; }
  renderBatch(el, body, repos, { listOpen });
  return el;
}

function renderSingle(el, body, r) {
  el.querySelector('[role="dialog"]')?.setAttribute('aria-label', `Index ${r.name} for chat?`);
  const title = el.querySelector('.font-heading');
  if (title) title.textContent = `Index ${r.name} for chat?`;
  body.innerHTML = `<div data-ask="single" class="text-ink">
    ${figuresHtml(r)}
    <div class="mt-s3 flex items-center gap-s3">
      <button type="button" data-ask-yes class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-ink">Index it</button>
      <button type="button" data-ask-no class="cursor-pointer border-0 bg-transparent p-0 text-ink-muted underline">not now</button>
      <span data-ask-state class="text-caveat text-ink-muted"></span>
    </div></div>`;
  body.querySelector('[data-ask-no]').addEventListener('click', () => el.querySelector('[data-act="close"]').click());
  body.querySelector('[data-ask-yes]').addEventListener('click', async (ev) => {
    const b = ev.currentTarget;
    b.disabled = true;
    const note = body.querySelector('[data-ask-state]');
    note.textContent = 'Queueing…';
    try {
      const res = await queueFirstIndex({ slugs: [r.slug], path: 'single' });
      if (res.refused?.length) { b.disabled = false; note.textContent = res.refused[0].reason; return; }
      note.textContent = `Queued. ${r.name} is waiting to be indexed.`;
      await refreshFirstIndex();
    } catch (err) { b.disabled = false; note.textContent = `Not queued: ${err.message}`; }
  });
}

/* The batch model. `answer`: 'pending' (not asked) | 'yes' | 'skip'. `runId`/`position` come from the queue call. */
function renderBatch(el, body, repos, { listOpen }) {
  const batchId = newBatchId();
  const m = {
    rows: repos.map((r) => ({ ...r, ticked: true, answer: 'pending', runId: '', position: 0 })),
    mode: 'choose',            // choose | stepping | done
    current: -1,
    filter: '',                // '' | 'queued' | 'skipped' | 'notasked' (which rows the summary counts opened)
    listOpen,
    note: '',
  };
  const total = () => m.rows.filter((r) => r.ticked).length;
  const lineOf = (r) => state.firstIndex?.lines?.[r.slug];
  const started = (r) => { const l = lineOf(r); return !!l && l.state !== 'waiting'; };
  const pending = () => m.rows.filter((r) => r.ticked && r.answer === 'pending');
  const wordOf = (r) => {
    const l = lineOf(r);
    if (r.answer === 'skip') return 'skipped';
    if (r.answer === 'pending') return m.mode === 'stepping' && m.rows[m.current] === r ? 'asking now' : 'not asked yet';
    if (!l || l.state === 'waiting') return `yes · queued ${r.position ? ordinal(r.position) : ''}`.trim();
    if (l.state === 'indexing') return 'indexing';
    if (l.state === 'indexed') return `indexed ${String(l.finished_at || '').slice(5, 16).replace('T', ' ')}`;
    return `failed${l.reason ? ` · ${l.reason}` : ''}`;
  };
  const glyph = (r) => {
    const w = wordOf(r);
    if (r.answer === 'skip') return '–';
    if (w === 'asking now') return '▸';
    if (r.answer === 'pending') return '○';
    if (w.startsWith('indexed')) return '✓';
    if (w.startsWith('failed')) return '✕';
    if (w === 'indexing' || w.startsWith('yes')) return '◔';
    return '·';
  };

  const queueRows = async (rows, path) => {
    const res = await queueFirstIndex({ slugs: rows.map((r) => r.slug), batchId, batchTotal: total(), path });
    const bySlug = new Map((res.queued || []).map((q) => [q.slug, q]));
    for (const r of rows) {
      const q = bySlug.get(r.slug);
      if (q) { r.answer = 'yes'; r.runId = q.run_id; r.position = q.position; }
    }
    if (res.refused?.length) m.note = res.refused.map((x) => `${x.slug}: ${x.reason}`).join(' · ');
    await refreshFirstIndex();
  };

  const nextPending = (from) => {
    const idx = m.rows.findIndex((r, i) => i > from && r.ticked && r.answer === 'pending');
    return idx;
  };
  const advance = () => {
    let i = nextPending(m.current);
    if (i < 0) i = m.rows.findIndex((r) => r.ticked && r.answer === 'pending');
    if (i >= 0) { m.current = i; m.mode = 'stepping'; } else { m.mode = 'done'; m.current = -1; }
  };

  const stepHtml = () => {
    const r = m.rows[m.current];
    const asked = m.rows.filter((x) => x.ticked && x.answer !== 'pending').length;
    const k = asked + 1;
    const left = pending();
    const showRemaining = k >= 2 && left.length >= 1;
    return `<div data-step class="rounded-sm border border-rule p-s2">
      <div data-step-title class="font-heading text-ink">${k} of ${total()} · ${esc(r.name)}</div>
      ${figuresHtml(r)}
      <div class="mt-s2 flex flex-wrap items-center gap-s3">
        <button type="button" data-step-yes class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-ink">Index ${esc(r.name)}</button>
        <button type="button" data-step-skip class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-ink">Skip</button>
        ${showRemaining ? `<button type="button" data-step-remaining title="${esc(totalsLine(left))}"
          class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-ink">Index the remaining ${left.length}</button>` : ''}
        <button type="button" data-step-skip-rest class="cursor-pointer border-0 bg-transparent p-0 text-ink-muted underline">Skip the rest</button>
      </div></div>`;
  };

  const summaryHtml = () => {
    const q = m.rows.filter((r) => r.answer === 'yes');
    const s = m.rows.filter((r) => r.answer === 'skip');
    const n = m.rows.filter((r) => r.ticked && r.answer === 'pending');
    const btn = (key, label, count) => `<button type="button" data-summary="${key}" ${count ? '' : 'disabled'}
      class="cursor-pointer border-0 bg-transparent p-0 ${count ? 'text-ink underline decoration-dotted' : 'text-ink-muted'}">${count} ${label}</button>`;
    return `<div data-summary-line class="mt-s2 text-ink">${btn('queued', 'queued', q.length)} · ${btn('skipped', 'skipped', s.length)} · ${btn('notasked', 'not asked', n.length)}
      ${n.length ? `<button type="button" data-resume class="ml-s2 cursor-pointer border-0 bg-transparent p-0 text-accent-ink underline">Ask about the ${n.length} ›</button>` : ''}</div>`;
  };

  const listRows = () => m.rows.filter((r) => {
    if (!m.filter) return true;
    if (m.filter === 'queued') return r.answer === 'yes';
    if (m.filter === 'skipped') return r.answer === 'skip';
    return r.ticked && r.answer === 'pending';
  });

  const render = () => {
    const ticked = m.rows.filter((r) => r.ticked);
    const remembered = rememberedPath();
    const choose = m.mode === 'choose';
    const rowHtml = listRows().map((r) => {
      const i = m.rows.indexOf(r);
      if (choose) {
        return `<li data-row="${esc(r.slug)}"><label class="flex items-center gap-s2"><input type="checkbox" data-tick="${i}" ${r.ticked ? 'checked' : ''}>
          <span>${esc(r.name)}</span><span class="text-ink-muted">${r.measured ? `${num(r.files)} files · ${fmtBytes(r.text_bytes)}` : 'not measured'}</span></label></li>`;
      }
      const inert = r.answer === 'yes' && started(r);
      return `<li data-row="${esc(r.slug)}" data-row-state="${esc(wordOf(r))}"><${inert ? 'div' : 'button type="button"'} ${inert ? '' : `data-row-press="${i}"`}
        class="flex w-full items-center gap-s2 border-0 bg-transparent p-0 text-left ${inert ? '' : 'cursor-pointer'} text-ink">
        <span class="font-glyph" aria-hidden="true">${glyph(r)}</span><span>${esc(r.name)}</span><span data-row-word class="text-ink-muted">${esc(wordOf(r))}</span></${inert ? 'div' : 'button'}></li>`;
    }).join('');
    const top = choose
      ? `<div data-ask="batch">
          <div data-totals class="text-ink">${esc(totalsLine(ticked))}</div>
          <div class="mt-s3 flex flex-wrap items-center gap-s3">
            <button type="button" data-path="all" ${remembered === 'all' ? 'data-default' : ''} ${ticked.length ? '' : 'disabled'}
              class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-ink">Index all ${ticked.length}</button>
            <button type="button" data-path="each" ${remembered === 'each' ? 'data-default' : ''} ${ticked.length ? '' : 'disabled'}
              class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-ink">Ask me for each ›</button>
            <button type="button" data-toggle-list class="cursor-pointer border-0 bg-transparent p-0 text-ink-muted underline">${m.listOpen ? 'hide' : 'show'} the ${m.rows.length} ›</button>
          </div></div>`
      : m.mode === 'stepping' ? stepHtml() : summaryHtml();
    body.innerHTML = `${top}
      ${m.note ? `<p data-ask-note class="mt-s2 text-state-warn">${esc(m.note)}</p>` : ''}
      ${(choose && !m.listOpen) ? '' : `<ul data-ask-list class="m-0 mt-s3 list-none p-0 text-caveat">${rowHtml}</ul>`}
`;
    bind();
    if (choose) body.querySelector('[data-default]')?.focus();
  };

  const bind = () => {
    body.querySelectorAll('[data-tick]').forEach((c) => c.addEventListener('change', () => {
      m.rows[Number(c.dataset.tick)].ticked = c.checked; render();
    }));
    body.querySelector('[data-toggle-list]')?.addEventListener('click', () => { m.listOpen = !m.listOpen; render(); });
    body.querySelector('[data-path="all"]')?.addEventListener('click', async () => {
      rememberPath('all'); m.mode = 'done'; m.listOpen = true;
      try { await queueRows(m.rows.filter((r) => r.ticked), 'all'); } catch (err) { m.note = `Not queued: ${err.message}`; m.mode = 'choose'; }
      render();
    });
    body.querySelector('[data-path="each"]')?.addEventListener('click', () => {
      rememberPath('each'); m.listOpen = true; m.mode = 'stepping'; m.current = m.rows.findIndex((r) => r.ticked); render();
    });
    body.querySelector('[data-step-yes]')?.addEventListener('click', async () => {
      const r = m.rows[m.current];
      try { await queueRows([r], 'each'); } catch (err) { m.note = `Not queued: ${err.message}`; render(); return; }
      if (r.answer !== 'yes') { render(); return; }
      advance(); render();
    });
    body.querySelector('[data-step-skip]')?.addEventListener('click', () => { m.rows[m.current].answer = 'skip'; advance(); render(); });
    body.querySelector('[data-step-remaining]')?.addEventListener('click', async () => {
      try { await queueRows(pending(), 'each'); } catch (err) { m.note = `Not queued: ${err.message}`; }
      m.mode = 'done'; m.current = -1; render();
    });
    body.querySelector('[data-step-skip-rest]')?.addEventListener('click', () => { m.mode = 'done'; m.current = -1; render(); });
    body.querySelectorAll('[data-summary]').forEach((b) => b.addEventListener('click', () => {
      m.filter = m.filter === b.dataset.summary ? '' : b.dataset.summary; render();
    }));
    body.querySelector('[data-resume]')?.addEventListener('click', () => {
      m.filter = ''; m.mode = 'stepping'; m.current = m.rows.findIndex((r) => r.ticked && r.answer === 'pending'); render();
    });
    body.querySelectorAll('[data-row-press]').forEach((b) => b.addEventListener('click', async () => {
      const r = m.rows[Number(b.dataset.rowPress)];
      if (r.answer === 'pending') { m.mode = 'stepping'; m.current = m.rows.indexOf(r); render(); return; }
      if (r.answer === 'skip') {                       // a skip changed to a yes: it names this repository
        try { await queueRows([r], 'each'); } catch (err) { m.note = `Not queued: ${err.message}`; }
        render(); return;
      }
      if (started(r)) return;
      try { await withdrawFirstIndex(r.runId); r.answer = 'skip'; r.runId = ''; r.position = 0; m.note = ''; }
      catch { m.note = `${r.name} has already started indexing.`; }
      await refreshFirstIndex();
      render();
    }));
  };

  // The list is the record: words follow the run rows while the dialog is open, and stop when it is closed.
  const unsub = subscribeFirstIndex(() => {
    if (!el.isConnected) { unsub(); return; }
    if (m.mode !== 'choose') render();
  });
  render();
}

export { INDEX_IT_WORD };
