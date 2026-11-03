/** Parity slice G1 (PI-001..PI-008): the Publish band of Curate, on a repository and on a database.
 *
 *  Real app.js, real router (the Curate nav button is clicked), a stateful stub server behind fetch.
 *  The rule under test: every word on screen follows the server's re-read rows, never the click. The
 *  known-negatives make the server answer 200 to a write while its record says otherwise, and watch
 *  the words follow the record.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const wait = (ms = 250) => new Promise((r) => setTimeout(r, ms));
const GUID = 'asset-guid-0001';
const REPORT = { guid: 'rep-guid-0001', qualified_name: 'SurveyReport::GitHubRepo::egeria_git::2026-10-07', display_name: 'Survey: egeria_git',
  surveyed_at: '2026-10-07T01:00:00', annotation_count: 42, schema_count: 0, table_count: 0, column_count: 0, description: '' };

function makeServer(over = {}) {
  const s = {
    calls: [],
    inEgeria: false, row: { word: 'none', read_at: '', sentence: '', first: '' }, project: { status: 'unset', word: 'no project', name: '' },
    canAgain: false,
    survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 7200, annotations: 42, steps: 12, stale_steps: 0, stale: [] },
    resurveyAnswers: 'ok',
    publishAnswers: 'ok',     // 'ok' | '428' | '409' | 'drop' (200 but nothing recorded) | 'sent'
    reports: [REPORT], reportsStatus: 200,
    readBack: 42,             // the annotation count the read-back finds (42 = what was published)
    goesOut: { viewer: 'dan', in_egeria: false, findable: [], feedback: [], doc_sources: [], waiting_for_others: 0, count: 0 },
    pressAnswer: { ok: true },
    annotations: [{ guid: 'ann-1', annotation_type: 'SchemaAnalysisAnnotation', summary: 'ok', confidence: 90, analysis_step: 'files', explanation: 'because', content_status: '' }],
    measurements: { slug: 'egeria_git', in_egeria: true, inventoried: true,
      profiles: [{ name: 'Profile File Types', type: 'ResourceProfileAnnotation', summary: '2 types', counts: { Python: 12, Markdown: 4 } }],
      retired: [{ label: 'Python', dataset_guid: 'ds-old', word: 'retired mechanism · kept in Egeria', qualified_name: 'DataSet::egeria_git::Python' }],
      retired_word: 'retired mechanism · kept in Egeria' },
    ...over,
  };
  // Brief F addendum: the server's `repo_publish.confirmation` on the row (the one "same result" function),
  // mirrored here so the band draws from what the server would send.
  const confirmOf = (row) => {
    const day = String(row.read_at || '').slice(5, 10);
    if (row.word === 'published') {
      if (row.annotations_in_egeria == null) return { state: 'unconfirmed', word: `published ${day} · not yet confirmed`, read_at: row.read_at };
      return row.annotations_in_egeria === row.annotation_count
        ? { state: 'confirmed', word: `published ${day} · confirmed`, read_at: row.read_at, egeria_count: row.annotations_in_egeria, published_count: row.annotation_count }
        : { state: 'differs', word: `published ${day} · Egeria shows ${row.annotations_in_egeria}`, read_at: row.read_at, egeria_count: row.annotations_in_egeria, published_count: row.annotation_count };
    }
    if (row.word === 'sent') return { state: 'unconfirmed', word: `published ${day} · not yet confirmed`, read_at: row.read_at };
    return { state: '', word: '' };
  };
  const state = () => ({ slug: 'egeria_git', in_egeria: s.inEgeria, asset_guid: s.inEgeria ? GUID : '',
    row: { ...s.row, confirm: s.row.confirm || confirmOf(s.row) },
    project: s.project, can_publish_again: s.canAgain, survey: s.survey, goes_out: s.goesOut });
  globalThis.fetch = async (url, opts = {}) => {
    const u = String(url);
    const method = opts.method || 'GET';
    const body = opts.body ? JSON.parse(opts.body) : undefined;
    s.calls.push({ method, url: u, body });
    const ok = (b, status = 200) => ({ ok: true, status, json: async () => b });
    const err = (status, detail) => ({ ok: false, status, statusText: detail, json: async () => ({ detail }) });
    if (u.endsWith('/publish-state')) return ok(state());
    if (u.endsWith('/resurvey')) {
      if (s.resurveyAnswers === '409') return err(409, 'a survey for egeria_git is already running');
      s.survey = { ...s.survey, exists: true, surveyed_at: '2026-10-07T09:00:00', age_seconds: 5, stale_steps: 0, stale: [] };
      return ok({ ok: true, errors: [], survey: s.survey });
    }
    if (u.endsWith('/publish-report')) {
      if (s.publishAnswers === 'nosurvey') return err(409, 'no survey to publish yet · run the first survey');
      if (s.publishAnswers === '428') return err(428, 'egeria_project_context_required');
      if (s.publishAnswers === '409') return err(409, 'a publish for egeria_git is already running');
      if (s.publishAnswers === '403') return err(403, 'zone sales-zone does not grant dan publish in Egeria');
      if (body.without_project) s.project = { status: 'declined', word: 'no project (chosen)', name: '' };
      if (s.publishAnswers === 'ok') {
        s.inEgeria = true; s.canAgain = true;
        s.row = { word: 'published', read_at: new Date().toISOString(), report_guid: REPORT.guid, reused: false, annotation_count: 42,
          annotations_in_egeria: s.readBack, surveyed_at: s.survey.surveyed_at };
        if (s.afterPress) s.afterPress(s);
      } else if (s.publishAnswers === 'sent') {
        s.inEgeria = true; s.canAgain = true; s.row = { word: 'sent', read_at: new Date().toISOString(), sentence: 'sent · waiting for Egeria', first: '' };
      } else if (s.publishAnswers === 'fail') {
        s.row = { word: 'not published', read_at: '', sentence: 'OMAG-500 refused. Context: more', first: 'OMAG-500 refused.' };
      } // 'drop': 200 and nothing recorded
      return ok(s.pressAnswer);
    }
    if (u.endsWith('/forget-links')) {
      s.inEgeria = false; s.canAgain = false; s.row = { word: 'forgotten', read_at: new Date().toISOString(), sentence: '', first: '' };
      return ok({ ok: true, surveys_deleted: 3, asset_guid_cleared: true });
    }
    if (u.endsWith('/root-folder')) return ok(s.rootFolder || { state: 'not_attached', guid: '', read_back_at: '', by: '', why: '' });
    if (u.endsWith('/egeria-surveys')) return s.reportsStatus === 200 ? ok(s.reports) : err(s.reportsStatus, 'Egeria is not reachable');
    if (u.includes('/egeria-surveys/') && u.endsWith('/annotations')) return ok(s.annotations);
    if (u.endsWith('/file-type-measurements')) return ok(s.measurements);
    if (u.includes('/curate/plan')) return ok({ technology_type: 'Git repository', disposition: 'using', in_population: true,
      last_surveyed_at: null, what_it_is: [], what_it_holds: [], made_of: [], relates: [], writes: {}, commits: [], survey: { exists: false }, project: { status: 'linked', word: 'project', name: 'P' } });
    if (u.includes('/components/tree')) return ok({ branches: [], topology: '' });
    if (u.includes('/blueprints')) return ok({ blueprints: [], perspectives: [] });
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/investigations') || u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    if (u.includes('/api/curate/tags') && !u.includes('/api/curate/tags/')) return ok([]);
    return ok(u.includes('/api/curate/') ? [] : {});
  };
  return s;
}

async function setUp(resourceType, serverOver = {}, { signedIn = true, slug = 'egeria_git', rows = {} } = {}) {
  const { document, window } = makeDomEnvironment();
  const server = makeServer(serverOver);
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  app.state.resourceType = resourceType;
  app.state.selectedSlug = slug;
  app.state.stage = 'understanding';
  app.state.subTab = 'questions';
  app.state.investigations = []; app.state.investigation = '';
  app.state.workListSlug = null; app.state.workListIndex = false;
  app.state.me = signedIn ? { user_id: 'dan' } : null;
  app.state.groups = [];
  app.state.databasesLoaded = true; app.state.filesystemsLoaded = true;
  app.state.projects = [{ slug, ...rows }]; app.state.databases = [{ slug, ...rows }]; app.state.filesystems = [{ slug, ...rows }];
  app.state.perspectives = []; app.state.allPerspectives = [];
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  return { document, app, server };
}

async function openCurate(document) {
  document.querySelector('#intent-nav button[data-stage="curate"]').click();
  await wait();
}
// A repository's Publish band is drawn on two tabs from one read: the report (Describe and publish) and the
// links and project (Contents). `band` is the report half, `items` the Contents half; `q` finds in either.
const band = (d) => d.getElementById('curate-host').querySelector('[data-curate-band="publish"]');
const items = (d) => d.getElementById('curate-host').querySelector('[data-curate-band="publish-items"]');
const q = (d, sel) => band(d).querySelector(sel) || (items(d) && items(d).querySelector(sel));
const post = (server, frag) => server.calls.filter((c) => c.method === 'POST' && c.url.includes(frag));

/* ── PI-001 / PI-002 ─────────────────────────────────────────────────── */

