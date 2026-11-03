/** Declared extensibility slice 1, PR 2: Curate's publish table on a database's "Describe and publish" tab
 *  (DESIGN-DECLARED-EXTENSIBILITY-RENDERING-CHAT-CONFIDENCE.md §5). Real app.js and router, a stub server behind fetch.
 *  What is pinned:
 *    - the table sits on Describe, after What people say and before the Publish band, and nowhere else;
 *    - a row per declared measure, every column from the server's row: analysis, measure with unit, value with unit,
 *      as-of, confidence with basis, scope, and "writes as" (the declared type, or "generic measure · no native type
 *      declared");
 *    - Publish | Keep in RE saves at once (one PUT naming the analysis, the measure and the choice) and the row
 *      redraws from the server's answer: who chose and when, the count of rows chosen; never from the click;
 *    - a row that cannot publish has Publish disabled and says why; a refusal stays under its row;
 *    - the published word comes from the row's proof (`published`), and an absent value shows the declared sentence;
 *    - nobody signed in: neither half can be pressed; a table that cannot be read says so. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { setUp, baseView, schema, table, wait, flat } from './scope-test-kit.mjs';

globalThis.CSS = globalThis.CSS || { escape: (v) => String(v).replace(/(["\\\]\[])/g, '\\$1') };

const GENERIC = 'generic measure · no native type declared';
const row = (over = {}) => ({
  analysis_id: 'example_step_x', analysis: 'Example Step X', measure: 'rows_sampled', locator: 'example_step_x/rows_sampled',
  unit: 'rows', value_field: 'rows_sampled', as_of_field: 'asOf', state: 'measured', as_of: '2026-10-27T08:30:00',
  confidence: { form: 'estimated', basis: 'sample_ratio', method: 'one row in ten, sampled', words: 'estimated · one row in ten, sampled' }, scope: 'resource',
  writes_as: { route: 'generic', type: 'ResourceMeasureAnnotation', words: GENERIC, writable: true, why: '' },
  result_kind: 'distribution', value: 1200, value_words: '1200 rows', publishable: true, why_not: '', choice: null,
  published: { state: 'local', words: 'local · not published' }, ...over,
});
const TABLE = () => ({
  entity_type: 'database', slug: 'adventureworks', chosen: 0,
  press: "chosen measures go with the page's one Publish: the commit on Contents",
  rows: [
    row(),
    row({ measure: 'buckets', locator: 'example_step_x/buckets', unit: 'buckets', value: 2, value_words: '2 buckets',
      writes_as: { route: 'native', type: 'ResourceMeasureAnnotation', words: 'ResourceMeasureAnnotation', writable: true, why: '' },
      published: { state: 'published', words: 'published · read back 2026-10-27 09:01', read_at: '2026-10-27T09:01:00' } }),
    row({ analysis_id: 'data_class_match', analysis: 'Data Class Match', measure: 'columns_examined',
      locator: 'data_class_match/columns_examined', unit: 'columns', value: null, value_words: '—', as_of: '',
      absence: 'not_measured', absence_words: 'Data class match has not run on this database.',
      writes_as: { route: 'native', type: 'DataClassAnnotation', words: 'DataClassAnnotation', writable: false,
        why: 'Resource Explorer does not write DataClassAnnotation from a declaration yet' },
      publishable: false, why_not: 'Resource Explorer does not write DataClassAnnotation from a declaration yet',
      published: { state: 'not_read_back', words: 'sent · not read back 2026-10-26 10:00', why: 'the report could not be read back' } }),
  ],
});

/** The kit's server, with the publish table behind it. `pt.answer(body)` decides a PUT's answer. */
function withTable(pt) {
  return ({ server }) => {
    const inner = globalThis.fetch;
    globalThis.fetch = async (url, opts = {}) => {
      const u = String(url);
      if (!u.includes('/api/publish-table/')) return inner(url, opts);
      const method = opts.method || 'GET';
      const body = opts.body ? JSON.parse(opts.body) : undefined;
      server.calls.push({ method, url: u, body });
      if (pt.fail) return { ok: false, status: 500, statusText: 'registry down', json: async () => ({ detail: 'registry down' }) };
      if (method === 'GET') return { ok: true, status: 200, json: async () => pt.table };
      const out = pt.answer(body);
      return out.ok === false ? out : { ok: true, status: 200, json: async () => out };
    };
  };
}

