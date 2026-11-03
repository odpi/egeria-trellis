/* Delete an investigation: the Delete control on the investigation page, its in-place confirm, the two Egeria
 * choices for a promoted investigation, and the persistent cue when Egeria stops the delete part-way.
 *
 * Owner ruling 2026-10-21: delete is permanent in Resource Explorer; for an investigation promoted to an Egeria
 * Project the person is asked each time whether Egeria's copy is ARCHIVED too (restorable; never deleted). Before
 * the confirm the page asks the server what is anchored to the Project (a read), and names every item other people
 * made there, by type, author and date: they are archived with the Project. While the ISSUE-117 archive/delete block
 * is on, the Egeria choice is shown disabled with the block's own sentence. An investigation that was never promoted
 * has one choice. Who may: its owner, or a Portal admin or curator (the server decides; this only decides whether to
 * offer the button).
 *
 * DELETE_WORDS is the WORDS table for the designer: every placeholder sentence on this control is here. */
import { deleteInvestigation, previewInvestigationDelete } from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { inPageConfirm, inPageChoice } from '/static/next/confirm-dialog.js';
import { cue } from '/static/next/stages/repo-manifest.js';

export const DELETE_WORDS = {
  button: 'Delete…',
  buttonTitle: 'Delete this investigation permanently',
  title: (name) => `Delete '${name}'?`,
  permanent: 'This is permanent. There is no undo.',
  // never promoted: one choice
  localOnly: 'It was never promoted to Egeria, so nothing in Egeria is touched. Its scope, dispositions and journal '
    + 'are removed from Resource Explorer.',
  localOnlyConfirm: 'Delete',
  // promoted: two choices, each with what it leaves behind
  choiceRe: 'Delete in Resource Explorer only',
  choiceReBody: 'Removes the investigation, its scope, dispositions and journal from Resource Explorer. '
    + 'In Egeria the Project, its working-set Collection and its journal Notes stay. '
    + 'If you later create an investigation with the same name and promote it, it will collide with the Project '
    + 'still in Egeria.',
  choiceBoth: 'Delete in Resource Explorer, archive in Egeria',
  choiceBothBody: 'Archives the Project in Egeria (restorable); its notes, ratings and comments go with it. '
    + 'Then removes the investigation from Resource Explorer. The resources in the Collection are only taken out of it; '
    + 'they are not touched. A Project that Resource Explorer did not create is left in Egeria. '
    + 'If Egeria refuses a step it stops: nothing is deleted in Resource Explorer, and this page says what is left.',
  checking: 'checking what is attached to the Project…',
  foreignHeading: (n) => `${n} item${n === 1 ? '' : 's'} on the Project that other people made. `
    + 'They are archived with it, and can be restored:',
  foreignNone: 'Nothing on the Project was made by anyone else.',
  foreignMore: (n) => `and ${n} more`,
  foreignBy: 'by',
  previewFailed: (why) => `Could not read what is attached to the Project, so archiving is not offered: ${why}`,
  cancel: 'Cancel',
  // while and after
  working: 'deleting…',
  stopTitle: 'Delete did not finish',
  stopCue: 'delete not finished',
  stopKept: 'The investigation is still in Resource Explorer.',
  stopDeleted: 'Archived in Egeria',
  stopRemains: 'Not archived in Egeria',
  stopUnlinked: 'Taken out of a Collection (the resource itself is untouched)',
  stopForeign: 'Other people\'s items on the Project (archived with it)',
  stopAuthor: 'by',
  stopLeft: 'Left alone',
  stopNothing: 'nothing',
  failedCue: 'not deleted',
  notPermittedCue: 'not permitted',
  doneRe: (name) => `Deleted '${name}' in Resource Explorer.`,
  doneBoth: (name) => `Deleted '${name}' in Resource Explorer and archived it in Egeria (restorable).`,
  leftBehind: 'Left in Egeria',
  archivedIn: 'Archived in Egeria',
};

/** Whether to OFFER Delete: the investigation is the signed-in person's own, or their Portal role is curator or
 *  admin. The server enforces the same rule; a button shown here that it refuses says so in words. Pure. */
