/* Admin → Question Catalog — browse, add, retire.
 *
 * Started as a full port of classic's `loadAdminQuestionCatalogPanel`
 * (read-only), backed by GET /api/analyses/question-catalog
 * (analyses.py's `list_question_catalog`, itself backed by
 * `docs/dr-egeria/resource_questions.csv` via `question_catalog_reader.py`).
 * classic never had write UI here either — SPEC-ADMIN-THE-FOUR-GAPS.md §4
 * called this the backend gap of the two registries: there was genuinely no
 * write route to port.
 *
 * **Decision (project owner, 2026-09-20):** the catalog is append-only. You
 * may add a new question and retire an existing one; you may never reword
 * one — editing a question changes the meaning of answers already recorded
 * against it. `question_catalog_writer.py` enforces this on the backend
 * (an add whose text already exists 400s rather than upserting); there is
 * no edit form here to match, on either side.
 *
 * Retired questions are never hidden — a past survey answer still refers to
 * its question exactly as it was asked, so this screen shows retirement as
 * a status, reusing the same STATUS-chip pattern already used for
 * `answering.kind` below rather than inventing a second visual language for
 * "this row means something different now".
 */
import {
  listQuestionCatalog, addQuestionCatalogEntry, retireQuestionCatalogEntry,
  listAllPerspectives,
} from '/static/re-api.js';
import { esc } from '/static/next/app.js';
import { inPageConfirm, inPageNotice } from '/static/next/confirm-dialog.js';

const STATUS = {
  direct: { icon: '●', tone: 'text-state-ok', label: 'Answered directly from stored data' },
  analysis: { icon: '⚙', tone: 'text-accent-ink', label: 'Answered by running an analysis' },
  human: { icon: '◐', tone: 'text-state-warn', label: 'Needs a human answer' },
  mixed: { icon: '◑', tone: 'text-accent-ink', label: 'Mixed — some automatic, some human' },
  gap: { icon: '○', tone: 'text-ink-muted', label: 'No surveyor answers this yet' },
  chart: { icon: '📈', tone: 'text-accent-ink', label: 'Answered by a chart' },
};

/* Reused for the retired/active LIFECYCLE state, distinct from the
 * `answering.kind` STATUS map above — same visual grammar (icon + tone +
 * label chip), different axis: this says whether the question is still
 * being asked at all, not how it gets answered. */
const LIFECYCLE = {
  active: { icon: '●', tone: 'text-state-ok', label: 'Active' },
  retired: { icon: '⊘', tone: 'text-ink-muted', label: 'Retired — kept for past answers, no longer asked' },
};

function statusOf(kind) {
  return STATUS[kind] || { icon: '?', tone: 'text-ink-muted', label: kind || 'unclassified' };
}

let _data = null;
let _host = null;
const state = { stage: '', perspectives: new Set(), search: '' };

function stages() {
  const set = new Set();
  (_data || []).forEach((q) => set.add(q.stage));
  return [...set].filter(Boolean).sort();
}

function perspectives() {
  const set = new Set();
  (_data || []).forEach((q) => (q.perspectives || []).forEach((p) => set.add(p)));
  return [...set].sort();
}

