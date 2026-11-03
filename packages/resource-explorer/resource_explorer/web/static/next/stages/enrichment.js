/* Enrichment — testimony, not paperwork.
 *
 * Moved out of app.js (PLAN-FINISH-REPOS.md, Part 2 §1): app.js keeps
 * routing, shared state and chrome; each stage owns its own pane's
 * rendering logic. `renderEnrichment` is called from app.js's generic
 * Questions-checklist engine when `state.stage === 'enrichment'` (see
 * `loadPane()`'s big shared function in app.js) — that engine itself stays
 * in app.js because it is shared machinery every built stage uses, not
 * Enrichment's alone. `renderEnrichmentEvidence` is exported too, though
 * only ever called from within `renderEnrichment` in this same module; it
 * stayed exported to mirror how app.js named it and in case a future
 * change needs to trigger the evidence rail from outside this file.
 */
import { ago, whenMs } from '/static/next/format.js';
import { inPageConfirm } from '/static/next/confirm-dialog.js';
import { getBulkFacts, listAnalyses, saveEnrichmentField, getDocSources, addDocSource, recheckDocSource, removeDocSource } from '/static/re-api.js';
import { state, esc, $, tnum, factGlyph, ensureRailShowing, railClaim, railTag, apiEntityType } from '/static/next/app.js';
// The row anatomy (who + when + "⚠ review — evidence moved: X") shared with
// the Questions tab's human-question answer rows — see row-anatomy.js's own
// header comment (ENRICHMENT-E0-ROW-ANATOMY). This is the row anatomy's
// ORIGINAL home: it used to be built inline below, and now calls out to the
// shared function instead so the Questions tab can call the exact same one.
import { personRowLineHtml } from '/static/next/row-anatomy.js';
import { glyphSpan } from '/static/next/glyphs.js';
import { observationState, sameFact } from '/static/next/observation-state.js';
import { RETENTION_BASES, resolveRetention } from '/static/next/retention-basis.js';
import { purposesNow, loadPurposes, isListed, notInListCueHtml, purposeSourceHtml } from '/static/next/purposes.js';

/* ── Enrichment: testimony, not paperwork ──────────────────────────────────
 *
 * Two halves, not one list of eight fields with one Save. The line between
 * them is not arbitrary: a field is a JUDGEMENT if a person's opinion is the
 * value — sensitivity, criticality, intended use, actual use, owner — and an
 * OBSERVATION if a person is supplying a fact about the world — license,
 * environment, retention. Opinions need an author, a date and a review
 * state. Observations need a source.
 *
 * Judgements come first and get the room: they are the part only a person
 * can supply. Every field saves alone — someone who knows the owner and not
 * the sensitivity can say so and leave. The author and date sit on every
 * judgement without hovering, because testimony without an author is not
 * testimony, and because that is what makes review meaningful later.
 *
 * Perishability, which needs no per-field configuration: a judgement carries
 * the measurements that were on screen when it was made. When one of those
 * moves, the judgement is not invalidated — it gets a flag that says what
 * moved. "⚠ review — evidence moved: cve_scan" is a specific, answerable
 * prompt, not a staleness timer.
 *
 * Evidence sits in the rail as MATERIAL, never as proposals. No "apply
 * suggestion". The one exception is a fact a survey already established —
 * the license — which is offered to confirm, with its source, into the
 * observations half.
 *
 * Nothing here is written to the catalogue until Curate. That sentence is
 * the one misconception worth pre-empting, and it is on the pane.
 */
/** What a field's "feeds" line says when no publish step reads it yet (said, not left to imply Curate does). */
export const KEPT_IN_RE = 'kept in Resource Explorer · no publish step reads it yet';
export const BACKUP_STATUSES = ['yes', 'no', 'partial', 'unknown'];
export const JUDGEMENTS = [
  { key: 'sensitivity',  label: 'Sensitivity',  options: ['public', 'internal', 'confidential', 'restricted'] },
  { key: 'criticality',  label: 'Criticality',  options: ['low', 'important', 'critical'] },
  // PI-098: replaces Classic's Purpose. Owner ruling 2026-10-13: offers the investigation-purposes list AND
  // takes free text; the two are stored apart (`picked`, `free_text`) so the text can be mined.
  { key: 'intended_use', label: 'Intended use', placeholder: 'or in your own words', purposes: true },
  { key: 'actual_use',   label: 'Actual use',   placeholder: 'how is it used today?' },
  { key: 'owner',        label: 'Owner',        placeholder: 'who answers for it?' },
  // PI-098: the rest of Classic's context form. Kept in RE and mirrored to the flat keys Classic reads
  // (context.py `_MIRROR`); no publish step reads them yet, and the `feeds` line says so.
  { key: 'steward',      label: 'Steward',      placeholder: 'who looks after its data quality?', feeds: KEPT_IN_RE },
  { key: 'notes',        label: 'Notes',        placeholder: 'anything else a reader should know', multiline: true, feeds: KEPT_IN_RE },
];
export const OBSERVATIONS = [
  { key: 'licence',      label: 'License',      fromAnalysis: 'license_classification' },
  { key: 'environment',  label: 'Environment',  options: ['prod', 'dev', 'test', 'research', 'archive'] },
  { key: 'retention',    label: 'Retention',    basis: true, placeholder: 'how long, and under whose retention rule?' },
  { key: 'location',      label: 'Location',      placeholder: 'where is it held? e.g. us-west-2, EU', feeds: KEPT_IN_RE },
  { key: 'backup_status', label: 'Backup status', options: BACKUP_STATUSES, feeds: KEPT_IN_RE },
];
// The analyses whose current state is the evidence for a judgement.
const ENRICHMENT_EVIDENCE = ['interface_surface', 'security_scan', 'chaoss_metrics', 'cve_scan',
  'repository_health', 'license_classification', 'secret_scan', 'documentation_coverage'];

function evidenceSnapshot() {
  const snap = {};
  for (const [id, f] of Object.entries(state.enrichmentFacts || {})) if (f.last_run_at) snap[id] = f.last_run_at;
  return snap;
}

/** Which of a judgement's evidence has moved since it was made. */
function movedSince(field) {
  const moved = [];
  for (const [id, at] of Object.entries(field.evidence || {})) {
    const now = state.enrichmentFacts?.[id]?.last_run_at;
    // Instants, not strings: `Z`, `+00:00` and naive stamps all occur, and
    // a string compare between spellings fires or fails on the suffix.
    if (now && whenMs(now) > whenMs(at)) moved.push(id);
  }
  return moved;
}

