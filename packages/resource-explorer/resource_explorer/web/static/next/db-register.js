/* Register one database — PI-015 (parity slice G2).
 *
 * Reached from Find databases → Saved sources → "Register one database…".
 * Preview-then-apply is the only path that creates a resource, so direct
 * registration is TEST-THEN-REGISTER:
 *
 *   Test connection   runs first; the probe's own sentence is shown, nothing is
 *                     stored (POST /api/databases/_test-connection).
 *   Register          is available only after a pass for the values ON SCREEN:
 *                     changing the host, port, database, user or password puts
 *                     the test back to "not tested". It writes through the
 *                     existing register route (POST /api/databases/register).
 *   the row after     "saved · you · just now", built from the RE-READ
 *                     registration row (GET /api/databases/{slug}/registration),
 *                     never from the click.
 *
 * The password is held in this module's memory only: it is never rendered into
 * an attribute or text, never in a URL, and is dropped once the save returns.
 * A pressed control looks pending and ignores a second press; state is a cue
 * plus a short word, the sentence beside it.
 */
import { esc, state } from '/static/next/app.js';
import {
  testDatabaseConnection, registerDatabase, getDatabaseRegistration,
} from '/static/re-api.js';
import { ago } from '/static/next/format.js';

const blankForm = () => ({
  slug: '', display_name: '', db_type: 'postgresql', host: '', port: 5432, database_name: '',
  group_slug: '', description: '', db_user: '', db_password: '',
  egeria_host: '', egeria_url: '', egeria_server: '',
});

const fresh = () => ({
  f: blankForm(),
  showEgeria: false,
  test: { phase: 'idle', sentence: '', passedFor: '' },   // phase: idle | testing | ok | error
  reg: { phase: 'idle', error: '', saved: null },          // phase: idle | saving | saved | error
});

let st = fresh();

/** What a passing test is valid for. Held in memory; never rendered. */
const fingerprint = (f) => [f.db_type, f.host, f.port, f.database_name, f.db_user, f.db_password].join('\u0001');

