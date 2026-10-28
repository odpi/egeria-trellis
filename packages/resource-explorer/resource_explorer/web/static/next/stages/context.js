/* Enrichment — Context, the stage's new first sub-tab (ENRICHMENT-E1-
 * CONTEXT-TAB, replying to docs/design-notes/exchange/REPLY-DESIGNER-ENRICHMENT-
 * STAGE-IA.md §1). Builds on E0 (ENRICHMENT-E0-ROW-ANATOMY): the shared row
 * anatomy (`row-anatomy.js`'s `personRowLineHtml`) and the inline
 * question-answer editor E0 built with no Context tab to live in yet.
 *
 * Five sections, in the reply's own order (§1):
 *   1. What we judge        — the judgement fields, moved here from the old
 *                              merged Enrichment/Questions pane.
 *   2. What you're looking for — the lens. Read-only, sourced from the
 *                              investigation, not the resource (§1: "the
 *                              lens belongs to the investigation").
 *   3. What we record       — the observation fields.
 *   4. What only you can answer — the catalog's human questions, with E0's
 *                              inline editor now given a real home.
 *   5. Where it's documented — #348's block (db and filesystem), mounted
 *                              unchanged via `renderDocSources`; E2 applies
 *                              the reply's corrections in place.
 *
 * Every row ends with a "feeds →" line (§1) naming which downstream
 * consumer(s) read the field — see `feedsLine()`.
 *
 * Judgements/observations reuse `stages/enrichment.js`'s `fieldRowHtml` and
 * its save-control wiring unchanged (`wireEnrichmentFieldControls`,
 * extracted from that file's `renderEnrichmentForm` so both this tab and
 * that file's own render path share one implementation rather than two).
 */
import { ago } from '/static/next/format.js';
import { getContext, getBulkFacts, saveQuestionAnswer, questionKey } from '/static/re-api.js';
import { state, esc, $, apiEntityType, railKeyOf } from '/static/next/app.js';
import { personRowLineHtml } from '/static/next/row-anatomy.js';
import {
  JUDGEMENTS, OBSERVATIONS, fieldRowHtml, wireEnrichmentFieldControls,
  renderEnrichmentEvidence, renderEnrichmentEvidenceLoading, fetchEnrichmentEvidence, renderDocSources,
} from '/static/next/stages/enrichment.js';
import { renderApiReferenceBlock } from '/static/next/stages/api-reference.js';

/** §1: "feeds → Curate (Confidentiality) · Assessment: 'is it safe to use'"
 *  — who reads this field, derived from what the catalog already records
 *  about it, not invented YAML (the brief's own instruction: derive from
 *  existing metadata, add nothing new unless derivation is genuinely
 *  impossible for a row).
 *
 *  Judgements/observations are NOT `question_catalog.yaml` rows — they are
 *  `EnrichmentField` keys on `context.py`'s fixed record, so there is no
 *  per-field "Answering Analysis" metadata to derive from at all; that is
 *  the "genuinely impossible" case the brief names. What IS recorded,
 *  directly in code (`enrichment.js`'s own header comment, unchanged since
 *  E0): "Nothing here is written to the catalogue until Curate." That is a
 *  real, verifiable downstream consumer — every judgement/observation feeds
 *  Curate's catalogue-of-record write. `licence` additionally names its
 *  survey source via the catalog's own `fromAnalysis` field (already on
 *  `OBSERVATIONS`, E0-era) rather than a fabricated cross-reference. */
function fixedFieldFeedsLine(def) {
  if (def.feeds) return `<div class="text-provenance text-ink-muted">feeds → ${esc(def.feeds)}</div>`;
  const parts = ['Curate (publish record)'];
  if (def.fromAnalysis) parts.push(`sourced from ${def.fromAnalysis}`);
  return `<div class="text-provenance text-ink-muted">feeds → ${esc(parts.join(' · '))}</div>`;
}

/** For a catalog human question: the analyses its own `answering` block
 *  names (`analysis_ids`) are the only declared consumers there are. With
 *  none, the honest line is "feeds → nothing reads this yet" -- NEVER the catalog's
 *  free-text `note` / `answering_mechanism` (CSV prose such as "N/A —
 *  human-supplied ... may also be Agent over RAG content"), which describes
 *  how a question might be answered, not who consumes the answer. */
function questionFeedsLine(entry) {
  const ids = entry.analysis_ids || [];
  const text = `feeds → ${ids.length ? ids.join(', ') : 'nothing reads this yet'}`;
  return `<div class="text-provenance text-ink-muted">${esc(text)}</div>`;
}

/* ── 2. What you're looking for: the lens (read-only) ─────────────────── */

