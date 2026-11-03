/* The shell's own status surfaces (parity P1): the backend-health banner (PI-138), the bootstrap banner
 * (PI-139) and the connection-details popover (PI-137).
 *
 * Three honest-absence rules run through all of it:
 *   - A check that could not be made is "not read", never "ok" and never 0.
 *   - A banner states a condition with a visible cue and a short word; the sentence is on demand (title / detail).
 *   - The bootstrap banner runs `check_and_heal` with force=false ONLY. A forced run re-runs every batch and, for a
 *     batch that is not idempotent, duplicates step links; re-api.js's runBootstrapMissingOnly takes no `force`.
 *
 * Pure builders (`bootstrapAttention`, `lastHeal`, `healthWord`) are exported for the tests. */
import {
  getHealthReady, getBootstrapStatus, runBootstrapMissingOnly, getWhoami, getAdminStatus, adminLocked,
} from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { ago } from '/static/next/format.js';

export const HEALTH_BANNER_ID = 'backend-health-banner';
export const BOOTSTRAP_BANNER_ID = 'bootstrap-banner';
export const POPOVER_ID = 'connection-popover';
const ARM_MS = 6000;

/* ── PI-138: backend health ─────────────────────────────────────────────────── */

/** The short word for a failed /health/ready. status 0 is a network failure (re-api.js's own ApiError). */
export function healthWord(err) {
  if (!err) return 'ok';
  if (err.status === 0) return 'Server not responding';
  if (err.status === 503) return 'Database unreachable';
  if (err.loginRequired) return 'Signed out';
  return `Server error${err.status ? ` ${err.status}` : ''}`;
}

function removeEl(doc, id) { doc.getElementById(id)?.remove(); }

/** The server process token last seen, and who wants to know when it changes. The first sighting is not a change. */
let lastBootId = null;
const restartListeners = new Set();
export function onServerRestart(fn) { restartListeners.add(fn); return () => restartListeners.delete(fn); }
export function noteBootId(id) {
  if (!id) return;
  const changed = lastBootId !== null && lastBootId !== id;
  lastBootId = id;
  if (changed) restartListeners.forEach((fn) => { try { fn(id); } catch (_) { /* one listener's failure is not another's */ } });
}
export function resetBootIdForTests() { lastBootId = null; restartListeners.clear(); }

/** One check. Resolves true when healthy. A dead SESSION is not a dead backend: the session banner owns that. */
export async function checkBackendHealth(doc, { getHealth = getHealthReady } = {}) {
  try {
    noteBootId((await getHealth())?.boot_id);
    removeEl(doc, HEALTH_BANNER_ID);
    return true;
  } catch (err) {
    if (err?.loginRequired) return true;
    showHealthBanner(doc, err, { getHealth });
    return false;
  }
}

function showHealthBanner(doc, err, { getHealth }) {
  let bar = doc.getElementById(HEALTH_BANNER_ID);
  if (!bar) {
    bar = doc.createElement('div');
    bar.id = HEALTH_BANNER_ID;
    bar.setAttribute('role', 'alert');
    bar.className = 'sticky top-0 z-40 flex flex-wrap items-baseline gap-s3 border-b border-state-warn bg-paper px-s3 py-s2 text-caveat text-ink';
    bar.innerHTML = `<span class="text-state-warn" aria-hidden="true">⚠</span>
      <span data-health-word></span>
      <button type="button" data-health-retry class="cursor-pointer rounded-sm border border-accent bg-transparent px-3 py-[2px] text-answer text-accent-ink">Retry</button>
      <span data-health-detail class="text-provenance text-ink-muted"></span>`;
    doc.body.insertBefore(bar, doc.body.firstChild);
    bar.querySelector('[data-health-retry]').addEventListener('click', async (ev) => {
      const btn = ev.currentTarget;
      btn.disabled = true; btn.textContent = 'Checking…';
      const ok = await checkBackendHealth(doc, { getHealth });
      if (!ok) { btn.disabled = false; btn.textContent = 'Retry'; }
    });
  }
  bar.querySelector('[data-health-word]').textContent = healthWord(err);
  // The sentence on demand: the server's own detail, as text.
  const detail = bar.querySelector('[data-health-detail]');
  detail.textContent = '';
  detail.title = String(err?.message || '');
  bar.title = String(err?.message || '');
}