const view = () => baseView({ schemas: [schema('sales', [table('sales', 'orders')])] });
async function open(pt, opts = {}) {
  const env = await setUp(view(), { tab: 'describe', beforeOpen: withTable(pt), ...opts });
  await wait(300);
  return env;
}
const band = (d) => d.querySelector('[data-curate-band="measures"]');
const rowEl = (d, loc) => [...d.querySelectorAll('[data-publish-row]')].find((e) => e.getAttribute('data-publish-row') === loc);

test('the table is on Describe, between What people say and the Publish band, and on no other tab', async () => {
  const { document } = await open({ table: TABLE() });
  const describe = document.querySelector('[data-curate-tab-panel="describe"]');
  const order = [...describe.querySelectorAll('[data-curate-band]')].map((e) => e.dataset.curateBand);
  assert.deepEqual(order.slice(order.indexOf('people'), order.indexOf('publish') + 1), ['people', 'measures', 'publish']);
  assert.equal(document.querySelectorAll('[data-publish-table-state]').length, 1);
  assert.ok(describe.querySelector('[data-publish-table-state="read"]'));
});

test('a row per declared measure, every column from the declaration', async () => {
  const { document } = await open({ table: TABLE() });
  assert.equal(document.querySelectorAll('[data-publish-row]').length, 3);
  const r = flat(rowEl(document, 'example_step_x/rows_sampled'));
  for (const words of ['Example Step X', 'rows_sampled', '· rows', '1200 rows', '2026-10-27 08:30', 'estimated · one row in ten, sampled',
    'resource', GENERIC, 'not chosen · stays in RE', 'local · not published']) {
    assert.ok(r.includes(words), `the row says "${words}": ${r}`);
  }
  const native = rowEl(document, 'example_step_x/buckets');
  assert.equal(native.querySelector('[data-writes-as]').dataset.writesAs, 'native');
  assert.equal(flat(native.querySelector('[data-writes-as]')), 'ResourceMeasureAnnotation');
  assert.equal(native.querySelector('[data-published]').dataset.published, 'published');
  assert.ok(flat(native).includes('published · read back 2026-10-27 09:01'));
  assert.ok(flat(band(document)).includes("the commit on Contents · 0 chosen to publish"));
});

test('a size in bytes reads as RE reads every other size, and no row shows a field path as its basis', async () => {
  const tbl = TABLE();
  tbl.rows.push(row({ measure: 'total_size', locator: 'example_step_x/total_size', unit: 'bytes', value: 131072,
    value_words: '131072 bytes' }));
  const { document } = await open({ table: tbl });
  const r = flat(rowEl(document, 'example_step_x/total_size'));
  assert.ok(r.includes('128 KB'), `a size is formatted: ${r}`);
  assert.ok(!r.includes('131072'), `no raw byte count: ${r}`);
  assert.ok(!flat(band(document)).includes('sample_ratio'), 'the basis field path is not shown');
});

test('a row that cannot publish disables Publish, says why, and still offers Keep in RE', async () => {
  const { document } = await open({ table: TABLE() });
  const r = rowEl(document, 'data_class_match/columns_examined');
  assert.equal(r.querySelector('[data-publish-choice="publish"]').disabled, true);
  assert.equal(r.querySelector('[data-publish-choice="keep"]').disabled, false);
  assert.equal(flat(r.querySelector('[data-why-not]')), 'Resource Explorer does not write DataClassAnnotation from a declaration yet');
  // The absent value says the declared sentence, never a zero; the failed read-back is a cue, not "published".
  assert.equal(r.querySelector('[data-absence]').dataset.absence, 'not_measured');
  assert.ok(flat(r).includes('Data class match has not run on this database.'));
  assert.equal(r.querySelector('[data-published]').dataset.published, 'not_read_back');
  assert.ok(flat(r).includes('sent · not read back'));
});

