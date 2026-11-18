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
  // An opt-in batch that is off (engine-host, F54) is not healed by "Run bootstrap now": it has its own line
  // (engineLine) and its own press (Admin › Repair), so it is not counted here.
  const missing = batches.filter((b) => (b.state ? b.state === 'missing' : b.present === false)
    && !(b.opt_in && b.opt_in.on === false));
  if (status.reinitializing) return { kind: 'restoring', word: 'Restoring definitions', missing: missing.length };
  if (status.egeria_reachable === false) return { kind: 'unreachable', word: 'Egeria not reachable', missing: missing.length };
  if (missing.length) return { kind: 'missing', word: `${missing.length} definition batch${missing.length === 1 ? '' : 'es'} missing`, missing: missing.length };
  return null;
}

/** The engine-host batch's own line (W65), for everyone: null, or {kind, word}. "missing" whenever the engine is
 *  verified absent, whatever the self-heal setting says; "declaring" while a pass is running that batch. opted_out
 *  and unknown draw nothing here (Admin › Repair says them). */
export const ENGINE_BATCH = 'engine-host';
export const ENGINE_MISSING_WORDS = "Resource Explorer's survey engine is not declared in this Egeria · RE-engine "
  + 'surveys cannot run in Egeria · an admin can declare it (Admin › Repair)';
export const ENGINE_DECLARING_WORDS = "Resource Explorer's survey engine: declaring…";
export function engineLine(status) {
  const b = status?.batches?.[ENGINE_BATCH];
  if (!b) return null;
  if (status.healing === ENGINE_BATCH || b.healing === true) return { kind: 'declaring', word: ENGINE_DECLARING_WORDS };
  if (b.state === 'missing') return { kind: 'missing', word: ENGINE_MISSING_WORDS };
  return null;
}

/** The Prefect pool's own line (2026-11-13): null, or {kind, word, runsLocally}. Two warnings, in the server's
 *  own words (`summary`, `runs_locally_words`), so the banner and the log say the same thing:
 *    - 'unreachable': the Prefect server could not be asked. Runs still happen (the local fallback), but none is
 *      recorded in Prefect, and dispatched steps run in RE's process (the Architect's ruling, 2026-11-13);
 *    - 'no_live': the server answered and no worker is live; dispatched steps run in RE's process instead.
 *    - 'dispatch_refused': a worker is live but a dispatched step would still run locally (its deployment is on
 *      another pool than the configured one, or it runs other code): the same decision dispatch makes;
 *    - 'parity_unchecked': a worker is live but advertises no commit, so RE cannot check it runs RE's code.
 *    - 'workers_elsewhere': RE's own worker is live on another pool than the one RE dispatches to, so it gets no
 *      step (its --pool differs from the web app's PREFECT_WORK_POOL). In the other states the summary says it.
 *  Nothing is drawn while the pool is unread (reachable null), while a worker on a known commit is live, or when Prefect is off by
 *  configuration (enabled false: every step runs locally by choice). Nothing waits in either warning state. */
export function prefectPoolLine(status) {
  const p = status?.prefect_pool;
  if (!p || p.enabled === false) return null;
  if (p.reachable === false) {
    return { kind: 'unreachable', word: `Prefect: ${p.summary || 'server not reachable'}`, runsLocally: p.runs_locally_words || '' };
  }
  if (p.reachable === true && p.pool_exists === false) {
    // The server answered: this pool does not exist. Its own words, because the fix is to create the pool,
    // not to start the server (read-back of 377c3353).
    return { kind: 'pool_missing', word: `Prefect: ${p.summary || 'pool does not exist'}`, runsLocally: p.runs_locally_words || '' };
  }
  if (p.reachable !== true) return null;
  if (p.live_count === 0) {
    return { kind: 'no_live', word: `Prefect: ${p.summary || 'no live worker'}`, runsLocally: p.runs_locally_words || '' };
  }
  // A live worker a step would still not be sent to (Architect ruling 2026-11-14): the server's own refusal.
  if (p.live_count > 0 && p.dispatch_refused) {
    return { kind: 'dispatch_refused', word: `Prefect: ${p.summary || p.dispatch_refused}`, runsLocally: p.runs_locally_words || '' };
  }
  // A live worker that advertises no commit: RE can dispatch to it but cannot check it runs RE's own code
  // (Architect ruling 2026-11-13). The server's summary carries the words.
  if (p.live_count > 0 && (p.parity_unchecked || []).length) {
    return { kind: 'parity_unchecked', word: `Prefect: ${p.summary || 'commit parity not checked'}`, runsLocally: '' };
  }
  if (p.live_count > 0 && (p.workers_elsewhere || []).length) {
    return { kind: 'workers_elsewhere', word: `Prefect: ${p.summary || 'a worker is on another pool'}`, runsLocally: '' };
  }
  return null;
}

function poolLineHtml(pool) {
  if (!pool) return '';
  return `<span data-prefect-pool-line="${esc(pool.kind)}" class="basis-full text-state-warn">`
    + `<span aria-hidden="true">⚠</span> <span data-prefect-pool-word>${esc(pool.word)}</span>`
    + (pool.runsLocally ? ` · <span data-prefect-pool-runs-locally>${esc(pool.runsLocally)}</span>` : '')
    + '</span>';
}

