/* Admin → Annotation Types — browse, register, edit, delete.
 *
 * Classic (index.html `loadAnnotationTypesView`) offers a list, a detail
 * view, and Register/Edit/Delete modals against POST/PUT/DELETE
 * /api/analyses/annotation-types(/{type}), which already existed on the
 * backend (analyses.py) — this file used to make ZERO write calls, by
 * deliberate, named deferral (see git history / ITEM-5-ADMIN-IMPLEMENTED.md
 * for why). SPEC-ADMIN-THE-FOUR-GAPS.md §4 calls this the "pure UI gap" of
 * the two registries: build create/edit/delete against routes already
 * there.
 *
 * Blast radius (spec §0/§4): an annotation type is referenced by recorded
 * annotations, so deleting or renaming one changes what existing records
 * mean. RE keeps no durable local table of individual annotation instances
 * by type (see the /usage route's docstring in analyses.py) — the closest
 * cheap, real number is how many projects have a LOCAL RECORD of publishing
 * this type at least once, which is a lower bound, not the true count.
 * getAnnotationTypeUsage()'s `note` says so; the confirmation copy below
 * repeats it verbatim rather than inventing a shorter, falsely-confident
 * version, and a fetch failure is shown as "unknown" — never silently
 * treated as 0.
 */
import {
  listAnnotationTypes, registerAnnotationType, updateAnnotationType,
  deleteAnnotationType, getAnnotationTypeUsage,
} from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { inPageConfirm, inPageNotice } from '/static/next/confirm-dialog.js';

let _types = null;
let _selected = null;
let _host = null;

function listHtml() {
  const rows = (_types || []).map((a) => `
    <tr data-select-type="${esc(a.type)}" class="cursor-pointer border-b border-rule hover:bg-paper-surface">
      <td class="py-s2 pr-s3 text-answer text-ink">${esc(a.display_name || a.type)}</td>
      <td class="py-s2 pr-s3 font-mono text-caveat text-accent-ink">${esc(a.type)}</td>
      <td class="max-w-[28ch] truncate py-s2 pr-s3 text-caveat text-ink-muted" title="${esc(a.description || '')}">${esc(a.description || '—')}</td>
      <td class="py-s2 font-mono text-caveat text-ink-muted">${esc(a.egeria_type || '—')}</td>
    </tr>`).join('');

  return `
    <div class="mb-s3 flex items-start justify-between gap-s3">
      <div>
        <h3 class="m-0 font-heading text-name font-normal text-ink">Annotation Types registry</h3>
        <p class="mt-[2px] max-w-[65ch] text-caveat text-ink-muted">Metadata annotation schemas and
          their mapping to Egeria framework properties.</p>
      </div>
      <button type="button" data-register
        class="whitespace-nowrap rounded-sm border border-accent px-s2 py-[3px] text-caveat text-accent-ink hover:bg-paper-surface">
        + Register annotation type
      </button>
    </div>
    ${_types && _types.length ? `<table class="w-full text-left">
      <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
        <th class="pb-s2 pr-s3 font-normal">Display name</th>
        <th class="pb-s2 pr-s3 font-normal">Type key</th>
        <th class="pb-s2 pr-s3 font-normal">Description</th>
        <th class="pb-s2 font-normal">Egeria class</th>
      </tr></thead>
      <tbody>${rows}</tbody>
    </table>` : '<p class="text-answer text-ink-muted">No annotation types registered.</p>'}`;
}

