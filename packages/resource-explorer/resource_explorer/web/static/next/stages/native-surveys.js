/* Egeria-native surveys on Survey & analyses (BRIEF-NATIVE-EGERIA-SURVEY-LAUNCH.md).
 *
 * A survey Egeria's own survey action engine can run for this kind of resource
 * gets a Run control that submits an engine action through pyegeria. Prefect
 * is not in this path.
 *
 * THE RULE THIS FILE EXISTS TO KEEP: every status word on a row comes from the
 * server's `run` object, which the server derived from persisted proof
 * (`native_survey_run.derive_native_state`). This file never decides "running"
 * or "complete" from what the browser just did -- a click that got a 200 is not
 * a submission, an absent error is not a success. It only chooses how to say
 * what the proof already says:
 *
 *   submitted to Egeria · <time>          engine-action GUID stored
 *   running · read <time>                 Egeria's own status, read back
 *   complete · read <time>                + report GUID + annotations stored
 *   <Egeria's word> · <message>           Egeria's own words on a failure
 *
 * A survey RE cannot run for this kind is a ◌ row that says why -- never a
 * "○ not run", which would tell the reader it merely has not been run yet.
 *
 * Re-renders replace `#native-surveys` only. The engine-note bug
 * (ENGINE-NOTE-PERSISTENCE-IMPLEMENTED.md) was a status written to a transient
 * DOM node that a whole-pane reload then wiped; nothing here is transient.
 */
import { ago, localTime, storedCopyWords } from '/static/next/format.js';
import { stateEntry } from '/static/next/glyphs.js';
import {
  getNativeSurveys, runNativeSurvey, refreshNativeSurveys, getNativeSurveyReport,
  registerWithEgeria, checkNativeSurveyPointers, cancelNativeAction,
} from '/static/re-api.js';
import { state, esc, apiEntityType } from '/static/next/app.js';
import { openDialog } from '/static/next/worklist.js';
import { followUntil } from '/static/next/live-status.js';
import { onServerRestart } from '/static/next/shell-status.js';
import { openFindDbServersDialog } from '/static/next/db-server-discovery.js';
import { reportViewerHtml, bindReportViewer } from '/static/next/stages/native-report.js';

const POLL_MS = 8000;
const FOLLOW_GIVE_UP_MS = 10 * 60 * 1000;
const CONFIRM_MS = 6000;      // how long "Confirm: start again" stays armed

/** The state -> glyph-family mapping. Words come from the row, not from here:
 *  a glyph's own `word` is only its title. */
function glyphFor(run, runnable, row = {}) {
  // A registration that existed and is gone, and a database never given to Egeria, are NOT errors:
  // each has its own muted cue (registering with Egeria is optional; nothing is owed).
  if (row.stale) return 'gone';
  switch (run.state) {
    case 'complete': case 'registered': return 'measured';
    // Every step ended and some were refused: an amber half-filled cue, never the red error.
    case 'partial': return 'partial';
    // A cancel Egeria read back is a decision: one neutral cue, neither the tick nor the cross.
    case 'failed': if (run.egeria_status === 'CANCELLED') return 'cancelled'; return 'error';
    case 'submit_failed': return 'error';
    case 'submitted': case 'running': case 'awaiting_report': case 'report_incomplete':
    case 'awaiting_registration': return 'running';
    case 'unreadable': return 'unknown';
    case 'not_registered': return row.cannot_run_reason ? 'no-surveyor' : 'optional';
    default:
      if (row.neutral) return 'optional';
      return runnable ? 'unrun' : 'no-surveyor';
  }
}

function timeSpan(iso) {
  if (!iso) return '';
  return `<span class="tnum" title="${esc(iso)}">${esc(ago(iso))}</span>`;
}

function guidSpan(guid) {
  return guid ? `<span class="font-mono" title="Egeria engine action ${esc(guid)}">${esc(guid)}</span>` : '';
}

/** "◐ stalled?" when the server flagged the action as active far longer than a survey takes. The sentence
 *  (what was measured, and the limit) is on hover; the line carries only the cue and the word. */
function stallCue(r) {
  if (!r.stalled) return '';
  const hours = (s) => (s >= 86400 ? (() => { const d = Math.round(s / 3600 / 2.4) / 10; return `${d} ${d === 1 ? 'day' : 'days'}`; })() : (() => { const h = Math.round(s / 360) / 10; return `${h} ${h === 1 ? 'hour' : 'hours'}`; })());
  const tip = `Requested ${hours(r.requested_age_s)} ago and Egeria still says ${r.egeria_status || 'nothing yet'}. `
    + `Flagged after ${hours(r.stall_after_s)} ${r.phase === 'running' ? 'since the request (waiting included)' : 'without starting'}. `
    + 'RE does not cancel or retry on this.';
  return ` · <span class="text-state-warn" data-native-stalled title="${esc(tip)}"><span class="font-glyph" aria-hidden="true">◐</span> stalled?</span>`;
}

/** Compact span for the timing cue: "5m", "11h", "2.5d". */
function brief(s) {
  const round = (n) => Math.round(n * 10) / 10;
  if (s >= 86400) return `${round(s / 86400)}d`;
  if (s >= 3600) return `${round(s / 3600)}h`;
  if (s < 60) return '<1m';
  return `${Math.round(s / 60)}m`;
}

/** When Egeria's own start time is stored: "◌ queued 2.5d before start · running 11h" (queued = requested →
 *  started, running = started → now). The sentence is on hover. Empty when no start time is stored, so the
 *  caller falls back to "requested Xd ago". */
function timingCue(r) {
  if (!r.started_at || r.running_s == null) return '';
  const queued = r.queued_s != null ? `<span class="font-glyph" aria-hidden="true">◌</span> queued ${esc(brief(r.queued_s))} before start · ` : '';
  const tip = `${r.queued_s != null ? `Egeria queued the action ${brief(r.queued_s)} (requested ${r.requested_at} UTC, started ${r.started_at} UTC). ` : `Egeria started it at ${r.started_at} UTC. `}`
    + `It has run ${brief(r.running_s)} since the start, as of now.`;
  return `<span data-native-timing title="${esc(tip)}">${queued}running ${esc(brief(r.running_s))}</span>`;
}