/** Retention: a drop-down of Egeria's basis values plus a free-text NOTE (the old question). The state
 *  is a cue on the element with a short word: 'pick one' (nothing stored) or 'set from existing' (an old
 *  free-text value was read as Project lifetime, its text kept as the note). Read-time only. */
function retentionControlHtml(def, field, size) {
  const r = resolveRetention(field);
  const opts = RETENTION_BASES.map((b) => `<option value="${b.name}" title="${esc(b.hint)}" ${b.name === r.basis ? 'selected' : ''}>${esc(b.label)}</option>`).join('');
  const cue = r.carried
    ? `<span data-retention-cue="carried" class="shrink-0 text-provenance text-ink-muted">set from existing · ${esc(RETENTION_BASES.find((b) => b.name === r.basis).label)}</span>`
    : (!r.basis ? `<span data-retention-cue="unset" class="shrink-0 text-provenance text-state-warn">pick one</span>` : '');
  return `<select data-field="${def.key}" class="rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] text-provenance text-ink">
      <option value="" ${r.basis ? '' : 'selected'}>—</option>${opts}</select>
    ${cue}
    <input data-note="${def.key}" type="text" value="${esc(r.note)}" placeholder="${esc(def.placeholder || '')}"
      class="min-w-0 flex-1 rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] text-provenance text-ink placeholder:text-ink-muted">`;
}

/** Intended use's (picked, free text), from the split fields, or -- a row written before the split -- from
 *  its single value: a list value reads as picked, anything else as free text. */
export function purposeFieldParts(field, names) {
  if (!field) return { picked: '', free: '' };
  if ('picked' in field || 'free_text' in field) return { picked: field.picked || '', free: field.free_text || '' };
  const v = field.value || '';
  return isListed(v, names) ? { picked: v, free: '' } : { picked: '', free: v };
}

/** The cue when either half is outside the list -- never when there is no list to compare against. */
function purposeCueHtml(picked, free, names) {
  const off = (t) => t && names.length && !isListed(t, names);
  return off(picked) || off(free) ? notInListCueHtml() : '';
}

/** Pick from the purposes list, or type, or both. A picked value no longer in the list stays selected. */
function purposeControlHtml(def, field, size) {
  const vocab = purposesNow();
  const names = vocab?.purposes || [];
  const { picked, free } = purposeFieldParts(field, names);
  const opts = (vocab?.values || []).map((p) => `<option value="${esc(p.name)}" title="${esc(p.description || '')}" ${
    picked && isListed(picked, [p.name]) ? 'selected' : ''}>${esc(p.name)}</option>`).join('');
  const kept = picked && !isListed(picked, names) ? `<option value="${esc(picked)}" selected>${esc(picked)}</option>` : '';
  // Literal branches, not `${size}`: Tailwind (and its coverage test) must see every class it compiles.
  return `<select data-purpose-pick="${def.key}" class="rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] ${size === 'text-answer' ? 'text-answer' : 'text-provenance'} text-ink">
      <option value="">—</option>${opts}${kept}</select>
    <input data-field="${def.key}" type="text" value="${esc(free)}" placeholder="${esc(def.placeholder || '')}"
      class="min-w-0 flex-1 rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] ${size === 'text-answer' ? 'text-answer' : 'text-provenance'} text-ink placeholder:text-ink-muted">
    <span data-purpose-cue-slot="${def.key}" class="shrink-0">${purposeCueHtml(picked, free, names)}</span>`;
}

function fieldControlHtml(def, field, kind = 'judgement') {
  const v = field?.value || '';
  // Judgements are the larger set on purpose; the recorded facts sit a
  // step down, at provenance size, so the split is visible, not narrated.
  const size = kind === 'judgement' ? 'text-answer' : 'text-provenance';
  if (def.basis) return retentionControlHtml(def, field, size);
  if (def.purposes) return purposeControlHtml(def, field, size);
  if (def.options) {
    return `<select data-field="${def.key}" class="rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] ${size} text-ink">
      <option value="">—</option>
      ${def.options.map((o) => `<option value="${o}" ${o === v ? 'selected' : ''}>${o}</option>`).join('')}
    </select>`;
  }
  if (def.multiline) {
    return `<textarea data-field="${def.key}" rows="2" placeholder="${esc(def.placeholder || '')}"
      class="w-full rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] text-provenance text-ink placeholder:text-ink-muted">${esc(v)}</textarea>`;
  }
  return `<input data-field="${def.key}" type="text" value="${esc(v)}" placeholder="${esc(def.placeholder || '')}"
    class="w-full rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] ${size} text-ink placeholder:text-ink-muted">`;
}

/** What the survey measures NOW for an observation with a proposing
 *  analysis, as `{ value, at }` -- or null. Null also when the analysis does
 *  not apply to this resource's kind: a database can never be proposed a
 *  licence, so its measurement is absent by construction, not by luck. */
function measurementFor(def) {
  if (!def.fromAnalysis || !analysisApplies(def.fromAnalysis)) return null;
  const p = proposedFrom(def.fromAnalysis);
  return p ? { value: p.value, at: p.at } : null;
}

/** True when the catalog says `analysisId` applies to this kind. Unknown
 *  (catalog unread) counts as applicable so nothing is claimed about absence. */
function analysisApplies(analysisId) {
  const a = state.enrichmentApplicable;
  return !a || a.has(analysisId);
}

/** The observation row's state line(s), drawn from `observationState()` over
 *  persisted rows -- every state word derives from rows that prove it. */