test('a repository never published: not in Egeria, no row, the publish control is there', async () => {
  const { document } = await setUp('repo');
  await openCurate(document);
  const t = band(document).textContent;
  assert.match(t, /Publish to Egeria/);
  assert.match(q(document, '[data-publish-row]').textContent, /not in Egeria/, 'line 1 carries one state');
  assert.equal(q(document, '[data-publish-go]').textContent.trim(), 'Publish to Egeria →');
  assert.equal(q(document, '[data-publish-go]').disabled, false);
  assert.equal(q(document, '[data-forget-open]').disabled, true, 'nothing to forget when not in Egeria');
});

test('signed out: the publish control is disabled and says why', async () => {
  const { document } = await setUp('repo', {}, { signedIn: false });
  await openCurate(document);
  assert.equal(q(document, '[data-publish-go]').disabled, true);
  assert.match(q(document, '[data-publish-feedback]').textContent, /sign in to publish/);
});

test('publish: POSTs whole (no zones, no steps), then the words come from the re-read; the control becomes Publish again', async () => {
  const { document, server } = await setUp('repo', { project: { status: 'personal', word: 'personal exploration', name: '' } });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  const [c] = post(server, '/api/egeria/egeria_git/publish-report');
  assert.deepEqual(c.body, { without_project: false }, 'no zone field, no steps field');
  assert.match(q(document, '[data-publish-row]').textContent, /in Egeria · published \d\d-\d\d · confirmed/);
  assert.equal(q(document, '[data-publish-confirm]').dataset.publishConfirm, 'confirmed');
  assert.equal(q(document, '[data-publish-status] [data-guid]'), null, 'the GUID is not on Describe and publish: only the word');
  assert.equal(q(document, '[data-publish-asset] [data-guid]').dataset.guid, GUID, 'the asset GUID is shown on Contents');
  assert.ok(items(document).querySelector('[data-copy-guid]'), 'and copyable there');
  assert.equal(band(document).querySelector('[data-publish-status] [data-copy-guid]'), null, 'the asset\'s copy GUID is not on tab 1 (the reports list keeps its own report GUIDs)');
  assert.equal(q(document, '[data-publish-go]').textContent.trim(), 'Publish again');
  assert.match(q(document, '[data-publish-project]').textContent, /personal exploration/);
  assert.ok(server.calls.filter((x) => x.url.endsWith('/publish-state')).length >= 2, 'the state was re-read after the write');
});

