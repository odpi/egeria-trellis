/* Database server discovery — the real port of classic's (index.html)
 * server-side introspection flow for databases, replacing the old-UI-link
 * stub `app.js`'s `find-repos` action used to show for
 * `state.resourceType === 'db'` (see that file's `FIND_TITLE` comment).
 *
 * Classic's mechanism, ported verbatim rather than reinvented: a Postgres
 * server is registered ONCE with stored credentials
 * (`POST /api/db-servers/register`); "Discover" connects to it right now
 * and lists what is actually there (`POST /api/db-servers/{slug}/discover`,
 * `DiscoveredDatabase` rows — each flags `is_registered` so an
 * already-added database shows greyed-out and pre-checked rather than
 * silently duplicable); a person selects which candidates to add and
 * `POST /api/db-servers/{slug}/add-database` registers each one as a real
 * `DatabaseEntity` — the same shape `listDatabases()` returns and the
 * sidebar already renders. See `web/routes/db_servers.py` for the exact
 * request/response shapes this file assumes.
 *
 * Chrome-level module, same placement rule as `discovery-import.js`
 * (SPEC-ACTIONABLE-AND-HONEST.md point 2): reached from the sidebar's
 * `find-repos` action, not a per-stage sub-tab, and not folded into
 * `stages/discovery.js`.
 *
 * Three views share one dialog, switched by `view.mode`:
 *   'servers'  — the registered-server list (or an honest empty state with
 *                a way to register one), each row offering Test/Discover/
 *                Remove — classic's `renderDatabaseList`'s servers section.
 *   'register' — the registration form — classic's `#register-server-modal`.
 *   'discover' — the discover-and-add flow for one server — classic's
 *                `#discover-db-modal` / `_fetchDiscoverDatabases` /
 *                `addSelectedDatabases`.
 */
import { openDialog } from '/static/next/worklist.js';
import {
  listDbServers, registerDbServer, deleteDbServer, testDbServer, testDbServerInline,
  discoverDatabases, addDiscoveredDatabase, listGroups,
} from '/static/re-api.js';
import { esc, icon, refreshGroupsAndSidebar } from '/static/next/app.js';

const emptyRegisterForm = () => ({
  slug: '', display_name: '', db_type: 'postgresql', host: '', port: 5432,
  description: '', group_slug: '', db_user: '', db_password: '',
  egeria_host: '', egeria_url: '', egeria_server: '', egeria_user: '', egeria_password: '',
});

const view = {
  mode: 'servers',       // 'servers' | 'register' | 'discover'
  servers: [],
  groups: [],
  busy: false,
  status: '',
  statusIsError: false,

  // 'register' mode
  form: emptyRegisterForm(),
  showEgeria: false,
  registerError: '',
  testResult: null,      // { ok, message } | null

  // 'discover' mode
  discoverSlug: null,
  discoverResults: [],   // DiscoveredDatabase[]
  discoverSelected: new Set(),
  discoverError: '',
  discoverLoading: false,
};

/** Opens the dialog and kicks off the first render. The only export. */
export async function openFindDbServersDialog() {
  const el = openDialog('Discover databases on a registered server',
    'Register a Postgres server once, then discover and add the databases on it', { wide: true });
  view.mode = 'servers';
  view.status = '';
  view.statusIsError = false;
  await loadServers(el);
}

async function loadServers(el) {
  view.busy = true;
  render(el);
  try {
    const [servers, groups] = await Promise.all([
      listDbServers(),
      listGroups().catch(() => []),
    ]);
    view.servers = servers || [];
    view.groups = groups || [];
  } catch (err) {
    view.status = `Could not load registered servers: ${err.message}`;
    view.statusIsError = true;
    view.servers = [];
  } finally {
    view.busy = false;
    render(el);
  }
}

function render(el) {
  const body = el.querySelector('#wl-detail-body');
  body.innerHTML = `
    ${statusLineHtml(view.status, view.statusIsError)}
    ${view.mode === 'register' ? registerFormHtml()
      : view.mode === 'discover' ? discoverHtml()
      : serversHtml()}
  `;
  bind(el);
}

function statusLineHtml(text, isError) {
  if (!text) return '';
  return `<p class="mb-s2 text-caveat ${isError ? 'text-state-warn' : 'text-ink-muted'}">${esc(text)}</p>`;
}

/* ── Servers list ───────────────────────────────────────────────────────── */

