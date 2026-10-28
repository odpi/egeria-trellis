/** What the single-page Understanding (a repository, a database) draws with the leading tabs off, as markup, for the
 *  byte-for-byte pin in understanding-tabs.test.mjs. The fixture (understanding-single.fixture.json) was generated
 *  by running THIS function against main's tree (1eace306, before the Understanding tabs). The stub server is fixed
 *  and time-free (no chart has a date, so nothing is "stale" by the clock). Run db first, then repo, in one process:
 *  the names-toggle ids are a per-process counter. */
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const wait = (ms = 400) => new Promise((r) => setTimeout(r, ms));
const RUN = { run_id: 7, surveyed_at: '2026-09-28T14:02:00', source: 'local', surveyed_as: 's' };
const env = (extra = {}) => ({ database: 'fixture_resource', run: RUN, state: 'measured', reasons: [], notes: [], scope: { checked: true, partial: false }, figure: null, ...extra });
const DIFF = { prev_date: '2026-09-21T10:00:00', curr_date: '2026-09-28T14:02:00', deltas: { schemas: 0, tables: 1, columns: 14 },
  new_tables: ['sales.a'], removed_tables: ['hr.old'], table_diff_state: 'measured', curr_table_state: 'measured', prev_table_state: 'measured' };
const FIG = { data: [{ type: 'bar', x: ['a', 'b'], y: [1, 2] }], layout: {} };

export async function snapshotUnderstanding(kind, { setKinds = true } = {}) {
  const { document, window } = makeDomEnvironment();
  globalThis.fetch = async (url) => {
    const u = String(url);
    const ok = (body) => ({ ok: true, status: 200, json: async () => body });
    const m = u.match(/\/api\/stats\/databases\/[^/]+\/([a-z_]+)/);
    if (m) return ok(env({ points: [{ run: RUN, schema_count: 1, table_count: 2, column_count: 3, states: {} }], runs: [RUN], series: [],
      tables: ['a.t'], row_counts: [5], sizes_mb: [1], schemas: ['a'], table_counts: [1], column_counts: [2], types: ['integer'], counts: [2],
      ranked_count: 1, truncated_by_limit: 0, table_count_total: 1, views_excluded: 0, not_established_count: 0, not_established_tables: [], figure: FIG }));
    if (/\/api\/databases\/[^/]+\/diff/.test(u)) return ok(DIFF);
    if (/\/api\/egeria\/[^/]+\/diff/.test(u)) return ok({ prev_date: '2026-10-03T09:00:00', curr_date: '2026-10-06T14:02:00', total_prev: 100, total_curr: 112, total_delta: 12, changes: [{ label: 'Python', prev: 60, curr: 72, delta: 12 }] });
    if (/\/api\/databases\/[^/]+\/surveys/.test(u)) return ok([]);
    if (/\/api\/databases\/[^/]+\/views/.test(u)) return ok({ state: 'measured', run: RUN, views: [] });
    if (/\/api\/stats\/[^/]+\/charts\//.test(u)) return ok({ data: [] });
    if (u.includes('/survey-report')) return ok({});
    if (u.includes('/questions')) return ok({ questions: [] });
    if (u.includes('/api/context/')) return ok({ enrichment: {}, question_answers: {} });
    if (u.includes('/investigations') || u.includes('/subscriptions') || u.includes('/schedules')) return ok([]);
    return ok({});
  };
  window.Plotly = { async newPlot(el) { el.drawn = true; }, purge() {}, async toImage() { return ''; } };
  const append = document.head.appendChild.bind(document.head);
  document.head.appendChild = (el) => { const r = append(el); if (el.tagName === 'SCRIPT') setTimeout(() => el.onload && el.onload(), 0); return r; };
  ensureLoaderRegistered();
  globalThis.location = window.location; globalThis.history = window.history;
  globalThis.getComputedStyle = window.getComputedStyle.bind(window);
  window.matchMedia = window.matchMedia || (() => ({ matches: true, addEventListener() {}, removeEventListener() {} }));
  const app = await import('/static/next/app.js');
  if (setKinds) (await import('/static/next/stages/stage-tabs.js')).setStageTabsKinds('understanding', []);
  Object.assign(app.state, { resourceType: kind, selectedSlug: 'fixture_resource', stage: 'scouting', subTab: 'questions',
    investigations: [], investigation: '', workListSlug: null, workListIndex: false, me: { user_id: 'dan' },
    groups: [], perspectives: [], allPerspectives: [], projects: [{ slug: 'fixture_resource', display_name: 'Fixture' }],
    databases: [{ slug: 'fixture_resource', display_name: 'Fixture' }], filesystems: [], databasesLoaded: true, filesystemsLoaded: true });
  for (const id of ['content', 'intent-nav', 'rail-evidence', 'perspective-row', 'app-grid']) {
    const d = document.createElement('div'); d.id = id; document.body.appendChild(d);
  }
  app.renderIntentNav();
  document.querySelector('#intent-nav button[data-stage="understanding"]').click();
  await wait(700);
  return document.getElementById('content').innerHTML;
}
