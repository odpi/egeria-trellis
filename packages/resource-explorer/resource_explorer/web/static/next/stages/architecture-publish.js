/* Publish for a repository's architecture -- the one control that writes accepted components and blueprints
 * to Egeria (owner, 2026-10-09: "Publish should be the verb to save to Egeria").
 *
 * Accept and Reject on the tree and the blueprint list are decisions only. This is where they take effect:
 *
 *   before the press   the list of what will be written, read from the SAME plan the run uses
 *                      (GET .../architecture/publish-plan). The control names its object and count:
 *                      "Publish 3 components · 1 blueprint"; only the parts that exist.
 *   after the press    per-item results from the proof rows the run wrote: done, skipped (already there),
 *                      failed (with Egeria's sentence), partial (written, not all of it confirmed), not permitted
 *                      (the presser may not change it: Egeria's zones, Brief Z; the rest of the press goes on).
 *   a reject           writes nothing: RE cannot remove an element. A rejected item already in Egeria is said to
 *                      be "still in Egeria" (a count, here; the word is on the row).
 *
 * THE RULE the rest of this band keeps: a status word comes from what the server derived from rows that prove
 * it, never from the click. Every action re-reads the plan afterwards and draws from the re-read.
 */
import { stateEntry } from '/static/next/glyphs.js';
import { getArchitecturePublishPlan, postArchitecturePublish, pollActivity } from '/static/re-api.js';
import { state, esc } from '/static/next/app.js';

/** The event the tree and the blueprint list fire after a verdict is recorded: the plan is re-read. */
export const ARCHITECTURE_CHANGED = 're:architecture-changed';

