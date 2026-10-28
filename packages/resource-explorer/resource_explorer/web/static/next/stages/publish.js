/* Publish to Egeria -- the section of Curate that says what Egeria holds for this resource.
 *
 * Parity slice G1 (PARITY-G1-IMPLEMENTED.md, rows PI-001..PI-009), Egeria on a repository. Classic
 * had an "Egeria" tab per repository; /next had a publish-state line in the resource header and nothing
 * else. There is no separate Publish stage in STAGES, so this is the fourth band of the Curate pane
 * ([data-curate-band="publish"], after the kind's own work), one component for every kind:
 *
 *   status    in Egeria or not, the asset GUID (copyable), and for a repository the last report row
 *   project   which Egeria project the publish names, or "no project" (PI-006)
 *   publish   "Publish to Egeria →", then "Publish again" (repository only; PI-001). Whole report, never
 *             scoped, no zone choice. A first press with no project answer shows the two choices.
 *   forget    "Forget Egeria links…" (repository only; PI-005): RE's registry only, Egeria untouched
 *   reports   ONE "Egeria reports" component for every kind (PI-003): `mountEgeriaReports`, exported
 *   file types  preview-then-commit (PI-004)
 *
 * THE RULE: every status word comes from what the server derived from proof rows (resource_explorer/
 * repo_publish.py). This file chooses how to say it; it never decides "published" from a click that got a
 * 200. Each action re-reads the state afterwards and draws from the re-read. A pressed control dims and
 * ignores a second press. State is shown by a glyph (the Egeria lane) plus a short word; the full Egeria
 * sentence is one gesture away (a <details>). Accent colors are for controls, never states.
 *
 * `mountEgeriaReports(el, { entityType, slug, inEgeria, onAsk })` is the entry point stage code reuses
 * (G3's runs dialog links a report GUID into it). See PARITY-G1-IMPLEMENTED.md.
 */
import { ago } from '/static/next/format.js';
import { stateEntry } from '/static/next/glyphs.js';
import {
  getRepoPublishState, publishRepoReport, forgetEgeriaLinks, resurveyRepo,
  getEgeriaReports, getEgeriaReportAnnotations, getFileTypeMeasurements, setRepoProjectContext,
} from '/static/re-api.js';
import { mountArchitecturePublish } from '/static/next/stages/architecture-publish.js';
import { commitStateLineHtml } from '/static/next/stages/curate-scope.js';
import {
  state, esc, $, ensureRailShowing, copyAsEvidence, openCurrentInvestigationStage, openCurate,
} from '/static/next/app.js';

/** The sentence a first press shows when no Egeria project answers (brief PI-006). */
export const NO_PROJECT_SENTENCE =
  'no Egeria project context · bind this investigation to a project, or publish without one';
/** What survives a "Forget Egeria links" (brief PI-005): roll-forward language, never "reset Egeria". */
export const FORGET_SENTENCE =
  'Egeria is unchanged; Resource Explorer forgets its cached GUIDs and survey history for this resource '
  + 'and re-reads them on the next publish.';

/* Buttons are built here, with their classes as literals in the markup: no dynamic class interpolation
 * (the class-coverage tripwire counts those, and a literal is what Tailwind's scanner can see). */
const button = (attrs, label, { disabled = false } = {}) =>
  `<button type="button" ${attrs} class="cursor-pointer rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink hover:border-accent disabled:cursor-default disabled:opacity-60"${disabled ? ' disabled' : ''}>${label}</button>`;

/** What the Publish button really does (repo_publish.publish_report, brief section 1): it publishes the
 *  survey ALREADY KEPT on this repository and reads the report back. It never runs a survey; "Re-survey
 *  now" is its own act, beside it. */
export const PUBLISH_SENTENCE =
  'Publishes the survey already kept on this repository to Egeria and reads the report back. '
  + 'It does not run a survey: Re-survey now does that, separately.';
/** The short words beside the button, idle and while it runs. */
export const PUBLISH_IDLE_WORDS = 'publishes the kept survey, tags, group and your feedback';
/** Brief F (owner, 2026-10-11): "we need to acknowledge that the user asked for a publish and validate its
 *  success". The press is acknowledged at once with this cue and word; no per-step list on tab 1. */
export const PUBLISH_RUNNING_WORDS = 'publishing…';
/** The sentence directly under a disabled Publish when nothing has been surveyed (brief section 1). */
export const NO_SURVEY_SENTENCE = 'no survey to publish yet · run the first survey';
export const SURVEYED_BEFORE_KEPT_SENTENCE = 'surveyed before 10-07 · nothing kept to publish · re-survey to publish';

/** The words for "no kept survey": the server's sentence, else the one its `surveyed_before_kept` flag names. */
function noKeptSentence(survey) {
  return survey?.sentence || (survey?.surveyed_before_kept ? SURVEYED_BEFORE_KEPT_SENTENCE : NO_SURVEY_SENTENCE);
}
/** What Re-survey now does, on hover. */
export const RESURVEY_SENTENCE =
  'Runs the survey on this repository and nothing else: nothing is sent to Egeria. The band then shows the new age.';
const linkButton = (attrs, label) =>
  `<button type="button" ${attrs} class="cursor-pointer bg-transparent text-accent-ink underline disabled:opacity-60">${label}</button>`;
const num = (n) => `<span class="tnum">${esc(n ?? 0)}</span>`;
const whoAmI = () => (state.me && (state.me.user_id || state.me.username || state.me.egeria_user)) || '';
const stale = (el, slug) => !el.isConnected || slug !== state.selectedSlug;

const cue = (stateKey, word, title = '') => {
  const e = stateEntry(stateKey);
  // The tone class is a literal in each branch (no class interpolation): ok, warn, or muted.
  const open = e.tone === 'text-state-ok' ? '<span class="text-state-ok"'
    : e.tone === 'text-state-warn' ? '<span class="text-state-warn"' : '<span class="text-ink-muted"';
  return `${open} data-cue="${esc(stateKey)}" title="${esc(title || e.word)}"><span class="font-glyph" aria-hidden="true">${e.glyph}</span> ${esc(word)}</span>`;
};

const guidHtml = (guid) => guid
  ? `<span class="font-mono" data-guid="${esc(guid)}">${esc(guid)}</span>
     ${linkButton(`data-copy-guid="${esc(guid)}" title="Copy the GUID"`, 'copy')}`
  : '';

function bindCopy(root) {
  root.querySelectorAll('[data-copy-guid]').forEach((b) => b.addEventListener('click', () =>
    copyAsEvidence(b.dataset.copyGuid, b)));
}

