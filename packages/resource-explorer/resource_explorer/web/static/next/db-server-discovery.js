/* Find databases — the sidebar's Find action for the Databases kind.
 *
 * REPLY-DESIGNER-DISCOVERY-SOURCES-ALL-KINDS.md §1/§2/§5 and slice 1; drawing:
 * wireframes/FindAndImport.dc.html. One dialog, three ways in, ONE candidate
 * table and ONE confirm at the end of every way in:
 *
 *   Saved sources         the registered servers, each with Run. A source
 *                         remembers its last run, so Run says "n new since
 *                         <date>" (and "0 new since <time>" on an immediate
 *                         re-run). Register/Test/Remove live here too:
 *                         registering a server is configuration.
 *   Discover on a server  the same listing, run once on connection details
 *                         typed here, with "save as a source". The password is
 *                         held in memory for this dialog only: never rendered
 *                         into an attribute, never stored until saved.
 *   From a file           a CSV (batch_io.py's contract): choose a file, see the
 *                         five-count preview (new, already registered, duplicates
 *                         in the file, invalid, of a kind not importable here),
 *                         each count opening its lines, then confirm into a group
 *                         and "Add these N to <investigation>". A database row
 *                         names WHERE its credential comes from (server, a saved
 *                         source; or connection_ref, a reference name), never the
 *                         credential: nothing in this tab sends or shows one.
 *
 * The candidate table says what the source returned and nothing else: every
 * fact is a measured value or a worded "not read" ("? not readable with this
 * credential"), never a 0 or a blank. A database the credential cannot CONNECT
 * to is LISTED with its mark, not filtered out. Already-registered rows are
 * dimmed and pre-checked and cannot be re-added.
 *
 * The confirm offers the destination group and "Add these N to <investigation>"
 * (POST /api/investigations/{slug}/members: the investigation scope API, the
 * same act as the work-lists slice; work lists are not used here because the
 * scope API exists). Registering is a local registry write; nothing is
 * written to Egeria from this dialog.
 *
 * Chrome-level module, same placement rule as `discovery-import.js`
 * (SPEC-ACTIONABLE-AND-HONEST.md point 2): reached from the sidebar's
 * `find-repos` action for `state.resourceType === 'db'`.
 */
import { openDialog } from '/static/next/worklist.js';
import { inPageConfirm } from '/static/next/confirm-dialog.js';
import {
  listDbServers, registerDbServer, deleteDbServer, testDbServer, testDbServerInline,
  runDatabaseSource, discoverDatabasesInline, addDiscoveredDatabase, assignGroup,
  addInvestigationMember, listInvestigations, listGroups,
  previewDiscoveryFile, importDiscoveryFile, fetchCandidatesCsv,
} from '/static/re-api.js';
import { esc, refreshGroupsAndSidebar, state } from '/static/next/app.js';
import { refreshOpenInvestigation } from '/static/next/stages/investigation.js';
import { credentialMarkHtml } from '/static/next/credential.js';
import { saveCsv } from '/static/next/download.js';
import { blankCredentialCells } from '/static/next/csv-guard.js';
import { credentialChangeHtml, bindCredentialChange } from '/static/next/credential-change.js';
import { ago } from '/static/next/format.js';
import { egeriaHostLineHtml, bindEgeriaHostEdit } from '/static/next/egeria-host-edit.js';
import {
  registerOneDatabaseHtml, bindRegisterOneDatabase, resetRegisterOneDatabase,
} from '/static/next/db-register.js';

const emptyRegisterForm = () => ({
  slug: '', display_name: '', db_type: 'postgresql', host: '', port: 5432,
  description: '', group_slug: '', db_user: '', db_password: '',
  egeria_host: '', egeria_url: '', egeria_server: '',
});

const emptyOneOff = () => ({
  host: '', port: 5432, db_user: '', db_password: '',   // db_password: memory only
  slug: '', display_name: '', savedSlug: '',
});

const emptyCandidates = () => ({
  source: null,        // { kind: 'saved'|'oneoff', slug, label, group_slug }
  meta: null,          // the Run's own facts: run_at, previous_run_at, first_run, new_count, candidate_count
  rows: [],            // DiscoveredDatabase[]
  selected: new Set(), // indices into rows
  loading: false,
  error: '',
  group: '',
  investigation: '',
  outcome: '',         // the confirm's own result line
  outcomeIsError: false,
});

const emptyFile = () => ({
  name: '',
  text: '',               // the file's text; the server re-plans it on every call
  preview: null,          // batch_io.preview_file's payload
  loading: false,
  error: '',
  open: 'new',            // which count is open
  serverChoices: {},      // {line: server slug} chosen in the preview for rows that named none
  deselected: new Set(),  // lines the person unticked (everything importable starts ticked)
  accept: new Set(),      // "line|field": proposed changes the person ticked (none by default)
  group: '',
  investigation: '',
  outcome: '',
  outcomeIsError: false,
});

const view = {
  tab: 'saved',          // 'saved' | 'discover' | 'file'
  mode: 'list',          // within 'saved': 'list' | 'register' (a server) | 'register-db' (one database)
  servers: [],
  groups: [],
  investigations: [],
  busy: false,
  loadFailureMsg: '',    // the exact status text a failed load set, so only it is cleared on success
  loadFailed: false,     // the saved sources could not be read: not the same as "there are none"
  status: '',
  statusIsError: false,

  // register form
  form: emptyRegisterForm(),
  showEgeria: false,
  registerError: '',
  testResult: null,

  oneOff: emptyOneOff(),
  cand: emptyCandidates(),
  file: emptyFile(),
};

/** Opens the dialog and kicks off the first render.
 *
 *  `{ tab: 'file', investigation: slug }` is the investigation page's "＋ add…
 *  from a file" door: the same dialog on its From-a-file tab with the
 *  destination already chosen. */
export async function openFindDbServersDialog(opts = {}) {
  const el = openDialog('Find databases',
    'Run a saved source, or discover on a server once. Nothing is registered until you confirm.',
    { wide: true });
  view.tab = opts.tab === 'file' ? 'file' : 'saved';
  view.mode = 'list';
  view.status = '';
  view.statusIsError = false;
  view.oneOff = emptyOneOff();
  view.cand = emptyCandidates();
  view.file = emptyFile();
  view.file.investigation = opts.investigation || state.investigation || '';
  await loadServers(el);
}

/** A message for a failed call. A 401 is said as what it is, so a signed-out
 *  person is told to sign in rather than shown a raw status. */
function failure(err, doing) {
  if (err && err.status === 401) return `Sign in to ${doing}.`;
  return `Could not ${doing}: ${err && err.message ? err.message : err}`;
}

async function loadServers(el) {
  view.busy = true;
  render(el);
  try {
    const [servers, groups, invs] = await Promise.all([
      listDbServers(),
      listGroups().catch(() => []),
      state.investigations && state.investigations.length
        ? Promise.resolve(state.investigations)
        : listInvestigations({ includeClosed: true }).catch(() => []),
    ]);
    view.servers = servers || [];
    view.groups = groups || [];
    view.investigations = (invs || []).filter((i) => i.status !== 'closed');
    view.loadFailed = false;
    // Clear the banner only if it IS the load-failure message; "Registered
    // server X." and the like are not ours to wipe.
    if (view.loadFailureMsg && view.status === view.loadFailureMsg) {
      view.status = '';
      view.statusIsError = false;
    }
    view.loadFailureMsg = '';
  } catch (err) {
    view.status = failure(err, 'load the saved sources');
    view.loadFailureMsg = view.status;
    view.statusIsError = true;
    view.loadFailed = true;
    view.servers = [];
  } finally {
    view.busy = false;
    render(el);
  }
}

/* ── Frame ─────────────────────────────────────────────────────────────── */