test('KNOWN-NEGATIVE: a 200 with nothing recorded does not say published', async () => {
  const { document } = await setUp('repo', { publishAnswers: 'drop' });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  const t = band(document).textContent;
  assert.doesNotMatch(t, /published \d\d-\d\d/);
  assert.match(q(document, '[data-publish-row]').textContent, /not in Egeria/);
});

test('a report sent but not read back says "sent · waiting for Egeria", never published', async () => {
  const { document } = await setUp('repo', { publishAnswers: 'sent' });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  assert.match(q(document, '[data-publish-row]').textContent, /published \d\d-\d\d · not yet confirmed/);
  assert.equal(q(document, '[data-publish-confirm]').dataset.publishConfirm, 'unconfirmed', 'never implies confirmed');
});

test('Egeria refusing reads "not published · <first sentence>" with the full sentence one gesture away', async () => {
  const { document } = await setUp('repo', { publishAnswers: 'fail' });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  const row = q(document, '[data-publish-row]');
  assert.match(row.textContent, /not published · OMAG-500 refused\./);
  assert.match(row.querySelector('[data-egeria-sentence]').textContent, /Context: more/);
});

test('a second press while the first is pending is ignored: one POST, and the control looks pending', async () => {
  const { document, server } = await setUp('repo');
  await openCurate(document);
  const b = q(document, '[data-publish-go]');
  b.click(); b.click(); b.click();
  assert.equal(b.disabled, true);
  assert.match(b.textContent, /…/);
  await wait();
  assert.equal(post(server, '/publish-report').length, 1);
});

test('a 409 (already running) says so and sends nothing else', async () => {
  const { document, server } = await setUp('repo', { publishAnswers: '409' });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  assert.match(q(document, '[data-publish-feedback]').textContent, /already running/);
  assert.equal(post(server, '/publish-report').length, 1);
});

/* ── PI-006: the project gate ────────────────────────────────────────── */

test('a 428 shows a cue and a word on Describe and publish; the three choices are the ONE Project row on Contents', async () => {
  const { document, server } = await setUp('repo', { publishAnswers: '428' });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  const gate = band(document).querySelector('[data-publish-gate-panel]');
  assert.ok(gate);
  assert.match(gate.textContent, /needs a project/);
  assert.ok(gate.querySelector('[data-cue]'), 'a cue, not a sentence');
  assert.equal(gate.querySelector('[data-cue]').title, 'no Egeria project context · bind this investigation to a project, or publish without one', 'the sentence on demand');
  assert.equal(gate.querySelector('[data-gate-bind]') || gate.querySelector('[data-gate-without]'), null, 'no controls on tab 1');
  assert.equal(q(document, '[data-publish-go]').disabled, false, 'the control is enabled again, not stuck pending');
  const row = items(document).querySelector('[data-curate-project-row]');
  assert.ok(row.querySelector('[data-gate-bind]') && row.querySelector('[data-gate-without]') && row.querySelector('[data-commit-decline]'));
  assert.equal(document.querySelectorAll('[data-gate-bind]').length, 1, 'the choices appear once on the whole screen');
  server.publishAnswers = 'ok';
  row.querySelector('[data-gate-without]').click();
  await wait();
  const ps = post(server, '/publish-report');
  assert.deepEqual(ps.map((c) => c.body.without_project), [false, true]);
  assert.match(q(document, '[data-publish-row]').textContent, /in Egeria · published \d\d-\d\d · confirmed/);
  assert.match(q(document, '[data-publish-project]').textContent, /no project \(chosen\)/);
  assert.equal(items(document).querySelector('[data-gate-bind]'), null, 'answered: the choices are gone');
});

test('"Bind this investigation to a project" goes to the Investigation stage and publishes nothing', async () => {
  const { document, app, server } = await setUp('repo', { publishAnswers: '428' });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  items(document).querySelector('[data-gate-bind]').click();      // the Project row on Contents
  await wait();
  assert.equal(app.state.stage, 'investigation');
  assert.equal(post(server, '/publish-report').length, 1, 'only the first press went out');
});

/* ── PI-005 ──────────────────────────────────────────────────────────── */

