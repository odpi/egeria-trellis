/** The work list's two actions into an investigation, and the words they use.
 *
 *  REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md §2 (slice W1-B).
 *
 *   - "Add these N to <investigation>" / "Add N selected to <investigation>" /
 *     "Add to an investigation…" (none current: the picker opens first).
 *   - "Start an investigation from this list…": one dialog, prefilled, Start.
 *
 *  Both sit on the work list pane's action row AND on each row of the list of
 *  work lists, so they live here, not in worklist.js (the pane) or app.js (the
 *  index). The caller hands in `host`, the few shell things this needs:
 *    currentInvestigation()          → slug or ''
 *    investigations()                → the loaded investigation rows
 *    chooseInvestigationThen(fn)     → open the picker, call fn(slug)
 *    makeCurrent(slug)               → make that investigation current
 *    refreshScope()                  → re-read the current scope and repaint
 *    refreshAfterStart()             → re-read investigations and work lists
 *  and an `onDone({ noteHtml, ok })` that says what happened where it can be read.
 */
import { openDialog, closeCellDetail } from '/static/next/worklist.js';
import { shortName } from '/static/next/investigation-picker.js';
import { investigationVocab } from '/static/next/stages/investigation.js';
import {
  addWorkListToInvestigation,
  createInvestigation,
  linkWorkListInvestigation,
} from '/static/re-api.js';

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** An investigation's name, cut at 24 characters with the whole name in the title. */
const nameHtml = (name) => `<span title="${esc(name)}">${esc(shortName(name))}</span>`;

/** The button's words. The button names the investigation, never "scope" alone.
 *  `selected` is the number of rows ticked; `count` is how many the list has. */
export function addActWording({ invName, count, selected = 0 }) {
  if (!invName) {
    return { label: 'Add to an investigation…', title: 'Choose an investigation first' };
  }
  return {
    label: selected ? `Add ${selected} selected to ${shortName(invName)}` : `Add these ${count} to ${shortName(invName)}`,
    title: `Add ${selected ? `the ${selected} selected` : `these ${count}`} to ${invName}’s scope`,
  };
}

export const START_LABEL = 'Start an investigation from this list…';

const invName = (host, slug) =>
  (host.investigations().find((i) => i.slug === slug) || {}).display_name || slug;

/** "2 were already in scope; their reasons were kept." */
function keptSentence(n) {
  if (!n) return '';
  return n === 1 ? '1 was already in scope; its reason was kept.' : `${n} were already in scope; their reasons were kept.`;
}

/** Put the list's members (or the `slugs` ticked) in an investigation's scope.
 *  With none current the picker opens first; the chosen investigation becomes
 *  the current one, as it does for the Select bar. */
export function addListToInvestigation({ list, slugs = null, host, onDone }) {
  const run = async (inv) => {
    let res;
    try {
      res = await addWorkListToInvestigation(list.slug, inv, slugs);
    } catch (err) {
      onDone({ ok: false, noteHtml: `<span class="text-state-warn">Could not add: ${esc(err.message)}</span>` });
      return;
    }
    const becameCurrent = inv !== host.currentInvestigation();
    if (becameCurrent) await host.makeCurrent(inv); else await host.refreshScope();
    const name = res.investigation_name || invName(host, inv);
    const added = res.added.length;
    const kept = keptSentence(res.already_in_scope.length);
    const head = added
      ? `Added <span class="tnum">${added}</span> to ${nameHtml(name)}${becameCurrent ? ', now your current investigation' : ''}.`
      : `Nothing added to ${nameHtml(name)}${becameCurrent ? ', now your current investigation' : ''}.`;
    onDone({ ok: true, investigation: inv, result: res, noteHtml: `${head}${kept ? ` ${esc(kept)}` : ''}` });
  };
  const current = host.currentInvestigation();
  if (current) return run(current);
  return host.chooseInvestigationThen(run);
}