function captureInputs(el) {
  // The one-off tab's fields survive a re-render: the password goes to the
  // module's memory and back onto the input as a PROPERTY, never an attribute.
  el.querySelectorAll('[data-oo]').forEach((inp) => {
    const key = inp.dataset.oo;
    if (key === 'port') view.oneOff.port = parseInt(inp.value, 10) || 0;
    else if (key === 'db_password') view.oneOff.db_password = inp.value;   // never trimmed, never an attribute
    else view.oneOff[key] = inp.value.trim();
  });
}

function render(el) {
  const body = el.querySelector('#wl-detail-body');
  if (!body) return;
  captureInputs(el);
  const panel = view.tab === 'saved'
    ? (view.mode === 'register' ? registerFormHtml()
      : view.mode === 'register-db' ? registerOneDatabaseHtml(view.groups) : savedHtml())
    : view.tab === 'discover' ? discoverHtml()
    : fileHtml();
  body.innerHTML = `
    ${tabsHtml()}
    ${statusLineHtml(view.status, view.statusIsError)}
    ${panel}
    ${view.tab !== 'file' && view.mode !== 'register' && view.mode !== 'register-db' ? candidatesHtml() : ''}
  `;
  const pw = body.querySelector('[data-oo="db_password"]');
  if (pw) pw.value = view.oneOff.db_password;
  bind(el);
  if (view.tab === 'saved' && view.mode === 'register-db') {
    bindRegisterOneDatabase(body, {
      rerender: () => render(el),
      back: () => { view.mode = 'list'; view.status = ''; render(el); },
      onRegistered: () => { refreshGroupsAndSidebar(); },
    });
  }
}

function tabsHtml() {
  const tab = (id, label, badge = '') => `<button data-tab="${id}"
    class="cursor-pointer border-0 bg-transparent pb-[2px] mr-s4 font-heading text-subtab ${
      view.tab === id ? 'border-b border-accent text-ink' : 'text-ink-muted hover:text-ink'}"
    >${esc(label)}${badge ? ` <span class="tnum text-provenance text-ink-muted">${esc(badge)}</span>` : ''}</button>`;
  return `<div class="mb-s3 flex items-center gap-0 text-subtab">
    ${tab('saved', 'Saved sources', view.servers.length ? String(view.servers.length) : '')}
    ${tab('discover', 'Discover on a server')}
    ${tab('file', 'From a file')}
  </div>`;
}

function statusLineHtml(text, isError) {
  if (!text) return '';
  return `<p class="mb-s2 text-caveat ${isError ? 'text-state-warn' : 'text-ink-muted'}">${esc(text)}</p>`;
}

/* ── Time words ────────────────────────────────────────────────────────── */

const pad = (n) => String(n).padStart(2, '0');

/** "MM-DD" for another day, "HH:MM" when the earlier run was the same (local)
 *  day as the later one, so an immediate re-run reads "0 new since 14:05". */
export function sinceLabel(earlierIso, laterIso) {
  const a = new Date(/Z|[+-]\d\d:?\d\d$/.test(earlierIso) ? earlierIso : `${earlierIso}Z`);
  const b = new Date(/Z|[+-]\d\d:?\d\d$/.test(laterIso) ? laterIso : `${laterIso}Z`);
  if (Number.isNaN(a.getTime())) return String(earlierIso);
  const sameDay = !Number.isNaN(b.getTime())
    && a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  return sameDay ? `${pad(a.getHours())}:${pad(a.getMinutes())}` : `${pad(a.getMonth() + 1)}-${pad(a.getDate())}`;
}

const stamp = (iso) => {
  const a = new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(a.getTime()) ? String(iso)
    : `${pad(a.getMonth() + 1)}-${pad(a.getDate())} ${pad(a.getHours())}:${pad(a.getMinutes())}`;
};

/* ── Saved sources ─────────────────────────────────────────────────────── */

function savedHtml() {
  if (view.busy && !view.servers.length) return `<p class="text-caveat text-ink-muted">Loading…</p>`;
  // A refusal is not an empty list: say nothing about "none" when we could not look.
  if (view.loadFailed) {
    return `<button data-act="reload" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-s3 py-[3px] text-caveat text-accent-ink">Try again</button>`;
  }

  const header = `
    <div class="mb-s3 flex items-center justify-between">
      <span class="text-caveat text-ink-muted">${view.servers.length} saved source(s): the database servers registered once, with their credential</span>
      <span class="flex items-center gap-s2">
        <button data-act="register-db" class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink"
          >Register one database…</button>
        <button data-act="new-server" class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink"
          >+ Register a server</button>
      </span>
    </div>`;

  if (!view.servers.length) {
    return `${header}
      <p class="max-w-[60ch] text-answer text-ink">No saved sources yet.</p>
      <p class="max-w-[60ch] text-caveat text-ink-muted">
        Register a Postgres server's connection once and it becomes a source you can Run again. Or use
        "Discover on a server" to look once without saving anything.
      </p>`;
  }

  const rows = view.servers.map((s) => {
    const noCred = !s.db_user;
    const lastRun = s.last_run_at
      ? `run ${esc(stamp(s.last_run_at))} · ${s.last_run_candidate_count} found`
      : 'never run';
    return `<div class="mb-s2 rounded-sm border border-rule p-s2" data-source="${esc(s.slug)}">
      <div class="flex items-start justify-between gap-s2">
        <div class="min-w-0">
          <div class="truncate font-heading text-caveat text-ink">${esc(s.display_name)}</div>
          <div class="font-mono text-provenance text-ink-muted">${esc(s.db_type)} · ${esc(s.host)}:${s.port}${
            s.group_slug ? ` · ${esc(s.group_slug)}` : ''}${s.db_user ? ` · credential: ${esc(s.db_user)}` : ''}</div>
          <div class="mt-[2px] text-provenance text-ink-muted" data-last-run>${lastRun}</div>
          ${noCred ? `<div class="mt-[2px] text-provenance text-state-warn" data-no-credential>⚠ no credentials stored: this source cannot run until one is added</div>` : ''}
        </div>
        <div class="flex shrink-0 items-center gap-s2 text-caveat">
          <button data-run="${esc(s.slug)}" ${noCred ? 'disabled title="No credentials stored"' : ''}
            class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[2px] text-caveat text-accent-ink hover:border-rule-strong"
            >${s.last_run_at ? 'Run again' : 'Run'}</button>
          <button data-test="${esc(s.slug)}" class="cursor-pointer bg-transparent text-accent-ink underline"
            >Test</button>
          <button data-remove="${esc(s.slug)}" class="cursor-pointer bg-transparent text-state-warn underline"
            >Remove</button>
        </div>
      </div>
      ${serverEgeriaHtml(s)}
      ${egeriaHostLineHtml('server', s.slug, s.egeria_host)}
      <div class="mt-s2 text-provenance text-ink-muted">
        ${(s.databases || []).length
          ? `${s.databases.length} registered from this server:`
          : 'None registered from this server yet.'}
      </div>
      ${(s.databases || []).map((d) => `<div data-server-db="${esc(d.slug)}" class="mt-[2px] flex flex-wrap items-baseline gap-s2 text-provenance text-ink-muted">
        <span class="text-ink">${esc(d.display_name)}</span> · ${
          d.last_surveyed_at ? `surveyed ${esc(ago(d.last_surveyed_at))}` : 'never surveyed'}
        <button type="button" data-change-cred="${esc(d.slug)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">Change credentials…</button>
        <div data-cred-host="${esc(d.slug)}" class="w-full"></div>
        <div class="w-full">${egeriaHostLineHtml('database', d.slug, d.egeria_host, s.egeria_host || '')}</div>
      </div>`).join('')}
      <div data-test-result="${esc(s.slug)}"></div>
    </div>`;
  }).join('');

  return `${header}${rows}`;
}

/** PI-014, read only: the server's Egeria connection as registered (never a
 *  password: the summary does not carry one). */
