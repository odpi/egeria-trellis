/* Admin → Publish Queue (PI-131): the Egeria outbox, row by row.
 *
 * The activity log says THAT a publish was incomplete; this is which elements, how many attempts each took and what
 * Egeria said (routes/outbox.py). Counts are over the whole table, never the filtered page, so a filter cannot read as
 * "nothing is stuck".
 *
 * RETRY. Only a DEAD row can be retried (the server refuses any other state), and NEVER a destructive one: a row whose
 * kind archives, deletes or detaches something in Egeria is not retried from here, because a retry of it is itself a
 * destructive write (row 69822, 2026-10-06). The server marks those rows `destructive`; this pane draws no Retry on
 * them and says why, and the route would refuse it with a 409 anyway. */
import { listOutbox, retryOutboxRow, getAdminStatus, adminLocked } from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { ago } from '/static/next/format.js';

const STATES = ['pending', 'running', 'failed', 'dead', 'done', 'superseded'];
const CUE = {
  pending: ['◔', 'text-ink-muted', 'waiting'], running: ['◔', 'text-accent-ink', 'running'],
  failed: ['↻', 'text-state-warn', 'retrying'], dead: ['✗', 'text-state-warn', 'dead'],
  done: ['✓', 'text-state-ok', 'done'], superseded: ['–', 'text-ink-muted', 'superseded'],
};

let _host = null;
let _filter = '';
let _data = null;
/** true when this caller may not retry (see `adminLocked`): a retry re-queues an Egeria write. */
let _locked = false;
let _lockRead = false;

/** A Retry is drawn only for a dead row of a kind that does not archive, delete or detach, and that the server
 *  does not mark `not_retryable` (a retry would fail the same way). Exported for the tests. */
export function canRetry(row) {
  return row.status === 'dead' && !row.destructive && !row.not_retryable;
}

function stateCell(r, max) {
  const [glyph, tone, word] = CUE[r.status] || ['·', 'text-ink-muted', r.status || 'unknown'];
  // A success with no retries is "first try": "done 0/8" reads as zero attempts on a write that went through.
  const tries = r.status === 'done' && !(Number(r.attempts) > 0) ? 'first try'
    : max ? `${r.attempts}/${max}` : `${r.attempts}`;
  return `<span class="${tone}">${glyph} ${esc(word)}</span> <span class="tnum text-provenance text-ink-muted">${esc(tries)}</span>`;
}

function whenCell(r) {
  if (r.status === 'done' && r.completed_at) return `done ${esc(ago(r.completed_at))}`;
  if (r.status === 'failed' && r.next_attempt_at) return `next try ${esc(ago(r.next_attempt_at))}`;
  return r.created_at ? `queued ${esc(ago(r.created_at))}` : 'not recorded';
}

function actionCell(r) {
  if (canRetry(r)) {
    const locked = _locked;
    return `<button type="button" data-outbox-retry="${esc(String(r.id))}" ${locked ? 'disabled data-admin-only' : ''}
      title="${locked ? 'Admin only: re-queueing a write to Egeria needs the admin credential (Admin → Feedback)' : 'Return this row to the queue; the drain will try it again'}"
      class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink
        ${locked ? 'cursor-default opacity-50' : 'cursor-pointer hover:border-accent'}">Retry</button>
      ${locked ? '<span class="text-provenance text-ink-muted"> · admin only</span>' : ''}
      <div data-outbox-retry-state="${esc(String(r.id))}" class="mt-[2px] text-provenance text-state-warn"></div>`;
  }
  if (r.destructive && r.status === 'dead') {
    return `<span data-outbox-no-retry title="This write archives, deletes or detaches in Egeria. A retry of it is itself a destructive write, so it is never retried from here. Press the original control again."
      class="text-provenance text-ink-muted">not retried · destructive</span>`;
  }
  if (r.not_retryable && r.status === 'dead') {
    return `<span data-outbox-no-retry title="${esc(r.not_retryable)}"
      class="text-provenance text-ink-muted">not retried · fix the value</span>`;
  }
  return '';
}

function rowHtml(r, max) {
  return `<tr class="border-b border-rule align-top" data-outbox-row="${esc(String(r.id))}" data-outbox-status="${esc(r.status || '')}">
    <td class="py-s2 pr-s3 tnum text-provenance text-ink-muted">${esc(String(r.id))}</td>
    <td class="py-s2 pr-s3 text-caveat text-ink">${esc(r.entity_type || '')}/${esc(r.entity_slug || '')}</td>
    <td class="py-s2 pr-s3 text-caveat text-ink-muted">${esc(r.element_kind || '')}${r.destructive ? ' <span class="text-state-warn" title="archives, deletes or detaches in Egeria">· destructive</span>' : ''}</td>
    <td class="py-s2 pr-s3 text-caveat">${stateCell(r, max)}</td>
    <td class="py-s2 pr-s3 text-provenance text-ink-muted">${whenCell(r)}</td>
    <td class="py-s2 pr-s3 text-provenance">
      <span class="font-mono text-ink-muted" title="${esc(r.qualified_name || '')}">${esc(String(r.qualified_name || '').slice(0, 48))}</span>
      ${r.last_error ? `<details class="mt-[2px]"><summary class="cursor-pointer text-state-warn">${esc(String(r.last_error).slice(0, 90))}</summary>
        <div data-outbox-error class="mt-[2px] whitespace-pre-wrap font-mono text-ink">${esc(r.last_error)}</div></details>` : ''}
    </td>
    <td class="py-s2">${actionCell(r)}</td>
  </tr>`;
}

