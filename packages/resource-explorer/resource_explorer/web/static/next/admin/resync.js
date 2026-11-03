/* Admin → Egeria Alignment ("Resync") — global drift reconciliation.
 *
 * Port of classic's `loadAdminResyncPanel`/`renderAdminResyncPanel`/
 * `_applyResync` (index.html), against `resource_explorer/egeria_resync.py`'s
 * twelve scanners and nine repair actions — the backend is complete; this is
 * a UI-only port. See SPEC-ADMIN-THE-FOUR-GAPS.md §1 and
 * RECONCILE-ADMIN-IMPLEMENTED.md.
 *
 * This is Resync, NOT Repair (admin/repair.js) — drift across the whole
 * store, not a correction to one repository. Classic has these as two
 * screens for a reason; see repair.js's own header comment.
 *
 * THE CORE RULE (do not flatten `Finding` into "a row with a button"):
 * every finding falls into exactly one of three shapes, rendered
 * differently on purpose —
 *
 *   1. Already scheduled  — `finding.scheduled` is true (backend-computed
 *      from SAFE_SCHEDULED_STEPS, egeria_resync.py's `Finding.as_dict()`).
 *      Runs unattended every ~600s already; the row reports current STATE
 *      (how many are drifted right now) and offers "run now", not a
 *      "here's a button to fix something broken" framing.
 *   2. Repairable, not scheduled — a checkbox + Apply button, and the
 *      confirmation before applying NAMES THE BLAST RADIUS: what it
 *      touches, how many (from the finding's own count), and whether it
 *      reverses. `clear_stale_investigations` gets its own explicit,
 *      unmissable warning — see BLAST_RADIUS below and the module comment
 *      above SAFE_SCHEDULED_STEPS in egeria_resync.py for why.
 *   3. `repair_step: ""` — no button, ever. A report. When `needs_decision`
 *      is true it is rendered as an open QUESTION (SPEC-ACTIONABLE-AND-
 *      HONEST.md's "a judgement is not a task" — the destination is a
 *      person's call, not an automatic fix), not an inert list item.
 *
 * `finding.scheduled` (added alongside the existing `finding.expensive`)
 * is the single source of truth for "is this one of SAFE_SCHEDULED_STEPS" —
 * this file used to keep its own hand-maintained copy (`SCHEDULED_STEPS`)
 * that had to be updated by hand whenever the Python set changed; see
 * RESYNC-STATUS-ROUTE-IMPLEMENTED.md for the history.
 * The scheduler's own run history (last_run_at/consecutive_failures/etc.)
 * comes from `/api/egeria/resync/scheduler-status` (egeria_resync.get_status()),
 * modeled on bootstrap.py's `/status` route — see loadStatus()/render()
 * below for how a non-zero consecutive_failures earns a flag.
 */
import { getPrivateZone, getResyncScan, getResyncStatus, applyResyncSteps } from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { inPageConfirm, inPageNotice } from '/static/next/confirm-dialog.js';

const SCHEDULE_INTERVAL_TEXT = 'every ~600s';

/** What each repairable step DOES, for the pre-apply confirmation — one
 *  sentence cannot honestly describe two different repairs (one clears a
 *  local record, another writes a new asset into Egeria), so each step gets
 *  its own line naming what it touches, how many (from the finding this
 *  step came from), and whether it reverses. `clear_stale_investigations`
 *  is deliberately the most explicit: egeria_resync.py's own comment above
 *  SAFE_SCHEDULED_STEPS calls an unlabelled button for this "the most
 *  dangerous control in the product" — `investigations.egeria_project_guid`
 *  can hold a GUID a person deliberately bound to a real Egeria Project,
 *  not only one this app created, and clearing it un-does that binding. */