/** Check now and then every `intervalMs`. Returns {stop}. */
export function installHealthBanner(doc = document, { getHealth = getHealthReady, intervalMs = 30000 } = {}) {
  checkBackendHealth(doc, { getHealth });
  const timer = setInterval(() => checkBackendHealth(doc, { getHealth }), intervalMs);
  return { stop: () => clearInterval(timer) };
}

/* ── PI-139: bootstrap ───────────────────────────────────────────────────── */

/** The most recent heal across batches: {at, result, batch} or null when none has ever run since the server started. */
export function lastHeal(status) {
  let best = null;
  for (const [id, b] of Object.entries(status?.batches || {})) {
    if (!b?.last_healed_at) continue;
    if (!best || Date.parse(b.last_healed_at) > Date.parse(best.at)) {
      best = { at: b.last_healed_at, result: b.last_heal_result || '', batch: b.display_name || id };
    }
  }
  return best;
}

/** What, if anything, needs a person's eye. null when everything verified is present. Three kinds:
 *  restoring (a heal is running), unreachable (Egeria could not be asked), missing (a verified absence, the only
 *  one a run can fix). A batch whose presence was never verified (present null) is NOT missing: it is unchecked. */
export function bootstrapAttention(status) {
  if (!status) return null;
  const batches = Object.values(status.batches || {});
  const missing = batches.filter((b) => b.present === false);
  if (status.reinitializing) return { kind: 'restoring', word: 'Restoring definitions', missing: missing.length };
  if (status.egeria_reachable === false) return { kind: 'unreachable', word: 'Egeria not reachable', missing: missing.length };
  if (missing.length) return { kind: 'missing', word: `${missing.length} definition batch${missing.length === 1 ? '' : 'es'} missing`, missing: missing.length };
  return null;
}

function healLine(status) {
  const h = lastHeal(status);
  return h
    ? `Last heal: ${h.batch} · ${h.result || 'no result recorded'} · ${ago(h.at)}`
    : 'Heal history: not read in this process';
}

/** True while a run is in flight: a poll must not rebuild the button under it, or a second run could be started. */
let runInFlight = false;

/** Draw, update or remove the bootstrap banner for `status` (null = could not be read: no banner, no claim). */
export function renderBootstrapBanner(doc, status, { run = runBootstrapMissingOnly, onDone, locked = false } = {}) {
  const need = bootstrapAttention(status);
  if (runInFlight && doc.getElementById(BOOTSTRAP_BANNER_ID)) return need;
  if (!need) { removeEl(doc, BOOTSTRAP_BANNER_ID); return null; }
  let bar = doc.getElementById(BOOTSTRAP_BANNER_ID);
  if (!bar) {
    bar = doc.createElement('div');
    bar.id = BOOTSTRAP_BANNER_ID;
    bar.setAttribute('role', 'status');
    bar.className = 'sticky top-0 z-30 flex flex-wrap items-baseline gap-s3 border-b border-rule-strong bg-paper px-s3 py-s2 text-caveat text-ink';
    doc.body.insertBefore(bar, doc.body.firstChild);
  }
  const canRun = need.kind === 'missing';
  bar.dataset.bootstrapKind = need.kind;
  bar.innerHTML = `<span class="${need.kind === 'restoring' ? 'text-accent-ink' : 'text-state-warn'}" aria-hidden="true">${need.kind === 'restoring' ? '◔' : '⚠'}</span>
    <span data-bootstrap-word>${esc(need.word)}</span>
    ${canRun ? `<button type="button" data-bootstrap-run ${locked ? 'disabled data-admin-only' : ''}
      title="${locked ? 'Admin only: needs the admin credential (Admin → Feedback)' : 'Heal only what is missing. Nothing already present is re-run.'}"
      class="rounded-sm border border-accent bg-transparent px-3 py-[2px] text-answer text-accent-ink ${locked ? 'cursor-default opacity-50' : 'cursor-pointer'}">Run bootstrap now</button>${locked ? '<span class="text-provenance text-ink-muted"> · admin only</span>' : ''}` : ''}
    <span data-bootstrap-state class="text-ink-muted"></span>
    <span data-bootstrap-heal class="text-provenance text-ink-muted">${esc(healLine(status))}</span>`;
  const btn = bar.querySelector('[data-bootstrap-run]');
  if (btn) bindRun(btn, bar, { run, onDone });
  return need;
}