function render() {
  if (!_host) return;
  const d = _data;
  const counts = d.counts || {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  _host.innerHTML = `
    <div class="mb-s2 flex items-center justify-between">
      <h3 class="m-0 font-heading text-name font-normal text-ink">📤 Publish Queue</h3>
      <button type="button" data-outbox-refresh class="cursor-pointer rounded-sm border border-rule-strong px-2 py-[2px] text-caveat text-ink hover:border-accent">Refresh</button>
    </div>
    <p class="mb-s3 max-w-[70ch] text-caveat text-ink-muted">Every element write queued for Egeria, with its attempts and Egeria's own
      answer. A dead row can be returned to the queue once its cause is fixed. A write that archives, deletes or detaches is never
      retried from here.</p>
    <div class="mb-s3 flex flex-wrap items-center gap-[6px]" data-outbox-chips>
      <button type="button" data-outbox-filter="" aria-pressed="${_filter === '' ? 'true' : 'false'}"
        class="cursor-pointer rounded-sm border bg-transparent px-2 py-[2px] text-caveat ${_filter === '' ? 'border-accent text-accent-ink' : 'border-rule text-ink-muted hover:text-ink'}">all <span class="tnum">${total}</span></button>
      ${STATES.map((s) => `<button type="button" data-outbox-filter="${s}" aria-pressed="${_filter === s ? 'true' : 'false'}"
        class="cursor-pointer rounded-sm border bg-transparent px-2 py-[2px] text-caveat ${_filter === s ? 'border-accent text-accent-ink' : 'border-rule text-ink-muted hover:text-ink'}
          ${s === 'dead' && counts.dead ? 'text-state-warn' : ''}">${CUE[s][0]} ${s} <span class="tnum">${counts[s] || 0}</span></button>`).join('')}
    </div>
    ${d.rows.length ? `<table class="w-full text-left">
      <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
        <th class="pb-s2 pr-s3 font-normal">#</th><th class="pb-s2 pr-s3 font-normal">Resource</th><th class="pb-s2 pr-s3 font-normal">Kind</th>
        <th class="pb-s2 pr-s3 font-normal">State</th><th class="pb-s2 pr-s3 font-normal">When</th><th class="pb-s2 pr-s3 font-normal">Element and Egeria's answer</th><th class="pb-s2 font-normal"></th>
      </tr></thead><tbody>${d.rows.map((r) => rowHtml(r, d.max_attempts)).join('')}</tbody></table>`
    : `<p data-outbox-empty class="text-answer text-ink">${total === 0
      ? 'The queue is empty: nothing has been queued for Egeria.'
      : `No ${_filter ? esc(_filter) : ''} rows. ${total} row(s) exist in other states.`}</p>`}`;
  bind();
}

async function load() {
  try {
    if (!_lockRead) { _locked = await getAdminStatus().then(adminLocked, () => false); _lockRead = true; }
    _data = await listOutbox({ status: _filter });
  } catch (err) {
    _host.innerHTML = `<p class="max-w-[70ch] text-answer text-state-warn">Could not read the Publish Queue: ${esc(err.message)}</p>`;
    return;
  }
  render();
}

async function retry(btn) {
  if (btn.disabled) return;
  const id = btn.dataset.outboxRetry;
  const st = [..._host.querySelectorAll('[data-outbox-retry-state]')].find((e) => e.dataset.outboxRetryState === id);
  btn.disabled = true; btn.textContent = 'Retrying…';
  try {
    await retryOutboxRow(id);
    await load();
  } catch (err) {
    btn.disabled = false; btn.textContent = 'Retry';
    if (st) st.textContent = err.status === 409 ? err.message : `Not retried: ${err.message}`;
  }
}

function bind() {
  _host.querySelector('[data-outbox-refresh]')?.addEventListener('click', load);
  _host.querySelectorAll('[data-outbox-filter]').forEach((b) => b.addEventListener('click', () => {
    _filter = b.dataset.outboxFilter;
    load();
  }));
  _host.querySelectorAll('[data-outbox-retry]').forEach((b) => b.addEventListener('click', () => retry(b)));
}

export async function renderOutbox(host) {
  _host = host;
  _filter = '';
  _lockRead = false;
  host.innerHTML = '<p class="text-answer text-ink-muted">reading…</p>';
  await load();
}
