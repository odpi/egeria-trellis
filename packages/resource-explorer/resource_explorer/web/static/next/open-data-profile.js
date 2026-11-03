/* The profile section of the shortlist panel (OD3): ExternalDataProfile.
 *
 *  The price line comes first: calls at most, and the cap on each data file. A distribution over the cap is
 *  "not profiled · over the cap", never an empty row. Every profiled file shows its sample ratio and rows;
 *  documentation is "stated by <file>", a proposal, never a fact; the fingerprint says what it was compared with.
 */
import { openDataProfileView, profileOpenData } from '/static/re-api.js';
import { esc } from '/static/next/app.js';

const errText = (e) => esc((e && (e.detail || e.message)) || String(e));

export function distributionHtml(d) {
  const profiled = d.state === 'profiled';
  const cols = profiled ? `<span class="text-ink-muted"> · ${d.columns.map((c) => esc(c.column_name)).join(', ')}</span>` : '';
  return profiled
    ? `<li class="text-caveat text-ink tnum" data-od-dist="profiled">${esc(d.name)} · ${esc(d.words)} · retrieved ${esc(d.retrieved_at)}${cols}</li>`
    : `<li class="text-caveat text-ink-muted tnum" data-od-dist="not-profiled">${esc(d.name)} · ${esc(d.words)}</li>`;
}

export function documentHtml(d) {
  const passages = (d.passages || []).map((p) => `<li class="text-provenance text-ink-muted">stated by ${esc(p.stated_by)}: ${esc(p.text)}</li>`).join('');
  return `<li class="text-caveat text-ink-muted" data-od-doc>${esc(d.name)} · ${esc(d.words)}<ul class="m-0 list-none p-0 pl-s3">${passages}</ul></li>`;
}

export function profileHtml(ds) {
  const p = ds.profile;
  if (!p) return `<div class="mb-s3" data-od-profile-row="never"><h6 class="m-0 text-caveat text-ink">${esc(ds.title)}</h6><p class="m-0 text-caveat text-ink-muted">${esc(ds.words)}</p></div>`;
  const overlap = (p.fingerprint_overlap || []).map((o) => `<li class="text-caveat text-ink-muted">${esc(o.distribution)} has ${o.same ? 'the same columns as' : `${o.shared} columns in common with`} ${esc(o.with)} / ${esc(o.with_distribution)}</li>`).join('');
  const prov = p.provenance || {};
  return `<div class="mb-s3" data-od-profile-row="run"><h6 class="m-0 text-caveat text-ink">${esc(ds.title)} · ${esc(p.words)}</h6>
    <ul class="m-0 list-none p-0">${(p.distributions || []).map(distributionHtml).join('')}${(p.documents || []).map(documentHtml).join('')}</ul>
    <p class="m-0 text-provenance text-ink-muted">Came from ${esc((prov.portals || []).join(', ') || 'not reported by this source')} · DOI ${esc(prov.doi === 'not_reported' ? 'not reported by this source' : prov.doi)} · DataCite: ${esc(prov.datacite)}</p>
    <ul class="m-0 list-none p-0">${overlap}</ul><p class="m-0 text-provenance text-ink-muted">Fingerprint: ${esc(p.fingerprint_scope || 'not run')}</p></div>`;
}

async function paint(host, workList) {
  const out = host.querySelector('[data-od-profile-result]');
  try {
    const v = await openDataProfileView(workList);
    host.querySelector('[data-od-profile-line]').textContent = `Profile: ${v.price.line}`;
    out.innerHTML = v.datasets.map(profileHtml).join('');
  } catch (e) { out.innerHTML = `<p class="text-state-warn">${errText(e)}</p>`; }
}

/** Mount the profile section for one work list into `host`. */
export async function mountProfile(host, workList) {
  host.innerHTML = `<h5 class="m-0 mb-s2 mt-s4 text-caps uppercase tracking-caps text-ink-muted">Profile</h5>
    <p data-od-profile-line class="mb-s2 text-caveat text-ink-muted tnum"></p>
    <button type="button" data-od-profile-run class="mb-s3 cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Profile the shortlist</button>
    <span data-od-profile-state class="ml-s2 text-provenance text-ink-muted"></span>
    <div data-od-profile-result></div>`;
  await paint(host, workList);
  const state = host.querySelector('[data-od-profile-state]');
  host.querySelector('[data-od-profile-run]').addEventListener('click', async () => {
    state.textContent = 'profiling… this downloads files, each capped';
    try {
      const r = await profileOpenData(workList);
      state.textContent = `${r.profiled} profiled · ${r.not_profiled} not profiled`;
      await paint(host, workList);
    } catch (e) { state.innerHTML = errText(e); }
  });
}