/** "Start an investigation from this list…" — this button, then Start. */
export async function openStartFromList({ list, host, onDone }) {
  const { purposes, classifications } = await investigationVocab();
  const count = list.member_count ?? (list.members || []).length;
  const d = openDialog('Start an investigation from this list', '');
  d.setAttribute('data-start-from-list', '');
  const body = d.querySelector('#wl-detail-body');
  body.innerHTML = `
    <label class="mb-[3px] block text-caveat text-ink-muted">Name</label>
    <input id="sfl-name" type="text" value="${esc(list.display_name || '')}"
      class="mb-s2 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-answer text-ink">
    <label class="mb-[3px] block text-caveat text-ink-muted">Description</label>
    <textarea id="sfl-desc" rows="2"
      class="mb-s2 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">${esc(list.description || '')}</textarea>
    <label class="mb-[3px] block text-caveat text-ink-muted">Purposes (optional)</label>
    <div class="mb-s2 flex flex-wrap gap-s2">
      ${purposes.map((p) => `<label class="inline-flex cursor-pointer items-center gap-[4px] text-caveat text-ink">
        <input type="checkbox" data-sfl-purpose value="${esc(p)}"> ${esc(p)}</label>`).join('')}
    </div>
    <label class="mb-[3px] block text-caveat text-ink-muted">Where it lives</label>
    <select id="sfl-binding" class="mb-s2 w-full rounded-sm border border-rule bg-paper px-2 py-1 text-caveat text-ink">
      ${classifications.bindings.map((b) => `<option value="${esc(b.name)}" ${
        b.name === classifications.default_binding ? 'selected' : ''}>${esc(b.label || b.name)}</option>`).join('')}
    </select>
    <p class="mb-s3 max-w-[60ch] text-caveat text-ink-muted">The ${esc(String(count))} on this list go into its scope with their reasons.
      The list stays, as the record of how the scope was chosen.</p>
    <div id="sfl-error" class="mb-s2 text-caveat text-state-warn"></div>
    <div class="flex gap-s2">
      <button id="sfl-start" type="button"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink">Start</button>
      <button data-act="close" type="button" class="cursor-pointer bg-transparent text-caveat text-ink-muted underline">Cancel</button>
    </div>`;
  body.querySelector('#sfl-start').addEventListener('click', async () => {
    const errEl = body.querySelector('#sfl-error');
    const name = body.querySelector('#sfl-name').value.trim();
    if (!name) { errEl.textContent = 'Name is required.'; return; }
    const btn = body.querySelector('#sfl-start');
    btn.disabled = true;
    btn.textContent = 'Starting…';
    let inv;
    try {
      inv = await createInvestigation({
        displayName: name,
        description: body.querySelector('#sfl-desc').value.trim(),
        purposes: [...body.querySelectorAll('[data-sfl-purpose]:checked')].map((c) => c.value),
        projectClassification: classifications.default_classification,
        egeriaBinding: body.querySelector('#sfl-binding').value,
      });
    } catch (err) {
      btn.disabled = false;
      btn.textContent = 'Start';
      errEl.textContent = err.message;
      return;
    }
    closeCellDetail();
    // The investigation now exists. Each later step reports its own failure
    // rather than pretending the whole thing failed or the whole thing worked.
    const problems = [];
    let res = { added: [], already_in_scope: [] };
    try { res = await addWorkListToInvestigation(list.slug, inv.slug); } catch (err) { problems.push(`could not add the members: ${err.message}`); }
    try { await linkWorkListInvestigation(list.slug, inv.slug); } catch (err) { problems.push(`could not tag the list: ${err.message}`); }
    await host.refreshAfterStart();
    await host.makeCurrent(inv.slug);
    const head = `Started ${nameHtml(inv.display_name || name)}, now your current investigation, with <span class="tnum">${res.added.length}</span> in scope.`;
    onDone({
      ok: !problems.length, investigation: inv.slug, result: res,
      noteHtml: `${head}${problems.length ? ` <span class="text-state-warn">${esc(problems.join('; '))}</span>` : ''}`,
    });
  });
  return d;
}