/** A mixed process run (engine host step 4): the one run line the server built from what it read of each step
 *  ("Postgres survey then derived · step 1 PostgreSQLSurvey ✓ report cb92eedc · step 2 … ✓ …"), and each step's
 *  report opening as any report does. */
export function processLineHtml(r) {
  // Numbered by the step's own place in the run, then filtered: a step-2 report is never "step 1 report".
  const reports = (r.steps || []).map((s, i) => ({ s, n: i + 1 })).filter(({ s }) => s.report_guid)
    .map(({ s, n }) => `<button type="button"
      data-native-report="${esc(s.report_guid)}" class="cursor-pointer bg-transparent p-0 text-accent-ink underline"
      >step ${n} report ›</button>`).join(' · ');
  const waiting = r.waiting_for ? ` · <span class="text-state-warn" data-native-waiting><span class="font-glyph" aria-hidden="true">◌</span> ${esc(r.waiting_for)}</span>` : '';
  const err = r.error ? ` · <span class="text-state-warn">last read failed: ${esc(r.error)}</span>` : '';
  const tone = r.state === 'failed' ? 'text-state-warn' : '';
  // Where it started, in the reader's local time (the words carry who; the time is drawn from the row's own stored
  // submission time, so no UTC date is ever shown).
  const when = r.submitted_at ? localTime(r.submitted_at) : '';
  const whenHtml = when ? ` · <span class="tnum" data-native-started-at title="${esc(r.submitted_at)}">${esc(when)}</span>` : '';
  // A partial run: cue plus the short word ("2 of 4 ran"); each refused step's reason is on demand.
  let partial = '';
  if (r.state === 'partial') {
    const reasons = (r.problems || []).map((p) => `step ${p.step} ${p.label}: ${p.reason || (p.kind === 'refused' ? 'not run' : 'failed')}`);
    partial = `<span class="text-state-warn" data-native-partial><span class="font-glyph" aria-hidden="true">◐</span> ${esc(r.summary || 'partial')}</span>`
      + (reasons.length ? ` <button type="button" data-native-partial-why-open
          class="cursor-pointer bg-transparent p-0 text-ink-muted underline decoration-dotted"
          title="${esc(reasons.join('\n'))}">why</button><span hidden data-native-partial-why
          class="text-ink-muted"> ${reasons.map((x) => `<span class="block">${esc(x)}</span>`).join('')}</span>` : '')
      + ' · ';
  }
  // A refusal the person can fix says how, on the run line, with the way to where the credential is set.
  const fix = r.fixable
    ? `<span class="block" data-native-fixable>${esc(r.fixable.words)} · <button type="button" data-native-fix-credential
        class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${esc(r.fixable.action)} ›</button></span>` : '';
  return `${partial}<span class="${tone}" data-native-process-line>${esc(r.line)}</span>${whenHtml}${waiting}`
    + `${r.read_at ? ` · read ${timeSpan(r.read_at)}` : ''}${reports ? ` · ${reports}` : ''}${err}${fix}`;
}

/** The one line of status. Pure over `row.run`. */
export function nativeSurveyStatusHtml(row) {
  const r = row.run || { state: 'not_run' };
  const err = r.error ? ` · <span class="text-state-warn">${esc(r.error)}</span>` : '';
  if (r.line && ['running', 'complete', 'partial', 'failed'].includes(r.state)) return processLineHtml(r);
  switch (r.state) {
    case 'submit_failed':
      return `<span class="text-state-warn">not submitted — Egeria did not accept it: ${esc(r.error)}</span>`;
    case 'submitted':
      return `submitted to Egeria · ${timeSpan(r.submitted_at)}${stallCue(r)} · ${guidSpan(r.engine_action_guid)}`;
    case 'unreadable':
      return `submitted to Egeria · ${timeSpan(r.submitted_at)} · ${guidSpan(r.engine_action_guid)}`
        + ` · <span class="text-state-warn">last read failed: ${esc(r.error)}</span>`;
    case 'running':
      // Waiting to start and running are two states; both say when the action was requested, and a stall is a
      // cue plus a word. The stall age is measured from the request; when Egeria's start time is stored the line says queued vs running.
      return `${r.phase === 'waiting' ? '<span class="font-glyph" aria-hidden="true">◌</span> waiting to start' : (timingCue(r) || 'running')}`
        + `${timingCue(r) ? '' : (r.submitted_at ? ` · requested ${timeSpan(r.submitted_at)}` : '')}${stallCue(r)}`
        + ` · Egeria says <span class="font-mono">${esc(r.egeria_status)}</span>`
        + ` · read ${timeSpan(r.read_at)} · ${guidSpan(r.engine_action_guid)}${err}`;
    case 'awaiting_report':
      return `Egeria says <span class="font-mono">${esc(r.egeria_status)}</span> · read ${timeSpan(r.read_at)}`
        + ` — its report is not read into RE yet${err}`;
    case 'report_incomplete':
      return `Egeria says <span class="font-mono">${esc(r.egeria_status)}</span> · read ${timeSpan(r.read_at)}`
        + ` — <span class="text-state-warn">RE's copy of the report is incomplete; reading it again</span>${err}`;
    case 'complete':
      return `complete · read ${timeSpan(r.read_at)} · report from ${timeSpan(r.report_at)}`
        + ` · <button type="button" data-native-report="${esc(r.report_guid)}"
            class="cursor-pointer bg-transparent p-0 text-accent-ink underline"
            ><span class="tnum">${esc(String(r.annotation_count))}</span> annotation${
              r.annotation_count === 1 ? '' : 's'} ›</button>`;
    case 'failed':
      // A cancel Egeria read back as CANCELLED is a decision, not a fault: its own words, with the check.
      if (r.egeria_status === 'CANCELLED') {
        return `cancelled in Egeria`
          + ` · read ${timeSpan(r.read_at)} · ${guidSpan(r.engine_action_guid)}`;
      }
      return `<span class="text-state-warn"><span class="font-mono">${esc(r.egeria_status)}</span>`
        + ` · ${esc(r.message)}</span> · read ${timeSpan(r.read_at)} · ${guidSpan(r.engine_action_guid)}`;
    case 'registered': {
      // "Registered" proves only that the elements exist. Until a survey proves Egeria can connect, the row says so,
      // from the server's `reach` (derived from the persisted survey runs), never from the click.
      const reach = row.reach && row.reach.state !== 'connected' ? row.reach : null;
      return `registered with Egeria${reach
        ? ` · <span data-native-reach-state="${esc(reach.state)}">${esc(reach.words)}${reach.address ? ` (tried <span class="font-mono">${esc(reach.address)}</span>)` : ''}</span>` : ''}${row.asset_guid
        ? ` · database asset <span class="font-mono" title="Egeria database asset">${esc(row.asset_guid)}</span>` : ''}`;
    }
    case 'awaiting_registration':
      return `Egeria says <span class="font-mono">${esc(r.egeria_status)}</span> · read ${timeSpan(r.read_at)}`
        + ' — the database is not readable in Egeria yet';
    case 'not_registered':
      // A kind RE cannot register says exactly why (not wired yet, or what the record lacks); a stale
      // pointer says so; otherwise this is the plain optional state.
      return row.cannot_run_reason
        ? `<span class="text-ink-muted">can't be registered from here — ${esc(row.cannot_run_reason)}</span>`
        : (row.stale
          ? `<span class="text-ink-muted" data-native-gone>${esc(row.gone_line || 'no longer in Egeria')}</span>`
          : '<span class="text-ink-muted">not registered with Egeria · optional</span>');
    default:
      if (row.stale) return `<span class="text-ink-muted" data-native-gone>${esc(row.gone_line || 'no longer in Egeria')}</span>`;
      if (row.neutral) return `<span class="text-ink-muted">${esc(row.cannot_run_reason)}</span>`;
      return row.runnable
        ? 'not run'
        : `<span class="text-ink-muted">can't be run from here — ${esc(row.cannot_run_reason)}</span>`;
  }
}