test('Publish saves at once and the row redraws from the saved row, never from the click', async () => {
  const pt = { table: TABLE() };
  pt.answer = (body) => row({ choice: { choice: body.choice, by: 'me', at: '2026-10-27T10:15:00' } });
  const { document, server } = await open(pt);
  rowEl(document, 'example_step_x/rows_sampled').querySelector('[data-publish-choice="publish"]').click();
  await wait(100);
  const puts = server.calls.filter((c) => c.method === 'PUT' && c.url.includes('/api/publish-table/'));
  assert.equal(puts.length, 1);
  assert.ok(puts[0].url.endsWith('/api/publish-table/database/adventureworks/choice'));
  assert.deepEqual(puts[0].body, { analysis_id: 'example_step_x', measure: 'rows_sampled', choice: 'publish' });
  const r = rowEl(document, 'example_step_x/rows_sampled');
  assert.equal(r.querySelector('[data-publish-choice="publish"]').getAttribute('aria-pressed'), 'true');
  assert.equal(flat(r.querySelector('[data-choice-line]')), 'will publish · me · 2026-10-27 10:15');
  assert.equal(flat(document.querySelector('[data-publish-table-chosen]')), '1');
  // Saved, not sent: the published word is still the proof row's.
  assert.equal(r.querySelector('[data-published]').dataset.published, 'local');
  // And it can be changed back: the redrawn row is bound again.
  pt.answer = (body) => row({ choice: { choice: body.choice, by: 'me', at: '2026-10-27T10:16:00' } });
  r.querySelector('[data-publish-choice="keep"]').click();
  await wait(100);
  assert.equal(flat(rowEl(document, 'example_step_x/rows_sampled').querySelector('[data-choice-line]')), 'kept in RE · me · 2026-10-27 10:16');
  assert.equal(flat(document.querySelector('[data-publish-table-chosen]')), '0');
});

test('a measure already in Egeria another way names that route as its state and offers Keep in RE only', async () => {
  const words = "already in Egeria via Resource Explorer's survey report (the commit on Contents)";
  const t = TABLE();
  t.rows[0] = row({ publishable: false, elsewhere: { route: 'survey_report', words },
    why_not: `${words}; publishing it here would write the same number twice`,
    published: { state: 'published', words: 'published · read back 2026-10-27 09:01' } });
  const { document } = await open({ table: t });
  const r = rowEl(document, 'example_step_x/rows_sampled');
  const st = r.querySelector('[data-published]');
  assert.equal(st.dataset.published, 'elsewhere');
  assert.equal(st.dataset.elsewhere, 'survey_report');
  assert.ok(flat(st).includes(words));
  assert.ok(!flat(st).includes('read back'), 'the route wins over an earlier proof row of this table');
  assert.equal(r.querySelector('[data-publish-choice="publish"]').disabled, true);
  assert.equal(r.querySelector('[data-publish-choice="keep"]').disabled, false);
  assert.ok(flat(r.querySelector('[data-why-not]')).endsWith('would write the same number twice'));
});

test('a Publish choice says it stands: sent by every commit until Keep in RE', async () => {
  const t = TABLE();
  t.rows[0] = row({ choice: { choice: 'publish', by: 'me', at: '2026-10-27T10:15:00', standing: 'sent by every commit until Keep in RE' } });
  t.chosen = 1;
  const { document } = await open({ table: t });
  const line = rowEl(document, 'example_step_x/rows_sampled').querySelector('[data-choice-line]');
  assert.equal(flat(line), 'will publish · me · 2026-10-27 10:15 · sent by every commit until Keep in RE');
  assert.ok(line.querySelector('[data-choice-standing]'));
});

