/* Open data as a resource kind, beside Repos · DBs · FS (Architect's walk §4, brief OD2 Part B3).
 *
 *  The sidebar lists the imported datasets; "＋ Find open data" opens the portal search (the pane Admin used to be
 *  the only door to); a dataset opens into its page in the main pane. Registering portals and directories, the
 *  caps and the price line's settings stay in Admin. The shortlist is the work list the import creates: it is
 *  listed under the sidebar's work lists like any other.
 *
 * Nothing here surveys, profiles or publishes. A dataset has no stage strip yet; Analysis, Curate and Automate
 * arrive with OD2-OD4, and this page says so in words about the dataset, not about the build.
 */
import { listOpenData, getOpenData, listOpenDataPortals } from '/static/re-api.js';
import { openDialog } from '/static/next/worklist.js';
import { esc } from '/static/next/app.js';
import { datasetPageHtml, openPortalPane, show } from '/static/next/admin/open_data.js';
import { watchTables } from '/static/next/colresize.js';
import { mountShortlist } from '/static/next/open-data-shortlist.js';
import { mountPublish } from '/static/next/open-data-publish.js';

export const NO_DATASETS_WORDS = 'Nothing imported yet. Use ＋ Find open data to search a data portal.';
export const NO_PORTALS_WORDS = 'No data portals are registered yet. Registering a portal is done in Admin › Open data.';
export const IMPORTED_EVENT = 're:open-data-imported';

/** The sidebar's dataset rows. Each is a `button[data-slug]`, so the sidebar's own selection handler opens it. */
export function openDataRowsHtml(rows, loaded, selectedSlug) {
  if (!loaded) return '<div class="px-1 py-2 text-chip text-chrome-muted">reading the datasets…</div>';
  if (!rows.length) return `<div data-od-empty class="px-1 py-2 text-chip text-chrome-muted">${esc(NO_DATASETS_WORDS)}</div>`;
  const inner = (d) => `<span class="block truncate">${show(d.title)}</span>
      <span class="block truncate text-provenance text-chrome-muted">${show(d.publisher)} · ${esc(d.licence_id === 'not_reported' ? 'license not reported' : d.licence_id)}</span>`;
  // Literal class strings per state (the Tailwind scan reads literals, not interpolations).
  return rows.map((d) => (d.slug === selectedSlug
    ? `<button type="button" data-slug="${esc(d.slug)}" class="mb-[2px] block w-full cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[3px] text-left text-accent-on-dark">${inner(d)}</button>`
    : `<button type="button" data-slug="${esc(d.slug)}" class="mb-[2px] block w-full cursor-pointer rounded-sm border border-chrome-line bg-transparent px-2 py-[3px] text-left text-chrome-ink hover:border-accent">${inner(d)}</button>`)).join('');
}

/** The main pane for the open_data kind: the selected dataset's page, or the sentence that says there is none. */
export async function renderOpenDataPane(el, slug, rows) {
  if (!slug || !rows.some((r) => r.slug === slug)) {
    el.innerHTML = `<p data-od-empty class="max-w-[70ch] text-answer text-ink">${esc(NO_DATASETS_WORDS)}</p>`;
    return;
  }
  el.innerHTML = '<p class="text-answer text-ink-muted">Reading the dataset…</p>';
  try {
    const dataset = await getOpenData(slug);
    el.innerHTML = `${datasetPageHtml(dataset)}<div data-od-publish></div><div data-od-shortlist></div>`;
    watchTables(el);
    mountPublish(el.querySelector('[data-od-publish]'), slug);
    if (dataset.work_list_slug) await mountShortlist(el.querySelector('[data-od-shortlist]'), dataset.work_list_slug);
  } catch (e) {
    el.innerHTML = `<p class="max-w-[70ch] text-answer text-state-warn">The dataset could not be read: ${esc((e && (e.detail || e.message)) || String(e))}</p>`;
  }
}

export async function fetchOpenData() {
  return (await listOpenData()) || [];
}

/** ＋ Find open data: pick a registered portal, then the same search pane the Admin screen used. */
export async function openFindOpenDataDialog({ investigation = '', lens = null, requestName = '' } = {}) {
  const dialog = openDialog('Find open data', 'search a data portal, then import chosen rows into a work list', { wide: true });
  const body = dialog.querySelector('#wl-detail-body');
  let portals = [];
  try { portals = (await listOpenDataPortals()).filter((p) => p.adapter_available); }
  catch (e) { body.innerHTML = `<p class="text-state-warn">The portals could not be read: ${esc((e && (e.detail || e.message)) || String(e))}</p>`; return; }
  if (!portals.length) { body.innerHTML = `<p data-od-empty class="text-answer text-ink">${esc(NO_PORTALS_WORDS)}</p>`; return; }
  body.innerHTML = `<label class="text-caveat text-ink-muted">Portal
      <select data-od-portal-pick class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink">
        ${portals.map((p) => `<option value="${esc(p.slug)}">${esc(p.display_name)}</option>`).join('')}</select></label>
    <div data-od-pane class="mt-s3"></div>`;
  const pick = body.querySelector('[data-od-portal-pick]');
  const open = () => openPortalPane(body, pick.value, { lens, investigation, requestName });
  pick.addEventListener('change', open);
  open();
}

