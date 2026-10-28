/** Behaviour: PI-046, a finished run links to the Egeria report it published.
 *  Real app.js and publish.js (the "Egeria reports" component); only fetch is stubbed. Nothing is read
 *  from Egeria until the link is pressed; a report no longer in Egeria says so; a run that was not
 *  published says that, not an empty report. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const GUID = 'aaaa-1111';

async function setUp({ published = true, readFails = false, live = [{ guid: GUID, display_name: 'Report A', surveyed_at: '2026-10-01T00:00:00', annotation_count: 2 }] } = {}) {
  const { document, window } = makeDomEnvironment();
  const calls = [];
  const detail = JSON.stringify({ egeria_report_guid: published ? GUID : '', steps: [{ step: 'S::a', status: 'ok', answered_by: 'local' }] });
  globalThis.fetch = async (url) => {
    const u = String(url);
    calls.push(u);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.startsWith('/api/activity/?entity_slug=')) return ok([{ id: 'r1', operation: 'survey', ts: new Date().toISOString(), status: 'ok', summary: 'Done', detail }]);
    if (u === '/api/activity/r1/declared-vs-received') {
      return ok({ published, recorded: true, egeria_report_guid: published ? GUID : '', summary: { declared: 1, received: 1 }, types: [] });
    }
    if (u.endsWith('/egeria-surveys')) return readFails ? { ok: false, status: 502, statusText: 'bad', json: async () => ({ detail: 'platform down' }) } : ok(live);
    if (u.endsWith(`/egeria-surveys/${GUID}/annotations`)) return ok([{ guid: 'an1', annotation_type: 'LanguageAnnotation', summary: 'Python' }]);
    return ok({});
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  globalThis.history = window.history;
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const api = await import('/static/re-api.js');
  api.clearCache();
  const app = await import('/static/next/app.js');
  Object.assign(app.state, { resourceType: 'repo', selectedSlug: 'r1', projects: [{ slug: 'r1', display_name: 'R1', is_published: true }], me: { user_id: 'dan' } });
  await app.openRunsList('r1');
  const d = document.getElementById('wl-detail');
  d.querySelector('details').open = true;
  d.querySelector('details').dispatchEvent(new window.Event('toggle'));
  await tick();
  return { document, window, app, calls, d };
}

test('an opened run shows its report GUID and a link, and has read nothing from Egeria yet', async () => {
  const t = await setUp();
  const link = t.d.querySelector(`[data-run-report="${GUID}"]`);
  assert.ok(link);
  assert.equal(link.querySelector('[data-run-report-guid]').textContent, GUID);
  assert.ok(link.querySelector('[data-run-report-open]'));
  assert.ok(!t.calls.some((u) => u.endsWith('/egeria-surveys')), 'no Egeria read before the press');
});

test('pressing the link opens that report and its annotations in the Egeria reports component', async () => {
  const t = await setUp();
  t.d.querySelector('[data-run-report-open]').click();
  await tick(200);
  assert.ok(t.calls.some((u) => u.endsWith('/api/egeria/r1/egeria-surveys')));
  assert.ok(t.calls.some((u) => u.endsWith(`/egeria-surveys/${GUID}/annotations`)));
  assert.match(text(t.d.querySelector(`[data-report-annotations="${GUID}"]`)), /LanguageAnnotation/);
  assert.equal(t.d.querySelector('[data-run-report-status]').textContent, '');
});

test('a report that is no longer in Egeria says so', async () => {
  const t = await setUp({ live: [] });
  t.d.querySelector('[data-run-report-open]').click();
  await tick(200);
  assert.match(t.d.querySelector('[data-run-report-status]').textContent, /not in Egeria now/);
});

test('a run that was not published says so instead of offering a report', async () => {
  const t = await setUp({ published: false });
  assert.equal(t.d.querySelector('[data-run-report-open]'), null);
  assert.match(text(t.d.querySelector('[data-run-report="none"]')), /not published/);
});

test('an Egeria that cannot be read is not reported as the report being gone', async () => {
  const t = await setUp({ readFails: true });
  t.d.querySelector('[data-run-report-open]').click();
  await tick(200);
  assert.match(text(t.d.querySelector('[data-reports-error]')), /could not be read/);
  const status = t.d.querySelector('[data-run-report-status]').textContent;
  assert.match(status, /could not be read/);
  assert.doesNotMatch(status, /not in Egeria now/);
});