test('Forget Egeria links: the confirmation says what survives; Cancel sends nothing; confirm forgets in RE and re-reads', async () => {
  const { document, server } = await setUp('repo', { inEgeria: true, canAgain: true, row: { word: 'published', read_at: new Date().toISOString(), report_guid: REPORT.guid } });
  await openCurate(document);
  assert.equal(q(document, '[data-forget-open]').textContent.trim(), 'Forget Egeria links…');
  q(document, '[data-forget-open]').click();
  assert.equal(q(document, '[data-forget-sentence]').textContent,
    'Egeria is unchanged; Resource Explorer forgets its cached GUIDs and survey history for this resource and re-reads them on the next publish.');
  q(document, '[data-forget-cancel]').click();
  assert.equal(post(server, '/forget-links').length, 0);
  assert.equal(q(document, '[data-forget-panel]'), null);
  q(document, '[data-forget-open]').click();
  const go = q(document, '[data-forget-go]');
  go.click(); go.click();
  await wait();
  assert.equal(post(server, '/forget-links').length, 1, 'a second press is ignored');
  assert.match(q(document, '[data-publish-status]').textContent, /not in Egeria/);
  assert.match(q(document, '[data-publish-row]').textContent, /links forgotten/);
  assert.match(q(document, '[data-publish-feedback]').textContent, /Egeria unchanged/);
  assert.doesNotMatch(band(document).textContent, /reset Egeria/i, 'roll-forward language, never "reset Egeria"');
});

/* ── PI-003 / PI-008: the one Egeria reports component ────────────────── */

test('Egeria reports: lists the asset\'s reports, expands annotations by report, shows a copyable GUID', async () => {
  const { document, server } = await setUp('repo', { inEgeria: true });
  await openCurate(document);
  const r = q(document, `[data-egeria-report="${REPORT.guid}"]`);
  assert.ok(r, 'the report from Egeria is listed (not only runs RE launched)');
  assert.match(r.textContent, /42/);
  assert.ok(r.querySelector('[data-copy-guid]'));
  assert.equal(server.calls.filter((c) => c.url.includes('/annotations')).length, 0, 'annotations are read on expand, not before');
  r.querySelector('[data-report-toggle]').click();
  await wait();
  assert.match(r.querySelector(`[data-report-annotations="${REPORT.guid}"]`).textContent, /SchemaAnalysisAnnotation/);
  assert.equal(server.calls.filter((c) => c.url.includes(`/egeria-surveys/${REPORT.guid}/annotations`)).length, 1);
  r.querySelector('[data-report-toggle]').click();
  assert.equal(r.querySelector(`[data-report-annotations="${REPORT.guid}"]`).textContent, '');
});

test('Egeria reports: Refresh re-reads; a failed read says so and is not "no reports"', async () => {
  const { document, server } = await setUp('repo', { inEgeria: true });
  await openCurate(document);
  const before = server.calls.filter((c) => c.url.endsWith('/egeria-surveys')).length;
  q(document, '[data-reports-refresh]').click();
  await wait();
  assert.equal(server.calls.filter((c) => c.url.endsWith('/egeria-surveys')).length, before + 1);
  server.reportsStatus = 503;
  q(document, '[data-reports-refresh]').click();
  await wait();
  assert.match(q(document, '[data-reports-error]').textContent, /Egeria could not be read: Egeria is not reachable/);
  assert.doesNotMatch(band(document).textContent, /holds no survey reports/);
});

test('Egeria reports: not in Egeria says so and never asks Egeria', async () => {
  const { document, server } = await setUp('repo', { inEgeria: false });
  await openCurate(document);
  assert.match(q(document, '[data-egeria-reports]').textContent, /Not in Egeria: there is no asset to list reports from/);
  assert.equal(server.calls.filter((c) => c.url.endsWith('/egeria-surveys')).length, 0);
});

test('Ask about this → prefills the chat rail with the report\'s identity and sends nothing', async () => {
  const { document, server } = await setUp('repo', { inEgeria: true });
  const ta = document.createElement('textarea'); ta.id = 'ask-input'; document.body.appendChild(ta);
  await openCurate(document);
  const before = server.calls.length;
  q(document, `[data-report-ask="${REPORT.guid}"]`).click();
  assert.match(ta.value, /Survey: egeria_git/);
  assert.match(ta.value, new RegExp(REPORT.guid));
  assert.equal(server.calls.length, before, 'prefill only: no request left');
});

/* ── one component, every kind ───────────────────────────────────────── */

test('a database: one state line (never the registry flag), reports from the database route, no publish control', async () => {
  const { document, server } = await setUp('db', {}, { slug: 'coco_pharma', rows: { is_published: true, egeria_asset_guid: 'db-asset-guid' } });
  await openCurate(document);
  const t = band(document).textContent;
  // The registry row says is_published, but no commit proof was read: the line must not say the database is in Egeria.
  assert.doesNotMatch(t, /\bin Egeria\b/);
  assert.doesNotMatch(q(document, '[data-db-publish-state]').textContent, /published/);
  assert.ok(q(document, '[data-publish-status] [data-db-publish-state]'), 'the state line is in the Publish band');
  assert.match(t, /Contents ›/);
  assert.equal(q(document, '[data-publish-asset] [data-guid]').dataset.guid, 'db-asset-guid', 'the asset row (GUID + copy) is on Contents');
  assert.equal(document.querySelector('[data-curate-tab-panel="describe"] [data-publish-asset]'), null);
  assert.equal(q(document, '[data-publish-go]'), null, 'a database has no publish press on tab 1');
  assert.doesNotMatch(t, /Catalog → above/);
  assert.equal(server.calls.filter((c) => c.url.includes('/api/databases/coco_pharma/egeria-surveys')).length, 1);
  assert.ok(q(document, '[data-egeria-report]'));
});

