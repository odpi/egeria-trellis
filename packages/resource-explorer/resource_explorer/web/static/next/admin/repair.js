/* Admin → Repair — per-repository correction.
 *
 * Port of classic's `loadAdminRepairPanel`/`selectRepairRepo`/
 * `renderRepairRepoDetail` (index.html) and its five actions
 * (`submitRepairRename`, `submitRepairGithubUrl`, `submitRepairEnableCollection`,
 * `submitRepairRepoint`, `submitRepairDropMembership`). Backend is complete —
 * resource_explorer/repair.py + web/routes/repair.py, mounted at
 * /api/admin/repair — this is a UI-only port.
 *
 * NOT Resync (admin/resync.js). This is a DIFFERENT job: fixing one
 * repository that was registered with the wrong name/URL/Egeria pointer, not
 * reconciling the whole store against Egeria's current state. See
 * SPEC-ADMIN-THE-FOUR-GAPS.md §1's correction for why folding these into one
 * screen would be wrong (a data-entry correction next to an irreversible
 * bulk clear).
 *
 * Blast-radius rule (§0): every action below names what it does BEFORE it
 * acts, including — per §0's sharpest example, classic's "this does not
 * delete your files on disk" — naming what it does NOT do when the action's
 * NAME sounds more destructive than it is. Checked against repair.py's own
 * docstrings, not assumed:
 *   - rename:            touches only RE's own registry rows + this repo's
 *                         pgvector collections. Does not touch GitHub or Egeria.
 *   - github_url change: drops this repo's existing pgvector collections
 *                         (they were embedded from the OLD url) and marks it
 *                         needing a re-index. Does not touch GitHub or Egeria,
 *                         and does not delete the repo's registry row.
 *   - enable collection:  additive — ingests one more collection type; does
 *                         not touch any existing collection.
 *   - drop membership:    removes membership from ONE investigation's Folio
 *                         only. Does NOT remove the repo from RE's registry,
 *                         and does not touch Egeria.
 *   - repoint membership: drop-then-add between two Folios; same "does not
 *                         remove the repo from RE" scope as drop.
 */
import {
  listProjects, listInvestigations, getRepairDrift, getRepairMemberships,
  repairRename, repairGithubUrl, repairEnableCollection,
  repairRepointMembership, repairDropMembership,
} from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { inPageConfirm, inPageNotice } from '/static/next/confirm-dialog.js';

let _host = null;
let _repos = [];
let _investigations = [];
let _selectedSlug = '';
let _drift = [];
let _memberships = [];

function repoOptionsHtml() {
  return _repos.map((r) =>
    `<option value="${esc(r.slug)}" ${r.slug === _selectedSlug ? 'selected' : ''}>${esc(r.display_name)} (${esc(r.slug)})</option>`
  ).join('');
}

function driftHtml() {
  if (!_drift.length) {
    return '<p class="text-caveat text-ink-muted">No drift — everything eligible is already enabled.</p>';
  }
  return _drift.map((f) => `
    <div class="flex items-center justify-between border-b border-rule py-s2 last:border-b-0">
      <div class="text-caveat text-ink">
        <span class="font-mono text-accent-ink">${esc(f.collection_type)}</span>
        <span class="text-ink-muted"> — ${f.matching_files} matching file(s), needs ${f.min_required}</span>
      </div>
      <button type="button" data-enable-collection="${esc(f.collection_type)}"
        class="cursor-pointer rounded-sm border border-rule-strong px-2 py-[2px] text-provenance text-ink hover:border-accent">+ Enable</button>
    </div>`).join('');
}

function membershipsHtml() {
  if (!_memberships.length) {
    return '<p class="text-caveat text-ink-muted">Not a member of any investigation.</p>';
  }
  const others = _investigations.filter((i) => !_memberships.some((m) => m.investigation_slug === i.slug));
  return _memberships.map((m) => `
    <div class="flex items-center justify-between gap-s2 border-b border-rule py-s2 last:border-b-0">
      <div class="min-w-0 text-caveat text-ink">
        ${esc(m.display_name)}
        <span class="font-mono text-provenance text-ink-muted"> (${esc(m.investigation_slug)}, ${esc(m.status)})</span>
      </div>
      <div class="flex shrink-0 items-center gap-[6px]">
        <select data-repoint-target="${esc(m.investigation_slug)}"
          class="rounded-sm border border-rule-strong bg-transparent px-1 py-[2px] text-provenance text-ink">
          ${others.map((i) => `<option value="${esc(i.slug)}">${esc(i.display_name)}</option>`).join('')
            || '<option value="">— no other investigations —</option>'}
        </select>
        <button type="button" data-repoint="${esc(m.investigation_slug)}"
          class="cursor-pointer rounded-sm border border-rule-strong px-2 py-[2px] text-provenance text-ink hover:border-accent">Repoint</button>
        <button type="button" data-drop="${esc(m.investigation_slug)}"
          class="cursor-pointer rounded-sm border border-state-warn px-2 py-[2px] text-provenance text-state-warn hover:bg-paper-surface">Drop</button>
      </div>
    </div>`).join('');
}

