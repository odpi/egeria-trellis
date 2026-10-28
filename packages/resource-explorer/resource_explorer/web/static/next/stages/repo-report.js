/* The full survey report for a repository (parity PI-053): metrics, file types, data files, dependencies.
 *
 * Classic had a "full report" page; Next showed the same measurements only as boards. This is a section of
 * the Understanding pane, read from `GET /api/egeria/{slug}/survey-report` (assembled from the registry,
 * no Egeria call).
 *
 * THE RULES: a figure the registry never held reads "not read", never 0 (`health_read` false means there
 * is no project_stats row, so the server's zeros were defaults). "Never surveyed" and "surveyed, none
 * found" are different statements. Nothing here writes: Classic's "Catalog selected" file-type button is
 * retired (file types are published as annotations, DESIGN-FILE-TYPES-AS-ANNOTATIONS.md).
 */
import { stateEntry } from '/static/next/glyphs.js';
import { ago } from '/static/next/format.js';

function esc(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

const unread = (title = 'The registry has no value for this yet.') =>
  `<span data-unread class="text-ink-muted" title="${esc(title)}"><span class="font-glyph" aria-hidden="true">${stateEntry('not_established').glyph}</span> not read</span>`;

const card = (key, label, bodyHtml) =>
  `<div data-report-card="${esc(key)}" class="min-w-0 rounded-sm border border-rule px-2 py-[2px]">`
  + `<div class="text-provenance uppercase text-ink-muted">${esc(label)}</div>`
  + `<div class="tnum truncate text-caveat text-ink">${bodyHtml}</div></div>`;

function sizeText(bytes) {
  if (!bytes) return '';
  return bytes >= 1048576 ? `${(bytes / 1048576).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`;
}


/** The report body for one `survey-report` response. */
export function repoReportHtml(d) {
  if (!d) return '';
  const h = d.health || {};
  const read = d.health_read === true;
  // null is "not read" even when the row exists (an ingestion-only row has no GitHub figures).
  const val = (v) => (read && v != null ? esc(String(v)) : unread());
  const surveyed = d.local_surveyed_at
    ? `kept survey ran <span class="tnum">${esc(ago(d.local_surveyed_at))}</span>`
    : '<span data-report-never class="text-ink-muted">no survey kept</span>';
  const published = d.latest_survey
    ? ` · published report${d.latest_survey.annotation_count != null ? `, <span class="tnum">${esc(d.latest_survey.annotation_count)}</span> annotations` : ''}`
    : ' · not published';

  const metrics = `<div data-report-metrics class="mt-s2 grid grid-cols-2 gap-s1 sm:grid-cols-4">
    ${card('stars', 'Stars', val(h.stars))}${card('forks', 'Forks', val(h.forks))}
    ${card('open_issues', 'Open issues', val(h.open_issues))}${card('contributors', 'Contributors', val(h.contributors))}
    ${card('language', 'Language', d.primary_language ? esc(d.primary_language) : unread())}
    ${card('license', 'License', read ? (h.license ? esc(h.license) : '<span class="text-ink-muted">none declared</span>') : unread())}
    ${card('total_files', 'Total files', d.local_surveyed_at ? esc(String(d.total_files ?? 0)) : unread('No survey is kept for this repository.'))}
  </div>`;

  let fileTypes;
  if (!d.local_surveyed_at) {
    fileTypes = `<p data-report-file-types="unread" class="mt-s2 text-caveat">${unread('No survey is kept for this repository.')}</p>`;
  } else if (!(d.file_types || []).length) {
    fileTypes = '<p data-report-file-types="none" class="mt-s2 text-caveat text-ink-muted">surveyed: no file types recorded</p>';
  } else {
    const total = d.total_files || 0;
    fileTypes = `<table data-report-file-types="rows" class="mt-s2 w-full"><thead><tr><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Type</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Files</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Share</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Extensions</th></tr></thead><tbody>${
      d.file_types.map((ft) => {
        const exts = Object.keys(ft.extensions || {}).sort((a, b) => ft.extensions[b] - ft.extensions[a]).slice(0, 5).join(', ');
        return `<tr data-file-type="${esc(ft.label)}"><td class="px-s2 py-[2px] text-caveat text-ink">${esc(ft.label)}</td><td class="px-s2 py-[2px] text-caveat text-ink tnum">${esc(ft.count)}</td><td class="px-s2 py-[2px] text-caveat text-ink tnum">${total ? Math.round((ft.count / total) * 100) : 0}%</td><td class="px-s2 py-[2px] text-caveat text-ink text-ink-muted">${esc(exts)}</td></tr>`;
      }).join('')}</tbody></table>`;
  }

  const profiles = d.data_profiles || [];
  let dataFiles;
  if (!profiles.length) {
    dataFiles = '<p data-report-data-files="none" class="mt-s2 text-caveat text-ink-muted">no data files recorded</p>';
  } else {
    dataFiles = `<table data-report-data-files="rows" class="mt-s2 w-full"><thead><tr><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">File</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Format</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Rows</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Columns</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Size</th></tr></thead><tbody>${
      profiles.map((p) => `<tr data-data-file="${esc(p.file_path)}"><td class="px-s2 py-[2px] text-caveat text-ink break-all">${esc(p.file_path)}</td><td class="px-s2 py-[2px] text-caveat text-ink">${esc(p.format)}</td>`
        + `<td class="px-s2 py-[2px] text-caveat text-ink tnum">${p.row_count == null ? unread('Not profiled.') : esc(p.row_count)}</td>`
        + `<td class="px-s2 py-[2px] text-caveat text-ink tnum">${p.col_count == null ? unread('Not profiled.') : esc(p.col_count)}</td>`
        + `<td class="px-s2 py-[2px] text-caveat text-ink tnum">${esc(sizeText(p.file_size_bytes))}</td></tr>`).join('')}</tbody></table>`;
  }

  const deps = d.dependencies || [];
  const dependencies = deps.length
    ? `<table data-report-dependencies="rows" class="mt-s2 w-full"><thead><tr><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Ecosystem</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Dependencies</th><th class="px-s2 py-[2px] text-left text-provenance font-normal uppercase text-ink-muted">Direct</th></tr></thead><tbody>${
      deps.map((e) => `<tr data-ecosystem="${esc(e.ecosystem)}"><td class="px-s2 py-[2px] text-caveat text-ink">${esc(e.ecosystem)}</td><td class="px-s2 py-[2px] text-caveat text-ink tnum">${esc(e.count)}</td><td class="px-s2 py-[2px] text-caveat text-ink tnum">${e.direct == null ? unread('Which dependencies are direct was not determined for this ecosystem.').replace('not read', 'not determined') : esc(e.direct)}</td></tr>`).join('')}</tbody></table>`
    : '<p data-report-dependencies="none" class="mt-s2 text-caveat text-ink-muted">no dependencies recorded</p>';

  const h4 = (t) => `<h4 class="mb-0 mt-s3 font-heading text-caps uppercase tracking-caps text-ink-muted">${esc(t)}</h4>`;
  return `<div data-survey-report>
    <div class="text-provenance text-ink-muted">${surveyed}${published}</div>
    ${h4('Repository metrics')}${metrics}
    ${h4('File types')}${fileTypes}
    ${h4('Data files')}${dataFiles}
    ${h4('Dependencies')}${dependencies}
  </div>`;
}

/** Read and draw the report into `host`. `fetchReport(slug)` is injected. A failed read says so; it does not
 *  draw an empty report. `stale()` lets the caller drop a late answer for a resource no longer in view. */
export async function mountRepoReport(host, slug, { fetchReport, stale = () => false }) {
  host.innerHTML = '<p class="mt-s2 text-caveat text-ink-muted">reading the report…</p>';
  try {
    const d = await fetchReport(slug);
    if (stale()) return;
    host.innerHTML = repoReportHtml(d);
  } catch (err) {
    if (stale()) return;
    host.innerHTML = `<p data-report-error class="mt-s2 text-caveat text-state-warn">The survey report could not be read: ${esc(err && err.message ? err.message : 'unknown error')}</p>`;
  }
}