function serverEgeriaHtml(s) {
  const parts = [];
  if (s.egeria_url) parts.push(esc(s.egeria_url));
  if (s.egeria_server) parts.push(`view server ${esc(s.egeria_server)}`);
  if (s.egeria_host) parts.push(`host ${esc(s.egeria_host)}`);
  return `<div data-server-egeria class="mt-[2px] text-provenance text-ink-muted">${
    parts.length ? `Egeria · ${parts.join(' · ')}` : 'no Egeria connection recorded'}</div>`;
}

async function runServerTest(el, slug) {
  const resultEl = el.querySelector(`[data-test-result="${cssEsc(slug)}"]`);
  if (resultEl) resultEl.innerHTML = `<div class="mt-s1 text-provenance text-ink-muted">Testing…</div>`;
  try {
    const data = await testDbServer(slug);
    if (resultEl) {
      resultEl.innerHTML = data.status === 'ok'
        ? `<div class="mt-s1 text-provenance text-state-ok">✓ Connected to ${esc(data.host)}:${data.port}. ${data.database_count} database(s) listed${
          data.connectable_count != null ? `, ${data.connectable_count} this credential can connect to` : ''}.</div>`
        : `<div class="mt-s1 text-provenance text-state-warn">✗ ${esc(data.error || 'Connection failed')}</div>`;
    }
  } catch (err) {
    if (resultEl) resultEl.innerHTML = `<div class="mt-s1 text-provenance text-state-warn">✗ ${esc(err.message)}</div>`;
  }
}

async function removeServerRow(el, slug) {
  if (!(await inPageConfirm({
    title: 'Remove server?',
    message: `Remove server "${slug}" and its linked database registrations?\nThis does not touch the actual database server.`,
    confirmLabel: 'Remove',
  }))) return;
  try {
    await deleteDbServer(slug);
    view.status = `Removed server "${slug}".`;
    view.statusIsError = false;
    if (view.cand.source && view.cand.source.slug === slug) view.cand = emptyCandidates();
    await loadServers(el);
    refreshGroupsAndSidebar();
  } catch (err) {
    view.status = failure(err, `remove "${slug}"`);
    view.statusIsError = true;
    render(el);
  }
}

/** Run a saved source: the response carries what is new since its last run. */
async function runSource(el, slug) {
  const srv = view.servers.find((s) => s.slug === slug);
  if (!srv) return;
  view.cand = emptyCandidates();
  view.cand.source = { kind: 'saved', slug, label: srv.display_name, group_slug: srv.group_slug || '' };
  view.cand.group = srv.group_slug || '';
  view.cand.investigation = state.investigation || '';
  view.cand.loading = true;
  view.status = '';
  render(el);
  try {
    const run = await runDatabaseSource(slug);
    view.cand.rows = run.candidates || [];
    view.cand.meta = {
      run_at: run.run_at, previous_run_at: run.previous_run_at, first_run: run.first_run,
      new_count: run.new_count, candidate_count: run.candidate_count,
    };
    // The saved row now remembers THIS run; reflect the stored values.
    srv.last_run_at = run.run_at;
    srv.last_run_candidate_count = run.candidate_count;
    view.cand.selected = new Set();
  } catch (err) {
    view.cand.error = failure(err, `run "${srv.display_name}"`);
  } finally {
    view.cand.loading = false;
    render(el);
  }
}

/* ── Register a server ─────────────────────────────────────────────────── */

function registerFormHtml() {
  const f = view.form;
  const field = (label, inner, extra = '') => `<div class="${extra}">
    <label class="mb-[2px] block text-caps uppercase tracking-caps text-ink-muted">${esc(label)}</label>
    ${inner}</div>`;
  const input = (key, placeholder = '', type = 'text') =>
    `<input data-f="${key}" type="${type}" placeholder="${esc(placeholder)}" value="${esc(f[key] ?? '')}"
       class="w-full rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">`;
  const groupOptions = view.groups.map((g) =>
    `<option value="${esc(g.slug)}" ${f.group_slug === g.slug ? 'selected' : ''}>${esc(g.display_name)}</option>`).join('');

  return `
    <div class="mb-s2 flex items-center gap-s2">
      <button data-act="back-to-servers" class="cursor-pointer bg-transparent text-caveat text-accent-ink underline">← Servers</button>
      <span class="font-heading text-caveat text-ink">Register Database Server</span>
    </div>
    <div class="grid grid-cols-2 gap-s2">
      ${field('Slug *', input('slug', 'my-pg-server'))}
      ${field('Display Name *', input('display_name', 'My PostgreSQL Server'))}
      ${field('Type', `<select data-f="db_type" class="w-full rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
        <option value="postgresql" ${f.db_type === 'postgresql' ? 'selected' : ''}>PostgreSQL</option>
      </select>`)}
      ${field('Port', input('port', '5432', 'number'))}
    </div>
    <div class="mt-s2">${field('Host *', input('host', 'localhost'))}</div>
    <div class="mt-s2">${field('Description', input('description', 'Optional description'))}</div>
    <div class="mt-s2">${field('Group', `<select data-f="group_slug" class="w-full rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
      <option value="">No group</option>${groupOptions}
    </select>`)}</div>
    <div class="mt-s3 border-t border-rule pt-s2">
      <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">DB Credentials <span class="normal-case text-ink-muted">(stored — used for Discover and surveys)</span></div>
      <div class="grid grid-cols-2 gap-s2">
        ${field('Username *', input('db_user', 'postgres'))}
        ${field('Password', input('db_password', '••••••••', 'password'))}
      </div>
    </div>
    <div class="mt-s3 border-t border-rule pt-s2">
      <button type="button" data-act="toggle-egeria" class="cursor-pointer bg-transparent text-caveat text-ink-muted hover:text-ink">
        ${view.showEgeria ? '▼' : '▶'} Egeria Connection <span class="text-ink-muted">(optional)</span>
      </button>
      ${view.showEgeria ? `<div class="mt-s2 space-y-s2">
        ${field('Egeria-visible Host (e.g. host.docker.internal)', input('egeria_host', 'host.docker.internal'))}
        ${field('Egeria Platform URL', input('egeria_url', 'https://localhost:9443'))}
        ${field('View Server', input('egeria_server', 'view-server'))}
        <p class="text-provenance text-ink-muted">Egeria calls run as you, or as Resource Explorer's service account for queued work.</p>
      </div>` : ''}
    </div>
    ${view.registerError ? `<p class="mt-s2 text-caveat text-state-warn">${esc(view.registerError)}</p>` : ''}
    ${view.testResult ? `<p class="mt-s2 text-caveat ${view.testResult.ok ? 'text-state-ok' : 'text-state-warn'}">${esc(view.testResult.message)}</p>` : ''}
    <div class="mt-s3 flex items-center gap-s2">
      <button data-act="test-inline" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-s3 py-[3px] text-caveat text-accent-ink" ${view.busy ? 'disabled' : ''}
        >⚡ Test</button>
      <button data-act="submit-register" class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink" ${view.busy ? 'disabled' : ''}
        >${view.busy ? 'Registering…' : 'Register Server'}</button>
    </div>`;
}

function readFormFromDom(el) {
  el.querySelectorAll('[data-f]').forEach((inp) => {
    const key = inp.dataset.f;
    if (inp.type === 'number') view.form[key] = parseInt(inp.value, 10) || 0;
    else view.form[key] = inp.value.trim();
  });
  applyEgeriaHostDefault();
}

/** `egeria_host` empty and the DB host is `localhost`/`127.0.0.1`: Egeria
 *  runs in its own container, so "localhost" from THERE means the Egeria
 *  container itself, never the Mac's own Postgres — `host.docker.internal`
 *  is what actually reaches back out. Found live, 2026-09-27: nothing
 *  filled this in, so `databases.py`'s own runtime fallback (`egeria_host
 *  or host`) silently used the DB's own "localhost" instead, which is
 *  wrong whenever Egeria is in Docker (the common case here). Only sets
 *  it when still empty — never overwrites a value the person typed,
 *  including one they deliberately cleared back to "" to opt out.
 *  Runs on every `readFormFromDom()` (test/submit time) rather than as a
 *  live keystroke listener, since `egeria_host`'s own input only exists in
 *  the DOM once the collapsed "Egeria Connection" section is expanded —
 *  reading `view.form` directly here works whether or not it ever was. */
