/** Behavior: item C3 (2026-11-19), a file system's Publish says what it does, re-publishes, and Curate reviews proposals.
 *
 *  Real fs-survey.js, publish.js, publish-table.js, curate-bands.js and re-api.js against a jsdom DOM; only fetch is stubbed.
 *
 *  - one name per action: Curate's root-folder button is "Attach root folder" ("Root folder attached · confirm" once
 *    attached) and calls the attach endpoint and nothing else;
 *  - "Publish chosen measures" under the table and the Survey card's Publish are ONE function (publishChosenMeasures):
 *    the same endpoint for the same lead, whichever button is pressed;
 *  - the Survey card's Publish comes back while a chosen measure is not on the report yet, in the same published line
 *    (no new line, no list), and "already published · nothing new to send" is shown as the press's answer;
 *  - the proposals band is ONE line while collapsed; lists open on demand, 20 at a time; Confirm / Reject save a signed
 *    verdict and redraw from the server's answer; a verdict whose proposal vanished reads "no longer proposed".
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();
const tick = (ms = 30) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const ok = (b) => ({ ok: true, status: 200, json: async () => b });

async function setUp(fetchImpl, me = { user_id: 'dan' }) {
  const { document, window } = makeDomEnvironment();
  globalThis.fetch = fetchImpl || (async () => ok({}));
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { selectedSlug: 'loading_bay', resourceType: 'filesystem', me, groups: [], projects: [], databases: [],
    filesystems: [{ slug: 'loading_bay', display_name: 'Loading bay' }], filesystemsLoaded: true });
  const host = document.createElement('div');
  document.body.appendChild(host);
  return { document, host, app };
}

const EGERIA = { state: 'egeria', surveyor: "Egeria's Folder Survey", checked_date: '2026-10-25', sentence: 'Egeria can read it' };
const RE = { state: 're_only', surveyor: 'Resource Explorer', checked_date: '2026-10-25', sentence: 'Resource Explorer can read it' };

const EGERIA_VIEW = (over = {}, ext = {}) => ({
  state: 'measured', reach: EGERIA, leads: EGERIA.surveyor, surveyor: "Egeria's Folder Survey", surveyed_at: '2026-11-19T01:20:55',
  report_guid: 'r-egeria', published: true, publish: null, pre_reset: null, report_sentence: '', detail_lines: [], limits: null,
  egeria_asset: true, egeria_run_state: 'complete', egeria_run_since: '', results: { measured: true, counts: { files: 10 }, times: {}, file_types: [], extensions: [], inaccessible: { count: 0, listed: [] } },
  price: { priced: false, words: '' }, measure_price: { words: '' }, file_profiles: null, narrative_docs: null, class_match: null, derived: null,
  extensions: { run_id: 'x', surveyed_at: '2026-11-19T01:32:00', led_by: 'egeria', capped_words: '', report_sentence: '', folders: {}, owners: {}, modes: {},
    hashing: {}, narratives: [], narratives_not_shown: 0, not_walked_by_name: {}, rows: { kept: true, files: 10, runs_kept: 1, error: '' },
    report_guid: 'r-ext', published: true, publish: { published: true, landed_words: '12 of 12 annotations in Egeria' }, measures_pending: 0, ...ext },
  ...over,
});
const RE_VIEW = (over = {}) => EGERIA_VIEW({ reach: RE, leads: RE.surveyor, surveyor: 'Resource Explorer', report_guid: 'r-re', published: true,
  publish: { published: true, landed_words: '9 of 9 annotations in Egeria' }, extensions: null, ...over });

// ── one name per action ─────────────────────────────────────────────────────────────────────────────────────────

test("Curate's root-folder button is 'Attach root folder', then 'Root folder attached · confirm', and calls attach-root only", async () => {
  const calls = [];
  let root = { state: 'not_attached' };
  const { host } = await setUp(async (url, o = {}) => {
    const u = String(url); const m = (o.method || 'GET').toUpperCase();
    calls.push(`${m} ${u}`);
    if (m === 'GET' && /root-folder$/.test(u)) return ok(root);
    if (m === 'POST' && /attach-root$/.test(u)) { root = { state: 'attached', guid: 'g-1', read_back_at: '2026-11-19T10:00:00' }; return ok(root); }
    return ok([]);
  });
  const publish = await import('/static/next/stages/publish.js');
  await publish.renderPublishFilesystem(host, 'loading_bay');
  const b = host.querySelector('[data-fs-publish-go]');
  assert.equal(text(b), 'Attach root folder');
  b.click();
  await tick(80);
  assert.equal(text(host.querySelector('[data-fs-publish-go]')), 'Root folder attached · confirm');
  const writes = calls.filter((c) => !c.startsWith('GET'));
  assert.deepEqual(writes, ['POST /api/filesystems/loading_bay/attach-root']);
});

const TABLE = (chosen) => ({ entity_type: 'filesystem', slug: 'loading_bay', chosen, press: 'chosen measures go inside the survey report',
  rows: [{ analysis_id: 'fs_permissions', analysis: 'Permissions', measure: 'fs_permissions', locator: 'fs_permissions/fs_permissions',
    unit: 'files', value: 3, value_words: '3 files', as_of: '2026-11-19T01:00:00', confidence: { words: 'measured' }, scope: 'resource',
    writes_as: { route: 'native', type: 'ResourceProfileAnnotation', words: 'ResourceProfileAnnotation', writable: true }, publishable: true, why_not: '',
    choice: chosen ? { choice: 'publish', by: 'dan', at: '2026-11-19T01:00:00', standing: 'sent by Publish chosen measures until Keep in RE' } : null,
    published: { state: 'local', words: 'local · not published' } }] });

async function tableWith({ reach, chosen = 1, answer }) {
  const calls = [];
  const env = await setUp(async (url, o = {}) => {
    const u = String(url); const m = (o.method || 'GET').toUpperCase();
    calls.push(`${m} ${u}`);
    if (u.includes('/api/publish-table/')) return ok(TABLE(chosen));
    if (/\/reach$/.test(u)) return ok(reach);
    if (m === 'POST') return ok(answer || { published: true, landed_words: '12 of 12 annotations in Egeria', report_guid: 'r-1' });
    return ok({});
  });
  const { renderPublishTable } = await import('/static/next/stages/publish-table.js');
  await renderPublishTable(env.host, 'loading_bay', 'filesystem');
  return { ...env, calls };
}

test("'Publish chosen measures' sits under the table, off until something is chosen, and says why", async () => {
  const none = await tableWith({ reach: EGERIA, chosen: 0 });
  const b0 = none.host.querySelector('[data-publish-chosen]');
  assert.equal(text(b0), 'Publish chosen measures');
  assert.equal(b0.disabled, true);
  assert.match(b0.title, /choose a measure to Publish first/);
  const some = await tableWith({ reach: EGERIA, chosen: 1 });
  assert.equal(some.host.querySelector('[data-publish-chosen]').disabled, false);
  assert.equal(some.host.querySelector('[data-publish-chosen-state]').textContent, '', 'no extra line until it is pressed');
  assert.match(text(some.host), /sent by Publish chosen measures until Keep in RE/);
});

test('the Curate button and the Survey card press the same publish for the same lead', async () => {
  const lead = { Egeria: [EGERIA, /walk-extensions\/publish$/, EGERIA_VIEW], Re: [RE, /folder-survey\/publish$/, RE_VIEW] };
  for (const [name, [reach, endpoint, view]] of Object.entries(lead)) {
    // Curate's button
    const t = await tableWith({ reach });
    t.host.querySelector('[data-publish-chosen]').click();
    await tick(80);
    const curatePosts = t.calls.filter((c) => c.startsWith('POST'));
    assert.equal(curatePosts.length, 1, name);
    assert.match(curatePosts[0], endpoint, `${name}: Curate`);
    assert.match(text(t.host.querySelector('[data-publish-chosen-state]')), /^published · 12 of 12 annotations in Egeria$/);
    // the Survey card's button
    const cardCalls = [];
    const s = await setUp(async (url, o = {}) => {
      const u = String(url); const m = (o.method || 'GET').toUpperCase();
      cardCalls.push(`${m} ${u}`);
      if (m === 'GET' && /folder-survey$/.test(u)) return ok(view({ published: false, publish: null }, { published: false, publish: null }));
      return ok({ published: true });
    });
    const fs = await import('/static/next/fs-survey.js');
    await fs.mountFolderSurvey(s.host, 'loading_bay');
    const act = name === 'Egeria' ? 'publish-measures' : 'publish';
    s.host.querySelector(`[data-fs-act="${act}"]`).click();
    await tick(80);
    const cardPosts = cardCalls.filter((c) => c.startsWith('POST'));
    assert.equal(cardPosts.length, 1, name);
    assert.match(cardPosts[0], endpoint, `${name}: the card`);
    assert.equal(cardPosts[0], curatePosts[0], `${name}: the same request`);
  }
});

test("an answer of 'nothing new' is the press's own words; a refusal and a failed lookup say 'not published'", async () => {
  const { renderPublishTable, publishOutcomeWords } = await import('/static/next/stages/publish-table.js');
  assert.equal(publishOutcomeWords({ nothing_new: true, words: 'already published · nothing new to send', published: true }),
    'already published · nothing new to send');
  assert.equal(publishOutcomeWords({ published: false, publish_refused: 'not published · x' }), 'not published · not published · x');
  assert.match(publishOutcomeWords({ published: false, not_published_why: 'no annotation landed in Egeria' }), /^not published · no annotation/);
  const t = await tableWith({ reach: EGERIA, answer: { published: true, nothing_new: true, words: 'already published · nothing new to send', report_guid: 'r-1' } });
  t.host.querySelector('[data-publish-chosen]').click();
  await tick(80);
  assert.equal(text(t.host.querySelector('[data-publish-chosen-state]')), 'already published · nothing new to send');
  assert.ok(renderPublishTable);
});

// ── the Survey card: the Publish returns while a chosen measure is not on the report ───────────────────────────────

test('the Survey card offers Publish again, in the same published line, while chosen measures are not on the report', async () => {
  const fs = await import('/static/next/fs-survey.js');
  const { host } = await setUp();
  for (const [name, view, act, line] of [
    ['egeria-led', EGERIA_VIEW({}, { measures_pending: 2 }), 'publish-measures', '[data-fs-measures-published]'],
    ['re-led', RE_VIEW({ measures_pending: 1 }), 'publish', '[data-fs-published]'],
  ]) {
    host.innerHTML = fs.folderSurveyCardHtml(view);
    const b = host.querySelector(`[data-fs-act="${act}"]`);
    assert.ok(b, `${name}: the Publish is back`);
    assert.equal(text(b), 'Publish these measures to Egeria');
    assert.match(text(host.querySelector(line)), / · \d chosen measures? not on the report yet$/);
    assert.equal(host.querySelectorAll(line).length, 1, 'one line, not a list');
    const clean = name === 'egeria-led' ? EGERIA_VIEW({}, { measures_pending: 0 }) : RE_VIEW({ measures_pending: 0 });
    host.innerHTML = fs.folderSurveyCardHtml(clean);
    assert.equal(host.querySelector(`[data-fs-act="${act}"]`), null, `${name}: nothing pending, published: no button`);
    assert.doesNotMatch(text(host.querySelector(line)), /not on the report/);
  }
});

test('the card shows "already published · nothing new to send" after a press that found nothing new', async () => {
  let views = 0;
  const { host } = await setUp(async (url, o = {}) => {
    const u = String(url); const m = (o.method || 'GET').toUpperCase();
    if (m === 'GET' && /folder-survey$/.test(u)) return ok(EGERIA_VIEW({}, { measures_pending: views++ ? 0 : 1 }));
    if (m === 'POST' && /walk-extensions\/publish$/.test(u)) return ok({ published: true, nothing_new: true, words: 'already published · nothing new to send' });
    return ok({});
  });
  const fs = await import('/static/next/fs-survey.js');
  await fs.mountFolderSurvey(host, 'loading_bay');
  host.querySelector('[data-fs-act="publish-measures"]').click();
  await tick(80);
  assert.equal(text(host.querySelector('[data-fs-measures-published]')), 'already published · nothing new to send');
  assert.equal(host.querySelector('[data-fs-act="publish-measures"]'), null, 'nothing pending now: the button is gone');
});

// ── the proposals band ──────────────────────────────────────────────────────────────────────────────────────────

const proposals = (n, over = {}) => Array.from({ length: n }, (_, i) => ({ key: `d${i}/README.md`, file: `d${i}/README.md`, folder: `d${i}`,
  title: `Folder ${i}`, description: `About folder ${i}.`, listed: true, verdict: null, ...over }));
const REVIEW = (over = {}) => {
  const folders = { kind: 'fs_folder_description', run: 'r1', items: proposals(45), gone: [], not_kept: 0, proposed: 45, confirmed: 3, rejected: 0, to_review: 42 };
  const classes = { kind: 'fs_data_class', run: 'm1', items: [], gone: [], not_kept: 0, proposed: 0, confirmed: 0, rejected: 0, to_review: 0 };
  return { folders, classes, to_review: 45, confirmed: 3, proposed: 45, stays: 'Confirmed descriptions stay in Resource Explorer; confirmed data classes go to Egeria only when you press Send.', ...over };
};

/** The band's visible lines: leaf blocks, a closed <details> counting only its summary. */
function visibleLines(host) {
  const root = host.cloneNode(true);
  root.querySelectorAll('details:not([open])').forEach((d) => [...d.children].filter((c) => c.tagName !== 'SUMMARY').forEach((c) => c.remove()));
  return [...root.querySelectorAll('div, summary, li')].filter((e) => !e.querySelector('div, summary, li, details') && text(e) !== '').map((e) => text(e));
}

