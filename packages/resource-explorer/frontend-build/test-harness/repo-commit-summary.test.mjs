/** Brief section 2 (owner, 2026-10-07): the commit summary for repositories.
 *
 *  Pure rendering of the table and its panel: before the press the numbers equal the selection; after
 *  it the state column is made of the server's proof summary, with the proof rows' counts and not the
 *  request's; a press with nothing selected, no survey or no project is refused with the sentence
 *  directly under the button. Section 1's box (re-survey the stale steps first) is off by default.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

async function mod() {
  ensureLoaderRegistered();
  const { document } = makeDomEnvironment();
  const m = await import(`/static/next/stages/repo-manifest.js?t=${Math.random()}`);
  return { m, document };
}

const SCOPE = (m = {}) => ({ manifest: { files: 2, folders: 1, containers: 1, items: 4, not_selected: 2, proposals_not_accepted: 1, left_out: 3,
  published_earlier: 5, chosen: [], container_locators: [], ...m }, rows: [], proposals: [] });
const PLAN = {
  in_population: true,
  survey: { exists: true, surveyed_at: '2026-10-07T01:00:00', age_seconds: 7200, annotations: 42, steps: 12, stale_steps: 3, stale: ['a', 'b', 'c'] },
  project: { status: 'linked', word: 'project', name: 'P' },
  what_it_is: [
    { kind: 'SoftwareCapability::pyegeria', candidate: true }, { kind: 'Endpoint', candidate: true }, { kind: 'InfrastructureAsset', candidate: true },
    { kind: 'Component', candidate: false }],
  what_it_holds: [{ kind: 'SubResource', candidate: true, detail: {} }],
  made_of: [{ detail: { blueprints_accepted: 2, blueprint_kinds: ['Deployment Blueprint'] } }],
};
// The counts of files, folders and containers are the selection RECORD's (`scope.manifest`, built by the server),
// never derived from the page; the plan only supplies the confirmed lines, the blueprints and the survey.
const sel = (over = {}) => ({ plan: PLAN, picks: new Set(['SoftwareCapability::pyegeria', 'Endpoint']),
  scope: SCOPE(), fileTypePicks: new Set(['Python']), ...over });
const cell = (box, id, col) => box.querySelector(`[data-manifest-row="${id}"]`).querySelectorAll('[role=cell]')[col].textContent.replace(/\s+/g, ' ').trim();

test('before the press the numbers equal the selection record', async () => {
  const { m, document } = await mod();
  const box = document.createElement('div');
  box.innerHTML = m.repoManifestHtml(sel());
  assert.equal(cell(box, 'report', 2), '1 report · 42 annotations');
  assert.match(cell(box, 'report', 3), /survey of 2026-10-07 \(.*ago\)/);
  assert.equal(cell(box, 'entities', 2), '2 entities · 1 proposed, not ticked');
  assert.equal(cell(box, 'files', 2), '2 DataFiles');
  assert.equal(cell(box, 'files', 3), 'your selection');
  assert.equal(cell(box, 'folders', 2), '1 FileFolder');
  assert.equal(cell(box, 'containers', 1), 'folders needed as containers');
  assert.equal(cell(box, 'containers', 2), '1 FileFolder');
  assert.equal(cell(box, 'containers', 3), 'the files above');
  assert.equal(box.querySelector('[data-manifest-row="file_types"]'), null, 'the DataSet-per-type row is retired');
  assert.equal(cell(box, 'blueprints', 2), '2 (Deployment Blueprint)');
  assert.equal(cell(box, 'left_out', 0), 'Left out');
  assert.equal(cell(box, 'left_out', 2), '2 not selected · 1 proposal not accepted · 3 left out');
  assert.equal(cell(box, 'published_earlier', 0), 'Published earlier');
  assert.equal(cell(box, 'published_earlier', 1), 'kept in Egeria');
  assert.equal(cell(box, 'published_earlier', 2), '5');
  assert.equal(cell(box, 'published_earlier', 3), 'previous publishes');
  assert.equal(box.querySelector('[data-manifest-state]'), null, 'no state column before a press');
});

test('the counts follow the record and nothing else; with no record read they say so', async () => {
  const { m } = await mod();
  const c = m.manifestCounts(sel({ scope: SCOPE({ files: 0, folders: 0, containers: 0 }), picks: new Set() }));
  assert.deepEqual([c.entities, c.files, c.folders, c.containers, c.n, c.items], [0, 0, 0, 0, 0, 0]);
  const d = m.manifestCounts(sel({ scope: SCOPE({ files: 1, folders: 0, containers: 2 }) }));
  assert.deepEqual([d.files, d.folders, d.containers, d.n, d.items], [1, 0, 2, 3, 5]);
  const u = m.manifestCounts(sel({ scope: null }));
  assert.equal(u.unread, true);
  assert.deepEqual([u.files, u.n], [0, 0]);
  assert.equal(m.publishLabel(d), 'Publish 3 items →');
  assert.equal(m.publishLabel({ n: 1 }), 'Publish 1 item →');
  assert.equal(m.publishLabel({ n: 0 }), 'Publish →');
});

test('after the press each row gets its own state from the proof rows, not the request', async () => {
  const { m, document } = await mod();
  const ps = {
    report: { state: 'published', read_at: new Date().toISOString(), annotation_count: 42, reused: true },
    sub_resources: { read_back: 3, sent: 1, failed: 1, first_failure: 'OMAG-404 element missing.',
      by_row: { files: { read_back: 2, sent: 0, failed: 1, first_failure: 'OMAG-404 element missing.' },
                folders: { read_back: 0, sent: 1, failed: 0, first_failure: '' },
                containers: { read_back: 1, sent: 0, failed: 0, first_failure: '' } } },
    file_types: { read_back: 0 },
  };
  const box = document.createElement('div');
  box.innerHTML = m.repoManifestHtml(sel({ ps }));
  const state = (id) => box.querySelector(`[data-manifest-state="${id}"]`).textContent.replace(/\s+/g, ' ').trim();
  assert.match(state('report'), /published · read back just now · reused the report already in Egeria/);
  assert.equal(box.querySelector('[data-manifest-state="report"] [data-cue]').dataset.cue, 'measured');
  assert.match(state('files'), /2 read back/);
  assert.match(state('files'), /1 not created · OMAG-404 element missing\./);
  assert.match(state('folders'), /1 sent, not yet read back/);
  assert.match(state('containers'), /1 read back/);
  assert.equal(box.querySelector('[data-manifest-state="files"] [data-cue="error"]') !== null, true);
  assert.equal(box.querySelector('[data-manifest-state="folders"] [data-cue="running"]') !== null, true);
});

test('sent, and not published, read as themselves', async () => {
  const { m, document } = await mod();
  const box = document.createElement('div');
  box.innerHTML = m.repoManifestHtml(sel({ ps: { report: { state: 'sent' }, sub_resources: {}, file_types: {} } }));
  assert.match(box.querySelector('[data-manifest-state="report"]').textContent, /sent · waiting for Egeria/);
  assert.equal(box.querySelector('[data-manifest-state="report"] [data-cue]').dataset.cue, 'running');
  box.innerHTML = m.repoManifestHtml(sel({ ps: { report: { state: 'not published', first: 'OMAG-409-001 name taken.', rest: 'More.' }, sub_resources: {}, file_types: {} } }));
  assert.match(box.querySelector('[data-manifest-state="report"]').textContent, /not published · OMAG-409-001 name taken\./);
  assert.equal(box.querySelector('[data-manifest-state="report"] [data-cue]').dataset.cue, 'error');
});

test('the button names the items it sends; blockers are directly under it', async () => {
  const { m, document } = await mod();
  const box = document.createElement('div');
  box.innerHTML = m.repoCommitPanelHtml({ ...sel(), me: 'dan', resurvey: false });
  assert.equal(box.querySelector('[data-curate-go]').textContent.trim(), 'Publish 4 items →', 'files + chosen folders + containers');
  assert.equal(box.querySelector('[data-curate-go]').disabled, false);
  assert.equal(box.querySelectorAll('[data-commit-blocker]').length, 0);

  box.innerHTML = m.repoCommitPanelHtml({ ...sel({ picks: new Set(), scope: SCOPE({ files: 0, folders: 0, containers: 0 }) }), me: 'dan', resurvey: false });
  assert.equal(box.querySelector('[data-curate-go]').disabled, true);
  assert.equal(box.querySelector('[data-commit-blocker="nothing"]').textContent.trim(), '⚠ nothing selected · confirm a line under what it is, or choose folders and files');

  // only confirmed lines: still a press (it publishes the repository and its report), labelled without a count
  box.innerHTML = m.repoCommitPanelHtml({ ...sel({ scope: SCOPE({ files: 0, folders: 0, containers: 0 }) }), me: 'dan', resurvey: false });
  assert.equal(box.querySelector('[data-curate-go]').disabled, false);
  assert.equal(box.querySelector('[data-curate-go]').textContent.trim(), 'Publish →');

  box.innerHTML = m.repoCommitPanelHtml({ ...sel({ scope: null }), me: 'dan', resurvey: false });
  assert.equal(box.querySelector('[data-curate-go]').disabled, true);
  assert.match(box.querySelector('[data-commit-blocker="scope_unread"]').textContent, /the selection could not be read/);

  const noSurvey = { ...PLAN, survey: { exists: false, sentence: 'no survey to publish yet · run the first survey' } };
  box.innerHTML = m.repoCommitPanelHtml({ ...sel({ plan: noSurvey }), me: 'dan', resurvey: false });
  assert.equal(box.querySelector('[data-curate-go]').disabled, true);
  assert.match(box.querySelector('[data-commit-blocker="no_survey"]').textContent, /no survey to publish yet · run the first survey/);

  const before = { ...PLAN, survey: { exists: false, surveyed_before_kept: true } };
  box.innerHTML = m.repoCommitPanelHtml({ ...sel({ plan: before }), me: 'dan', resurvey: false });
  assert.equal(box.querySelector('[data-curate-go]').disabled, true);
  assert.match(box.querySelector('[data-commit-blocker="no_survey"]').textContent.trim(), /^\S* ?surveyed before 10-07 · nothing kept to publish · re-survey to publish$/);
  assert.doesNotMatch(box.textContent, /first survey/);

  const noProject = { ...PLAN, project: { status: 'unset', word: 'no project' } };
  box.innerHTML = m.repoCommitPanelHtml({ ...sel({ plan: noProject }), me: 'dan', resurvey: false });
  const b = box.querySelector('[data-commit-blocker="no_project"]');
  assert.match(b.textContent, /no Egeria project context/);
  // The choices are ONE Project row on Contents (publish.js), not a second copy in the blocker.
  assert.equal(b.querySelector('[data-commit-bind]') || b.querySelector('[data-commit-decline]') || b.querySelector('button'), null, 'the blocker names the block; the choices are the Project row');

  box.innerHTML = m.repoCommitPanelHtml({ ...sel(), me: '', resurvey: false });
  assert.equal(box.querySelector('[data-curate-go]').disabled, true);
});

test('the re-survey box is off by default, names the stale count, and is unavailable when nothing is stale', async () => {
  const { m, document } = await mod();
  const box = document.createElement('div');
  box.innerHTML = m.repoCommitPanelHtml({ ...sel(), me: 'dan', resurvey: false });
  const cb = box.querySelector('[data-commit-resurvey]');
  assert.equal(cb.checked, false);
  assert.equal(cb.disabled, false);
  assert.match(box.querySelector('[data-commit-survey-line]').textContent, /survey of 2026-10-07 · 3 stale steps/);
  assert.match(box.querySelector('[data-commit-resurvey]').closest('label').textContent, /re-survey stale steps first \(adds minutes\)/);
  const fresh = { ...PLAN, survey: { ...PLAN.survey, stale_steps: 0, stale: [] } };
  box.innerHTML = m.repoCommitPanelHtml({ ...sel({ plan: fresh }), me: 'dan', resurvey: false });
  assert.equal(box.querySelector('[data-commit-resurvey]').disabled, true);
  box.innerHTML = m.repoCommitPanelHtml({ ...sel(), me: 'dan', resurvey: true });
  assert.equal(box.querySelector('[data-commit-resurvey]').checked, true);
});

test('the commit header reads "Publish · commit <id> · step n of m · running: <label> · k failed"', async () => {
  const { m } = await mod();
  const rec = { id: 'abcdef1234', steps: [{ name: 'publish_asset', state: 'done' }, { name: 'classifications', state: 'running' },
    { name: 'sub_resources', state: 'pending' }, { name: 'components', state: 'pending' }] };
  assert.equal(m.commitHeaderHtml(rec).replace(/<[^>]+>/g, '').trim(), 'Publish · commit abcdef12 · step 2 of 4 · running: classifications · 0 failed');
  rec.steps[1].state = 'failed';
  assert.match(m.commitHeaderHtml(rec).replace(/<[^>]+>/g, ''), /step 1 of 4 · 1 failed|step 2 of 4 · 1 failed/);
});