/** The databases a server survey found, exactly as Egeria reported them. A database RE has registered and
 *  Egeria has no asset for is "found, not yet published", with the one control. */
export function discoveredDatabasesHtml(row) {
  const found = row.discovered || [];
  if (!found.length) return '';
  return `<div class="mt-s2" data-native-discovered>
    <div class="text-caps uppercase tracking-caps text-ink-muted">Databases Egeria found on this server</div>
    ${found.map((d) => `<div class="flex flex-wrap items-baseline gap-s2 py-[2px]">
      <span class="font-mono text-ink">${esc(d.name)}</span>
      <span class="text-provenance text-ink-muted">${
        d.state === 'cataloged' ? 'published in Egeria'
          : (d.re_slug ? 'found, not yet published' : 'found · not registered in RE')}</span>
      ${d.control ? `<button type="button" data-native-register-slug="${esc(d.re_slug)}"
        class="cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink"
        >${esc(d.control)}</button>` : ''}
    </div>`).join('')}
  </div>`;
}

/** Egeria's own sentence is in the status above, unchanged. When it reads as a connection problem the
 *  server adds one line, and RE's own survey stays available. */
function reachNoteHtml(row) {
  return row.reach_note
    ? `<div class="mt-[2px] text-provenance text-ink-muted" data-native-reach>${esc(row.reach_note)}</div>` : '';
}

/** A survey Egeria could not connect for names the address it tried (Egeria's own sentence stays whole above it).
 *  The "localhost is Egeria's own machine" clause is the server's, said only for localhost/127.0.0.1. */
export function triedHtml(row) {
  const t = row.tried;
  if (!t || !t.address) return '';
  return `<div class="mt-[2px] text-provenance text-ink-muted" data-native-tried>Egeria tried
    <span class="font-mono text-ink">${esc(t.address)}</span>${t.own_machine ? ", and localhost is Egeria's own machine" : ''} ·
    <button type="button" data-native-change-host class="cursor-pointer bg-transparent p-0 text-accent-ink underline">change the Egeria-visible host ›</button></div>`;
}

/** The Egeria lane of a registration row: Egeria still holds the database under an older address than the
 *  one RE would send now. Derived from the server's read-back record, so it stays until a read-back shows the new one. */
export function willChangeHtml(row) {
  const a = row.address;
  if (!a || !a.will_change) return '';
  return `<div class="mt-[2px] text-provenance text-ink" data-native-will-change><span class="font-glyph" aria-hidden="true">⚠</span>
    will change · Egeria has <span class="font-mono">${esc(a.held)}</span> · ${esc((row.register && row.register.label) || 'Register again →')} sends
    <span class="font-mono">${esc(a.current)}</span></div>`;
}

/** Cancel cues that outlive a repaint: a cancel Egeria did not confirm stays on its row until the next try
 *  (guid -> the words). A cancel that worked needs none: the row's own status then says CANCELLED. */
const cancelCues = new Map();
/** Cancels in flight (guid): a poll repaint mid-request keeps "cancelling…" on the row. */
const cancelling = new Set();

/** The cancel control, only while Egeria last said the action is waiting or running (the server's `run.state`
 *  is 'running' exactly for those statuses). Cue plus short words; the confirm is a state of the control. */
export function cancelControlHtml(row) {
  const r = row.run || {};
  const guid = r.engine_action_guid;
  // The server decides (`cancellable`: a single-action survey, Egeria's last read succeeded and said waiting or
  // running). A process run, a registration or a failed last read never offers it.
  const live = row.cancellable === true && r.state === 'running' && !r.error;
  const cue = guid && cancelCues.get(guid);
  const keep = cue && live
    ? `<div class="mt-[2px] text-provenance text-state-warn" data-native-cancel-cue><span class="font-glyph" aria-hidden="true">◐</span> ${esc(cue)}</div>` : '';
  if (!live || !guid) return '';
  return `<div class="mt-[2px]" data-native-cancel-box="${esc(guid)}">
    <button type="button" data-native-cancel="${esc(guid)}"
      class="cursor-pointer border-0 bg-transparent p-0 text-provenance text-accent-ink underline">Cancel</button>
    <span hidden data-native-cancel-confirm class="text-provenance text-ink">
      <span class="font-glyph" aria-hidden="true">◌</span> cancel this survey in Egeria?
      <button type="button" data-native-cancel-yes="${esc(guid)}" class="cursor-pointer rounded-sm border border-state-warn bg-transparent px-2 text-provenance text-state-warn">Yes, cancel</button>
      <button type="button" data-native-cancel-no class="cursor-pointer border-0 bg-transparent p-0 text-provenance text-ink-muted underline">keep it</button>
    </span>
    <span ${cancelling.has(guid) ? '' : 'hidden'} data-native-cancel-ack class="text-provenance text-ink-muted">${
  cancelling.has(guid) ? '<span class="font-glyph" aria-hidden="true">◔</span> cancelling…' : ''}</span>
    ${keep}</div>`;
}

