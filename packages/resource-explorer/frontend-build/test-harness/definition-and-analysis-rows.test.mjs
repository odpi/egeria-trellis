/** Brief sections 8 and 9 (owner, 2026-10-07): on a database's Survey pane the definition row and the
 *  analyses rows say what they do. 8: the two "re-run →" buttons did different things; the analyses
 *  row says which credential it uses, and (owner-approved) offers its own credential dialog.
 *  9: the definition row counts the analyses it runs, and every analyses row names its definition.
 *
 *  Real app.js module, rows rendered from the server's shapes.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

const SCAN = 'Database Scouting Scan';
const cov = (over = {}) => ({ runs_in: [], also_in: [], not_in: [], ...over });
const row = (over = {}) => ({
  analysis_id: 'schema_inventory', name: 'Schema Inventory', tier: 'scouting', runnable: true, runnable_reason: '',
  last_run_at: '', last_run_status: '', questions: [], serves: 'question', cost: null,
  uses_credential: 'stored', definitions: cov(), last_run_ran_as: null, last_run_not_retried: '', ...over,
});

async function render(r) {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  const box = document.createElement('div');
  document.body.appendChild(box);
  box.innerHTML = app.analysisIndexRowHtml(r);
  return { box, app, document };
}

test('section 8: the definition row opens a dialog, so its word ends in an ellipsis; the analyses row keeps "Re-run →"', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  const box = document.createElement('div');
  document.body.appendChild(box);
  box.innerHTML = app.surveyRowHtml({ display_name: SCAN, qualified_name: 'GovActionProcess::X', steps: [], last_run_at: '2026-09-28T03:15:42Z' });
  assert.equal(box.querySelector('[data-run-survey]').textContent.trim(), 'Re-run…');
  box.innerHTML = app.analysisIndexRowHtml(row({ last_run_at: '2026-09-28T03:15:42Z' }));
  assert.equal(box.querySelector('[data-analysis-run]').textContent.trim(), 'Re-run →');
});

test('section 8: every runnable database analysis row says which credential it uses, with the sentence on hover', async () => {
  const { box } = await render(row());
  const w = box.querySelector('[data-analysis-credential="stored"]');
  assert.ok(w, 'the credential word must be on the row');
  assert.equal(w.textContent.trim(), 'uses stored credential');
  assert.match(w.getAttribute('title'), /runs in the background with the credential saved for this database/);
});

test('section 8: a zero-fetch analysis does not claim a stored credential (it opens no connection)', async () => {
  const { box } = await render(row({ analysis_id: 'db_classification', uses_credential: 'none' }));
  assert.equal(box.querySelector('[data-analysis-credential="stored"]'), null);
  assert.equal(box.querySelector('[data-analysis-credential="none"]').textContent.trim(), 'needs no credential');
  assert.equal(box.querySelector('[data-analysis-override]'), null, 'no override offered where no credential is used');
});

test('designer Q5: "Re-run →" is a button; "with options…" beside it is a quiet link, on a stored-credential row only', async () => {
  const { box } = await render(row());
  const link = box.querySelector('[data-analysis-override]');
  assert.equal(link.textContent.trim(), 'with options…');
  assert.ok(/\bborder-0\b/.test(link.className) && /\bunderline\b/.test(link.className) && /text-ink-muted/.test(link.className),
    'muted, underlined, no border: a link, not a twin button');
  assert.ok(/\bborder\b/.test(box.querySelector('[data-analysis-run]').className.replace('border-accent', '')) ||
    box.querySelector('[data-analysis-run]').className.includes('border-accent'), 'the run control keeps its border');
  const none = await render(row({ runnable: false, runnable_reason: 'no step' }));
  assert.equal(none.box.querySelector('[data-analysis-override]'), null);
});

test('section 8 (optional slice): an override run reads "ran as <user> (this run)" and "not retried", from the row\'s record', async () => {
  const { box } = await render(row({
    last_run_at: '2026-10-07T10:00:00Z', last_run_status: 'ok',
    last_run_ran_as: { user: 'one_off_user', scope: 'this run' },
    last_run_not_retried: 'not retried · credential was for this run only' }));
  const t = box.querySelector('[data-analysis-ran-as]').textContent.replace(/\s+/g, ' ');
  assert.match(t, /ran as one_off_user \(this run\)/);
  assert.match(t, /not retried · credential was for this run only/);
  const plain = await render(row({ last_run_at: '2026-10-07T10:00:00Z' }));
  assert.equal(plain.box.querySelector('[data-analysis-ran-as]'), null, 'a stored-credential run claims no ran-as');
});

test('section 8 (optional slice): the dialog sends the credential only on Run once, and a password alone is refused', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  const sent = [];
  app.openAnalysisOverrideDialog(row(), 'laz_local_adventureworks', (c) => sent.push(c));
  const body = document.querySelector('#wl-detail-body');
  const user = body.querySelector('[data-run-override-user]');
  const pw = body.querySelector('[data-run-override-password]');
  assert.equal(pw.getAttribute('value'), null, 'a password is never an attribute');
  pw.value = 'fake-not-real';
  body.querySelector('[data-act="go"]').click();
  assert.deepEqual(sent, [], 'a password with no user sends nothing');
  assert.match(body.querySelector('[data-run-override-error]').textContent, /needs both a user and a password/);
  user.value = 'one_off_user';
  body.querySelector('[data-act="go"]').click();
  assert.deepEqual(sent, [{ user: 'one_off_user', password: 'fake-not-real' }]);
});

test('section 9: the five scouting rows name the Scan and their step', async () => {
  const steps = { schema_inventory: 'postgres_schema_and_stats', db_activity_signals: 'postgres_operations',
    credential_capability: 'credential_capability' };
  for (const [aid, step] of Object.entries(steps)) {
    const { box } = await render(row({ analysis_id: aid, definitions: cov({ runs_in: [{ display_name: SCAN, step }] }) }));
    const t = box.querySelector('[data-analysis-coverage]').textContent.replace(/\s+/g, ' ');
    assert.match(t, new RegExp(`in ${SCAN} · step ${step}`), aid);
  }
});

test('section 9: the bundle side-effects under "Other stages" say they also run when the Scan runs', async () => {
  const { box } = await render(row({ analysis_id: 'db_external_dependencies', tier: 'analysis',
    definitions: cov({ also_in: [{ display_name: SCAN, step: 'postgres_operations' }] }) }));
  assert.match(box.querySelector('[data-analysis-coverage]').textContent.replace(/\s+/g, ' '),
    /also runs when Database Scouting Scan runs \(postgres_operations\)/);
});

test('section 9: an analysis a definition no longer covers says it runs on its own', async () => {
  const { box } = await render(row({ definitions: cov({ not_in: [{ display_name: SCAN, step: '' }] }) }));
  assert.match(box.querySelector('[data-analysis-coverage]').textContent.replace(/\s+/g, ' '),
    /not part of Database Scouting Scan · runs on its own/);
});

test('section 9: several definitions are counted, with the list one gesture away', async () => {
  const { box } = await render(row({ definitions: cov({ runs_in: [
    { display_name: 'A', step: 's1' }, { display_name: 'B', step: 's1' }] }) }));
  const d = box.querySelector('[data-analysis-coverage] details');
  assert.match(d.querySelector('summary').textContent.replace(/\s+/g, ' '), /in 2 definitions/);
  assert.match(d.textContent, /A · step s1/);
  assert.match(d.textContent, /B · step s1/);
});

test('section 9: the definition row says "3 steps · runs 5 analyses", the list one gesture away; no claim when the server did not say', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  const box = document.createElement('div');
  document.body.appendChild(box);
  const a = (id) => ({ analysis_id: id, name: id });
  box.innerHTML = app.surveyRowHtml({ display_name: SCAN, qualified_name: 'GovActionProcess::X',
    steps: [1, 2, 3].map((i) => ({ qualified_name: `s${i}` })),
    runs_analyses: { own: ['a', 'b', 'c', 'd', 'e'].map(a), also: [a('f'), a('g')] } });
  const t = box.textContent.replace(/\s+/g, ' ');
  assert.match(t, /3 steps/);
  assert.match(t, /runs 5 analyses/);
  assert.equal(box.querySelectorAll('[data-runs-analyses] ul')[0].querySelectorAll('li').length, 5);
  assert.match(box.querySelector('[data-runs-analyses]').textContent, /also produces, from the same steps/);
  box.innerHTML = app.surveyRowHtml({ display_name: SCAN, qualified_name: 'GovActionProcess::X', steps: [] });
  assert.equal(box.querySelector('[data-runs-analyses]'), null);
});
