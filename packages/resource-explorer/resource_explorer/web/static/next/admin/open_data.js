/* Open data (OD1): Data portals, Portal directories, and the imported datasets' own page.
 *
 * Reached from Admin → Open data. Nothing here surveys, profiles or publishes; a dataset's page offers no Run.
 *
 *  Data portals       the registered portals; Register (name, API kind, base URL, optional key); a portal opens
 *                     into its discovery-source pane: terms and filters, the PRICE LINE before the press, the
 *                     candidate table, tick rows, Import into a work list (cap shown, Admin-changeable).
 *  Portal directories a directory's Run lists portals as candidate rows; "already registered", "adapter: ckan"
 *                     or "no adapter for <kind> yet · registered for later" on each; Import registers them.
 *  Open data          the datasets held; a dataset opens into its page: the publisher's record verbatim, the
 *                     normalised fields "stated by <portal> · retrieved <time>", and the absence word for every
 *                     question a survey will answer.
 *
 * Absent fields read "not reported by this source" (the server sends the typed `not_reported`). Cues, not
 * sentences; every table is resizable (colresize.js).
 */
import {
  listOpenDataPortals, registerOpenDataPortal, checkOpenDataPortal, openDataPrice, searchOpenData, importOpenData,
  openDataLensTerms, listPortalDirectories, registerPortalDirectory, runPortalDirectory, importDirectoryPortals,
  listOpenData, getOpenData, openDataDirectoryPrice,
} from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { ago } from '/static/next/format.js';
import { watchTables } from '/static/next/colresize.js';

export const NOT_REPORTED = 'not_reported';
export const NOT_REPORTED_WORDS = 'not reported by this source';
export const NO_SURVEY_WORDS = 'Not yet run';
const SURVEY_QUESTIONS = ['License and terms', 'Description', 'Update cadence', 'Coverage', 'Formats and access',
  'Structure', 'Size and cost', 'Quality', 'Personal data', 'Provenance', 'Overlap with data held', 'Citations',
  'Change since last look'];

/** A value as the screen shows it: the typed absence becomes the words; lists join; flags become cues. */
export function show(v) {
  if (v === NOT_REPORTED || v === undefined || v === null) return `<span class="text-ink-muted">${NOT_REPORTED_WORDS}</span>`;
  if (v === true) return '✓';
  if (v === false) return '✗';
  if (Array.isArray(v)) return esc(v.join(', '));
  return esc(String(v));
}
const showDir = (v) => (v === NOT_REPORTED ? '<span class="text-ink-muted">not reported by this directory</span>' : show(v));

export function flagsCue(l) {
  if (!l) return show(NOT_REPORTED);
  const f = (name, v) => `${name} ${v === NOT_REPORTED ? '?' : v ? '✓' : '✗'}`;
  return esc(`${f('attr', l.attribution)} · ${f('share-alike', l.share_alike)} · ${f('commercial', l.commercial)}`);
}

/** A modified stamp as YYYY-MM-DD (W32, the owner: "show the year for that column"): a portal's dates can be years
 *  old, so the year is the part that matters and the time of day is not. The date is the source's own: a date alone
 *  stays as given, a date-time is cut to its date (no zone shift). The candidates column and the dataset page say it
 *  the same way. Never the raw ISO string with microseconds; unreadable text comes back as it was. */
export function modifiedWords(v) {
  if (v === NOT_REPORTED || v === undefined || v === null || v === '') return show(NOT_REPORTED);
  const text = String(v);
  return esc(/^\d{4}-\d{2}-\d{2}(?:$|[T ])/.test(text) ? text.slice(0, 10) : text);
}

export function bytes(n) {
  if (n === NOT_REPORTED || n === null || n === undefined || n === '') return show(NOT_REPORTED);
  const x = Number(n);
  if (!Number.isFinite(x)) return esc(String(n));
  if (x >= 1e6) return esc(`${(x / 1e6).toFixed(1)} MB`);
  if (x >= 1e3) return esc(`${(x / 1e3).toFixed(0)} kB`);
  return esc(`${x} B`);
}