/** A run RE stopped following: the state word stays (the run's state has not changed, only RE stopped looking),
 *  the line says when it was last read, and "check now" reads once. Replaces the old "reload to check again". */
export function stoppedFollowingHtml(row) {
  const r = row.run || {};
  const asOf = r.read_at ? new Date(Date.parse(r.read_at)).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false }) : '';
  return `<div class="mt-[2px] text-provenance text-ink-muted" data-native-stopped><span class="font-glyph" aria-hidden="true">◔</span>
    ${esc(r.state === 'submitted' ? 'submitted' : 'running')}${asOf ? ` · as of <span class="tnum">${esc(asOf)}</span>` : ''} · stopped checking after 10 minutes ·
    <button type="button" data-native-check-now class="cursor-pointer bg-transparent p-0 text-accent-ink underline">check now</button></div>`;
}

/** One survey row: glyph, name, kind, status, and Run when RE can run it. `stopped`: RE gave up following it. */
export function nativeSurveyRowHtml(row, opts = {}) {
  const run = row.run || { state: 'not_run' };
  const g = stateGlyph(glyphFor(run, row.runnable, row));
  const busy = !!row.in_flight;
  const ran = run.state !== 'not_run';
  return `<div class="flex flex-wrap items-baseline gap-s2 border-b border-rule py-s2" data-native-survey="${esc(row.qualified_name)}">
    <span class="w-[16px] shrink-0 ${g.tone}" title="${esc(g.word)}" aria-label="${esc(g.word)}">${g.glyph}</span>
    <div class="min-w-0 flex-1">
      <div class="flex flex-wrap items-baseline gap-s2">
        <span class="text-answer text-ink">${esc(row.display_name)}</span>
        <span class="text-provenance text-ink-muted">${row.kind === 'mixed_process'
          ? "Egeria process · native survey, then Resource Explorer's engine host"
          : row.kind === 're_definition'
            ? "Resource Explorer's Survey Definition · coordinated by Egeria"
            : "Egeria's own survey engine"}</span>
      </div>
      <div class="mt-[2px] text-provenance text-ink-muted" data-native-status>${nativeSurveyStatusHtml(row)}</div>
      ${opts.stopped === true && row.in_flight ? stoppedFollowingHtml(row) : ''}
      ${cancelControlHtml(row)}
      ${row.credentials_note ? `<div class="mt-[2px] text-provenance text-ink-muted"><span class="text-ink-muted" title="not confirmed either way">?</span> ${esc(row.credentials_note)}</div>` : ''}
      ${reachNoteHtml(row)}
      ${triedHtml(row)}
      ${willChangeHtml(row)}
      ${(row.notes || []).map((n) => `<div class="mt-[2px] text-provenance text-ink-muted" data-native-note>${esc(n)}</div>`).join('')}
      ${(row.description && row.kind !== 're_definition') ? `<div class="mt-[2px] max-w-[70ch] text-provenance text-ink-muted">${esc(row.description)}</div>` : ''}
      ${discoveredDatabasesHtml(row)}
    </div>
    ${(!row.runnable && row.register && row.register.available) ? `<button type="button" data-native-register="${esc(row.qualified_name)}" ${row.register.start_again ? `data-native-start-again="1" ${row.register.one_press ? 'data-native-one-press="1"' : ''} data-native-confirm="${esc(row.register.confirm || '')}"` : ''} ${busy ? 'disabled' : ''}
      class="shrink-0 cursor-pointer rounded-sm border border-accent bg-transparent px-2 py-[2px] text-caveat text-accent-ink"
      >${esc(row.register.label)}</button>` : ''}
    ${row.kind === 're_definition' ? coordinateControlHtml(row, { ran }) : ''}
    ${row.runnable && row.kind !== 're_definition' ? `<button type="button" data-native-run="${esc(row.qualified_name)}" ${busy ? 'disabled' : ''}
      class="shrink-0 cursor-pointer rounded-sm border ${busy ? 'border-rule-strong text-ink-muted' : 'border-accent text-accent-ink'} bg-transparent px-2 py-[2px] text-caveat"
      >${busy ? 'in flight' : (ran ? 'Run again in Egeria →' : 'Run in Egeria →')}</button>` : ''}
    <span data-native-info="${esc(row.qualified_name)}" class="hidden w-full text-provenance text-ink-muted"></span>
    <span data-native-error="${esc(row.qualified_name)}" class="hidden w-full text-provenance text-state-warn"></span>
  </div>`;
}

const stateGlyph = stateEntry;

/** The rows of Egeria's OWN surveys: native surveys and mixed processes. A Survey Definition's coordinated run is a run
 *  of the definition RE authored, not a survey of Egeria's, so it is shown on the definition's own card
 *  (designer 2026-10-16, Q1: one thing, one card) and `re_definition` rows are not listed here. */
export const ownSurveyRows = (rows) => (rows || []).filter((r) => r.kind !== 're_definition');

export function nativeSurveysSectionHtml(rows) {
  if (!rows || !rows.length) return '';
  const own = ownSurveyRows(rows);
  // Only definition runs to follow: the host stays (it drives the follow and the re-read) but nothing is drawn.
  if (!own.length) return '<div id="native-surveys" hidden></div>';
  return `<div class="mt-s3 mb-s3">
    <div class="text-caps uppercase tracking-caps text-ink-muted">Egeria's own surveys</div>
    <div class="text-caveat text-ink-muted">Run by Egeria's survey action engine; RE submits the
      action and reads back what Egeria says about it.</div>
    <div id="native-surveys">${own.map((r) => nativeSurveyRowHtml(r)).join('')}</div>
  </div>`;
}