/** The full Egeria sentence, one gesture away. */
const detailsHtml = (full) => `<details class="inline"><summary class="inline cursor-pointer text-ink-muted underline">details</summary>
    <span class="block max-w-[70ch] whitespace-pre-wrap text-provenance text-ink-muted" data-egeria-sentence>${esc(full)}</span></details>`;

/** A sentence from Egeria: the first sentence shown, the rest one gesture away. */
const sentenceHtml = (first, full) => (!full || full === first) ? esc(first) : `${esc(first)} ${detailsHtml(full)}`;

/** Elements by attribute value without CSS.escape (a GUID is safe, but a label is not). */
const byAttr = (root, attr, value) => [...root.querySelectorAll(`[${attr}]`)].find((e) => e.getAttribute(attr) === value) || null;

/** The published report was made from an older survey than the kept one: a visible cue and a short word,
 *  the sentence on hover. Nothing when the two match, or when either date is unknown. */
export function behindCue(row, kept) {
  const a = Date.parse(row && row.surveyed_at);
  const b = Date.parse(kept && kept.exists ? kept.surveyed_at : '');
  if (Number.isNaN(a) || Number.isNaN(b) || a >= b) return '';
  return ` <span data-publish-behind>${cue('partial', 'behind', `The report in Egeria was made from the survey of ${String(row.surveyed_at).slice(0, 10)}; the kept survey is newer (${String(kept.surveyed_at).slice(0, 10)}). Publish again to bring it up to date.`)}</span>`;
}

/* ── the report row: words from the server's row, never from the click ─ */

/** `row` is `publish_state().row` from the server. Returns `{ state, html }`. */
export function reportRowHtml(row, keptSurvey = null) {
  switch (row?.word) {
    case 'published':
      return cue('measured', `published · ${row.surveyed_at ? `from the survey of ${row.surveyed_at.slice(0, 10)} · ` : ''}read back ${ago(row.read_at)}`)
        + behindCue(row, keptSurvey)
        + (row.reused ? ' <span class="text-provenance text-ink-muted">· reused the report already in Egeria</span>' : '')
        + (row.annotation_count != null ? ` <span class="text-provenance text-ink-muted">· ${num(row.annotation_count)} annotations</span>` : '');
    case 'sent':
      return cue('running', 'sent · waiting for Egeria')
        + (row.sentence && row.sentence !== 'sent · waiting for Egeria' ? ` <span class="text-provenance text-ink-muted">${esc(row.sentence)}</span>` : '');
    case 'not published':
      return cue('error', `not published · ${row.first || 'Egeria gave no sentence'}`)
        + (row.sentence && row.sentence !== row.first ? ` ${detailsHtml(row.sentence)}` : '');
    case 'forgotten':
      return cue('unrun', 'links forgotten · re-read on the next publish');
    default:
      return cue('unrun', 'no publish recorded here');
  }
}

function projectHtml(project) {
  if (!project || project.status === 'unset') {
    return `<span class="text-ink-muted">no project</span> <span class="text-provenance text-ink-muted">· the first press asks</span>`;
  }
  const name = project.name ? ` <span class="font-mono">${esc(project.name)}</span>` : '';
  const detail = project.detail ? ` <span class="text-provenance text-ink-muted">· ${esc(project.detail)}</span>` : '';
  return `<span class="text-ink">${esc(project.word)}</span>${name}${detail}`;
}

/* ── the one "Egeria reports" component (PI-003) ───────────────────────── */

const annotationRowHtml = (a) => `<li class="py-[2px] text-caveat" data-annotation="${esc(a.guid)}">
  <span class="text-ink">${esc(a.annotation_type)}</span>
  <span class="text-ink-muted">· ${esc(a.summary || '')}</span>
  ${a.confidence != null ? `<span class="tnum text-provenance text-ink-muted">· confidence ${esc(a.confidence)}</span>` : ''}
  ${a.analysis_step ? `<span class="text-provenance text-ink-muted">· ${esc(a.analysis_step)}</span>` : ''}
  ${a.content_status === 'DRAFT' ? '<span class="text-provenance text-ink-muted">· draft proposal</span>' : ''}
  ${a.explanation ? `<details class="inline"><summary class="inline cursor-pointer text-ink-muted underline">why</summary>
    <span class="block max-w-[70ch] whitespace-pre-wrap text-provenance text-ink-muted">${esc(a.explanation)}</span></details>` : ''}
</li>`;

export function reportRowListHtml(r) {
  const counts = r.schema_count || r.table_count || r.column_count
    ? ` · ${num(r.schema_count)} schemas · ${num(r.table_count)} tables · ${num(r.column_count)} columns` : '';
  return `<div class="border-t border-rule py-s1" data-egeria-report="${esc(r.guid)}">
    <div class="flex flex-wrap items-baseline gap-x-s2 text-caveat">
      <span class="text-ink">${esc(r.display_name || r.qualified_name)}</span>
      <span class="text-provenance text-ink-muted">surveyed ${r.surveyed_at ? esc(ago(r.surveyed_at)) : 'at an unknown time'}
        · ${num(r.annotation_count)} annotations${counts}</span>
      ${guidHtml(r.guid)}
      ${linkButton(`data-report-toggle="${esc(r.guid)}" aria-expanded="false"`, 'Show annotations')}
      ${linkButton(`data-report-ask="${esc(r.guid)}"`, 'Ask about this →')}
    </div>
    <div data-report-annotations="${esc(r.guid)}" class="ml-s3"></div>
  </div>`;
}

/** Chat-rail prefill (PI-008): the report's identity, nothing sent until the person presses Ask. */
export function askAboutText(entityType, slug, r) {
  return `Summarize the survey report "${r.display_name || r.qualified_name}" (Egeria GUID ${r.guid}`
    + `${r.surveyed_at ? `, surveyed ${r.surveyed_at}` : ''}) for the ${entityType} ${slug}.`;
}

function defaultAsk(text) {
  ensureRailShowing();
  const input = $('ask-input');
  if (!input) return false;
  input.value = text;
  input.focus();
  return true;
}

/** Mount the Egeria reports component into `el`.
 *  `entityType`  'repo' | 'database' | 'filesystem' (the API kind, never defaulted)
 *  `inEgeria`    whether the resource has an Egeria asset: with no asset the reader answers [] and that
 *                must read "not in Egeria", not "no reports"
 *  `onAsk`       optional (text) => boolean, replaces the chat-rail prefill
 *  Returns `{ refresh, ready, expand(guid) }` so another stage can open one report. `refresh()` and `ready`
 *  resolve true when Egeria was read, false when the read failed, null when superseded. */