const errText = (e) => esc((e && (e.detail || e.message)) || String(e));
/** Said under the registration form when the API is DCAT-AP. */
export const DCAT_AP_HELP = 'DCAT-AP: registering reads 1 to 3 addresses to find the catalog feed (the base URL plus /catalog.jsonld, then /data.json, then the base URL itself). A feed has no search: Resource Explorer reads whole pages and keeps the rows that match.';

const q = (host, sel) => host.querySelector(sel);

// ── Data portals ──────────────────────────────────────────────────────────────

export async function renderDataPortals(host) {
  const portals = await listOpenDataPortals();
  host.innerHTML = `
    <div class="mb-s2 flex items-center justify-between">
      <h3 class="m-0 font-heading text-name font-normal text-ink">🌐 Data portals</h3>
      <button type="button" data-od-register-toggle class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Register</button>
    </div>
    <form data-od-register class="mb-s3 hidden flex-wrap items-end gap-[6px]">
      <label class="text-caveat text-ink-muted">Name <input name="display_name" required class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"></label>
      <label class="text-caveat text-ink-muted">API <select name="api_kind" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"><option>CKAN</option><option>DCAT-AP</option></select></label>
      <label class="text-caveat text-ink-muted">Base URL <input name="base_url" required placeholder="https://demo.ckan.org" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:260px"></label>
      <label class="text-caveat text-ink-muted">API key <input name="api_key" type="password" autocomplete="off" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"></label>
      <button type="button" data-od-check class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Check</button>
      <button type="submit" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Register</button>
      <span data-od-register-state class="text-provenance text-ink-muted"></span>
      <p data-od-api-help class="m-0 hidden basis-full text-provenance text-ink-muted">${esc(DCAT_AP_HELP)}</p>
    </form>
    ${portals.length ? `<div class="overflow-x-auto"><table class="w-full text-left" data-rc-id="od-portals">
      <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
        <th class="pb-s2 pr-s3 font-normal">Portal</th><th class="pb-s2 pr-s3 font-normal">API</th><th class="pb-s2 pr-s3 font-normal">URL</th><th class="pb-s2 pr-s3 font-normal">Origin</th>
        <th class="pb-s2 pr-s3 font-normal">Cost</th><th class="pb-s2 pr-s3 font-normal">Registered</th><th class="pb-s2 pr-s3 font-normal"></th></tr></thead>
      <tbody>${portals.map((p) => `<tr class="border-b border-rule" data-od-portal="${esc(p.slug)}">
        <td class="py-s2 pr-s3 text-caveat align-top">${esc(p.display_name)}</td>
        <td class="py-s2 pr-s3 text-caveat align-top">${esc(p.api_kind)}${p.adapter_available ? '' : ' <span class="text-state-warn">· no adapter yet</span>'}</td>
        <td class="py-s2 pr-s3 text-caveat align-top font-mono text-provenance break-all" data-od-portal-url>${esc(p.base_url)}</td>
        <td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">${esc(p.origin)}</td>
        <td class="py-s2 pr-s3 text-caveat align-top tnum text-ink-muted" data-od-cost><div class="min-w-[12rem]">${p.cost && p.cost.calls_per_page ? `${p.cost.calls_per_page} call/page · ${p.cost.page_size} rows${p.cost.cost_note ? ` · ${esc(p.cost.cost_note)}` : ''}` : 'cost not declared by the adapter'}${p.has_key ? ' · key held' : ''}</div></td>
        <td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">${esc(ago(p.registered_at))}${p.registered_by ? ` · ${esc(p.registered_by)}` : ' · the person who registered it was not recorded'}</td>
        <td class="py-s2 pr-s3 text-caveat align-top">${p.adapter_available ? `<button type="button" data-od-open="${esc(p.slug)}" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Search</button>` : '<span class="text-ink-muted">registered for later</span>'}</td>
      </tr>`).join('')}</tbody></table></div>` : '<p data-od-empty class="text-answer text-ink">No portals registered yet.</p>'}
    <div data-od-pane class="mt-s4"></div>`;
  watchTables(host);
  const form = q(host, '[data-od-register]');
  q(host, '[data-od-register-toggle]').addEventListener('click', () => form.classList.toggle('hidden'));
  const apiHelp = q(host, '[data-od-api-help]');
  const showApiHelp = () => apiHelp.classList.toggle('hidden', form.elements.api_kind.value !== 'DCAT-AP');
  form.elements.api_kind.addEventListener('change', showApiHelp);
  const body = () => Object.fromEntries(new FormData(form).entries());
  const state = q(host, '[data-od-register-state]');
  q(host, '[data-od-check]').addEventListener('click', async () => {
    state.textContent = 'checking…';
    try { const r = await checkOpenDataPortal(body()); state.textContent = r.words; } catch (e) { state.innerHTML = errText(e); }
  });
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    try { await registerOpenDataPortal(body()); await renderDataPortals(host); } catch (e) { state.innerHTML = errText(e); }
  });
  host.querySelectorAll('[data-od-open]').forEach((b) => b.addEventListener('click', () => openPortalPane(host, b.dataset.odOpen)));
}

