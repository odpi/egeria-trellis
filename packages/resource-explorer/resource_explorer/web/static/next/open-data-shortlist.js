/* The shortlist panel (OD2): scout a work list of open data, state what you need, and winnow.
 *
 *  Scout   re-reads every dataset of the work list from its portal, one call each; the price line is shown first.
 *  Winnow  compares each dataset with the request (license, format, size, period, place) on the stored columns.
 *          Only a stated mismatch drops a row, and dropped rows stay listed with the reason. "could not check"
 *          rows are kept and said; a request that states no need on a dimension passes nobody on it.
 */
import { openDataScoutPrice, scoutOpenData, fitOpenData } from '/static/re-api.js';
import { esc, state as appState } from '/static/next/app.js';
import { watchTables } from '/static/next/colresize.js';
import { mountProfile } from '/static/next/open-data-profile.js';
import { mountRefresh } from '/static/next/open-data-publish.js';

const errText = (e) => esc((e && (e.detail || e.message)) || String(e));
const CUE = { fits: '✓', does_not_fit: '✗', could_not_check: '?', not_stated: '–' };

export function fitCellHtml(c) {
  return `<td class="py-s2 pr-s3 text-caveat align-top" title="${esc(c.words)}"><span class="font-glyph">${CUE[c.status] || '?'}</span> <span class="text-ink-muted">${esc(c.words)}</span></td>`;
}

export function winnowHtml(res) {
  const verdictWords = { passes: 'passes', dropped: 'dropped', open: 'open: nothing stated drops it' };
  return `<p data-od-winnow-summary class="mb-s2 text-answer text-ink tnum">${res.rows.length} datasets · ${res.passes} pass · ${res.open} open · ${res.dropped} dropped — ${esc(res.summary)}${
    res.could_not_check ? ` · ${res.could_not_check} with a dimension that could not be checked` : ''}</p>
    <table class="w-full text-left" data-rc-id="od-winnow"><thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
      <th class="pb-s2 pr-s3 font-normal">Dataset</th><th class="pb-s2 pr-s3 font-normal">License</th><th class="pb-s2 pr-s3 font-normal">Format</th><th class="pb-s2 pr-s3 font-normal">Size</th>
      <th class="pb-s2 pr-s3 font-normal">Period</th><th class="pb-s2 pr-s3 font-normal">Place</th><th class="pb-s2 pr-s3 font-normal">Overlap</th><th class="pb-s2 pr-s3 font-normal">Verdict</th></tr></thead>
    <tbody>${res.rows.map((r) => `<tr class="border-b border-rule" data-od-fit="${esc(r.verdict)}">
      <td class="py-s2 pr-s3 text-caveat align-top">${esc(r.title)}</td>${['license', 'format', 'size', 'period', 'place'].map((k) => fitCellHtml(r.cells[k])).join('')}
      <td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">${esc(r.overlap.words)}</td>
      <td class="py-s2 pr-s3 text-caveat align-top">${esc(verdictWords[r.verdict] || r.verdict)}</td></tr>`).join('')}</tbody></table>`;
}

export function requestFromForm(form) {
  const f = Object.fromEntries(new FormData(form).entries());
  const licence = {};
  if (f.need_commercial) licence.commercial = true;
  if (f.need_share_alike === '0') licence.share_alike = false;
  const mb = Number(f.max_mb);
  return { formats: f.formats || '', period: f.period || '', places: f.places || '', licence,
           max_size_bytes: Number.isFinite(mb) && mb > 0 ? String(Math.round(mb * 1e6)) : '' };
}

/** Mount the panel for one work list into `host`. */
export async function mountShortlist(host, workList) {
  host.innerHTML = `<h5 class="m-0 mb-s2 mt-s4 text-caps uppercase tracking-caps text-ink-muted">Shortlist · <span class="font-mono">${esc(workList)}</span></h5>
    <p data-od-scout-line class="mb-s2 text-caveat text-ink-muted tnum"></p>
    <button type="button" data-od-scout class="mb-s3 cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Scout</button>
    <span data-od-scout-state class="ml-s2 text-provenance text-ink-muted"></span>
    <form data-od-request class="mb-s3 flex flex-wrap items-end gap-[6px]">
      <label class="text-caveat text-ink-muted">Formats <input name="formats" placeholder="CSV, JSON" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:120px"></label>
      <label class="text-caveat text-ink-muted">Period <input name="period" placeholder="2019-2023" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:100px"></label>
      <label class="text-caveat text-ink-muted">Places <input name="places" placeholder="Texas" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:120px"></label>
      <label class="text-caveat text-ink-muted">Largest size (MB) <input name="max_mb" type="number" min="0" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:80px"></label>
      <label class="text-caveat text-ink-muted">License needs
        <select name="need_commercial" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"><option value="">commercial: not stated</option><option value="1">commercial use needed</option></select>
        <select name="need_share_alike" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"><option value="">share-alike: not stated</option><option value="0">no share-alike</option></select></label>
      <button type="submit" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Winnow</button>
    </form>
    <div data-od-fit-result></div>
    <div data-od-profile></div>
    <div data-od-refresh></div>`;
  const line = host.querySelector('[data-od-scout-line]');
  const state = host.querySelector('[data-od-scout-state]');
  try { const p = await openDataScoutPrice(workList); line.textContent = `Scout: ${p.line}`; }
  catch (e) { line.innerHTML = errText(e); }
  host.querySelector('[data-od-scout]').addEventListener('click', async () => {
    state.textContent = 'scouting…';
    try {
      const r = await scoutOpenData(workList);
      state.textContent = `${r.scouted} scouted · ${r.could_not_scout} could not be scouted${r.rows.filter((o) => o.outcome !== 'scouted').map((o) => ` · ${o.title}: ${o.words}`).join('')}`;
    } catch (e) { state.innerHTML = errText(e); }
  });
  mountProfile(host.querySelector('[data-od-profile]'), workList);
  mountRefresh(host.querySelector('[data-od-refresh]'), workList);
  const form = host.querySelector('[data-od-request]');
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const out = host.querySelector('[data-od-fit-result]');
    out.textContent = 'Winnowing…';
    try { out.innerHTML = winnowHtml(await fitOpenData(workList, requestFromForm(form), appState.investigation || '')); watchTables(out); }
    catch (e) { out.innerHTML = `<p class="text-state-warn">${errText(e)}</p>`; }
  });
}