const BLAST_RADIUS = {
  clear_stale_assets: (n) =>
    `Clears the local "has an Egeria asset" pointer on ${n} record(s) whose asset no `
    + `longer resolves in Egeria. Local only — writes nothing to Egeria, and re-publishing `
    + `later restores the pointer. Reversible.`,
  clear_orphan_publish_claims: (n) =>
    `Clears ${n} local publish-claim record(s) that point at a survey report Egeria no `
    + `longer has. Local only. Reversible — the claim is recreated the next time that repo publishes.`,
  flag_vanished_publishes: (n) =>
    `WRITES a flag (egeria_linkage_status) on ${n} record(s) whose published survey vanished `
    + `from Egeria — it does not delete anything. A false positive here costs one wrong badge `
    + `state until the next scan corrects it, never a decision silently unmade.`,
  flag_stale_dbfs_assets: (n) =>
    `WRITES a stale flag (egeria_linkage_status) on ${n} database/filesystem record(s) whose `
    + `asset no longer resolves in Egeria, and clears the flag on any that resolve again. It `
    + `never deletes the GUID and writes nothing to Egeria.`,
  clear_stale_investigations: (n) =>
    `⚠ Clears the Egeria Project/working-set binding on ${n} investigation(s)/working set(s) `
    + `whose target no longer resolves in Egeria. THIS CAN UNBIND A REAL EGERIA PROJECT A `
    + `PERSON DELIBERATELY CHOSE — nothing distinguishes a binding this app created from one `
    + `you bound by hand, and a transient Egeria outage can make a live binding look gone. `
    + `Not reversible by this app: you would need to re-bind it yourself.`,
  clear_stale_contexts: (n) =>
    `Clears ${n} entity→Egeria-Project context row(s) that decide whether a resource may `
    + `publish and what it attaches to, where the target Project no longer resolves. Same `
    + `binding risk as clear_stale_investigations above — a bound choice, not just a cached `
    + `pointer. Not reversible by this app.`,
  catalog_assets: (n) =>
    `CREATES an Egeria asset for ${n} repo(s) — publishes only the repository-health step, `
    + `no archives downloaded. Reversible in Egeria directly (the asset can be deleted there).`,
  republish_survey_results: (n) =>
    `Runs the FULL survey for ${n} repo(s) and publishes the results as a new SurveyReport. `
    + `DOWNLOADS REPOSITORY ARCHIVES — across a large corpus, expect minutes and real GitHub `
    + `API budget. Adds a new report; does not delete the existing asset.`,
  reauthor_survey_definitions: (n) =>
    `Re-authors ${n} Survey Definition(s) IN EGERIA by re-running their Dr.Egeria documents, `
    + `then reconciles their step links. Writes to shared Egeria and can take a few `
    + `minutes — leave the page open.`,
  relink_investigation_members: (n) =>
    `Attaches ${n} investigation member(s) to their Egeria assets. Additive — does not detach `
    + `or delete any existing link.`,
};

let _scan = null;
let _zone = null;
let _status = null;
let _busy = false;
let _host = null;

function itemsHtml(f) {
  if (!f.count) return '';
  const rows = (f.items || []).map((i) =>
    `<div class="font-mono text-provenance text-ink-muted">${
      esc(Object.entries(i).map(([k, v]) => `${k}=${v}`).join('  '))}</div>`).join('');
  return `<details class="mt-[6px]">
    <summary class="cursor-pointer select-none text-caveat text-ink-muted hover:text-ink">show ${f.count}</summary>
    <div class="mt-[4px] max-h-[30vh] space-y-[2px] overflow-auto rounded-sm border border-rule bg-paper-surface p-s2">
      ${rows}
      ${f.truncated ? `<div class="text-provenance text-ink-muted">… and ${f.truncated} more</div>` : ''}
    </div>
  </details>`;
}

/** Shape 1 — already scheduled. Reports STATE, offers "run now".
 *
 *  `status` is egeria_resync.get_status()'s response (or null if it could
 *  not be fetched). Per the design review: a consecutive-failure count
 *  earns a flag when non-zero, and is silent otherwise — a clean scheduler
 *  history adds no extra text to the row. `last_run_at` is shown alongside
 *  so a curator can tell recency at a glance, distinguishing "the scheduler
 *  ran and correctly found nothing" from "it hasn't run in days", which
 *  otherwise look like the identical clean row. */