/** The discovery-source pane for one portal: terms, filters, the price line, candidates, preview, Import. */
export async function openPortalPane(host, slug, { lens = null, investigation = '', requestName = '' } = {}) {
  const pane = q(host, '[data-od-pane]');
  const suggested = lens || investigation ? await openDataLensTerms(lens || {}, investigation).catch(() => null) : null;
  pane.innerHTML = `
    <h4 class="m-0 mb-s2 font-heading text-name font-normal text-ink">Search ${esc(slug)}</h4>
    <form data-od-search class="mb-s2 flex flex-wrap items-end gap-[6px]">
      <label class="text-caveat text-ink-muted">Terms <input name="terms" value="${esc(suggested ? suggested.terms : '')}" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:240px"></label>
      <label class="text-caveat text-ink-muted">Format <input name="format" placeholder="CSV" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:80px"></label>
      <label class="text-caveat text-ink-muted">Modified after <input name="modified_after" type="date" value="${esc((suggested && suggested.filters.modified_after) || '')}" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"></label>
      <label class="text-caveat text-ink-muted">License needs
        <select name="need_commercial" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"><option value="">commercial: any</option><option value="1">commercial use needed</option></select>
        <select name="need_share_alike" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"><option value="">share-alike: any</option><option value="0">no share-alike</option></select></label>
      <label class="text-caveat text-ink-muted">Pages <input name="pages" type="number" min="1" max="20" value="2" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:56px"></label>
      <button type="submit" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Run</button>
    </form>
    <p data-od-price class="mb-s2 text-provenance text-ink-muted tnum"></p>
    ${suggested && suggested.source ? `<p class="mb-s2 text-provenance text-ink-muted">terms from the ${esc(suggested.source)}; edit them</p>` : ''}
    <div data-od-results class="overflow-x-auto"></div>`;
  const form = q(pane, '[data-od-search]');
  const price = q(pane, '[data-od-price]');
  const refreshPrice = async () => {
    try { const p = await openDataPrice(slug, Number(form.pages.value) || 1); price.textContent = `${p.line} · up to ${p.rows_at_most} rows`; }
    catch (e) { price.innerHTML = errText(e); }
  };
  form.pages.addEventListener('change', refreshPrice);
  refreshPrice();
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const f = Object.fromEntries(new FormData(form).entries());
    const filters = {};
    if (f.format) filters.format = f.format;
    if (f.modified_after) filters.modified_after = f.modified_after;
    const licence = {};
    if (f.need_commercial) licence.commercial = true;
    if (f.need_share_alike === '0') licence.share_alike = false;
    if (Object.keys(licence).length) filters.licence = licence;
    const out = q(pane, '[data-od-results]');
    out.innerHTML = '<p class="text-ink-muted">Searching…</p>';
    try {
      const res = await searchOpenData(slug, { terms: f.terms, filters, pages: Number(f.pages) || 1 });
      drawCandidates(pane, out, slug, res, f.terms, requestName, investigation, licence);
    } catch (e) { out.innerHTML = `<p class="text-state-warn">${errText(e)}</p>`; }
  });
}