function detailHtml(repo) {
  return `
    <div class="space-y-s4">
      <div class="rounded-sm border border-rule p-s3">
        <h4 class="m-0 text-caps uppercase tracking-caps text-ink-muted">Rename slug</h4>
        <p class="mt-[4px] max-w-[65ch] text-caveat text-ink-muted">Moves every registry row and this
          repo's own pgvector collections to the new slug. Shared collections other repos also use are
          left untouched. Does not touch GitHub or Egeria — this only renames RE's own record. Refuses
          if the new slug is already taken.</p>
        <div class="mt-s2 flex items-center gap-[6px]">
          <span class="font-mono text-caveat text-ink-muted">${esc(repo.slug)}</span>
          <span class="text-ink-muted">→</span>
          <input type="text" data-rename-input placeholder="new_slug"
            class="flex-1 rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] font-mono text-caveat text-ink">
          <button type="button" data-rename
            class="cursor-pointer rounded-sm border border-accent px-3 py-[3px] text-caveat text-accent-ink hover:bg-paper-surface">Rename</button>
        </div>
      </div>

      <div class="rounded-sm border border-rule p-s3">
        <h4 class="m-0 text-caps uppercase tracking-caps text-ink-muted">Correct github_url</h4>
        <p class="mt-[4px] max-w-[65ch] text-caveat text-ink-muted">Current:
          <span class="font-mono text-ink">${esc(repo.github_url)}</span>. Changing this drops every
          existing collection (${repo.collections.length}) — they were embedded from the old URL — and
          leaves the repo needing a re-index. Does not touch GitHub itself, and does not delete the
          repo's registry row.</p>
        <div class="mt-s2 flex items-center gap-[6px]">
          <input type="text" data-url-input placeholder="https://github.com/org/correct-repo"
            class="flex-1 rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] font-mono text-caveat text-ink">
          <button type="button" data-change-url
            class="cursor-pointer rounded-sm border border-accent px-3 py-[3px] text-caveat text-accent-ink hover:bg-paper-surface">Change URL</button>
        </div>
      </div>

      <div class="rounded-sm border border-rule p-s3">
        <h4 class="m-0 text-caps uppercase tracking-caps text-ink-muted">Collection drift</h4>
        <p class="mt-[4px] text-caveat text-ink-muted">Collection types this repo is eligible for but
          hasn't enabled. Additive — enabling one does not touch any existing collection.</p>
        <div class="mt-s2" data-drift-section>${driftHtml()}</div>
      </div>

      <div class="rounded-sm border border-rule p-s3">
        <h4 class="m-0 text-caps uppercase tracking-caps text-ink-muted">Investigation memberships</h4>
        <p class="mt-[4px] text-caveat text-ink-muted">Which investigations this repo is in scope for.
          Dropping or repointing only changes Folio membership — it does not remove the repo from RE's
          registry, and does not touch Egeria.</p>
        <div class="mt-s2" data-memberships-section>${membershipsHtml()}</div>
      </div>
    </div>`;
}

function render() {
  const repo = _repos.find((r) => r.slug === _selectedSlug);
  _host.innerHTML = `
    <h3 class="m-0 font-heading text-name font-normal text-ink">🔧 Repair</h3>
    <p class="mt-[4px] max-w-[70ch] text-caveat text-ink-muted">Fix a repo registered under the wrong
      name or URL, turn on a collection type drift flagged, or move it between investigations. Each
      action names what it does — and, where the name sounds worse than the act, what it does NOT
      do — before it runs.</p>
    <div class="my-s3 h-px bg-rule"></div>
    <div class="rounded-sm border border-rule p-s3">
      <label class="block text-provenance uppercase tracking-caps text-ink-muted">Repo</label>
      <select data-repo-select
        class="mt-[4px] w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[4px] text-caveat text-ink">
        <option value="">— choose a repo —</option>
        ${repoOptionsHtml()}
      </select>
    </div>
    <div class="mt-s3" data-repair-detail>
      ${repo ? detailHtml(repo) : '<p class="py-s4 text-center text-caveat text-ink-muted">Choose a repo above to see repair actions.</p>'}
    </div>`;
  bind();
}

async function selectRepo(slug) {
  _selectedSlug = slug;
  _drift = [];
  _memberships = [];
  render();
  if (!slug) return;
  try {
    _drift = await getRepairDrift(slug);
  } catch (_) { _drift = []; }
  const driftHost = _host.querySelector('[data-drift-section]');
  if (driftHost) { driftHost.innerHTML = driftHtml(); bindDrift(); }
  try {
    _memberships = await getRepairMemberships(slug);
  } catch (_) { _memberships = []; }
  const memHost = _host.querySelector('[data-memberships-section]');
  if (memHost) { memHost.innerHTML = membershipsHtml(); bindMemberships(); }
}

