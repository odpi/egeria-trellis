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
  registerWithEgeria, checkNativeSurveyPointers, cancelNativeAction, cancelProcessRun,
} from '/static/re-api.js';
import { state, esc, apiEntityType, openAnalysisOverrideDialog } from '/static/next/app.js';
import { openCredentialForm } from '/static/next/credential-change.js';
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
  if (row.name_taken) return 'name_taken_unreadable';
  if (row.registered_again) return 'registered_again';
  // The sweep stopped retrying the report: nothing is running, and nothing is proven either.
  if (run.read_gave_up) return 'unknown';
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

/** A pointer that reads as absent while Egeria holds a database of this name that this user cannot read: the
 *  server's words (`name_taken_line`), no register control (it would make a second element). */
function nameTakenHtml(row) {
  return `<span class="text-ink-muted" data-native-name-taken>${esc(row.name_taken_line || "Egeria has a database of this name RE can't read · ask its owner")}</span>`;
}

/** The pointer reads as absent and the lookup by name found a DIFFERENT GUID: registered again. The register
 *  press (the existing one) looks the element up by name and adopts it. */
function registeredAgainHtml(row) {
  return `<span class="text-ink-muted" data-native-registered-again>${esc(row.registered_again_line || 'registered again under another GUID')}</span>`;
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

/** The words of a coordinated run's one-run credential (DESIGN-COORDINATED-RUN-CREDENTIAL.md §2.1, §2.3, §2.4).
 *  PLACEHOLDERS: the designer's words are open (Q3); every word the control and the run line draw for it is here, so
 *  one edit changes them. */
export const RUN_CREDENTIAL_WORDS = {
  // PLACEHOLDER: the second control beside "Run · coordinated by Egeria →"; it opens the existing credential dialog.
  optionsLabel: 'with options…',
  optionsTitle: 'Opens a dialog: run this definition once, coordinated by Egeria, with another credential',
  // PLACEHOLDER: the existing dialog's lead line, for the coordinated case (its definition-card copy is unchanged).
  dialogSubtitle: 'Egeria runs this definition; RE\'s own steps use this credential once; it is not stored',
  // PLACEHOLDER: a step Egeria runs itself reads Egeria's secrets store, never the one-run credential.
  nativeStep: (n) => `step ${n} runs in Egeria with the stored credential`,
  // PLACEHOLDER: a later step refused because this process does not hold the credential. The neutral words; the run
  // line shows the step's own, more specific words when the server proved which (restarted, timed out, another process).
  lost: 'this Resource Explorer process no longer holds the one-run credential (restarted, timed out, or the step ran in another process)',
  // PLACEHOLDER: the stored login's capability probe says nothing about the one-run credential.
  capabilityNotAssessed: 'not assessed for this run\'s credential',
  // PLACEHOLDER: Egeria started the run, the credential is held, and the run row's marker could not be written.
  markerNotRecorded: 'started · the one-run credential\'s marker wasn\'t recorded',
};

/** "ran as <user> (scope)": the same form RE's own override runs show (`analysisRanAsHtml`, app.js). */
export function ranAsHtml(a) {
  if (!a || !a.user) return '';
  return `ran as <span class="font-mono">${esc(a.user)}</span> (${esc(a.scope || 'this run')})`;
}

/** The one-run credential's part of a run line, from the server's derived row only (`run_credential`, `ran_as`,
 *  `native_steps`, `credential_lost`). Nothing when the run was not started with one. */
export function runCredentialHtml(r) {
  if (!r || !r.run_credential) return '';
  const w = RUN_CREDENTIAL_WORDS;
  const capability = r.ran_as && r.ran_as.capability
    ? ` · <span data-run-credential-capability>capability ${esc(r.ran_as.capability)}</span>` : '';
  const ranAs = r.ran_as && r.ran_as.user
    ? ` · <span data-run-credential-ran-as class="text-ink-muted">${ranAsHtml(r.ran_as)}${capability}</span>` : '';
  const native = (r.native_steps || []).map((n) => `<span class="block text-ink-muted" data-run-credential-native-step="${esc(n)}">${esc(w.nativeStep(n))}</span>`).join('');
  const lost = r.credential_lost
    ? `<span class="block text-state-warn" data-run-credential-lost><span class="font-glyph" aria-hidden="true">◐</span> ${esc(r.credential_lost_words || w.lost)}</span>` : '';
  return `${ranAs}${native}${lost}`;
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
  // A run Egeria cancelled is a decision, not a fault: the neutral tone the ⊘ cancelled glyph has, never the warn one.
  const tone = r.state === 'failed' ? (r.egeria_status === 'CANCELLED' ? 'text-ink-muted' : 'text-state-warn') : '';
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
        class="cursor-pointer bg-transparent p-0 text-accent-ink underline"
        title="Replaces the stored credential RE uses for this database, and Egeria's native surveys use too (through the re-projected secrets file).">${esc(r.fixable.action)} ›</button></span>` : '';
  return `${partial}<span class="${tone}" data-native-process-line>${esc(r.line)}</span>${whenHtml}${waiting}`
    + `${r.read_at ? ` · read ${timeSpan(r.read_at)}` : ''}${reports ? ` · ${reports}` : ''}${runCredentialHtml(r)}${err}${fix}`;
}

/** "◌ not checked since 20:31" when the server found the run still in flight and its proof row's `read_at` more than
 *  two read-back intervals old (`run.not_checked_since` is that `read_at`; absent otherwise). The cue is on the
 *  element (warn tone + glyph) as well as in words. */
function notCheckedCue(r) {
  if (!r || !r.not_checked_since) return '';
  const when = localTime(r.not_checked_since);
  return ` · <span class="text-state-warn" data-native-not-checked title="${esc(`RE last read this from Egeria at ${r.not_checked_since} UTC; its read-back looks every minute.`)}"><span class="font-glyph" aria-hidden="true">◌</span> not checked since ${esc(when || r.not_checked_since)}</span>`;
}

/** The one line of status. Pure over `row.run`. */
export function nativeSurveyStatusHtml(row) {
  const r = row.run || { state: 'not_run' };
  return statusLineHtml(row) + notCheckedCue(r);
}

function statusLineHtml(row) {
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
    case 'awaiting_report': case 'report_incomplete':
      // The sweep stopped retrying (20 tries or 24 h): the row's own words, and a refresh that starts the count again.
      if (r.read_gave_up) {
        return `Egeria says <span class="font-mono">${esc(r.egeria_status)}</span> · read ${timeSpan(r.read_at)} — `
          + `<span class="text-state-warn" data-native-gave-up>${esc(gaveUpLead(r.read_gave_up_words))}</span>`
          + ` <button type="button" data-native-check-now class="cursor-pointer bg-transparent p-0 text-accent-ink underline">refresh to retry</button>${err}`;
      }
      if (r.state === 'report_incomplete') {
        return `Egeria says <span class="font-mono">${esc(r.egeria_status)}</span> · read ${timeSpan(r.read_at)}`
          + ` — <span class="text-state-warn">RE's copy of the report is incomplete; reading it again</span>${err}`;
      }
      return `Egeria says <span class="font-mono">${esc(r.egeria_status)}</span> · read ${timeSpan(r.read_at)}`
        + ` — its report is not read into RE yet${err}`;
    case 'complete':
      return `complete · read ${timeSpan(r.read_at)} · report from ${timeSpan(r.report_at)}`
        + ` · <button type="button" data-native-report="${esc(r.report_guid)}"
            class="cursor-pointer bg-transparent p-0 text-accent-ink underline"
            >${r.count_label ? `<span class="tnum" data-native-count-label>${esc(r.count_label)}</span>`
              : `<span class="tnum">${esc(String(r.annotation_count))}</span> annotation${
                r.annotation_count === 1 ? '' : 's'}`} ›</button>`;
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
        ? ` · ${esc(row.asset_kind || 'asset')} <span class="font-mono" title="Egeria ${esc(row.asset_kind || 'asset')}">${esc(row.asset_guid)}</span>` : ''}`;
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
          : row.name_taken
            ? nameTakenHtml(row)
            : row.registered_again
              ? registeredAgainHtml(row)
              : '<span class="text-ink-muted">not registered with Egeria · optional</span>');
    default:
      if (row.stale) return `<span class="text-ink-muted" data-native-gone>${esc(row.gone_line || 'no longer in Egeria')}</span>`;
      if (row.name_taken) return nameTakenHtml(row);
      if (row.registered_again) return registeredAgainHtml(row);
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
  // A caution row draws no Register press, so it must not name one.
  if (!a || !a.will_change || row.name_taken) return '';
  return `<div class="mt-[2px] text-provenance text-ink" data-native-will-change><span class="font-glyph" aria-hidden="true">⚠</span>
    will change · Egeria has <span class="font-mono">${esc(a.held)}</span> · ${esc((row.register && row.register.label) || 'Register again →')} sends
    <span class="font-mono">${esc(a.current)}</span></div>`;
}

/** Cancel cues that outlive a repaint: a cancel Egeria did not confirm stays on its row until the next try
 *  (guid -> the words). A cancel that worked needs none: the row's own status then says CANCELLED. */
const cancelCues = new Map();

/** The line for a native survey's cancel that did not go through, in the definition card's three outcome words
 *  (PROCESS_CANCEL_WORDS; one block, never copied): the run was over (409), Egeria won't let this person (403),
 *  Egeria refused with its reason (422), or the cancel was not sent (502, or no answer at all). */
export function nativeCancelWords(err) {
  const w = PROCESS_CANCEL_WORDS;
  const st = err && err.status;
  // The unprefixed reason: a real Egeria 403 arrives as "refused by Egeria: <sentence>" (re-api.js).
  const msg = (err && (err.sentence || err.message)) || '';
  // RE's own gate refusals (sign-in, the access rule, "only a single-action survey") are shown verbatim; only
  // Egeria's answers (`by: egeria`, or a security refusal `refusedBy`) take the designer's words.
  const egeria = !!err && (err.by === 'egeria' || err.refusedBy === 'egeria');
  if (!st || (err && err.unsent)) return w.unsent;
  if (!egeria) return msg;
  if (st === 409) return w.tooLate;
  if (st === 403) return w.notPermitted('you', msg);
  if (st === 422) return w.refused(msg);
  return msg;
}
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
  // No ◐ (it means "measured, within a limit", designer §6). Only "asked Egeria; it still says X" is pending, so ◔.
  const keep = cue && live
    ? `<div class="mt-[2px] text-provenance text-state-warn" data-native-cancel-cue>${
      cue.pending ? '<span class="font-glyph" aria-hidden="true">◔</span> ' : ''}${esc(cue.words)}</div>` : '';
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

/** The server's terminal words ("report not read after 20 tries · refresh to retry"), without the trailing
 *  "refresh to retry": that is the button beside them. */
const gaveUpLead = (words) => String(words || '').replace(/\s*\u00b7\s*refresh to retry\s*$/, '');

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
      ${row.surveyor ? `<div class="mt-[2px] text-provenance text-ink-muted" data-native-surveyor>surveyed by ${esc(row.surveyor)}</div>` : ''}
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

/** "a, b and c" */
const listWords = (xs) => (xs.length < 2 ? (xs[0] || '') : `${xs.slice(0, -1).join(', ')} and ${xs[xs.length - 1]}`);
/** HH:MM in the reader's local time, from a stored UTC instant. */
const hhmm = (iso) => {
  if (!iso) return '';
  const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(iso) ? iso : `${iso}Z`);
  return Number.isNaN(t) ? '' : new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
};
const stepWords = (s) => `step ${s.step}, ${s.label}`;

/** The words of Cancel on a definition's coordinated run (§2.4): the designer's (Q4, 2026-10-17). Every word the
 *  control and its cues draw is here, so one edit changes them. Each takes only what the server's row gives. */
export const PROCESS_CANCEL_WORDS = {
  button: 'Cancel run',
  confirm: (ctx) => {
    const r = ctx && ctx.running;
    if (!r) return 'Cancel this run? The running step finishes but publishes nothing, and no later step starts. Finished steps keep their reports.';
    // Step identifiers (postgres_column_profile) stay exactly as written: only the word "step" opens a sentence.
    const later = ctx.later && ctx.later.length ? `, and ${listWords(ctx.later)} won't start` : '';
    const kept = ctx.finished && ctx.finished.length ? ` ${listWords(ctx.finished)} keep${ctx.finished.length === 1 ? 's its report' : ' their reports'}.` : '';
    return `Cancel this run? S${stepWords(r).slice(1)}, finishes but publishes nothing${later}.${kept}`;
  },
  confirmYes: 'Yes, cancel',
  confirmNo: 'keep running',
  pending: (who, at, ctx) => `cancel requested by ${who} · ${hhmm(at)} · waiting for Egeria${ctx && ctx.running ? ` · ${stepWords(ctx.running)}, finishes first` : ''}`,
  finishing: (step) => `cancelled · ${stepWords(step)}, is finishing and will publish nothing`,
  // PLACEHOLDER (designer asked): Egeria cancelled while the step was publishing; the engine host keeps that report.
  finishingKept: (step) => `cancelled · ${stepWords(step)}, is finishing · its report is kept`,
  // PLACEHOLDER (designer asked): publishing has begun but the report element is not yet proven to exist.
  finishingWriting: (step) => `cancelled · ${stepWords(step)}, is finishing · its report is being written`,
  done: (c) => `cancelled by ${c.by} · ${hhmm(c.at)} · ${c.finished} of ${c.of} steps finished`,
  // PLACEHOLDER: the link a cancelled step's kept report carries on the done line ("K of M finished" is unchanged).
  doneKept: (n) => `step ${n}'s report kept ›`,
  // PLACEHOLDER: a cancelled run in "runs · N ›", drawn muted, never as a fault.
  historyCancelled: 'cancelled',
  doneOutside: (c) => `cancelled in Egeria · ${hhmm(c.at)} · not from Resource Explorer`,
  tooLate: 'too late to cancel · the run had already finished',
  notPermitted: (who, reason) => `Egeria won't let ${who} cancel this run · ${reason}`,
  refused: (reason) => `Egeria refused the cancel: ${reason}`,
  unsent: "cancel not sent · couldn't reach Egeria · try again",
  outside: 'started outside Resource Explorer',
  outsideHover: 'RE can cancel only runs it started; cancel this one in Egeria',
};