/** A definition card's run line: the latest run, whoever coordinated it, with the same words either way (the
 *  server's derived row, not re-derived here). `stopped`: RE gave up following it (state word stays, with "check now"). */
export function definitionRunLineHtml(row, { stopped = false } = {}) {
  const g = stateGlyph(glyphFor(row.run || { state: 'not_run' }, row.runnable, row));
  return `<div class="mt-[2px] text-provenance text-ink-muted" data-def-run-line="${esc(row.qualified_name)}">
    <span class="${g.tone}" title="${esc(g.word)}" aria-label="${esc(g.word)}">${g.glyph}</span>
    <span data-native-status>${nativeSurveyStatusHtml(row)}</span>${stopped && row.in_flight ? stoppedFollowingHtml(row) : ''}</div>`;
}

const RUN_STATE_WORD = { complete: 'complete', partial: 'partial', failed: 'failed', running: 'running', submitted: 'submitted' };

/** "runs · 6 ›": every run of the definition, whoever coordinated it (the server's `runs`, newest first). A run made
 *  under an earlier version of the definition carries "(4-step version)" after its count. */
export function definitionRunsListHtml(c) {
  const runs = c.runs || [];
  if (!runs.length) return '';
  const item = (r) => {
    const who = r.coordinated_by === 'egeria' ? 'coordinated by Egeria' : 'by RE';
    const count = r.ran != null && r.of != null ? ` · <span class="tnum">${r.ran} of ${r.of}</span> ran${r.version_note ? ` <span data-run-version>${esc(r.version_note)}</span>` : ''}` : '';
    const reports = (r.reports || []).map((x) => `<button type="button" data-native-report="${esc(x.guid)}"
      class="cursor-pointer bg-transparent p-0 text-accent-ink underline">step ${x.step} report ›</button>`).join(' · ');
    return `<li data-def-run class="py-[1px]"><span class="tnum">${esc(localTime(r.when))}</span> · ${who}${r.by ? ` · started by ${esc(r.by)}` : ''}
      · ${esc(RUN_STATE_WORD[r.state] || r.state || '')}${count}${reports ? ` · ${reports}` : ''}</li>`;
  };
  return `<details data-def-runs class="mt-[2px] text-provenance text-ink-muted"><summary class="cursor-pointer">runs · <span class="tnum">${runs.length}</span> ›</summary>
    <ul class="ml-s3 list-none">${runs.map(item).join('')}</ul></details>`;
}

/** A failed native-survey read is not "no native surveys": the section keeps
 *  its heading and says the read failed, with a re-check. Never drawn as nothing. */
export function nativeSurveysUnreadableHtml() {
  return `<div class="mt-s3 mb-s3" id="native-surveys-unreadable">
    <div class="text-caps uppercase tracking-caps text-ink-muted">Egeria's own surveys</div>
    <div class="text-caveat text-state-warn">? couldn't read Egeria's surveys ·
      <button type="button" data-native-recheck class="cursor-pointer bg-transparent underline">re-check</button></div>
  </div>`;
}

const _follow = new WeakMap();
const _rereaders = new WeakMap();
const _bound = new Set();   // hosts that registered a re-read

/** Re-read every bound native-survey section still on screen. */
export function rereadNativeSurveys() {
  for (const host of [..._bound]) {
    if (!host.isConnected) { _bound.delete(host); continue; }
    _rereaders.get(host)?.();
  }
}
let _restartWired = false;
const _wiredDocs = new WeakSet();
function wireReturnAndRestart() {
  if (!_restartWired) { _restartWired = true; onServerRestart(rereadNativeSurveys); }
  if (typeof document === 'undefined' || _wiredDocs.has(document)) return;
  _wiredDocs.add(document);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') rereadNativeSurveys(); });
  if (typeof window !== 'undefined') window.addEventListener('focus', rereadNativeSurveys);
}

/** Wire a rendered section: Run buttons, the annotations view, and the poll
 *  that runs only while something is in flight. Safe to call again on a fresh
 *  host; the previous host's timer dies with its detached node. */