/** Two presses, so a heal that writes to Egeria is never one stray click: the first arms, the second runs. */
function bindRun(btn, bar, { run, onDone }) {
  let armed = null;
  const state = bar.querySelector('[data-bootstrap-state]');
  btn.addEventListener('click', async () => {
    if (btn.disabled) return;
    if (!armed) {
      btn.textContent = 'Press again to run';
      state.textContent = 'Heals only what is missing.';
      armed = setTimeout(() => { armed = null; btn.textContent = 'Run bootstrap now'; state.textContent = ''; }, ARM_MS);
      return;
    }
    clearTimeout(armed); armed = null;
    btn.disabled = true; btn.textContent = 'Running…'; state.textContent = '';
    runInFlight = true;
    try {
      const out = await run();
      const parts = Object.entries(out?.batches || {}).map(([id, r]) => `${id}: ${r.action}`);
      state.textContent = parts.length ? parts.join(' · ') : 'done';
      btn.textContent = 'Run bootstrap now';
      btn.disabled = false;
      runInFlight = false;
      await onDone?.();
    } catch (err) {
      runInFlight = false;
      btn.disabled = false; btn.textContent = 'Run bootstrap now';
      // 409: the server is already healing (this tab's run, another tab, or the automatic loop). Say so as a state.
      state.textContent = err.status === 409 ? '◔ running' : `Not run: ${err.message}`;
    }
  });
}

/** Read the status now and draw the banner. A failed read removes nothing it cannot be sure of and claims nothing. */
export async function refreshBootstrapBanner(doc, { getStatus = getBootstrapStatus, run, getAdmin = getAdminStatus } = {}) {
  let status = null;
  try { status = await getStatus(); } catch { return null; }
  const locked = await getAdmin().then(adminLocked, () => false);
  renderBootstrapBanner(doc, status, { run, locked, onDone: () => refreshBootstrapBanner(doc, { getStatus, run, getAdmin }) });
  return status;
}

export function installBootstrapBanner(doc = document, { getStatus = getBootstrapStatus, run, intervalMs = 60000 } = {}) {
  refreshBootstrapBanner(doc, { getStatus, run });
  const timer = setInterval(() => refreshBootstrapBanner(doc, { getStatus, run }), intervalMs);
  return { stop: () => clearInterval(timer) };
}

/* ── PI-137: connection details ──────────────────────────────────────────── */

export function closeConnectionPopover(doc = document) { removeEl(doc, POPOVER_ID); }

const NOT_READ = '<span class="text-ink-muted">not read</span>';

/** Pure: who the last Egeria call ran as, from the value the server RECORDED (Brief I), never inferred.
 *  null = whoami not read; a recorded-nothing answer says so in words. */
export function lastCallWords(whoami) {
  if (!whoami) return null;
  const last = whoami.last_egeria_call;
  if (!last || !last.as) return 'not recorded in this process';
  return [last.as, last.purpose, last.at ? ago(last.at) : ''].filter(Boolean).join(' · ');
}

/** Pure: who background and queued work runs as (Brief L), from whoami's `daemon` — the userId the
 *  server's credential seam loaded, never inferred. null = whoami not read. */
export function daemonWords(whoami) {
  if (!whoami) return null;
  const d = whoami.daemon;
  if (!d || !d.user_id) return 'not reported by this server';
  if (d.source === 'env') return `${d.user_id} · from .env (development bootstrap)`;
  if (d.source === 'default') return `${d.user_id} · code default (EGERIA_USER_ID unset)`;
  return d.user_id;
}