export function mayOfferDelete(inv, me) {
  if (inv && inv.is_mine) return true;
  const role = String((me && me.role) || '').toLowerCase();
  return role === 'curator' || role === 'admin';
}

export function deleteButtonHtml() {
  return `<button data-act="inv-delete" type="button" title="${esc(DELETE_WORDS.buttonTitle)}"
    class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-state-warn hover:border-accent disabled:cursor-default disabled:opacity-60">${esc(DELETE_WORDS.button)}</button>`;
}

const listOf = (items) => (items || []).map((x) => `<li>${esc(x.kind)} <span class="tnum">${esc(x.guid)}</span>${
  x.name ? ` · ${esc(x.name)}` : ''}${x.author ? ` · ${esc(DELETE_WORDS.stopAuthor)} ${esc(x.author)}` : ''}${
  x.created ? ` · ${esc(x.created)}` : ''}${x.words ? ` · ${esc(x.words)}` : ''}${x.note ? ` · ${esc(x.note)}` : ''}${
  x.why ? ` · ${esc(x.why)}` : ''}</li>`).join('');

/** The other-people's-items list for the confirm, as plain lines (the dialog's message is text): kind, author, date,
 *  name. At most 15 lines; the rest are counted (the token that goes back with the delete covers all of them). */
export function foreignLines(foreign) {
  const W = DELETE_WORDS;
  const list = foreign || [];
  if (!list.length) return W.foreignNone;
  const line = (f) => `- ${f.kind}${f.author ? ` ${W.foreignBy} ${f.author}` : ''}${f.created ? ` · ${f.created}` : ''}${
    f.name ? ` · ${f.name}` : ''}`;
  const shown = list.slice(0, 15).map(line);
  if (list.length > 15) shown.push(`- ${W.foreignMore(list.length - 15)}`);
  return `${W.foreignHeading(list.length)}\n${shown.join('\n')}`;
}

/** The persistent cue for a delete that stopped in Egeria: what was removed, what remains with Egeria's words. */
export function stopHtml(result) {
  const W = DELETE_WORDS;
  const errs = (result.errors || []).map((e) => `<li>${esc(e)}</li>`).join('');
  return `<div data-inv-delete-stop class="mt-s2 max-w-[80ch] rounded-sm border border-rule p-s2 text-caveat">
    <div>${cue('error', W.stopCue, (result.errors || []).join(' '))} ${esc(W.stopKept)}</div>
    ${errs ? `<ul class="m-0 mt-s1 list-none p-0 text-state-warn" data-inv-delete-errors>${errs}</ul>` : ''}
    <div class="mt-s1 text-ink-muted">${esc(W.stopDeleted)}:</div>
    <ul class="m-0 list-none p-0" data-inv-delete-removed>${listOf(result.archived) || `<li>${esc(W.stopNothing)}</li>`}</ul>
    <div class="mt-s1 text-state-warn">${esc(W.stopRemains)}:</div>
    <ul class="m-0 list-none p-0" data-inv-delete-remains>${listOf(result.remaining) || `<li>${esc(W.stopNothing)}</li>`}</ul>
    ${(result.unlinked || []).length ? `<div class="mt-s1 text-ink-muted">${esc(W.stopUnlinked)}:</div>
    <ul class="m-0 list-none p-0" data-inv-delete-unlinked>${listOf(result.unlinked)}</ul>` : ''}
    ${(result.foreign || []).length ? `<div class="mt-s1 text-ink-muted">${esc(W.stopForeign)}:</div>
    <ul class="m-0 list-none p-0" data-inv-delete-foreign>${listOf(result.foreign)}</ul>` : ''}
    ${(result.left || []).length ? `<div class="mt-s1 text-ink-muted">${esc(W.stopLeft)}:</div>
    <ul class="m-0 list-none p-0" data-inv-delete-left>${listOf(result.left)}</ul>` : ''}
  </div>`;
}

/** Draw the stop the SERVER remembers (`inv.delete_stop`: the latest delete activity row for this investigation
 *  when it ended in an error and no later delete finished) into `host`. Called on every render of the page, so the
 *  cue survives a reload and comes from the activity log, not from anything held in this page. */