export function bindNativeSurveys(root, slug, initialRows, { pollMs = POLL_MS, onRows = null } = {}) {
  const host = root.querySelector('#native-surveys');
  if (!host) return;
  let rows = initialRows || [];

  let gaveUp = false;                    // RE stopped following the rows in flight: each says so, with "check now"
  const draw = (next) => {
    rows = next;
    host.innerHTML = ownSurveyRows(rows).map((r) => nativeSurveyRowHtml(r, { stopped: gaveUp })).join('');
    bindRows();
    if (onRows) onRows(rows, { stopped: gaveUp });      // the definition cards follow the same rows
    bindCoordinateControls(host, slug, { after: () => { reread(); } });
  };
  const paint = (next) => { gaveUp = false; draw(next); schedule(); };
  const alive = () => host.isConnected && state.selectedSlug === slug && state.subTab === 'survey';
  const kind = () => ({ entityType: apiEntityType(state.resourceType) });
  // Follow the rows while any is in flight (2 s for the first minute, then 10 s), repainting each answer, and stop
  // at the first answer with nothing in flight. `pollMs` (tests) fixes the pace.
  const schedule = () => {
    _follow.get(host)?.stop();
    _follow.delete(host);
    if (!rows || !rows.some((r) => r.in_flight)) return;
    _follow.set(host, followUntil({
      read: () => refreshNativeSurveys(slug, kind()),
      onUpdate: (res) => draw(res.surveys),
      isDone: (res) => !(res.surveys || []).some((r) => r.in_flight),
      alive, fixedMs: pollMs === POLL_MS ? null : pollMs,
      // A state stuck in flight must not be read forever: after 10 minutes the page says it stopped, once.
      maxMs: FOLLOW_GIVE_UP_MS,
      onGiveUp: () => {
        if (!host.isConnected || gaveUp) return;
        gaveUp = true;
        draw(rows);
      },
    }));
  };
  // What a row may do (runnable or not) is the server's answer today; a restart or a long absence can change it.
  // A cheap READ (no Egeria refresh), repainted unless something is mid-press.
  const reread = async () => {
    if (!alive()) return;
    try {
      const res = await getNativeSurveys(slug, kind());
      if (alive()) paint(res.surveys);
    } catch (_) { /* the rows on screen stay; the next trigger tries again */ }
  };
  _rereaders.set(host, reread);
  const showError = (qn, msg) => {
    const slot = [...host.querySelectorAll('[data-native-error]')].find((e) => e.dataset.nativeError === qn);
    if (!slot) return;
    slot.textContent = msg;
    slot.classList.remove('hidden');
  };
  // "check now": one read, repainted from the server's rows (which resumes following if the run is still going).
  const checkNow = async () => {
    const res = await refreshNativeSurveys(slug, kind());
    if (alive()) paint(res.surveys);
  };
  const bindRows = () => {
    host.querySelectorAll('[data-native-run]').forEach((b) => b.addEventListener('click', async () => {
      const qn = b.dataset.nativeRun;
      b.disabled = true;
      try {
        const res = await runNativeSurvey(slug, qn, { entityType: apiEntityType(state.resourceType) });
        paint(res.surveys);
      } catch (err) {
        // 409 (already in flight) and 422 (RE knows why it cannot) are REASONS;
        // 502 (Egeria refused) is also persisted, so the repaint below shows it
        // on the row rather than only here.
        b.disabled = false;
        showError(qn, err.message);
        if (err.status === 422) await checkPointers();     // a stale pointer is found by a READ, then the row offers the control
        if (err.status === 502) {
          try { paint((await getNativeSurveys(slug, { entityType: apiEntityType(state.resourceType) })).surveys); }
          catch (_) { /* the message above already says what happened */ }
        }
      }
    }));
    // Registering is a choice made with a press. The press shows at once that it was pressed
    // (disabled, "registering…"), and the result repaints from the server's rows -- never from the click.
    const showInfo = (qn, msg) => {
      const slot = [...host.querySelectorAll('[data-native-info]')].find((e) => e.dataset.nativeInfo === qn);
      if (!slot || !msg) return;
      slot.textContent = msg;
      slot.classList.remove('hidden');
    };
    // The confirm is a state of the button: it must not outlive its moment. Reset after a refused or
    // failed press, and on a timer, so a later click can never send without asking again.
    const resetConfirm = (b) => {
      if (b.dataset.confirm !== '1') return;
      clearTimeout(b._confirmTimer);
      delete b.dataset.confirm;
      if (b.dataset.label) b.textContent = b.dataset.label;
      b.classList.remove('border-state-warn');
      const slot = b.closest('[data-native-survey]')?.querySelector('[data-native-info]');
      if (slot) { slot.textContent = ''; slot.classList.add('hidden'); }      // the line goes with the disarm
    };
    const register = async (b, targetSlug, qn, startAgain = false) => {
      if (startAgain && b.dataset.confirm !== '1' && !b.dataset.nativeOnePress) {
        // A second registration can create a second database: ask once, visibly, before sending it. When the
        // server says there is nothing to duplicate (the earlier process failed), it is one press.
        b.dataset.confirm = '1';
        b.dataset.label = b.textContent;
        b.textContent = 'Confirm: start again';
        b.classList.add('border-state-warn');
        showInfo(qn, `${b.dataset.nativeConfirm || 'Starting again may create a second database element in Egeria.'} Press again to confirm.`);
        b._disarm = () => resetConfirm(b);
        b._confirmTimer = setTimeout(b._disarm, CONFIRM_MS);
        return;
      }
      clearTimeout(b._confirmTimer);
      const label = b.dataset.label || b.textContent;
      b.disabled = true;
      b.textContent = 'registering…';
      try {
        const res = await registerWithEgeria(targetSlug, { entityType: apiEntityType(state.resourceType), startAgain });
        if (targetSlug === slug) {
          paint(res.surveys);
          const proj = (res.registered && res.registered.projected) || {};
          // Said, never silent: the press may have written the local secrets file.
          showInfo(qn, proj.written ? 'RE re-projected the secrets file (a local write) so Egeria can read this database\'s credentials' : '');
          showInfo(qn, proj.checked === false ? proj.reason : '');
        }
        else {
          b.textContent = 'registered · see that database';
        }
      } catch (err) {
        b.disabled = false;
        b.textContent = label;
        resetConfirm(b);
        b.classList.remove('border-state-warn');
        delete b.dataset.confirm;
        showError(qn || '', err.message);
        if (err.status === 502 && targetSlug === slug) {
          try { paint((await getNativeSurveys(slug, { entityType: apiEntityType(state.resourceType) })).surveys); }
          catch (_) { /* the message above already says what happened */ }
        }
      }
    };
    host.querySelectorAll('[data-native-register]').forEach((b) => b.addEventListener('click',
      () => register(b, slug, b.dataset.nativeRegister, !!b.dataset.nativeStartAgain)));
    host.querySelectorAll('[data-native-register-slug]').forEach((b) => b.addEventListener('click',
      () => register(b, b.dataset.nativeRegisterSlug, '')));
    bindRunLineControls(host, slug, { checkNow });
    // Where the Egeria-visible host is edited (the saved sources, per server and per database).
    host.querySelectorAll('[data-native-change-host]').forEach((b) => b.addEventListener('click', () => openFindDbServersDialog()));
    host.querySelectorAll('[data-native-cancel]').forEach((b) => b.addEventListener('click', () => {
      const box = b.closest('[data-native-cancel-box]');
      b.hidden = true;
      const conf = box.querySelector('[data-native-cancel-confirm]');
      conf.hidden = false;
      // The confirm is a state of the control: it lapses, as the register confirm does.
      setTimeout(() => { if (box.isConnected && !cancelling.has(box.dataset.nativeCancelBox)) { conf.hidden = true; b.hidden = false; } }, CONFIRM_MS);
    }));
    host.querySelectorAll('[data-native-cancel-no]').forEach((b) => b.addEventListener('click', () => {
      const box = b.closest('[data-native-cancel-box]');
      box.querySelector('[data-native-cancel-confirm]').hidden = true;
      box.querySelector('[data-native-cancel]').hidden = false;
    }));
    host.querySelectorAll('[data-native-cancel-yes]').forEach((b) => b.addEventListener('click', async () => {
      const guid = b.dataset.nativeCancelYes;
      const box = b.closest('[data-native-cancel-box]');
      const ack = box.querySelector('[data-native-cancel-ack]');
      box.querySelector('[data-native-cancel-confirm]').hidden = true;
      cancelCues.delete(guid);
      cancelling.add(guid);
      ack.hidden = false;
      ack.innerHTML = '<span class="font-glyph" aria-hidden="true">◔</span> cancelling…';   // acknowledged at once
      const done = () => cancelling.delete(guid);
      try {
        const res = await cancelNativeAction(slug, guid, { entityType: apiEntityType(state.resourceType) });
        done();
        if (res.cancelled) {
          // Validated by the read-back, not by the 200: the repaint below is the server's own rows.
          paint(res.surveys);          // the row's own status now reads "✓ cancelled in Egeria"
          if (res.note) {
            const d = openDialog('Cancelled in Egeria', '');
            d.querySelector('#wl-detail-body').innerHTML = `<p class="text-ink" data-native-cancel-note>${esc(res.note)}</p>`;
          }
          return;
        }
        const why = res.read_error
          ? `asked Egeria to cancel; the read-back failed (${res.read_error})`
          : `asked Egeria to cancel; it still says ${res.egeria_status || 'nothing'}`;
        cancelCues.set(guid, why);
        paint(res.surveys);
        failDialog(why);
      } catch (err) {
        done();
        const why = `not cancelled: ${err.message}`;
        cancelCues.set(guid, why);
        paint(rows);
        failDialog(why);
      }
    }));
  };

  // Read the stored pointer back from Egeria, once, ONLY when one is stored (registered rows carry the
  // database asset's GUID). A never-registered database makes no Egeria call at all.
  const checkPointers = async () => {
    try {
      const res = await checkNativeSurveyPointers(slug, { entityType: apiEntityType(state.resourceType) });
      if (alive()) paint(res.surveys);
    } catch (_) { /* the rows already on screen are what RE knows; a failed check says nothing */ }
  };

  _bound.add(host);
  wireReturnAndRestart();
  // The rows were rendered by the pane from the same fetch; bind them, and
  // start polling only if any of them is in flight.
  bindRows();
  bindCoordinateControls(host, slug, { after: () => { reread(); } });
  schedule();
  if (rows.some((r) => r.asset_guid)) checkPointers();
}