/** The empty state says WHY: the portal had none for these terms, or the license needs dropped them all. */
export function noCandidatesWords(res, licence) {
  const needs = [];
  if (licence && licence.commercial) needs.push('commercial use needed');
  if (licence && licence.share_alike === false) needs.push('no share-alike');
  if (res.licence_dropped) {
    return `No candidates: the portal returned ${res.licence_dropped} row${res.licence_dropped === 1 ? '' : 's'}, and the license needs (${needs.join(', ') || 'as set'}) dropped ${res.licence_dropped === 1 ? 'it' : 'all of them'}.`;
  }
  return 'No candidates: the portal has no datasets for these terms and filters.';
}

export function drawCandidates(pane, out, slug, res, terms, requestName, investigation, licence = null) {
  const rows = res.rows;
  out.innerHTML = `
    <p class="mb-s2 text-provenance text-ink-muted tnum">${rows.length} row${rows.length === 1 ? '' : 's'} of ${show(res.total)} · ${res.calls_made} call${res.calls_made === 1 ? '' : 's'} made
      ${res.licence_dropped ? ` · ${res.licence_dropped} dropped on license` : ''}</p>
    ${rows.length ? `<table class="w-full min-w-[56rem] table-fixed text-left" data-rc-id="od-candidates">
      <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
        <th class="w-[2rem] pb-s2 pr-s3 font-normal"></th><th class="w-[14rem] pb-s2 pr-s3 font-normal">Title</th><th class="pb-s2 pr-s3 font-normal">Publisher</th><th class="pb-s2 pr-s3 font-normal">License</th>
        <th class="pb-s2 pr-s3 font-normal">Flags</th><th class="pb-s2 pr-s3 font-normal">Modified</th><th class="pb-s2 pr-s3 font-normal">Formats</th><th class="pb-s2 pr-s3 font-normal">Size</th>
        <th class="pb-s2 pr-s3 font-normal">Identifier</th><th class="pb-s2 pr-s3 font-normal">Portal</th></tr></thead>
      <tbody>${rows.map((r, i) => `<tr class="border-b border-rule" data-od-row="${i}">
        <td class="py-s2 pr-s3 text-caveat align-top"><input type="checkbox" data-od-pick="${i}"></td>
        <td class="py-s2 pr-s3 text-caveat align-top">${show(r.title)}</td><td class="py-s2 pr-s3 text-caveat align-top">${show(r.publisher)}</td>
        <td class="py-s2 pr-s3 text-caveat align-top">${show(r.licence_id)}</td>
        <td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">${r.licence_check ? `<span class="text-state-warn">${esc(r.licence_check)}</span>` : flagsCue(r.licence)}</td>
        <td class="py-s2 pr-s3 text-caveat align-top">${modifiedWords(r.modified)}</td><td class="py-s2 pr-s3 text-caveat align-top">${show(r.formats)}</td><td class="py-s2 pr-s3 text-caveat align-top tnum">${bytes(r.size)}</td>
        <td class="py-s2 pr-s3 text-caveat align-top font-mono text-provenance break-all" data-od-identifier>${show(r.identifier)}${r.doi === NOT_REPORTED ? ' <span class="text-ink-muted">· no DOI reported</span>' : ''}</td>
        <td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">${esc(r.portal_name)}</td></tr>`).join('')}</tbody></table>
      <form data-od-import class="mt-s3 flex flex-wrap items-end gap-[6px]">
        <label class="text-caveat text-ink-muted">Work list <input name="work_list_name" value="${esc(requestName || terms || 'open data')}" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:240px"></label>
        <button type="submit" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Import chosen</button>
        <span data-od-import-price class="text-provenance text-ink-muted tnum">0 chosen · 0 calls</span>
        <span class="text-provenance text-ink-muted">at most 25 per import</span>
      </form><div data-od-import-result class="mt-s3"></div>` : `<p data-od-none class="text-answer text-ink">${esc(noCandidatesWords(res, licence))}</p>`}`;
  watchTables(out);
  const form = q(out, '[data-od-import]');
  if (!form) return;
  const picked = () => [...out.querySelectorAll('[data-od-pick]:checked')].map((c) => rows[Number(c.dataset.odPick)]);
  out.querySelectorAll('[data-od-pick]').forEach((c) => c.addEventListener('change', () => {
    const n = picked().length;
    q(out, '[data-od-import-price]').textContent = `${n} chosen · ${n} call${n === 1 ? '' : 's'} (one read per dataset)`;
  }));
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const chosen = picked().map((r) => ({ portal_slug: r.portal_slug, dataset_id: r.dataset_id }));
    const result = q(out, '[data-od-import-result]');
    if (!chosen.length) { result.textContent = 'Choose at least one row.'; return; }
    result.textContent = 'Importing…';
    try {
      const r = await importOpenData(slug, { rows: chosen, work_list_name: form.work_list_name.value, terms, investigation });
      result.innerHTML = importResultHtml(r);
      document.dispatchEvent(new CustomEvent('re:open-data-imported', { detail: { work_list: r.work_list } }));
      watchTables(result);
    } catch (e) { result.innerHTML = `<p class="text-state-warn">${errText(e)}</p>`; }
  });
}