function observationStateHtml(def, field, st) {
  const signed = (verb) => personRowLineHtml({
    author: field?.author, whenIso: field?.set_at, verb,
    sourceLine: field?.source && field.source !== 'user' ? `from ${esc(field.source)}` : '',
  });
  const m = st.measured;
  const measuredBeside = (label) => `<span data-observation-measured>${label} <span class="text-ink">${esc(m || st.stored)}</span>
    (${esc(def.fromAnalysis)})</span>`;
  switch (st.state) {
    case 'proposed': {
      const meas = measurementFor(def);
      return `<div data-observation-state="proposed" class="text-provenance text-ink-muted">
        proposed: <span>${esc(m)}</span> · proposed by <span class="font-mono">${esc(def.fromAnalysis)}</span>${
          meas?.at ? ` · <span class="tnum">${esc(ago(meas.at))}</span>` : ''} ·
        <button type="button" data-confirm="${def.key}" data-source="${esc(def.fromAnalysis)}" data-value="${esc(m)}"
          class="cursor-pointer bg-transparent p-0 text-accent-ink underline">accept</button></div>`;
    }
    case 'confirmed': {
      // The person's value now equals what the survey measures, but the
      // measurement their row was made against was different: say so, so the
      // stale stored measurement is not silently invisible.
      const meas = measurementFor(def);
      const agrees = st.stored && m && !sameFact(st.stored, m);
      return `<div data-observation-state="confirmed" class="text-provenance text-ink-muted">${signed('confirmed by')}</div>${
        agrees ? `<div data-observation-agrees class="text-provenance text-ink-muted">survey now agrees${
          meas?.at ? ` · <span class="tnum">${esc(ago(meas.at))}</span>` : ''}</div>` : ''}`;
    }
    case 'overridden':
      return `<div data-observation-state="overridden" class="text-provenance text-ink-muted">${signed('set by')}</div>
        <div class="text-provenance text-ink-muted">${measuredBeside('measured:')}</div>`;
    case 'disagrees':
      return `<div data-observation-state="disagrees" class="text-provenance text-ink-muted">
        <span class="text-state-warn">⚠ review — survey now measures ${esc(m)}${st.stored ? ` (was ${esc(st.stored)})` : ''}</span> ·
        ${signed('recorded by')}</div>
        <div class="text-provenance text-ink-muted">${measuredBeside('now measured:')} ·
          <button type="button" data-confirm="${def.key}" data-source="${esc(def.fromAnalysis)}" data-value="${esc(m)}"
            class="cursor-pointer bg-transparent p-0 text-accent-ink underline">accept measured</button> ·
          <button type="button" data-keep="${def.key}" data-kind="observation"
            class="cursor-pointer bg-transparent p-0 text-accent-ink underline">keep mine</button></div>`;
    default:
      return `<div data-observation-state="none" class="text-provenance text-ink-muted">not measured yet ·
        <span class="font-mono">${esc(def.fromAnalysis)}</span> has no result to propose</div>`;
  }
}

/** The database owner role a survey measured (`pg_database.datdba`), on the
 *  OWNER judgement row, as MATERIAL: owner is a judgement, so the measured
 *  role is never offered as a value to accept -- no button, no data-confirm.
 *  Drawn only for databases, and only once Context has read the evidence
 *  (`state.ownerMaterialFor`), so a form that never fetched it cannot claim
 *  "not measured yet". */
function ownerMaterialHtml() {
  if (state.resourceType !== 'db' || state.ownerMaterialFor !== state.selectedSlug) return '';
  // Its OWN fact (`database_owner`), never schema_inventory's tables list.
  const fact = state.enrichmentFacts?.database_owner;
  const owner = fact && fact.state === 'measured' ? fact.value?.owner : '';
  const body = owner
    ? `database owner role: <span class="text-ink">${esc(owner)}</span> (measured)`
    : 'database owner role: not measured yet · run a survey';
  return `<div data-owner-material class="text-provenance text-ink-muted">${body}</div>`;
}

/** The one summary line for judgements whose evidence moved: the analyses that moved (union over the judgements),
 *  and how many judgements it touches. '' when none moved. Same source as each row's cue (`movedSince`). */
export function movedSummaryHtml() {
  const ids = [];
  let rows = 0;
  for (const d of JUDGEMENTS) {
    const f = (state.enrichment || {})[d.key];
    const m = f ? movedSince(f) : [];
    if (m.length) rows += 1;
    for (const id of m) if (!ids.includes(id)) ids.push(id);
  }
  if (!rows) return '';
  return `<div data-moved-summary class="mb-s2 text-provenance"><span class="text-state-warn">⚠ review — evidence moved: ${esc(ids.join(', '))}</span>
    <span class="text-ink-muted"> · ${rows} judgement${rows === 1 ? '' : 's'} made before ${ids.length === 1 ? 'it' : 'they'} last ran</span></div>`;
}

export function fieldRowHtml(def, kind) {
  const field = (state.enrichment || {})[def.key];
  const moved = field && kind === 'judgement' ? movedSince(field) : [];
  // An observation with a proposing analysis (licence) is drawn by the four
  // observation states (E3); every other row keeps the plain anatomy.
  const proposing = kind === 'observation' && !!def.fromAnalysis;
  const applies = proposing && analysisApplies(def.fromAnalysis);
  const st = proposing && applies ? observationState({ field, measurement: measurementFor(def) }) : null;
  // Judgements carry an author; observations carry a source. The server
  // stamps `author` on every field, so a source-only branch was dead code
  // and a confirmed license read as "alice · 2d ago" with its source stored
  // and invisible. Both halves render now, in that order. Built through the
  // shared row anatomy (row-anatomy.js) — the same function the Questions
  // tab's human-answer rows call.
  // The evidence-moved warning is said ONCE per resource (`movedSummaryHtml`, above the judgements); a row carries
  // only a small cue that jumps to it. The same eight analyses repeated on every row stopped being read.
  const movedCue = moved.length
    ? `<button type="button" data-moved-jump title="evidence moved: ${esc(moved.join(', '))}" class="cursor-pointer bg-transparent p-0 text-state-warn underline">⚠ review</button>${field?.author ? ' · ' : ''}`
    : '';
  const who = proposing ? '' : movedCue + personRowLineHtml({
    author: field?.author,
    whenIso: field?.set_at,
    sourceLine: kind === 'observation' && field?.source ? `from ${esc(field.source)}` : '',
    suffix: field?.interim ? ' · interim' : '',
  });
  // Databases (and any kind the analysis does not apply to): plain words, and
  // a typed value becomes confirmed at once -- nothing can propose it.
  const noSurvey = proposing && !applies
    ? `<div data-observation-state="no-survey" class="text-provenance text-ink-muted">no survey measures this for ${esc(kindNoun())}</div>
       ${field?.value ? `<div class="text-provenance text-ink-muted">${personRowLineHtml({ author: field.author, whenIso: field.set_at, verb: 'confirmed by' })}</div>` : ''}`
    : '';
  // "What we judge" is the larger of the two sets -- the split's whole
  // argument -- so its labels are body size in ink, not caption size muted.
  const labelCls = kind === 'judgement' ? 'text-question font-heading text-ink' : 'text-provenance text-ink-muted';
  return `<div class="grid grid-cols-[minmax(8.5rem,14rem)_1fr] items-baseline gap-x-s3 gap-y-[2px] border-b border-rule py-s2">
    <div class="${labelCls}">${esc(def.label)}</div>
    <div class="min-w-0">
      <div class="flex items-baseline gap-s2">${fieldControlHtml(def, field, kind)}
        <button type="button" data-save="${def.key}" data-kind="${kind}"
          class="shrink-0 cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-provenance text-accent-ink">Save</button></div>
      <div class="text-provenance text-ink-muted">${who}</div>
      ${def.purposes ? purposeSourceHtml(purposesNow()) : ''}
      ${st ? observationStateHtml(def, field, st) : noSurvey}
      ${def.key === 'owner' ? ownerMaterialHtml() + ownerNoteHtml(field) : ''}
    </div>
  </div>`;
}