/** A failed cancel: the reason in a pop-up (the persistent cue is on the row). */
function failDialog(reason) {
  const d = openDialog('Could not cancel the survey', '');
  d.querySelector('#wl-detail-body').innerHTML = `<p class="text-state-warn" data-native-cancel-error>${esc(reason)}</p>`;
}

export async function openReport(slug, reportGuid) {
  const d = openDialog('Egeria survey report', reportGuid);
  const body = d.querySelector('#wl-detail-body');
  let rep;
  try {
    rep = await getNativeSurveyReport(slug, reportGuid, { entityType: apiEntityType(state.resourceType) });
  } catch (err) {
    body.innerHTML = `<p class="text-state-warn">${esc(err.message)}</p>`;
    return;
  }
  // This is RE's own stored copy; say so, with when it was read from Egeria, and ONE added line only when the
  // reset marker postdates that read (the server decides, from the marker row).
  const copy = storedCopyWords(rep);
  body.innerHTML = `
    <div class="mb-s2 text-caps uppercase tracking-caps text-ink-muted">${esc(copy.head)}</div>
    ${copy.resetLine ? `<div class="mb-s2 text-provenance text-state-warn" data-stored-copy-reset>${esc(copy.resetLine)}</div>` : ''}
    ${reportViewerHtml(rep)}`;
  bindReportViewer(body, rep);
}

/* ── "coordinated by Egeria" (engine host case 3) ──────────────────────────
 *
 * The second control beside a Survey Definition's Run (owner, 2026-10-15; never "run in Egeria"). It asks Egeria to
 * run the definition as a process (`initiate_gov_action_process` through the server's EgeriaSurveyPort); Egeria
 * then hands each step to Resource Explorer's engine host. Hidden, with a short sentence, when it cannot be offered
 * (engine host off, ...). Its state is ON the control -- pressed, starting, started, not started -- with a cue and a
 * short word; the sentence is on demand. "started" comes from the server's answer, which it derived from the
 * persisted row (`native_survey_run.derive_mixed_state`), never from the click. */

const COORD_IN_FLIGHT = new Set(['submitted', 'running', 'awaiting_report', 'unreadable']);

/** The control for one definition row. `c.coordinated_by_egeria` is the server's {available, why_not, state, line}; the
 *  server leaves it off rows it does not belong to (repository definitions). When it cannot be offered the row draws
 *  nothing: the reason is said once for the pane (`coordinateOffNoteHtml`), not on every row. */
export function coordinateControlHtml(c, { ran = null } = {}) {
  const ctl = c && c.coordinated_by_egeria;
  if (!ctl) return '';
  const qn = esc(c.qualified_name || c.guid || '');
  // Never "run in Egeria": Egeria coordinates, Resource Explorer's engine host runs each step.
  const hasRun = ran == null ? !!(c.last_run_at || ctl.state) : ran;
  const label = esc(`${hasRun ? 'Re-run' : 'Run'} · coordinated by Egeria →`);
  if (!ctl.available) {
    // Shown disabled, never hidden; the reason sits beside it in words, the full sentence on hover.
    const why = ctl.why_not || 'not offered for this definition';
    return `<span class="inline-flex shrink-0 items-baseline gap-s1">
    <button type="button" data-coordinate-egeria="${qn}" disabled aria-disabled="true" title="${esc(why)}"
      class="cursor-not-allowed rounded-sm border border-rule-strong bg-transparent px-2 py-[2px] text-caveat text-ink-muted"
      >${label}</button>
    <span data-coordinate-unavailable="${qn}" class="text-provenance text-ink-muted" title="${esc(why)}">${esc(why)}</span></span>`;
  }
  const live = COORD_IN_FLIGHT.has(String(ctl.state || ''));
  return `<span class="inline-flex shrink-0 items-baseline gap-s1">
    <button type="button" data-coordinate-egeria="${qn}" ${live ? 'disabled aria-pressed="true"' : 'aria-pressed="false"'}
      title="${esc(live ? (ctl.line || 'Egeria is coordinating a run of this definition') : 'Egeria runs this definition as a process; Resource Explorer\'s engine host runs each step')}"
      class="cursor-pointer rounded-sm border ${live ? 'border-rule-strong text-ink-muted' : 'border-accent text-accent-ink'} bg-transparent px-2 py-[2px] text-caveat"
      >${live ? `<span aria-hidden="true">◔</span> ${label} · running` : label}</button>
    <span data-coordinate-why="${qn}" class="hidden text-provenance"></span></span>`;
}