export function importResultHtml(r) {
  const cue = (o) => (o.outcome === 'imported' ? '✓' : o.outcome === 'already_in_catalog' ? '=' : '✗');
  return `<p class="mb-s2 text-answer text-ink tnum">${r.imported} imported · ${r.already_in_catalog} already in the catalog${r.refused ? ` · ${r.refused} refused` : ''}
      ${r.work_list ? ` · work list <span class="font-mono">${esc(r.work_list)}</span>` : ''}</p>
    <table class="w-full text-left" data-rc-id="od-import-result"><thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
      <th class="pb-s2 pr-s3 font-normal"></th><th class="pb-s2 pr-s3 font-normal">Title</th><th class="pb-s2 pr-s3 font-normal">Identifier</th><th class="pb-s2 pr-s3 font-normal">Portals</th><th class="pb-s2 pr-s3 font-normal">Result</th><th class="pb-s2 pr-s3 font-normal">DataCite</th></tr></thead>
    <tbody>${r.rows.map((o) => `<tr class="border-b border-rule" data-od-result="${esc(o.outcome)}">
      <td class="py-s2 pr-s3 text-caveat align-top">${cue(o)}</td><td class="py-s2 pr-s3 text-caveat align-top">${show(o.title)}</td><td class="py-s2 pr-s3 text-caveat align-top font-mono text-provenance">${esc(o.identifier || o.dataset_id || '')}</td>
      <td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">${esc((o.portals || [o.portal]).join(', '))}</td>
      <td class="py-s2 pr-s3 text-caveat align-top ${o.outcome === 'refused' ? 'text-state-warn' : ''}">${esc(o.words)}</td>
      <td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">${esc(o.datacite || '')}</td></tr>`).join('')}</tbody></table>`;
}

// ── Portal directories ────────────────────────────────────────────────────────

