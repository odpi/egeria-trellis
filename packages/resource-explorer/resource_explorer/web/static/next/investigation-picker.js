/** The investigation picker and the wording of the "add to <investigation>" act.
 *
 *  REPLY-DESIGNER-WORK-LISTS-VS-INVESTIGATIONS.md §2 and §4. One reusable
 *  component: it lists the OPEN investigations plus a "start a new one" row and
 *  hands back the chosen slug. "Start a new one" is not a second creation form:
 *  the caller passes `onNew`, which opens the existing "New investigation"
 *  dialog (stages/investigation.js).
 *
 *  Callbacks, not a Promise: opening the creation dialog replaces this one
 *  (`openDialog` closes whatever dialog is up), so there is no single moment at
 *  which "the picker was dismissed" could be told apart from "it handed over to
 *  the creation dialog". A Promise that never settles would be a worse contract
 *  than a callback that simply is not called.
 */
import { openDialog, closeCellDetail } from '/static/next/worklist.js';

const esc = (s) => String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

/** Names longer than this are cut with an ellipsis (full name in the title). */
export const NAME_LIMIT = 24;

export function shortName(name) {
  const n = String(name || '');
  return n.length > NAME_LIMIT ? `${n.slice(0, NAME_LIMIT - 1)}…` : n;
}

/** Only open investigations are offered: a closed or suspended one is not a
 *  place to put new work. */
export function openInvestigations(investigations) {
  return (investigations || []).filter((i) => (i.status || 'open') === 'open');
}

/** What the "add to …" act says, and what it does.
 *   - none current            → "add to an investigation…", opens the picker
 *   - current, resource in it → "already in <name>'s scope", plain text, no action
 *   - current, not in it      → "add to <name>" */
export function scopeActWording(invName, inScope) {
  if (!invName) return { mode: 'pick', label: 'add to an investigation…', title: 'Choose an investigation first' };
  if (inScope) {
    return { mode: 'text', label: `already in ${shortName(invName)}’s scope`, title: `Already in ${invName}’s scope` };
  }
  return { mode: 'add', label: `add to ${shortName(invName)}`, title: `Add to ${invName}’s scope` };
}

/** Open the picker. `onChoose(slug)` runs with the chosen investigation's slug;
 *  `onNew()` runs for "start a new one…" (it should open the creation dialog and
 *  arrange its own continuation). Neither runs if the person just closes it. */
export function openInvestigationPicker({ investigations, onChoose, onNew, heading = 'Add to an investigation' }) {
  const open = openInvestigations(investigations);
  const d = openDialog(heading, '');
  d.setAttribute('data-investigation-picker', '');
  const body = d.querySelector('#wl-detail-body');
  body.innerHTML = `
    <p class="mb-s2 max-w-[60ch] text-caveat text-ink-muted">Pick the investigation these belong to, or start a new one.</p>
    <ul class="m-0 list-none p-0">
      ${open.map((i) => `<li class="border-t border-rule py-s2">
        <button type="button" data-pick-investigation="${esc(i.slug)}" title="${esc(i.display_name || i.slug)}"
          class="cursor-pointer bg-transparent p-0 text-answer text-ink underline">${esc(i.display_name || i.slug)}</button>
      </li>`).join('')}
      <li class="border-t border-rule py-s2">
        <button type="button" data-pick-new
          class="cursor-pointer bg-transparent p-0 text-answer text-accent-ink underline">start a new one…</button>
      </li>
    </ul>`;
  body.querySelectorAll('[data-pick-investigation]').forEach((b) => b.addEventListener('click', () => {
    closeCellDetail();
    onChoose(b.dataset.pickInvestigation);
  }));
  body.querySelector('[data-pick-new]').addEventListener('click', () => { onNew(); });
  return d;
}