async function reviewBand(review, { onVerdict } = {}) {
  const calls = [];
  const env = await setUp(async (url, o = {}) => {
    const u = String(url); const m = (o.method || 'GET').toUpperCase();
    const body = o.body ? JSON.parse(o.body) : null;
    calls.push({ m, u, body });
    if (m === 'GET' && /\/review$/.test(u)) return ok(review);
    if (m === 'PUT' && /\/review\/verdict$/.test(u)) return ok(onVerdict(body));
    return ok({});
  });
  const bands = await import('/static/next/stages/curate-bands.js');
  await bands.renderFilesystemReview(env.host, 'loading_bay');
  return { ...env, calls, bands };
}

test('the collapsed band is one line, "N proposals to review · M confirmed"', async () => {
  const { host } = await reviewBand(REVIEW());
  assert.deepEqual(visibleLines(host), ['45 proposals to review · 3 confirmed']);
  const one = REVIEW();
  one.to_review = 1; one.confirmed = 3;
  const { host: h2 } = await reviewBand(one);
  assert.deepEqual(visibleLines(h2), ['1 proposal to review · 3 confirmed']);
});

test('with nothing proposed and no verdict held, the band keeps its old sentence', async () => {
  const empty = { folders: { items: [], gone: [] }, classes: { items: [], gone: [] }, to_review: 0, confirmed: 0 };
  const { host } = await reviewBand(empty);
  assert.match(text(host), /^Nothing to review for file systems yet\./);
});

