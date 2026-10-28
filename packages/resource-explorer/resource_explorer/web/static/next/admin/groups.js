/* Admin → Groups — create/delete resource Groups, assign a resource to one,
 * and surface the org-sharing suggestions nothing in /next currently shows.
 *
 * Full port of classic's (`index.html`) `createAdminGroup`, `deleteAdminGroup`,
 * `openAssignGroupModal`/`closeAssignGroupModal`/`submitAssignGroup`, and
 * `applyGroupSuggestion` — see SPEC-ADMIN-THE-FOUR-GAPS.md §2. Nothing here
 * is new design; classic already runs all four of these against the same
 * routes (`GET/POST /api/projects/groups`, `DELETE .../{slug}`,
 * `GET .../groups/suggestions`, `POST /api/projects/{slug}/group`).
 *
 * One deliberate departure from classic's layout: classic opens assignment
 * from a 🗂 button on each resource's own row (sidebar/Curate), which /next
 * has no equivalent of yet — there is no per-resource "assign to group"
 * affordance anywhere in /next's resource views. Building that entry point
 * would mean touching sidebar/resource-card rendering that this item's
 * brief explicitly keeps out of scope (owned by concurrent work on the
 * sidebar's display half — see SIDEBAR-GROUP-COLLAPSE-IMPLEMENTED.md).
 * Assignment is therefore offered here instead, as its own resource-picker
 * + group-picker control, against the exact same `POST /{slug}/group` route
 * classic's modal uses. Bulk assignment via multi-select is explicitly out
 * of scope (SPEC-ADMIN-THE-FOUR-GAPS.md §2 — "wants the sidebar's selection,
 * belongs with the collapse round").
 *
 * Blast radius (SPEC-ADMIN-THE-FOUR-GAPS.md §0/§2): deleting a group does
 * NOT delete its member resources — they return to Ungrouped. The classic
 * pane never confirms this at all before deleting (checked directly —
 * `deleteAdminGroup` has no `window.confirm`, unlike its sibling
 * filesystem/database "remove from registry" actions a few hundred lines
 * away, which do). That is the exact gap §0 calls out ("a destructive-
 * sounding action naming what it does not touch is the sharper half of the
 * rule") — this port adds the confirmation classic is missing, worded after
 * classic's own best line for it ("Remove filesystem X… This does not
 * delete your files on disk").
 */
import { state, esc, refreshGroupsAndSidebar } from '/static/next/app.js';
import { inPageConfirm, inPageNotice } from '/static/next/confirm-dialog.js';
import {
  listGroups, groupSuggestions, createGroup, deleteGroup, assignGroup,
  listProjects, listDatabases, listFilesystems,
} from '/static/re-api.js';

let _host = null;
let _groups = [];
let _suggestions = [];
let _resources = []; // [{ kind: 'repo'|'database'|'filesystem', slug, display_name, group_slug }]
const createForm = { slug: '', displayName: '', description: '' };
const assignForm = { resourceKey: '', groupSlug: '' };

function memberCountsHtml(g) {
  const n = (g.projects || []).length;
  const d = (g.databases || []).length;
  const f = (g.filesystems || []).length;
  return `${n} repo${n === 1 ? '' : 's'} · ${d} db${d === 1 ? '' : 's'} · ${f} fs`;
}

function totalMembers(g) {
  return (g.projects || []).length + (g.databases || []).length + (g.filesystems || []).length;
}

function groupRowHtml(g) {
  return `<tr class="border-b border-rule hover:bg-paper-surface">
    <td class="py-s2 pr-s3 text-caveat text-ink">${esc(g.display_name)}</td>
    <td class="py-s2 pr-s3 font-mono text-provenance text-accent-ink">${esc(g.slug)}</td>
    <td class="max-w-[24ch] truncate py-s2 pr-s3 text-caveat text-ink-muted">${esc(g.description) || '—'}</td>
    <td class="whitespace-nowrap py-s2 pr-s3 text-caveat text-ink-muted">${esc(memberCountsHtml(g))}</td>
    <td class="py-s2 text-right">
      <button type="button" data-delete-group="${esc(g.slug)}"
        class="cursor-pointer rounded-sm border border-state-warn bg-transparent px-2 py-[2px] text-caveat text-state-warn">🗑️ Delete</button>
    </td>
  </tr>`;
}

