/* Admin → Prefect — flow-run status/cancel for locally-dispatched
 * (`executes_at: prefect`) survey steps only.
 *
 * Full port of classic's `loadAdminPrefectPanel`/`renderAdminPrefectPanel`.
 * Does NOT cover `executes_at: egeria` steps, which Egeria coordinates
 * itself — see web/routes/prefect_status.py's module docstring. Cancel is
 * kept (not deferred): it is a single, reversible, already-idempotent POST
 * against a specific flow-run id, the same shape as Automate's schedule
 * delete that item 4 already ported — not a reconciliation/config-mutation
 * surface like the six panes this item defers.
 */
import { getPrefectStatus, listPrefectFlowRuns, cancelPrefectFlowRun } from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { inPageConfirm } from '/static/next/confirm-dialog.js';

const STATE_TONE = {
  COMPLETED: 'text-state-ok', RUNNING: 'text-accent-ink',
  SCHEDULED: 'text-ink-muted', PENDING: 'text-ink-muted',
  FAILED: 'text-state-warn', CRASHED: 'text-state-warn',
  CANCELLED: 'text-state-warn', CANCELLING: 'text-state-warn',
};
const ACTIVE = new Set(['RUNNING', 'PENDING', 'SCHEDULED', 'CANCELLING']);

let _host = null;

async function reload() {
  if (!_host) return;
  let status;
  let runsBody;
  try {
    [status, runsBody] = await Promise.all([getPrefectStatus(), listPrefectFlowRuns(50)]);
  } catch (err) {
    _host.innerHTML = `<p class="max-w-[70ch] text-answer text-state-warn">Could not load Prefect status: ${esc(err.message)}</p>`;
    return;
  }

  const banner = status.reachable
    ? `<p class="flex items-center gap-[6px] text-caveat text-state-ok">
         <span class="inline-block h-[8px] w-[8px] rounded-full bg-state-ok"></span>
         Reachable at <a href="${esc(status.api_url)}" target="_blank" rel="noopener" class="underline">${esc(status.api_url)}</a>
         · <a href="${esc(status.ui_url)}" target="_blank" rel="noopener" class="underline">Open Prefect UI ↗</a> for full per-task logs</p>`
    : `<p class="flex items-center gap-[6px] text-caveat text-state-warn">
         <span class="inline-block h-[8px] w-[8px] rounded-full bg-state-warn"></span>
         Not reachable at ${esc(status.api_url)} — steps run locally, exactly as if Prefect were disabled.
         ${status.error ? `<span class="text-ink-muted">(${esc(status.error)})</span>` : ''}</p>`;

  const rows = (runsBody.flow_runs || []).map((r) => {
    const tone = STATE_TONE[r.state] || 'text-ink-muted';
    const cancelBtn = ACTIVE.has(r.state)
      ? `<button type="button" data-cancel="${esc(r.id)}"
          class="cursor-pointer rounded-sm border border-state-warn bg-transparent px-2 py-[2px] text-caveat text-state-warn">⏹ Cancel</button>`
      : '';
    return `<tr class="border-b border-rule hover:bg-paper-surface">
      <td class="py-s2 pr-s3 text-caveat text-ink">${esc(r.slug || '—')}</td>
      <td class="py-s2 pr-s3 font-mono text-caveat text-accent-ink">${esc(r.step || '—')}</td>
      <td class="py-s2 pr-s3 text-caveat ${tone}">${esc(r.state)}</td>
      <td class="py-s2 pr-s3 text-provenance text-ink-muted">${esc(r.start_time || r.created || '—')}</td>
      <td class="py-s2 pr-s3 text-provenance text-ink-muted">${esc(r.end_time || '—')}</td>
      <td class="py-s2 text-right">${cancelBtn}</td>
    </tr>`;
  }).join('');

  _host.innerHTML = `
    <div class="mb-s2 flex items-center justify-between">
      <h3 class="m-0 font-heading text-name font-normal text-ink">⚡ Prefect</h3>
      <button type="button" data-refresh
        class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">🔄 Refresh</button>
    </div>
    <p class="mb-s2 max-w-[65ch] text-caveat text-ink-muted">Flow-run status for locally-dispatched
      survey steps (<code>executes_at: prefect</code>) — not <code>executes_at: egeria</code> steps,
      which Egeria coordinates itself.</p>
    ${banner}
    ${rows ? `<div class="mt-s3 max-h-[42vh] overflow-auto rounded-sm border border-rule">
      <table class="w-full text-left">
        <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
          <th class="px-s2 py-s2 font-normal">Resource</th>
          <th class="px-s2 py-s2 font-normal">Step</th>
          <th class="px-s2 py-s2 font-normal">State</th>
          <th class="px-s2 py-s2 font-normal">Started</th>
          <th class="px-s2 py-s2 font-normal">Ended</th>
          <th class="px-s2 py-s2 font-normal"></th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>` : '<p class="py-s4 text-center text-caveat text-ink-muted">No flow runs yet — nothing has run via executes_at: prefect.</p>'}`;

  bind();
}

function bind() {
  if (!_host) return;
  const refresh = _host.querySelector('[data-refresh]');
  if (refresh) refresh.addEventListener('click', () => reload());
  _host.querySelectorAll('[data-cancel]').forEach((b) => b.addEventListener('click', async () => {
    if (!(await inPageConfirm({ message: 'Cancel this flow run? The step it was running will stop, not silently retry locally.', confirmLabel: 'Confirm' }))) return;
    b.disabled = true;
    b.textContent = 'Cancelling…';
    try {
      await cancelPrefectFlowRun(b.dataset.cancel);
      setTimeout(reload, 1000); // give Prefect a moment to transition state
    } catch (err) {
      b.disabled = false;
      b.textContent = `Not cancelled: ${err.message}`;
    }
  }));
}

export async function renderPrefect(host) {
  _host = host;
  await reload();
}