export function showRememberedStop(inv, host) {
  if (host) host.innerHTML = inv && inv.delete_stop ? stopHtml(inv.delete_stop) : '';
}

/** What a finished delete left in Egeria, for the list page's one-line note. */
function leftBehindHtml(result, inv) {
  const left = (result.left || []).map((x) => `${x.kind} ${x.guid}`);
  if (result.choice === 're_only' && inv.egeria_project_guid) {
    left.push(`Project ${inv.egeria_project_guid}`);
  }
  const archived = (result.archived || []).filter((x) => !x.with_project).map((x) => `${x.kind} ${x.guid}`);
  return (archived.length ? ` ${esc(DELETE_WORDS.archivedIn)}: ${archived.map(esc).join(', ')}.` : '')
    + (left.length ? ` ${esc(DELETE_WORDS.leftBehind)}: ${left.map(esc).join(', ')}.` : '');
}

/** Press handler. `ctx` supplies what lives in investigation.js: `failurePopup(title, reason)`, `back()` (to the
 *  list, after refreshing the sidebar) and `host` (the element that holds the persistent cue). */
export async function startDelete(inv, btn, ctx) {
  const W = DELETE_WORDS;
  const name = inv.display_name || inv.slug;
  const promoted = !!inv.egeria_project_guid;
  let egeria = false;
  let confirmed = '';
  if (promoted) {
    // Reads only: whether archiving is blocked, and every item other people made on the Project.
    const idleLabel = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = cue('running', W.checking);
    let preview;
    try {
      preview = await previewInvestigationDelete(inv.slug);
    } catch (err) {
      preview = { ok: false, error: err.message, blocked: false, foreign: [] };
    }
    btn.disabled = false;
    btn.innerHTML = idleLabel;
    const blockedWords = preview.blocked ? (preview.blocked_words || '') : '';
    const unavailable = blockedWords || (!preview.ok ? W.previewFailed(preview.error || '') : '');
    const bothText = `${W.choiceBoth}\n${W.choiceBothBody}\n\n${unavailable
      || (preview.project_ours === false ? '' : foreignLines(preview.foreign))}`.trimEnd();
    const choice = await inPageChoice({
      title: W.title(name),
      message: `${W.permanent}\n\n${W.choiceRe}\n${W.choiceReBody}\n\n${bothText}`,
      choices: [{ value: 're', label: W.choiceRe },
        { value: 'both', label: W.choiceBoth, disabled: !!unavailable, title: unavailable }],
      cancelLabel: W.cancel,
    });
    if (!choice) return;
    egeria = choice === 'both';
    confirmed = preview.token || '';
  } else if (!(await inPageConfirm({
    title: W.title(name), message: `${W.permanent}\n\n${W.localOnly}`, confirmLabel: W.localOnlyConfirm,
    cancelLabel: W.cancel,
  }))) return;

  const idle = btn.innerHTML;
  btn.disabled = true;
  btn.innerHTML = cue('running', W.working);
  let result;
  try {
    result = await deleteInvestigation(inv.slug, { egeria, confirmed });
  } catch (err) {
    btn.disabled = false;
    if (err.status === 403) btn.innerHTML = `${esc(W.button)} ${cue('no_access', W.notPermittedCue, err.message)}`;
    else {
      btn.innerHTML = `${esc(W.button)} ${cue('error', W.failedCue, err.message)}`;
      ctx.failurePopup(W.failedCue, err.message);
    }
    return;
  }
  if (!result.local_deleted) {
    // Egeria stopped the delete: the investigation is kept. A pop-up now, and a cue that stays on the page.
    btn.disabled = false;
    btn.innerHTML = idle;
    // Draw what the server just recorded (the same activity row a reload reads back).
    if (ctx.host) ctx.host.innerHTML = stopHtml(result);
    ctx.failurePopup(W.stopTitle, [...(result.errors || []), W.stopKept].join(' '));
    return;
  }
  await ctx.back({
    note: `${esc(result.choice === 're_and_egeria' ? W.doneBoth(name) : W.doneRe(name))}${leftBehindHtml(result, inv)}`,
  });
}
