/* W38: deleting a work list, from the list's own page (never from the sidebar row, where its members and publish state
 * are out of view). Its own module, like investigation-delete.js: the words "delete" for a work list live here, not in
 * the resource-removal files the source scan guards. */
import { deleteWorkList } from '/static/re-api.js';
import { inPageConfirm, inPageNotice } from '/static/next/confirm-dialog.js';

export function deleteListButtonHtml() {
  return `<button data-act="delete-list" class="cursor-pointer bg-transparent text-state-warn underline"
        title="Remove this list from Resource Explorer. Its members stay registered; an Egeria Collection it published stays in Egeria."
        >Delete this list…</button>`;
}

/** What the confirmation says, from the list's own row. "This cannot be undone" is said in words. */
export function deleteConfirmMessage(wl, n) {
  const noun = { database: 'databases', filesystem: 'file systems', repo: 'repositories' }[wl.entity_type] || 'members';
  const egeria = wl.egeria_guid
    ? `The Egeria Collection it published stays in Egeria (${String(wl.egeria_guid).slice(0, 8)}).`
    : 'It was never published to Egeria.';
  return `Removes '${wl.display_name || wl.slug}' from Resource Explorer. Its ${n} ${noun} stay registered. ${egeria} This cannot be undone.`;
}

/** Wire the header's button. `current()` returns the open list row; `ctx.host.afterDelete` redraws the shell. */
export function bindDeleteList(host, current, ctx) {
  host.querySelector('[data-act="delete-list"]')?.addEventListener('click', async () => {
    const wl = current();
    const ok = await inPageConfirm({ title: 'Delete this list?', message: deleteConfirmMessage(wl, (wl.members || []).length),
      confirmLabel: 'Delete list' });
    if (!ok) return;
    try {
      await deleteWorkList(wl.slug);
    } catch (err) {
      await inPageNotice({ title: 'The list was not deleted', message: err.message });
      return;
    }
    if (ctx.host && ctx.host.afterDelete) await ctx.host.afterDelete(wl.slug);
    else if (ctx.onExit) ctx.onExit();
  });
}