function applyEgeriaHostDefault() {
  const f = view.form;
  if (f.egeria_host) return;
  if (f.host === 'localhost' || f.host === '127.0.0.1') {
    f.egeria_host = 'host.docker.internal';
  }
}

async function testInline(el) {
  readFormFromDom(el);
  const f = view.form;
  if (!f.host || !f.db_user) {
    view.testResult = { ok: false, message: 'Enter host and username first.' };
    render(el);
    return;
  }
  view.busy = true;
  render(el);
  try {
    const data = await testDbServerInline({
      host: f.host, port: f.port || 5432, db_user: f.db_user, db_password: f.db_password, db_type: f.db_type,
    });
    view.testResult = data.status === 'ok'
      ? { ok: true, message: `✓ Connected — ${data.database_count} database(s) visible · ${(data.server_version || '').split(' ').slice(0, 2).join(' ')}` }
      : { ok: false, message: `✗ ${data.error || 'Connection failed'}` };
  } catch (err) {
    view.testResult = { ok: false, message: `✗ Request failed: ${err.message}` };
  } finally {
    view.busy = false;
    render(el);
  }
}

async function submitRegister(el) {
  readFormFromDom(el);
  const f = view.form;
  view.registerError = '';
  if (!f.slug || !f.display_name || !f.host || !f.db_user) {
    view.registerError = 'Slug, display name, host, and username are required.';
    render(el);
    return;
  }
  view.busy = true;
  render(el);
  try {
    await registerDbServer(f);
    view.form = emptyRegisterForm();
    view.showEgeria = false;
    view.testResult = null;
    view.mode = 'list';
    view.status = `Registered server "${f.slug}".`;
    view.statusIsError = false;
    await loadServers(el);
    refreshGroupsAndSidebar();
  } catch (err) {
    view.registerError = err.message;
  } finally {
    view.busy = false;
    render(el);
  }
}


/* ── Discover on a server (one-off) ────────────────────────────────────── */

function discoverHtml() {
  const o = view.oneOff;
  const field = (label, inner) => `<div>
    <label class="mb-[2px] block text-caps uppercase tracking-caps text-ink-muted">${esc(label)}</label>${inner}</div>`;
  const input = (key, placeholder, type = 'text', val = '') =>
    `<input data-oo="${key}" type="${type}" placeholder="${esc(placeholder)}" ${type === 'password' ? '' : `value="${esc(val)}"`}
       autocomplete="off" class="w-full rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">`;
  const saved = !!o.savedSlug;
  return `
    <p class="mb-s2 max-w-[70ch] text-caveat text-ink-muted">
      List the databases on a Postgres server once, without registering it. The password is used for this one
      connection and kept in memory only. Save the server as a source if you want to Run it again or register
      databases from it.
    </p>
    <div class="grid grid-cols-2 gap-s2 sm:grid-cols-4">
      ${field('Host', input('host', 'pg.regional.example', 'text', o.host))}
      ${field('Port', input('port', '5432', 'number', o.port || ''))}
      ${field('Username', input('db_user', 'scout_ro', 'text', o.db_user))}
      ${field('Password', input('db_password', '', 'password'))}
    </div>
    <div class="mt-s2 flex items-center gap-s2">
      <button data-act="discover-inline" class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink"
        ${view.cand.loading ? 'disabled' : ''}>${view.cand.loading ? 'Connecting…' : 'Discover'}</button>
    </div>
    ${view.cand.rows.length && view.cand.source && view.cand.source.fromOneOff ? `
    <div class="mt-s3 rounded-sm border border-rule p-s2" data-save-source>
      ${saved
        ? `<span class="text-caveat text-state-ok">✓ Saved as the source "${esc(o.savedSlug)}". Its candidates below can now be registered.</span>`
        : `<div class="mb-s1 text-caps uppercase tracking-caps text-ink-muted">Save as a source <span class="normal-case">(stores the credential with it)</span></div>
           <div class="flex flex-wrap items-center gap-s2">
             <input data-oo="slug" placeholder="regional-pg" value="${esc(o.slug)}" autocomplete="off"
               class="rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
             <input data-oo="display_name" placeholder="Display name" value="${esc(o.display_name)}" autocomplete="off"
               class="rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
             <button data-act="save-source" class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink"
               ${view.busy ? 'disabled' : ''}>Save as a source</button>
           </div>`}
    </div>` : ''}`;
}

async function discoverInline(el) {
  captureInputs(el);
  const o = view.oneOff;
  if (!o.host || !o.db_user) {
    view.status = 'Enter a host and a username first.';
    view.statusIsError = true;
    render(el);
    return;
  }
  view.cand = emptyCandidates();
  view.cand.source = { kind: 'oneoff', fromOneOff: true, slug: '', label: `${o.host}:${o.port || 5432}`, group_slug: '' };
  view.cand.investigation = state.investigation || '';
  view.cand.loading = true;
  view.status = '';
  o.savedSlug = '';
  render(el);
  try {
    const data = await discoverDatabasesInline({
      host: o.host, port: o.port || 5432, db_user: o.db_user, db_password: o.db_password, db_type: 'postgresql',
    });
    view.cand.rows = data.candidates || [];
    view.cand.meta = { run_at: null, previous_run_at: null, first_run: null, new_count: null,
      candidate_count: view.cand.rows.length };
  } catch (err) {
    view.cand.error = failure(err, 'discover on this server');
  } finally {
    view.cand.loading = false;
    render(el);
  }
}

/** Save the one-off server as a source. The registration is the same call the
 *  Register form makes; afterwards the listed candidates belong to a saved
 *  source, so the confirm can register them. */
async function saveSource(el) {
  captureInputs(el);
  const o = view.oneOff;
  if (!o.slug || !o.display_name) {
    view.status = 'A saved source needs a slug and a display name.';
    view.statusIsError = true;
    render(el);
    return;
  }
  view.busy = true;
  render(el);
  try {
    const f = { ...emptyRegisterForm(), slug: o.slug, display_name: o.display_name, host: o.host,
      port: o.port || 5432, db_user: o.db_user, db_password: o.db_password };
    if (f.host === 'localhost' || f.host === '127.0.0.1') f.egeria_host = 'host.docker.internal';
    await registerDbServer(f);
    o.savedSlug = o.slug;
    o.db_password = '';          // stored with the source now; drop the in-memory copy
    const pwBox = el.querySelector('[data-oo="db_password"]');
    if (pwBox) pwBox.value = '';  // render() reads the boxes back first, so clear the box too
    view.cand.rows.forEach((r) => { r.server_slug = o.slug; });
    view.cand.source = { kind: 'saved', fromOneOff: true, slug: o.slug, label: o.display_name, group_slug: '' };
    view.status = `Saved "${o.display_name}" as a source.`;
    view.statusIsError = false;
    const servers = await listDbServers().catch(() => null);
    if (servers) view.servers = servers;
  } catch (err) {
    view.status = failure(err, 'save this source');
    view.statusIsError = true;
  } finally {
    view.busy = false;
    render(el);
  }
}

/* ── From a file ───────────────────────────────────────────────────────── */

/** Read a chosen file's text. `File.text()` where it exists, FileReader otherwise. */
async function readFileText(file) {
  if (typeof file.text === 'function') return file.text();
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result || ''));
    r.onerror = () => reject(r.error || new Error('could not read the file'));
    r.readAsText(file);
  });
}