const cue = (stateKey, word, title = '') => {
  const e = stateEntry(stateKey);
  const open = e.tone === 'text-state-ok' ? '<span class="text-state-ok"'
    : e.tone === 'text-state-warn' ? '<span class="text-state-warn"' : '<span class="text-ink-muted"';
  return `${open} data-cue="${esc(stateKey)}" title="${esc(title || e.word)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(word)}</span>`;
};
const num = (n) => `<span class="tnum">${esc(n ?? 0)}</span>`;
const whoAmI = () => (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';

/** Result status -> the cue the row shows. */
const RESULT_CUE = { done: 'measured', skipped: 'unrun', failed: 'error', partial: 'partial', not_permitted: 'no_access' };
const RESULT_WORD = { done: 'done', skipped: 'already there', failed: 'failed', partial: 'partial', not_permitted: 'not permitted' };
/** The words after the cue: a refused row's stored words repeat the cue's word, so it is said once. */
const resultWords = (r) => (r.status === 'not_permitted' ? String(r.words || '').replace(/^not permitted · /, '') : r.words || '');
const SHOWN = 8;

/** The list of what will be written, then the press, then the last press's results. `plan` is the server's
 *  answer; every field may be missing (an older server), and a missing field draws as nothing. */
export function architecturePublishHtml(plan, { signedIn = true, pending = false } = {}) {
  const comps = plan?.components?.to_write || [];
  const bps = plan?.blueprints?.to_write || [];
  const held = (plan?.components?.in_egeria || 0) + (plan?.blueprints?.in_egeria || 0);
  const still = (plan?.components?.rejected_in_egeria || 0) + (plan?.blueprints?.rejected_in_egeria || 0);
  const nothing = !comps.length && !bps.length;
  const label = plan?.label || (nothing ? 'Nothing to publish' : 'Publish');
  const why = !signedIn ? 'sign in to publish — it needs an author' : nothing ? 'nothing accepted is waiting' : '';
  const list = nothing ? '' : `<div class="mt-s1 text-caveat" data-architecture-will-write>
      <div class="text-provenance text-ink-muted">will be written to Egeria</div>
      ${comps.slice(0, SHOWN).map((c) => `<div data-will-component="${esc(c.path)}" class="py-[1px]">${cue('unrun', 'component')}
        <span class="font-mono text-ink">${esc(c.name || c.path)}</span>${c.type ? ` <span class="text-provenance text-ink-muted">· ${esc(c.type)}</span>` : ''}</div>`).join('')}
      ${comps.length > SHOWN ? `<div class="py-[1px] text-provenance text-ink-muted">and ${num(comps.length - SHOWN)} more component${comps.length - SHOWN === 1 ? '' : 's'}</div>` : ''}
      ${bps.map((b) => `<div data-will-blueprint="${esc(b.key)}" class="py-[1px]">${cue('unrun', 'blueprint')}
        <span class="text-ink">${esc(b.name || b.key)}</span> <span class="text-provenance text-ink-muted">· ${
          b.state === 'finish' ? `already there${[
            b.unattached ? `${num(b.unattached)} member${b.unattached === 1 ? '' : 's'} to attach` : '',
            b.unconfirmed_compositions ? `${num(b.unconfirmed_compositions)} composition${b.unconfirmed_compositions === 1 ? '' : 's'} to confirm` : '',
          ].filter(Boolean).map((t) => ` · ${t}`).join('')}` : 'new'}</span></div>`).join('')}
    </div>`;
  // A second blueprint of a kind with no identifier would be refused at the write: it is held back, and said.
  const heldBack = plan?.blueprints?.needs_identifier || [];
  const held_html = heldBack.length ? `<div class="mt-s1 text-caveat" data-architecture-held-back>
      <div class="text-provenance text-ink-muted">held back · not written by this press</div>
      ${heldBack.map((h) => `<div data-held-blueprint="${esc(h.key)}" class="py-[1px]">${cue('partial', 'needs an identifier', h.words || '')}
        <span class="text-ink">${esc(h.name || h.key)}</span>${h.words ? ` <span class="text-provenance text-ink-muted">· ${esc(h.words)}</span>` : ''}</div>`).join('')}
    </div>` : '';
  const check = plan?.blueprints?.check_egeria || [];
  const check_html = check.length ? `<div class="mt-s1 text-caveat" data-architecture-check-egeria>
      ${check.map((c) => `<div data-check-blueprint="${esc(c.key)}" class="py-[1px]">${cue('partial', 'check Egeria', c.words || '')}
        <span class="text-ink">${esc(c.name || c.key)}</span> <span class="text-provenance text-ink-muted">· ${esc(c.words || '')}</span></div>`).join('')}
    </div>` : '';
  const last = plan?.last?.items || [];
  const missing = plan?.last?.missing ? `<div class="mt-s2 text-caveat" data-architecture-no-results>${cue('error', 'the last publish left no results', 'The last publish ended before it wrote its results, so nothing from an earlier publish is shown as current.')}</div>` : '';
  const results = last.length ? `<div class="mt-s2 text-caveat" data-architecture-results>
      <div class="text-provenance text-ink-muted">last publish</div>
      ${last.map((r) => `<div data-result="${esc(r.key)}" data-result-status="${esc(r.status)}" class="py-[1px]">
        ${cue(RESULT_CUE[r.status] || 'unrun', RESULT_WORD[r.status] || r.status, r.words || '')}
        <span class="text-ink">${esc(r.name || r.key)}</span>${resultWords(r) && r.status !== 'done' ? ` <span class="text-provenance text-ink-muted">· ${esc(resultWords(r))}</span>` : ''}</div>`).join('')}
    </div>` : '';
  return `<div data-architecture-publish>
    <div class="text-caveat text-ink">${held ? `${num(held)} in Egeria` : 'nothing in Egeria yet'}${
      still ? ` <span class="text-provenance text-ink-muted">· ${num(still)} rejected but still in Egeria (RE removes nothing)</span>` : ''}</div>
    ${list}${held_html}${check_html}
    <div class="mt-s2 flex flex-wrap items-baseline gap-s3">
      <button type="button" data-architecture-go ${nothing || !signedIn || pending ? 'disabled' : ''}
        title="Writes what is listed above: the accepted components, then the accepted blueprints. Nothing is written until this is pressed."
        class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60">${esc(nothing ? label : `${label} →`)}</button>
      <span data-architecture-feedback class="text-provenance text-ink-muted">${pending ? cue('running', 'publishing') : esc(why)}</span>
    </div>
    ${results}${missing}
  </div>`;
}

/** Draw the architecture Publish into `host` and keep it current. Never throws into the pane: a failure says
 *  so in its own slot. The plan is re-read when a verdict changes (ARCHITECTURE_CHANGED). */
export async function mountArchitecturePublish(host, slug) {
  if (!host) return null;
  const alive = () => host.isConnected && slug === state.selectedSlug;
  let plan = null;
  let pending = false;
  const draw = (feedback = '') => {
    if (!alive()) return;
    host.innerHTML = architecturePublishHtml(plan, { signedIn: !!whoAmI(), pending });
    if (feedback) host.querySelector('[data-architecture-feedback]').innerHTML = feedback;
    host.querySelector('[data-architecture-go]')?.addEventListener('click', press);
  };
  const reread = async (feedback = '') => {
    try { plan = await getArchitecturePublishPlan(slug); }
    catch (err) {
      if (alive()) host.innerHTML = `<div class="text-caveat text-state-warn" data-architecture-error>What Publish would write could not be read: ${esc(err.message)}
        <button type="button" data-architecture-retry class="cursor-pointer bg-transparent p-0 text-accent-ink underline">retry</button></div>`;
      host.querySelector('[data-architecture-retry]')?.addEventListener('click', () => reread());
      return;
    }
    draw(feedback);
  };
  async function press() {
    if (pending) return;                                  // a second press is ignored
    pending = true;
    draw();
    try {
      const out = await postArchitecturePublish(slug);
      if (out && out.activity_id) await pollActivity(out.activity_id);
      pending = false;
      await reread();
    } catch (err) {
      pending = false;
      await reread();
      const fb = host.querySelector('[data-architecture-feedback]');
      if (fb) fb.innerHTML = err.status === 409 ? cue('running', 'a publish is already running for this repository')
        : err.status === 401 ? cue('unrun', 'sign in to publish — it needs an author')
          : err.status === 403 ? cue('no_access', 'not permitted', err.message) + ` <span class="text-provenance text-ink-muted">· ${esc(err.message)}</span>`
            : cue('error', `not published · ${err.message}`);
    }
  }
  const onChange = () => { if (!host.isConnected) { document.removeEventListener(ARCHITECTURE_CHANGED, onChange); return; } if (slug === state.selectedSlug && !pending) reread(); };
  document.addEventListener(ARCHITECTURE_CHANGED, onChange);
  await reread();
  return { reread };
}