/** Owner candidates from contributor data are DEFERRED, and a deferred
 *  affordance is marked, never omitted (the sub-tab rule, app.js above).
 *  Until one is named, the investigator stands as interim owner -- offered
 *  as a one-click act by the signed-in person, not inferred from a blank. */
function ownerNoteHtml(field) {
  const me = (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';
  const offer = !field?.value && me
    ? ` · <button type="button" data-owner-interim="${esc(me)}"
        class="cursor-pointer bg-transparent p-0 text-accent-ink underline">stand as interim owner</button>`
    : '';
  return `<div class="text-provenance text-ink-muted"><span class="border-b border-dashed border-current">owner candidates
    from contributor data · not built yet</span>${offer}</div>`;
}

/** A fact a survey already established, offered to confirm — not applied.
 *  Gated on a CLASSIFIED license: "No license detected on this repository."
 *  is a measured finding too, and offering it to confirm would write that
 *  sentence into the license field. The tier finding's label says which. */
function proposedFrom(analysisId) {
  const f = state.enrichmentFacts?.[analysisId];
  if (!f || f.state !== 'measured') return null;
  const tier = (f.value?.findings || []).find((x) => x.check_name === 'license_risk_tier');
  // `none` is both "no license" and "nothing examined"; `unknown` is a
  // license that IS present and unclassified -- its name is still a fact.
  if (!tier || !tier.label || String(tier.label) === 'none') return null;
  // The license itself, not its risk tier: the finding's label is
  // "permissive" and its summary is "Apache License 2.0 — Permissive". The
  // part before the dash is the fact a person would confirm.
  const raw = tier.summary || f.headline || '';
  if (!raw.includes(' — ')) return null;
  const value = String(raw).split(' — ')[0].trim();
  return value ? { value, at: f.last_run_at || '' } : null;
}

export async function renderEnrichment(slug) {
  const host = $('enrichment-form');
  // Nothing calls this since E1 moved the form to the Context tab, and the
  // Questions pane no longer carries #enrichment-form. If something calls it
  // again it must fail loudly, not draw nothing.
  if (!host) throw new Error('Enrichment pane host missing: #enrichment-form is not in the document');
  host.innerHTML = `<div class="text-caveat text-ink-muted">Reading the evidence…</div>`;
  await fetchEnrichmentEvidence(slug);
  if (slug !== state.selectedSlug) return;
  renderEnrichmentForm(slug);
}

/** The evidence fetch, BY KIND (ENRICHMENT-E3 §1). It used to pass no entity
 *  type, so the server answered for `repo` whatever the resource was, and a
 *  database's rail listed repository analyses as "never run" -- a false claim,
 *  since they do not apply to a database at all. Now: the resource's REAL
 *  entity type, and only the evidence analyses the catalog declares for that
 *  kind (`resource_types` in analysis_catalog.yaml, via `listAnalyses`).
 *  An inapplicable analysis is absent, not "never run".
 *
 *  Sets `state.enrichmentFacts` (measurements) and `state.enrichmentApplicable`
 *  (the Set of catalogued analysis ids for this kind, or null when the
 *  catalog could not be read -- absence of knowledge, said as such). */
export async function fetchEnrichmentEvidence(slug) {
  const entityType = apiEntityType(state.resourceType);
  let applicable = null;
  try {
    const catalog = await listAnalyses(entityType);
    if (Array.isArray(catalog)) applicable = new Set(catalog.map((a) => a.id));
  } catch { applicable = null; }
  const ids = applicable ? ENRICHMENT_EVIDENCE.filter((id) => applicable.has(id)) : [];
  let facts = {};
  if (ids.length) {
    try {
      const res = await getBulkFacts([slug], ids, entityType);
      facts = Object.fromEntries(((res.subjects || {})[slug] || []).map((f) => [f.analysis_id, f]));
    } catch { facts = {}; }
  }
  // A late response for a resource the person has already left must not
  // overwrite the current one's facts -- state.enrichmentFacts is shared,
  // unscoped-by-slug state, and the rail reads it.
  if (slug === state.selectedSlug) {
    state.enrichmentFacts = facts;
    state.enrichmentApplicable = applicable;
  }
}

const KIND_NOUN = { repo: 'repositories', database: 'databases', filesystem: 'filesystems' };
function kindNoun() { return KIND_NOUN[apiEntityType(state.resourceType)] || 'this kind of resource'; }

/** The rail's honest "loading" frame, written the moment Context starts
 *  reading a DIFFERENT resource than the one the rail last showed, so the
 *  previous resource's measurements are never on screen under the new one's
 *  name. */
export function renderEnrichmentEvidenceLoading(slug) {
  const out = $('rail-evidence');
  if (!out) return;
  railTag(out, slug);
  out.innerHTML = `
    <div class="mb-s1 flex items-baseline gap-s2">
      <span class="font-heading uppercase tracking-caps text-caps text-accent-on-dark">Evidence · enrichment</span>
      <span class="text-caps text-chrome-muted">for <span class="font-mono">${esc(slug)}</span></span>
    </div>
    <div class="text-caps text-chrome-muted">Reading the evidence…</div>`;
}

/** Re-render the form in place from already-fetched `state.enrichmentFacts`
 *  — no evidence re-fetch, no "Reading the evidence…" wipe. A save changes
 *  one field's value, not the survey evidence behind it, so re-running the
 *  full fetch-then-blank-then-rebuild `renderEnrichment` after every save
 *  was flashing the *entire* form to a loading placeholder and back for the
 *  ~2s the fetch took — every field, not just the one just saved. Call this
 *  from the save handlers below instead; `renderEnrichment` (the fetching
 *  one) stays for the initial pane load, where there is nothing on screen
 *  yet to flicker. */
function renderEnrichmentForm(slug) {
  const host = $('enrichment-form');
  if (!host) return;

  const setJ = JUDGEMENTS.filter((d) => state.enrichment?.[d.key]?.value).length;
  host.innerHTML = `
    <p class="mb-s3 max-w-[70ch] text-caveat text-ink-muted">Nothing here reaches Egeria until you publish it (Curate).
      What you set here is testimony — yours, dated — and the surveys' findings in the rail are material to read, not answers to accept.</p>
    <div class="mb-s1 flex items-baseline gap-s2">
      <span class="font-heading text-question text-ink">What we judge</span>
      <span class="text-provenance text-ink-muted"><span class="tnum">${setJ}</span> of <span class="tnum">${JUDGEMENTS.length}</span> set · perishable</span>
    </div>
    ${movedSummaryHtml()}
    ${JUDGEMENTS.map((d) => fieldRowHtml(d, 'judgement')).join('')}
    <div class="mb-s1 mt-s4 flex items-baseline gap-s2">
      <span class="text-caps uppercase tracking-caps text-ink">What we record</span>
      <span class="text-provenance text-ink-muted">durable</span>
    </div>
    ${OBSERVATIONS.map((d) => fieldRowHtml(d, 'observation')).join('')}
    ${['db', 'filesystem'].includes(state.resourceType) ? `<div id="doc-sources-block" class="mt-s4"></div>` : ''}
    <div class="mb-s1 mt-s4 text-caps uppercase tracking-caps text-ink">What only you can answer</div>
    <div class="mb-s2 text-provenance text-ink-muted">The catalog's own questions for a person, below — each saves alone.</div>`;

  host.querySelectorAll('[data-moved-jump]').forEach((b) => b.addEventListener('click', () => host.querySelector('[data-moved-summary]')?.scrollIntoView?.({ block: 'center' })));
  wireEnrichmentFieldControls(host, slug, () => renderEnrichmentForm(slug));
  renderEnrichmentEvidence(slug);
  if (['db', 'filesystem'].includes(state.resourceType)) renderDocSources(slug);
}

/** Save-control wiring for the judgement/observation rows built by
 *  `fieldRowHtml` above (`[data-save]`/`[data-owner-interim]`/
 *  `[data-confirm]`) — extracted (ENRICHMENT-E1-CONTEXT-TAB) so
 *  `stages/context.js`'s Context tab can wire the SAME controls against its
 *  own container, rather than reimplementing the save/confirm/interim-owner
 *  logic a second time. `rerender` is called after every successful save —
 *  `renderEnrichmentForm` here, `renderContext` there — so each caller
 *  re-renders its own pane, not the other one's. */
export function wireEnrichmentFieldControls(host, slug, rerender) {
  const me = (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';
  // Intended use offers the purposes list: load it once, then redraw with it (a failed load is remembered
  // with its words, so this never loops).
  if (host.querySelector('[data-purpose-pick]') && !purposesNow()) loadPurposes().then(() => rerender());
  host.querySelectorAll('[data-purpose-pick]').forEach((sel) => {
    const key = sel.dataset.purposePick;
    const input = host.querySelector(`[data-field="${key}"]`);
    const slot = host.querySelector(`[data-purpose-cue-slot="${key}"]`);
    const sync = () => { if (slot) slot.innerHTML = purposeCueHtml(sel.value.trim(), (input?.value || '').trim(), purposesNow()?.purposes || []); };
    sel.addEventListener('change', sync);
    input?.addEventListener('input', sync);
  });
  host.querySelectorAll('[data-save]').forEach((b) => b.addEventListener('click', async () => {
    const key = b.dataset.save; const kind = b.dataset.kind;
    const ctl = host.querySelector(`[data-field="${key}"]`);
    let value = (ctl?.value || '').trim();
    // A purpose field sends its two halves apart; `value` joins them for every reader of one string.
    const pick = host.querySelector(`[data-purpose-pick="${key}"]`);
    const split = pick ? { picked: pick.value.trim(), free_text: value } : {};
    if (pick) value = [split.picked, split.free_text].filter(Boolean).join(' — ');
    const note = (host.querySelector(`[data-note="${key}"]`)?.value || '').trim();
    b.disabled = true; b.textContent = 'saving…';
    try {
      const out = await saveEnrichmentField(slug, key, {
        ...split,
        value, note, kind, evidence: kind === 'judgement' ? evidenceSnapshot() : {},
        // Interim means "the investigator stands in", which is a person
        // naming themself -- not a blank. A blank owner is no owner.
        interim: key === 'owner' && !!value && value === me,
        source: kind === 'observation' ? 'user' : '',
      }, apiEntityType(state.resourceType));
      state.enrichment = { ...(state.enrichment || {}), [key]: out.field };
      rerender();
    } catch (err) {
      b.disabled = false;
      b.textContent = err.status === 401 ? 'sign in to record' : `not saved: ${err.message}`;
    }
  }));
  host.querySelectorAll('[data-owner-interim]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true; b.textContent = 'recording…';
    try {
      const out = await saveEnrichmentField(slug, 'owner', {
        value: b.dataset.ownerInterim, kind: 'judgement', evidence: evidenceSnapshot(), interim: true,
      }, apiEntityType(state.resourceType));
      state.enrichment = { ...(state.enrichment || {}), owner: out.field };
      rerender();
    } catch (err) {
      b.disabled = false;
      b.textContent = err.status === 401 ? 'sign in to record' : `not recorded: ${err.message}`;
    }
  }));
  // "keep mine": the person re-chooses their own value after a survey began
  // measuring something else. The write re-stamps the current measurement
  // server-side, which is the only thing that clears "⚠ review".
  host.querySelectorAll('[data-keep]').forEach((b) => b.addEventListener('click', async () => {
    const key = b.dataset.keep;
    b.disabled = true; b.textContent = 'recording…';
    try {
      const cur = state.enrichment?.[key];
      // Retention keeps its basis (a legacy text resolved to one) and its note: a re-save must neither 422 nor clear the note.
      const r = key === 'retention' ? resolveRetention(cur) : null;
      const out = await saveEnrichmentField(slug, key, {
        value: r ? r.basis : (cur?.value || ''), note: r ? r.note : (cur?.note || ''), kind: 'observation', source: 'user',
      }, apiEntityType(state.resourceType));
      state.enrichment = { ...(state.enrichment || {}), [key]: out.field };
      rerender();
    } catch (err) {
      b.disabled = false;
      b.textContent = err.status === 401 ? 'sign in to record' : `not recorded: ${err.message}`;
    }
  }));
  host.querySelectorAll('[data-confirm]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true; b.textContent = 'confirming…';
    try {
      const out = await saveEnrichmentField(slug, b.dataset.confirm, {
        value: b.dataset.value, note: state.enrichment?.[b.dataset.confirm]?.note || '', kind: 'observation', source: b.dataset.source,
      }, apiEntityType(state.resourceType));
      state.enrichment = { ...(state.enrichment || {}), [b.dataset.confirm]: out.field };
      rerender();
    } catch (err) {
      b.disabled = false;
      b.textContent = err.status === 401 ? 'sign in to record' : `not confirmed: ${err.message}`;
    }
  }));
}