function scheduledRowHtml(f, status) {
  const clean = f.count === 0;
  const failures = status ? (status.consecutive_failures || 0) : 0;
  const lastRunAt = status ? status.last_run_at : '';
  return `<div class="rounded-sm border border-rule p-s3" data-scheduled-row="${esc(f.repair_step)}">
    <div class="flex items-start justify-between gap-s3">
      <div class="min-w-0">
        <div class="flex items-center gap-[6px] text-answer text-ink">
          <span class="inline-block h-[8px] w-[8px] rounded-full ${clean ? 'bg-state-ok' : 'bg-state-warn'}"></span>
          ${esc(f.title)}
        </div>
        <div class="mt-[2px] text-caveat text-ink-muted">${esc(f.detail)}</div>
        <div class="mt-[4px] text-provenance text-ink-muted">
          Runs unattended ${SCHEDULE_INTERVAL_TEXT} — this reports what a pass would find right now
          (${f.count} currently), not something waiting on you to fix.
          ${lastRunAt ? ` Last run: ${esc(lastRunAt)}.` : ''}
        </div>
        ${failures > 0 ? `<div class="mt-[4px] text-caveat text-state-warn">
          ⚠ ${failures} consecutive failure(s)${status && status.last_error ? ` — last error: ${esc(status.last_error)}` : ''}
        </div>` : ''}
      </div>
      <button type="button" data-run-now="${esc(f.repair_step)}" ${clean ? 'disabled' : ''}
        class="shrink-0 cursor-pointer rounded-sm border px-2 py-[2px] text-caveat
          ${clean ? 'border-rule text-ink-muted opacity-50' : 'border-rule-strong text-ink hover:border-accent'}">
        ${clean ? 'Nothing to run' : 'Run now'}
      </button>
    </div>
    ${itemsHtml(f)}
  </div>`;
}

/** Shape 2 — repairable, not scheduled. Checkbox; batch-applied below. */
function repairableRowHtml(f) {
  return `<div class="rounded-sm border border-rule p-s3">
    <div class="flex items-start justify-between gap-s3">
      <div class="min-w-0">
        <div class="text-answer text-ink">${esc(f.title)} <span class="text-ink-muted">· ${f.count}</span></div>
        <div class="mt-[2px] text-caveat text-ink-muted">${esc(f.detail)}</div>
      </div>
      <label class="flex shrink-0 items-center gap-[6px] text-caveat text-ink"
        title="${f.expensive ? 'Writes to Egeria and can take minutes — ticked deliberately, not by default' : ''}">
        <input type="checkbox" data-resync-step="${esc(f.repair_step)}" ${f.expensive ? '' : 'checked'}>
        fix
        ${f.expensive ? '<span class="text-provenance uppercase tracking-caps text-state-warn">slow</span>' : ''}
      </label>
    </div>
    ${itemsHtml(f)}
  </div>`;
}

/** Shape 3 — repair_step === "". No button. A report, or (needs_decision)
 *  a question routed to a person rather than a fix routed to a click. */
function decisionRowHtml(f) {
  return `<div class="rounded-sm border border-rule p-s3">
    <div class="flex items-start justify-between gap-s3">
      <div class="min-w-0">
        <div class="text-answer text-ink">${esc(f.title)} <span class="text-ink-muted">· ${f.count}</span></div>
        <div class="mt-[2px] whitespace-pre-line text-caveat text-ink-muted">${esc(f.detail)}</div>
      </div>
      <span class="shrink-0 rounded-sm border border-rule-strong px-2 py-[2px] text-provenance uppercase tracking-caps text-state-warn"
        title="No single repair is correct here — this is a question for a person, not a fix RE can apply">
        ${f.needs_decision ? 'your call ›' : 'report'}
      </span>
    </div>
    ${itemsHtml(f)}
  </div>`;
}

// The start-up check over every zone RE stamps: one row per zone, a cue plus a short word, the sentence on hover.
// Full literal markup per verdict (no interpolated class names, so the Tailwind coverage check can read them).
const ZONE_CUE = {
  ok: '<span class="text-state-ok">✓</span>',
  warned: '<span class="text-state-warn">⚠</span>',
  blocked: '<span class="text-state-warn">⛔</span>',
  could_not_check: '<span class="text-state-warn">?</span>',
  unknown: '<span class="text-ink-muted">…</span>',
};
function zoneControlsHtml(rows) {
  if (!rows || !rows.length) return '';
  return `<ul class="mt-s2 list-none p-0 text-provenance">${rows.map((e) => {
    const cue = ZONE_CUE[e.verdict] || ZONE_CUE.unknown;
    const facts = e.control === 'present'
      ? `control present · lists ${esc((e.operations || []).join(', ') || 'no operation')} · DEFAULT ${e.default_list ? 'yes' : 'no'}`
      : `control ${esc(e.control)}`;
    const words = e.verdict === 'ok' ? ''
      : e.verdict === 'unknown' ? `· <span class="text-ink-muted">${esc(e.words || '')}</span>`
        : `· <span class="text-state-warn">${esc(e.words || '')}</span>`;
    return `<li title="${esc(e.words || '')}">${cue}
      <span class="font-mono">${esc(e.zone)}</span> · <span class="text-ink-muted">${facts}</span>
      ${words}</li>`;
  }).join('')}</ul>`;
}