/** The cue under a definition's run line, from the server's row only: `run.cancel` (the press's proof row, and the
 *  read-back for done) and `process_cancel` (finishing, outside). Never from the click. */
export function processCancelCueHtml(row) {
  const r = row.run || {};
  const c = r.cancel || {};
  const pc = row.process_cancel || {};
  const w = PROCESS_CANCEL_WORDS;
  const who = (by) => (by && by === pc.viewer ? 'you' : (by || 'someone'));
  const line = (attr, tone, glyph, words, title = '', tail = '') => `<div class="mt-[2px] text-provenance ${tone}" ${attr}${
    title ? ` title="${esc(title)}"` : ''}>${glyph ? `<span class="font-glyph" aria-hidden="true">${glyph}</span> ` : ''}${esc(words)}${tail}</div>`;
  if (c.state === 'done') {
    if (pc.finishing) {
      // From the run row's proof (publish_started_at), never the branch the engine host took.
      if (pc.finishing.report_kept) return line('data-process-cancel-finishing data-process-cancel-report-kept', 'text-ink-muted', '⊘', w.finishingKept(pc.finishing));
      if (pc.finishing.publish_started_at) return line('data-process-cancel-finishing data-process-cancel-report-writing', 'text-ink-muted', '⊘', w.finishingWriting(pc.finishing));
      return line('data-process-cancel-finishing', 'text-ink-muted', '⊘', w.finishing(pc.finishing));
    }
    // A cancelled step's kept report opens as any report does (bindRunLineControls).
    const kept = (pc.kept || []).filter((k) => k && k.guid).map((k) => ` · <button type="button" data-native-report="${esc(k.guid)}"
      data-process-cancel-kept-report class="cursor-pointer bg-transparent p-0 text-accent-ink underline">${esc(w.doneKept(k.step))}</button>`).join('');
    return c.outside ? line('data-process-cancel-done-outside', 'text-ink-muted', '⊘', w.doneOutside(c), '', kept)
      : line('data-process-cancel-done', 'text-ink-muted', '⊘', w.done(c), '', kept);
  }
  if (c.state === 'pending') return line('data-process-cancel-pending', 'text-ink-muted', '◔', w.pending(who(c.by), c.at, r.cancel_context));
  if (c.state === 'too_late' || (c.state === 'refused' && c.kind === 'too_late')) {
    return line('data-process-cancel-too-late', 'text-ink-muted', '', w.tooLate);
  }
  if (c.state === 'refused') {
    return c.kind === 'not_permitted'
      ? line('data-process-cancel-refused', 'text-state-warn', '', w.notPermitted(who(c.by), c.reason || ''))
      : line('data-process-cancel-refused', 'text-state-warn', '', w.refused(c.reason || ''));
  }
  // Not sent, or sent with no answer (a timeout): the same words until a read-back shows its effect.
  if (c.state === 'unsent') return line('data-process-cancel-unsent', 'text-state-warn', '', w.unsent);
  if (pc.outside) return line('data-process-cancel-outside', 'text-ink-muted', '', w.outside, w.outsideHover);
  return '';
}

