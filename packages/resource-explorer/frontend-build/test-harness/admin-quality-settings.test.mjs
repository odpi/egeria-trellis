/** Behaviour: the Admin page shows the value profile's settings, read-only, each with its value as a person reads it,
 *  whether it was set or is the default, its environment name and what it means (architect, 2026-10-24: the
 *  written-live share must be readable there, not only as an env name). Real module; only fetch is stubbed. */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { makeDomEnvironment, ensureLoaderRegistered } from './dom-harness.mjs';

const text = (el) => el.textContent.replace(/\s+/g, ' ').trim();

const SETTINGS = { settings: [
  { name: 'live_write_table_share', setting: 'VALUE_PROFILE_LIVE_WRITE_TABLE_SHARE', value: 0.25,
    unit: 'share of tables', source: 'default',
    means: 'When a repeat value-profile publish finds counts moved on more than this share of the tables it measured, '
      + 'the run card adds “this database is written live · see change rates”.' },
  { name: 'table_budget_seconds', setting: 'VALUE_PROFILE_TABLE_BUDGET_SECONDS', value: 120, unit: 'seconds',
    source: 'set in the environment', means: 'The most time one table’s value profile may take.' },
] };

async function setUp(answer) {
  const { document, window } = makeDomEnvironment();
  globalThis.fetch = async (url) => {
    const u = String(url);
    if (u.startsWith('/api/quality-checks/settings')) return answer;
    return { ok: true, status: 200, json: async () => ({}) };
  };
  ensureLoaderRegistered();
  globalThis.location = window.location;
  globalThis.history = window.history;
  const api = await import('/static/re-api.js');
  api.clearCache();
  const pane = await import('/static/next/admin/quality_settings.js');
  const host = document.createElement('div');
  document.body.appendChild(host);
  return { host, pane };
}

test('the written-live share reads as a percent of tables with what it means and where it is set', async () => {
  const { host, pane } = await setUp({ ok: true, status: 200, json: async () => SETTINGS });
  await pane.renderQualitySettings(host);
  const live = host.querySelector('[data-quality-setting="live_write_table_share"]');
  assert.ok(live, 'no row for the written-live share');
  assert.equal(text(live.querySelector('[data-setting-value]')), 'more than 25% of the tables measured');
  assert.equal(text(live.querySelector('[data-setting-source]')), 'default');
  assert.match(text(live.querySelector('[data-setting-means]')), /this database is written live · see change rates/);
  assert.match(text(live), /VALUE_PROFILE_LIVE_WRITE_TABLE_SHARE/);
  const budget = host.querySelector('[data-quality-setting="table_budget_seconds"]');
  assert.equal(text(budget.querySelector('[data-setting-value]')), '120 seconds');
  assert.equal(text(budget.querySelector('[data-setting-source]')), 'set in the environment');
});

test('a refused read says it could not read the settings, never an empty pane', async () => {
  const { host, pane } = await setUp({ ok: false, status: 403, json: async () => ({ detail: 'admin only' }),
    text: async () => 'admin only' });
  await pane.renderQualitySettings(host);
  assert.match(text(host), /^Could not read the settings/);
});

test('the Admin page lists the pane under Configure', async () => {
  const src = await (await import('node:fs/promises')).readFile(
    new URL('../../resource_explorer/web/static/next/admin/index.js', import.meta.url), 'utf8');
  assert.match(src, /\{ id: 'admin-quality-settings', label: '📏 Value profile settings', render: renderQualitySettings \}/);
});