/** Pure: RE's own identity in Egeria (7h), from whoami's `identity_in_egeria` — the words the server's
 *  startup check RECORDED ("present" / "created (…)" / "could not check (reason)"), never inferred.
 *  A failed person-profile lookup (Ownership fell back to the userId form) is appended. null = not read. */
export function identityInEgeriaWords(whoami) {
  if (!whoami) return null;
  const s = whoami.identity_in_egeria;
  const base = (s && s.words) ? s.words : 'not reported by this server';
  const f = whoami.owner_lookup;
  return f && f.reason ? `${base} · last owner-profile lookup failed (${f.user_id}): ${f.reason}` : base;
}

/** Pure: the popover's rows from what was read. Each value is a string or null (not read). */
export function connectionRows({ me, whoami, status }) {
  const who = me && (me.user_id || me.username || me.egeria_user);
  const heal = lastHeal(status);
  return [
    ['Signed in as', who || null],
    ['View server', whoami?.view_server || null],
    ['Platform', whoami?.platform_url || null],
    ['Build', whoami ? (whoami.build_sha || null) : null],
    ['Last Egeria call as', lastCallWords(whoami)],
    ['Background work as', daemonWords(whoami)],
    ['RE identity in Egeria', identityInEgeriaWords(whoami)],
    ['Last bootstrap heal', status ? (heal ? `${heal.batch} · ${heal.result || 'no result'} · ${ago(heal.at)}` : 'not read in this process') : null],
  ];
}

/** Open the popover under `anchor`. `me` is the signed-in user the app already holds. */
export async function openConnectionPopover(doc, anchor, { me, getInfo = getWhoami, getStatus = getBootstrapStatus } = {}) {
  closeConnectionPopover(doc);
  const pop = doc.createElement('div');
  pop.id = POPOVER_ID;
  pop.setAttribute('role', 'dialog');
  pop.setAttribute('aria-label', 'Connection details');
  pop.className = 'fixed right-s4 top-[40px] z-50 w-[22.5rem] max-w-[92vw] rounded-sm border border-rule-strong bg-paper p-s3 text-caveat text-ink shadow-lg';
  pop.innerHTML = '<p class="text-ink-muted">reading…</p>';
  doc.body.appendChild(pop);
  const onKey = (e) => { if (e.key === 'Escape') { closeConnectionPopover(doc); doc.removeEventListener('keydown', onKey); } };
  const onDoc = (e) => {
    if (!pop.contains(e.target) && e.target !== anchor && !anchor?.contains?.(e.target)) {
      closeConnectionPopover(doc); doc.removeEventListener('click', onDoc, true); doc.removeEventListener('keydown', onKey);
    }
  };
  doc.addEventListener('keydown', onKey);
  setTimeout(() => doc.addEventListener('click', onDoc, true), 0);

  const [whoamiR, statusR] = await Promise.allSettled([getInfo(), getStatus()]);
  if (!doc.getElementById(POPOVER_ID)) return pop;
  const rows = connectionRows({
    me, whoami: whoamiR.status === 'fulfilled' ? whoamiR.value : null,
    status: statusR.status === 'fulfilled' ? statusR.value : null,
  });
  pop.innerHTML = `<div class="mb-s2 font-heading text-answer text-ink">Connection</div>
    <dl class="m-0 grid grid-cols-[9rem_1fr] gap-x-s2 gap-y-[3px]">${rows.map(([k, v]) => `
      <dt class="text-ink-muted">${esc(k)}</dt>
      <dd data-conn="${esc(k)}" class="m-0 break-all font-mono text-provenance"${k === 'Build' && v ? ` title="${esc(v)}"` : ''}>${
        v ? esc(k === 'Build' ? String(v).slice(0, 10) : v) : NOT_READ}</dd>`).join('')}</dl>
    <p class="mt-s2 text-provenance text-ink-muted">What you start runs as you in Egeria; scheduled and queued work runs as the service account, recorded as yours.</p>`;
  return pop;
}