export function mountEgeriaReports(el, { entityType, slug, inEgeria, onAsk = defaultAsk, heading = true }) {
  if (!entityType) throw new Error('mountEgeriaReports: entityType is required');
  let reports = [];
  const body = () => el.querySelector('[data-egeria-reports-body]');

  const frame = () => {
    el.innerHTML = `<div data-egeria-reports class="min-w-0">
      <div class="flex flex-wrap items-baseline gap-x-s2">
        ${heading ? '<span class="text-answer text-ink">Egeria reports</span>' : ''}
        ${button('data-reports-refresh', 'Refresh')}
        <span data-reports-status class="text-provenance text-ink-muted"></span>
      </div>
      <div data-egeria-reports-body class="mt-s1"></div></div>`;
    el.querySelector('[data-reports-refresh]').addEventListener('click', () => refresh());
  };

  const draw = () => {
    const b = body();
    if (!b) return;
    b.innerHTML = reports.map(reportRowListHtml).join('');
    bindCopy(b);
    b.querySelectorAll('[data-report-toggle]').forEach((t) =>
      t.addEventListener('click', () => toggle(t.dataset.reportToggle)));
    b.querySelectorAll('[data-report-ask]').forEach((t) => t.addEventListener('click', () => {
      const r = reports.find((x) => x.guid === t.dataset.reportAsk);
      if (r && !onAsk(askAboutText(entityType, slug, r))) {
        el.querySelector('[data-reports-status]').textContent = 'the chat rail could not be opened';
      }
    }));
  };

  async function toggle(guid) {
    const slot = byAttr(el, 'data-report-annotations', guid);
    const btn = byAttr(el, 'data-report-toggle', guid);
    if (!slot || !btn || btn.dataset.pending) return;
    if (btn.getAttribute('aria-expanded') === 'true') {
      slot.innerHTML = '';
      btn.setAttribute('aria-expanded', 'false');
      btn.textContent = 'Show annotations';
      return;
    }
    btn.dataset.pending = '1';
    btn.disabled = true;
    btn.textContent = 'Reading…';
    try {
      const rows = await getEgeriaReportAnnotations(entityType, slug, guid);
      slot.innerHTML = rows.length
        ? `<ul class="m-0 list-none p-0">${rows.map(annotationRowHtml).join('')}</ul>`
        : `<div class="text-caveat text-ink-muted">Egeria holds no annotations under this report.</div>`;
      btn.setAttribute('aria-expanded', 'true');
      btn.textContent = 'Hide annotations';
    } catch (err) {
      slot.innerHTML = `<div class="text-caveat text-state-warn">The annotations could not be read: ${esc(err.message)}</div>`;
      btn.textContent = 'Show annotations';
    } finally {
      btn.disabled = false;
      delete btn.dataset.pending;
    }
  }

  async function refresh() {
    if (!el.querySelector('[data-egeria-reports]')) frame();
    const status = el.querySelector('[data-reports-status]');
    const rbtn = el.querySelector('[data-reports-refresh]');
    if (rbtn.dataset.pending) return null;
    rbtn.dataset.pending = '1';
    rbtn.disabled = true;
    status.textContent = 'reading Egeria…';
    try {
      if (!inEgeria) {
        reports = [];
        draw();
        body().innerHTML = `<div class="text-caveat text-ink-muted">Not in Egeria: there is no asset to list reports from.</div>`;
        status.textContent = '';
        return true;
      }
      reports = await getEgeriaReports(entityType, slug);
      if (stale(el, slug)) return null;
      draw();
      if (!reports.length) {
        body().innerHTML = `<div class="text-caveat text-ink-muted">Egeria holds no survey reports on this asset.</div>`;
      }
      status.textContent = `read ${new Date().toTimeString().slice(0, 5)} · ${reports.length} report${reports.length === 1 ? '' : 's'}`;
      return true;
    } catch (err) {
      if (stale(el, slug)) return null;
      reports = [];
      draw();
      body().innerHTML = `<div class="text-caveat text-state-warn" data-reports-error>Egeria could not be read: ${esc(err.message)}</div>`;
      status.textContent = '';
      return false;   // the read failed: callers must not read an empty list as "not in Egeria"
    } finally {
      rbtn.disabled = false;
      delete rbtn.dataset.pending;
    }
  }

  frame();
  const first = refresh();
  return {
    refresh,
    ready: first,
    async expand(guid) {
      await first;
      const btn = byAttr(el, 'data-report-toggle', guid);
      if (btn && btn.getAttribute('aria-expanded') !== 'true') await toggle(guid);
    },
  };
}

/* ── the Publish section ───────────────────────────────────────────────── */

function resourceRow(slug) {
  const rows = state.resourceType === 'db' ? state.databases
    : state.resourceType === 'filesystem' ? state.filesystems : state.projects;
  return (rows || []).find((r) => r.slug === slug) || null;
}

const head = '<div class="mb-s1 text-caps uppercase tracking-caps text-ink">Publish to Egeria</div>';

function nonRepoStatusHtml(entityType, slug) {
  const row = resourceRow(slug) || {};
  const guid = row.egeria_asset_guid || '';
  const note = row.egeria_publish_note
    ? ` <span class="text-provenance text-state-warn">${esc(row.egeria_publish_note)}</span>` : '';
  const how = entityType === 'database'
    ? 'A database reaches Egeria through Publish → above (its survey report is published with it).'
    : 'A file system has no publish control here yet.';
  return `<div class="text-caveat" data-publish-status>${row.is_published
    ? cue('measured', 'in Egeria') + ` <span class="text-provenance text-ink-muted">· asset</span> ${guidHtml(guid)}`
    : cue('unrun', 'not in Egeria')}${note}</div>
    <div class="mt-s1 text-provenance text-ink-muted">${esc(how)}</div>`;
}

/** The survey a press would publish, before the press (brief section 1): its date, its age, its size,
 *  and how much of it is stale. Stale steps are NAMED, not auto-run: a ⚠ and the word "stale", the list
 *  one gesture away. With no survey: the sentence, and nothing else claims one exists. */
