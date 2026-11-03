/* The data request band on an investigation (design step 1; OD2 brief B4): what data this investigation needs, stated
 * once. Six dimensions, each "not stated" until filled. It feeds the portal search (terms, modified-after) and the
 * winnow (license, format, period, place). Stored per investigation in `investigation_data_requests` (approved by the
 * project owner 2026-10-30); until that table is applied the band says so and does not offer a save it would refuse.
 */
import { getDataRequest, putDataRequest } from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { openFindOpenDataDialog } from '/static/next/open-data-kind.js';

const NOT_STATED = '<span class="text-ink-muted">not stated</span>';
const errText = (e) => esc((e && (e.detail || e.message)) || String(e));
const list = (v) => (v && v.length ? esc(v.join(', ')) : NOT_STATED);

export function requestSummaryHtml(r) {
  const lic = (r && r.licence) || {};
  const needs = [lic.commercial ? 'commercial use needed' : '', lic.share_alike === false ? 'no share-alike' : ''].filter(Boolean);
  const cell = (label, value) => `<div style="min-width:140px"><div class="text-caps uppercase tracking-caps text-ink-muted">${esc(label)}</div><div class="text-caveat text-ink">${value}</div></div>`;
  return `<div data-dr-summary class="mb-s2 flex flex-wrap gap-s4">
    ${cell('Subjects', list(r && r.subjects))}${cell('Period', r && r.period ? esc(r.period) : NOT_STATED)}${cell('Places', list(r && r.places))}
    ${cell('Grain', r && r.grain ? esc(r.grain) : NOT_STATED)}${cell('License needs', needs.length ? esc(needs.join(', ')) : NOT_STATED)}${cell('Formats', list(r && r.formats))}</div>`;
}

export function bodyFromForm(form) {
  const f = Object.fromEntries(new FormData(form).entries());
  const split = (t) => String(t || '').split(',').map((x) => x.trim()).filter(Boolean);
  const licence = {};
  if (f.need_commercial) licence.commercial = true;
  if (f.need_share_alike === '0') licence.share_alike = false;
  return { subjects: split(f.subjects), period: f.period || '', places: split(f.places), grain: f.grain || '', licence, formats: split(f.formats) };
}

/** Mount the band for one investigation into `host`. */
export async function mountDataRequestBand(host, slug) {
  let got;
  try { got = await getDataRequest(slug); }
  catch (e) { host.innerHTML = `<p class="text-caveat text-state-warn">The data request could not be read: ${errText(e)}</p>`; return; }
  const r = got.request;
  host.innerHTML = `<div class="mb-s2 flex items-center gap-s3">
      <h4 class="m-0 font-heading text-subtab font-normal text-ink">Data request</h4>
      <button type="button" data-dr-edit class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">${got.stated ? 'Edit' : 'State it'}</button>
      <button type="button" data-dr-search class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Search portals for this request</button>
    </div>
    ${got.stated ? '' : '<p data-dr-none class="mb-s2 text-caveat text-ink-muted">No data request stated for this investigation.</p>'}
    ${requestSummaryHtml(r)}
    ${got.stored ? '' : '<p data-dr-unstored class="mb-s2 text-caveat text-state-warn">The data request cannot be stored yet: its table is applied at the next owner-approved restart.</p>'}
    <form data-dr-form class="mb-s2 hidden flex-wrap items-end gap-[6px]">
      <label class="text-caveat text-ink-muted">Subjects <input name="subjects" value="${esc(((r && r.subjects) || []).join(', '))}" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:200px"></label>
      <label class="text-caveat text-ink-muted">Period <input name="period" value="${esc((r && r.period) || '')}" placeholder="2019-2023" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:100px"></label>
      <label class="text-caveat text-ink-muted">Places <input name="places" value="${esc(((r && r.places) || []).join(', '))}" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:140px"></label>
      <label class="text-caveat text-ink-muted">Grain <input name="grain" value="${esc((r && r.grain) || '')}" placeholder="daily, by county" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:140px"></label>
      <label class="text-caveat text-ink-muted">Formats <input name="formats" value="${esc(((r && r.formats) || []).join(', '))}" placeholder="CSV, JSON" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:120px"></label>
      <label class="text-caveat text-ink-muted">License needs
        <select name="need_commercial" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"><option value="">commercial: not stated</option><option value="1" ${r && r.licence && r.licence.commercial ? 'selected' : ''}>commercial use needed</option></select>
        <select name="need_share_alike" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"><option value="">share-alike: not stated</option><option value="0" ${r && r.licence && r.licence.share_alike === false ? 'selected' : ''}>no share-alike</option></select></label>
      <button type="submit" ${got.stored ? '' : 'disabled'} class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">Save</button>
      <span data-dr-state class="text-provenance text-ink-muted"></span>
    </form>`;
  const form = host.querySelector('[data-dr-form]');
  host.querySelector('[data-dr-edit]').addEventListener('click', () => form.classList.toggle('hidden'));
  host.querySelector('[data-dr-search]').addEventListener('click', () => openFindOpenDataDialog({ investigation: slug }));
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const state = host.querySelector('[data-dr-state]');
    state.textContent = 'saving…';
    try { await putDataRequest(slug, bodyFromForm(form)); await mountDataRequestBand(host, slug); }
    catch (e) { state.innerHTML = errText(e); }
  });
}