export async function renderPortalDirectories(host) {
  const dirs = await listPortalDirectories();
  host.innerHTML = `
    <div class="mb-s2 flex items-center justify-between">
      <h3 class="m-0 font-heading text-name font-normal text-ink">📚 Portal directories</h3>
      <button type="button" data-od-register-toggle class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Register</button>
    </div>
    <form data-od-register class="mb-s3 hidden flex-wrap items-end gap-[6px]">
      <label class="text-caveat text-ink-muted">Name <input name="display_name" required class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" value="dataportals.org"></label>
      <label class="text-caveat text-ink-muted">API <select name="api_kind" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"><option>dataportals.org</option><option>re3data</option></select></label>
      <label class="text-caveat text-ink-muted">Listing URL <input name="base_url" placeholder="https://dataportals.org/api/data.json" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:300px"></label>
      <button type="submit" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Register</button><span data-od-register-state class="text-provenance text-ink-muted"></span>
    </form>
    ${dirs.length ? `<table class="w-full text-left" data-rc-id="od-directories"><thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
      <th class="pb-s2 pr-s3 font-normal">Directory</th><th class="pb-s2 pr-s3 font-normal">API</th><th class="pb-s2 pr-s3 font-normal">URL</th><th class="pb-s2 pr-s3 font-normal">Last run</th><th class="pb-s2 pr-s3 font-normal"></th></tr></thead>
      <tbody>${dirs.map((d) => `<tr class="border-b border-rule"><td class="py-s2 pr-s3 text-caveat align-top">${esc(d.display_name)}</td><td class="py-s2 pr-s3 text-caveat align-top">${esc(d.api_kind)}</td>
        <td class="py-s2 pr-s3 text-caveat align-top font-mono text-provenance">${esc(d.base_url)}</td><td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">${d.last_run_at ? esc(ago(d.last_run_at)) : 'not yet run'}</td>
        <td class="py-s2 pr-s3 text-caveat align-top"><button type="button" data-od-run="${esc(d.slug)}" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Run</button></td></tr>`).join('')}</tbody></table>`
      : '<p data-od-empty class="text-answer text-ink">No directories registered yet.</p>'}
    <div data-od-pane class="mt-s4"></div>`;
  watchTables(host);
  const form = q(host, '[data-od-register]');
  q(host, '[data-od-register-toggle]').addEventListener('click', () => form.classList.toggle('hidden'));
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    try { await registerPortalDirectory(Object.fromEntries(new FormData(form).entries())); await renderPortalDirectories(host); }
    catch (e) { q(host, '[data-od-register-state]').innerHTML = errText(e); }
  });
  host.querySelectorAll('[data-od-run]').forEach((b) => b.addEventListener('click', () => runDirectoryPane(host, b.dataset.odRun)));
}

async function runDirectoryPane(host, slug) {
  const pane = q(host, '[data-od-pane]');
  pane.innerHTML = `<form data-od-dirrun class="mb-s2 flex flex-wrap items-end gap-[6px]">
      <label class="text-caveat text-ink-muted">Filter <input name="terms" placeholder="name, country, API" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink"></label>
      <label class="text-caveat text-ink-muted">Pages <input name="pages" type="number" min="1" max="20" value="8" class="rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink" style="width:56px"></label>
      <button type="submit" class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Run</button><span data-od-dirprice class="text-provenance text-ink-muted tnum"></span></form>
    <div data-od-results></div>`;
  const form = q(pane, '[data-od-dirrun]');
  const refreshDirPrice = async () => {
    const out = q(pane, '[data-od-dirprice]');
    try { out.textContent = (await openDataDirectoryPrice(slug, Number(form.pages.value) || 1)).line; }
    catch (e) { out.innerHTML = errText(e); }
  };
  form.pages.addEventListener('change', refreshDirPrice);
  refreshDirPrice();
  form.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    const out = q(pane, '[data-od-results]');
    out.innerHTML = '<p class="text-ink-muted">Running…</p>';
    try {
      const res = await runPortalDirectory(slug, { pages: Number(form.pages.value) || 1, terms: form.terms.value });
      out.innerHTML = `<p class="mb-s2 text-provenance text-ink-muted tnum">${res.rows.length} of ${show(res.total)} · ${esc(res.price.line)}</p>
        <table class="w-full text-left" data-rc-id="od-dir-candidates"><thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
          <th class="pb-s2 pr-s3 font-normal"></th><th class="pb-s2 pr-s3 font-normal">Portal</th><th class="pb-s2 pr-s3 font-normal">Country</th><th class="pb-s2 pr-s3 font-normal">Domain</th><th class="pb-s2 pr-s3 font-normal">API as stated</th>
          <th class="pb-s2 pr-s3 font-normal">URL</th><th class="pb-s2 pr-s3 font-normal">Datasets</th><th class="pb-s2 pr-s3 font-normal">State</th></tr></thead>
        <tbody>${res.rows.map((r, i) => `<tr class="border-b border-rule" data-od-dirrow="${i}">
          <td class="py-s2 pr-s3 text-caveat align-top"><input type="checkbox" data-od-pick="${i}" ${r.already_registered || r.url === NOT_REPORTED ? 'disabled' : ''}></td>
          <td class="py-s2 pr-s3 text-caveat align-top">${showDir(r.name)}</td><td class="py-s2 pr-s3 text-caveat align-top">${showDir(r.country)}</td><td class="py-s2 pr-s3 text-caveat align-top">${showDir(r.domain)}</td>
          <td class="py-s2 pr-s3 text-caveat align-top">${showDir(r.api_kind)}</td><td class="py-s2 pr-s3 text-caveat align-top font-mono text-provenance">${showDir(r.url)}</td>
          <td class="py-s2 pr-s3 text-caveat align-top">${showDir(r.dataset_count)}</td>
          <td class="py-s2 pr-s3 text-caveat align-top ${r.adapter ? '' : 'text-ink-muted'}">${esc(r.status)}</td></tr>`).join('')}</tbody></table>
        <button type="button" data-od-dirimport class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent mt-s3">Register chosen</button><div data-od-import-result class="mt-s3"></div>`;
      watchTables(out);
      q(out, '[data-od-dirimport]').addEventListener('click', async () => {
        const urls = [...out.querySelectorAll('[data-od-pick]:checked')].map((c) => res.rows[Number(c.dataset.odPick)].url);
        const target = q(out, '[data-od-import-result]');
        if (!urls.length) { target.textContent = 'Choose at least one portal.'; return; }
        try {
          const r = await importDirectoryPortals(slug, { urls });
          target.innerHTML = `<p class="text-answer text-ink tnum">${r.registered} registered</p><ul class="m-0 list-none p-0">${r.rows.map((o) => `<li class="text-caveat">${o.outcome === 'registered' ? '✓' : '='} ${esc(o.name || o.url)} · ${esc(o.words)}</li>`).join('')}</ul>`;
        } catch (e) { target.innerHTML = `<p class="text-state-warn">${errText(e)}</p>`; }
      });
    } catch (e) { out.innerHTML = `<p class="text-state-warn">${errText(e)}</p>`; }
  });
}

