/* Rebuild a repository's chat index from scratch (BRIEF-REINDEX-FROM-SCRATCH.md, R2): the ask, progress, accept (the
 * held first rebuild), undo and discard, behind one door in the repository header.
 *
 * Admins only (Q3): every call carries the admin token the Admin pages keep (`re_admin_token`), and a signed-in admin
 * needs none. The cue is drawn only after the progress read succeeds, so a person who is not an admin sees nothing.
 *
 * Nothing here is assumed: every state word comes from GET /api/reindex/progress (the run row the server writes), and
 * the ask's figures from GET /api/reindex/ask, measured. Undo and discard ask first: discard drops tables for good.
 */
import { openDialog } from '/static/next/worklist.js';
import { state, esc } from '/static/next/app.js';
import { stateEntry } from '/static/next/glyphs.js';

const TOKEN_KEY = 're_admin_token';

/** The progress of each repository read so far, by slug. */
const progressBySlug = new Map();

function headers(json = false) {
  const t = (typeof sessionStorage !== 'undefined' && sessionStorage.getItem(TOKEN_KEY)) || '';
  return { ...(json ? { 'Content-Type': 'application/json' } : {}), ...(t ? { 'X-Admin-Token': t } : {}) };
}

async function call(method, url, body) {
  const res = await fetch(url, { method, headers: headers(body !== undefined),
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}) });
  let data = {};
  try { data = await res.json(); } catch { /* no body */ }
  if (!res.ok) {
    const err = new Error(data.detail || `HTTP ${res.status}`);
    err.status = res.status;
    throw err;
  }
  return data;
}

const ACTIVE = new Set(['queued', 'building', 'built', 'surveyed', 'pruning', 'list_pruned', 'swapping', 'swapped',
  'extending', 'list_extended', 'undo_pruning', 'undo_swapping', 'undo_swapped', 'undo_extending']);
const PHASE_WORDS = {
  queued: 'queued', building: 'building aside', built: 'built', surveyed: 'survey results updated',
  pruning: 'swapping', list_pruned: 'swapping', swapping: 'swapping', swapped: 'swapped', extending: 'finishing',
  list_extended: 'finishing', awaiting_accept: 'built, waiting for you to accept', done: 'rebuilt',
  rolled_back: 'rolled back', failed: 'failed', cancelled: 'cancelled before it swapped',
};

const g = (name) => `<span class="font-glyph" aria-hidden="true">${stateEntry(name).glyph}</span>`;
const day = (iso) => String(iso || '').slice(0, 10);
const btn = (attr, label, primary = false) => primary
  ? `<button type="button" ${attr} class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-accent-ink">${label}</button>`
  : `<button type="button" ${attr} class="cursor-pointer border-0 bg-transparent p-0 text-accent-ink underline">${label}</button>`;

/** The cue beside the index cue, from a progress read: a state word, and the door. '' when there is nothing to say. */
export function reindexCueHtml(pr) {
  if (!pr) return '';
  const open = btn('data-reindex-open', pr.run_id ? 'rebuild ›' : 'rebuild from scratch ›');
  if (!pr.run_id) return `<span data-reindex-cue>${open}</span>`;
  const phase = pr.phase || '';
  if (ACTIVE.has(phase)) {
    return `<span data-reindex-cue data-reindex-phase="${esc(phase)}" class="text-ink-muted">${g('running')} rebuild ${esc(PHASE_WORDS[phase] || phase)} ${btn('data-reindex-open', 'details ›')}</span>`;
  }
  if (phase === 'awaiting_accept') {
    return `<span data-reindex-cue data-reindex-phase="awaiting_accept" class="text-ink-muted">${g('running')} rebuild built · ${btn('data-reindex-open', 'accept ›')}</span>`;
  }
  if (phase === 'done' && pr.undo_available) {
    return `<span data-reindex-cue data-reindex-phase="done" class="text-state-ok">${g('answered')} rebuilt · undo until ${esc(day(pr.previous_until))} ${btn('data-reindex-open', 'manage ›')}</span>`;
  }
  if (phase === 'failed' || phase === 'rolled_back') {
    const why = (pr.outcomes || []).slice(-1)[0]?.words || pr.error || '';
    return `<span data-reindex-cue data-reindex-phase="${esc(phase)}" class="text-state-warn" title="${esc(why)}">${g('error')} rebuild ${esc(PHASE_WORDS[phase])} · ${open}</span>`;
  }
  return `<span data-reindex-cue>${open}</span>`;
}

