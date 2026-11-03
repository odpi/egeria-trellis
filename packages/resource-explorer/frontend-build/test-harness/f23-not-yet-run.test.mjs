/** F23 (owner, 2026-10-30: "I prefer \"not yet run\""): a step or analysis that has never run says so ONCE, in exactly
 *  those words. "never run" and "not run yet" are gone, from the rendered row and from every string /next shows. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

const OLD = /never run|not run yet/i;
const fact = (id, over = {}) => ({ analysis_id: id, state: 'never_run', is_known: false, headline: '', value: {}, last_run_at: '',
  as_of: '', can_run: [id], note: 'This analysis has not yet run on this resource.', runnable: true, runnable_reason: '',
  library: false, ...over });
const entry = { question: 'Are this database\'s planner statistics current?', id: 'Q-0073', kind: 'analysis', stage: 'Analysis',
  analysis_ids: ['db_statistics_freshness', 'statistics_freshness'], answering_mechanism: 'Code Analysis' };
const text = (html) => html.replace(/<[^>]*>/g, ' ').replace(/\s+/g, ' ');

async function load() {
  makeDomEnvironment();
  await loadAppModule();
  return import('/static/next/app.js');
}

test('a question whose analyses all never ran says "Not yet run." once: no row-level word, no step-line word', async () => {
  const app = await load();
  const env = { answerable: false, blocked_reason: 'Not yet run. Run: db_statistics_freshness, statistics_freshness.',
    facts: [fact('db_statistics_freshness'), fact('statistics_freshness', { library: true })], caveat: '', sources: [] };
  const said = text(app.rowInner(entry, 0, env));
  assert.ok(!OLD.test(said), said);
  assert.equal(said.match(/not yet run/gi).length, 1, said);
  assert.match(said, /statistics_freshness · run/);   // the step line still offers Run
});

test('a step that never ran beside a lead that did says "not yet run" once, on its own line', async () => {
  const app = await load();
  const env = { answerable: true, facts: [
    fact('db_statistics_freshness', { state: 'measured', is_known: true, headline: '3 of 4 tables are stale.', note: '',
      last_run_at: '2026-10-29T10:00:00' }),
    fact('statistics_freshness', { library: true, note: '' })], caveat: '', sources: [] };
  const said = text(app.rowInner(entry, 0, env));
  assert.ok(!OLD.test(said), said);
  assert.equal(said.match(/not yet run/gi).length, 1, said);
});

test('no string /next shows says "never run" or "not run yet"', () => {
  const root = join(dirname(fileURLToPath(import.meta.url)), '../../resource_explorer/web/static/next');
  const files = [];
  const walk = (d) => { for (const n of readdirSync(d)) { const p = join(d, n); statSync(p).isDirectory() ? walk(p) : p.endsWith('.js') && files.push(p); } };
  walk(root);
  const found = [];
  for (const p of files) {
    readFileSync(p, 'utf8').split('\n').forEach((line, i) => {
      const code = line.replace(/\/\/.*$/, '');
      if (/^\s*\*/.test(line) || /^\s*\/\*/.test(line)) return;              // comments may name the old words
      if (/(['"`])[^'"`]*(never run|not run yet)[^'"`]*\1/i.test(code)) found.push(`${p.split('/next/')[1]}:${i + 1}`);
    });
  }
  assert.deepEqual(found, []);
});