/** §1: "no lens declared for A sample db investigation · declare it on the
 *  investigation ›" + a quiet secondary "try one on this resource only"
 *  (the existing `lens_source: ad_hoc` mechanism).
 *
 *  Neither link target exists as a built UI today — confirmed by search
 *  (grep `lens_declared`/`DataLens`/`lens_source` across the package):
 *  `db_derived.py`'s own header comment on `preliminary_fit` says a full,
 *  versioned, investigation-level `DataLens` with a framing-form UI is
 *  explicitly out of scope for that change, and nothing in
 *  `stages/investigation.js` or `web/routes/investigations.py` mentions a
 *  lens at all. `lens_source: ad_hoc` is a label `compute_preliminary_fit`
 *  stamps on a supplied-but-unlabeled lens dict — there is no click path
 *  that supplies one. So this row reads the absence honestly (from
 *  `preliminary_fit`'s own last-read `lens_declared` flag — the one real
 *  stored signal that exists) and marks BOTH links "not built in /next",
 *  the same dashed-underline convention every other deferred affordance in
 *  this codebase uses, rather than inventing a route to a feature that
 *  isn't there. See ENRICHMENT-E1-CONTEXT-TAB-IMPLEMENTED.md, "What I could
 *  not find," for the full account — same shape as E0's own finding about
 *  the reply doc's branch placement. */
function lensRowHtml() {
  const fit = state.enrichmentFacts?.preliminary_fit;
  const declared = !!(fit && fit.value && fit.value.lens_declared);
  const invName = (state.investigations || []).find((i) => i.slug === state.investigation)?.display_name
    || state.investigation || 'this investigation';
  const declareLink = `<span class="cursor-not-allowed border-b border-dashed border-current text-ink-muted"
    title="declare a lens on the investigation — not built in /next">declare it on the investigation ›</span>`;
  const tryLink = `<span class="cursor-not-allowed border-b border-dashed border-current text-ink-muted"
    title="lens_source: ad_hoc exists server-side; no UI declares one yet — not built in /next">try one on this resource only</span>`;
  const body = declared
    ? `<span class="text-ink">a lens is declared for ${esc(invName)}</span>`
    : `<span class="text-ink">no lens declared for ${esc(invName)}</span> · ${declareLink}`;
  return `<div class="grid grid-cols-[130px_1fr] items-baseline gap-x-s3 gap-y-[2px] border-b border-rule py-s2">
    <div class="text-question font-heading text-ink">What you're looking for</div>
    <div class="min-w-0">
      <div class="text-answer">${body}</div>
      <div class="text-provenance text-ink-muted">${tryLink}</div>
      <div class="text-provenance text-ink-muted">feeds → preliminary_fit (Enrichment, once declared)</div>
    </div>
  </div>`;
}

/* ── 4. What only you can answer: the catalog's human questions ───────── */

/** One human-question row, E0's row anatomy, this tab's own home for it
 *  (§0.3/§6 item 1 of the reply: "Context's editor in place of
 *  `window.prompt`" — E0 built the mechanism with nowhere to put it yet;
 *  this is that home). Independent of app.js's `rowShell`/`bodyLines`
 *  (the Questions tab's own, unchanged, full-catalog rendering) rather than
 *  sharing its i-indexed DOM machinery, which is scoped to the Questions
 *  tab's own `#question-rows` full list — reusing `personRowLineHtml` (the
 *  actual shared row-anatomy component) satisfies "wire it in here, using
 *  E0's own row-anatomy rendering" without entangling two independent
 *  panes' DOM through the same array indices. */
function humanQuestionRowHtml(entry) {
  const key = questionKey(entry.question);
  const held = (state.contextAnswers || {})[key];
  const editing = state.editingContextAnswer === entry.question;
  const who = held?.answer
    ? (held.answered_by
        ? personRowLineHtml({ author: held.answered_by, whenIso: held.answered_at, verb: 'answered by' })
        : `answered ${esc(ago(held.answered_at))}`)
    : '';
  const editorHtml = editing ? `<div class="mt-s1 flex items-start gap-s2">
      <textarea data-context-answer-input rows="2"
        class="w-full max-w-[60ch] rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] text-answer text-ink"
        placeholder="${esc(entry.note || '')}">${esc(held?.answer || '')}</textarea>
      <div class="flex shrink-0 flex-col gap-[2px]">
        <button type="button" data-context-answer-save="${esc(entry.question)}"
          class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-provenance text-accent-ink">Save</button>
        <button type="button" data-context-answer-cancel="1"
          class="cursor-pointer bg-transparent p-0 text-provenance text-ink-muted underline">cancel</button>
      </div>
    </div>` : '';
  return `<div class="border-b border-rule py-s2">
    <div class="text-question font-heading text-ink">${esc(entry.question)}</div>
    <div class="pl-s4">
    ${held?.answer && !editing ? `<div class="text-answer text-ink">${esc(held.answer)}</div>` : ''}
    <div class="text-provenance text-ink-muted">
      ${who}
      ${!editing ? `<button type="button" data-context-answer-edit="${esc(entry.question)}"
        class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${held?.answer ? 'change' : 'Answer this →'}</button>` : ''}
    </div>
    ${editorHtml}
    ${questionFeedsLine(entry)}
    </div>
  </div>`;
}