/* ── Documentation sources ───────────────────────────────────────────────
 * BRIEF-DATABASE-DOCUMENTATION-SOURCES.md slice 1, "Declare and probe".
 * A person points RE at a URL that documents this resource elsewhere; RE
 * probes it read-only and reports reachable/needs_sign_in/not_found/blocked
 * with the HTTP status and fetch time. Ingest/re-ingest (slice 2) is shown
 * as a disabled "coming soon" affordance rather than omitted, so that slice
 * has a known place to attach.
 */
const DOC_SOURCE_TYPES = [
  { value: 'data_dictionary', label: 'data dictionary' },
  { value: 'design_notes', label: 'design notes' },
  { value: 'runbook', label: 'runbook' },
  { value: 'wiki', label: 'wiki' },
  { value: 'installation_guide', label: 'installation guide' },
  { value: 'user_manual', label: 'user manual' },
  { value: 'api_reference', label: 'api reference' },
  { value: 'release_notes', label: 'release notes' },
  { value: 'other', label: 'other' },
];

// E2 (2026-09-29, design reply §3): probe states route through glyphs.js's
// one table -- the local PROBE_GLYPH (a fifth glyph table, with an
// off-vocabulary `●`) is gone. The glyph and its wording are one decision.
// timed_out / unreachable / blocked share the `?` family: none of them
// establishes anything about the page, but they are three DIFFERENT facts
// (no answer in time / never answered / answered with a refusal).
const PROBE_GLYPH_STATE = {
  reachable: 'measured', needs_sign_in: 'scoped', not_found: 'error',
  blocked: 'not_established', timed_out: 'not_established', unreachable: 'not_established',
};