test('a refused choice stays under its row and changes nothing else', async () => {
  const pt = { table: TABLE() };
  pt.answer = () => ({ ok: false, status: 422, statusText: 'refused', json: async () => ({ detail: 'Example Step X · rows_sampled cannot publish: no as-of' }) });
  const { document } = await open(pt);
  const r = rowEl(document, 'example_step_x/rows_sampled');
  r.querySelector('[data-publish-choice="publish"]').click();
  await wait(100);
  assert.ok(flat(r.querySelector('[data-choice-feedback]')).startsWith('not saved · '));
  assert.ok(flat(r.querySelector('[data-choice-feedback]')).includes('cannot publish: no as-of'));
  assert.equal(flat(r.querySelector('[data-choice-line]')), 'not chosen · stays in RE');
  assert.equal(flat(document.querySelector('[data-publish-table-chosen]')), '0');
});

test('nobody signed in: neither half can be pressed, and the table says why', async () => {
  const { document } = await open({ table: TABLE() }, { signedIn: false });
  for (const b of document.querySelectorAll('[data-publish-choice]')) assert.equal(b.disabled, true);
  assert.ok(flat(band(document)).includes('sign in to choose'));
});

test('a table that cannot be read says so, and a resource with no declared measure says that', async () => {
  let env = await open({ table: TABLE(), fail: true });
  assert.ok(env.document.querySelector('[data-publish-table-state="not_read"]'));
  assert.ok(flat(band(env.document)).includes('the declared measures could not be read'));
  env = await open({ table: { ...TABLE(), rows: [] } });
  assert.ok(env.document.querySelector('[data-publish-table-state="empty"]'));
});

test("a zero-fetch fact's as-of reaches the answer line: its read snapshot, never 'run time not recorded'", async () => {
  const { readEnvelope } = await import('/static/next/envelope.js');
  const esc = (s) => String(s);
  const fact = { analysis_id: 'schema_diff', state: 'measured', is_known: true, headline: '2 columns added.', value: {},
    last_run_at: '', as_of: '2026-10-20T07:00:00', can_run: [] };
  const lines = readEnvelope({}, { facts: [fact], answerable: true }, esc, esc);
  assert.equal(lines.asOf, '2026-10-20T07:00:00');
  assert.equal(lines.lastRun, '');
  assert.equal(lines.runTimeUnrecorded, false);
  // A fact whose as-of is its run time adds nothing new: the run line already says it.
  const ran = readEnvelope({}, { facts: [{ ...fact, last_run_at: '2026-10-21T09:00:00', as_of: '2026-10-21T09:00:00' }] }, esc, esc);
  assert.equal(ran.asOf, '');
  assert.equal(ran.lastRun, '2026-10-21T09:00:00');
});