/** A refusal RE's own gate gave before Egeria was asked (403 / 409, or 502 when the fresh read failed), per process
 *  instance: shown in place on the line and kept across `checkNow` repaints until the next press of Cancel. */
const gateRefusals = new Map();
function gateRefusalHtml(guid) {
  const words = guid && gateRefusals.get(guid);
  return words ? `<span class="block text-state-warn" data-process-cancel-error>${esc(words)}</span>` : '';
}

/** "Cancel run" on its own line under a definition's coordinated run line: only when the server offers it to THIS
 *  reader (a run RE started, its starter or an admin, while it runs: `row.process_cancel.offered`). The confirm is a
 *  state of the line, in place, never a dialog. */
export function processCancelControlHtml(row) {
  const r = row.run || {};
  const guid = r.engine_action_guid;
  if (!guid || !(row.process_cancel && row.process_cancel.offered === true)) return '';
  const w = PROCESS_CANCEL_WORDS;
  return `<div class="mt-[2px]" data-process-cancel-box="${esc(guid)}">
    <button type="button" data-process-cancel="${esc(guid)}"
      class="cursor-pointer border-0 bg-transparent p-0 text-provenance text-accent-ink underline">${esc(w.button)}</button>
    <span hidden data-process-cancel-confirm class="text-provenance text-ink">
      <span class="font-glyph" aria-hidden="true">◌</span> ${esc(w.confirm(r.cancel_context))}
      <button type="button" data-process-cancel-yes="${esc(guid)}" class="cursor-pointer rounded-sm border border-state-warn bg-transparent px-2 text-provenance text-state-warn">${esc(w.confirmYes)}</button>
      <button type="button" data-process-cancel-no class="cursor-pointer border-0 bg-transparent p-0 text-provenance text-ink-muted underline">${esc(w.confirmNo)}</button>
    </span>
    <span hidden data-process-cancel-ack class="text-provenance text-ink-muted"></span>${gateRefusalHtml(guid)}</div>`;
}