function suggestionRowHtml(s, i) {
  return `<div class="flex items-center justify-between gap-s3 rounded-sm border border-rule bg-paper-surface p-s2">
    <div class="text-caveat text-ink">
      <span class="font-mono text-accent-ink">${esc(s.org)}</span> —
      ${s.repo_count} ungrouped repo${s.repo_count === 1 ? '' : 's'} share this GitHub org:
      <span class="text-ink-muted">${s.repo_slugs.map(esc).join(', ')}</span>
    </div>
    <button type="button" data-apply-suggestion="${i}"
      class="shrink-0 cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink"
      >+ Group these ${s.repo_count}</button>
  </div>`;
}

function resourceOptionsHtml() {
  const byKind = { repo: [], database: [], filesystem: [] };
  for (const r of _resources) byKind[r.kind]?.push(r);
  const label = { repo: 'Repos', database: 'Databases', filesystem: 'Filesystems' };
  return ['repo', 'database', 'filesystem'].filter((k) => byKind[k].length).map((k) => `
    <optgroup label="${esc(label[k])}">
      ${byKind[k].map((r) => `<option value="${esc(k)}:${esc(r.slug)}">${esc(r.display_name || r.slug)}${
        r.group_slug ? ` (in ${esc(_groupDisplayName(r.group_slug))})` : ''}</option>`).join('')}
    </optgroup>`).join('');
}

function _groupDisplayName(slug) {
  return _groups.find((g) => g.slug === slug)?.display_name || slug;
}

function groupOptionsHtml(selected) {
  return '<option value="">— Ungrouped —</option>' +
    _groups.map((g) => `<option value="${esc(g.slug)}" ${g.slug === selected ? 'selected' : ''}>${esc(g.display_name)}</option>`).join('');
}

function render() {
  if (!_host) return;
  const rows = _groups.map(groupRowHtml).join('');
  const suggestionsHtml = _suggestions.length ? `
    <div class="mb-s4 rounded-sm border border-state-warn bg-paper-surface p-s3">
      <div class="mb-s2 text-caps uppercase tracking-caps text-state-warn">💡 Suggested groupings</div>
      <p class="mb-s2 max-w-[65ch] text-caveat text-ink-muted">These ungrouped repos share a GitHub
        org — often a sign they're the same logical project. Nothing is grouped automatically;
        review and apply.</p>
      <div class="flex flex-col gap-s2">${_suggestions.map(suggestionRowHtml).join('')}</div>
    </div>` : '';

  _host.innerHTML = `
    <h3 class="m-0 font-heading text-name font-normal text-ink">🗂 Resource Groups</h3>
    <p class="mt-[2px] max-w-[70ch] text-caveat text-ink-muted">Group repos, databases and
      filesystems that belong together (e.g. one product's resources). Deleting a group never
      deletes what's in it — members return to Ungrouped.</p>

    <div class="my-s3 rounded-sm border border-rule bg-paper-surface p-s3">
      <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">+ New group</div>
      <div class="flex flex-wrap items-end gap-s2">
        <label class="text-caveat text-ink-muted">Slug
          <input data-new-slug type="text" placeholder="my-product" value="${esc(createForm.slug)}"
            class="mt-[2px] block w-36 rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink" />
        </label>
        <label class="text-caveat text-ink-muted">Display name
          <input data-new-name type="text" placeholder="My Product" value="${esc(createForm.displayName)}"
            class="mt-[2px] block w-48 rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink" />
        </label>
        <label class="min-w-[16ch] flex-1 text-caveat text-ink-muted">Description (optional)
          <input data-new-desc type="text" value="${esc(createForm.description)}"
            class="mt-[2px] block w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink" />
        </label>
        <button type="button" data-create-group
          class="cursor-pointer rounded-sm border border-accent bg-transparent px-3 py-[4px] text-caveat font-semibold text-accent-ink">+ Create</button>
      </div>
      <div data-create-feedback class="mt-s2 text-caveat text-ink-muted"></div>
    </div>

    ${suggestionsHtml}

    <div class="mb-s4 rounded-sm border border-rule bg-paper-surface p-s3">
      <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">Assign a resource to a group</div>
      <div class="flex flex-wrap items-end gap-s2">
        <label class="min-w-[20ch] flex-1 text-caveat text-ink-muted">Resource
          <select data-assign-resource
            class="mt-[2px] block w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink">
            <option value="">— choose —</option>
            ${resourceOptionsHtml()}
          </select>
        </label>
        <label class="min-w-[16ch] text-caveat text-ink-muted">Group
          <select data-assign-group
            class="mt-[2px] block w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink">
            ${groupOptionsHtml(assignForm.groupSlug)}
          </select>
        </label>
        <button type="button" data-assign-submit
          class="cursor-pointer rounded-sm border border-accent bg-transparent px-3 py-[4px] text-caveat font-semibold text-accent-ink">Assign</button>
      </div>
      <div data-assign-feedback class="mt-s2 text-caveat text-ink-muted"></div>
    </div>

    <div class="max-h-[38vh] overflow-auto rounded-sm border border-rule">
      <table class="w-full text-left">
        <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
          <th class="px-s2 py-s2 font-normal">Display name</th>
          <th class="px-s2 py-s2 font-normal">Slug</th>
          <th class="px-s2 py-s2 font-normal">Description</th>
          <th class="px-s2 py-s2 font-normal">Members</th>
          <th class="px-s2 py-s2 font-normal"></th>
        </tr></thead>
        <tbody>${rows || '<tr><td colspan="5" class="py-s4 text-center text-caveat text-ink-muted">No groups yet.</td></tr>'}</tbody>
      </table>
    </div>`;

  bind();
}

