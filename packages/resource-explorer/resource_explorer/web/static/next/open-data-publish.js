/* Publish and look-again for open data (OD4).
 *
 *  Look again   re-reads each dataset of the shortlist and its distributions (priced first) and says what changed
 *               on their side. The first look says it has no earlier list of distributions.
 *  Publish      writes one dataset to Egeria as the signed-in person and reads it back by GUID. Every step is a line:
 *               written, not written (and why), or failed (and why).
 */
import { openDataRefreshPrice, refreshOpenData, openDataPublishState, publishOpenData } from '/static/re-api.js';
import { esc } from '/static/next/app.js';

const errText = (e) => esc((e && (e.detail || e.message)) || String(e));

export function refreshRowsHtml(res) {
  return `<p class="mb-s2 text-answer text-ink tnum" data-od-refresh-summary>${res.changed} changed · ${res.unchanged} no change on their side · ${res.first_look} first look · ${res.could_not_refresh} could not be read</p>
    <ul class="m-0 list-none p-0">${res.rows.map((r) => `<li class="text-caveat text-ink" data-od-refresh="${esc(r.outcome)}">${esc(r.title)} · ${esc(r.words)}</li>`).join('')}</ul>`;
}

export function proofHtml(p) {
  if (!p) return '';
  const steps = p.steps.map((s) => `<li class="text-caveat text-ink-muted" data-od-publish-step="${esc(s.outcome)}">${esc(s.step)} · ${esc(s.outcome.replace(/_/g, ' '))} · ${esc(s.words)}</li>`).join('');
  const rb = p.read_back || {};
  const back = rb.matched ? `read back by GUID ${esc(p.guid)} · ${esc(rb.type_name)} ${esc(rb.qualified_name)}` : `not read back · ${esc(rb.why || 'no answer')}`;
  return `<p class="m-0 text-answer text-ink" data-od-publish-state="${p.complete ? 'complete' : 'incomplete'}">${p.complete ? 'Published' : 'Published in part'} · ${back}</p><ul class="m-0 list-none p-0">${steps}</ul>`;
}

/** Mount the look-again section for a work list into `host`. */
export async function mountRefresh(host, workList) {
  host.innerHTML = `<h5 class="m-0 mb-s2 mt-s4 text-caps uppercase tracking-caps text-ink-muted">Look again</h5>
    <p data-od-refresh-line class="mb-s2 text-caveat text-ink-muted tnum"></p>
    <button type="button" data-od-refresh-run class="mb-s3 cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Look again at the shortlist</button>
    <div data-od-refresh-result></div>`;
  const line = host.querySelector('[data-od-refresh-line]');
  const out = host.querySelector('[data-od-refresh-result]');
  try { line.textContent = `Look again: ${(await openDataRefreshPrice(workList)).line}`; } catch (e) { line.innerHTML = errText(e); }
  host.querySelector('[data-od-refresh-run]').addEventListener('click', async () => {
    out.textContent = 'looking…';
    try { out.innerHTML = refreshRowsHtml(await refreshOpenData(workList)); } catch (e) { out.innerHTML = `<p class="text-state-warn">${errText(e)}</p>`; }
  });
}

/** Mount the publish section for one dataset into `host`. */
export async function mountPublish(host, slug) {
  host.innerHTML = `<h5 class="m-0 mb-s2 mt-s4 text-caps uppercase tracking-caps text-ink-muted">Publish to Egeria</h5>
    <p data-od-publish-line class="mb-s2 text-caveat text-ink-muted tnum"></p>
    <button type="button" data-od-publish-run class="mb-s3 cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent">Publish this dataset</button>
    <div data-od-publish-result></div>`;
  const line = host.querySelector('[data-od-publish-line]');
  const out = host.querySelector('[data-od-publish-result]');
  try {
    const st = await openDataPublishState(slug);
    line.textContent = `Publish: ${st.price.line}`;
    out.innerHTML = st.proof ? proofHtml(st.proof) : `<p class="m-0 text-caveat text-ink-muted">${esc(st.words)}</p>`;
  } catch (e) { line.innerHTML = errText(e); }
  host.querySelector('[data-od-publish-run]').addEventListener('click', async () => {
    out.textContent = 'publishing…';
    try { out.innerHTML = proofHtml(await publishOpenData(slug)); } catch (e) { out.innerHTML = `<p class="text-state-warn">${errText(e)}</p>`; }
  });
}