async function loadFile(el, file) {
  const prev = view.file;
  view.file = emptyFile();
  view.file.name = file.name || 'the file';
  view.file.group = prev.group;
  view.file.investigation = prev.investigation;
  view.file.loading = true;
  render(el);
  try {
    // A credential never leaves the browser: cells under a credential-like
    // header are blanked before the text is kept or posted (csv-guard.js).
    view.file.text = blankCredentialCells(await readFileText(file)).text;
  } catch (err) {
    view.file.error = failure(err, 'read that file');
    view.file.loading = false;
    render(el);
    return;
  }
  await runPreview(el);
}

/** (Re)plan the file on the server. Nothing is written. Keeps the outcome line. */
async function runPreview(el) {
  const f = view.file;
  f.loading = true;
  f.error = '';
  render(el);
  try {
    f.preview = await previewDiscoveryFile(f.text, f.serverChoices, f.investigation);
  } catch (err) {
    f.error = failure(err, 'read this file');
    f.preview = null;
  } finally {
    f.loading = false;
    render(el);
  }
}

const COUNT_LABELS = [
  ['new', (n) => `${n} new`],
  ['already_registered', (n) => `${n} already registered`],
  ['duplicate_in_file', (n) => `${n} duplicate${n === 1 ? '' : 's'} in the file`],
  ['invalid', (n) => `${n} invalid`],
  ['not_importable', (n) => `${n} of a kind not importable here`],
];

function fileHtml() {
  const f = view.file;
  const chooser = `
    <p class="mb-s2 max-w-[70ch] text-caveat text-ink-muted">
      A CSV with a resource_type and an address on every row. A database row names where its credential comes
      from: <span class="font-mono">server</span> (a saved source) or <span class="font-mono">connection_ref</span>
      (a reference name). A credential is never read from a file.
    </p>
    <div class="mb-s2 flex flex-wrap items-center gap-s2">
      <label class="text-caveat text-ink-muted">Choose a CSV file
        <input type="file" data-file-input accept=".csv,text/csv,text/plain" class="ml-s2 text-caveat text-ink"></label>
      ${f.name ? `<span class="font-mono text-provenance text-ink-muted" data-file-name>${esc(f.name)}</span>` : ''}
    </div>`;
  if (f.loading) return `${chooser}<p class="text-caveat text-ink-muted">Reading the file…</p>`;
  if (f.error) return `${chooser}<p class="text-caveat text-state-warn" data-file-error>${esc(f.error)}</p>`;
  const p = f.preview;
  if (!p) return chooser;
  if (p.refused) {
    return `${chooser}<div class="rounded-sm border border-rule p-s2" data-file-refused>
      <p class="text-caveat text-state-warn">${esc(p.refused)}</p>
      <p class="mt-s1 text-provenance text-ink-muted">Header row found</p>
      <p class="font-mono text-caveat text-ink" data-file-header>${esc((p.header || []).join(', '))}</p>
    </div>`;
  }
  return `${chooser}${previewHtml(p)}${fileConfirmHtml(p)}`;
}

function countsHtml(p) {
  const items = COUNT_LABELS.map(([key, label]) => {
    const n = p.counts[key];
    const inInv = key === 'already_registered' ? alreadyInCount(p) : 0;
    const extra = key === 'new' && p.counts.needs_person
      ? ` <span class="text-state-warn" data-needs-person-count>(⚠ ${p.counts.needs_person} need a person)</span>` : '';
    const words = label(n) + (inInv ? ` (${inInv} already in ${investigationName(view.file.investigation)})` : '');
    if (!n) return `<span class="text-ink-muted" data-count-zero="${key}">${esc(words)}</span>`;
    const open = view.file.open === key;
    return `<button data-count="${key}" class="cursor-pointer border-0 bg-transparent p-0 text-caveat underline ${
      open ? 'text-ink' : 'text-accent-ink'}">${esc(words)}</button>${extra}`;
  });
  return `<p class="mb-s2 text-caveat text-ink" data-counts><span class="font-heading">${p.rows} row${
    p.rows === 1 ? '' : 's'}</span> · ${items.join(' · ')}</p>`;
}

function previewHtml(p) {
  const messages = (p.messages || []).map((m) =>
    `<p class="mb-s1 text-provenance text-ink-muted" data-file-message>${esc(m)}</p>`).join('');
  return `${countsHtml(p)}${messages}${openLinesHtml(p)}${proposedHtml(p)}`;
}

const lineCell = (i) => `<td class="py-s1 pr-s2 font-mono text-provenance text-ink-muted">line ${i.line}</td>`;
const addrCell = (i) => `<td class="py-s1 pr-s2 max-w-[260px] truncate font-mono text-caveat text-ink" title="${esc(i.address)}">${
  esc(i.address || '(no address)')}<span class="text-ink-muted"> · ${esc(i.resource_type)}</span></td>`;

function openLinesHtml(p) {
  const key = view.file.open;
  const list = p.lines && p.lines[key];
  if (!key || !list || !list.length) return '';
  const f = view.file;
  const rows = list.map((i) => {
    let lead = '<td class="py-s1 pr-s2"></td>';
    let what = `<span class="text-ink-muted">${esc(i.message)}</span>`;
    if (key === 'new' && i.needs_person) {
      const chosen = f.serverChoices[i.line] || '';
      const opts = (p.servers || []).map((sv) => {
        const match = (i.matching_servers || []).includes(sv.slug);
        return `<option value="${esc(sv.slug)}" ${chosen === sv.slug ? 'selected' : ''}>${esc(sv.display_name)} (${
          esc(sv.host)}:${sv.port})${match ? ' · same host' : ''}</option>`;
      }).join('');
      what = `<span class="text-state-warn">⚠ needs a person: name a server or a credential</span>
        <select data-choose-server="${i.line}" class="ml-s2 rounded-sm border border-rule bg-transparent px-2 py-[2px] text-caveat text-ink">
          <option value="">choose a server…</option>${opts}</select>`;
    } else if (key === 'new') {
      lead = `<td class="py-s1 pr-s2"><input type="checkbox" data-file-line="${i.line}" ${
        f.deselected.has(i.line) ? '' : 'checked'}></td>`;
      what = `<span class="text-ink-muted">${i.server ? `on ${esc(i.server)}` : `reference ${esc(i.connection_ref)}`}${
        i.group ? ` · group ${esc(i.group)}` : ''}</span>`;
    } else if (key === 'already_registered') {
      if (isAlreadyIn(p, i)) {
        lead = '<td class="py-s1 pr-s2"></td>';
        what = `<span class="text-ink-muted" data-already-in>already registered${i.slug ? ` as ${esc(i.slug)}` : ''}; already in ${
          esc(investigationName(f.investigation))}, nothing to add</span>`;
      } else {
        lead = `<td class="py-s1 pr-s2"><input type="checkbox" data-file-line="${i.line}" ${
          f.deselected.has(i.line) ? '' : 'checked'}></td>`;
        what = `<span class="text-ink-muted">already registered${i.slug ? ` as ${esc(i.slug)}` : ''}; only added to the investigation's scope</span>`;
      }
    }
    return `<tr class="border-b border-rule" data-file-row="${i.line}">${lead}${lineCell(i)}${addrCell(i)}<td class="py-s1 text-caveat">${what}</td></tr>`;
  }).join('');
  return `<div class="mb-s2 max-h-[30vh] overflow-auto rounded-sm border border-rule" data-lines="${key}">
    <table class="w-full text-left"><tbody>${rows}</tbody></table></div>`;
}