function bind() {
  if (!_host) return;

  _host.querySelector('[data-create-group]')?.addEventListener('click', onCreateGroup);
  _host.querySelectorAll('[data-apply-suggestion]').forEach((b) => b.addEventListener('click', () => onApplySuggestion(Number(b.dataset.applySuggestion))));
  _host.querySelectorAll('[data-delete-group]').forEach((b) => b.addEventListener('click', () => onDeleteGroup(b.dataset.deleteGroup)));

  const resourceSel = _host.querySelector('[data-assign-resource]');
  const groupSel = _host.querySelector('[data-assign-group]');
  resourceSel?.addEventListener('change', () => {
    assignForm.resourceKey = resourceSel.value;
    // Pre-select the resource's current group, same as classic's modal
    // pre-selecting `currentGroupSlug` when it opens.
    const [, slug] = assignForm.resourceKey.split(':');
    const r = _resources.find((x) => x.slug === slug);
    if (groupSel) groupSel.value = r?.group_slug || '';
    assignForm.groupSlug = groupSel?.value || '';
  });
  groupSel?.addEventListener('change', () => { assignForm.groupSlug = groupSel.value; });
  _host.querySelector('[data-assign-submit]')?.addEventListener('click', onAssign);
}

async function onCreateGroup() {
  const fb = _host.querySelector('[data-create-feedback]');
  const slug = (_host.querySelector('[data-new-slug]')?.value || '').trim();
  const displayName = (_host.querySelector('[data-new-name]')?.value || '').trim();
  const description = (_host.querySelector('[data-new-desc]')?.value || '').trim();
  if (!slug || !displayName) {
    if (fb) fb.innerHTML = '<span class="text-state-warn">Slug and display name are both required.</span>';
    return;
  }
  if (fb) fb.textContent = 'Creating…';
  try {
    await createGroup(slug, displayName, description);
    createForm.slug = ''; createForm.displayName = ''; createForm.description = '';
    await reload();
  } catch (err) {
    if (fb) fb.innerHTML = `<span class="text-state-warn">Failed: ${esc(err.message)}</span>`;
  }
}

async function onApplySuggestion(index) {
  const s = _suggestions[index];
  if (!s) return;
  let groupSlug = s.org.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
  // Avoid colliding with an existing group slug — same guard as classic's
  // applyGroupSuggestion (two orgs normalizing to the same slug, or a
  // manually-created group of the same name).
  if (_groups.some((g) => g.slug === groupSlug)) groupSlug = `${groupSlug}-${Date.now().toString(36)}`;
  try {
    await createGroup(groupSlug, s.suggested_display_name, `Auto-suggested from shared GitHub org: ${s.org}`);
    for (const repoSlug of s.repo_slugs) {
      // Explicit 'repo': suggested groups come from shared GitHub orgs, so every slug here is a repo.
      await assignGroup(repoSlug, groupSlug, 'repo');
    }
    await reload();
  } catch (err) {
    await inPageNotice({ message: `Failed to apply suggestion: ${err.message}` });
  }
}