export function surveyLineHtml(survey) {
  if (!survey || !survey.exists) {
    return `<div class="mt-s1 text-caveat" data-publish-survey="none">${cue(survey?.surveyed_before_kept ? 'partial' : 'unrun', noKeptSentence(survey))}</div>`;
  }
  const date = String(survey.surveyed_at || '').slice(0, 10);
  const stale = survey.stale_steps
    ? `<div class="mt-[2px] text-caveat" data-publish-stale>${cue('partial', 'stale')}
        <span class="text-ink-muted">${num(survey.stale_steps)} of ${num(survey.steps)} steps are stale (older than their refresh rule) · re-survey to refresh</span>
        <details class="inline"><summary class="inline cursor-pointer text-ink-muted underline">which</summary>
          <span class="block font-mono text-provenance text-ink-muted">${(survey.stale || []).map(esc).join(', ')}</span></details></div>` : '';
  return `<div class="mt-s1 text-caveat" data-publish-survey="kept">kept survey · <span class="tnum">${esc(date)}</span>
      <span class="text-ink-muted">· ${esc(ago(survey.surveyed_at))} · ${num(survey.annotations)} annotations · ${num(survey.steps)} steps ran</span></div>${stale}`;
}

/* ── a repository's publish state, drawn on two tabs from ONE read ─────────────────────────────────
 *
 * Describe and publish holds what a person does to get the REPORT to Egeria (the press, Re-survey now, file
 * types, the Egeria reports list); Contents holds the plumbing around it (the asset GUID, Forget links, the
 * ONE Project row); Structure holds Architecture Publish. All three share the single `getRepoPublishState`
 * read, `reread` and `draw` in `renderPublishRepo`. The words are the server's, as before. */

/** The project as a cue and a short word (the sentence is the tooltip). The controls live on Contents. */
function projectCueHtml(project) {
  const status = project && project.status;
  const unanswered = !project || status === 'unset' || status === 'unbound';
  const name = project && project.name ? ` ${project.name}` : '';
  const word = project ? `${project.word}${name}` : 'no project';
  return cue(unanswered ? 'unrun' : 'measured', word, project && project.detail ? project.detail : '');
}

/* ── Tab 1's Publish band: three lines (Brief F addendum A, owner 2026-10-11) ───────────────────────────────
 *
 *   1. ONE state: "✓ in Egeria · published 10-11 · confirmed" (the read-back matched: `repo_publish.confirmation`,
 *      the one "same result" function), "in Egeria · published 10-11 · not yet confirmed", or
 *      "◐ in Egeria · published 10-11 · Egeria shows N"; the project as a cue; [Show in Egeria ▸] holds the
 *      Egeria report rows (GUID, copy, Show annotations, Ask about this, Refresh) as detail on demand.
 *   2. next publish: the kept survey, its staleness, whether it is newer than what is published, and what the
 *      ONE press sends (the report, tags and group, documentation sources, YOUR rating and notes), each a cue and
 *      a short word, from `goes_out` (the server's `single_publish.goes_out`, the functions the press acts on).
 *   3. the presses.
 * A press is acknowledged at once ("◔ publishing…"); success is validated by line 1 turning "confirmed"; a
 * failure is a pop-up with the reason and the items, AND a persistent cue from the rows. The steps the press
 * took are in the data, behind "details ▸" only. */

const CONFIRM_CUE = { confirmed: 'measured', unconfirmed: 'queued', differs: 'partial' };
const hhmm = (iso) => { const t = String(iso || ''); return t.length >= 16 ? t.slice(11, 16) : ''; };

/** Line 1's state, from the server's row and its `confirm` (never from the click). */
export function inEgeriaHtml(s) {
  const row = (s && s.row) || {};
  const c = row.confirm || {};
  if ((row.word === 'published' || row.word === 'sent') && c.state) {
    const title = c.state === 'differs'
      ? `Published ${c.published_count ?? '?'} annotations; Egeria's read-back shows ${c.egeria_count ?? '?'}.`
      : c.state === 'confirmed' ? `read back ${hhmm(c.read_at)} · Egeria shows the same report and count`
        : 'published; the read-back has not shown the same result yet';
    return `<span data-publish-confirm="${esc(c.state)}">${cue(CONFIRM_CUE[c.state] || 'queued', `in Egeria · ${c.word}`, title)}</span>`
      + behindCue(row, s.survey)
      + (row.annotation_count != null ? ` <span class="text-provenance text-ink-muted">· ${num(row.annotation_count)} annotations</span>` : '')
      + (c.state === 'confirmed' && hhmm(c.read_at) ? ` <span class="text-provenance text-ink-muted" data-publish-read-back>· read back ${esc(hhmm(c.read_at))}</span>` : '')
      + (row.reused ? ' <span class="text-provenance text-ink-muted">· reused the report already in Egeria</span>' : '');
  }
  const lead = s && s.in_egeria ? cue('measured', 'in Egeria') : cue('unrun', 'not in Egeria');
  return row.word && row.word !== 'none' ? `${lead} · ${reportRowHtml(row, s.survey)}` : lead;
}

/** One item the next press sends: a cue and a short word. */
const goesItem = (attr, what, state, word) => {
  // From the server's word: a tag's `not_sent` is "saved · will publish", a rating's is a failed send.
  const w = String(word || '');
  const key = /^not permitted/.test(w) ? 'no_access' : /^not sent/.test(w) ? 'partial' : 'stored';
  return `<span class="mr-s2 inline-block" ${attr}><span class="text-ink">${esc(what)}</span> ${cue(key, word)}</span>`;
};

/** Line 2: the kept survey is newer than what is published (the same dates `behindCue` compares). */
function newerHtml(s) {
  if (!behindCue((s && s.row) || {}, s && s.survey)) return '';
  return `<div class="mt-[2px] text-provenance" data-publish-newer>${cue('partial', `newer than published · ${s.survey.annotations ?? '?'} annotations`)}</div>`;
}

/** Line 2's list: what the ONE press sends (the server's `goes_out`). */
export function goesOutHtml(s) {
  const g = (s && s.goes_out) || null;
  const parts = [];
  if (s && s.survey && s.survey.exists) parts.push(goesItem('data-goes-out="report"', 'report', 'saved', 'will publish'));
  if (!g) {
    return `<div class="mt-[2px] text-provenance" data-publish-goes-out>${parts.join('')}<span class="text-ink-muted">tags, group and feedback: sign in to see what your press sends</span></div>`;
  }
  (g.findable || []).forEach((i) => parts.push(goesItem(`data-goes-out="${esc(i.kind)}:${esc(i.name)}"`,
    `${i.kind === 'group' ? 'group' : 'tag'} ${i.label || i.name}`, i.state, i.word)));
  (g.doc_sources || []).forEach((d) => parts.push(goesItem(`data-goes-out="doc:${esc(d.id ?? d.url)}"`, `doc ${d.label}`, 'saved', d.word)));
  (g.feedback || []).forEach((i) => parts.push(goesItem(`data-goes-out="${esc(i.kind)}:${esc(i.id)}"`,
    i.kind === 'rating' ? 'your rating' : 'your note', i.state, i.word)));
  const others = g.waiting_for_others
    ? `<span class="text-ink-muted" data-goes-out-others>· ${num(g.waiting_for_others)} from others · waiting for them</span>` : '';
  return `<div class="mt-[2px] text-provenance" data-publish-goes-out>${parts.join('') || '<span class="text-ink-muted">nothing else to send</span>'}${others}</div>`;
}