/** Once for the pane: why "coordinated by Egeria" is not offered on the rows that do not offer it (engine host off,
 *  not published to Egeria, ...): each distinct reason once, short words on the line, the server's full reason on
 *  hover. Rows that offer it, and rows it does not belong to (repository definitions), add nothing. */
export function coordinateOffNoteHtml(candidates) {
  const off = (candidates || []).map((c) => c && c.coordinated_by_egeria).filter((x) => x && !x.available);
  if (!off.length) return '';
  const seen = new Map();                                 // "label · reason" -> [label, reason], each once
  for (const x of off) {
    const label = x.label || 'coordinated by Egeria';
    const why = x.why_not || 'not offered for this definition';
    seen.set(`${label}\u0000${why}`, [label, why]);
  }
  return [...seen.values()].map(([label, why]) => `<div data-coordinate-off class="mt-s1 text-provenance text-ink-muted" title="${esc(why)}">
    <span aria-hidden="true">◌</span> ${esc(label)} · ${esc(shortWhy(why))}</div>`).join('');
}

/** The first clause of a reason, for the short words ("engine host off"). */
function shortWhy(text) {
  return String(text || '').split(' · ')[0].slice(0, 60);
}

/** Bind every "coordinated by Egeria" control under `el`. `after` repaints the pane from the server's rows. */
export function bindCoordinateControls(el, slug, { after = null } = {}) {
  el.querySelectorAll('[data-coordinate-egeria]').forEach((b) => {
    if (b._coordBound) return;                              // the definition card and the survey row may share a root
    b._coordBound = true;
    b.addEventListener('click', async () => {
    if (b.disabled) return;                                 // a pressed control ignores a second press
    const qn = b.dataset.coordinateEgeria;
    const why = b.parentElement && b.parentElement.querySelector('[data-coordinate-why]');
    b.disabled = true;
    b.setAttribute('aria-pressed', 'true');
    b.innerHTML = '<span aria-hidden="true">◔</span> starting…';
    if (why) { why.textContent = ''; why.classList.add('hidden'); }
    try {
      const res = await runNativeSurvey(slug, qn, { entityType: apiEntityType(state.resourceType) });
      const run = (res && res.run) || {};
      const started = COORD_IN_FLIGHT.has(String(run.state || '')) || run.state === 'complete' || run.state === 'partial';
      b.innerHTML = started
        ? '<span class="text-state-ok" aria-hidden="true">●</span> started'
        : `<span class="text-state-warn" aria-hidden="true">✗</span> ${esc(run.state || 'not started')}`;
      b.title = run.line || run.message || (run.engine_action_guid ? `process ${run.engine_action_guid}` : '');
      if (typeof after === 'function') after();
    } catch (err) {
      b.disabled = false;
      b.setAttribute('aria-pressed', 'false');
      b.innerHTML = '<span class="text-state-warn" aria-hidden="true">✗</span> not started';
      if (why) {
        // The sentence on demand: one click on "why" shows it, the button keeps the short word.
        why.innerHTML = `<button type="button" class="cursor-pointer bg-transparent p-0 text-ink-muted underline"
          data-coordinate-why-open>why</button><span class="hidden text-state-warn" data-coordinate-why-text>${esc(err.message || '')}</span>`;
        why.classList.remove('hidden');
        why.querySelector('[data-coordinate-why-open]').addEventListener('click', (ev) => {
          ev.currentTarget.classList.add('hidden');
          why.querySelector('[data-coordinate-why-text]').classList.remove('hidden');
        });
      }
    }
    });
  });
}

/** The controls a run line carries, wherever the line is drawn (the list of Egeria's own surveys or a definition card):
 *  the report links, "why" on a partial run's refused steps, the credential link of a fixable refusal and "check now".
 *  Each button binds once. `checkNow` is the caller's one read-and-repaint. */
export function bindRunLineControls(root, slug, { checkNow = null } = {}) {
  const once = (b, fn) => { if (b._runLineBound) return; b._runLineBound = true; b.addEventListener('click', fn); };
  root.querySelectorAll('[data-native-report]').forEach((b) => once(b, () => openReport(slug, b.dataset.nativeReport)));
  // A partial run's refused steps, with the launcher's reasons: one press on "why" shows them (the sentence on demand).
  root.querySelectorAll('[data-native-partial-why-open]').forEach((b) => once(b, () => {
    const text = b.parentElement.querySelector('[data-native-partial-why]');
    if (text) { text.hidden = !text.hidden; b.setAttribute('aria-expanded', String(!text.hidden)); }
  }));
  // A fixable refusal: the link goes to where the database credential is set (the resource header's door).
  root.querySelectorAll('[data-native-fix-credential]').forEach((b) => once(b, () => {
    const door = document.querySelector('[data-act="change-credentials"]');
    if (door) { door.scrollIntoView?.({ block: 'center' }); door.click(); }
  }));
  // "check now" on a run RE stopped following: reads once and resumes following if it is still going.
  root.querySelectorAll('[data-native-check-now]').forEach((b) => once(b, async () => {
    if (b.disabled || !checkNow) return;
    b.disabled = true;
    b.textContent = 'checking…';
    try { await checkNow(); } catch (err) {
      b.disabled = false;
      b.textContent = 'check now';
      b.insertAdjacentHTML('afterend', ` <span class="text-state-warn" data-native-check-error>could not check: ${esc(err.message || '')}</span>`);
    }
  }));
}