async function onDeleteGroup(slug) {
  const g = _groups.find((x) => x.slug === slug);
  const n = g ? totalMembers(g) : 0;
  const name = g ? g.display_name : slug;
  // Blast radius named plainly (SPEC-ADMIN-THE-FOUR-GAPS.md §0/§2): a group
  // delete reads as destructive and is not — members return to Ungrouped,
  // never deleted. Classic's own equivalent action for this pane skips a
  // confirm entirely; this is the fix, not a copy.
  const msg = n > 0
    ? `Delete group "${name}"? ${n} resource${n === 1 ? '' : 's'} return to Ungrouped — nothing is deleted.`
    : `Delete group "${name}"? It has no members.`;
  if (!(await inPageConfirm({ message: msg, confirmLabel: 'Confirm' }))) return;
  try {
    await deleteGroup(slug);
    await reload();
  } catch (err) {
    await inPageNotice({ message: `Failed to delete group: ${err.message}` });
  }
}

async function onAssign() {
  const fb = _host.querySelector('[data-assign-feedback]');
  if (!assignForm.resourceKey) {
    if (fb) fb.innerHTML = '<span class="text-state-warn">Choose a resource first.</span>';
    return;
  }
  const [kind, slug] = assignForm.resourceKey.split(':');
  const resourceType = kind === 'database' ? 'database' : kind === 'filesystem' ? 'filesystem' : 'repo';
  if (fb) fb.textContent = 'Assigning…';
  try {
    await assignGroup(slug, assignForm.groupSlug, resourceType);
    if (fb) {
      fb.textContent = assignForm.groupSlug
        ? `Assigned to ${_groupDisplayName(assignForm.groupSlug)}.`
        : 'Removed from group.';
    }
    await reload();
  } catch (err) {
    if (fb) fb.innerHTML = `<span class="text-state-warn">Failed: ${esc(err.message)}</span>`;
  }
}

async function reload() {
  if (!_host) return;
  let groupsRes;
  let suggestionsRes;
  let projectsRes;
  let databasesRes;
  let filesystemsRes;
  try {
    [groupsRes, suggestionsRes, projectsRes, databasesRes, filesystemsRes] = await Promise.allSettled([
      listGroups(), groupSuggestions(),
      listProjects({ includeIgnored: true, includeHidden: true }),
      listDatabases(), listFilesystems(),
    ]);
  } catch (err) {
    _host.innerHTML = `<p class="max-w-[70ch] text-answer text-state-warn">Could not load groups: ${esc(err.message)}</p>`;
    return;
  }
  if (groupsRes.status !== 'fulfilled') {
    _host.innerHTML = `<p class="max-w-[70ch] text-answer text-state-warn">Could not load groups: ${esc(groupsRes.reason?.message || 'unknown error')}</p>`;
    return;
  }
  _groups = groupsRes.value || [];
  _suggestions = suggestionsRes.status === 'fulfilled' ? (suggestionsRes.value || []) : [];
  const projects = projectsRes.status === 'fulfilled' ? (projectsRes.value || []) : [];
  const databases = databasesRes.status === 'fulfilled' ? (databasesRes.value || []) : [];
  const filesystems = filesystemsRes.status === 'fulfilled' ? (filesystemsRes.value || []) : [];
  _resources = [
    ...projects.map((p) => ({ kind: 'repo', slug: p.slug, display_name: p.display_name, group_slug: p.group_slug || '' })),
    ...databases.map((d) => ({ kind: 'database', slug: d.slug, display_name: d.display_name, group_slug: d.group_slug || '' })),
    ...filesystems.map((f) => ({ kind: 'filesystem', slug: f.slug, display_name: f.display_name, group_slug: f.group_slug || '' })),
  ];

  // Keep the sidebar's own copy of groups/projects (state.groups/state.projects,
  // loaded once in start()) in sync with what this pane just changed — see
  // refreshGroupsAndSidebar's own comment in app.js.
  state.groups = _groups;
  await refreshGroupsAndSidebar();

  render();
}

export async function renderGroups(host) {
  _host = host;
  createForm.slug = ''; createForm.displayName = ''; createForm.description = '';
  assignForm.resourceKey = ''; assignForm.groupSlug = '';
  await reload();
}