function bindDrift() {
  _host.querySelectorAll('[data-enable-collection]').forEach((b) => b.addEventListener('click', async () => {
    const collectionType = b.dataset.enableCollection;
    b.disabled = true;
    try {
      const data = await repairEnableCollection(_selectedSlug, collectionType);
      await inPageNotice({ title: 'Done', message: `Enabled '${data.collection}' — ${data.chunks_inserted} chunk(s) inserted.` });
      await selectRepo(_selectedSlug);
    } catch (err) {
      await inPageNotice({ message: `Enable failed: ${err.message}` });
      b.disabled = false;
    }
  }));
}

function bindMemberships() {
  _host.querySelectorAll('[data-repoint]').forEach((b) => b.addEventListener('click', async () => {
    const from = b.dataset.repoint;
    const select = _host.querySelector(`[data-repoint-target="${CSS.escape(from)}"]`);
    const to = select?.value;
    if (!to) { await inPageNotice({ title: 'Nothing to do', message: 'No other investigation to repoint to.' }); return; }
    try {
      const data = await repairRepointMembership(_selectedSlug, from, to);
      await inPageNotice({ title: 'Done', message: `Moved '${_selectedSlug}' to '${data.to_investigation}'.` });
      await selectRepo(_selectedSlug);
    } catch (err) {
      await inPageNotice({ message: `Repoint failed: ${err.message}` });
    }
  }));
  _host.querySelectorAll('[data-drop]').forEach((b) => b.addEventListener('click', async () => {
    const investigationSlug = b.dataset.drop;
    if (!(await inPageConfirm({ message: `Drop '${_selectedSlug}' from investigation '${investigationSlug}'? This only removes it from `
      + `that investigation's Folio — it stays in RE's registry and any other investigation it's in.`, confirmLabel: 'Confirm' }))) return;
    try {
      await repairDropMembership(_selectedSlug, investigationSlug);
      await selectRepo(_selectedSlug);
    } catch (err) {
      await inPageNotice({ message: `Drop failed: ${err.message}` });
    }
  }));
}

function bind() {
  _host.querySelector('[data-repo-select]')?.addEventListener('change', (e) => selectRepo(e.target.value));

  _host.querySelector('[data-rename]')?.addEventListener('click', async () => {
    const input = _host.querySelector('[data-rename-input]');
    const newSlug = (input?.value || '').trim();
    if (!newSlug) { await inPageNotice({ title: 'Nothing to do', message: 'Enter a new slug first.' }); return; }
    if (!(await inPageConfirm({ message: `Rename '${_selectedSlug}' to '${newSlug}'? This moves every registry row and this repo's own `
      + `pgvector collections. It does not touch GitHub or Egeria.`, confirmLabel: 'Confirm' }))) return;
    try {
      const data = await repairRename(_selectedSlug, newSlug);
      await inPageNotice({ title: 'Done', message: `Renamed '${_selectedSlug}' → '${data.new_slug}'.` });
      _selectedSlug = data.new_slug;
      await reloadRepos();
    } catch (err) {
      await inPageNotice({ message: `Rename failed: ${err.message}` });
    }
  });

  _host.querySelector('[data-change-url]')?.addEventListener('click', async () => {
    const input = _host.querySelector('[data-url-input]');
    const newUrl = (input?.value || '').trim();
    if (!newUrl) { await inPageNotice({ title: 'Nothing to do', message: 'Enter the correct github_url first.' }); return; }
    const repo = _repos.find((r) => r.slug === _selectedSlug);
    if (!(await inPageConfirm({ message: `Change github_url for '${_selectedSlug}' to '${newUrl}'? This drops all `
      + `${repo ? repo.collections.length : 'existing'} collection(s) — you'll need to re-index `
      + `afterward. It does not touch GitHub itself.`, confirmLabel: 'Confirm' }))) return;
    try {
      const data = await repairGithubUrl(_selectedSlug, newUrl, true);
      await inPageNotice({ title: 'Done', message: `'${_selectedSlug}' now points at ${data.new_url}. Run refresh to re-index.` });
      await reloadRepos();
    } catch (err) {
      await inPageNotice({ message: `URL change failed: ${err.message}` });
    }
  });

  bindDrift();
  bindMemberships();
}

async function reloadRepos() {
  _repos = await listProjects({ includeIgnored: true, includeHidden: true });
  render();
  if (_selectedSlug) await selectRepo(_selectedSlug);
}

export async function renderRepair(host) {
  _host = host;
  _selectedSlug = '';
  [_repos, _investigations] = await Promise.all([
    listProjects({ includeIgnored: true, includeHidden: true }),
    listInvestigations({ includeClosed: true }),
  ]);
  render();
}