function serversHtml() {
  if (view.busy) return `<p class="text-caveat text-ink-muted">Loading…</p>`;

  const header = `
    <div class="mb-s3 flex items-center justify-between">
      <span class="text-caveat text-ink-muted">${view.servers.length} server(s) registered</span>
      <button data-act="new-server" class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink"
        >+ Register a server</button>
    </div>`;

  if (!view.servers.length) {
    return `${header}
      <p class="max-w-[60ch] text-answer text-ink">No database servers are registered yet.</p>
      <p class="max-w-[60ch] text-caveat text-ink-muted">
        Register a Postgres server's connection once, then Discover lists the databases actually on
        it — nothing is added until you select which ones to bring in.
      </p>`;
  }

  const rows = view.servers.map((s) => `
    <div class="mb-s2 rounded-sm border border-rule p-s2">
      <div class="flex items-start justify-between gap-s2">
        <div class="min-w-0">
          <div class="truncate font-heading text-caveat text-ink">${esc(s.display_name)}</div>
          <div class="font-mono text-provenance text-ink-muted">${esc(s.db_type)} · ${esc(s.host)}:${s.port}${
            s.group_slug ? ` · ${esc(s.group_slug)}` : ''}</div>
          ${!s.db_user ? `<div class="mt-[2px] text-provenance text-state-warn">⚠ no credentials stored</div>` : ''}
        </div>
        <div class="flex shrink-0 items-center gap-s2 text-caveat">
          <button data-test="${esc(s.slug)}" class="cursor-pointer bg-transparent text-accent-ink underline"
            >Test</button>
          <button data-discover="${esc(s.slug)}" class="cursor-pointer bg-transparent text-accent-ink underline"
            >Discover</button>
          <button data-remove="${esc(s.slug)}" class="cursor-pointer bg-transparent text-state-warn underline"
            >Remove</button>
        </div>
      </div>
      <div class="mt-s2 text-provenance text-ink-muted">
        ${(s.databases || []).length
          ? `${s.databases.length} database(s): ${s.databases.map((d) => esc(d.display_name)).join(', ')}`
          : 'No databases registered from this server yet — click Discover.'}
      </div>
      <div data-test-result="${esc(s.slug)}"></div>
    </div>`).join('');

  return `${header}${rows}`;
}

async function runServerTest(el, slug) {
  const resultEl = el.querySelector(`[data-test-result="${cssEsc(slug)}"]`);
  if (resultEl) resultEl.innerHTML = `<div class="mt-s1 text-provenance text-ink-muted">Testing…</div>`;
  try {
    const data = await testDbServer(slug);
    if (resultEl) {
      resultEl.innerHTML = data.status === 'ok'
        ? `<div class="mt-s1 text-provenance text-state-ok">✓ Connected to ${esc(data.host)}:${data.port} — ${data.database_count} database(s) visible.</div>`
        : `<div class="mt-s1 text-provenance text-state-warn">✗ ${esc(data.error || 'Connection failed')}</div>`;
    }
  } catch (err) {
    if (resultEl) resultEl.innerHTML = `<div class="mt-s1 text-provenance text-state-warn">✗ ${esc(err.message)}</div>`;
  }
}