/** Read the progress of `slug` and draw the cue in place. Nothing is drawn when the read is refused (not an admin). */
export async function refreshReindexCue(slug = state.selectedSlug) {
  if (!slug || typeof document === 'undefined') return null;
  let pr;
  try { pr = await call('GET', `/api/reindex/progress?slug=${encodeURIComponent(slug)}`); } catch { return null; }
  progressBySlug.set(slug, pr);
  const host = document.querySelector('[data-reindex-host]');
  if (host) host.innerHTML = reindexCueHtml(pr) ? ` · ${reindexCueHtml(pr)}` : '';
  return pr;
}

/** Wire the door in a header. Delegated, so a redrawn cue keeps working. */
const bound = new WeakSet();
export function bindReindexDoor(root) {
  if (!root || bound.has(root)) return;
  bound.add(root);
  root.addEventListener('click', (ev) => {
    if (!ev.target.closest('[data-reindex-open]')) return;
    openReindexDialog(state.selectedSlug);
  });
}

/* ── the dialog ─────────────────────────────────────────────────────────── */

function outcomesHtml(pr) {
  const rows = (pr.outcomes || []).map((o) => `<li data-reindex-step="${esc(o.phase)}" class="mt-[2px]">${esc(o.words)}</li>`).join('');
  return rows ? `<ol class="m-0 max-w-[65ch] text-caveat text-ink">${rows}</ol>` : '';
}

/** Open the dialog for `slug`: the ask when nothing is in flight, else what the rebuild is doing and what can be done. */
export async function openReindexDialog(slug) {
  // Wide: only the wide dialog is user-resizable, and every pane's width is (the project owner's rule).
  const el = openDialog('Rebuild the chat index', slug, { wide: true });
  const body = el.querySelector('#wl-detail-body');
  const close = () => el.querySelector('[data-act="close"]')?.click();
  let pr;
  try { pr = await call('GET', `/api/reindex/progress?slug=${encodeURIComponent(slug)}`); } catch (err) {
    body.innerHTML = `<p data-reindex-error class="text-state-warn">${err.status === 401 || err.status === 403
      ? 'Rebuilding is for admins: sign in as an admin, or enter the admin token in Admin → Feedback.'
      : `Could not read the rebuild's state: ${esc(err.message)}`}</p>`;
    return el;
  }
  const phase = pr.phase || '';
  if (pr.run_id && (ACTIVE.has(phase) || phase === 'awaiting_accept' || (phase === 'done' && pr.undo_available))) {
    renderState(el, body, slug, pr, close);
  } else {
    await renderAsk(el, body, slug, pr, close);
  }
  return el;
}

async function renderAsk(el, body, slug, pr, close) {
  let ask;
  try { ask = await call('GET', `/api/reindex/ask?slug=${encodeURIComponent(slug)}`); } catch (err) {
    body.innerHTML = `<p data-reindex-error class="text-state-warn">Could not read what a rebuild would do: ${esc(err.message)}</p>`;
    return;
  }
  const last = pr && pr.run_id && (pr.phase === 'failed' || pr.phase === 'rolled_back')
    ? `<p data-reindex-last class="text-caveat text-state-warn">The last rebuild ${esc(PHASE_WORDS[pr.phase])}: ${esc((pr.outcomes || []).slice(-1)[0]?.words || pr.error || '')}</p>` : '';
  if (ask.refused) {
    body.innerHTML = `${last}<p data-reindex-refused class="text-ink">${esc(ask.refused)}</p>`;
    return;
  }
  const rows = (ask.collections || []).map((c) => `<li class="mt-[2px]"><span class="font-mono">${esc(c.collection)}</span> · ${
    c.chunks === null ? 'chunk count not read' : `${Number(c.chunks).toLocaleString()} chunk(s)`}</li>`).join('');
  body.innerHTML = `<div data-reindex-ask class="max-w-[65ch] text-ink">
    ${last}
    <p class="m-0">A fresh download and embed of ${esc(ask.name)}, built alongside the live index: chat keeps answering
      from the live one until the swap. The collection types are planned from the repository as it is today.</p>
    <ul data-reindex-collections class="mt-s3 text-caveat">${rows}</ul>
    <p data-reindex-needs class="mt-s3 text-caveat">It ${esc(ask.needs_words)}.</p>
    <p class="text-caveat text-ink-muted">The previous index is kept ${esc(String(ask.keep_days))} days so the rebuild can be
      undone. ${esc(ask.survey_words)}.</p>
    ${ask.first ? '<p data-reindex-first class="text-caveat text-ink">This is the first rebuild: it is held after building, for you to accept before it swaps, and it is timed.</p>' : ''}
    <div class="mt-s3 flex items-center gap-s3">
      ${btn('data-reindex-yes', 'Rebuild', true)}
      ${btn('data-reindex-no', 'not now')}
      <span data-reindex-note class="text-caveat text-ink-muted"></span>
    </div></div>`;
  body.querySelector('[data-reindex-no]').addEventListener('click', close);
  body.querySelector('[data-reindex-yes]').addEventListener('click', async (ev) => {
    const b = ev.currentTarget;
    const note = body.querySelector('[data-reindex-note]');
    b.disabled = true;
    note.textContent = 'Queueing…';
    try {
      const out = await call('POST', '/api/reindex/queue', { slug });
      note.textContent = out.held ? 'Queued. It will wait for you to accept once it is built.' : 'Queued. A worker builds it aside.';
      await refreshReindexCue(slug);
    } catch (err) {
      b.disabled = false;
      note.textContent = `Not queued: ${err.message}`;
    }
  });
}

