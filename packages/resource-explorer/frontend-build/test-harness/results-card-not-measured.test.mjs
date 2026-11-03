// An analysis result whose value was never measured says "not measured" -- never blank, never 0 -- and the count of
// what it left out is shown beside it. Found on a filesystem_inventory result with an unknown total
// (total_size_bytes: null, unmeasured_file_count: 3).
import test from 'node:test';
import assert from 'node:assert/strict';
import { makeDomEnvironment, loadAppModule } from './dom-harness.mjs';

const RESULTS = { file_count: 3, total_size_bytes: null, unmeasured_file_count: 3, directory_count: 1 };

test('the evidence card says "not measured" for a null total and shows the unmeasured count', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  document.body.innerHTML = '<div id="host"></div>';
  const host = document.getElementById('host');
  host.innerHTML = Object.entries(RESULTS).map(([k, v]) => app.measureHtml(k, v)).join('');
  assert.match(host.textContent, /total size bytes\s*not measured/);
  assert.match(host.textContent, /unmeasured file count\s*3/);
  assert.doesNotMatch(host.textContent, /not set/);
});

test('a known size on the evidence card is a size, not a raw byte count', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  document.body.innerHTML = '<div id="host"></div>';
  const host = document.getElementById('host');
  host.innerHTML = app.measureHtml('total_size_bytes', 131072);
  assert.match(host.textContent, /total size bytes\s*128\.0 KB/);
});

test('the COUNTS table keeps a null total as a "not measured" row', async () => {
  const { document } = makeDomEnvironment();
  const app = await loadAppModule();
  document.body.innerHTML = '<div id="host"></div>';
  const host = document.getElementById('host');
  const board = { id: 'filesystem_inventory', analyses: [{ analysis_id: 'filesystem_inventory', results: RESULTS }] };
  host.innerHTML = app.boardCountsHtml(board, new Set());
  const row = [...host.querySelectorAll('tr')].find((r) => /total size bytes/.test(r.textContent));
  assert.ok(row, 'the row is there, not dropped');
  assert.match(row.textContent, /not measured/);
  assert.match(host.textContent, /unmeasured file count\s*3/);
});