function wireHumanQuestionControls(host, slug) {
  if (host.dataset.contextHumanWired === '1') return;
  host.dataset.contextHumanWired = '1';
  host.addEventListener('click', async (ev) => {
    const editBtn = ev.target.closest('[data-context-answer-edit]');
    if (editBtn) {
      state.editingContextAnswer = editBtn.getAttribute('data-context-answer-edit');
      renderHumanQuestions(slug);
      host.querySelector('[data-context-answer-input]')?.focus();
      return;
    }
    if (ev.target.closest('[data-context-answer-cancel]')) {
      state.editingContextAnswer = '';
      renderHumanQuestions(slug);
      return;
    }
    const saveBtn = ev.target.closest('[data-context-answer-save]');
    if (saveBtn) {
      const question = saveBtn.getAttribute('data-context-answer-save');
      const input = host.querySelector('[data-context-answer-input]');
      const answer = (input?.value || '').trim();
      const label = saveBtn.textContent;
      saveBtn.disabled = true;
      saveBtn.textContent = 'saving…';
      try {
        const out = await saveQuestionAnswer(apiEntityType(state.resourceType), slug, question, answer);
        const key = questionKey(question);
        state.contextAnswers = { ...(state.contextAnswers || {}), [key]: out.answer };
        state.editingContextAnswer = '';
        renderHumanQuestions(slug);
      } catch (err) {
        saveBtn.disabled = false;
        saveBtn.textContent = err.status === 401 ? 'sign in to answer' : `not saved: ${err.message}`;
        setTimeout(() => { saveBtn.textContent = label; saveBtn.disabled = false; }, 4000);
      }
    }
  });
}

let _humanQuestions = [];

function renderHumanQuestions(slug) {
  const host = $('context-human-questions');
  if (!host) return;
  host.innerHTML = _humanQuestions.length
    ? _humanQuestions.map(humanQuestionRowHtml).join('')
    : `<div class="text-caveat text-ink-muted">No human questions for this stage.</div>`;
  wireHumanQuestionControls(host, slug);
}

/* ── 5. Where it's documented: #348's slot ─────────────────────────────── */

/** Documentation Sources (PR #348), mounted here unchanged: the block is
 *  `enrichment.js`'s own `renderDocSources`, byte-for-byte, into the same
 *  `#doc-sources-block` host the old Enrichment form gave it, for db, filesystem
 *  and (E2) repo. E2 applies the reply's §3 corrections in place.
 *  Other kinds get no section (heading included) rather than a "not built" line. */
const DOC_SOURCE_KINDS = ['db', 'filesystem', 'repo'];

function docSourcesSlotHtml() {
  if (!DOC_SOURCE_KINDS.includes(state.resourceType)) return '';
  return `<div class="mt-s4">
    <h3 class="m-0 mb-s1 mt-s4 font-heading text-name font-normal text-ink">Where it's documented</h3>
    <div class="mb-s2 text-provenance text-ink-muted">sources, not answers</div>
    <div id="doc-sources-block"></div>
  </div>`;
}

/** The API reference block (repositories only): the three sources, the registration control for a running
 *  instance's OpenAPI URL, and the way into the full reference. */
function apiReferenceSlotHtml() {
  if (state.resourceType !== 'repo') return '';
  return `<div class="mt-s4">
    <h3 class="m-0 mb-s1 mt-s4 font-heading text-name font-normal text-ink">API reference</h3>
    <div class="mb-s2 text-provenance text-ink-muted">where each list came from, and a running instance to read it from</div>
    <div id="api-reference-block"></div>
  </div>`;
}

/* ── Entry point ────────────────────────────────────────────────────────── */

// preliminary_fit: the lens row. database_owner: the measured database owner
// role (its own fact, `value.owner`), material for the owner judgement row.
const ENRICHMENT_EVIDENCE_FOR_LENS = ['preliminary_fit', 'database_owner'];

// The slug the rail's facts currently belong to. A re-render of the SAME
// resource (after a save) keeps its facts; a different resource clears them
// and shows "loading" until its own fetch lands -- never the previous one's
// measurements under the new name.
let _railFactsSlug = null;