function probeGlyphHtml(src) {
  if (!src.probe_state) return glyphSpan('unrun');
  return glyphSpan(PROBE_GLYPH_STATE[src.probe_state] || 'not_established');
}

/** The probe line's wording, per state. Returns {text, used_error}: the
 *  states that already say why (timed out / unreachable) suppress the
 *  separate probe_error span so the reason is not shown twice. */
function probeWording(src) {
  const code = src.probe_status_code ? `HTTP ${src.probe_status_code}` : '';
  switch (src.probe_state) {
    case 'reachable': return { text: ['reachable', code].filter(Boolean).join(' · '), usedError: false };
    case 'needs_sign_in': return { text: ['reachable behind a sign-in', code].filter(Boolean).join(' · '), usedError: false };
    case 'not_found': return { text: `not found${code ? ` · ${code}` : ''} — the link is broken, not the site`, usedError: false };
    case 'blocked': return { text: `blocked by the site${code ? ` · ${code}` : ''}`, usedError: true };
    case 'timed_out': {
      const secs = src.probe_ms != null ? `${(src.probe_ms / 1000).toFixed(1)}s` : 'no answer';
      return { text: `timed out · ${secs} · re-check`, usedError: true };
    }
    case 'unreachable': {
      const why = (src.probe_error || 'no answer').slice(0, 120);
      return { text: `unreachable · ${why} · re-check`, usedError: true };
    }
    default: return { text: src.probe_state || 'not probed yet', usedError: false };
  }
}

// Egeria publish-state fix (2026-09-29, extended round 4 same day) — every
// row carries exactly one of these FIVE states, never blank. Originally
// `publishNote` rendered '' for ANY published resource regardless of
// whether THIS source actually made it to Egeria (confirmed live: an
// adventureworks source sat local-only, origin='local', on an already-
// published resource, with nothing on the row saying so) — round 1 fixed
// that to four states. Round 4 added `not_catalogued`: a row with a ref
// guid but no link guid and nothing pending/running used to render
// `publishing` with no outbox row backing that claim at all (a second
// find-absence-as-answer bug, this time about the STATE ITSELF rather than
// the header note) — see `derive_doc_source_egeria_state` (backend) for the
// exact rule.
const EGERIA_STATE_TEXT = {
  catalogued: () => 'published in Egeria',
  publishing: () => 'local — publishing…',
  publish_failed: (src) => `local — publish failed: ${src.egeria_state_detail || 'unknown error'}, retrying`,
  not_catalogued: () => 'local — not published (publish needed)',
  local_only: () => 'local only — resource not published',
};
const EGERIA_STATE_TONE = {
  catalogued: 'text-state-ok', publishing: 'text-ink-muted',
  publish_failed: 'text-state-warn', not_catalogued: 'text-state-warn',
  local_only: 'text-ink-muted',
};

// Egeria publish-state fix, round 4 (2026-09-29): the add/remove HTTP
// response renders the PRE-drain state — `_attempt_outbox_row_immediately`
// (web/routes/doc_sources.py) fires the real Egeria write on a background
// thread specifically so the request is not held open for it, so the
// response legitimately cannot know the outcome yet. The bug was that
// nothing EVER re-fetched afterward: live-verified 2026-09-29 (a clean
// retry, source "pdr") that the server-side drain completed in ~4s — ref
// and link both created, outbox row 'done' — while the page kept showing
// "local — publishing…" forever, because no code path re-rendered the block
// once that background thread finished. Same idiom `pollActivity` (re-
// api.js) already establishes elsewhere in this codebase for "started an
// operation off the request thread, need to reflect its own completion" —
// bounded rather than indefinite, so a row Egeria genuinely cannot reach
// (down, or retries exhausted into 'dead') stops polling and falls back to
// showing whatever real state the last fetch returned, not an infinite spin.
let docSourcesPollTimer = null;
const DOC_SOURCES_POLL_MS = 2000;
const DOC_SOURCES_POLL_MAX_MS = 30000;

function stopDocSourcesPoll() {
  if (docSourcesPollTimer) {
    clearTimeout(docSourcesPollTimer);
    docSourcesPollTimer = null;
  }
}

function scheduleDocSourcesPoll(slug, entityType, deadline) {
  stopDocSourcesPoll();
  if (Date.now() >= deadline) return; // cap reached — leave the last fetch's state showing
  docSourcesPollTimer = setTimeout(async () => {
    docSourcesPollTimer = null;
    if (slug !== state.selectedSlug) return; // navigated away — nothing to update
    let data;
    try {
      data = await getDocSources(entityType, slug);
    } catch {
      // A transient fetch failure during the poll is not the same as the
      // publish itself failing — keep polling within the same deadline
      // rather than giving up on the first blip.
      scheduleDocSourcesPoll(slug, entityType, deadline);
      return;
    }
    if (slug !== state.selectedSlug) return;
    renderDocSourcesFromData(slug, entityType, data, deadline);
  }, DOC_SOURCES_POLL_MS);
}