function proposedHtml(p) {
  const ch = p.proposed_changes || [];
  if (!ch.length) return '';
  const f = view.file;
  const byField = {};
  ch.forEach((c) => { byField[c.field] = (byField[c.field] || 0) + 1; });
  const head = Object.entries(byField).map(([k, n]) => `${n} row${n === 1 ? '' : 's'} would change ${k}`).join(' · ');
  const rows = ch.map((c) => {
    const id = `${c.line}|${c.field}`;
    return `<li class="mb-s1 text-caveat"><label>
      <input type="checkbox" data-accept-change="${esc(id)}" ${c.blocked ? 'disabled' : ''} ${f.accept.has(id) ? 'checked' : ''}>
      <span class="font-mono text-provenance text-ink-muted">line ${c.line}</span>
      ${esc(c.slug)}: ${esc(c.field)} <span class="text-ink-muted">${esc(c.current || '(none)')}</span> → ${esc(c.proposed)}
      ${c.blocked ? `<span class="text-state-warn"> · ${esc(c.blocked)}</span>` : ''}</label></li>`;
  }).join('');
  return `<div class="mb-s2 rounded-sm border border-rule p-s2" data-proposed>
    <p class="mb-s1 text-caveat text-ink">${esc(head)}</p>
    <p class="mb-s1 text-provenance text-ink-muted">Proposed, not applied. Tick the ones to apply when you confirm.</p>
    <ul class="m-0 list-none p-0">${rows}</ul></div>`;
}

/** True when the server says this already-registered row is already a member of
 *  the investigation now chosen. The flag is only trusted for the investigation
 *  the preview was planned against: a preview still showing the previous choice
 *  must not suppress rows for the new one. */
function isAlreadyIn(p, i) {
  const f = view.file;
  return !!f.investigation && (p.investigation || '') === f.investigation && i.in_investigation === true;
}

const alreadyInCount = (p) => ((p.lines && p.lines.already_registered) || []).filter((i) => isAlreadyIn(p, i)).length;

/** The lines a confirm would act on, and how many of them count toward "Add these N".
 *  An already-registered row counts only if it is not yet in the chosen investigation
 *  (a scope export imported into a second investigation adds them; re-importing into
 *  the same one adds nothing, and must not offer to). */
function fileSelection(p) {
  const f = view.file;
  const ready = ((p.lines && p.lines.new) || []).filter((i) => !i.needs_person && !f.deselected.has(i.line));
  const known = ((p.lines && p.lines.already_registered) || [])
    .filter((i) => !f.deselected.has(i.line) && !isAlreadyIn(p, i));
  const lines = [...ready.map((i) => i.line), ...(f.investigation ? known.map((i) => i.line) : [])];
  const n = ready.length + (f.investigation ? known.length : 0);
  return { lines, n, ready: ready.length, known: known.length };
}

function fileConfirmHtml(p) {
  const f = view.file;
  const sel = fileSelection(p);
  const nChanges = f.accept.size;
  const label = !sel.n
    ? (nChanges ? `Apply these ${nChanges} change${nChanges === 1 ? '' : 's'}` : 'Nothing to add')
    : '';
  return `<div class="my-s3 h-px bg-rule"></div>
    ${confirmBarHtml(f, sel.n, { disabled: view.busy || (!sel.n && !nChanges), label })}
    ${f.outcome ? `<p class="mt-s2 text-caveat ${f.outcomeIsError ? 'text-state-warn' : 'text-state-ok'}" data-outcome>${esc(f.outcome)}</p>` : ''}`;
}

/** The confirm for a file: re-plans on the server from the text, then applies
 *  the ticked lines. Local registry writes only; nothing goes to Egeria. */
async function confirmFile(el) {
  const f = view.file;
  if (!f.preview || f.preview.refused) return;
  const sel = fileSelection(f.preview);
  const accept = [...f.accept].map((k) => { const [line, field] = k.split('|'); return { line: Number(line), field }; });
  view.busy = true;
  render(el);
  let res;
  try {
    res = await importDiscoveryFile({
      text: f.text, lines: sel.lines, server_choices: f.serverChoices, group: f.group,
      investigation: f.investigation, accept_changes: accept,
    });
  } catch (err) {
    view.busy = false;
    f.outcome = err && err.status === 401 ? 'Sign in to add these.' : `Could not add these: ${err && err.message ? err.message : err}`;
    f.outcomeIsError = true;
    render(el);
    return;
  }
  view.busy = false;
  const c = res.counts || {};
  const parts = [];
  if (c.registered) parts.push(`Registered ${c.registered} database(s).`);
  if (f.investigation && c.scoped) parts.push(`Added ${c.scoped} to ${investigationName(f.investigation)}.`);
  if (c.changed) parts.push(`Changed ${c.changed} row(s).`);
  if (!c.registered && !c.scoped && !c.changed) parts.push('Nothing was added.');
  const failures = res.failures || [];
  if (failures.length) parts.push(`${failures.length} failed: ${failures.map((x) => `line ${x.line}: ${x.message}`).join('; ')}`);
  f.outcome = parts.join(' ');
  f.outcomeIsError = failures.length > 0;
  f.accept = new Set();
  const servers = await listDbServers().catch(() => null);
  if (servers) view.servers = servers;
  await runPreview(el);          // the counts now reflect what was just added
  refreshGroupsAndSidebar();
  // If that investigation's page is the open pane, show the new members now.
  if (f.investigation && c.scoped) refreshOpenInvestigation(f.investigation);
}

/* ── The candidate table (shared by every tab) and the one confirm ─────── */

const HIDDEN_VERDICTS = new Set(['abandoned', 'ignored']);

const selectable = (r) => !r.is_registered && r.can_connect !== false;

function mutedNote(text) { return `<span class="text-ink-muted">${esc(text)}</span>`; }

/** One candidate row. Exported so the states it can be in are testable as a table. */
export function candidateRowHtml(r, i, checked) {
  const registered = !!r.is_registered;
  const noConnect = r.can_connect === false;
  const size = r.size_pretty != null && r.size_pretty !== ''
    ? esc(r.size_pretty)
    : '<span class="text-ink-muted" data-size-unread>? not readable with this credential</span>';
  const owner = r.owner != null && r.owner !== '' ? esc(r.owner) : mutedNote('not read');
  const desc = r.description == null ? mutedNote('not reported by this source')
    : r.description === '' ? `<span class="text-ink-muted" data-desc-none>none set</span>`
    : esc(r.description);
  const connect = noConnect
    ? `<span class="text-state-warn" data-connect="no">? can't connect with this credential</span>`
    : `<span data-connect="yes">✓ yes</span>`;
  const v = r.verdict;
  const verdict = v
    ? `<span title="${esc(v.reason || '')}">${esc(v.disposition)}${v.reason ? ` · “${esc(v.reason)}”` : ''}</span>`
    : mutedNote('undecided');
  const newMark = r.is_new === true ? ' <span class="text-state-ok" data-new>new</span>' : '';
  return `<tr class="border-b border-rule ${registered ? 'opacity-50' : ''}" data-cand="${esc(r.key || r.name)}"
      data-state="${registered ? 'registered' : noConnect ? 'no-connect' : 'new'}">
    <td class="py-s1 pr-s2"><input type="checkbox" data-cand-row="${i}"
      ${registered ? 'disabled checked' : (noConnect ? 'disabled' : (checked ? 'checked' : ''))}></td>
    <td class="py-s1 pr-s2 max-w-[220px] truncate font-mono text-caveat text-ink" title="${esc(r.address || '')}">${esc(r.name)}${newMark}${
      registered ? ' <span class="text-ink-muted" data-registered>already registered</span>' : ''}${
      registered ? credentialMarkHtml(r, 'block text-provenance') : ''}
      ${r.server_slug ? `<div class="text-provenance text-ink-muted">${esc(r.server_slug)}</div>` : ''}</td>
    <td class="py-s1 pr-s2 text-caveat text-ink-muted">${size}</td>
    <td class="py-s1 pr-s2 text-caveat text-ink-muted">${owner}</td>
    <td class="py-s1 pr-s2 max-w-[220px] truncate text-caveat text-ink-muted">${desc}</td>
    <td class="py-s1 pr-s2 text-caveat text-ink-muted">${connect}</td>
    <td class="py-s1 pr-s2 text-caveat">${verdict}</td>
    <td class="py-s1 text-caveat text-ink-muted">after registration</td>
  </tr>`;
}