test('a file system whose survey held a GUID but has no proof row: held, not "not attached", reports listed from the GUID', async () => {
  // Held-GUID case: the survey path published the folder (egeria_asset_guid is set) and no read-back by GUID was
  // recorded. That is "not measured", which must not read as "not attached" or as "no asset to list reports from".
  const a = await setUp('filesystem',
    { rootFolder: { state: 'held', guid: 'fs-guid', read_back_at: '', by: '', why: '' } },
    { slug: 'docs_fs', rows: { is_published: true, egeria_asset_guid: 'fs-guid' } });
  await openCurate(a.document);
  assert.match(band(a.document).textContent, /GUID held · not read back yet/);
  assert.doesNotMatch(band(a.document).textContent, /root folder not attached/);
  assert.doesNotMatch(band(a.document).textContent, /no asset to list reports from/);
  assert.equal(a.server.calls.filter((c) => c.url.includes('/api/filesystems/docs_fs/egeria-surveys')).length, 1);
  assert.equal(q(a.document, '[data-publish-go]'), null);
  assert.ok(q(a.document, '[data-fs-publish-go]'), 'the one file-system Publish');
});

test('a file system with nothing held and no proof row: not attached, and no reports are asked for', async () => {
  const a = await setUp('filesystem', {}, { slug: 'docs_fs', rows: { is_published: false, egeria_asset_guid: '' } });
  await openCurate(a.document);
  assert.match(band(a.document).textContent, /root folder not attached/);
  assert.equal(a.server.calls.filter((c) => c.url.includes('/api/filesystems/docs_fs/egeria-surveys')).length, 0);
});

test('a file system with a read-back proof row: attached, and its reports are read from the file-system route', async () => {
  const { document, server } = await setUp('filesystem',
    { rootFolder: { state: 'attached', guid: 'fs-guid', read_back_at: '2026-10-20T09:30:00', by: 'dan', why: '' } },
    { slug: 'docs_fs', rows: { is_published: false, egeria_asset_guid: 'fs-guid' } });
  await openCurate(document);
  assert.match(band(document).textContent, /root folder attached/);
  assert.equal(server.calls.filter((c) => c.url.includes('/api/filesystems/docs_fs/egeria-surveys')).length, 1);
});

/* ── PI-004 ──────────────────────────────────────────────────────────── */

test('File types: the section is a read-only display of the report profiles; the old press is gone', async () => {
  const { document, server } = await setUp('repo', { inEgeria: true });
  await openCurate(document);
  assert.equal(q(document, '[data-file-types-section]'), null, 'the DataSet section is removed');
  const sec = q(document, '[data-file-measurements-section]');
  assert.equal(server.calls.filter((c) => c.url.endsWith('/file-type-measurements')).length, 0, 'nothing is read until opened');
  sec.open = true;
  sec.dispatchEvent(new document.defaultView.Event('toggle'));
  await wait();
  const row = sec.querySelector('[data-file-measurement="Profile File Types"]');
  assert.ok(row, "Egeria's own annotation name is shown verbatim");
  assert.match(row.textContent.replace(/\s+/g, ' '), /in the report .*Python 12 · Markdown 4/);
  assert.equal(sec.querySelector('[data-file-types-go]'), null, 'no Catalog file types button');
  assert.equal(sec.querySelectorAll('input[type=checkbox]').length, 0);
  const old = sec.querySelector('[data-retired-dataset="Python"]');
  assert.match(old.textContent.replace(/\s+/g, ' '), /retired mechanism · kept in Egeria/);
  assert.equal(old.querySelector('button')?.textContent.trim(), 'copy', 'only a copy-GUID control: no delete or archive');
  assert.equal(server.calls.filter((c) => c.method === 'POST' && /file-types|catalog-elements/.test(c.url)).length, 0, 'nothing is ever posted');
});

test('File types: no inventory yet says so and shows nothing to press', async () => {
  const { document } = await setUp('repo', { inEgeria: false, measurements: { inventoried: false, profiles: [], retired: [] } });
  await openCurate(document);
  const sec = q(document, '[data-file-measurements-section]');
  sec.open = true;
  sec.dispatchEvent(new document.defaultView.Event('toggle'));
  await wait();
  assert.match(sec.textContent, /survey the repository first/);
});

/* ── brief section 1: publish the survey already kept; re-survey is its own act ───────────────── */

test('the band shows the survey it would publish, with its age, before the press', async () => {
  const { document } = await setUp('repo');
  await openCurate(document);
  const line = q(document, '[data-publish-survey="kept"]');
  assert.match(line.textContent.replace(/\s+/g, ' '), /kept survey · 2026-10-07 · .*ago · 42 annotations · 12 steps ran/);
  assert.equal(q(document, '[data-publish-go]').textContent.trim(), 'Publish to Egeria →');
  assert.equal(q(document, '[data-resurvey-go]').textContent.trim(), 'Re-survey now →');
  assert.equal(q(document, '[data-publish-stale]'), null, 'nothing stale: no stale line');
});