const whoAmI = () =>
  (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';

const requiredFilled = (f) => !!(f.slug && f.display_name && f.host && f.database_name && f.db_user && f.db_password);

/** Test state as a short word and a cue; the sentence rides beside it. */
function testStateWord() {
  const { phase, passedFor } = st.test;
  if (phase === 'testing') return { glyph: '…', word: 'testing' };
  if (phase === 'ok' && passedFor === fingerprint(st.f)) return { glyph: '✓', word: 'passed' };
  if (phase === 'ok') return { glyph: '○', word: 'changed since the test · test again' };
  if (phase === 'error') return { glyph: '✕', word: 'failed' };
  return { glyph: '○', word: 'not tested' };
}

const canRegister = () =>
  st.test.phase === 'ok' && st.test.passedFor === fingerprint(st.f)
  && requiredFilled(st.f) && st.reg.phase !== 'saving';

/** The "Register one database…" panel's markup. */
export function registerOneDatabaseHtml(groups = []) {
  const f = st.f;
  if (st.reg.phase === 'saved' && st.reg.saved) {
    return `<div class="mb-s2 flex items-center gap-s2">
        <button data-act="rdb-back" class="cursor-pointer bg-transparent text-caveat text-accent-ink underline">← Servers</button>
        <span class="font-heading text-caveat text-ink">Register one database</span>
      </div>
      <div data-rdb-saved class="text-answer text-ink">${esc(st.reg.saved)}</div>
      <p class="mt-s1 text-caveat text-ink-muted">It is in the database list now. Nothing was sent to Egeria.</p>
      <button data-act="rdb-another" class="mt-s2 cursor-pointer rounded-sm border border-rule-strong bg-transparent px-s3 py-[3px] text-caveat text-accent-ink"
        >Register another</button>`;
  }
  const field = (label, inner) => `<div>
    <label class="mb-[2px] block text-caps uppercase tracking-caps text-ink-muted">${esc(label)}</label>${inner}</div>`;
  const input = (key, placeholder = '', type = 'text') =>
    `<input data-rdb="${key}" type="${type}" placeholder="${esc(placeholder)}" autocomplete="off"
       ${type === 'password' ? '' : `value="${esc(f[key] ?? '')}"`}
       class="w-full rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">`;
  const groupOptions = groups.map((g) =>
    `<option value="${esc(g.slug)}" ${f.group_slug === g.slug ? 'selected' : ''}>${esc(g.display_name)}</option>`).join('');
  const tw = testStateWord();
  const busy = st.test.phase === 'testing' || st.reg.phase === 'saving';
  return `
    <div class="mb-s2 flex items-center gap-s2">
      <button data-act="rdb-back" class="cursor-pointer bg-transparent text-caveat text-accent-ink underline">← Servers</button>
      <span class="font-heading text-caveat text-ink">Register one database</span>
    </div>
    <p class="mb-s2 max-w-[70ch] text-caveat text-ink-muted">
      Test the connection first; Register is available once the test has passed for what is typed here.
      Nothing is sent to Egeria.
    </p>
    <div class="grid grid-cols-2 gap-s2">
      ${field('Slug *', input('slug', 'my-database'))}
      ${field('Display name *', input('display_name', 'My database'))}
      ${field('Host *', input('host', 'localhost'))}
      ${field('Port', input('port', '5432', 'number'))}
      ${field('Database *', input('database_name', 'appdb'))}
      ${field('Group', `<select data-rdb="group_slug" class="w-full rounded-sm border border-rule bg-transparent px-2 py-[3px] text-caveat text-ink">
        <option value="">No group</option>${groupOptions}</select>`)}
    </div>
    <div class="mt-s3 border-t border-rule pt-s2">
      <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">Credential <span class="normal-case text-ink-muted">(stored with the database)</span></div>
      <div class="grid grid-cols-2 gap-s2">
        ${field('Username *', input('db_user', 'scout_ro'))}
        ${field('Password *', input('db_password', '', 'password'))}
      </div>
    </div>
    <div class="mt-s3 border-t border-rule pt-s2">
      <button type="button" data-act="rdb-toggle-egeria" class="cursor-pointer bg-transparent text-caveat text-ink-muted hover:text-ink">
        ${st.showEgeria ? '▼' : '▶'} Egeria connection <span class="text-ink-muted">(optional)</span>
      </button>
      ${st.showEgeria ? `<div class="mt-s2 space-y-s2">
        ${field('Egeria-visible host', input('egeria_host', 'host.docker.internal'))}
        ${field('Egeria platform URL', input('egeria_url', 'https://localhost:9443'))}
        ${field('View server', input('egeria_server', 'view-server'))}
      </div>` : ''}
    </div>
    <div class="mt-s3 flex items-center gap-s2">
      <button data-act="rdb-test" ${busy ? 'disabled' : ''}
        class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-s3 py-[3px] text-caveat text-accent-ink"
        >${st.test.phase === 'testing' ? 'Testing…' : 'Test connection'}</button>
      <button data-act="rdb-register" ${canRegister() ? '' : 'disabled'}
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-s3 py-[3px] text-caveat text-accent-ink"
        >${st.reg.phase === 'saving' ? 'Registering…' : 'Register'}</button>
      <span data-rdb-test-state class="text-caveat text-ink"><span aria-hidden="true">${tw.glyph}</span> ${esc(tw.word)}</span>
    </div>
    ${st.test.sentence ? `<p data-rdb-sentence class="mt-s1 text-caveat text-ink-muted">${esc(st.test.sentence)}</p>` : ''}
    ${st.reg.error ? `<p data-rdb-error class="mt-s1 text-caveat text-state-warn">${esc(st.reg.error)}</p>` : ''}`;
}

/** Read the inputs into memory. Passwords are never trimmed and never go back
 *  into an attribute. */
function capture(host) {
  host.querySelectorAll('[data-rdb]').forEach((inp) => {
    const key = inp.dataset.rdb;
    if (key === 'port') st.f.port = parseInt(inp.value, 10) || 0;
    else if (key === 'db_password') st.f[key] = inp.value;
    else st.f[key] = inp.value.trim();
  });
}

/** localhost databases are reached from Egeria's container as host.docker.internal,
 *  the same default the server form applies (db-server-discovery.js). Only when
 *  the field was left empty. */
function egeriaHostDefault(f) {
  if (!f.egeria_host && (f.host === 'localhost' || f.host === '127.0.0.1')) return 'host.docker.internal';
  return f.egeria_host;
}

/** Wire the panel. `ctx.rerender()` redraws the dialog; `ctx.onRegistered()`
 *  refreshes the lists after a save; `ctx.back()` returns to the server list. */
export function bindRegisterOneDatabase(host, ctx) {
  const pw = host.querySelector('[data-rdb="db_password"]');
  if (pw) pw.value = st.f.db_password;

  const refreshCues = () => {
    const tw = testStateWord();
    const cue = host.querySelector('[data-rdb-test-state]');
    if (cue) cue.innerHTML = `<span aria-hidden="true">${tw.glyph}</span> ${esc(tw.word)}`;
    const reg = host.querySelector('[data-act="rdb-register"]');
    if (reg) reg.disabled = !canRegister();
  };
  host.querySelectorAll('[data-rdb]').forEach((inp) => {
    inp.addEventListener('input', () => { capture(host); refreshCues(); });
    inp.addEventListener('change', () => { capture(host); refreshCues(); });
  });
  host.querySelector('[data-act="rdb-back"]')?.addEventListener('click', () => { ctx.back(); });
  host.querySelector('[data-act="rdb-another"]')?.addEventListener('click', () => { st = fresh(); ctx.rerender(); });
  host.querySelector('[data-act="rdb-toggle-egeria"]')?.addEventListener('click', () => {
    capture(host);
    st.showEgeria = !st.showEgeria;
    ctx.rerender();
  });

  host.querySelector('[data-act="rdb-test"]')?.addEventListener('click', async () => {
    if (st.test.phase === 'testing' || st.reg.phase === 'saving') return;     // ignores a second press
    capture(host);
    const f = st.f;
    st.reg.error = '';
    if (!f.host || !f.database_name || !f.db_user || !f.db_password) {
      st.test = { phase: 'error', sentence: 'Enter host, database, username and password first.', passedFor: '' };
      ctx.rerender();
      return;
    }
    const sent = fingerprint(f);
    st.test = { phase: 'testing', sentence: '', passedFor: '' };
    ctx.rerender();
    try {
      const out = await testDatabaseConnection({
        host: f.host, port: f.port || 5432, database_name: f.database_name,
        db_type: f.db_type, db_user: f.db_user, db_password: f.db_password,
      });
      st.test = out.status === 'ok'
        ? { phase: 'ok', sentence: out.sentence || '', passedFor: sent }
        : { phase: 'error', sentence: out.sentence || 'The connection test failed.', passedFor: '' };
    } catch (err) {
      st.test = {
        phase: 'error', passedFor: '',
        sentence: err && err.status === 401 ? 'Sign in to test a connection.' : `The test could not run: ${err && err.message ? err.message : err}`,
      };
    }
    ctx.rerender();
  });

  host.querySelector('[data-act="rdb-register"]')?.addEventListener('click', async () => {
    if (st.reg.phase === 'saving' || !canRegister()) return;                   // ignores a second press
    capture(host);
    if (!canRegister()) { ctx.rerender(); return; }
    const f = st.f;
    st.reg = { phase: 'saving', error: '', saved: null };
    ctx.rerender();
    try {
      await registerDatabase({
        slug: f.slug, display_name: f.display_name, db_type: f.db_type, host: f.host, port: f.port || 5432,
        database_name: f.database_name, description: f.description, group_slug: f.group_slug,
        db_user: f.db_user, db_password: f.db_password,
        egeria_host: egeriaHostDefault(f), egeria_url: f.egeria_url, egeria_server: f.egeria_server,
      });
      // The password's job is done: drop every in-memory copy before anything else.
      st.f.db_password = '';
      st.test = { phase: 'idle', sentence: '', passedFor: '' };
      // The row says what the RE-READ registration says, not what was clicked.
      let line;
      try {
        const reg = await getDatabaseRegistration(f.slug);
        const by = reg.registered_by;
        const who = by ? (by === whoAmI() ? 'you' : by) : "who isn't recorded";
        line = `saved · ${who} · ${ago(reg.registered_at) || 'time not recorded'}`;
      } catch (_) {
        line = 'the save returned, but the registration could not be re-read';
      }
      st.reg = { phase: 'saved', error: '', saved: line };
      ctx.onRegistered(f.slug);
    } catch (err) {
      st.reg = {
        phase: 'error', saved: null,
        error: err && err.status === 401 ? 'not saved · sign in to register a database'
          : `not saved · ${err && err.message ? err.message : err}`,
      };
    }
    ctx.rerender();
  });
}

/** Opening the panel starts from a clean form (and no remembered password). */
export function resetRegisterOneDatabase() { st = fresh(); }

/** For the harness: whether any password is still held in memory. */
export const holdsPassword = () => !!st.f.db_password;
