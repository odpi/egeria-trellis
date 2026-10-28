/** Behaviour (parity P3: PI-055, PI-056, PI-116, PI-117, PI-118, PI-119): asking from a question row, the
 *  "why is this here" disclosure, "what would answer this" before asking, running a missing analysis, running
 *  or scheduling from a chat answer, and confirming a suggested alias.
 *
 *  Real app.js, real chat.js and the real router. ONLY `fetch` is replaced (a stateful stub in the shape of
 *  the server's routes), so every "the next read shows" assertion reads what the stub holds AFTER the press:
 *  a run flips the stub's answer, a schedule save is read back from the schedule list, an alias is read back
 *  from the alias list. Nothing here talks to Egeria or a database.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const SLUG = 'egeria_git';
const wait = (ms = 150) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const Q_RUN = {
  question: 'Is the project actively maintained?', stage: 'Scouting', kind: 'analysis',
  perspectives: ['Maintenance'], purposes: ['Assess'], analysis_ids: ['activity_metrics'], answering_mechanism: 'Analysis',
  derivation: { matched_perspectives: [], matched_purposes: [], purpose_ranked: false, analysis_ids: ['activity_metrics'], checks: ['recent commits'] },
};
const Q_NONE = {
  question: 'Do we have the skills to run it?', stage: 'Scouting', kind: 'human',
  perspectives: [], analysis_ids: [], answering_mechanism: 'Human-Supplied',
  derivation: { matched_perspectives: [], matched_purposes: [], purpose_ranked: false, analysis_ids: [], checks: [] },
};
const Q_TWO = {
  question: 'Is it safe and maintained?', stage: 'Scouting', kind: 'analysis', perspectives: [], purposes: [],
  analysis_ids: ['activity_metrics', 'cve_scan'], answering_mechanism: 'Analysis',
  derivation: { matched_perspectives: [], matched_purposes: [], purpose_ranked: false, analysis_ids: ['activity_metrics', 'cve_scan'], checks: [] },
};
const HEADLINE = 'Active: 40 commits in the last 90 days.';
const unrunEnv = () => ({ answerable: false, blocked_reason: 'Not run yet.', facts: [
  { is_known: false, analysis_id: 'activity_metrics', state: 'never_run', can_run: ['activity_metrics'] }] });
const answeredEnv = () => ({ answerable: true, facts: [
  { is_known: true, analysis_id: 'activity_metrics', state: 'measured', headline: HEADLINE, last_run_at: '2026-10-09T10:00:00' }] });

/** A stateful stand-in for the server routes this file touches. */
function makeServer(over = {}) {
  const s = {
    calls: [], questions: [Q_RUN, Q_NONE], ran: new Set(), schedules: [], aliases: [],
    gaps: ['cve_scan', 'activity_metrics'], runStatus: 'success', runError: 'rate limit reached',
    scheduleFails: false, aliasLists: true, noGaps: false, deferred: [], stateFactOnly: false, ...over,
  };
  const enc = new TextEncoder();
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url); const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b) => ({ ok: true, status: 200, json: async () => b, text: async () => JSON.stringify(b) });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    if (u.includes('/scouting-questions')) return ok({ questions: s.questions });
    if (u.includes(`/api/analyses/facts/${SLUG}/answer`) && u.includes('safe') && s.stateFactOnly) return ok({ answerable: true, facts: [
      { is_known: true, analysis_id: 'activity_metrics', state: 'measured', headline: HEADLINE, last_run_at: '' }],
      analyses_behind: [{ analysis_id: 'cve_scan', state: 'measured', is_known: true, last_run_at: '2026-10-08T10:00:00' }] });
    if (u.includes(`/api/analyses/facts/${SLUG}/answer`) && u.includes('safe')) return ok({ answerable: false, blocked_reason: 'Part of this has not run.', facts: [
      { is_known: true, analysis_id: 'activity_metrics', state: 'measured', headline: HEADLINE, last_run_at: '2026-10-09T10:00:00' },
      { is_known: false, analysis_id: 'cve_scan', state: 'never_run', can_run: ['cve_scan'] }] });
    if (u.includes(`/api/analyses/facts/${SLUG}/answer`)) return ok(s.ran.has('activity_metrics') ? answeredEnv() : unrunEnv());
    const run = u.match(/\/analyses\/([a-z_]+)\/run$/);
    if (run && method === 'POST') { s.ran.add(run[1]); s.lastRun = run[1]; return ok({ activity_id: `act-${run[1]}`, status: 'started' }); }
    if (u.startsWith('/api/activity/act-')) {
      return ok({ id: u.split('/').pop(), status: s.runStatus,
        summary: s.runStatus === 'error' ? s.runError : 'done', detail: s.runStatus === 'error' ? JSON.stringify({ error: s.runError }) : '{}' });
    }
    if (u === `/api/schedules/repo/${SLUG}` && method === 'GET') return ok(s.schedules);
    if (u === `/api/schedules/repo/${SLUG}` && method === 'POST') {
      if (s.scheduleFails) return err(500, 'schedule store is down');
      s.schedules = s.schedules.filter((x) => x.analysis_id !== body.analysis_id);
      s.schedules.push({ analysis_id: body.analysis_id, schedule: body.schedule, enabled: body.enabled });
      return ok({ status: 'ok' });
    }
    if (u === '/api/context/compile') {
      const open = s.gaps.filter((g) => !s.ran.has(g));
      const manifest = { packed: [{ key: 'foss_scorecard', rung: 'FULL', size: 120 }], compile_id: 'c1' };
      if (!s.noGaps) manifest.gaps = open.map((key) => ({ key, reason: 'no stored result' }));
      if (s.deferred.length) manifest.deferred = s.deferred.map((key) => ({ key, reason: 'below the section cap of 12' }));
      return ok({ text: 'compiled text', manifest, derivation: [], compile_id: 'c1' });
    }
    if (u === '/api/aliases/' && method === 'POST') {
      const norm = String(body.alias).toLowerCase().replace(/[ -]/g, '_');
      const held = (s.otherAliases || {})[norm];
      if (held && !body.move) return err(409, `alias already used for ${held}`);
      s.aliases.push({ alias: String(body.alias).toLowerCase().replace(/[ -]/g, '_'), project_slug: body.project_slug });
      return ok({ saved: true });
    }
    if (u.startsWith('/api/aliases/') && method === 'GET') {
      return ok({ project_slug: u.split('/').pop(), aliases: s.aliasLists ? s.aliases : [] });
    }
    if (u === '/api/query/stream') {
      const done = body.project_slug
        ? { t: 'done', intent: 'health', hash: 'h2', cached: false,
            compiled: { text: 't', manifest: { packed: [], gaps: s.gaps.filter((g) => !s.ran.has(g)).map((key) => ({ key, reason: 'no stored result' })) } } }
        : { t: 'done', intent: 'lookup', hash: 'h1', cached: false,
            alias_suggestion: { term: 'Foo Bar', candidate_slug: 'foo_bar', candidate_name: 'Foo Bar Repo' } };
      const frames = [{ t: 'chunk', v: 'No such repo.' }, done];
      let sent = false;
      return { ok: true, status: 200, body: { getReader: () => ({ async read() {
        if (sent) return { done: true };
        sent = true;
        return { done: false, value: enc.encode(frames.map((f) => `data: ${JSON.stringify(f)}\n\n`).join('')) };
      } }) } };
    }
    return ok({});
  };
  return s;
}