export async function renderContext(slug) {
  const host = $('context-form');
  if (!host) return;
  // The rail is keyed to (resource, stage, sub-tab) -- see syncRailToSelection().
  // Every await below can land after the person moved on, so remember the key
  // this render belongs to and write nothing under a different one.
  const railKey = railKeyOf();
  const railEmpty = !$('rail-evidence')?.firstElementChild;
  if (_railFactsSlug !== slug) {
    state.enrichmentFacts = {};
    _railFactsSlug = slug;
    renderEnrichmentEvidenceLoading(slug);
  } else if (railEmpty) {
    // Same resource, but the rail was emptied because the pane was left and
    // re-entered: say it is loading rather than leave the slot blank.
    renderEnrichmentEvidenceLoading(slug);
  }
  host.innerHTML = `<div class="text-caveat text-ink-muted">Reading what's been supplied…</div>`;

  try {
    const ctx = await getContext(apiEntityType(state.resourceType), slug);
    state.contextAnswers = ctx?.question_answers || {};
    state.enrichment = ctx?.enrichment || {};
  } catch {
    state.contextAnswers = {};
    state.enrichment = {};
  }

  // The lens row reads preliminary_fit's own last-read `lens_declared` flag
  // (see lensRowHtml's header comment) — only fetched for databases, since
  // preliminary_fit is database-only. ALWAYS refetched, not reused from a
  // prior render: `state.enrichmentFacts` is shared, unscoped-by-slug
  // state (the same object `stages/enrichment.js`'s own `renderEnrichment`
  // unconditionally overwrites on every call, for the same reason) — a
  // "fetch only if empty" guard here would show a stale lens signal from
  // whichever resource was rendered last, on this one specifically.
  // Parity with the pre-E1 form (`renderEnrichment`): the same evidence
  // fetch, all resource kinds. It REPLACES state.enrichmentFacts, so it runs
  // first and the lens read below adds preliminary_fit on top.
  await fetchEnrichmentEvidence(slug);

  if (state.resourceType === 'db') {
    try {
      const res = await getBulkFacts([slug], ENRICHMENT_EVIDENCE_FOR_LENS, apiEntityType(state.resourceType));
      if (slug === state.selectedSlug) {
        state.enrichmentFacts = {
          ...(state.enrichmentFacts || {}),
          ...Object.fromEntries(((res.subjects || {})[slug] || []).map((f) => [f.analysis_id, f])),
        };
        state.ownerMaterialFor = slug;
      }
    } catch { /* the row degrades to "no lens declared" without it */ }
  }

  _humanQuestions = (state.questions || []).filter((q) => q.kind === 'human');

  if (slug !== state.selectedSlug || railKeyOf() !== railKey) return;

  const setJ = JUDGEMENTS.filter((d) => state.enrichment?.[d.key]?.value).length;
  host.innerHTML = `
    <p class="mb-s3 max-w-[70ch] text-caveat text-ink-muted">Everything a person has supplied about this
      resource — each row signed and dated, and each saying what it feeds. Nothing here is written to the
      Egeria until you publish it (Curate).</p>

    <div class="mb-s1 flex items-baseline gap-s2">
      <h3 class="m-0 mb-s1 mt-s4 font-heading text-name font-normal text-ink">What we judge</h3>
      <span class="text-provenance text-ink-muted"><span class="tnum">${setJ}</span> of <span class="tnum">${JUDGEMENTS.length}</span> set · perishable</span>
    </div>
    <div id="context-judgements">${JUDGEMENTS.map((d) => `${fieldRowHtml(d, 'judgement')}${fixedFieldFeedsLine(d)}`).join('')}</div>

    ${lensRowHtml()}

    <div class="mb-s1 mt-s4 flex items-baseline gap-s2">
      <h3 class="m-0 mb-s1 mt-s4 font-heading text-name font-normal text-ink">What we record</h3>
      <span class="text-provenance text-ink-muted">durable</span>
    </div>
    <div id="context-observations">${OBSERVATIONS.map((d) => `${fieldRowHtml(d, 'observation')}${fixedFieldFeedsLine(d)}`).join('')}</div>

    <h3 class="m-0 mb-s1 mt-s4 font-heading text-name font-normal text-ink">What only you can answer</h3>
    <div class="mb-s2 text-provenance text-ink-muted">The catalog's own questions for a person, below — each saves alone.</div>
    <div id="context-human-questions"></div>

    ${docSourcesSlotHtml()}
    ${apiReferenceSlotHtml()}
  `;

  wireEnrichmentFieldControls(host, slug, () => renderContext(slug));
  renderHumanQuestions(slug);
  // The old form rendered the rail for every resource kind.
  renderEnrichmentEvidence(slug);
  // Documentation sources, mounted as #348 built it (db and filesystem).
  if (DOC_SOURCE_KINDS.includes(state.resourceType)) renderDocSources(slug, { title: false });
  if (state.resourceType === 'repo') renderApiReferenceBlock(slug);
}
