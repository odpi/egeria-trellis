/** TR-81: one byte count, one unit. Every size the UI labels KB/MB/GB is decimal (1 MB = 1,000,000 bytes), through
 *  `fmtBytesDecimal` in native-report.js, the JS twin of resource_explorer/byte_format.py. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ensureLoaderRegistered } from './dom-harness.mjs';

ensureLoaderRegistered();

test('the loading_bay bytes read 60.2 MB, as the server says them', async () => {
  const nr = await import('/static/next/stages/native-report.js');
  assert.equal(nr.fmtBytesDecimal(60_209_097), '60.2 MB');
  assert.equal(nr.fmtBytes(60_209_097), '60.2 MB', 'the older name is the same function, not a second format');
  assert.equal(nr.fmtBytes(8_400_000_000), '8.4 GB');
});

test('every other formatter in the UI agrees with it', async () => {
  const rt = await import('/static/next/stages/report-tables.js');
  assert.equal(rt.fmtSize(60_209_097), '60.2 MB');
  assert.equal(rt.fmtSize(1_048_576), '1.0 MB');
});