test('open, a list shows 20 and pages by "more"; a data-class match names the file, column and class', async () => {
  const r = REVIEW();
  r.classes.items = [{ key: 'a.csv::email::DataClass::Email', file: 'a.csv', column: 'email', data_class: 'Email Address', privacy_relevant: true, listed: true, verdict: null }];
  r.classes.proposed = 1; r.classes.to_review = 1; r.to_review = 46;
  const { host } = await reviewBand(r);
  const details = host.querySelector('[data-fs-review-details]');
  details.open = true;
  const list = host.querySelector('[data-review-list="folders"]');
  assert.equal(list.querySelectorAll('[data-review-item]').length, 20);
  const more = host.querySelector('[data-review-more="folders"]');
  assert.equal(text(more), 'more (25)');
  more.click();
  assert.equal(host.querySelector('[data-review-list="folders"]').querySelectorAll('[data-review-item]').length, 40);
  host.querySelector('[data-review-more="folders"]').click();
  assert.equal(host.querySelector('[data-review-list="folders"]').querySelectorAll('[data-review-item]').length, 45);
  assert.equal(host.querySelector('[data-review-more="folders"]'), null);
  assert.match(text(host.querySelector('[data-review-list="classes"]')), /a\.csv › email matches Email Address · privacy-relevant/);
  const labels = [...host.querySelector('[data-review-item]').querySelectorAll('button')].map(text);
  assert.deepEqual(labels, ['Confirm', 'Reject']);
  assert.match(text(host.querySelector('[data-fs-review-stays]')), /stay in Resource Explorer; confirmed data classes go to Egeria only when you press Send\./);
});