test('stale steps are named, not auto-run: a ⚠ cue, the word stale, the list one gesture away', async () => {
  const { document } = await setUp('repo', { survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 7200,
    annotations: 42, steps: 12, stale_steps: 3, stale: ['repo_health', 'repo_language', 'repo_security'] } });
  await openCurate(document);
  const stale = q(document, '[data-publish-stale]');
  assert.match(stale.textContent.replace(/\s+/g, ' '), /stale 3 of 12 steps are stale \(older than their refresh rule\) · re-survey to refresh/);
  assert.equal(stale.querySelector('[data-cue]').dataset.cue, 'partial');
  assert.match(stale.querySelector('details').textContent, /repo_health, repo_language, repo_security/);
});

test('no survey: Publish is disabled, the reason is directly under it, and only the survey control is live', async () => {
  const { document, server } = await setUp('repo', { survey: { exists: false, sentence: 'no survey to publish yet · run the first survey' } });
  await openCurate(document);
  assert.equal(q(document, '[data-publish-go]').disabled, true);
  assert.equal(q(document, '[data-resurvey-go]').disabled, false);
  assert.equal(q(document, '[data-resurvey-go]').textContent.trim(), 'Run the first survey →');
  assert.equal(q(document, '[data-publish-blocker]').textContent.trim(), 'no survey to publish yet · run the first survey');
  q(document, '[data-publish-go]').click();
  await wait();
  assert.equal(post(server, '/publish-report').length, 0, 'a disabled Publish sent nothing');
});

test('surveyed before kept surveys: not the first survey; a cue and short words, Re-survey now, Publish still off', async () => {
  const { document, server } = await setUp('repo', { survey: { exists: false, surveyed_before_kept: true,
    sentence: 'surveyed before 10-07 · nothing kept to publish · re-survey to publish' } });
  await openCurate(document);
  assert.equal(q(document, '[data-publish-go]').disabled, true);
  assert.equal(q(document, '[data-resurvey-go]').disabled, false);
  assert.equal(q(document, '[data-resurvey-go]').textContent.trim(), 'Re-survey now →');
  assert.equal(q(document, '[data-publish-blocker]').textContent.trim(), 'surveyed before 10-07 · nothing kept to publish · re-survey to publish');
  assert.equal(q(document, '[data-publish-survey="none"] [data-cue]').dataset.cue, 'partial');
  assert.doesNotMatch(q(document, '[data-publish-survey]').textContent, /first survey/);
  q(document, '[data-publish-go]').click();
  await wait();
  assert.equal(post(server, '/publish-report').length, 0);
});

test('surveyed before kept surveys, flag only (no sentence from the server): the same words', async () => {
  const { document } = await setUp('repo', { survey: { exists: false, surveyed_before_kept: true } });
  await openCurate(document);
  assert.equal(q(document, '[data-resurvey-go]').textContent.trim(), 'Re-survey now →');
  assert.equal(q(document, '[data-publish-blocker]').textContent.trim(), 'surveyed before 10-07 · nothing kept to publish · re-survey to publish');
});

test('Re-survey now runs the survey and nothing else: no publish is sent, and the age changes from the re-read', async () => {
  const { document, server } = await setUp('repo', { survey: { exists: true, surveyed_at: '2026-10-01T01:00:00', age_seconds: 600000,
    annotations: 42, steps: 12, stale_steps: 2, stale: ['repo_health', 'repo_language'] } });
  await openCurate(document);
  assert.match(q(document, '[data-publish-survey]').textContent, /2026-10-01/);
  const b = q(document, '[data-resurvey-go]');
  b.click();
  assert.equal(b.disabled, true, 'a pressed control ignores a second press');
  assert.equal(b.textContent.trim(), 'Re-surveying …', 'an immediate visible change');
  assert.equal(q(document, '[data-publish-go]').disabled, true, 'no publish mid-survey');
  await wait(400);
  assert.equal(post(server, '/resurvey').length, 1);
  assert.equal(post(server, '/publish-report').length, 0, 're-survey must not publish');
  assert.match(q(document, '[data-publish-survey]').textContent, /2026-10-07/);
  assert.equal(q(document, '[data-publish-stale]'), null);
  assert.equal(q(document, '[data-publish-go]').disabled, false);
});

test('a publish answered 409 "no survey" says nothing was sent, not "already running"', async () => {
  const { document } = await setUp('repo', { publishAnswers: 'nosurvey' });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  assert.match(q(document, '[data-publish-feedback]').textContent, /no survey to publish yet · run the first survey/);
  assert.doesNotMatch(q(document, '[data-publish-feedback]').textContent, /already running/);
});

/* ── Brief F: the three-line band, the press acknowledged, success validated, failure a pop-up ───────────── */

const docOf = (d) => d;   // the pop-up is on the document, not in the band

test('Brief F: a press is acknowledged at once ("◔ publishing…"), with no per-step list on tab 1', async () => {
  const { document } = await setUp('repo', { project: { status: 'personal', word: 'personal exploration', name: '' } });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  const fb = q(document, '[data-publish-feedback]');
  assert.match(fb.textContent, /publishing…/);
  assert.equal(fb.querySelector('[data-cue]').dataset.cue, 'running');
  assert.equal(band(document).querySelector('[data-curate-commit-steps], [data-publish-steps], ol'), null, 'no stepper');
  await wait();
});