function detailHtml(a) {
  const props = (a.properties || []).length
    ? a.properties.map((p) => `<span class="mr-[4px] mb-[4px] inline-block rounded-sm border border-rule-strong px-2 py-[1px] font-mono text-provenance text-ink">${esc(p)}</span>`).join('')
    : '<span class="text-caveat text-ink-muted">No properties defined.</span>';

  return `
    <button type="button" data-back class="mb-s3 cursor-pointer bg-transparent p-0 text-caveat text-accent-ink underline">← All annotation types</button>
    <div class="mb-s3 flex items-start justify-between gap-s3">
      <div>
        <div class="text-caps uppercase tracking-caps text-accent-ink">Annotation type schema</div>
        <h3 class="m-0 mt-[2px] font-heading text-name font-normal text-ink">${esc(a.display_name || a.type)}</h3>
        <div class="mt-[2px] font-mono text-caveat text-ink-muted">${esc(a.type)}</div>
      </div>
      <div class="flex shrink-0 gap-s2">
        <button type="button" data-edit="${esc(a.type)}"
          class="whitespace-nowrap rounded-sm border border-rule-strong px-s2 py-[3px] text-caveat text-ink hover:bg-paper-surface">
          ✏ Edit
        </button>
        <button type="button" data-delete="${esc(a.type)}"
          class="whitespace-nowrap rounded-sm border border-state-warn px-s2 py-[3px] text-caveat text-state-warn hover:bg-paper-surface">
          🗑 Delete
        </button>
      </div>
    </div>
    <div class="my-s3 h-px bg-rule"></div>
    <div class="mb-s3">
      <div class="text-caps uppercase tracking-caps text-ink-muted">Description</div>
      <p class="mt-[4px] max-w-[65ch] text-answer text-ink">${esc(a.description || 'No description provided.')}</p>
    </div>
    <div class="mb-s3">
      <div class="text-caps uppercase tracking-caps text-ink-muted">Properties / attributes</div>
      <div class="mt-[6px]">${props}</div>
    </div>
    <div>
      <div class="text-caps uppercase tracking-caps text-ink-muted">Bindings</div>
      <div class="mt-[6px] text-caveat text-ink-muted">Egeria property class</div>
      <div class="font-mono text-caveat text-ink">${esc(a.egeria_type || '—')}</div>
      <div class="mt-[6px] text-caveat text-ink-muted">Python representation class</div>
      <div class="font-mono text-caveat text-ink">${esc(a.python_class || '—')}</div>
    </div>`;
}

/* ── Register/Edit modal ────────────────────────────────────────────────── */

function closeFormModal() {
  document.getElementById('annotation-type-form-modal')?.remove();
}

function openFormModal(existing /* null for register */) {
  closeFormModal();
  const isEdit = !!existing;
  const el = document.createElement('div');
  el.id = 'annotation-type-form-modal';
  el.className = 'fixed inset-0 z-[60] flex items-start justify-center bg-black/40 p-s4 overflow-auto';
  el.innerHTML = `
    <div class="mt-[6vh] w-full max-w-[520px] rounded bg-paper p-s4 shadow-lg" role="dialog" aria-modal="true"
      aria-label="${isEdit ? 'Edit' : 'Register'} annotation type">
      <div class="font-heading text-name text-ink">${isEdit ? 'Edit' : 'Register'} annotation type</div>
      <div class="my-s3 h-px bg-rule"></div>
      <form data-form class="space-y-s2">
        <label class="block">
          <div class="text-caps uppercase tracking-caps text-ink-muted">Type key ${isEdit ? '(immutable)' : ''}</div>
          <input data-f="type" required ${isEdit ? 'disabled' : ''} value="${esc(existing?.type || '')}"
            class="mt-[2px] w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] font-mono text-caveat text-ink disabled:opacity-60" />
        </label>
        <label class="block">
          <div class="text-caps uppercase tracking-caps text-ink-muted">Display name</div>
          <input data-f="display_name" required value="${esc(existing?.display_name || '')}"
            class="mt-[2px] w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink" />
        </label>
        <label class="block">
          <div class="text-caps uppercase tracking-caps text-ink-muted">Description</div>
          <textarea data-f="description" rows="2" required
            class="mt-[2px] w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink">${esc(existing?.description || '')}</textarea>
        </label>
        <label class="block">
          <div class="text-caps uppercase tracking-caps text-ink-muted">Properties (comma-separated)</div>
          <input data-f="properties" value="${esc((existing?.properties || []).join(', '))}"
            class="mt-[2px] w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink" />
        </label>
        <label class="block">
          <div class="text-caps uppercase tracking-caps text-ink-muted">Egeria property class</div>
          <input data-f="egeria_type" value="${esc(existing?.egeria_type || '')}"
            class="mt-[2px] w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] font-mono text-caveat text-ink" />
        </label>
        <label class="block">
          <div class="text-caps uppercase tracking-caps text-ink-muted">Python representation class</div>
          <input data-f="python_class" value="${esc(existing?.python_class || '')}"
            class="mt-[2px] w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] font-mono text-caveat text-ink" />
        </label>
        <p data-error class="text-caveat text-state-warn"></p>
        <div class="flex justify-end gap-s2 pt-s2">
          <button type="button" data-cancel class="rounded-sm border border-rule-strong px-s3 py-[3px] text-caveat text-ink-muted hover:text-ink">Cancel</button>
          <button type="submit" class="rounded-sm border border-accent px-s3 py-[3px] text-caveat text-accent-ink hover:bg-paper-surface">${isEdit ? 'Save changes' : 'Register'}</button>
        </div>
      </form>
    </div>`;
  document.body.appendChild(el);
  el.addEventListener('click', (e) => { if (e.target === el) closeFormModal(); });
  el.querySelector('[data-cancel]').addEventListener('click', closeFormModal);

  el.querySelector('[data-form]').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = (name) => el.querySelector(`[data-f="${name}"]`).value.trim();
    const errorEl = el.querySelector('[data-error]');
    const type = isEdit ? existing.type : f('type');
    const displayName = f('display_name');
    const description = f('description');
    if (!type || !displayName || !description) {
      errorEl.textContent = 'Type key, display name and description are required.';
      return;
    }
    const body = {
      type,
      display_name: displayName,
      description,
      properties: f('properties').split(',').map((s) => s.trim()).filter(Boolean),
      egeria_type: f('egeria_type'),
      python_class: f('python_class'),
    };
    try {
      if (isEdit) await updateAnnotationType(type, body);
      else await registerAnnotationType(body);
      closeFormModal();
      await refresh(type);
    } catch (err) {
      errorEl.textContent = err.message || 'Save failed.';
    }
  });
}