/** Tab 1, whole: the three lines, then (after a press) the steps behind "details ▸". */
export function renderPublishDescribe(s, signedIn) {
  return `${head}
    <div class="text-caveat" data-publish-line1><span data-publish-status><span data-publish-row>${inEgeriaHtml(s)}</span></span>
      <span class="text-provenance text-ink-muted" data-publish-project>· project ${projectCueHtml(s.project)}</span>
      <details class="inline" data-publish-show-egeria><summary class="inline cursor-pointer text-accent-ink underline">Show in Egeria ▸</summary>
        <div class="mt-s1" data-egeria-reports-host></div></details></div>
    <div class="mt-s1 text-caveat" data-publish-next><span class="text-provenance text-ink-muted">next publish</span>
      ${surveyLineHtml(s.survey)}${newerHtml(s)}
      ${goesOutHtml(s)}
      <details class="mt-[2px]" data-file-measurements-section><summary class="cursor-pointer text-provenance text-ink-muted">file types ▸
        <span class="text-ink-muted">· in the survey report</span></summary>
        <div class="mt-s1" data-file-measurements-host></div></details></div>
    ${describeControlsHtml(s, signedIn)}
    <div data-publish-details-host></div>`;
}

/** Line 3: the one press, Re-survey now, and the words beside them. A 428 draws a cue and a short
 *  word under them (the controls are on Contents). */
function describeControlsHtml(s, signedIn) {
  const label = s.can_publish_again ? 'Publish again' : 'Publish to Egeria →';
  const hasSurvey = !!(s.survey && s.survey.exists);
  // Round 2: with no kept survey the press still sends the rest (tags, group, your feedback) of a resource already
  // in Egeria; the report line says why there is no report.
  const restToSend = !!(s.in_egeria && s.goes_out && Number(s.goes_out.count || 0) > 0);
  const why = signedIn ? '' : 'sign in to publish — it needs an author';
  return `<div class="mt-s2 flex flex-wrap items-baseline gap-s3">
    ${button(`data-publish-go title="${esc(PUBLISH_SENTENCE)}"`, esc(label), { disabled: !(signedIn && (hasSurvey || restToSend)) })}
    ${button(`data-resurvey-go title="${esc(RESURVEY_SENTENCE)}"`, hasSurvey || s.survey?.surveyed_before_kept ? 'Re-survey now →' : 'Run the first survey →', { disabled: !signedIn })}
    <span data-publish-feedback class="text-provenance text-ink-muted" title="${esc(PUBLISH_SENTENCE)}">${esc(why || PUBLISH_IDLE_WORDS)}</span></div>
    ${hasSurvey ? '' : `<div class="mt-[2px] text-provenance text-ink-muted" data-publish-blocker>${esc(noKeptSentence(s.survey))}</div>`}
    <div data-publish-gate></div>`;
}

/** What went wrong in a press, from the re-read rows (and the press's own answer for parts with no row). */
export function pressFailures(out, st) {
  const f = [];
  const row = (st && st.row) || {};
  if (row.word === 'not published') f.push({ what: 'report', word: `not published · ${row.first || 'Egeria gave no sentence'}` });
  ((st && st.goes_out && st.goes_out.feedback) || []).forEach((i) => {
    if (i.state === 'not_sent' || i.state === 'not_permitted') f.push({ what: i.kind === 'rating' ? 'your rating' : 'your note', word: i.word });
  });
  const sent = (out && out.sent) || {};
  if (sent.findable && sent.findable.error) f.push({ what: 'tags and group', word: `not sent · ${sent.findable.error}` });
  ((sent.findable && sent.findable.failed) || []).forEach((i) => f.push({ what: `${i.kind} ${i.name}`, word: i.word }));
  if (sent.doc_sources && sent.doc_sources.error) f.push({ what: 'documentation sources', word: `not sent · ${sent.doc_sources.error}` });
  ((sent.doc_sources && sent.doc_sources.failed) || []).forEach((d) => f.push({ what: 'documentation source', word: `not sent · ${d.error || 'failed'}` }));
  if (sent.feedback && sent.feedback.error) f.push({ what: 'your feedback', word: `not sent · ${sent.feedback.error}` });
  return f;
}

/** The failure pop-up (owner: "if there is a failure that might be a pop-up"). Retry only when something other
 *  than a refusal failed (a refusal needs a permission change, not a retry); destructive writes are never in it. */
export function showPublishFailure(doc, failures, onRetry) {
  doc.querySelector('[data-publish-failure]')?.remove();
  const canRetry = failures.some((x) => !/^not permitted/.test(x.word));
  const box = doc.createElement('div');
  box.setAttribute('data-publish-failure', '1');
  box.setAttribute('role', 'alertdialog');
  box.setAttribute('aria-modal', 'true');
  box.setAttribute('aria-label', 'Publish did not finish');
  box.className = 'fixed inset-0 z-50 flex items-start justify-center bg-black/40 p-s4 overflow-auto';
  box.innerHTML = `<div class="mt-[6vh] w-full max-w-[640px] rounded bg-paper p-s4 text-caveat text-ink shadow-lg">
    <div class="text-answer text-ink">The publish did not finish</div>
    <ul class="m-0 mt-s1 list-none p-0">${failures.map((x) => `<li data-publish-failed-item class="py-[2px]"><span class="text-ink">${esc(x.what)}</span> · ${cue(/^not permitted/.test(x.word) ? 'no_access' : 'partial', x.word)}</li>`).join('')}</ul>
    <div class="mt-s2 flex gap-s3">${canRetry ? button('data-publish-failure-retry', 'Retry') : ''}${button('data-publish-failure-close', 'Close')}</div></div>`;
  doc.body.appendChild(box);
  box.querySelector('[data-publish-failure-close]').addEventListener('click', () => box.remove());
  box.querySelector('[data-publish-failure-retry]')?.addEventListener('click', () => { box.remove(); onRetry(); });
  return box;
}