test('Brief F: success is validated by line 1: "✓ in Egeria · published · confirmed", read back in small text', async () => {
  const { document } = await setUp('repo', { project: { status: 'personal', word: 'personal exploration', name: '' } });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  const c = q(document, '[data-publish-confirm] [data-cue]');
  assert.equal(c.dataset.cue, 'measured');
  assert.match(c.textContent, /✓ in Egeria · published \d\d-\d\d · confirmed/);
  assert.ok(q(document, '[data-publish-read-back]'), 'the read-back time is labelled as such');
  assert.match(q(document, '[data-publish-feedback]').textContent, /confirmed/);
  assert.equal(docOf(document).querySelector('[data-publish-failure]'), null, 'no pop-up on success');
});

test('Brief F: a read-back that differs says what Egeria shows with the partial cue, never "confirmed"', async () => {
  const { document } = await setUp('repo', { readBack: 40, project: { status: 'personal', word: 'personal exploration', name: '' } });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  const c = q(document, '[data-publish-confirm] [data-cue]');
  assert.equal(c.dataset.cue, 'partial');
  assert.match(c.textContent, /Egeria shows 40/);
  assert.doesNotMatch(c.textContent, / confirmed/);
  assert.match(c.title, /Published 42 annotations; Egeria's read-back shows 40/);
});

test('Brief F: a failure is a pop-up with the items and Retry, AND a persistent cue from the rows', async () => {
  const { document, server } = await setUp('repo', { publishAnswers: 'fail', project: { status: 'personal', word: 'personal exploration', name: '' } });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  const pop = docOf(document).querySelector('[data-publish-failure]');
  assert.ok(pop, 'a pop-up');
  assert.equal(pop.getAttribute('role'), 'alertdialog');
  assert.match(pop.textContent, /report · .*not published · OMAG-500 refused\./);
  assert.ok(pop.querySelector('[data-publish-failure-retry]'), 'Retry where a retry can help');
  pop.querySelector('[data-publish-failure-close]').click();
  assert.equal(docOf(document).querySelector('[data-publish-failure]'), null);
  assert.match(q(document, '[data-publish-row]').textContent, /not published · OMAG-500 refused\./, 'the cue stays after the pop-up goes');
  server.publishAnswers = 'ok';
  q(document, '[data-publish-go]').click();
  await wait();
  assert.equal(post(server, '/publish-report').length, 2);
});

test('Brief F: a refusal pops up "not permitted" with no Retry; feedback refused per item comes from the re-read list', async () => {
  const { document } = await setUp('repo', { project: { status: 'personal', word: 'personal exploration', name: '' },
    afterPress: (srv) => { srv.goesOut = { ...srv.goesOut, feedback: [{ kind: 'rating', id: 'f1', state: 'not_permitted', word: 'not permitted · zone z does not grant dan add feedback' }], count: 1 }; } });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  const pop = docOf(document).querySelector('[data-publish-failure]');
  assert.match(pop.textContent, /your rating · .*not permitted/);
  assert.equal(pop.querySelector('[data-publish-failure-retry]'), null, 'a refusal needs a permission change, not a retry');
  const item = q(document, '[data-goes-out="rating:f1"]');
  assert.match(item.textContent, /not permitted/);
  assert.equal(item.querySelector('[data-cue]').dataset.cue, 'no_access');
  const { document: d2 } = await setUp('repo', { publishAnswers: '403' });
  await openCurate(d2);
  band(d2).querySelector('[data-publish-go]').click();
  await wait();
  assert.equal(d2.querySelector('[data-publish-failure] [data-publish-failure-retry]'), null);
  assert.match(d2.querySelector('[data-publish-failure]').textContent, /not permitted/);
});

test('Brief F: the steps of a press are in the data, behind "details ▸" only, closed by default', async () => {
  const { document } = await setUp('repo', { project: { status: 'personal', word: 'personal exploration', name: '' },
    pressAnswer: { ok: true, report_guid: 'rep-guid-0001', sent: { findable: { queued: 1 }, feedback: { queued: 2, refused: '', not_permitted: [] } } } });
  await openCurate(document);
  assert.equal(q(document, '[data-publish-details]'), null, 'nothing before a press');
  q(document, '[data-publish-go]').click();
  await wait();
  const d = q(document, '[data-publish-details]');
  assert.ok(d && d.tagName === 'DETAILS' && !d.open);
  assert.match(d.querySelector('summary').textContent, /details ▸/);
  assert.match(d.textContent, /tags and group · 1 queued/);
  assert.match(d.textContent, /your feedback · 2 sent/);
});

test('Brief F: line 2 lists what the ONE press sends, each a cue and a word; others\' feedback waits for them', async () => {
  const { document } = await setUp('repo', { goesOut: { viewer: 'dan', in_egeria: true,
    findable: [{ kind: 'tag', name: 'observability', label: 'observability', state: 'not_sent', word: 'saved · will publish', action: 'link' }],
    feedback: [{ kind: 'rating', id: 'f9', state: 'saved', word: 'saved · will publish' }, { kind: 'note', id: 'j9', state: 'saved', word: 'saved · will publish' }],
    doc_sources: [], waiting_for_others: 2, count: 3 } });
  await openCurate(document);
  const next = q(document, '[data-publish-next]');
  assert.match(next.textContent, /next publish/);
  assert.match(q(document, '[data-goes-out="report"]').textContent, /report/);
  assert.match(q(document, '[data-goes-out="tag:observability"]').textContent, /tag observability .*saved · will publish/);
  assert.equal(q(document, '[data-goes-out="tag:observability"] [data-cue]').dataset.cue, 'stored');
  assert.match(q(document, '[data-goes-out="rating:f9"]').textContent, /your rating/);
  assert.match(q(document, '[data-goes-out="note:j9"]').textContent, /your note/);
  assert.match(q(document, '[data-goes-out-others]').textContent, /2 from others · waiting for them/);
});

test('Brief F: the Egeria report rows live inside "Show in Egeria ▸" (closed), with no "Egeria reports" heading', async () => {
  const { document } = await setUp('repo', { inEgeria: true, canAgain: true,
    row: { word: 'published', read_at: '2026-10-11T16:36:00', report_guid: REPORT.guid, annotation_count: 42, annotations_in_egeria: 42 } });
  await openCurate(document);
  await wait();
  const show = q(document, '[data-publish-show-egeria]');
  assert.ok(show && show.tagName === 'DETAILS' && !show.open);
  assert.match(show.querySelector('summary').textContent, /Show in Egeria ▸/);
  assert.ok(show.querySelector('[data-egeria-reports]'), 'the report rows are inside');
  assert.doesNotMatch(band(document).textContent, /Egeria reports/);
});

test('Brief F addendum B: after a press the Findable band re-reads its state (a saved tag then reads published)', async () => {
  const { document, server } = await setUp('repo', { project: { status: 'personal', word: 'personal exploration', name: '' } });
  await openCurate(document);
  const before = server.calls.filter((c) => c.url.includes('/api/curate/egeria-state/')).length;
  q(document, '[data-publish-go]').click();
  await wait(400);
  const after = server.calls.filter((c) => c.url.includes('/api/curate/egeria-state/')).length;
  assert.ok(after > before, 'the tag state is read again from the rows after the press');
  assert.ok(server.calls.filter((c) => c.url.includes('/api/curate/feedback-state/')).length >= 1);
});

test('Brief F: line 2 says when the kept survey is newer than what is published, with its annotation count', async () => {
  const { document } = await setUp('repo', { inEgeria: true, canAgain: true,
    survey: { exists: true, surveyed_at: '2026-10-09T01:00:00', age_seconds: 300, annotations: 120, steps: 43, stale_steps: 1, stale: ['x'] },
    row: { word: 'published', read_at: '2026-10-08T16:36:00', report_guid: REPORT.guid, annotation_count: 117, annotations_in_egeria: 117, surveyed_at: '2026-10-08T01:00:00' } });
  await openCurate(document);
  assert.match(q(document, '[data-publish-newer]').textContent, /newer than published · 120 annotations/);
  assert.ok(q(document, '[data-publish-next] [data-publish-stale]'), 'staleness is on the next-publish line');
});

/* ── Brief F round 2 ─────────────────────────────────────────────────────── */

test('Brief F r2: the pop-up lists each tag or group item the press could not send, from the press answer', async () => {
  const { document } = await setUp('repo', { project: { status: 'personal', word: 'personal exploration', name: '' },
    pressAnswer: { ok: true, sent: { findable: { queued: 1, failed: [{ kind: 'tag', name: 'observability', word: 'not sent · OMAG-500 busy' }] } } } });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  const pop = document.querySelector('[data-publish-failure]');
  assert.ok(pop);
  assert.match(pop.textContent, /tag observability · .*not sent · OMAG-500 busy/);
});

test('Brief F r2: with no kept survey, a resource in Egeria with items to send can still press Publish; the report line says why', async () => {
  const { document, server } = await setUp('repo', { inEgeria: true, publishAnswers: 'nosurvey',
    survey: { exists: false, sentence: 'no survey to publish yet · run the first survey' },
    goesOut: { viewer: 'dan', in_egeria: true, findable: [], feedback: [{ kind: 'note', id: 'j1', state: 'saved', word: 'saved · will publish' }], doc_sources: [], waiting_for_others: 0, count: 1 } });
  await openCurate(document);
  const go = q(document, '[data-publish-go]');
  assert.equal(go.disabled, false);
  assert.match(q(document, '[data-publish-blocker]').textContent, /no survey to publish yet/);
  go.click();
  await wait();
  assert.equal(post(server, '/publish-report').length, 1);
  assert.match(q(document, '[data-publish-feedback]').textContent, /no survey/);
});

test('Brief F r2: a 428 still re-reads the band and the sections (the rest of the press went), then draws the gate', async () => {
  const { document, server } = await setUp('repo', { publishAnswers: '428' });
  await openCurate(document);
  const before = server.calls.filter((c) => c.url.endsWith('/publish-state')).length;
  q(document, '[data-publish-go]').click();
  await wait();
  assert.ok(server.calls.filter((c) => c.url.endsWith('/publish-state')).length > before, 're-read after the gated press');
  assert.ok(band(document).querySelector('[data-publish-gate-panel]'), 'the gate is on the re-drawn band');
});

test('Brief F r6: the details line counts unread-access items apart from refusals', async () => {
  const { document } = await setUp('repo', { project: { status: 'personal', word: 'personal exploration', name: '' },
    pressAnswer: { ok: true, sent: { feedback: { queued: 0, refused: '', not_permitted: [], unread: [{ kind: 'rating', id: 'f1' }] } } } });
  await openCurate(document);
  q(document, '[data-publish-go]').click();
  await wait();
  const d = q(document, '[data-publish-details]');
  assert.match(d.textContent, /1 not sent \(couldn't check access\)/);
  assert.doesNotMatch(d.textContent, /not permitted/);
});