function metaLineHtml() {
  const m = view.cand.meta;
  if (!m) return '';
  const label = view.cand.source ? view.cand.source.label : '';
  let words;
  if (m.run_at == null) {
    words = `${m.candidate_count} found on ${label}`;            // a one-off has no previous run to compare with
  } else if (m.first_run) {
    words = `First run of ${label}: ${m.candidate_count} found. Nothing to compare with yet.`;
  } else {
    words = `<span data-new-since>${m.new_count} new since ${esc(sinceLabel(m.previous_run_at, m.run_at))}</span> · ${m.candidate_count} found on ${esc(label)}`;
  }
  return `<p class="mb-s2 text-caveat text-ink" data-run-meta>${m.run_at == null ? esc(words) : words}</p>`;
}

function investigationName(slug) {
  const inv = view.investigations.find((i) => i.slug === slug)
    || (state.investigations || []).find((i) => i.slug === slug);
  return inv ? (inv.display_name || inv.slug) : slug;
}

/** The ONE confirm bar: destination group, investigation, and the button. Both
 *  the candidate table and the From-a-file preview end in it, so "Add these N to
 *  <investigation>" reads and behaves the same wherever the candidates came from.
 *  `t` is the state it edits (view.cand or view.file). */
function confirmBarHtml(t, nSelected, { disabled = false, label = '' } = {}) {
  const groupOptions = view.groups.map((g) =>
    `<option value="${esc(g.slug)}" ${t.group === g.slug ? 'selected' : ''}>${esc(g.display_name)}</option>`).join('');
  const invOptions = view.investigations.map((i) =>
    `<option value="${esc(i.slug)}" ${t.investigation === i.slug ? 'selected' : ''}>${esc(i.display_name || i.slug)}</option>`).join('');
  const buttonLabel = label || (t.investigation
    ? `Add these ${nSelected} to ${investigationName(t.investigation)}`
    : `Register these ${nSelected}`);
  return `<div class="mt-s3 flex flex-wrap items-center gap-s2 border-t border-rule pt-s2" data-confirm>
      <label class="text-caveat text-ink-muted">Group
        <select data-act="group" class="ml-s2 rounded-sm border border-rule bg-transparent px-2 py-[2px] text-caveat text-ink">
          <option value="">No group</option>${groupOptions}
        </select></label>
      <label class="text-caveat text-ink-muted">Investigation
        <select data-act="investigation" class="ml-s2 rounded-sm border border-rule bg-transparent px-2 py-[2px] text-caveat text-ink">
          <option value="">none</option>${invOptions}
        </select></label>
      <button data-act="confirm" class="ml-auto cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink"
        ${disabled ? 'disabled' : ''}>${esc(view.busy ? 'Adding…' : buttonLabel)}</button>
    </div>`;
}