/** The steps a press took (the press's own answer), for diagnosis only: never shown by default. */
export function pressDetailsHtml(out) {
  const sent = (out && out.sent) || {};
  const lines = [`report · ${out && out.ok === false ? `failed at ${out.stage || 'publish'}` : 'published'}${out && out.report_guid ? ` · ${out.report_guid}` : ''}`];
  if (sent.skipped) lines.push(`rest · ${sent.skipped}`);
  if (sent.findable) lines.push(`tags and group · ${sent.findable.error || `${sent.findable.queued} queued`}`);
  if (sent.doc_sources) lines.push(`documentation sources · ${sent.doc_sources.error || `${sent.doc_sources.published} published`}`);
  if (sent.feedback) lines.push(`your feedback · ${sent.feedback.error || sent.feedback.refused || `${sent.feedback.queued} sent`}${(sent.feedback.not_permitted || []).length ? ` · ${sent.feedback.not_permitted.length} not permitted` : ''}${(sent.feedback.unread || []).length ? ` · ${sent.feedback.unread.length} not sent (couldn't check access)` : ''}`);
  return `<details class="mt-s1" data-publish-details><summary class="cursor-pointer text-provenance text-ink-muted">details ▸</summary>
    <ul class="m-0 list-none p-0 font-mono text-provenance text-ink-muted">${lines.map((l) => `<li>${esc(l)}</li>`).join('')}</ul></details>`;
}

/** The ONE Project row (Contents). It always names the project; when nothing answers (or an Egeria reset left it
 *  unbound) it carries the three choices, and nowhere else on the screen does. */
function projectRowHtml(project, signedIn) {
  const unanswered = !project || project.status === 'unset' || project.status === 'unbound';
  return `<div class="mt-s2 text-caveat" data-curate-project-row>
    <span class="text-provenance text-ink-muted">project</span> ${projectHtml(project)}
    ${unanswered ? `<span class="ml-s2 inline-flex flex-wrap items-baseline gap-s3" title="${esc(NO_PROJECT_SENTENCE)}">
      ${button('data-gate-bind', 'Bind this investigation to a project')}
      ${button('data-commit-decline', 'Decline a project')}
      ${button('data-gate-without', 'Publish this report without one', { disabled: !signedIn })}</span>` : ''}
    <span data-curate-project-feedback class="ml-s2 text-provenance text-ink-muted"></span></div>`;
}

/** Contents: the asset GUID and its copy, the Project row, Forget Egeria links. */
export function renderPublishContents(s, signedIn) {
  return `<div class="mb-s1 text-caps uppercase tracking-caps text-ink">Publish · links and project</div>
    <div class="text-caveat" data-publish-asset>${s.in_egeria
    ? `<span class="text-provenance text-ink-muted">asset</span> ${guidHtml(s.asset_guid)}`
    : cue('unrun', 'no asset yet')}</div>
    ${projectRowHtml(s.project, signedIn)}
    <div class="mt-s2 flex flex-wrap items-baseline gap-s3">
      ${button('data-forget-open', 'Forget Egeria links…', { disabled: !(signedIn && s.in_egeria) })}</div>
    <div data-forget-confirm></div>`;
}

function forgetHtml() {
  return `<div class="mt-s2 max-w-[70ch] border-l border-rule-strong pl-s2" data-forget-panel role="alertdialog" aria-label="Forget Egeria links">
    <div class="text-caveat text-ink" data-forget-sentence>${esc(FORGET_SENTENCE)}</div>
    <div class="mt-s1 flex flex-wrap gap-s3">
      ${button('data-forget-go', 'Forget links')}
      ${button('data-forget-cancel', 'Cancel')}
    </div></div>`;
}

/** File types are a MEASUREMENT (ruling 2026-10-07): the survey report carries them as Egeria's own
 *  profile annotations, so this section only shows them. It sends nothing and has no button. DataSets
 *  an earlier press made stay in Egeria and read as history. */
async function renderFileTypeMeasurements(host, slug) {
  host.innerHTML = '<div class="text-caveat text-ink-muted">Reading the file types from RE\'s survey…</div>';
  let m;
  try { m = await getFileTypeMeasurements(slug); } catch (err) {
    if (host.isConnected) host.innerHTML = `<div class="text-caveat text-state-warn">The file types could not be read: ${esc(err.message)}</div>`;
    return;
  }
  if (!host.isConnected) return;
  if (!m.inventoried) {
    host.innerHTML = '<div class="text-caveat text-ink-muted" data-file-measurements-empty>No file inventory yet: survey the repository first.</div>';
    return;
  }
  const top = (counts) => Object.entries(counts).sort((x, y) => y[1] - x[1]).slice(0, 6)
    .map(([k, n]) => `${esc(k)} ${num(n)}`).join(' · ');
  const profiles = m.profiles.map((p) => `<div class="py-[1px] text-caveat" data-file-measurement="${esc(p.name)}">
      ${cue('measured', 'in the report')} <span class="text-ink">${esc(p.name)}</span>
      <span class="text-provenance text-ink-muted">· ${top(p.counts)}</span></div>`).join('');
  const retired = m.retired.length ? `<div class="mt-s2 text-provenance text-ink-muted" data-file-types-retired>earlier publishes</div>
    ${m.retired.map((r) => `<div class="py-[1px] text-caveat" data-retired-dataset="${esc(r.label)}">
      <span class="text-ink">${esc(r.label)}</span>
      <span class="text-provenance text-ink-muted">· ${esc(r.word)}</span> ${guidHtml(r.dataset_guid)}</div>`).join('')}` : '';
  host.innerHTML = `
    <div class="text-provenance text-ink-muted">Sent with the survey report, as profile annotations. Files are published by selection on Curate.</div>
    <div class="mt-s1">${profiles}</div>${retired}`;
  bindCopy(host);
}

/** Draw the Publish band for a database or a file system. A repository draws through `renderPublishRepo`
 *  (its two halves live on the Describe and Contents tabs). Never throws into the pane. */
export async function renderPublishBand(el, slug, entityType) {
  if (!el) throw new Error('Publish band host missing');
  if (entityType === 'repo') throw new Error('renderPublishBand: a repository draws through renderPublishRepo');
  el.innerHTML = `${head}${nonRepoStatusHtml(entityType, slug)}<div class="mt-s2" data-egeria-reports-host></div>`;
  bindCopy(el);
  const row = resourceRow(slug) || {};
  mountEgeriaReports(el.querySelector('[data-egeria-reports-host]'), { entityType, slug, inEgeria: !!row.is_published });
}

/** A database's publish band on the Curate tabs (Brief S2). `hosts` = { describe, contents }.
 *  Describe: the ONE state line (from the scope view's commit header, painted by `paintDbPublishState` as each view
 *  arrives: never from the registry's `is_published` flag), then the Egeria reports list. There is no press here: a
 *  database's one press is the commit on Contents. Contents: the asset GUID and its copy control. Never throws. */
export async function renderPublishDb(hosts, slug) {
  const { describe: dEl, contents: cEl } = hosts;
  if (!dEl) throw new Error('Publish band host missing');
  const row = resourceRow(slug) || {};
  const note = row.egeria_publish_note
    ? `<div class="mt-s1 text-provenance text-state-warn">${esc(row.egeria_publish_note)}</div>` : '';
  dEl.innerHTML = `${head}<div data-publish-status data-db-publish-state-host>${commitStateLineHtml(null)}</div>${note}
    <div class="mt-s2" data-egeria-reports-host></div>`;
  paintDbPublishState(dEl, null);
  if (cEl) {
    cEl.innerHTML = row.egeria_asset_guid
      ? `<div data-publish-asset class="text-caveat"><span class="text-provenance text-ink-muted">asset</span> ${guidHtml(row.egeria_asset_guid)}</div>` : '';
    bindCopy(cEl);
  }
  mountEgeriaReports(dEl.querySelector('[data-egeria-reports-host]'), { entityType: 'database', slug, inEgeria: !!row.is_published });
}

/** Draw (or redraw) the database's one-line state into `root`'s state-line host from a scope view. `view` null =
 *  still reading; `failed` = the read did not arrive. The "Contents ›" link switches tab in place. */
export function paintDbPublishState(root, view, failed = false) {
  const host = root && root.querySelector && root.querySelector('[data-db-publish-state-host]');
  if (!host) return;
  host.innerHTML = commitStateLineHtml(view, failed);
  const b = host.querySelector('[data-db-goto-contents]');
  if (b) b.onclick = () => openCurate({ tab: 'curate_contents' });
}

/** A repository's publish state on the Curate tabs. `hosts` = { describe, contents, structure } (elements).
 *  ONE state read serves Describe and Contents; `reread` and `draw` are shared, so a press on either tab
 *  redraws both from the re-read. Architecture Publish mounts on Structure the first time that tab opens
 *  (`mountStructure()`), not at page load. Returns { ready, mountStructure } at once; `ready` resolves when the
 *  first read has been drawn (it never rejects: a failed read says so in the slots). */
export function renderPublishRepo(hosts, slug) {
  const { describe: dEl, contents: cEl, structure: sEl } = hosts;
  if (!dEl || !cEl) throw new Error('Publish band host missing');
  const signedIn = !!whoAmI();
  let archMounted = false;
  const mountStructure = () => {
    if (archMounted || !sEl || !sEl.isConnected) return;
    archMounted = true;
    // The architecture (accepted components and blueprints) is published by its own control.
    mountArchitecturePublish(sEl, slug).catch(() => {});
  };
  const ready = (async () => {
    dEl.innerHTML = `${head}<div class="text-caveat text-ink-muted">Reading what Egeria holds…</div>`;
    cEl.innerHTML = `<div class="mb-s1 text-caps uppercase tracking-caps text-ink">Publish · links and project</div>
      <div class="text-caveat text-ink-muted">Reading what Egeria holds…</div>`;
    let s;
    try { s = await getRepoPublishState(slug); } catch (err) {
      if (!stale(dEl, slug)) {
        dEl.innerHTML = `${head}<div class="text-caveat text-state-warn">The publish state could not be read: ${esc(err.message)}</div>`;
        cEl.innerHTML = '';
      }
      return;
    }
    if (stale(dEl, slug)) return;
    draw(s);
  })();

  /** The project answer changed or was re-read: Curate's own plan (the commit blockers) follows it. */
  const announceProject = (project) =>
    dEl.ownerDocument.dispatchEvent(new CustomEvent('re:project-changed', { detail: { slug, project } }));

  let lastPress = null;          // the last press's own answer: its steps, behind "details ▸" only
  function draw(st, feedback = '') {
    dEl.innerHTML = renderPublishDescribe(st, signedIn);
    cEl.innerHTML = renderPublishContents(st, signedIn);
    bindCopy(dEl); bindCopy(cEl);
    if (feedback) {
      dEl.querySelector('[data-publish-feedback]').textContent = feedback;
      const pf = cEl.querySelector('[data-curate-project-feedback]');
      if (pf) pf.textContent = feedback;
    }
    if (lastPress) dEl.querySelector('[data-publish-details-host]').innerHTML = pressDetailsHtml(lastPress);
    wire(st);
    // The Egeria report rows are detail on demand: they sit inside the closed "Show in Egeria ▸" disclosure
    // (read as before, so another stage can still open one report in it).
    mountEgeriaReports(dEl.querySelector('[data-egeria-reports-host]'), { entityType: 'repo', slug, inEgeria: st.in_egeria, heading: false });
  }

  // After an ACT (a press, a decline, a forget) the state is re-read and both halves redraw from the re-read;
  // the project answer it carries is told to Curate's own plan, so the commit's blockers follow it.
  const reread = async (feedback = '') => {
    const st = await getRepoPublishState(slug);
    if (stale(dEl, slug)) return null;
    draw(st, feedback);
    announceProject(st.project);
    return st;
  };
  /** Where the Project row's press shows its result: the row's own span, a cue and a short word. */
  const tellRow = (html) => { const pf = cEl.querySelector('[data-curate-project-feedback]'); if (pf) pf.innerHTML = html; };

  function wire(st) {
    const go = dEl.querySelector('[data-publish-go]');
    const feedback = dEl.querySelector('[data-publish-feedback]');
    const gate = dEl.querySelector('[data-publish-gate]');
    const press = async (withoutProject, fromRow = false) => {
      if (go.dataset.pending) return;                       // a second press is ignored
      go.dataset.pending = '1';
      go.disabled = true;
      const idle = go.textContent;
      go.textContent = `${idle} …`;
      feedback.innerHTML = cue('running', PUBLISH_RUNNING_WORDS);
      feedback.title = PUBLISH_SENTENCE;
      gate.innerHTML = '';
      if (fromRow) tellRow(cue('running', 'publishing'));
      try {
        const out = await publishRepoReport(slug, { withoutProject });
        lastPress = out || null;
        const st = await reread();
        // The validation is line 1 (the re-read row's `confirm`); the sections it sent re-read their own rows.
        dEl.ownerDocument.dispatchEvent(new CustomEvent('re:published', { detail: { slug } }));
        if (st) {
          const c = (st.row && st.row.confirm) || {};
          const fb = dEl.querySelector('[data-publish-feedback]');
          if (fb && c.word) fb.innerHTML = cue(CONFIRM_CUE[c.state] || 'queued', c.word);
          const failures = pressFailures(out, st);
          if (failures.length) showPublishFailure(dEl.ownerDocument, failures, () => dEl.querySelector('[data-publish-go]')?.click());
        }
        // The word follows the re-read row, never the click.
        if (fromRow && st) tellRow(st.row && st.row.word === 'published' ? cue('measured', 'published') : cue('error', 'not published'));
      } catch (err) {
        if (err.status === 428) {
          go.disabled = false; go.textContent = idle; delete go.dataset.pending;
          feedback.textContent = '';
          // Round 2: the gate stops the report only; the rest of the press went when the resource is in Egeria,
          // so the band and the sections re-read their rows first, then the gate is drawn on the new band.
          let g2 = gate;
          try {
            if (await reread()) g2 = dEl.querySelector('[data-publish-gate]') || gate;
          } catch { /* the gate below is what matters */ }
          dEl.ownerDocument.dispatchEvent(new CustomEvent('re:published', { detail: { slug } }));
          // A cue and a short word; the three choices are the Project row on Contents.
          g2.innerHTML = `<div class="mt-s1 text-caveat" data-publish-gate-panel role="status">${
            cue('unrun', 'needs a project', NO_PROJECT_SENTENCE)} ${
            linkButton('data-publish-gate-goto="curate_contents"', 'Contents ›')}</div>`;
          g2.querySelector('[data-publish-gate-goto]').addEventListener('click', () => openCurate({ tab: 'curate_contents' }));
          return;
        }
        try { await reread(); } catch { /* the failure below is what matters */ }
        dEl.ownerDocument.dispatchEvent(new CustomEvent('re:published', { detail: { slug } }));
        if (err.status !== 409) {
          // A failure is a pop-up (owner, 2026-10-11) AND a persistent cue below (a pop-up disappears).
          showPublishFailure(dEl.ownerDocument, [{ what: 'publish', word: err.status === 403 ? `not permitted · ${err.message}` : `not published · ${err.message}` }],
            () => dEl.querySelector('[data-publish-go]')?.click());
        }
        const fb = dEl.querySelector('[data-publish-feedback]');
        if (fb) fb.innerHTML = err.status === 403
          ? cue('no_access', 'not permitted', err.message)   // Brief Z2: the reason on demand, not a sentence
          : err.status === 409 && err.message.startsWith('no survey')
          ? cue('unrun', err.message)                    // nothing surveyed: no report (the rest re-reads above)
          : err.status === 409
            ? cue('running', 'a publish is already running for this resource')
            : cue('error', `not published · ${err.message}`);
        if (fromRow) tellRow(err.status === 403 ? cue('no_access', 'not permitted', err.message)
          : err.status === 409 ? cue('running', 'already running') : cue('error', 'not published', err.message));
      }
    };
    go?.addEventListener('click', () => press(false));

    // The Project row (Contents): the three choices, each once.
    cEl.querySelector('[data-gate-bind]')?.addEventListener('click', () => openCurrentInvestigationStage());
    cEl.querySelector('[data-gate-without]')?.addEventListener('click', () => press(true, true));
    const decline = cEl.querySelector('[data-commit-decline]');
    decline?.addEventListener('click', async () => {
      if (decline.disabled) return;
      decline.disabled = true; decline.textContent = 'declining …';
      try {
        await setRepoProjectContext(slug, 'declined');
        await reread();
      } catch (err) {
        decline.disabled = false; decline.textContent = 'Decline a project';
        const pf = cEl.querySelector('[data-curate-project-feedback]');
        if (pf) pf.textContent = `not declined · ${err.message}`;
      }
    });

    // Re-survey now: runs the survey and NOTHING else. An immediate visible change on the press; the
    // words after it come from the re-read state (the new age), never from the click.
    const rs = dEl.querySelector('[data-resurvey-go]');
    rs?.addEventListener('click', async () => {
      if (rs.dataset.pending) return;
      rs.dataset.pending = '1';
      rs.disabled = true;
      const idle = rs.textContent;
      rs.textContent = 'Re-surveying …';
      if (go) go.disabled = true;                          // a publish mid-survey would send a half-kept one
      feedback.innerHTML = cue('running', 'surveying · nothing is sent to Egeria');
      try {
        const out = await resurveyRepo(slug);
        await reread(out && out.ok === false
          ? `survey finished with problems · ${(out.errors || []).join('; ').slice(0, 200)}` : 'survey kept · see its age above');
      } catch (err) {
        rs.disabled = false; rs.textContent = idle; delete rs.dataset.pending;
        if (go) go.disabled = !(st.survey && st.survey.exists);
        feedback.innerHTML = err.status === 409
          ? cue('running', 'a survey is already running for this resource')
          : cue('error', `not surveyed · ${err.message}`);
      }
    });

    cEl.querySelector('[data-forget-open]')?.addEventListener('click', () => {
      const slot = cEl.querySelector('[data-forget-confirm]');
      slot.innerHTML = forgetHtml();
      slot.querySelector('[data-forget-cancel]').addEventListener('click', () => { slot.innerHTML = ''; });
      const fgo = slot.querySelector('[data-forget-go]');
      fgo.addEventListener('click', async () => {
        if (fgo.dataset.pending) return;
        fgo.dataset.pending = '1';
        fgo.disabled = true;
        fgo.textContent = 'Forgetting …';
        try {
          const out = await forgetEgeriaLinks(slug);
          await reread(`links forgotten · ${out.surveys_deleted} survey record${out.surveys_deleted === 1 ? '' : 's'} dropped · Egeria unchanged`);
        } catch (err) {
          fgo.disabled = false; fgo.textContent = 'Forget links'; delete fgo.dataset.pending;
          slot.querySelector('[data-forget-sentence]').insertAdjacentHTML(
            'afterend', err.status === 403
              ? `<div class="text-caveat" data-forget-error>${cue('no_access', 'not permitted', err.message)}</div>`
              : `<div class="text-caveat text-state-warn" data-forget-error>Not forgotten · ${esc(err.message)}</div>`);
        }
      });
    });

    const ft = dEl.querySelector('[data-file-measurements-section]');
    ft?.addEventListener('toggle', () => {
      if (ft.open && !ft.dataset.loaded) {
        ft.dataset.loaded = '1';
        renderFileTypeMeasurements(ft.querySelector('[data-file-measurements-host]'), slug);
      }
    });
  }

  return { ready, mountStructure };
}