function privateZoneHtml() {
  const z = _zone;
  if (!z) return '';
  const zone = esc(z.zone || 'private zone');
  if (z.enforced) {
    return `<div class="mb-s3 rounded-sm border border-rule bg-paper-surface p-s3">
      <div class="text-caveat text-state-ok">🔒 Private zone enforced</div>
      <div class="mt-[2px] text-provenance text-ink-muted">Personal and Experiment investigations
        publish to <span class="font-mono">${zone}</span>, readable only by their creator.</div>
      ${zoneControlsHtml(z.zone_controls)}
    </div>`;
  }
  const settling = (z.settling_seconds_remaining || 0) > 0;
  return `<div class="mb-s3 rounded-sm border border-rule bg-paper-surface p-s3">
    <div class="text-caveat text-state-warn">${settling ? '⏳ Private zone settling' : '⚠ Private investigations cannot publish'}</div>
    <div class="mt-[2px] text-caveat text-ink">${settling
      ? `The <span class="font-mono">${zone}</span> control exists but Egeria's security connector `
        + `has not reloaded it yet (about ${Math.ceil((z.settling_seconds_remaining || 0) / 60)} min). `
        + `Private publishing is refused until it has.`
      : `The <span class="font-mono">${zone}</span> governance zone is not confirmed enforced, so `
        + `surveys of Personal/Experiment investigations are refused rather than published where `
        + `everyone could read them.`}</div>
    ${z.detail ? `<div class="mt-[2px] text-provenance text-ink-muted">${esc(z.detail)}</div>` : ''}
    ${z.remedy ? `<div class="mt-[2px] text-caveat text-accent-ink">${esc(z.remedy)}</div>` : ''}
    ${zoneControlsHtml(z.zone_controls)}
  </div>`;
}

function render() {
  if (!_host) return;
  const d = _scan || {};

  if (!d.reachable) {
    // Unreachable is never drift — showing "nothing to do" here would read
    // as a clean bill of health for a catalog we could not read at all.
    _host.innerHTML = `
      <h3 class="m-0 font-heading text-name font-normal text-ink">🔄 Egeria Alignment</h3>
      <div class="my-s3 h-px bg-rule"></div>
      <p class="text-answer text-state-warn">Egeria is unreachable — nothing was checked.</p>
      <p class="text-caveat text-ink-muted">${esc(d.unreachable_reason || '')}</p>
      <p class="text-caveat text-ink-muted">Deliberately not reported as "no drift": an Egeria we
        cannot read is not one known to be fine.</p>
      <button type="button" data-retry
        class="mt-s2 cursor-pointer rounded-sm border border-rule-strong px-2 py-[2px] text-caveat text-ink hover:border-accent">Retry</button>`;
    _host.querySelector('[data-retry]')?.addEventListener('click', load);
    return;
  }

  const findings = d.findings || [];
  const scheduled = findings.filter((f) => f.repair_step && f.scheduled);
  const repairable = findings.filter((f) => f.repair_step && !f.scheduled);
  const decisions = findings.filter((f) => !f.repair_step);

  const expensiveCount = repairable.filter((f) => f.expensive)
    .reduce((n, f) => n + f.count, 0);
  const nowRepairable = (d.repairable || 0)
    - scheduled.reduce((n, f) => n + f.count, 0) - expensiveCount;

  _host.innerHTML = `
    <div class="mb-s2 flex items-center justify-between">
      <h3 class="m-0 font-heading text-name font-normal text-ink">🔄 Egeria Alignment</h3>
      <button type="button" data-rescan
        class="cursor-pointer rounded-sm border border-rule-strong px-2 py-[2px] text-caveat text-ink hover:border-accent">🔄 Re-scan</button>
    </div>
    <p class="mb-s3 max-w-[70ch] text-caveat text-ink-muted">Everywhere RE and Egeria have drifted
      apart, in both directions: pointers here into Egeria that no longer holds what they point
      at, and definitions in Egeria that have fallen behind what RE can now run. Scanning never
      writes.</p>
    ${privateZoneHtml()}
    <div class="mb-s3 flex flex-wrap items-center gap-s3 text-caveat">
      <span class="text-ink">${d.total || 0} item(s) drifted</span>
      <span class="text-ink-muted">·</span>
      <span class="text-accent-ink">${Math.max(0, nowRepairable)} repairable now</span>
      ${expensiveCount ? `<span class="text-ink-muted">·</span>
        <span class="text-state-warn" title="Slow repairs that write to Egeria — unticked by default">${expensiveCount} slow, not selected</span>` : ''}
      <span class="text-ink-muted">·</span>
      <span class="text-state-warn">${d.needs_decision || 0} need a decision</span>
      ${d.undetermined_count ? `<span class="text-ink-muted">·</span>
        <span class="text-state-warn" title="Lookups that failed — reported, never counted as drift and never cleared">${d.undetermined_count} undetermined</span>` : ''}
    </div>

    ${!findings.length ? '<p class="text-answer text-state-ok">Nothing has drifted. RE and Egeria agree.</p>' : ''}

    ${scheduled.length ? `<div class="mb-s4">
      <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">Already scheduled — runs ${SCHEDULE_INTERVAL_TEXT}</div>
      <div class="space-y-s2">${scheduled.map((f) => scheduledRowHtml(f, _status)).join('')}</div>
    </div>` : ''}

    ${repairable.length ? `<div class="mb-s4">
      <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">Repairable</div>
      <div class="space-y-s2">${repairable.map(repairableRowHtml).join('')}</div>
      <div class="mt-s2 flex items-center gap-s3">
        <button type="button" data-apply-selected
          class="cursor-pointer rounded-sm border border-accent bg-transparent px-3 py-[4px] text-caveat text-accent-ink hover:bg-paper-surface">
          Apply selected
        </button>
        <span class="text-provenance text-ink-muted">Runs in dependency order regardless of what you tick.</span>
      </div>
    </div>` : ''}

    ${decisions.length ? `<div class="mb-s4">
      <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">Needs a decision — not RE's to fix</div>
      <div class="space-y-s2">${decisions.map(decisionRowHtml).join('')}</div>
    </div>` : ''}

    <div data-resync-result></div>`;

  bind();
}

/** Every way an apply can have not worked, as plain sentences.
 *  HTTP 200 only means the request was handled — a repair that failed on
 *  every repo, or could not reach Egeria, still returns 200. */
export function applyFailures(data) {
  const out = [];
  if (data && data.reachable === false) {
    out.push(`Egeria is unreachable — nothing was applied. ${data.unreachable_reason || ''}`.trim());
  }
  for (const [step, v] of Object.entries((data && data.applied) || {})) {
    if (!v || !(v.failed > 0)) continue;
    const errs = v.errors || [];
    if (!errs.length) out.push(`${step}: ${v.failed} failed`);
    for (const e of errs) out.push(`${step}: ${e.slug} — ${e.error}`);
    if (v.failed > errs.length) out.push(`${step}: ${v.failed - errs.length} more failed (not listed)`);
  }
  return out;
}

function resultHtml(data) {
  const applied = (data && data.applied) || {};
  const failures = applyFailures(data);
  return `<div class="mt-s3 rounded-sm border ${failures.length ? 'border-state-warn' : 'border-rule'} bg-paper-surface p-s3 text-caveat">
    ${failures.length ? `<div data-resync-failures class="mb-s2 text-state-warn">
      <div>Not everything was applied (${failures.length} problem(s)):</div>
      ${failures.map((f) => `<div class="font-mono text-provenance">${esc(f)}</div>`).join('')}
      <button type="button" data-resync-dismiss
        class="mt-s2 cursor-pointer rounded-sm border border-rule-strong px-2 py-[2px] text-caveat text-ink hover:border-accent">Dismiss and re-scan</button>
    </div>` : ''}
    <div class="text-ink">Applied:</div>
    ${Object.entries(applied).map(([k, v]) =>
      `<div class="font-mono text-provenance text-ink-muted">${esc(k)} — ${esc(JSON.stringify(v))}</div>`).join('')}
  </div>`;
}

/** Show the result. On any failure the result STAYS until dismissed —
 *  load() would replace it within the same tick, which is how a failed
 *  Apply used to look like nothing had happened. */
async function showApplyResult(data) {
  const out = _host.querySelector('[data-resync-result]');
  if (out) out.innerHTML = resultHtml(data);
  if (applyFailures(data).length) {
    out?.querySelector('[data-resync-dismiss]')?.addEventListener('click', () => load());
    return;
  }
  await load();
}

async function runNow(step, finding) {
  if (_busy) return;
  _busy = true;
  const btn = _host.querySelector(`[data-run-now="${CSS.escape(step)}"]`);
  if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }
  try {
    const data = await applyResyncSteps([step]);
    await showApplyResult(data);
  } catch (err) {
    await inPageNotice({ message: `Run failed: ${err.message}` });
  } finally {
    _busy = false;
  }
}

async function applySelected() {
  if (_busy) return;
  const steps = [..._host.querySelectorAll('[data-resync-step]:checked')].map((c) => c.dataset.resyncStep);
  if (!steps.length) { await inPageNotice({ title: 'Nothing to do', message: 'Nothing selected.' }); return; }

  const findingByStep = new Map((_scan.findings || []).map((f) => [f.repair_step, f]));
  const lines = steps.map((s) => {
    const f = findingByStep.get(s);
    const describe = BLAST_RADIUS[s];
    return describe && f ? `• ${describe(f.count)}` : `• ${s}`;
  });
  const confirmed = (await inPageConfirm({ message: `Apply ${steps.length} repair step(s)?\n\n${steps.join(', ')}\n\n${lines.join('\n\n')}`, confirmLabel: 'Confirm' }));
  if (!confirmed) return;

  _busy = true;
  const btn = _host.querySelector('[data-apply-selected]');
  if (btn) { btn.disabled = true; btn.textContent = 'Applying…'; }
  try {
    const data = await applyResyncSteps(steps);
    await showApplyResult(data);
    _busy = false;
    const b = _host.querySelector('[data-apply-selected]');
    if (b) { b.disabled = false; b.textContent = 'Apply selected'; }
  } catch (err) {
    await inPageNotice({ message: `Resync failed: ${err.message}` });
    _busy = false;
    if (btn) { btn.disabled = false; btn.textContent = 'Apply selected'; }
  }
}

function bind() {
  _host.querySelector('[data-rescan]')?.addEventListener('click', load);
  _host.querySelector('[data-apply-selected]')?.addEventListener('click', applySelected);
  _host.querySelectorAll('[data-run-now]').forEach((b) => b.addEventListener('click', () => {
    if (b.disabled) return;
    runNow(b.dataset.runNow);
  }));
}

async function load() {
  if (!_host) return;
  _host.innerHTML = '<p class="text-answer text-ink-muted">Scanning Egeria…</p>';
  // Deliberately not awaited with the scan below, same as classic: the
  // zone's state is useful even when the scan fails, and a scan failure
  // must not hide it.
  getPrivateZone().then((z) => { _zone = z; if (_scan) render(); }).catch(() => { _zone = null; });
  // Same treatment: the scheduler's run history is useful even if this
  // particular scan fails, and a status fetch failing must not hide the
  // scan -- the scheduled rows just render without the extra recency/
  // failure detail (status is optional in scheduledRowHtml).
  getResyncStatus().then((s) => { _status = s; if (_scan) render(); }).catch(() => { _status = null; });
  try {
    _scan = await getResyncScan();
  } catch (err) {
    _host.innerHTML = `<p class="max-w-[70ch] text-answer text-state-warn">Could not scan: ${esc(err.message)}</p>`;
    return;
  }
  render();
}

export async function renderResync(host) {
  _host = host;
  _busy = false;
  await load();
}