test("a file system's table names its own press, offers no Contents button, and titles Publish by the folder survey", async () => {
  await open({ table: TABLE() });                       // sets the page up; the module reads state from it
  const { publishTableHtml } = await import('/static/next/stages/publish-table.js');
  const fsTable = { ...TABLE(), entity_type: 'filesystem', slug: 'loading_bay', chosen: 1,
    press: "chosen measures go inside Resource Explorer's own file-system survey report: the folder survey's Publish sends them, each read back by name",
    rows: [row({ analysis_id: 'fs_permissions', analysis: 'File permissions', measure: 'fs_permissions', locator: 'fs_permissions/fs_permissions' }),
      row({ analysis_id: 'fs_activity', analysis: 'File activity', measure: 'fs_access_activity', locator: 'fs_activity/fs_access_activity',
        value: null, value_words: '—', as_of: '', absence: 'not_measured', absence_words: 'Not measured: access times were not recorded on this walk.',
        publishable: false, why_not: 'not measured this run · Not measured: access times were not recorded on this walk.' })] };
  const html = publishTableHtml(fsTable, true, 'filesystem');
  assert.ok(html.includes('file-system survey report'));
  assert.ok(!html.includes('data-publish-table-goto'), 'a file system has no Contents tab');
  assert.match(html, /Publish fs_permissions with the folder survey(&#39;|&#039;|&apos;|')s Publish/);
  const absent = html.slice(html.indexOf('fs_activity/fs_access_activity'));
  assert.ok(absent.includes('not measured this run'));
  assert.ok(/data-publish-choice="publish"[^>]*disabled/.test(absent), 'no Publish on a measure that was not established');
});

test("the question row never says 'the figures above' for an analysis whose figures are not above (F3)", async () => {
  await open({ table: TABLE() });
  const { readEnvelope } = await import('/static/next/envelope.js');
  const esc = (s) => String(s);
  const fact = (id, over = {}) => ({ analysis_id: id, state: 'measured', is_known: true, value: { Owners: 3, Groups: 2 }, last_run_at: '2026-10-29T10:00:00', ...over });
  // fs_permissions is not the lead: the lead has a headline, so fs_permissions' figures are not on the row
  const lines = readEnvelope({ analysis_ids: ['fs_lead', 'fs_permissions'] },
    { facts: [fact('fs_permissions'), fact('fs_lead', { headline: 'Three owners.' })], answerable: true }, esc, esc);
  assert.ok(!/figures above/.test(lines.caveat), lines.caveat);
  assert.match(lines.caveat, /fs_permissions has no written summary and is not shown here — see Survey & analyses/);
  // alone, its raw measures ARE the answer line, and the caveat may say so
  const alone = readEnvelope({}, { facts: [fact('fs_permissions')], answerable: true }, esc, esc);
  assert.match(alone.caveat, /fs_permissions has no written summary — the figures above are the raw measures/);
});

test("a library step whose value is nested measures shows no figures, and the row says where they are (F3)", async () => {
  await open({ table: TABLE() });
  const { readEnvelope } = await import('/static/next/envelope.js');
  const esc = (s) => String(s);
  const nested = { analysis_id: 'fs_activity', state: 'measured', is_known: true, value: { bands: { hot: 3 } }, last_run_at: '2026-10-29T10:00:00' };
  const lines = readEnvelope({ analysis_ids: ['fs_activity'] }, { facts: [nested], answerable: true }, esc, esc);
  assert.equal(lines.answer, '');
  assert.ok(!/figures above/.test(lines.caveat), lines.caveat);
  assert.match(lines.caveat, /fs_activity has no written summary and is not shown here — see Survey & analyses/);
});

test("a derived row says what it was derived from, not a second identical date (F18)", async () => {
  await open({ table: TABLE() });
  const { rowHtml } = await import('/static/next/stages/publish-table.js');
  const base = { locator: 'a/b', analysis: 'A', measure: 'm', unit: 'u', as_of: '2026-10-20T07:00:00', measured_at: '2026-10-20T07:00:00',
    confidence: { words: '' }, writes_as: { words: 'x', route: 'generic' }, published: null };
  const derived = rowHtml({ ...base, derived: true }, false);
  assert.match(derived, /derived on read from the survey of /);
  assert.ok(!derived.includes('data-measured-at'));
  assert.match(rowHtml({ ...base, derived: true, measured_at: '' }, false), /derived on read; the survey it read is not recorded/);
  assert.ok(rowHtml({ ...base, derived: false }, false).includes('data-measured-at'));
});

test("a row names both dates: when the statistic is as of, and when it was measured (F15)", async () => {
  await open({ table: TABLE() });
  const { rowHtml } = await import('/static/next/stages/publish-table.js');
  const html = rowHtml(row({ as_of: '2026-10-25T01:36:00', measured_at: '2026-10-29T10:00:00' }), true);
  assert.match(html, /2026-10-25 01:36/);
  assert.match(html, /data-as-of-label>as of</);
  assert.match(html, /data-measured-at>measured 2026-10-29 10:00</);
  assert.ok(!/data-measured-at/.test(rowHtml(row({ measured_at: '' }), true)));
});