// ── Open data (the datasets) and the resource page ────────────────────────────

export async function renderOpenData(host) {
  const rows = await listOpenData();
  host.innerHTML = `<h3 class="m-0 mb-s2 font-heading text-name font-normal text-ink">🗂 Open data</h3>
    ${rows.length ? `<table class="w-full text-left" data-rc-id="od-datasets"><thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
      <th class="pb-s2 pr-s3 font-normal">Title</th><th class="pb-s2 pr-s3 font-normal">Identifier</th><th class="pb-s2 pr-s3 font-normal">Publisher</th><th class="pb-s2 pr-s3 font-normal">License</th><th class="pb-s2 pr-s3 font-normal">Portals</th><th class="pb-s2 pr-s3 font-normal">Work list</th><th class="pb-s2 pr-s3 font-normal">Imported</th></tr></thead>
      <tbody>${rows.map((d) => `<tr class="border-b border-rule"><td class="py-s2 pr-s3 text-caveat align-top"><a href="#" data-od-dataset="${esc(d.slug)}" class="text-accent-ink">${show(d.title)}</a></td>
        <td class="py-s2 pr-s3 text-caveat align-top font-mono text-provenance">${esc(d.identifier)}</td><td class="py-s2 pr-s3 text-caveat align-top">${show(d.publisher)}</td><td class="py-s2 pr-s3 text-caveat align-top">${show(d.licence_id)}</td>
        <td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">${esc(d.portals.join(', '))}</td><td class="py-s2 pr-s3 text-caveat align-top font-mono text-provenance">${esc(d.work_list_slug)}</td>
        <td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">${esc(ago(d.imported_at))}</td></tr>`).join('')}</tbody></table>` : '<p data-od-empty class="text-answer text-ink">Nothing imported yet.</p>'}
    <div data-od-pane class="mt-s4"></div>`;
  watchTables(host);
  host.querySelectorAll('[data-od-dataset]').forEach((a) => a.addEventListener('click', async (ev) => {
    ev.preventDefault();
    q(host, '[data-od-pane]').innerHTML = datasetPageHtml(await getOpenData(a.dataset.odDataset));
    watchTables(q(host, '[data-od-pane]'));
  }));
}

