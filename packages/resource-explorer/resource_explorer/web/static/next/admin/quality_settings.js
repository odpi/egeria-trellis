/* Admin: the value profile's and quality checks' settings in force -- read-only.
 *
 * The settings are configuration (environment), not edited here; this pane shows each value, whether it was set or is
 * the default, its environment name, and what it means in a person's words (architect, 2026-10-24: the written-live
 * share must be readable here, not only as an env name). */
import { esc } from '/static/next/app.js';
import { getQualitySettings } from '/static/re-api.js';

/** A value as a person reads it: a share as a percent of tables, the rest with their unit. */
export function settingValue(row) {
  if (row.unit === 'share of tables') return `more than ${Math.round(Number(row.value) * 100)}% of the tables measured`;
  return `${row.value} ${row.unit}`;
}

export function settingsHtml(data) {
  const rows = (data && data.settings) || [];
  return `<div class="max-w-[70ch]">
    <h3 class="m-0 mb-s1 font-heading text-name font-normal text-ink">Value profile settings</h3>
    <div class="mb-s2 text-provenance text-ink-muted">the value profile's and quality checks' settings in force · read-only</div>
    ${rows.length ? rows.map((r) => `<div class="mb-s3 border-b border-rule pb-s2" data-quality-setting="${esc(r.name)}">
      <div class="text-answer text-ink"><span data-setting-value>${esc(settingValue(r))}</span>
        <span class="text-provenance text-ink-muted"> · <span data-setting-source>${esc(r.source)}</span></span></div>
      <div class="mt-s1 text-caveat text-ink" data-setting-means>${esc(r.means)}</div>
      <div class="mt-s1 text-provenance text-ink-muted">Setting: <span class="font-mono">${esc(r.setting)}</span>.</div>
    </div>`).join('') : '<div class="text-caveat text-ink-muted">No settings were returned.</div>'}
  </div>`;
}

export async function renderQualitySettings(host) {
  host.innerHTML = '<div class="text-caveat text-ink-muted">Reading the settings…</div>';
  try {
    host.innerHTML = settingsHtml(await getQualitySettings());
  } catch (err) {
    host.innerHTML = `<div class="text-caveat text-state-warn">Could not read the settings: ${esc(err.message)}</div>`;
  }
}