test('Confirm saves one signed verdict and the band redraws from the server answer, count and all', async () => {
  const r = REVIEW();
  const { host, calls } = await reviewBand(r, { onVerdict: (body) => {
    assert.deepEqual(body, { kind: 'fs_folder_description', key: 'd0/README.md', verdict: 'confirm' });
    const items = proposals(45); items[0].verdict = { verdict: 'confirm', by: 'dan', at: '2026-11-19T12:00:00', run: 'r1' };
    return { kind: 'fs_folder_description', items, gone: [], not_kept: 0, proposed: 45, confirmed: 1, rejected: 0, to_review: 44 };
  } });
  host.querySelector('[data-fs-review-details]').open = true;
  host.querySelector('[data-review-item="d0/README.md"] [data-review-verdict="confirm"]').click();
  await tick(60);
  assert.equal(calls.filter((c) => c.m === 'PUT').length, 1);
  assert.equal(text(host.querySelector('[data-fs-review-line]')), '44 proposals to review · 1 confirmed');
  const row = host.querySelector('[data-review-item="d0/README.md"]');
  assert.match(text(row.querySelector('[data-review-verdict-words]')), /^confirmed by dan 2026-11-19$/);
  assert.equal(row.querySelector('[data-review-verdict="confirm"]').getAttribute('aria-pressed'), 'true');
  assert.equal(host.querySelector('[data-fs-review-details]').open, true, 'stays open after a verdict');
});