/** A definition card's run line: the latest run, whoever coordinated it, with the same words either way (the
 *  server's derived row, not re-derived here). `stopped`: RE gave up following it (state word stays, with "check now"). */
export function definitionRunLineHtml(row, { stopped = false } = {}) {
  const g = stateGlyph(glyphFor(row.run || { state: 'not_run' }, row.runnable, row));
  return `<div class="mt-[2px] text-provenance text-ink-muted" data-def-run-line="${esc(row.qualified_name)}">
    <span class="${g.tone}" title="${esc(g.word)}" aria-label="${esc(g.word)}">${g.glyph}</span>
    <span data-native-status>${nativeSurveyStatusHtml(row)}</span>${stopped && row.in_flight ? stoppedFollowingHtml(row) : ''}${processCancelCueHtml(row)}${processCancelControlHtml(row)}${
  row.process_cancel && row.process_cancel.offered === true ? '' : gateRefusalHtml((row.run || {}).engine_action_guid)}</div>`;
}

const RUN_STATE_WORD = { complete: 'complete', partial: 'partial', failed: 'failed', running: 'running', submitted: 'submitted',
  cancelled: PROCESS_CANCEL_WORDS.historyCancelled };
/** A history entry's state word: a fault is warn-toned, a cancel is a decision (muted, like the rest of the list). */
const runStateHtml = (state) => {
  const word = esc(RUN_STATE_WORD[state] || state || '');
  if (state === 'cancelled') return `<span class="text-ink-muted" data-run-state="cancelled">${word}</span>`;
  return word;
};

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
      · ${runStateHtml(r.state)}${count}${reports ? ` · ${reports}` : ''}</li>`;
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
        cancelCues.set(guid, { words: why, pending: true });
        paint(res.surveys);
        failDialog(why);
      } catch (err) {
        done();
        const why = nativeCancelWords(err);
        cancelCues.set(guid, { words: why, pending: false });
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
  // "with options…" (DESIGN-COORDINATED-RUN-CREDENTIAL.md §2.1): the same credential dialog RE's own runs use, for this
  // one coordinated run. Not while a run is in flight (the control is pressed).
  const nativeSteps = (ctl.native_steps || []).map((n) => String(n)).join(',');
  const options = live ? '' : `<button type="button" data-coordinate-egeria-options="${qn}"
      data-coordinate-native-steps="${esc(nativeSteps)}"
      title="${esc(RUN_CREDENTIAL_WORDS.optionsTitle)}"
      class="shrink-0 cursor-pointer border-0 bg-transparent p-0 text-caveat text-ink-muted underline"
      >${esc(RUN_CREDENTIAL_WORDS.optionsLabel)}</button>`;
  return `<span class="inline-flex shrink-0 items-baseline gap-s1">
    <button type="button" data-coordinate-egeria="${qn}" ${live ? 'disabled aria-pressed="true"' : 'aria-pressed="false"'}
      title="${esc(live ? (ctl.line || 'Egeria is coordinating a run of this definition') : 'Egeria runs this definition as a process; Resource Explorer\'s engine host runs each step')}"
      class="cursor-pointer rounded-sm border ${live ? 'border-rule-strong text-ink-muted' : 'border-accent text-accent-ink'} bg-transparent px-2 py-[2px] text-caveat"
      >${live ? `<span aria-hidden="true">◔</span> ${label} · running` : label}</button>${options}
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
  /** One start of a coordinated run from the control `b`, with `credential` ({user, password}) for this run only when
   *  the "with options…" dialog gave one: it goes in the body of this one call and nowhere else. */
  const start = async (b, credential = null) => {
    if (b.disabled) return;                                 // a pressed control ignores a second press
    const qn = b.dataset.coordinateEgeria;
    const why = b.parentElement && b.parentElement.querySelector('[data-coordinate-why]');
    const opts = b.parentElement && b.parentElement.querySelector('[data-coordinate-egeria-options]');
    b.disabled = true;
    if (opts) opts.disabled = true;
    b.setAttribute('aria-pressed', 'true');
    b.innerHTML = '<span aria-hidden="true">◔</span> starting…';
    if (why) { why.textContent = ''; why.classList.add('hidden'); }
    try {
      const res = await runNativeSurvey(slug, qn, { entityType: apiEntityType(state.resourceType), credential });
      const run = (res && res.run) || {};
      const started = COORD_IN_FLIGHT.has(String(run.state || '')) || run.state === 'complete' || run.state === 'partial';
      // Egeria started it and the run row's marker was not written: a warn cue with its words, never the plain tick
      // (the run line cannot yet say this run asked for a one-run credential).
      const unmarked = run.run_credential_marker_error;
      b.innerHTML = unmarked
        ? `<span class="font-glyph text-state-warn" aria-hidden="true">◐</span> <span data-coordinate-marker-warn class="text-state-warn">${esc(RUN_CREDENTIAL_WORDS.markerNotRecorded)}</span>`
        : started
          ? '<span class="text-state-ok" aria-hidden="true">●</span> started'
          : `<span class="text-state-warn" aria-hidden="true">✗</span> ${esc(run.state || 'not started')}`;
      b.title = unmarked || run.line || run.message || (run.engine_action_guid ? `process ${run.engine_action_guid}` : '');
      if (typeof after === 'function') after();
    } catch (err) {
      b.disabled = false;
      if (opts) opts.disabled = false;
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
  };
  el.querySelectorAll('[data-coordinate-egeria]').forEach((b) => {
    if (b._coordBound) return;                              // the definition card and the survey row may share a root
    b._coordBound = true;
    b.addEventListener('click', () => start(b));
  });
  el.querySelectorAll('[data-coordinate-egeria-options]').forEach((o) => {
    if (o._coordBound) return;
    o._coordBound = true;
    o.addEventListener('click', () => {
      const b = o.parentElement && o.parentElement.querySelector('[data-coordinate-egeria]');
      if (!b || b.disabled) return;
      const qn = o.dataset.coordinateEgeriaOptions;
      const w = RUN_CREDENTIAL_WORDS;
      const native = String(o.dataset.coordinateNativeSteps || '').split(',').filter(Boolean);
      // The EXISTING dialog and its browser memory (run-credential.js), with the coordinated case's own lead line and,
      // when Egeria runs any step itself, which steps the credential does not reach.
      openAnalysisOverrideDialog({ name: qn, analysis_id: qn }, slug, (credential) => start(b, credential), {
        subtitle: w.dialogSubtitle,
        notes: native.map((n) => w.nativeStep(n)),
      });
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
  // A fixable refusal: the link opens the same test-before-save credential form, in place under the run line that
  // carries it, on whatever stage the line is drawn (the header's door is not on every stage). Never a silent press.
  root.querySelectorAll('[data-native-fix-credential]').forEach((b) => once(b, () => {
    const p = (state.databases || []).find((x) => x.slug === slug);
    const anchor = b.closest('[data-native-fixable]') || b;
    let slot = anchor.parentElement.querySelector(':scope > [data-native-credential-slot]');
    if (!p) {
      b.textContent = 'open Scouting to change the credential';
      b.title = 'This stage has no copy of the database row; the Scouting stage has the credential door.';
      return;
    }
    if (!slot) {
      slot = document.createElement('div');
      slot.setAttribute('data-native-credential-slot', '');
      slot.className = 'mt-s1';
      anchor.insertAdjacentElement('afterend', slot);
    }
    openCredentialForm(slot, p, {
      onSaved: () => { slot.innerHTML = '<span class="text-ink" data-native-credential-saved>credential saved · re-run to use it</span>'; },
    });
  }));
  // Cancel on a definition's coordinated run (§2.4): press -> confirm -> the request. The line then repaints from the
  // server's rows only ("cancelling…" from the press's proof row, ⊘ cancelled from Egeria's read-back); a refusal is
  // Egeria's reason on the line. `checkNow` repaints; nothing here decides a state.
  root.querySelectorAll('[data-process-cancel]').forEach((b) => once(b, () => {
    const box = b.closest('[data-process-cancel-box]');
    gateRefusals.delete(b.dataset.processCancel);       // the next press clears the last gate refusal
    box.querySelectorAll('[data-process-cancel-error]').forEach((e) => e.remove());
    b.hidden = true;
    const conf = box.querySelector('[data-process-cancel-confirm]');
    conf.hidden = false;
    setTimeout(() => { if (box.isConnected && !box.dataset.sent) { conf.hidden = true; b.hidden = false; } }, CONFIRM_MS);
  }));
  root.querySelectorAll('[data-process-cancel-no]').forEach((b) => once(b, () => {
    const box = b.closest('[data-process-cancel-box]');
    box.querySelector('[data-process-cancel-confirm]').hidden = true;
    box.querySelector('[data-process-cancel]').hidden = false;
  }));
  root.querySelectorAll('[data-process-cancel-yes]').forEach((b) => once(b, async () => {
    const box = b.closest('[data-process-cancel-box]');
    const ack = box.querySelector('[data-process-cancel-ack]');
    box.dataset.sent = '1';
    box.querySelector('[data-process-cancel-confirm]').hidden = true;
    ack.hidden = false;
    ack.innerHTML = '<span class="font-glyph" aria-hidden="true">◔</span> …';   // the press is seen at once; the words come from the row
    const guid = b.dataset.processCancelYes;
    try {
      await cancelProcessRun(slug, guid, { entityType: apiEntityType(state.resourceType) });
    } catch (err) {
      // RE's gate said no before Egeria was asked: kept per run, so the repaint below draws it in place.
      if ([403, 409, 502].includes(err && err.status)) gateRefusals.set(guid, err.message || '');
      ack.innerHTML = `<span class="text-state-warn" data-process-cancel-error>${esc(err.message || '')}</span>`;
    }
    if (checkNow) await checkNow();      // the server's rows: its words (pending, refused, not sent), never this click's
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