async function setUp(over = {}, { slug = SLUG } = {}) {
  const { document, window } = makeDomEnvironment();
  window.localStorage.clear();
  const server = makeServer(over);
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  const chat = await import('/static/next/chat.js');
  const s = app.state;
  Object.assign(s, { resourceType: 'repo', selectedSlug: slug, stage: 'scouting', subTab: 'questions', chat: [], promoted: null,
    projects: [{ slug: SLUG, display_name: SLUG }], groups: [], investigations: [], investigation: '', workListSlug: null,
    workListIndex: false, perspectives: [], allPerspectives: [], workingSet: new Set(), me: { user_id: 'me' },
    activePerspectives: new Set(), whyOpen: new Set() });
  for (const id of ['content', 'intent-nav', 'perspective-row', 'app-grid', 'question-rows', 'rail']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  chat.renderRail();
  app.renderIntentNav();
  document.querySelector('#intent-nav button[data-stage="scouting"]').click();
  await wait(400);
  return { document, window, app, chat, server };
}

const row = (document, n = 0) => document.getElementById(`qrow-${n}`);
const posts = (server, rx) => server.calls.filter((c) => c.method === 'POST' && rx.test(c.url));

/* ── PI-055 ─────────────────────────────────────────────────────────────── */

test('PI-055: "ask in chat" on a question row answers it in chat from the row\'s own answer, without a model', async () => {
  const { document, app, server } = await setUp({ ran: new Set(['activity_metrics']) });
  const btn = row(document).querySelector('button[data-ask-chat]');
  assert.ok(btn, 'the row offers it as a visible control');
  assert.match(text(btn), /ask in chat/);
  btn.click();
  await wait();
  const turn = app.state.chat.at(-1);
  assert.equal(turn.question, Q_RUN.question);
  assert.equal(turn.slug, SLUG);
  assert.match(turn.answer, /Active: 40 commits in the last 90 days\./, 'the row\'s own answer text');
  assert.match(text(document.getElementById('promoted-body')), /Active: 40 commits/);
  assert.match(text(document.getElementById('chat-log')), /Is the project actively maintained\?/, 'the turn is in the rail transcript');
  assert.deepEqual(posts(server, /\/api\/query/), [], 'nothing was retyped at a model');
});

test('PI-055: pressing the question text itself does the same', async () => {
  const { document, app } = await setUp({ ran: new Set(['activity_metrics']) });
  row(document).querySelector('span[data-ask-chat]').click();
  await wait();
  assert.equal(app.state.chat.at(-1).question, Q_RUN.question);
});

test('PI-055: asked before the row\'s answer has loaded, the chat reads it itself', async () => {
  const { document, app, server } = await setUp({ ran: new Set(['activity_metrics']) });
  app.state.answers.delete(Q_RUN.question);
  const before = server.calls.filter((c) => c.url.includes('/answer?')).length;
  row(document).querySelector('button[data-ask-chat]').click();
  await wait();
  assert.equal(server.calls.filter((c) => c.url.includes('/answer?')).length, before + 1);
  assert.match(app.state.chat.at(-1).answer, /Active: 40 commits/);
});

/* ── PI-056 ─────────────────────────────────────────────────────────────── */

test('PI-056: "why is this here" says stage, perspective, purpose and the analyses that answer it, from the catalog entry', async () => {
  const { document } = await setUp();
  const d = row(document).querySelector('details[data-why]');
  assert.ok(d, 'the disclosure is on the row');
  assert.equal(d.open, false, 'closed until asked for');
  const body = text(d.querySelector('[data-why-body]'));
  assert.match(body, /Stage: Scouting/);
  assert.match(body, /Perspective: no filter is on; it carries Maintenance/);
  assert.match(body, /Serves purpose: Assess/);
  assert.match(body, /Answered by: activity_metrics/);
  assert.match(body, /Checks: recent commits/);
});

test('PI-056: a question with no analysis says so as a catalog fact, and a missing field reads "not reported"', async () => {
  const { document } = await setUp({ questions: [{ ...Q_NONE, purposes: undefined }] });
  const body = text(row(document).querySelector('[data-why-body]'));
  assert.match(body, /No analysis answers this yet — Human-Supplied/);
  assert.match(body, /Serves purpose: not reported/);
  assert.match(body, /Perspective: no filter is on; it carries none/);
});

test('PI-056: with a perspective held, the line names the one that matched; an opened disclosure stays open across a redraw', async () => {
  const { document, app } = await setUp();
  app.state.activePerspectives = new Set(['Maintenance']);
  app.state.whyOpen.add(Q_RUN.question);
  const html = app.whyHereHtml(Q_RUN, 0);
  assert.match(html, /<details[^>]* open/);
  assert.match(html, /Shown because of perspective:<\/span> <span class="text-ink">Maintenance/);
  // a real redraw of the row (its answer arrives again) keeps the disclosure open
  const d = row(document).querySelector('details[data-why]');
  d.open = true; d.dispatchEvent(new document.defaultView.Event('toggle'));
  assert.ok(app.state.whyOpen.has(Q_RUN.question), 'opening is remembered');
  d.open = false; d.dispatchEvent(new document.defaultView.Event('toggle'));
  assert.ok(!app.state.whyOpen.has(Q_RUN.question), 'closing is remembered');
});

/* ── PI-118 ─────────────────────────────────────────────────────────────── */

test('PI-118: a chat answer to an unrun question offers Run; pressing it runs the analysis and the NEXT read replaces the answer', async () => {
  const { document, app, server } = await setUp();
  row(document).querySelector('button[data-ask-chat]').click();
  await wait();
  const box = document.querySelector('[data-runnable-box]');
  assert.ok(box, 'the analyses behind the answer are listed');
  const r = box.querySelector('[data-runnable="activity_metrics"]');
  assert.match(text(r.querySelector('[data-run-status]')), /not run/);
  assert.doesNotMatch(text(document.getElementById('promoted-body')), /Active: 40 commits/, 'before: no answer');
  r.querySelector('[data-run]').click();
  await wait(200);
  assert.equal(posts(server, /activity_metrics\/run$/).length, 1, 'one run was started');
  const after = document.querySelector('[data-runnable="activity_metrics"]');
  assert.match(text(after.querySelector('[data-run-status]')), /ran just now/);
  assert.match(text(document.getElementById('promoted-body')), /Active: 40 commits in the last 90 days\./, 'after: the answer read again');
  assert.match(app.state.chat.at(-1).answer, /Active: 40 commits/);
});

test('PI-118: a run that ends in error says why and does NOT change the answer', async () => {
  const { document, server } = await setUp({ runStatus: 'error' });
  row(document).querySelector('button[data-ask-chat]').click();
  await wait();
  document.querySelector('[data-runnable="activity_metrics"] [data-run]').click();
  await wait(200);
  const st = text(document.querySelector('[data-runnable="activity_metrics"] [data-run-status]'));
  assert.match(st, /failed/);
  assert.match(st, /rate limit reached/);
  assert.doesNotMatch(text(document.getElementById('promoted-body')), /Active: 40 commits/);
  assert.equal(server.calls.filter((c) => /\/answer\?/.test(c.url)).length >= 1, true);
});

test('PI-118: Schedule saves a cadence and the line is read back from the schedules the server holds', async () => {
  const { document, server } = await setUp();
  row(document).querySelector('button[data-ask-chat]').click();
  await wait();
  const r = () => document.querySelector('[data-runnable="activity_metrics"]');
  assert.match(text(r().querySelector('[data-sched-status]')), /not scheduled/, 'read: the server holds none');
  const sel = r().querySelector('[data-sched-cadence]');
  sel.value = 'weekly';
  r().querySelector('[data-sched-save]').click();
  await wait();
  const post = posts(server, /\/api\/schedules\/repo\//)[0];
  assert.deepEqual(post.body, { analysis_id: 'activity_metrics', schedule: 'weekly', enabled: true, target_kind: 'analysis' });
  assert.match(text(r().querySelector('[data-sched-status]')), /scheduled weekly/);
  assert.deepEqual(server.schedules.map((x) => x.schedule), ['weekly']);
});

test('PI-118: a schedule the server refuses is shown as not saved, never as scheduled', async () => {
  const { document } = await setUp({ scheduleFails: true });
  row(document).querySelector('button[data-ask-chat]').click();
  await wait();
  document.querySelector('[data-runnable="activity_metrics"] [data-sched-save]').click();
  await wait();
  const st = text(document.querySelector('[data-runnable="activity_metrics"] [data-sched-status]'));
  assert.match(st, /schedule not saved/);
  assert.match(st, /schedule store is down/);
  assert.doesNotMatch(st, /scheduled (daily|weekly|monthly)/);
});

/* ── PI-116 / PI-117 ────────────────────────────────────────────────────── */

test('PI-116: "what would answer this?" compiles the typed question WITHOUT asking it, and lists what is missing', async () => {
  const { document, app, server } = await setUp();
  document.getElementById('ask-input').value = 'How healthy is the project?';
  document.getElementById('ask-compile').click();
  await wait();
  const c = posts(server, /\/api\/context\/compile/);
  assert.equal(c.length, 1);
  assert.equal(c[0].body.question, 'How healthy is the project?');
  assert.equal(c[0].body.resource_slug, SLUG);
  assert.equal(c[0].body.entity_type, 'repo');
  assert.deepEqual(posts(server, /\/api\/query/), [], 'nothing was asked');
  assert.equal(app.state.chat.length, 0, 'no chat turn was added');
  const rail = text(document.getElementById('rail-evidence'));
  assert.match(rail, /Nothing has been asked/);
  assert.match(rail, /2 not run/);
  assert.match(rail, /cve_scan/);
  assert.match(rail, /1 stored result used: foss_scorecard/);
  assert.equal(document.getElementById('ask-input').value, 'How healthy is the project?', 'the question stays in the box');
});

test('PI-116: with nothing typed, or no gaps reported, it says so rather than claiming nothing is missing', async () => {
  const { document } = await setUp({ noGaps: true });
  document.getElementById('ask-compile').click();
  await wait(50);
  assert.match(text(document.querySelector('[data-compile-note]')), /Type the question first/);
  document.getElementById('ask-input').value = 'q';
  document.getElementById('ask-compile').click();
  await wait();
  const g = document.querySelector('[data-compile-gaps]');
  assert.match(text(g), /gaps not reported/);
  assert.doesNotMatch(text(g), /nothing missing/);
});

test('PI-116: analyses ranked below the section cap are said to be not read, never folded into "nothing missing"', async () => {
  const { document } = await setUp({ gaps: [], deferred: ['cve_scan', 'secret_scan'] });
  document.getElementById('ask-input').value = 'Does this repository have any known security vulnerabilities in its dependencies?';
  document.getElementById('ask-compile').click();
  await wait();
  const g = text(document.querySelector('[data-compile-gaps]'));
  assert.match(g, /2 not read/);
  assert.match(g, /cve_scan, secret_scan/);
  assert.doesNotMatch(g, /nothing missing\s*every analysis/);
});

test('PI-117: Run on a gap runs that analysis, compiles again, and the gap is gone only if the new compile says so', async () => {
  const { document, server } = await setUp();
  document.getElementById('ask-input').value = 'How healthy is the project?';
  document.getElementById('ask-compile').click();
  await wait();
  const gap = () => document.querySelector('[data-gap="cve_scan"]');
  assert.match(text(gap()), /not run/);
  gap().querySelector('[data-gap-run]').click();
  await wait(250);
  assert.equal(posts(server, /cve_scan\/run$/).length, 1);
  assert.equal(posts(server, /\/api\/context\/compile/).length, 2, 'the compile was read again after the run');
  assert.equal(gap(), null, 'the new compile no longer lists it');
  const rail = text(document.getElementById('rail-evidence'));
  assert.match(rail, /1 not run/);
  assert.match(rail, /cve_scan now has a stored result/);
  assert.match(rail, /Re-checked just now/);
  document.querySelector('[data-gap="activity_metrics"] [data-gap-run]').click();
  await wait(250);
  assert.match(text(document.querySelector('[data-compile-gaps]')), /nothing missing/);
});

test('PI-117: a gap whose run ends in error stays listed with the reason, and no compile is re-read', async () => {
  const { document, server } = await setUp({ runStatus: 'error' });
  document.getElementById('ask-input').value = 'q';
  document.getElementById('ask-compile').click();
  await wait();
  document.querySelector('[data-gap="cve_scan"] [data-gap-run]').click();
  await wait(250);
  const g = text(document.querySelector('[data-gap="cve_scan"]'));
  assert.match(g, /failed/);
  assert.match(g, /rate limit reached/);
  assert.equal(posts(server, /\/api\/context\/compile/).length, 1);
});

/* ── PI-119 ─────────────────────────────────────────────────────────────── */

async function askWithAlias(over = {}) {
  const t = await setUp(over, { slug: '' });
  t.document.getElementById('ask-input').value = 'foo bar status?';
  t.document.getElementById('ask-submit').click();
  await wait(300);
  return t;
}

test('PI-119: a suggested alias can be confirmed; it is saved and READ BACK from the alias list', async () => {
  const { document, server } = await askWithAlias();
  const box = document.querySelector('[data-alias-box]');
  assert.ok(box, 'the suggestion is on the answer');
  assert.match(text(box.querySelector('[data-alias-sentence]')), /No resource named “Foo Bar” was resolved: did you mean Foo Bar Repo \(foo_bar\)\?/);
  box.querySelector('[data-alias-yes]').click();
  await wait();
  const post = posts(server, /\/api\/aliases\//)[0];
  assert.deepEqual(post.body, { alias: 'Foo Bar', project_slug: 'foo_bar', move: false });
  assert.ok(server.calls.some((c) => c.method === 'GET' && c.url === '/api/aliases/foo_bar'), 'read back after the save');
  const after = text(document.querySelector('[data-alias-box]'));
  assert.match(after, /remembered/);
  assert.match(after, /“Foo Bar” will resolve to foo_bar/);
  assert.equal(document.querySelector('[data-alias-yes]'), null);
});

test('PI-119: a save the alias list does not confirm is reported as not saved', async () => {
  const { document } = await askWithAlias({ aliasLists: false });
  document.querySelector('[data-alias-yes]').click();
  await wait();
  const after = text(document.querySelector('[data-alias-box]'));
  assert.match(after, /not saved/);
  assert.doesNotMatch(after, /remembered/);
  assert.ok(document.querySelector('[data-alias-yes]'), 'it can be tried again');
});

test('PI-119: No saves nothing and says so', async () => {
  const { document, server } = await askWithAlias();
  document.querySelector('[data-alias-no]').click();
  await wait(50);
  assert.deepEqual(posts(server, /\/api\/aliases\//), []);
  assert.match(text(document.querySelector('[data-alias-box]')), /not remembered/);
});

/* ── PI-118, a free-text question ───────────────────────────────────────── */

test('PI-118: a free-text answer lists the analyses its compile found missing; running one compiles again and lists what is STILL missing', async () => {
  const { document, app, server } = await setUp({ gaps: ['cve_scan', 'activity_metrics'] });
  document.getElementById('ask-input').value = 'How healthy is the project?';
  document.getElementById('ask-submit').click();
  await wait(300);
  const ids = () => [...document.querySelectorAll('[data-runnable]')].map((r) => r.dataset.runnable);
  assert.deepEqual(ids(), ['cve_scan', 'activity_metrics'], 'the compile\'s own gap list');
  assert.match(text(document.querySelector('[data-runnable-box]')), /would answer this and have no stored result/);
  document.querySelector('[data-runnable="cve_scan"] [data-run]').click();
  await wait(250);
  assert.equal(posts(server, /cve_scan\/run$/).length, 1);
  assert.equal(posts(server, /\/api\/context\/compile/).length, 1, 'the compile was read again after the run');
  assert.match(text(document.querySelector('[data-runnable="cve_scan"] [data-run-status]')), /ran just now/);
  assert.match(text(document.querySelector('[data-runnable="activity_metrics"] [data-run-status]')), /not run/, 'the other is still missing');
  assert.ok(ids().includes('activity_metrics'), 'what is still missing stays listed');
  assert.match(text(document.querySelector('[data-recheck-note]')), /Ran cve_scan; it now has a stored result\. This answer was written before it ran, so ask again to use it\./);
  assert.equal(app.state.chat.length, 1, 'no second question was asked');
});

/* ── review round 2 ─────────────────────────────────────────────────────── */

test('review 1: "has run" is per analysis: one that has nothing recorded reads "not run" beside one that has run', async () => {
  const { document } = await setUp({ questions: [Q_TWO] });
  document.querySelector('#qrow-0 button[data-ask-chat]').click();
  await wait();
  assert.match(text(document.querySelector('[data-runnable="activity_metrics"] [data-run-status]')), /has run/);
  const other = text(document.querySelector('[data-runnable="cve_scan"] [data-run-status]'));
  assert.match(other, /not run/);
  assert.doesNotMatch(other, /has run/);
});

test('review 4: an alias already used for another resource is not moved silently; "Move it here" is a second press', async () => {
  const { document, server } = await askWithAlias({ otherAliases: { foo_bar: 'old_repo' } });
  document.querySelector('[data-alias-yes]').click();
  await wait();
  let box = text(document.querySelector('[data-alias-box]'));
  assert.match(box, /already used for old_repo/);
  assert.doesNotMatch(box, /remembered/);
  assert.equal(posts(server, /\/api\/aliases\//)[0].body.move, false);
  assert.deepEqual(server.aliases, [], 'nothing moved');
  document.querySelector('[data-alias-move]').click();
  await wait();
  assert.equal(posts(server, /\/api\/aliases\//)[1].body.move, true);
  box = text(document.querySelector('[data-alias-box]'));
  assert.match(box, /remembered/);
});

test('review 5: nothing the disclosure says uses the word "catalog"', async () => {
  const { document, app } = await setUp({ questions: [{ ...Q_NONE, purposes: [] }, Q_RUN] });
  app.state.investigations = [{ slug: 'inv', purposes: ['Learn'] }];
  app.state.investigation = 'inv';
  const html = app.whyHereHtml({ ...Q_RUN, purposes: ['Assess'] }, 0) + app.whyHereHtml({ ...Q_NONE, purposes: [] }, 1);
  assert.match(html, /none stated in the question list/);
  assert.match(html, /follows in the list's own order/);
  assert.doesNotMatch(html, /catalog/i);
});

test('walk 2026-10-12: an analysis the server reports behind a state-answered question reads "has run", not "not run"', async () => {
  const { document } = await setUp({ questions: [Q_TWO], stateFactOnly: true });
  document.querySelector('#qrow-0 button[data-ask-chat]').click();
  await wait();
  const st = text(document.querySelector('[data-runnable="cve_scan"] [data-run-status]'));
  assert.match(st, /has run/);
  assert.doesNotMatch(st, /not run/);
});