test('a verdict whose proposal is no longer proposed is listed as such, never dropped', async () => {
  const r = REVIEW();
  r.folders.gone = [{ key: 'old/NOTES.md', file: 'old/NOTES.md', folder: 'old', listed: false, words: 'no longer proposed',
    verdict: { verdict: 'reject', by: 'mandy', at: '2026-11-18T09:00:00', run: '2026-11-17T01:00:00' } }];
  const { host } = await reviewBand(r);
  host.querySelector('[data-fs-review-details]').open = true;
  const gone = host.querySelector('[data-review-gone="old/NOTES.md"]');
  assert.match(text(gone), /no longer proposed · rejected by mandy 2026-11-18 · proposed by the run of 2026-11-17/);
});

test('nobody signed in: Confirm and Reject are off, with the reason', async () => {
  const calls = [];
  const env = await setUp(async (url, o = {}) => { calls.push(String(url)); return ok(REVIEW()); }, null);
  const bands = await import('/static/next/stages/curate-bands.js');
  await bands.renderFilesystemReview(env.host, 'loading_bay');
  env.host.querySelector('[data-fs-review-details]').open = true;
  const b = env.host.querySelector('[data-review-verdict="confirm"]');
  assert.equal(b.disabled, true);
  assert.match(b.title, /sign in to give a verdict/);
});

test('the send button is absent when nothing is confirmed or user is signed out', async () => {
  // No confirmed items
  const r = REVIEW();
  r.confirmed = 0;
  const { host } = await reviewBand(r);
  assert.equal(host.querySelector('[data-fs-send-classes]'), null, 'no button when confirmed=0');

  // Signed out
  const calls = [];
  const env = await setUp(async (url, o = {}) => { calls.push(String(url)); return ok(r); }, null);
  const bands = await import('/static/next/stages/curate-bands.js');
  await bands.renderFilesystemReview(env.host, 'loading_bay');
  assert.equal(env.host.querySelector('[data-fs-send-classes]'), null, 'no button when signed out');

  // Confirmed > 0 and signed in: button present
  const r2 = REVIEW();
  r2.classes.confirmed = 1;
  const env2 = await setUp(async (url, o = {}) => { calls.push(String(url)); return ok(r2); });
  const bands2 = await import('/static/next/stages/curate-bands.js');
  await bands2.renderFilesystemReview(env2.host, 'loading_bay');
  const btn = env2.host.querySelector('[data-fs-send-classes]');
  assert.ok(btn, 'button present when confirmed > 0 and signed in');
  assert.match(text(btn), /Send confirmed data classes/);
});

test('R1: button shows only when confirmed classes > 0, not just confirmed folders', async () => {
  // Confirmed folders + 0 confirmed classes -> NO button
  const r1 = REVIEW();
  r1.folders.confirmed = 3;  // 3 confirmed folders
  r1.classes.confirmed = 0;  // 0 confirmed classes
  const { host } = await reviewBand(r1);
  assert.equal(host.querySelector('[data-fs-send-classes]'), null, 'no button when only folders confirmed');

  // 2 confirmed classes + 3 confirmed folders -> button title says "2" (classes only)
  const r2 = REVIEW();
  r2.folders.confirmed = 3;
  r2.classes.confirmed = 2;
  const env2 = await setUp(async (url, o = {}) => { return ok(r2); });
  const bands2 = await import('/static/next/stages/curate-bands.js');
  await bands2.renderFilesystemReview(env2.host, 'loading_bay');
  const btn = env2.host.querySelector('[data-fs-send-classes]');
  assert.ok(btn, 'button present when classes confirmed');
  assert.match(text(btn), /Send confirmed data classes/);
  // The title should say "2" (classes count), not "5" (folders + classes)
  assert.match(btn.title, /Sends the 2 confirmed data-class matches to Egeria/);
});