function render() {
  if (!_host) return;
  const search = state.search.trim().toLowerCase();
  const questions = (_data || []).filter((q) => {
    if (state.stage && !q.stage.split('/').map((s) => s.trim()).includes(state.stage)) return false;
    if (state.perspectives.size && !(q.perspectives || []).some((p) => state.perspectives.has(p))) return false;
    if (search && !q.question.toLowerCase().includes(search)) return false;
    return true;
  });

  const stageChips = stages().map((s) => `<button type="button" data-stage="${esc(s)}"
    class="cursor-pointer rounded-sm border px-2 py-[2px] text-caveat
      ${s === state.stage ? 'border-accent text-accent-ink' : 'border-rule-strong text-ink-muted hover:text-ink'}"
    >${esc(s)}</button>`).join('');

  const perspChips = perspectives().map((p) => `<button type="button" data-persp="${esc(p)}"
    class="cursor-pointer rounded-full border px-2 py-[1px] text-provenance
      ${state.perspectives.has(p) ? 'border-accent text-accent-ink' : 'border-rule-strong text-ink-muted hover:text-ink'}"
    >${esc(p)}</button>`).join('');

  const rows = questions.map((q) => {
    const st = statusOf(q.answering.kind);
    const life = LIFECYCLE[q.retired ? 'retired' : 'active'];
    return `<tr class="border-b border-rule align-top hover:bg-paper-surface ${q.retired ? 'opacity-60' : ''}">
      <td class="whitespace-nowrap py-s2 pr-s3 text-caveat ${life.tone}" title="${esc(life.label)}">${life.icon}</td>
      <td class="max-w-[32ch] py-s2 pr-s3 text-caveat text-ink">${esc(q.question)}</td>
      <td class="whitespace-nowrap py-s2 pr-s3 text-provenance text-ink-muted" title="Funnel stage">${esc(q.stage)}</td>
      <td class="py-s2 pr-s3 text-provenance text-ink-muted">${(q.perspectives || []).map((p) => esc(p)).join(' · ') || '—'}</td>
      <td class="whitespace-nowrap py-s2 pr-s3 text-caveat ${st.tone}" title="${esc(st.label)}">${st.icon} ${esc(q.answering.kind)}</td>
      <td class="whitespace-nowrap py-s2 pr-s3 text-provenance text-ink-muted">${esc(q.answering_mechanism || '—')}</td>
      <td class="max-w-[32ch] py-s2 pr-s3 text-provenance text-ink-muted" title="${esc(q.answering.note || '')}">${esc(q.answering.note || '')}</td>
      <td class="whitespace-nowrap py-s2 text-provenance">${q.retired ? '' : `<button type="button" data-retire="${esc(q.question)}"
        class="cursor-pointer rounded-sm border border-rule-strong px-2 py-[1px] text-provenance text-ink-muted hover:text-state-warn hover:border-state-warn">Retire</button>`}</td>
    </tr>`;
  }).join('') || `<tr><td colspan="8" class="py-s4 text-center text-caveat text-ink-muted">No questions match this filter.</td></tr>`;

  _host.innerHTML = `
    <div class="mb-s3 flex items-start justify-between gap-s3">
      <div>
        <h3 class="m-0 font-heading text-name font-normal text-ink">❓ Question Catalog</h3>
        <p class="mt-[2px] max-w-[65ch] text-caveat text-ink-muted">Every question authored in
          <code>docs/dr-egeria/resource_questions.csv</code> — the source of truth for both
          Scouting's Questions checklist and the Question elements published to Egeria.
          <strong>Append-only</strong> (project owner decision, 2026-09-20): add a new question or
          retire an old one; a question's own text is never reworded in place, since past survey
          answers refer to it exactly as it was asked. Retired questions stay listed, dimmed, never
          hidden.</p>
      </div>
      <button type="button" data-add
        class="whitespace-nowrap rounded-sm border border-accent px-s2 py-[3px] text-caveat text-accent-ink hover:bg-paper-surface">
        + Add question
      </button>
    </div>
    <div class="my-s3 space-y-s2">
      <input data-search value="${esc(state.search)}" placeholder="Search question text…"
        class="w-full max-w-[360px] rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink" />
      <div>
        <div class="text-caps uppercase tracking-caps text-ink-muted">Stage</div>
        <div class="mt-[4px] flex flex-wrap gap-[6px]">${stageChips || '<span class="text-caveat text-ink-muted">—</span>'}</div>
      </div>
      ${perspectives().length ? `<div>
        <div class="text-caps uppercase tracking-caps text-ink-muted">Perspective (filter)</div>
        <div class="mt-[4px] flex flex-wrap gap-[6px]">${perspChips}</div>
      </div>` : ''}
    </div>
    <div class="mb-s2 text-provenance text-ink-muted">${questions.length} / ${(_data || []).length} question(s)</div>
    <div class="max-h-[46vh] overflow-auto rounded-sm border border-rule">
      <table class="w-full text-left">
        <thead><tr class="border-b border-rule text-caps uppercase tracking-caps text-ink-muted">
          <th class="px-s2 py-s2 font-normal"></th>
          <th class="px-s2 py-s2 font-normal">Question</th>
          <th class="px-s2 py-s2 font-normal">Stage</th>
          <th class="px-s2 py-s2 font-normal">Perspectives</th>
          <th class="px-s2 py-s2 font-normal">Kind</th>
          <th class="px-s2 py-s2 font-normal">Mechanism</th>
          <th class="px-s2 py-s2 font-normal">Note</th>
          <th class="px-s2 py-s2 font-normal"></th>
        </tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;

  bind();
}

function bind() {
  if (!_host) return;
  const search = _host.querySelector('[data-search]');
  if (search) search.addEventListener('input', (e) => { state.search = e.target.value; render(); });
  _host.querySelectorAll('[data-stage]').forEach((b) => b.addEventListener('click', () => {
    state.stage = state.stage === b.dataset.stage ? '' : b.dataset.stage;
    render();
  }));
  _host.querySelectorAll('[data-persp]').forEach((b) => b.addEventListener('click', () => {
    if (state.perspectives.has(b.dataset.persp)) state.perspectives.delete(b.dataset.persp);
    else state.perspectives.add(b.dataset.persp);
    render();
  }));
  const add = _host.querySelector('[data-add]');
  if (add) add.addEventListener('click', openAddModal);
  _host.querySelectorAll('[data-retire]').forEach((b) => b.addEventListener('click', () => confirmRetire(b.dataset.retire)));
}

/* ── Retire, with blast-radius-style confirmation ──────────────────────── */

async function confirmRetire(question) {
  const message = `Retire "${question}"? This does not delete it or any past survey answer that `
    + 'references it — it stops appearing as an open question and is kept in the catalog, shown '
    + 'dimmed, so old answers stay traceable to exactly the question they answered.';
  if (!(await inPageConfirm({ message: message, confirmLabel: 'Confirm' }))) return;
  try {
    await retireQuestionCatalogEntry(question);
    _data = await listQuestionCatalog('repo'); // explicit: this admin editor edits the repo catalog only
    render();
  } catch (err) {
    await inPageNotice({ message: err.message || 'Failed to retire question.' });
  }
}

/* ── Add question modal ─────────────────────────────────────────────────── */

function closeAddModal() {
  document.getElementById('question-catalog-add-modal')?.remove();
}

async function openAddModal() {
  closeAddModal();
  let allPerspectives = [];
  try { allPerspectives = await listAllPerspectives(); } catch (_) { allPerspectives = perspectives(); }

  const el = document.createElement('div');
  el.id = 'question-catalog-add-modal';
  el.className = 'fixed inset-0 z-[60] flex items-start justify-center bg-black/40 p-s4 overflow-auto';
  el.innerHTML = `
    <div class="mt-[6vh] w-full max-w-[560px] rounded bg-paper p-s4 shadow-lg" role="dialog" aria-modal="true"
      aria-label="Add question">
      <div class="font-heading text-name text-ink">Add question</div>
      <p class="mt-[2px] max-w-[60ch] text-caveat text-ink-muted">Append-only: this creates a new
        catalog entry. It cannot be used to reword an existing question — if the text already
        exists, the save is refused.</p>
      <div class="my-s3 h-px bg-rule"></div>
      <form data-form class="space-y-s2">
        <label class="block">
          <div class="text-caps uppercase tracking-caps text-ink-muted">Question</div>
          <textarea data-f="question" rows="2" required
            class="mt-[2px] w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink"></textarea>
        </label>
        <label class="block">
          <div class="text-caps uppercase tracking-caps text-ink-muted">Funnel stage</div>
          <input data-f="stage" placeholder="e.g. Scouting"
            class="mt-[2px] w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink" />
        </label>
        <label class="block">
          <div class="text-caps uppercase tracking-caps text-ink-muted">Why is this important?</div>
          <textarea data-f="why_important" rows="2"
            class="mt-[2px] w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink"></textarea>
        </label>
        <label class="block">
          <div class="text-caps uppercase tracking-caps text-ink-muted">Rationale / source</div>
          <textarea data-f="rationale" rows="2"
            class="mt-[2px] w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink"></textarea>
        </label>
        <label class="block">
          <div class="text-caps uppercase tracking-caps text-ink-muted">Answering mechanism</div>
          <input data-f="answering_mechanism" placeholder="e.g. Human-Supplied"
            class="mt-[2px] w-full rounded-sm border border-rule-strong bg-transparent px-2 py-[3px] text-caveat text-ink" />
        </label>
        <div>
          <div class="text-caps uppercase tracking-caps text-ink-muted">Perspectives</div>
          <div class="mt-[4px] flex flex-wrap gap-[6px]">${allPerspectives.map((p) => `
            <label class="flex cursor-pointer items-center gap-[4px] rounded-full border border-rule-strong px-2 py-[1px] text-provenance text-ink-muted has-[:checked]:border-accent has-[:checked]:text-accent-ink">
              <input type="checkbox" data-persp-check value="${esc(p)}" class="accent-current" />${esc(p)}
            </label>`).join('')}</div>
        </div>
        <p class="text-caveat text-ink-muted">New questions start as a declared gap ("GAP: not yet
          answered") — nothing here claims a surveyor already answers it until that's verified.</p>
        <p data-error class="text-caveat text-state-warn"></p>
        <div class="flex justify-end gap-s2 pt-s2">
          <button type="button" data-cancel class="rounded-sm border border-rule-strong px-s3 py-[3px] text-caveat text-ink-muted hover:text-ink">Cancel</button>
          <button type="submit" class="rounded-sm border border-accent px-s3 py-[3px] text-caveat text-accent-ink hover:bg-paper-surface">Add question</button>
        </div>
      </form>
    </div>`;
  document.body.appendChild(el);
  el.addEventListener('click', (e) => { if (e.target === el) closeAddModal(); });
  el.querySelector('[data-cancel]').addEventListener('click', closeAddModal);

  el.querySelector('[data-form]').addEventListener('submit', async (e) => {
    e.preventDefault();
    const f = (name) => el.querySelector(`[data-f="${name}"]`).value.trim();
    const errorEl = el.querySelector('[data-error]');
    const question = f('question');
    if (!question) { errorEl.textContent = 'Question text is required.'; return; }
    const selectedPerspectives = [...el.querySelectorAll('[data-persp-check]:checked')].map((c) => c.value);
    try {
      await addQuestionCatalogEntry({
        question,
        stage: f('stage'),
        perspectives: selectedPerspectives,
        purposes: [],
        whyImportant: f('why_important'),
        rationale: f('rationale'),
        answeringMechanism: f('answering_mechanism'),
      });
      closeAddModal();
      _data = await listQuestionCatalog('repo'); // explicit: this admin editor edits the repo catalog only
      render();
    } catch (err) {
      errorEl.textContent = err.message || 'Save failed.';
    }
  });
}

export async function renderQuestionCatalog(host) {
  _host = host;
  state.stage = '';
  state.perspectives = new Set();
  state.search = '';
  _data = await listQuestionCatalog('repo'); // explicit: this admin editor edits the repo catalog only
  render();
}