/* ── Delete confirmation, with blast radius ────────────────────────────── */

async function confirmDelete(typeName) {
  let message = `Delete annotation type "${typeName}"? `;
  try {
    const usage = await getAnnotationTypeUsage(typeName);
    message += usage.note;
  } catch (_) {
    // The usage lookup itself failed — say so plainly rather than letting a
    // network error be read as "usage is definitely zero, safe to delete".
    message += 'Its usage could not be checked just now (lookup failed), so '
      + 'how many recorded annotations reference it is UNKNOWN, not zero.';
  }
  if (!(await inPageConfirm({ message: message, confirmLabel: 'Confirm' }))) return;
  try {
    await deleteAnnotationType(typeName);
    _selected = null;
    await refresh(null);
  } catch (err) {
    await inPageNotice({ message: err.message || 'Failed to delete annotation type.' });
  }
}

/* ── render/bind ─────────────────────────────────────────────────────────── */

function renderCurrent() {
  if (!_host) return;
  _host.innerHTML = _selected ? detailHtml(_selected) : listHtml();
  bind(_host);
}

async function refresh(selectType) {
  _types = await listAnnotationTypes();
  _selected = selectType ? (_types.find((a) => a.type === selectType) || null) : null;
  renderCurrent();
}

function bind(host) {
  host.querySelectorAll('[data-select-type]').forEach((r) => r.addEventListener('click', () => {
    _selected = (_types || []).find((a) => a.type === r.dataset.selectType) || null;
    renderCurrent();
  }));
  const back = host.querySelector('[data-back]');
  if (back) back.addEventListener('click', () => { _selected = null; renderCurrent(); });

  const register = host.querySelector('[data-register]');
  if (register) register.addEventListener('click', () => openFormModal(null));

  const edit = host.querySelector('[data-edit]');
  if (edit) edit.addEventListener('click', () => openFormModal(_selected));

  const delBtn = host.querySelector('[data-delete]');
  if (delBtn) delBtn.addEventListener('click', () => confirmDelete(delBtn.dataset.delete));
}

export async function renderAnnotationTypes(host) {
  _host = host;
  _selected = null;
  _types = await listAnnotationTypes();
  renderCurrent();
}