function candidatesHtml() {
  const c = view.cand;
  if (c.loading) return `<div class="my-s3 h-px bg-rule"></div><p class="text-caveat text-ink-muted">Connecting to the server…</p>`;
  if (c.error) return `<div class="my-s3 h-px bg-rule"></div><p class="text-caveat text-state-warn" data-run-error>${esc(c.error)}</p>`;
  if (!c.source) return '';
  if (!c.rows.length) {
    return `<div class="my-s3 h-px bg-rule"></div>${metaLineHtml()}<p class="text-caveat text-ink-muted">No databases were listed on this server.</p>`;
  }

  const rows = c.rows.map((r, i) => candidateRowHtml(r, i, c.selected.has(i))).join('');
  const nSelected = [...c.selected].filter((i) => c.rows[i] && selectable(c.rows[i])).length;
  const nRegistered = c.rows.filter((r) => r.is_registered).length;
  const nNoConnect = c.rows.filter((r) => !r.is_registered && r.can_connect === false).length;
  const selectableCount = c.rows.filter((r) => selectable(r) && !HIDDEN_VERDICTS.has(r.verdict && r.verdict.disposition)).length;
  const needsSave = !c.source.slug && c.source.kind === 'oneoff';

  return `
    <div class="my-s3 h-px bg-rule"></div>
    ${metaLineHtml()}
    <div class="mb-s2 flex flex-wrap items-center gap-s2 text-caveat">
      <button data-act="select-all-new" class="cursor-pointer bg-transparent text-accent-ink underline"
        >Select all new${selectableCount ? ` (${selectableCount})` : ''}</button>
      <button data-act="select-none" class="cursor-pointer bg-transparent text-ink-muted underline">Deselect all</button>
      <button data-act="export-candidates" class="cursor-pointer bg-transparent text-accent-ink underline"
        >Export CSV</button>
      <span class="text-ink-muted" data-selection-count>${nSelected} selected · ${nRegistered} already registered${
        nNoConnect ? ` · ${nNoConnect} can't connect with this credential` : ''}</span>
    </div>
    <div class="max-h-[40vh] overflow-auto rounded-sm border border-rule">
      <table class="w-full text-left" data-candidate-table>
        <thead class="sticky top-0 bg-paper text-caps uppercase tracking-caps text-ink-muted">
          <tr class="border-b border-rule">
            <th class="px-2 py-s1"></th>
            <th class="px-2 py-s1 font-normal">Database</th>
            <th class="px-2 py-s1 font-normal">Size</th>
            <th class="px-2 py-s1 font-normal">Owner role</th>
            <th class="px-2 py-s1 font-normal">Description</th>
            <th class="px-2 py-s1 font-normal">Connect with this credential</th>
            <th class="px-2 py-s1 font-normal">Prior verdict</th>
            <th class="px-2 py-s1 font-normal">activity · after registration</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
    ${nNoConnect ? `<p class="mt-s1 text-provenance text-ink-muted">A database marked "can't connect" stays a candidate until a credential that can connect is given; it cannot be registered with this one.</p>` : ''}
    ${confirmBarHtml(c, nSelected, { disabled: view.busy || !nSelected || needsSave, label: '' })}
    ${needsSave ? `<p class="mt-s1 text-provenance text-ink-muted" data-needs-save>Save this server as a source above to register databases from it.</p>` : ''}
    ${c.outcome ? `<p class="mt-s2 text-caveat ${c.outcomeIsError ? 'text-state-warn' : 'text-state-ok'}" data-outcome>${esc(c.outcome)}</p>` : ''}`;
}

/** The one confirm. Registers each selected database from its saved source (a
 *  local registry write), puts it in the chosen group, and, when an
 *  investigation is chosen, adds it to that investigation's scope. Nothing is
 *  written to Egeria. */
async function confirmAdd(el) {
  const c = view.cand;
  const slug = c.source && c.source.slug;
  if (!slug) return;
  const chosen = [...c.selected].map((i) => c.rows[i]).filter((r) => r && selectable(r));
  if (!chosen.length) {
    c.outcome = 'Select at least one database.';
    c.outcomeIsError = true;
    render(el);
    return;
  }
  view.busy = true;
  render(el);
  const registered = [];
  const scoped = [];
  const failures = [];
  const when = new Date().toISOString().slice(0, 10);
  for (const r of chosen) {
    let dbSlug;
    try {
      const res = await addDiscoveredDatabase(slug, r.name);
      dbSlug = res.slug;
      registered.push(r);
      r.is_registered = true;
      r.registered_slug = dbSlug;
    } catch (err) {
      failures.push(`${r.name}: ${err && err.status === 401 ? 'sign in to register' : err.message}`);
      continue;
    }
    if (c.group) {
      try { await assignGroup(dbSlug, c.group, 'database'); } catch (err) {
        failures.push(`${r.name}: registered, but the group was not set (${err.message})`);
      }
    }
    if (c.investigation) {
      try {
        await addInvestigationMember(c.investigation, 'database', dbSlug,
          `Found by ${c.source.label} on ${when}`);
        scoped.push(r);
      } catch (err) {
        failures.push(`${r.name}: registered, but not added to the investigation (${err && err.status === 401 ? 'sign in first' : err.message})`);
      }
    }
  }
  view.busy = false;
  c.selected = new Set();
  const parts = [];
  if (registered.length) parts.push(`Registered ${registered.length} database(s) from "${c.source.label}".`);
  if (c.investigation && scoped.length) parts.push(`Added ${scoped.length} to ${investigationName(c.investigation)}.`);
  if (!registered.length) parts.push('No databases were registered.');
  if (failures.length) parts.push(`${failures.length} failed: ${failures.join('; ')}`);
  c.outcome = parts.join(' ');
  c.outcomeIsError = !registered.length || failures.length > 0;
  const servers = await listDbServers().catch(() => null);
  if (servers) view.servers = servers;
  render(el);
  refreshGroupsAndSidebar();
  // If that investigation's page is the open pane, show the new members now.
  if (c.investigation && scoped.length) refreshOpenInvestigation(c.investigation);
}

/** Download the candidate table as CSV (the shared column contract). The rows sent
 *  are the ones on screen; none carries a credential. */
async function exportCandidates(el) {
  const c = view.cand;
  if (!c.source || !c.rows.length) return;
  try {
    const { text, filename } = await fetchCandidatesCsv({
      label: c.source.label || '', server_slug: c.source.slug || '',
      candidates: c.rows.map((r) => ({ name: r.name, address: r.address, server_slug: r.server_slug, verdict: r.verdict })),
    });
    saveCsv(text, filename);
    c.outcome = `Exported ${c.rows.length} candidate(s) as ${filename}.`;
    c.outcomeIsError = false;
  } catch (err) {
    c.outcome = failure(err, 'export the candidates');
    c.outcomeIsError = true;
  }
  render(el);
}

/* ── Wiring ─────────────────────────────────────────────────────────────── */

function cssEsc(s) {
  return String(s).replace(/["\\]/g, '\\$&');
}

function bind(el) {
  el.querySelectorAll('[data-tab]').forEach((b) => b.addEventListener('click', () => {
    captureInputs(el);
    if (view.tab !== b.dataset.tab) {
      view.tab = b.dataset.tab;
      view.mode = 'list';
      view.status = '';
      // A candidate set belongs to the source that produced it.
      view.cand = emptyCandidates();
      if (view.tab !== 'discover') { view.oneOff.db_password = ''; }
    }
    render(el);
  }));

  el.querySelector('[data-act="reload"]')?.addEventListener('click', () => loadServers(el));
  el.querySelector('[data-act="new-server"]')?.addEventListener('click', () => {
    view.mode = 'register';
    view.form = emptyRegisterForm();
    view.showEgeria = false;
    view.registerError = '';
    view.testResult = null;
    render(el);
  });
  el.querySelector('[data-act="register-db"]')?.addEventListener('click', () => {
    resetRegisterOneDatabase();
    view.mode = 'register-db';
    view.status = '';
    render(el);
  });
  el.querySelector('[data-act="back-to-servers"]')?.addEventListener('click', () => {
    view.mode = 'list';
    view.status = '';
    render(el);
  });
  el.querySelector('[data-act="toggle-egeria"]')?.addEventListener('click', () => {
    readFormFromDom(el);
    view.showEgeria = !view.showEgeria;
    render(el);
  });
  el.querySelector('[data-act="test-inline"]')?.addEventListener('click', () => testInline(el));
  el.querySelector('[data-act="submit-register"]')?.addEventListener('click', () => submitRegister(el));

  el.querySelectorAll('[data-change-cred]').forEach((b) => b.addEventListener('click', () => {
    const slug = b.dataset.changeCred;
    const hostEl = el.querySelector(`[data-cred-host="${cssEsc(slug)}"]`);
    if (!hostEl) return;
    hostEl.innerHTML = credentialChangeHtml({ slug, db_user: '' });
    bindCredentialChange(hostEl, slug, { onCancel: () => { hostEl.innerHTML = ''; } });
  }));
  el.querySelectorAll('[data-eh]').forEach((line) => {
    const [kind, slug] = line.dataset.eh.split(/:(.*)/s);
    bindEgeriaHostEdit(line, kind, slug, kind === 'server' ? { onCarried: (slugs, value) => {
      slugs.forEach((sl) => {
        const v = el.querySelector(`[data-eh="database:${cssEsc(sl)}"] [data-eh-value]`);
        if (v) v.textContent = value;
      });
    } } : {});
  });
  el.querySelectorAll('[data-test]').forEach((b) =>
    b.addEventListener('click', () => runServerTest(el, b.dataset.test)));
  el.querySelectorAll('[data-run]').forEach((b) =>
    b.addEventListener('click', () => { if (!b.disabled) runSource(el, b.dataset.run); }));
  el.querySelectorAll('[data-remove]').forEach((b) =>
    b.addEventListener('click', () => removeServerRow(el, b.dataset.remove)));

  el.querySelector('[data-act="discover-inline"]')?.addEventListener('click', () => discoverInline(el));
  el.querySelector('[data-act="save-source"]')?.addEventListener('click', () => saveSource(el));

  el.querySelectorAll('[data-cand-row]').forEach((cb) => cb.addEventListener('change', () => {
    const i = Number(cb.dataset.candRow);
    if (cb.checked) view.cand.selected.add(i); else view.cand.selected.delete(i);
    render(el);
  }));
  el.querySelector('[data-act="select-all-new"]')?.addEventListener('click', () => {
    view.cand.rows.forEach((r, i) => {
      if (selectable(r) && !HIDDEN_VERDICTS.has(r.verdict && r.verdict.disposition)) view.cand.selected.add(i);
    });
    render(el);
  });
  el.querySelector('[data-act="select-none"]')?.addEventListener('click', () => {
    view.cand.selected.clear();
    render(el);
  });
  // The confirm bar edits whichever state the visible tab owns.
  const target = () => (view.tab === 'file' ? view.file : view.cand);
  el.querySelector('[data-act="group"]')?.addEventListener('change', (e) => { target().group = e.target.value; });
  el.querySelector('[data-act="investigation"]')?.addEventListener('change', (e) => {
    target().investigation = e.target.value;
    // The file's already-in-this-investigation facts belong to the investigation
    // it was planned against: ask the server again for the one now chosen.
    if (view.tab === 'file' && view.file.text && view.file.preview && !view.file.preview.refused) runPreview(el);
    else render(el);
  });
  el.querySelector('[data-act="confirm"]')?.addEventListener('click', () =>
    (view.tab === 'file' ? confirmFile(el) : confirmAdd(el)));
  el.querySelector('[data-act="export-candidates"]')?.addEventListener('click', () => exportCandidates(el));

  // From a file
  el.querySelector('[data-file-input]')?.addEventListener('change', (e) => {
    const file = e.target.files && e.target.files[0];
    if (file) loadFile(el, file);
  });
  el.querySelectorAll('[data-count]').forEach((b) => b.addEventListener('click', () => {
    view.file.open = view.file.open === b.dataset.count ? '' : b.dataset.count;
    render(el);
  }));
  el.querySelectorAll('[data-file-line]').forEach((cb) => cb.addEventListener('change', () => {
    const line = Number(cb.dataset.fileLine);
    if (cb.checked) view.file.deselected.delete(line); else view.file.deselected.add(line);
    render(el);
  }));
  el.querySelectorAll('[data-accept-change]').forEach((cb) => cb.addEventListener('change', () => {
    if (cb.checked) view.file.accept.add(cb.dataset.acceptChange); else view.file.accept.delete(cb.dataset.acceptChange);
    render(el);
  }));
  el.querySelectorAll('[data-choose-server]').forEach((sel) => sel.addEventListener('change', () => {
    const line = Number(sel.dataset.chooseServer);
    if (sel.value) view.file.serverChoices[line] = sel.value; else delete view.file.serverChoices[line];
    runPreview(el);        // the server re-plans: the row becomes importable (or not)
  }));
}