function docSourceRowHtml(src) {
  const w = probeWording(src);
  const timeBit = (src.probe_ms != null && ['reachable', 'needs_sign_in', 'not_found', 'blocked'].includes(src.probe_state))
    ? ` · ${src.probe_ms}ms` : '';
  const whenBit = src.probed_at ? ` · probed ${esc(ago(src.probed_at))}` : (src.probe_state ? '' : ' · not probed yet');
  const typeLabel = (DOC_SOURCE_TYPES.find((t) => t.value === src.source_type) || {}).label || src.source_type;
  const originBit = src.origin === 'egeria' ? ' · <span class="text-ink-muted">declared in Egeria</span>' : '';
  const egeriaState = src.egeria_state || 'local_only';
  const egeriaText = (EGERIA_STATE_TEXT[egeriaState] || EGERIA_STATE_TEXT.local_only)(src);
  const egeriaTone = EGERIA_STATE_TONE[egeriaState] || 'text-ink-muted';
  // The ref GUID is surfaced via `title` rather than in the row's own text --
  // the brief's "behind/near the evidence link" convention, not clutter on
  // the line itself.
  const egeriaTitle = egeriaState === 'catalogued' && src.egeria_state_detail
    ? ` title="ExternalReference ${esc(src.egeria_state_detail)}"` : '';
  // E2 §3: signed like every other Context row -- one shared component
  // (row-anatomy.js), on its own line so the probe line stays purely a
  // probe line. An unsigned legacy row says so rather than showing nothing.
  const signature = personRowLineHtml({ author: src.added_by || 'unknown', whenIso: src.added_at || '', verb: 'added by' });
  return `<div class="border-b border-rule py-s2" data-source-row="${esc(src.id)}">
    <div class="flex items-baseline gap-s2">
      <span data-doc-probe-glyph="${esc(src.id)}">${probeGlyphHtml(src)}</span>
      <a href="${esc(src.url)}" target="_blank" rel="noopener" class="min-w-0 flex-1 truncate text-question text-accent-ink underline">${esc(src.label || src.url)}</a>
      <span class="text-provenance text-ink-muted">${esc(typeLabel)}</span>
    </div>
    <div class="pl-[20px] text-provenance text-ink-muted" data-doc-probe-line="${esc(src.id)}">
      ${esc(w.text)}${timeBit}${whenBit}${originBit}
      ${src.probe_error && !w.usedError ? ` · <span class="text-state-warn">${esc(src.probe_error)}</span>` : ''}
    </div>
    <div class="pl-[20px] text-provenance text-ink-muted" data-doc-ingest="${esc(src.id)}">ingestion not built yet</div>
    <div class="pl-[20px] text-provenance"${egeriaTitle}>
      <span class="${egeriaTone}" data-doc-egeria-state="${esc(src.id)}">${esc(egeriaText)}</span>
    </div>
    <div class="pl-[20px] text-provenance text-ink-muted" data-doc-signature="${esc(src.id)}">${signature}</div>
    <div class="pl-[20px] text-provenance text-ink-muted">feeds → nothing reads this yet</div>
    <div class="pl-[20px] mt-[2px] flex items-baseline gap-s3 text-provenance">
      <button type="button" data-doc-recheck="${esc(src.id)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">re-check</button>
      <button type="button" data-doc-remove="${esc(src.id)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline">remove</button>
    </div>
  </div>`;
}

// Exported for the /next render harness (frontend-build/test-harness) —
// same pattern app.js uses for surveyRowHtml/schemaTreeHtml/tableHtml: no
// logic changed, only visibility, so a test can call it directly rather
// than driving the whole Enrichment pane bootstrap.
export async function renderDocSources(slug, { title = true } = {}) {
  const host = $('doc-sources-block');
  if (!host) return;
  // `title: false` omits this block's own "Documentation sources" heading
  // (Context supplies its own section heading). Remembered on the host so
  // the block's internal re-renders (add/remove/recheck, the poll) keep it
  // without threading the option through every call.
  if (title) delete host.dataset.omitTitle; else host.dataset.omitTitle = '1';
  stopDocSourcesPoll(); // a fresh render supersedes any poll from a prior one
  const entityType = apiEntityType(state.resourceType);
  host.innerHTML = `<div class="text-caveat text-ink-muted">Loading documentation sources…</div>`;
  let data;
  try {
    data = await getDocSources(entityType, slug);
  } catch (err) {
    host.innerHTML = `<div class="text-caveat text-state-warn">Could not load documentation sources: ${esc(err.message)}</div>`;
    return;
  }
  if (slug !== state.selectedSlug) return;
  renderDocSourcesFromData(slug, entityType, data, Date.now() + DOC_SOURCES_POLL_MAX_MS);
}

