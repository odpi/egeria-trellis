/** Behaviour: PI-046, the post-run panel. A finished survey run lists its steps (a step that did not finish `ok` is
 *  an error cue with its reason, not a quiet row), says how many ran and how many did not, and carries the run's
 *  Egeria report GUID. Classic drew this after a run; the live data the 2026-10-21 rehearsal walked had no run to
 *  open, so the state is built here from a fixture. Real app.js (`openRunsList`); only fetch is stubbed.
 *  The report link itself is pinned in p2-run-report-link.test.mjs. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const tick = (ms = 60) => new Promise((r) => setTimeout(r, ms));
const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();
const GUID = 'bbbb-2222';

const STEPS = [
  { step: 'Survey::fetch_files', status: 'ok', answered_by: 'local' },
  { step: 'Survey::language_mix', status: 'ok', answered_by: 'local' },
  { step: 'Survey::secret_scan', status: 'error', detail: 'the scanner binary was not found on this host', answered_by: 'local' },
];

async function setUp({ steps = STEPS, withGuid = true } = {}) {
  const { document, window } = makeDomEnvironment();
  const detail = JSON.stringify({ egeria_report_guid: withGuid ? GUID : '', steps });
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (b) => ({ ok: true, status: 200, json: async () => b });
    if (u.startsWith('/api/activity/?entity_slug=')) {
      return ok([{ id: 'r1', operation: 'survey', ts: new Date().toISOString(), status: 'ok', summary: 'Survey run', detail }]);
    }
    if (u === '/api/activity/r1/declared-vs-received') {
      return ok({ published: withGuid, recorded: true, egeria_report_guid: withGuid ? GUID : '', summary: { declared: 3, received: 2 }, types: [] });
    }
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
  return { d };
}

test('a finished run lists every step, with the step that did not finish named and its reason given', async () => {
  const { d } = await setUp();
  const items = [...d.querySelectorAll('details ul li')];
  assert.equal(items.length, 3);
  assert.deepEqual(items.map((li) => li.querySelector('.font-mono').textContent.trim()), ['fetch_files', 'language_mix', 'secret_scan']);
  const failed = items[2];
  assert.match(text(failed), /scanner binary was not found/);
  assert.notEqual(failed.querySelector('[aria-label]').getAttribute('aria-label'), items[0].querySelector('[aria-label]').getAttribute('aria-label'),
    'a step that did not finish ok has its own cue, not the "ran" one');
});

test('the run says how many steps ran and how many did not', async () => {
  const { d } = await setUp();
  const line = text(d.querySelector('details > div.text-provenance'));
  assert.match(line, /2 ran/);
  assert.match(line, /1 did not/);
});

test('the same panel carries the run\'s report GUID', async () => {
  const { d } = await setUp();
  assert.equal(d.querySelector(`[data-run-report="${GUID}"] [data-run-report-guid]`).textContent, GUID);
});

test('a run that recorded no steps says so rather than listing nothing as done', async () => {
  const { d } = await setUp({ steps: [], withGuid: false });
  assert.equal(d.querySelectorAll('details ul li').length, 0);
  assert.match(text(d.querySelector('details')), /recorded no st/);
});