test('a press of the send button POSTs to the right URL once and shows sent N', async () => {
  const calls = [];
  const env = await setUp(async (url, o = {}) => {
    const u = String(url); const m = (o.method || 'GET').toUpperCase();
    calls.push({ m, u });
    if (m === 'GET' && /\/review$/.test(u)) return ok(REVIEW({ classes: { confirmed: 1 } }));
    if (m === 'POST' && /\/review\/publish-data-classes$/.test(u)) return ok({ sent: 1, added: 1, nothing_new: false, words: 'published 1 confirmed data-class matches' });
    return ok({});
  });
  const bands = await import('/static/next/stages/curate-bands.js');
  await bands.renderFilesystemReview(env.host, 'loading_bay');
  env.host.querySelector('[data-fs-review-details]').open = true;
  const btn = env.host.querySelector('[data-fs-send-classes]');
  assert.ok(btn);
  btn.click();
  await tick(60);
  const posts = calls.filter((c) => c.m === 'POST');
  assert.equal(posts.length, 1);
  assert.match(posts[0].u, /\/review\/publish-data-classes$/);
  const fb = env.host.querySelector('[data-fs-send-feedback]');
  assert.equal(text(fb), 'sent 1 confirmed data classes');
});

test('a press answered nothing_new shows "nothing new to send"', async () => {
  const calls = [];
  const env = await setUp(async (url, o = {}) => {
    const u = String(url); const m = (o.method || 'GET').toUpperCase();
    calls.push({ m, u });
    if (m === 'GET' && /\/review$/.test(u)) return ok(REVIEW({ classes: { confirmed: 1 } }));
    if (m === 'POST' && /\/review\/publish-data-classes$/.test(u)) return ok({ sent: 0, added: 0, nothing_new: true, words: 'already published · nothing new to send' });
    return ok({});
  });
  const bands = await import('/static/next/stages/curate-bands.js');
  await bands.renderFilesystemReview(env.host, 'loading_bay');
  env.host.querySelector('[data-fs-review-details]').open = true;
  const btn = env.host.querySelector('[data-fs-send-classes]');
  btn.click();
  await tick(60);
  const fb = env.host.querySelector('[data-fs-send-feedback]');
  assert.equal(text(fb), 'nothing new to send');
});

test('a press answered with an error shows "not sent · …"', async () => {
  const calls = [];
  const env = await setUp(async (url, o = {}) => {
    const u = String(url); const m = (o.method || 'GET').toUpperCase();
    calls.push({ m, u });
    if (m === 'GET' && /\/review$/.test(u)) return ok(REVIEW({ classes: { confirmed: 1 } }));
    if (m === 'POST' && /\/review\/publish-data-classes$/.test(u)) {
      return { status: 502, ok: false, json: async () => ({ detail: 'Egeria not reachable' }) };
    }
    return ok({});
  });
  const bands = await import('/static/next/stages/curate-bands.js');
  await bands.renderFilesystemReview(env.host, 'loading_bay');
  env.host.querySelector('[data-fs-review-details]').open = true;
  const btn = env.host.querySelector('[data-fs-send-classes]');
  btn.click();
  await tick(60);
  const fb = env.host.querySelector('[data-fs-send-feedback]');
  assert.match(text(fb), /not sent · Egeria not reachable/);
});

test('a second press while one is pending is ignored', async () => {
  const calls = [];
  let resolve;
  const promise = new Promise((r) => { resolve = r; });
  const env = await setUp(async (url, o = {}) => {
    const u = String(url); const m = (o.method || 'GET').toUpperCase();
    calls.push({ m, u });
    if (m === 'GET' && /\/review$/.test(u)) return ok(REVIEW({ classes: { confirmed: 1 } }));
    if (m === 'POST' && /\/review\/publish-data-classes$/.test(u)) return promise;
    return ok({});
  });
  const bands = await import('/static/next/stages/curate-bands.js');
  await bands.renderFilesystemReview(env.host, 'loading_bay');
  env.host.querySelector('[data-fs-review-details]').open = true;
  const btn = env.host.querySelector('[data-fs-send-classes]');
  btn.click();
  // Click again while pending
  btn.click();
  await tick(60);
  const posts = calls.filter((c) => c.m === 'POST');
  assert.equal(posts.length, 1, 'only one POST despite two clicks');
});