function engineLineHtml(eng) {
  if (!eng) return '';
  return `<span data-engine-line="${esc(eng.kind)}" class="basis-full ${eng.kind === 'declaring' ? 'text-accent-ink' : 'text-state-warn'}">`
    + `<span aria-hidden="true">${eng.kind === 'declaring' ? '◔' : '⚠'}</span> ${esc(eng.word)}</span>`;
}

function healLine(status) {
  const h = lastHeal(status);
  return h
    ? `Last heal: ${h.batch} · ${h.result || 'no result recorded'} · ${ago(h.at)}`
    : 'Heal history: not read in this process';
}

/** True while a run is in flight: a poll must not rebuild the button under it, or a second run could be started. */
let runInFlight = false;

/* The banner's close box (2026-11-17). Closing hides exactly what is showing now: the dismissal is keyed by the
 * words of every line, so any change (a new condition, a different worker, the need going from missing to
 * restoring) draws the banner again, and a new tab or session starts with it showing. It is kept for the tab only
 * (sessionStorage), never sent anywhere, and never makes a condition healthier: the checks and the log lines
 * still run, only this tab's banner is hidden. */
export const BANNER_DISMISSED_KEY = 're_bootstrap_banner_dismissed';
function bannerSignature(need, eng, pool) {
  return JSON.stringify([need ? [need.kind, need.word] : null, eng ? [eng.kind, eng.word] : null, pool ? [pool.kind, pool.word] : null]);
}
function dismissedSignature(doc) {
  try { return doc.defaultView?.sessionStorage?.getItem(BANNER_DISMISSED_KEY) ?? globalThis.sessionStorage?.getItem(BANNER_DISMISSED_KEY) ?? null; }
  catch { return null; }
}
function rememberDismissed(doc, sig) {
  try { (doc.defaultView?.sessionStorage ?? globalThis.sessionStorage)?.setItem(BANNER_DISMISSED_KEY, sig); } catch { /* storage off: closed until the next poll */ }
}
const CLOSE_HTML = '<button type="button" data-banner-close aria-label="Close this banner" title="Close this banner (it returns if the condition changes)"'
  + ' class="ml-auto cursor-pointer border-0 bg-transparent px-2 text-ink-muted hover:text-ink">×</button>';
function bindClose(doc, bar, sig) {
  bar.insertAdjacentHTML('beforeend', CLOSE_HTML);
  bar.querySelector('[data-banner-close]').addEventListener('click', () => { rememberDismissed(doc, sig); bar.remove(); });
}

/** Draw, update or remove the bootstrap banner for `status` (null = could not be read: no banner, no claim). */
export function renderBootstrapBanner(doc, status, { run = runBootstrapMissingOnly, onDone, locked = false } = {}) {
  const need = bootstrapAttention(status);
  const eng = engineLine(status);
  const pool = prefectPoolLine(status);
  if (runInFlight && doc.getElementById(BOOTSTRAP_BANNER_ID)) return need;
  if (!need && !eng && !pool) { removeEl(doc, BOOTSTRAP_BANNER_ID); return null; }
  const sig = bannerSignature(need, eng, pool);
  if (dismissedSignature(doc) === sig) { removeEl(doc, BOOTSTRAP_BANNER_ID); return need; }
  let bar = doc.getElementById(BOOTSTRAP_BANNER_ID);
  if (!bar) {
    bar = doc.createElement('div');
    bar.id = BOOTSTRAP_BANNER_ID;
    bar.setAttribute('role', 'status');
    bar.className = 'sticky top-0 z-30 flex flex-wrap items-baseline gap-s3 border-b border-rule-strong bg-paper px-s3 py-s2 text-caveat text-ink';
    doc.body.insertBefore(bar, doc.body.firstChild);
  }
  if (!need) {
    // Only the engine and/or pool lines: no batch word, no run button (neither is healed by a bootstrap run).
    bar.dataset.bootstrapKind = eng ? 'engine' : 'prefect-pool';
    bar.innerHTML = engineLineHtml(eng) + poolLineHtml(pool);
    bindClose(doc, bar, sig);
    return eng ? { kind: 'engine', word: eng.word, missing: 0 } : { kind: 'prefect-pool', word: pool.word, missing: 0 };
  }
  const canRun = need.kind === 'missing';
  bar.dataset.bootstrapKind = need.kind;
  bar.innerHTML = `${engineLineHtml(eng)}${poolLineHtml(pool)}<span class="${need.kind === 'restoring' ? 'text-accent-ink' : 'text-state-warn'}" aria-hidden="true">${need.kind === 'restoring' ? '◔' : '⚠'}</span>
    <span data-bootstrap-word>${esc(need.word)}</span>
    ${canRun ? `<button type="button" data-bootstrap-run ${locked ? 'disabled data-admin-only' : ''}
      title="${locked ? 'Admin only: needs the admin credential (Admin → Feedback)' : 'Heal only what is missing. Nothing already present is re-run.'}"
      class="rounded-sm border border-accent bg-transparent px-3 py-[2px] text-answer text-accent-ink ${locked ? 'cursor-default opacity-50' : 'cursor-pointer'}">Run bootstrap now</button>${locked ? '<span class="text-provenance text-ink-muted"> · admin only</span>' : ''}` : ''}
    <span data-bootstrap-state class="text-ink-muted"></span>
    <span data-bootstrap-heal class="text-provenance text-ink-muted">${esc(healLine(status))}</span>`;
  const btn = bar.querySelector('[data-bootstrap-run]');
  if (btn) bindRun(btn, bar, { run, onDone });
  bindClose(doc, bar, sig);
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