async function removeServerRow(el, slug) {
  if (!window.confirm(`Remove server "${slug}" and its linked database registrations?\nThis does not touch the actual database server.`)) return;
  try {
    await deleteDbServer(slug);
    view.status = `Removed server "${slug}".`;
    view.statusIsError = false;
    await loadServers(el);
    refreshGroupsAndSidebar();
  } catch (err) {
    view.status = `Could not remove "${slug}": ${err.message}`;
    view.statusIsError = true;
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
        <div class="grid grid-cols-3 gap-s2">
          ${field('View Server', input('egeria_server', 'view-server'))}
          ${field('Egeria User', input('egeria_user', 'erinoverview'))}
          ${field('Egeria Password', input('egeria_password', '••••••••', 'password'))}
        </div>
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
    view.mode = 'servers';
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

/* ── Discover & add databases ──────────────────────────────────────────── */

function discoverHtml() {
  const srv = view.servers.find((s) => s.slug === view.discoverSlug);
  const header = `
    <div class="mb-s2 flex items-center gap-s2">
      <button data-act="back-to-servers" class="cursor-pointer bg-transparent text-caveat text-accent-ink underline">← Servers</button>
      <span class="font-heading text-caveat text-ink">Discover Databases — ${esc(srv ? srv.display_name : view.discoverSlug)}</span>
    </div>`;

  if (view.discoverLoading) {
    return `${header}<p class="text-caveat text-ink-muted">Connecting to server…</p>`;
  }
  if (view.discoverError) {
    return `${header}<p class="text-caveat text-state-warn">${esc(view.discoverError)}</p>`;
  }
  if (!view.discoverResults.length) {
    return `${header}<p class="text-caveat text-ink-muted">No accessible databases found.</p>`;
  }

  const rows = view.discoverResults.map((db, i) => {
    const disabled = db.is_registered;
    return `<label class="mb-s1 flex cursor-pointer items-start gap-s2 rounded-sm border border-rule p-s2 ${disabled ? 'opacity-50' : 'hover:border-rule-strong'}">
      <input type="checkbox" data-discover-row="${i}" ${disabled ? 'disabled checked' : ''} ${view.discoverSelected.has(i) ? 'checked' : ''}>
      <div class="min-w-0 flex-1">
        <div class="font-mono text-caveat text-ink">${esc(db.name)}${disabled ? ' <span class="text-ink-muted">(already registered)</span>' : ''}</div>
        <div class="text-provenance text-ink-muted">${db.size_pretty ? esc(db.size_pretty) + ' · ' : ''}${esc(db.owner)}${db.description ? ' · ' + esc(db.description) : ''}</div>
      </div>
    </label>`;
  }).join('');

  return `${header}
    <div class="max-h-[46vh] overflow-y-auto">${rows}</div>
    <div class="mt-s3 flex items-center gap-s2 border-t border-rule pt-s2">
      <button data-act="add-selected" class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink" ${view.busy ? 'disabled' : ''}
        >${view.busy ? 'Adding…' : 'Add Selected →'}</button>
    </div>`;
}

async function openDiscover(el, slug) {
  view.mode = 'discover';
  view.discoverSlug = slug;
  view.discoverResults = [];
  view.discoverSelected = new Set();
  view.discoverError = '';
  view.discoverLoading = true;
  render(el);
  try {
    view.discoverResults = await discoverDatabases(slug);
  } catch (err) {
    view.discoverError = `Could not connect to server: ${err.message}`;
  } finally {
    view.discoverLoading = false;
    render(el);
  }
}

async function addSelected(el) {
  const slug = view.discoverSlug;
  if (!slug) return;
  const chosen = [...view.discoverSelected]
    .map((i) => view.discoverResults[i])
    .filter((db) => db && !db.is_registered);
  if (!chosen.length) {
    view.status = 'Select at least one database.';
    view.statusIsError = true;
    render(el);
    return;
  }
  view.busy = true;
  render(el);
  let added = 0;
  const failures = [];
  for (const db of chosen) {
    try {
      await addDiscoveredDatabase(slug, db.name);
      added++;
    } catch (err) {
      failures.push(`${db.name}: ${err.message}`);
    }
  }
  view.busy = false;
  view.mode = 'servers';
  view.status = added
    ? `Added ${added} database(s) from server "${slug}".${failures.length ? ` (${failures.length} failed: ${failures.join('; ')})` : ''}`
    : `No databases were added.${failures.length ? ` ${failures.join('; ')}` : ''}`;
  view.statusIsError = !added;
  await loadServers(el);
  // Bring the newly-added databases into the sidebar without a full reload
  // -- mirrors discovery-import.js's post-import refresh. `state.databases`
  // is already loaded by the time this dialog is reachable (the "+" action
  // only shows for state.resourceType === 'db', which loads it on switch),
  // so this actually re-fetches rather than being a no-op.
  refreshGroupsAndSidebar();
}

/* ── Wiring ─────────────────────────────────────────────────────────────── */

function cssEsc(s) {
  return String(s).replace(/["\\]/g, '\\$&');
}

function bind(el) {
  el.querySelector('[data-act="new-server"]')?.addEventListener('click', () => {
    view.mode = 'register';
    view.form = emptyRegisterForm();
    view.showEgeria = false;
    view.registerError = '';
    view.testResult = null;
    render(el);
  });
  el.querySelector('[data-act="back-to-servers"]')?.addEventListener('click', () => {
    view.mode = 'servers';
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

  el.querySelectorAll('[data-test]').forEach((b) =>
    b.addEventListener('click', () => runServerTest(el, b.dataset.test)));
  el.querySelectorAll('[data-discover]').forEach((b) =>
    b.addEventListener('click', () => openDiscover(el, b.dataset.discover)));
  el.querySelectorAll('[data-remove]').forEach((b) =>
    b.addEventListener('click', () => removeServerRow(el, b.dataset.remove)));

  el.querySelectorAll('[data-discover-row]').forEach((cb) => cb.addEventListener('change', () => {
    const i = Number(cb.dataset.discoverRow);
    if (cb.checked) view.discoverSelected.add(i); else view.discoverSelected.delete(i);
  }));
  el.querySelector('[data-act="add-selected"]')?.addEventListener('click', () => addSelected(el));
}