/** The dataset's page: normalised fields with who stated them and when, the survey absence word, the record verbatim. */
export function datasetPageHtml(d) {
  const who = (d.stated_by || d.portals || []).join(', ');
  const when = `retrieved ${d.retrieved_at ? ago(d.retrieved_at) : 'at a time that was not recorded'}`;
  // An absence is not "stated by" anyone: the source says it gave no value.
  const field = (label, value, absent = false) => `<tr class="border-b border-rule"><td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">${esc(label)}</td><td class="py-s2 pr-s3 text-caveat align-top">${value}</td>
      <td class="py-s2 pr-s3 text-caveat align-top text-provenance text-ink-muted">${esc(absent ? `${who} gave no value · ${when}` : `stated by ${who} · ${when}`)}</td></tr>`;
  const gone = (v) => v === NOT_REPORTED || v === undefined || v === null;
  const dc = d.datacite;
  return `<div data-od-page>
    <h4 class="m-0 mb-s2 font-heading text-name font-normal text-ink">${show(d.title)}</h4>
    <p class="mb-s2 text-provenance text-ink-muted">${d.identifier_kind === 'doi' ? '' : 'no DOI reported · '}${esc(d.identifier_kind === 'doi' ? 'DOI' : d.identifier_kind === 'dcat_identifier' ? 'publisher id' : 'portal id')} <span class="font-mono break-all" data-od-identifier>${esc(d.identifier)}</span>
      · imported ${esc(ago(d.imported_at))}${d.imported_by ? ` by ${esc(d.imported_by)}` : ' (the person who imported it was not recorded)'} into <span class="font-mono">${esc(d.work_list_slug)}</span></p>
    <table class="w-full text-left" data-rc-id="od-fields"><thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
      <th class="pb-s2 pr-s3 font-normal">Field</th><th class="pb-s2 pr-s3 font-normal">Value</th><th class="pb-s2 pr-s3 font-normal">Source</th></tr></thead><tbody>
      ${field('Publisher', show(d.publisher), gone(d.publisher))}${field('License', show(d.licence_id), gone(d.licence_id))}${field('License flags', flagsCue(d.licence), !d.licence)}
      ${field('Modified', modifiedWords(d.modified), gone(d.modified))}${field('Formats', show(d.formats), gone(d.formats))}${field('Size', bytes(d.size), gone(d.size))}
      ${dc ? `<tr class="border-b border-rule"><td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">DataCite publisher</td><td class="py-s2 pr-s3 text-caveat align-top">${show(dc.publisher)}</td><td class="py-s2 pr-s3 text-caveat align-top text-provenance text-ink-muted">stated by DataCite</td></tr>
        <tr class="border-b border-rule"><td class="py-s2 pr-s3 text-caveat align-top text-ink-muted">Related identifiers</td><td class="py-s2 pr-s3 text-caveat align-top">${show((dc.related_identifiers === NOT_REPORTED ? NOT_REPORTED : dc.related_identifiers.map((r) => `${r.relation} ${r.identifier}`)))}</td><td class="py-s2 pr-s3 text-caveat align-top text-provenance text-ink-muted">stated by DataCite</td></tr>` : ''}
    </tbody></table>
    <h5 class="m-0 mb-s2 mt-s3 text-caps uppercase tracking-caps text-ink-muted">Questions</h5>
    <p data-od-survey class="m-0 text-caveat text-ink-muted">${NO_SURVEY_WORDS}. It will answer: ${esc(SURVEY_QUESTIONS.join(', '))}.</p>
    <details class="mt-s3"><summary class="cursor-pointer text-caveat text-ink">The publisher's record, verbatim</summary>
      <pre data-od-record class="mt-s2 overflow-auto whitespace-pre-wrap font-mono text-provenance text-ink" style="max-height:420px">${esc(JSON.stringify(d.record, null, 2))}</pre></details>
  </div>`;
}