// Split from `renderDocSources` (round 4, 2026-09-29) so the poll tick can
// re-render from a fresh fetch WITHOUT re-showing "Loading…" (that flicker
// on every 2s tick would be worse than the bug it fixes) and without
// re-deriving its own copy of "is anything still publishing". `deadline` is
// an absolute `Date.now()`-scale timestamp threaded through so the total
// poll budget is fixed from the first render, not restarted every tick.
function renderDocSourcesFromData(slug, entityType, data, deadline) {
  const host = $('doc-sources-block');
  if (!host) return;
  const sources = data.sources || [];
  // Egeria publish-state fix (2026-09-29): the header used to say nothing
  // once `data.published` was true, regardless of whether any given source
  // had actually made it to Egeria — this counts the real per-row states
  // instead of a single resource-level boolean. `in_egeria_count`/
  // `local_count` come from the same server-side count as each row's own
  // `egeria_state` (server response), so the two can never disagree; a
  // client-side recount from `sources` would be a second copy of that logic
  // to keep in sync.
  const inEgeria = data.in_egeria_count || 0;
  const local = data.local_count != null ? data.local_count : sources.length - inEgeria;
  const countsLine = `<span class="tnum">${sources.length}</span> declared`
    + (sources.length ? ` · <span class="tnum">${inEgeria}</span> in Egeria · <span class="tnum">${local}</span> local` : '');
  // A stale-linkage warning (or any other publish_note the resource-level
  // egeria_linkage check hands back) is a fact about the RESOURCE's own
  // Egeria asset link, distinct from any one source's state — kept as a
  // second line whenever present, never folded into a row's state text.
  const staleNote = data.publish_note
    ? `<div class="text-provenance text-state-warn">${esc(data.publish_note)}</div>` : '';
  host.innerHTML = `
    <div class="mb-s1 flex items-baseline gap-s2">
      ${host.dataset.omitTitle ? '' : '<span class="font-heading text-question text-ink">Documentation sources</span>'}
      <span class="text-provenance text-ink-muted">${countsLine}</span>
    </div>
    ${staleNote}
    ${sources.length ? sources.map(docSourceRowHtml).join('') : `<div class="text-provenance text-ink-muted">No documentation sources declared yet.</div>`}
    <div class="mt-s2 grid grid-cols-[1fr_140px_150px_auto] items-baseline gap-s2">
      <input id="doc-source-url" type="text" placeholder="https://…" class="w-full rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] text-answer text-ink placeholder:text-ink-muted">
      <input id="doc-source-label" type="text" placeholder="label" class="w-full rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] text-answer text-ink placeholder:text-ink-muted">
      <select id="doc-source-type" class="rounded-sm border border-rule-strong bg-transparent px-[6px] py-[2px] text-answer text-ink">
        ${DOC_SOURCE_TYPES.map((t) => `<option value="${t.value}">${esc(t.label)}</option>`).join('')}
      </select>
      <button type="button" id="doc-source-add" class="shrink-0 cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[1px] text-provenance text-accent-ink">Save and probe</button>
    </div>
    <div id="doc-source-add-status" class="mt-[2px] text-provenance text-ink-muted"></div>`;

  host.querySelectorAll('[data-doc-recheck]').forEach((b) => b.addEventListener('click', async () => {
    b.disabled = true; b.textContent = 'checking…';
    try {
      await recheckDocSource(entityType, slug, b.dataset.docRecheck);
      renderDocSources(slug);
    } catch (err) {
      b.disabled = false; b.textContent = `not checked: ${err.message}`;
    }
  }));
  host.querySelectorAll('[data-doc-remove]').forEach((b) => b.addEventListener('click', async () => {
    // E2: ask once before deleting. Declining leaves the row and the button
    // exactly as they were -- no request, no state change.
    const row = b.closest('[data-source-row]');
    const name = row?.querySelector('a')?.textContent || 'this source';
    if (!(await inPageConfirm({ title: 'Remove documentation source?', message: `Remove documentation source "${name}"?`, confirmLabel: 'Remove' }))) return;
    b.disabled = true; b.textContent = 'removing…';
    try {
      await removeDocSource(entityType, slug, b.dataset.docRemove);
      renderDocSources(slug);
    } catch (err) {
      b.disabled = false; b.textContent = `not removed: ${err.message}`;
    }
  }));
  const addBtn = $('doc-source-add');
  addBtn?.addEventListener('click', async () => {
    const url = ($('doc-source-url')?.value || '').trim();
    const label = ($('doc-source-label')?.value || '').trim();
    const sourceType = $('doc-source-type')?.value || 'other';
    const statusEl = $('doc-source-add-status');
    if (!url) { statusEl.textContent = 'enter a URL first'; return; }
    addBtn.disabled = true; addBtn.textContent = 'saving…';
    if (statusEl) statusEl.textContent = 'probing…';
    try {
      await addDocSource(entityType, slug, { url, label, sourceType });
      renderDocSources(slug);
    } catch (err) {
      addBtn.disabled = false; addBtn.textContent = 'Save and probe';
      if (statusEl) statusEl.textContent = err.status === 401 ? 'sign in to add a source' : `not added: ${err.message}`;
    }
  });

  // Round 4 fix: keep polling while ANY row is still `publishing` — the
  // background `_attempt_outbox_row_immediately` thread this state depends
  // on runs off the request that produced this very data, so the only way
  // to learn it finished is to ask again. Stops on its own once no row is
  // `publishing` (resolved to `catalogued`/`publish_failed`) or the
  // deadline passes, whichever comes first.
  if (sources.some((s) => s.egeria_state === 'publishing')) {
    scheduleDocSourcesPoll(slug, entityType, deadline);
  }
}

/** The rail: evidence as material. Each analysis's own sentence, its age, and
 *  "new since you judged" where its run postdates the latest judgement that
 *  saw it — the perishability mechanism doing its job at the moment it is
 *  useful. */
export function renderEnrichmentEvidence(slug) {
  const out = $('rail-evidence');
  if (!out) return;
  // Rail state is a persisted preference; "new since you judged" written
  // into a closed drawer is the perishability signal nobody sees. The DOM,
  // not the preference, says whether it is showing.
  ensureRailShowing();
  railClaim();
  railTag(out, slug);
  const judged = Object.values(state.enrichment || {}).filter((f) => f.kind === 'judgement' && f.set_at);
  const items = ENRICHMENT_EVIDENCE.map((id) => state.enrichmentFacts?.[id]).filter(Boolean);
  const applicable = state.enrichmentApplicable;
  // Absence is a sentence, never an empty panel and never "never run" rows for
  // analyses that do not apply to this kind (E3 §1).
  const none = !applicable
    ? `Could not read which analyses apply to ${esc(kindNoun())}.`
    : !ENRICHMENT_EVIDENCE.some((id) => applicable.has(id))
      ? `no enrichment evidence is listed for ${esc(kindNoun())} yet`
      : 'No measurements to show yet.';
  out.innerHTML = `
    <div class="mb-s1 flex items-baseline gap-s2">
      <span class="font-heading uppercase tracking-caps text-caps text-accent-on-dark">Evidence · enrichment</span>
      <span class="text-caps text-chrome-muted">for <span class="font-mono">${esc(slug)}</span> · material, not proposals</span>
    </div>
    ${items.length ? items.map((f) => {
      const g = factGlyph(f.state);
      const seen = judged.some((j) => j.evidence?.[f.analysis_id]);
      const fresh = judged.some((j) => j.evidence?.[f.analysis_id] && whenMs(f.last_run_at) > whenMs(j.evidence[f.analysis_id]));
      return `<div class="border-b border-chrome-line-soft py-[4px]">
        <div class="flex items-baseline gap-s2 text-subtab text-chrome-ink">
          <span class="${g.tone === 'text-state-ok' ? 'text-state-ok-on-dark' : g.tone === 'text-state-warn' ? 'text-state-warn-on-dark' : 'text-chrome-muted'}">${g.glyph}</span>
          <span class="min-w-0 flex-1">${tnum(esc(f.headline || f.state))}</span></div>
        <div class="pl-[20px] text-caps text-chrome-muted"><span class="font-mono">${esc(f.analysis_id)}</span>${
          f.last_run_at ? ` · <span class="tnum">${esc(ago(f.last_run_at))}</span>` : ''}${
          fresh ? ` · <span class="text-accent-on-dark">new since you judged</span>` : seen ? '' : ''}</div>
      </div>`;
    }).join('') : `<div class="text-caps text-chrome-muted" data-rail-empty>${none}</div>`}
    <div class="mt-s2 text-caps text-chrome-muted">Material to read, not answers to accept. No "apply suggestion".</div>`;
}