function renderState(el, body, slug, pr, close) {
  const phase = pr.phase || '';
  const actions = [];
  if (phase === 'awaiting_accept') {
    actions.push(btn('data-reindex-accept', 'Accept and swap', true));
    actions.push(btn('data-reindex-cancel', 'Cancel the rebuild'));
  }
  if (phase === 'done' && pr.undo_available) {
    actions.push(btn('data-reindex-undo', 'Undo the rebuild'));
    actions.push(btn('data-reindex-discard', 'Discard the previous index'));
  }
  body.innerHTML = `<div data-reindex-state="${esc(phase)}" class="max-w-[65ch] text-ink">
    <p class="m-0">${esc(PHASE_WORDS[phase] || phase)}${phase === 'done' && pr.previous_until
      ? ` · undo available until ${esc(day(pr.previous_until))}` : ''}</p>
    ${outcomesHtml(pr)}
    ${pr.needs_words ? `<p class="text-caveat text-ink-muted">It ${esc(pr.needs_words)}.</p>` : ''}
    <div class="mt-s3 flex items-center gap-s3">${actions.join('')}${btn('data-reindex-close', 'close')}
      <span data-reindex-note class="text-caveat text-ink-muted"></span></div>
    <div data-reindex-confirm class="mt-s3"></div></div>`;
  const note = body.querySelector('[data-reindex-note]');
  const confirmBox = body.querySelector('[data-reindex-confirm]');
  body.querySelector('[data-reindex-close]').addEventListener('click', close);
  const act = async (b, method, url, done) => {
    b.disabled = true;
    note.textContent = 'Working…';
    try {
      await call(method, url);
      note.textContent = done;
      await refreshReindexCue(slug);
    } catch (err) {
      b.disabled = false;
      note.textContent = `Not done: ${err.message}`;
    }
  };
  // Undo and discard ask first, in place: a second, explicit button, never a single press.
  const ask = (sentence, label, run) => {
    confirmBox.innerHTML = `<p data-reindex-ask-first class="text-caveat text-ink">${esc(sentence)}</p>
      <div class="flex items-center gap-s3">${btn('data-reindex-confirm-yes', esc(label), true)}${btn('data-reindex-confirm-no', 'cancel')}</div>`;
    confirmBox.querySelector('[data-reindex-confirm-no]').addEventListener('click', () => { confirmBox.innerHTML = ''; });
    confirmBox.querySelector('[data-reindex-confirm-yes]').addEventListener('click', (ev) => run(ev.currentTarget));
  };
  body.querySelector('[data-reindex-accept]')?.addEventListener('click', (ev) => act(ev.currentTarget, 'POST',
    `/api/reindex/${encodeURIComponent(pr.run_id)}/accept`, 'Accepted: the survey writes and the swap are queued.'));
  body.querySelector('[data-reindex-cancel]')?.addEventListener('click', () => ask(
    'Cancel drops what this rebuild built. Chat keeps the index it has now, which was never changed. The next rebuild will be held for you again.',
    'Yes, cancel it', (b) => act(b, 'POST', `/api/reindex/${encodeURIComponent(pr.run_id)}/cancel`,
      'Cancelled: what it built was dropped; the live index is unchanged.')));
  body.querySelector('[data-reindex-undo]')?.addEventListener('click', () => ask(
    'Undo puts the previous index back for chat and moves the votes back. The survey results stay as the rebuild left them.',
    'Yes, undo', (b) => act(b, 'POST', `/api/reindex/${encodeURIComponent(slug)}/undo`, 'Undone: the previous index is live again.')));
  body.querySelector('[data-reindex-discard]')?.addEventListener('click', () => {
    const n = (pr.previous_tables || []).length;
    ask(`Discard drops ${n} table${n === 1 ? '' : 's'} (${(pr.previous_tables || []).join(', ')}); undo will no longer be possible.`,
      'Yes, discard', (b) => act(b, 'POST', `/api/reindex/${encodeURIComponent(slug)}/discard-previous`,
        'Discarded: the previous index is dropped.'));
  });
}

export function _progressFor(slug) { return progressBySlug.get(slug); }
